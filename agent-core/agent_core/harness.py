"""Execution budgets for model driven agent loops.

The runtime owns graph state; the harness limits work performed inside one
model/tool node. Limits are per execution, never shared across runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from time import sleep
from uuid import uuid4
from typing import Any, Callable, Mapping, Protocol, Sequence

from .results import NodeResult
from .approval import (ApprovalConflictError, FileApprovalDecisionStore,
                       FilePendingInvocationStore, PendingInvocation, argument_digest)
from .runtime import _json_snapshot, _validate_run_id
from .tools import ToolResult, ToolStatus


@dataclass(frozen=True)
class HarnessLimits:
    max_model_calls: int
    max_tool_calls: int

    def __post_init__(self) -> None:
        for name in ("max_model_calls", "max_tool_calls"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class ContextLimits:
    max_messages: int | None = None
    max_chars: int | None = None

    def __post_init__(self) -> None:
        for value, name in ((self.max_messages, "max_messages"), (self.max_chars, "max_chars")):
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
                raise ValueError(f"{name} must be a positive integer when provided")


class ContextAssembler:
    """Trim whole older turns while preserving the current turn and system message."""

    def __init__(self, limits: ContextLimits | None = None) -> None:
        self.limits = limits or ContextLimits()

    @staticmethod
    def _chars(messages: Sequence[Mapping[str, Any]]) -> int:
        return sum(len(json.dumps(dict(message), ensure_ascii=False, sort_keys=True)) for message in messages)

    def assemble(self, messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        assembled, _dropped = self.assemble_with_report(messages)
        return assembled

    def assemble_with_report(self, messages: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
            raise TypeError("messages must be a sequence")
        if any(not isinstance(message, Mapping) for message in messages):
            raise TypeError("each message must be a mapping")
        result = [_json_snapshot(dict(message)) for message in messages]
        if self.limits.max_messages is None and self.limits.max_chars is None:
            return result, 0
        system = result[:1] if result and result[0].get("role") == "system" else []
        body = result[len(system):]
        turns: list[list[dict[str, Any]]] = []
        for message in body:
            if message.get("role") == "user" or not turns:
                turns.append([])
            turns[-1].append(message)
        if not turns:
            raise ContextLimitError("CONTEXT_LIMIT_EXCEEDED")

        def fits(candidate: list[dict[str, Any]]) -> bool:
            return ((self.limits.max_messages is None or len(candidate) <= self.limits.max_messages)
                    and (self.limits.max_chars is None or self._chars(candidate) <= self.limits.max_chars))

        selected = system + turns[-1]
        if not fits(selected):
            raise ContextLimitError("CONTEXT_LIMIT_EXCEEDED")
        for turn in reversed(turns[:-1]):
            candidate = system + turn + selected[len(system):]
            if fits(candidate):
                selected = candidate
            else:
                break
        return selected, len(result) - len(selected)


class ContextLimitError(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class HarnessBudget:
    """Count admitted calls before side effects happen."""

    def __init__(self, limits: HarnessLimits) -> None:
        if not isinstance(limits, HarnessLimits):
            raise TypeError("limits must be HarnessLimits")
        self.limits = limits
        self.model_calls = 0
        self.tool_calls = 0

    def claim_model_call(self) -> bool:
        if self.model_calls >= self.limits.max_model_calls:
            return False
        self.model_calls += 1
        return True

    def claim_tool_call(self) -> bool:
        if self.tool_calls >= self.limits.max_tool_calls:
            return False
        self.tool_calls += 1
        return True

    def usage(self) -> dict[str, int]:
        return {
            "model_calls": self.model_calls,
            "remaining_model_calls": self.limits.max_model_calls - self.model_calls,
            "tool_calls": self.tool_calls,
            "remaining_tool_calls": self.limits.max_tool_calls - self.tool_calls,
        }


@dataclass(frozen=True)
class HarnessEvent:
    """Append-only fact for one model or tool invocation."""

    run_id: str
    sequence: int
    event_id: str
    event_type: str
    status: str
    occurred_at: str
    call_id: str
    node_id: str | None = None
    execution_id: str | None = None
    reason_code: str | None = None
    summary: Mapping[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        _validate_run_id(self.run_id)
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence <= 0:
            raise ValueError("event sequence must be a positive integer")
        for value, name in ((self.event_id, "event_id"), (self.event_type, "event_type"),
                            (self.status, "status"), (self.call_id, "call_id")):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be nonempty")
        if self.reason_code is not None and (not isinstance(self.reason_code, str) or not self.reason_code.strip()):
            raise ValueError("reason_code must be nonempty when provided")
        object.__setattr__(self, "summary", _json_snapshot(self.summary or {}))


class HarnessAuditStore(Protocol):
    def append(self, event: HarnessEvent) -> None: ...
    def load(self, run_id: str) -> tuple[HarnessEvent, ...]: ...


def _validate_event_sequence(events: Sequence[HarnessEvent], run_id: str) -> tuple[HarnessEvent, ...]:
    for expected, event in enumerate(events, start=1):
        if not isinstance(event, HarnessEvent) or event.run_id != run_id or event.sequence != expected:
            raise ValueError("invalid harness event sequence")
    return tuple(events)


class InMemoryHarnessAuditStore:
    durable = False

    def __init__(self) -> None:
        self._records: dict[str, list[HarnessEvent]] = {}
        self._lock = RLock()

    def append(self, event: HarnessEvent) -> None:
        if not isinstance(event, HarnessEvent):
            raise TypeError("event must be HarnessEvent")
        with self._lock:
            events = self._records.setdefault(event.run_id, [])
            if event.sequence != len(events) + 1:
                raise ValueError("harness event sequence must be contiguous")
            events.append(event)

    def load(self, run_id: str) -> tuple[HarnessEvent, ...]:
        _validate_run_id(run_id)
        with self._lock:
            return tuple(self._records.get(run_id, ()))


class FileHarnessAuditStore:
    """Atomic JSON persistence for append-only Harness facts."""

    durable = True

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)
        self._lock = RLock()

    def _path(self, run_id: str) -> Path:
        _validate_run_id(run_id)
        return self._directory / f"{run_id}.harness.json"

    def append(self, event: HarnessEvent) -> None:
        if not isinstance(event, HarnessEvent):
            raise TypeError("event must be HarnessEvent")
        with self._lock:
            events = list(self.load(event.run_id))
            if event.sequence != len(events) + 1:
                raise ValueError("harness event sequence must be contiguous")
            events.append(event)
            self._directory.mkdir(parents=True, exist_ok=True)
            target = self._path(event.run_id)
            temporary = self._directory / f".{event.run_id}.{uuid4().hex}.harness.tmp"
            try:
                temporary.write_text(json.dumps({"schema_version": 1, "run_id": event.run_id,
                    "events": [asdict(item) for item in events]}, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
                temporary.replace(target)
            except Exception:
                try:
                    temporary.unlink()
                except OSError:
                    pass
                raise

    def load(self, run_id: str) -> tuple[HarnessEvent, ...]:
        target = self._path(run_id)
        with self._lock:
            if not target.is_file():
                return ()
            try:
                payload = json.loads(target.read_text(encoding="utf-8"))
                if payload.get("schema_version") != 1 or payload.get("run_id") != run_id:
                    raise ValueError("invalid harness audit record")
                events = tuple(HarnessEvent(**item) for item in payload["events"])
                return _validate_event_sequence(events, run_id)
            except (OSError, json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
                raise ValueError("invalid harness audit record") from exc


class HarnessAudit:
    """Records invocation facts and exposes a stable per-run query surface."""

    def __init__(self, store: HarnessAuditStore | None = None) -> None:
        self.store = store or InMemoryHarnessAuditStore()
        self._lock = RLock()

    def record(self, run_id: str, event_type: str, status: str, *, call_id: str,
               node_id: str | None = None, execution_id: str | None = None,
               reason_code: str | None = None, summary: Mapping[str, Any] | None = None) -> HarnessEvent:
        with self._lock:
            try:
                events = self.store.load(run_id)
                event = HarnessEvent(run_id, len(events) + 1, uuid4().hex, event_type, status,
                                     datetime.now(timezone.utc).isoformat(), call_id,
                                     node_id, execution_id, reason_code, summary or {})
                self.store.append(event)
            except Exception as exc:
                raise HarnessAuditError("AUDIT_WRITE_FAILED") from exc
            return event

    def events(self, run_id: str) -> tuple[HarnessEvent, ...]:
        return self.store.load(run_id)


class HarnessAuditError(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class RunBudget:
    """Durable, per-run call reservations shared by Harness nodes."""

    def __init__(self, limits: HarnessLimits, audit: HarnessAudit, run_id: str) -> None:
        if not isinstance(limits, HarnessLimits) or not isinstance(audit, HarnessAudit):
            raise TypeError("run budget requires HarnessLimits and HarnessAudit")
        _validate_run_id(run_id)
        self.limits, self.audit, self.run_id = limits, audit, run_id

    def claim(self, kind: str, call_id: str, *, node_id: str | None,
              execution_id: str | None, source_run_id: str | None = None) -> str:
        if kind not in ("model", "tool"):
            raise ValueError("invalid run budget kind")
        with self.audit._lock:
            try:
                events = self.audit.events(self.run_id)
            except Exception as exc:
                raise HarnessAuditError("AUDIT_READ_FAILED") from exc
            claims = [event for event in events if event.event_type == "run_budget.claimed"]
            limits = {"model": self.limits.max_model_calls, "tool": self.limits.max_tool_calls}
            if any(event.summary.get("limits") != limits for event in claims):
                raise HarnessLimitError("RUN_BUDGET_LIMIT_MISMATCH")
            settled = {event.summary.get("claim_id") for event in events
                       if event.event_type == "run_budget.settled"}
            if any(event.event_id not in settled for event in claims):
                raise HarnessLimitError("MODEL_OUTCOME_UNKNOWN" if any(
                    event.event_id not in settled and event.summary.get("kind") == "model"
                    for event in claims) else "TOOL_OUTCOME_UNKNOWN")
            if sum(event.summary.get("kind") == kind for event in claims) >= limits[kind]:
                raise HarnessLimitError("MAX_MODEL_CALLS" if kind == "model" else "MAX_TOOL_CALLS")
            claim = self.audit.record(self.run_id, "run_budget.claimed", "PENDING",
                                      call_id=call_id, node_id=node_id,
                                      execution_id=execution_id,
                                      summary={"kind": kind, "limits": limits,
                                               "source_run_id": source_run_id or self.run_id})
            return claim.event_id

    def settle(self, claim_id: str, kind: str, call_id: str, *,
               node_id: str | None, execution_id: str | None) -> None:
        self.audit.record(self.run_id, "run_budget.settled", "COMPLETED",
                          call_id=call_id, node_id=node_id, execution_id=execution_id,
                          summary={"claim_id": claim_id, "kind": kind})


def model_usage(reply: Any) -> dict[str, int]:
    """Return only token counts explicitly supplied by the adapter."""
    usage = reply.get("usage") if isinstance(reply, Mapping) else None
    if not isinstance(usage, Mapping):
        return {}
    return {key: value for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if (value := usage.get(key)) is not None
            and isinstance(value, int) and not isinstance(value, bool) and value >= 0}


class ModelToolLoop(Protocol):
    """Replaceable model/tool driver used by the declarative agent loop node."""

    def run(
        self, complete: Callable[[Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]], Mapping[str, Any]],
        messages: list[dict[str, Any]],
        schemas: Sequence[Mapping[str, Any]],
        pipeline: "ToolInvocationPipeline",
        *, into: str, route: str | None,
    ) -> NodeResult: ...


class ToolInvoker(Protocol):
    """Tool provider consumed by the Harness pipeline."""

    def invoke(
        self, name: str, arguments: Mapping[str, Any], *,
        granted_capabilities: frozenset[str],
    ) -> ToolResult: ...


class ToolPolicy(Protocol):
    """Pre-execution policy for tools exposed through a Harness."""

    def authorize(self, name: str, arguments: Mapping[str, Any], registry: ToolInvoker) -> str | None: ...


class DefaultToolPolicy:
    """Expose only the schemas declared by the current agent node."""

    def __init__(self, allowed_tools: frozenset[str] | None = None) -> None:
        if allowed_tools is not None and any(not isinstance(item, str) or not item.strip() for item in allowed_tools):
            raise ValueError("allowed_tools must contain nonempty strings")
        self.allowed_tools = allowed_tools

    def authorize(self, name: str, arguments: Mapping[str, Any], registry: ToolInvoker) -> str | None:
        if self.allowed_tools is not None and name not in self.allowed_tools:
            lookup = getattr(registry, "get", None)
            if callable(lookup) and lookup(name) is None:
                return None
            return "TOOL_NOT_EXPOSED"
        return None


class HarnessLimitError(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ModelRetryPolicy:
    max_retries: int = 0
    backoff_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.max_retries, int) or isinstance(self.max_retries, bool) \
                or not 0 <= self.max_retries <= 5:
            raise ValueError("max_retries must be an integer from 0 to 5")
        if not isinstance(self.backoff_seconds, (int, float)) or isinstance(self.backoff_seconds, bool) \
                or not 0 <= self.backoff_seconds <= 30:
            raise ValueError("backoff_seconds must be between 0 and 30")


@dataclass(frozen=True)
class ToolRetryPolicy:
    max_retries: int = 0
    backoff_seconds: float = 0.0

    def __post_init__(self) -> None:
        ModelRetryPolicy(self.max_retries, self.backoff_seconds)


class ModelInvocationPipeline:
    """Budgeted model entry point supplied to a replaceable loop."""

    def __init__(
        self, complete: Callable[[Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]], Mapping[str, Any]],
        budget: HarnessBudget, *, audit: HarnessAudit | None = None,
        run_id: str | None = None, node_id: str | None = None, execution_id: str | None = None,
        context: ContextAssembler | None = None, retry_policy: ModelRetryPolicy | None = None,
        retry_allowed: Callable[[Exception], bool] | None = None,
        run_budget: RunBudget | None = None,
    ) -> None:
        if not callable(complete):
            raise TypeError("complete must be callable")
        if not isinstance(budget, HarnessBudget):
            raise TypeError("budget must be HarnessBudget")
        self._complete = complete
        self._budget = budget
        self._audit = audit
        self._run_id = run_id
        self._node_id = node_id
        self._execution_id = execution_id
        self._context = context or ContextAssembler()
        if retry_policy is not None and not isinstance(retry_policy, ModelRetryPolicy):
            raise TypeError("retry_policy must be a ModelRetryPolicy")
        if retry_allowed is not None and not callable(retry_allowed):
            raise TypeError("retry_allowed must be callable")
        self._retry_policy = retry_policy or ModelRetryPolicy()
        self._retry_allowed = retry_allowed
        if run_budget is not None and (audit is None or run_id is None):
            raise ValueError("run_budget requires audit and run_id")
        self._run_budget = run_budget

    def complete(
        self, messages: Sequence[Mapping[str, Any]], schemas: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        call_id = uuid4().hex
        original_count = len(messages)
        try:
            messages, dropped_count = self._context.assemble_with_report(messages)
        except ContextLimitError as exc:
            if self._audit is not None and self._run_id is not None:
                self._audit.record(self._run_id, "model.rejected", "FAILED", call_id=call_id,
                                   node_id=self._node_id, execution_id=self._execution_id,
                                   reason_code=exc.reason_code,
                                   summary={"original_message_count": original_count})
            raise
        if self._audit is not None and self._run_id is not None:
            self._audit.record(self._run_id, "model.requested", "PENDING", call_id=call_id,
                               node_id=self._node_id, execution_id=self._execution_id,
                               summary={"message_count": len(messages), "original_message_count": original_count,
                                        "dropped_message_count": dropped_count,
                                        "tool_schema_count": len(schemas)})
        for attempt in range(1, self._retry_policy.max_retries + 2):
            if self._budget.model_calls >= self._budget.limits.max_model_calls:
                if self._audit is not None and self._run_id is not None:
                    self._audit.record(self._run_id, "model.rejected", "FAILED", call_id=call_id,
                                       node_id=self._node_id, execution_id=self._execution_id,
                                       reason_code="MAX_TOOL_ROUNDS",
                                       summary={"budget": self._budget.usage()})
                raise HarnessLimitError("MAX_TOOL_ROUNDS")
            claim_id = None
            if self._run_budget is not None:
                try:
                    claim_id = self._run_budget.claim("model", call_id, node_id=self._node_id,
                                                      execution_id=self._execution_id,
                                                      source_run_id=self._run_id)
                except HarnessLimitError as exc:
                    self._audit.record(self._run_id, "model.rejected", "FAILED", call_id=call_id,
                                       node_id=self._node_id, execution_id=self._execution_id,
                                       reason_code=exc.reason_code,
                                       summary={"budget": self._budget.usage()})
                    raise
            if not self._budget.claim_model_call():
                if self._audit is not None and self._run_id is not None:
                    self._audit.record(self._run_id, "model.rejected", "FAILED", call_id=call_id,
                                       node_id=self._node_id, execution_id=self._execution_id,
                                       reason_code="MAX_TOOL_ROUNDS",
                                       summary={"budget": self._budget.usage()})
                raise HarnessLimitError("MAX_TOOL_ROUNDS")
            try:
                result = self._complete(messages, schemas)
            except Exception as exc:
                reason = getattr(exc, "reason_code", None)
                reason = reason if isinstance(reason, str) and reason.strip() else "MODEL_REQUEST_FAILED"
                retry = (reason in ("MODEL_TIMEOUT", "MODEL_UNREACHABLE")
                         and attempt <= self._retry_policy.max_retries
                         and self._budget.model_calls < self._budget.limits.max_model_calls
                         and (self._retry_allowed is None or self._retry_allowed(exc)))
                backoff = min(self._retry_policy.backoff_seconds * (2 ** (attempt - 1)), 30.0) if retry else 0.0
                if self._audit is not None and self._run_id is not None:
                    if self._retry_policy.max_retries:
                        self._audit.record(self._run_id, "model.attempt_failed", "FAILED",
                                           call_id=call_id, node_id=self._node_id,
                                           execution_id=self._execution_id, reason_code=reason,
                                           summary={"attempt": attempt, "retry": retry,
                                                    "backoff_seconds": backoff,
                                                    "error_type": type(exc).__name__,
                                                    "budget": self._budget.usage()})
                    if not retry:
                        self._audit.record(self._run_id, "model.failed", "FAILED", call_id=call_id,
                                           node_id=self._node_id, execution_id=self._execution_id,
                                           reason_code=reason,
                                           summary={"error_type": type(exc).__name__,
                                                    "budget": self._budget.usage()})
                if claim_id is not None:
                    self._run_budget.settle(claim_id, "model", call_id, node_id=self._node_id,
                                            execution_id=self._execution_id)
                if not retry:
                    raise
                if backoff:
                    sleep(backoff)
                if self._retry_allowed is not None and not self._retry_allowed(exc):
                    if self._audit is not None and self._run_id is not None:
                        self._audit.record(self._run_id, "model.retry_cancelled", "FAILED",
                                           call_id=call_id, node_id=self._node_id,
                                           execution_id=self._execution_id,
                                           reason_code="CANCEL_REQUESTED",
                                           summary={"attempt": attempt,
                                                    "budget": self._budget.usage()})
                    raise
            else:
                if self._retry_policy.max_retries and self._audit is not None and self._run_id is not None:
                    self._audit.record(self._run_id, "model.attempt_completed", "COMPLETED",
                                       call_id=call_id, node_id=self._node_id,
                                       execution_id=self._execution_id,
                                       summary={"attempt": attempt, "budget": self._budget.usage()})
                break
        if self._audit is not None and self._run_id is not None:
            self._audit.record(self._run_id, "model.completed", "COMPLETED", call_id=call_id,
                               node_id=self._node_id, execution_id=self._execution_id,
                               summary={"reply_keys": sorted(result) if isinstance(result, Mapping) else [],
                                        "usage": model_usage(result),
                                        "budget": self._budget.usage()})
        if claim_id is not None:
            self._run_budget.settle(claim_id, "model", call_id, node_id=self._node_id,
                                    execution_id=self._execution_id)
        return result


class ToolInvocationPipeline:
    """One entry point for declarative tool nodes and model-driven loops.

    The registry remains responsible for capability checks and tool result
    validation. Later policy and audit stages can be inserted here without
    changing the loop or individual tools.
    """

    def __init__(
        self, registry: ToolInvoker, *,
        granted_capabilities: frozenset[str], budget: HarnessBudget,
        audit: HarnessAudit | None = None, run_id: str | None = None,
        node_id: str | None = None, execution_id: str | None = None,
        policy: ToolPolicy | None = None,
        pending_invocations: FilePendingInvocationStore | None = None,
        retry_policy: ToolRetryPolicy | None = None,
        retry_allowed: Callable[[], bool] | None = None,
        run_budget: RunBudget | None = None,
        state_version: int | None = None,
        invoke_allowed: Callable[[], bool] | None = None,
    ) -> None:
        if not callable(getattr(registry, "invoke", None)):
            raise TypeError("registry must provide invoke")
        if not isinstance(budget, HarnessBudget):
            raise TypeError("budget must be HarnessBudget")
        self._registry = registry
        if state_version is not None and (
                not isinstance(state_version, int) or isinstance(state_version, bool) or state_version < 0):
            raise ValueError("state_version must be a nonnegative integer")
        self._state_version = state_version
        if invoke_allowed is not None and not callable(invoke_allowed):
            raise TypeError("invoke_allowed must be callable")
        self._invoke_allowed = invoke_allowed
        self._granted_capabilities = granted_capabilities
        self._budget = budget
        self._audit = audit
        self._run_id = run_id
        self._node_id = node_id
        self._execution_id = execution_id
        self._policy = policy or DefaultToolPolicy()
        self._pending_invocations = pending_invocations
        if retry_policy is not None and not isinstance(retry_policy, ToolRetryPolicy):
            raise TypeError("retry_policy must be a ToolRetryPolicy")
        if retry_allowed is not None and not callable(retry_allowed):
            raise TypeError("retry_allowed must be callable")
        self._retry_policy = retry_policy or ToolRetryPolicy()
        self._retry_allowed = retry_allowed
        if run_budget is not None and (audit is None or run_id is None):
            raise ValueError("run_budget requires audit and run_id")
        self._run_budget = run_budget

    def restore_budget(self, pending: PendingInvocation) -> str | None:
        """Restore usage represented by a saved default-loop snapshot."""
        if self._budget.model_calls or self._budget.tool_calls:
            raise ValueError("restore requires an unused budget")
        model_calls = (pending.model_calls if pending.model_calls is not None else
                       sum(message.get("role") == "assistant" and
                           bool(message.get("tool_calls")) for message in pending.messages))
        tool_calls = (pending.tool_calls if pending.tool_calls is not None else len(pending.rounds))
        tool_calls += pending.execution_state == "COMPLETED"
        if model_calls > self._budget.limits.max_model_calls:
            return "MAX_MODEL_CALLS"
        if tool_calls > self._budget.limits.max_tool_calls or (
                pending.execution_state == "PENDING" and
                tool_calls == self._budget.limits.max_tool_calls):
            return "MAX_TOOL_CALLS"
        self._budget.model_calls = model_calls
        self._budget.tool_calls = tool_calls
        return None

    def validate_pending(self, pending: PendingInvocation,
                         decisions: FileApprovalDecisionStore) -> str | None:
        if self._run_id != pending.run_id or self._node_id != pending.node_id:
            return "APPROVAL_REQUEST_MISMATCH"
        decision = decisions.load_for_request(pending.run_id, pending.request_id)
        if decision is None or decision.decision != "APPROVED":
            return "APPROVAL_NOT_GRANTED"
        if (decision.request_id != pending.request_id or
                decision.tool_name != pending.tool_name or
                decision.argument_digest != pending.argument_digest):
            return "APPROVAL_REQUEST_MISMATCH"
        return None

    def save_pending(self, request: Mapping[str, Any], messages: list[dict[str, Any]],
                     calls: Sequence[Mapping[str, Any]], rounds: list[dict[str, Any]],
                     *, into: str, route: str | None, state_version: int | None = None) -> bool:
        if self._pending_invocations is None or self._run_id is None or self._node_id is None:
            return True
        try:
            self._pending_invocations.save(PendingInvocation(
                run_id=self._run_id, node_id=self._node_id,
                request_id=request["request_id"], tool_name=request["tool_name"],
                argument_digest=request["argument_digest"], messages=messages,
                remaining_calls=[dict(call) for call in calls], rounds=rounds,
                into=into, route=route,
                model_calls=self._budget.model_calls or None,
                tool_calls=self._budget.tool_calls,
                state_version=self._state_version if state_version is None else state_version,
            ))
        except Exception:
            return False
        return True

    def invoke(self, name: str, arguments: Mapping[str, Any], *,
               approval_decisions: FileApprovalDecisionStore | None = None,
               pending_request_id: str | None = None) -> ToolResult:
        if not isinstance(name, str) or not name.strip():
            return ToolResult("<invalid>", ToolStatus.BLOCKED, reason_code="INVALID_TOOL_NAME")
        if self._invoke_allowed is not None and not self._invoke_allowed():
            return ToolResult(name, ToolStatus.FAILED, reason_code="CANCEL_REQUESTED")
        call_id = uuid4().hex
        if self._audit is not None and self._run_id is not None:
            try:
                self._audit.record(self._run_id, "tool.requested", "PENDING", call_id=call_id,
                                   node_id=self._node_id, execution_id=self._execution_id,
                                   summary={"tool": name, "argument_keys": sorted(arguments) if isinstance(arguments, Mapping) else []})
            except HarnessAuditError as exc:
                return ToolResult(name, ToolStatus.FAILED, reason_code=exc.reason_code)
        try:
            policy_reason = self._policy.authorize(name, arguments, self._registry)
        except Exception:
            policy_reason = "TOOL_POLICY_FAILED"
        if policy_reason is not None:
            result = ToolResult(name, ToolStatus.BLOCKED, reason_code=policy_reason)
            if self._audit is not None and self._run_id is not None:
                try:
                    self._audit.record(self._run_id, "tool.blocked", result.status.value, call_id=call_id,
                                       node_id=self._node_id, execution_id=self._execution_id,
                                       reason_code=policy_reason, summary={"tool": name})
                except HarnessAuditError:
                    return ToolResult(name, ToolStatus.FAILED, reason_code="AUDIT_WRITE_FAILED")
            return result
        lookup = getattr(self._registry, "get", None)
        tool = lookup(name) if callable(lookup) else None
        if tool is not None and getattr(tool, "requires_approval", False):
            required = getattr(tool, "required_capabilities", frozenset())
            if (not isinstance(self._granted_capabilities, frozenset)
                    or not required <= self._granted_capabilities):
                result = ToolResult(name, ToolStatus.BLOCKED, reason_code="CAPABILITY_DENIED")
                if self._audit is not None and self._run_id is not None:
                    try:
                        self._audit.record(self._run_id, "tool.blocked", result.status.value,
                                           call_id=call_id, node_id=self._node_id,
                                           execution_id=self._execution_id,
                                           reason_code=result.reason_code, summary={"tool": name})
                    except HarnessAuditError:
                        return ToolResult(name, ToolStatus.FAILED, reason_code="AUDIT_WRITE_FAILED")
                return result
            if approval_decisions is None:
                try:
                    safe_arguments = _json_snapshot(arguments)
                except (TypeError, ValueError, OverflowError):
                    return ToolResult(name, ToolStatus.BLOCKED, reason_code="INVALID_ARGUMENTS")
                digest = argument_digest(safe_arguments)
                request = {"request_id": uuid4().hex, "tool_name": name,
                           "argument_digest": digest}
                if getattr(tool, 'approval_description', None) is not None:
                    from .approval_display import clean_display
                    try:
                        request['display'] = clean_display({'description': tool.approval_description,
                            'fields': {key: safe_arguments[key] for key in tool.approval_fields}})
                    except (ValueError, KeyError, TypeError):
                        return ToolResult(name, ToolStatus.BLOCKED, reason_code='APPROVAL_DISPLAY_INVALID')
                if self._audit is not None and self._run_id is not None:
                    try:
                        self._audit.record(self._run_id, "tool.waiting_approval", "WAITING_APPROVAL",
                                           call_id=call_id, node_id=self._node_id,
                                           execution_id=self._execution_id,
                                           reason_code="APPROVAL_REQUIRED", summary=request)
                    except HarnessAuditError:
                        return ToolResult(name, ToolStatus.FAILED, reason_code="AUDIT_WRITE_FAILED")
                return ToolResult(name, ToolStatus.WAITING_APPROVAL, request,
                                  reason_code="APPROVAL_REQUIRED")
        retryable_tool = (approval_decisions is None and tool is not None and
                          (getattr(tool, "read_only", False) or
                           (getattr(tool, "side_effect", False) and
                            getattr(tool, "idempotency", None) is not None and
                            callable(getattr(self._registry, "invoke_idempotent", None)))))
        max_retries = self._retry_policy.max_retries if retryable_tool else 0
        key = None
        if approval_decisions is None and tool is not None and getattr(tool, "idempotency", None) is not None:
            try:
                key = f"{self._run_id}:{self._node_id}:{call_id}:{argument_digest(arguments)}"
            except (TypeError, ValueError, OverflowError):
                return ToolResult(name, ToolStatus.BLOCKED, reason_code="INVALID_ARGUMENTS")
        for attempt in range(1, max_retries + 2):
            claim_id = None
            if self._budget.tool_calls < self._budget.limits.max_tool_calls and self._run_budget is not None:
                try:
                    claim_id = self._run_budget.claim("tool", call_id, node_id=self._node_id,
                                                       execution_id=self._execution_id,
                                                       source_run_id=self._run_id)
                except (HarnessLimitError, HarnessAuditError) as exc:
                    if isinstance(exc, HarnessLimitError):
                        try:
                            self._audit.record(self._run_id, "tool.rejected", "FAILED",
                                               call_id=call_id, node_id=self._node_id,
                                               execution_id=self._execution_id,
                                               reason_code=exc.reason_code,
                                               summary={"tool": name, "budget": self._budget.usage()})
                        except HarnessAuditError as audit_exc:
                            return ToolResult(name, ToolStatus.FAILED,
                                              reason_code=audit_exc.reason_code)
                    return ToolResult(name, ToolStatus.FAILED, reason_code=exc.reason_code)
            if not self._budget.claim_tool_call():
                result = ToolResult(name, ToolStatus.FAILED, reason_code="MAX_TOOL_CALLS")
                if self._audit is not None and self._run_id is not None:
                    try:
                        self._audit.record(self._run_id, "tool.rejected", "FAILED", call_id=call_id,
                                           node_id=self._node_id, execution_id=self._execution_id,
                                           reason_code=result.reason_code,
                                           summary={"tool": name, "budget": self._budget.usage()})
                    except HarnessAuditError:
                        return ToolResult(name, ToolStatus.FAILED, reason_code="AUDIT_WRITE_FAILED")
                return result
            try:
                if approval_decisions is not None:
                    if self._run_id is None or self._pending_invocations is None:
                        result = ToolResult(name, ToolStatus.BLOCKED,
                                            reason_code="PENDING_INVOCATION_NOT_FOUND")
                    else:
                        result = self._registry.invoke_approved(
                            name, arguments, run_id=self._run_id,
                            decisions=approval_decisions,
                            pending_invocations=self._pending_invocations,
                            granted_capabilities=self._granted_capabilities,
                            request_id=pending_request_id,
                        )
                elif key is not None and callable(getattr(self._registry, "invoke_idempotent", None)):
                    result = self._registry.invoke_idempotent(
                        name, arguments, key, granted_capabilities=self._granted_capabilities)
                else:
                    result = self._registry.invoke(
                        name, arguments, granted_capabilities=self._granted_capabilities)
            except ApprovalConflictError as exc:
                result = ToolResult(name, ToolStatus.BLOCKED, reason_code=exc.reason_code)
            except OSError:
                pending = (self._pending_invocations.load(self._run_id)
                           if self._pending_invocations is not None and self._run_id is not None else None)
                reason = ("PENDING_INVOCATION_SAVE_FAILED" if pending is not None
                          and pending.execution_state == "PENDING" else "TOOL_OUTCOME_UNKNOWN")
                result = ToolResult(name, ToolStatus.FAILED, reason_code=reason)
            except Exception as exc:
                reason = getattr(exc, "reason_code", None)
                result = ToolResult(
                    name, ToolStatus.FAILED,
                    reason_code=reason if isinstance(reason, str) and reason.strip() else "TOOL_EXCEPTION",
                    error_type=type(exc).__name__,
                )
            if not isinstance(result, ToolResult):
                result = ToolResult(name, ToolStatus.FAILED, reason_code="INVALID_TOOL_RESULT")
            retry = (result.status is ToolStatus.FAILED and
                     result.reason_code in ("TOOL_TIMEOUT", "TOOL_UNREACHABLE") and
                     attempt <= max_retries and
                     self._budget.tool_calls < self._budget.limits.max_tool_calls and
                     (self._retry_allowed is None or self._retry_allowed()))
            backoff = min(self._retry_policy.backoff_seconds * (2 ** (attempt - 1)), 30.0) if retry else 0.0
            if max_retries and self._audit is not None and self._run_id is not None:
                try:
                    attempt_event = {
                        ToolStatus.FAILED: "tool.attempt_failed",
                        ToolStatus.BLOCKED: "tool.attempt_blocked",
                        ToolStatus.COMPLETED: "tool.attempt_completed",
                    }.get(result.status, "tool.attempt_blocked")
                    self._audit.record(self._run_id, attempt_event, result.status.value,
                                       call_id=call_id, node_id=self._node_id,
                                       execution_id=self._execution_id,
                                       reason_code=result.reason_code,
                                       summary={"tool": name, "attempt": attempt,
                                                "retry": retry, "backoff_seconds": backoff,
                                                "budget": self._budget.usage()})
                except HarnessAuditError:
                    return ToolResult(name, ToolStatus.FAILED, reason_code="AUDIT_WRITE_FAILED")
            if retry and claim_id is not None:
                try:
                    self._run_budget.settle(claim_id, "tool", call_id, node_id=self._node_id,
                                            execution_id=self._execution_id)
                    claim_id = None
                except HarnessAuditError as exc:
                    return ToolResult(name, ToolStatus.FAILED, reason_code=exc.reason_code)
            if not retry:
                break
            if backoff:
                sleep(backoff)
            if self._retry_allowed is not None and not self._retry_allowed():
                if self._audit is not None and self._run_id is not None:
                    try:
                        self._audit.record(self._run_id, "tool.retry_cancelled", "FAILED",
                                           call_id=call_id, node_id=self._node_id,
                                           execution_id=self._execution_id,
                                           reason_code="CANCEL_REQUESTED",
                                           summary={"tool": name, "attempt": attempt,
                                                    "budget": self._budget.usage()})
                    except HarnessAuditError:
                        return ToolResult(name, ToolStatus.FAILED, reason_code="AUDIT_WRITE_FAILED")
                break
        if not isinstance(result, ToolResult):
            result = ToolResult(name, ToolStatus.FAILED, reason_code="INVALID_TOOL_RESULT")
        if self._audit is not None and self._run_id is not None:
            try:
                self._audit.record(self._run_id, f"tool.{result.status.value.lower()}", result.status.value,
                                   call_id=call_id, node_id=self._node_id, execution_id=self._execution_id,
                                   reason_code=result.reason_code,
                                   summary={"tool": name, "output_keys": sorted(result.output),
                                            "budget": self._budget.usage()})
            except HarnessAuditError:
                return ToolResult(name, ToolStatus.FAILED, reason_code="AUDIT_WRITE_FAILED")
        if claim_id is not None:
            try:
                self._run_budget.settle(claim_id, "tool", call_id, node_id=self._node_id,
                                        execution_id=self._execution_id)
            except HarnessAuditError as exc:
                return ToolResult(name, ToolStatus.FAILED, reason_code=exc.reason_code)
        return result


class DefaultModelToolLoop:
    """Default model/tool loop; it owns no domain tools or runtime state."""

    def run(
        self, complete: Callable[[Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]], Mapping[str, Any]],
        messages: list[dict[str, Any]],
        schemas: Sequence[Mapping[str, Any]],
        pipeline: ToolInvocationPipeline,
        *, into: str, route: str | None,
    ) -> NodeResult:
        return self._run(complete, messages, schemas, pipeline, into=into,
                         route=route, rounds=[], pending=None, decisions=None)

    def resume_pending(
        self, complete: Callable[[Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]], Mapping[str, Any]],
        pending: PendingInvocation,
        schemas: Sequence[Mapping[str, Any]],
        pipeline: ToolInvocationPipeline,
        decisions: FileApprovalDecisionStore,
    ) -> NodeResult:
        """Continue from saved model facts; never repeat an uncertain tool call."""
        if not isinstance(pending, PendingInvocation):
            raise TypeError("pending must be PendingInvocation")
        approval_reason = pipeline.validate_pending(pending, decisions)
        if approval_reason is not None:
            return NodeResult.blocked(approval_reason)
        if pending.execution_state == "STARTED":
            return NodeResult.blocked("TOOL_OUTCOME_UNKNOWN")
        limit_reason = pipeline.restore_budget(pending)
        if limit_reason is not None:
            return NodeResult.failed(limit_reason)
        return self._run(complete, list(pending.messages), schemas, pipeline,
                         into=pending.into, route=pending.route, rounds=list(pending.rounds),
                         pending=pending, decisions=decisions)

    def _run(
        self, complete: Callable[[Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]], Mapping[str, Any]],
        messages: list[dict[str, Any]], schemas: Sequence[Mapping[str, Any]],
        pipeline: ToolInvocationPipeline, *, into: str, route: str | None,
        rounds: list[dict[str, Any]], pending: PendingInvocation | None,
        decisions: FileApprovalDecisionStore | None,
    ) -> NodeResult:
        saved_calls = pending.remaining_calls if pending is not None else None
        while True:
            if saved_calls is None:
                try:
                    reply = complete(messages, schemas)
                except Exception as exc:
                    reason = getattr(exc, "reason_code", None)
                    if isinstance(reason, str) and reason.strip():
                        return NodeResult.failed(reason)
                    return NodeResult.failed("MODEL_REQUEST_FAILED")
                tool_calls = reply.get("tool_calls") if isinstance(reply, Mapping) else None
                if not tool_calls:
                    content = reply.get("content") if isinstance(reply, Mapping) else None
                    if not isinstance(content, str):
                        return NodeResult.failed("MODEL_INVALID_REPLY")
                    return NodeResult.success({into: content, f"{into}_rounds": rounds}, route=route)
                if not isinstance(tool_calls, list):
                    return NodeResult.failed("MODEL_INVALID_REPLY")
            else:
                tool_calls = saved_calls
                saved_calls = None
            for call in tool_calls:
                if (
                    not isinstance(call, Mapping)
                    or not isinstance(call.get("id"), str)
                    or not call["id"].strip()
                    or not isinstance(call.get("name"), str)
                    or not call["name"].strip()
                    or not isinstance(call.get("arguments"), Mapping)
                ):
                    return NodeResult.failed("MODEL_INVALID_REPLY")
            if pending is None:
                messages.append({
                    "role": "assistant",
                    "content": reply.get("content"),
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {"name": call["name"], "arguments": json.dumps(call["arguments"], ensure_ascii=False)},
                        }
                        for call in tool_calls
                    ],
                })
            for index, call in enumerate(tool_calls):
                if pending is not None and index == 0:
                    if pending.execution_state == "COMPLETED":
                        try:
                            stored = pending.tool_result
                            result = ToolResult(
                                stored["name"], ToolStatus(stored["status"]),
                                stored["output"], stored["reason_code"], stored["error_type"],
                            )
                            if result.name != call["name"] or result.status not in (
                                    ToolStatus.COMPLETED, ToolStatus.FAILED):
                                raise ValueError("stored tool result does not match pending call")
                        except (KeyError, TypeError, ValueError):
                            return NodeResult.failed("PENDING_INVOCATION_INVALID")
                    else:
                        result = pipeline.invoke(call["name"], call["arguments"],
                                                 approval_decisions=decisions,
                                                 pending_request_id=pending.request_id)
                else:
                    result = pipeline.invoke(call["name"], call["arguments"])
                if pending is not None and index == 0:
                    pending = None
                if result.status is ToolStatus.WAITING_APPROVAL:
                    if not pipeline.save_pending(result.output, messages, tool_calls[index:], rounds,
                                                 into=into, route=route):
                        return NodeResult.failed("PENDING_INVOCATION_SAVE_FAILED")
                    return NodeResult.waiting_approval(result.output)
                if result.status is ToolStatus.BLOCKED:
                    return NodeResult.blocked(result.reason_code or "TOOL_BLOCKED")
                if result.status is ToolStatus.FAILED:
                    return NodeResult.failed(result.reason_code or "TOOL_FAILED")
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(dict(result.output), ensure_ascii=False),
                })
                rounds.append({
                    "tool": call["name"],
                    "arguments": dict(call["arguments"]),
                    "output": dict(result.output),
                })
        return NodeResult.failed("MAX_TOOL_ROUNDS")
