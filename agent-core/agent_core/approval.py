"""Durable decisions bound to one pending approval request."""

from __future__ import annotations

import json
import hashlib
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .runtime import RunResult, RunStatus, _validate_run_id
from .runtime import _json_snapshot
from .tools import ToolResult, ToolStatus


class ApprovalConflictError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def argument_digest(arguments: Mapping[str, Any]) -> str:
    safe = _json_snapshot(arguments)
    payload = json.dumps(safe, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApprovalDecision:
    run_id: str
    request_id: str
    tool_name: str
    argument_digest: str
    decision: str
    decided_at: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        _validate_run_id(self.run_id)
        for name in ("request_id", "tool_name", "argument_digest", "decided_at"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be nonempty")
        if self.decision not in ("APPROVED", "REJECTED"):
            raise ValueError("decision must be APPROVED or REJECTED")
        if self.schema_version != 1:
            raise ValueError("unsupported approval decision schema_version")
        try:
            datetime.fromisoformat(self.decided_at)
        except ValueError as exc:
            raise ValueError("decided_at must be ISO-8601") from exc

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class FileApprovalDecisionStore:
    """Immutable decisions bound to waiting requests; one file per run.

    A run may collect several decisions over its lifetime — one per waiting
    approval request. Each decision is written once, never changed, and
    validated against the run's current pending request when submitted.
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self._lock = threading.RLock()

    def _path(self, run_id: str) -> Path:
        _validate_run_id(run_id)
        return self.directory / f"{run_id}.approval.json"

    def load_decisions(self, run_id: str) -> tuple[ApprovalDecision, ...]:
        """Return every recorded decision for the run, oldest first."""
        with self._lock:
            path = self._path(run_id)
            if not path.is_file():
                return ()
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("approval record must be an object")
                entries = ([payload] if payload.get("schema_version", 1) == 1
                           else payload.get("decisions"))
                if payload.get("schema_version", 1) not in (1, 2) or not isinstance(entries, list):
                    raise ValueError("unsupported approval record schema_version")
                decisions = tuple(ApprovalDecision(**entry) for entry in entries)
                if any(decision.run_id != run_id for decision in decisions):
                    raise ValueError("approval run_id does not match filename")
                return decisions
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise ValueError("invalid approval decision record") from exc

    def load(self, run_id: str) -> ApprovalDecision | None:
        """Return the most recent decision for the run, or None."""
        decisions = self.load_decisions(run_id)
        return decisions[-1] if decisions else None

    def load_for_request(self, run_id: str, request_id: str) -> ApprovalDecision | None:
        """Return the decision bound to one waiting request, or None."""
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request_id must be nonempty")
        for decision in self.load_decisions(run_id):
            if decision.request_id == request_id:
                return decision
        return None

    def decide(self, result: RunResult, request_id: str, decision: str) -> ApprovalDecision:
        if not isinstance(result, RunResult):
            raise TypeError("result must be RunResult")
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request_id must be nonempty")
        if decision not in ("APPROVED", "REJECTED"):
            raise ValueError("decision must be APPROVED or REJECTED")
        if result.status is not RunStatus.WAITING_APPROVAL or not isinstance(result.pending_approval, Mapping):
            raise ApprovalConflictError("RUN_NOT_WAITING_APPROVAL")
        request = result.pending_approval
        if request_id != request.get("request_id"):
            raise ApprovalConflictError("APPROVAL_REQUEST_MISMATCH")
        with self._lock:
            existing = self.load_for_request(result.run_id, request_id)
            if existing is not None:
                if (existing.tool_name != request.get("tool_name")
                        or existing.argument_digest != request.get("argument_digest")):
                    raise ApprovalConflictError("APPROVAL_REQUEST_MISMATCH")
                if existing.decision != decision:
                    raise ApprovalConflictError("APPROVAL_DECISION_CONFLICT")
                return existing
            record = ApprovalDecision(
                result.run_id, request_id, request["tool_name"], request["argument_digest"],
                decision, datetime.now(timezone.utc).isoformat(),
            )
            self._append(result.run_id, record)
            return record

    def _append(self, run_id: str, record: ApprovalDecision) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        decisions = list(self.load_decisions(run_id))
        decisions.append(record)
        path = self._path(run_id)
        temporary = self.directory / f".{run_id}.{uuid4().hex}.approval.tmp"
        try:
            temporary.write_text(json.dumps({
                "schema_version": 2,
                "run_id": run_id,
                "decisions": [item.to_dict() for item in decisions],
            }, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            temporary.replace(path)
        except Exception:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise


@dataclass(frozen=True)
class PendingInvocation:
    run_id: str
    node_id: str
    request_id: str
    tool_name: str
    argument_digest: str
    messages: list[dict[str, Any]]
    remaining_calls: list[dict[str, Any]]
    rounds: list[dict[str, Any]]
    into: str
    route: str | None
    schema_version: int = 1
    execution_state: str = "PENDING"
    tool_result: Mapping[str, Any] | None = None
    model_calls: int | None = None
    tool_calls: int | None = None
    state_version: int | None = None

    def __post_init__(self) -> None:
        _validate_run_id(self.run_id)
        for name in ("node_id", "request_id", "tool_name", "argument_digest", "into"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be nonempty")
        if self.route is not None and (not isinstance(self.route, str) or not self.route.strip()):
            raise ValueError("route must be nonempty when provided")
        if not isinstance(self.messages, list) or not isinstance(self.remaining_calls, list) \
                or not isinstance(self.rounds, list) or not self.remaining_calls:
            raise ValueError("pending invocation requires message and call lists")
        if any(not isinstance(item, Mapping) for group in
               (self.messages, self.remaining_calls, self.rounds) for item in group):
            raise ValueError("pending invocation list entries must be objects")
        if self.remaining_calls[0].get("name") != self.tool_name:
            raise ValueError("first pending call must match the approved tool")
        arguments = self.remaining_calls[0].get("arguments")
        if not isinstance(arguments, Mapping) or argument_digest(arguments) != self.argument_digest:
            raise ValueError("pending call arguments do not match approval digest")
        if self.schema_version != 1:
            raise ValueError("unsupported pending invocation schema_version")
        if self.execution_state not in ("PENDING", "STARTED", "COMPLETED"):
            raise ValueError("invalid pending invocation execution_state")
        if (self.execution_state == "COMPLETED") != (self.tool_result is not None):
            raise ValueError("completed invocation requires a tool result")
        if self.model_calls is not None and (
                not isinstance(self.model_calls, int) or isinstance(self.model_calls, bool)
                or self.model_calls < 0):
            raise ValueError("model_calls must be a nonnegative integer")
        if self.tool_calls is not None and (
                not isinstance(self.tool_calls, int) or isinstance(self.tool_calls, bool)
                or self.tool_calls < 0):
            raise ValueError("tool_calls must be a nonnegative integer")
        if self.state_version is not None and (
                not isinstance(self.state_version, int) or isinstance(self.state_version, bool)
                or self.state_version < 0):
            raise ValueError("state_version must be a nonnegative integer")
        for name in ("messages", "remaining_calls", "rounds"):
            object.__setattr__(self, name, _json_snapshot({"items": getattr(self, name)})["items"])
        if self.tool_result is not None:
            object.__setattr__(self, "tool_result", _json_snapshot(self.tool_result))

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class FilePendingInvocationStore:
    """Private model-visible facts needed to resume without repeating a model request.

    A run may wait several times; each wait appends one invocation snapshot
    keyed by its approval request id. Execution states move one way:
    ``PENDING -> STARTED -> COMPLETED``. A snapshot stuck at ``STARTED``
    means the external outcome is unknown and requires operator
    reconciliation before the tool may be considered executed.
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self._lock = threading.RLock()

    def _path(self, run_id: str) -> Path:
        _validate_run_id(run_id)
        return self.directory / f"{run_id}.json"

    def save(self, invocation: PendingInvocation) -> None:
        if not isinstance(invocation, PendingInvocation):
            raise TypeError("invocation must be PendingInvocation")
        with self._lock:
            existing = self.load_for_request(invocation.run_id, invocation.request_id)
            if existing is not None:
                raise ApprovalConflictError("PENDING_INVOCATION_EXISTS")
            self._append(invocation)

    def _invocations(self, run_id: str) -> list[PendingInvocation]:
        return list(self.load_all(run_id))

    def _append(self, invocation: PendingInvocation) -> None:
        invocations = self._invocations(invocation.run_id)
        invocations.append(invocation)
        self._write_all(invocation.run_id, invocations)

    def _replace(self, run_id: str, updated: PendingInvocation) -> None:
        invocations = self._invocations(run_id)
        for index, item in enumerate(invocations):
            if item.request_id == updated.request_id:
                invocations[index] = updated
                break
        else:
            raise ApprovalConflictError("PENDING_INVOCATION_NOT_FOUND")
        self._write_all(run_id, invocations)

    def _write_all(self, run_id: str, invocations: list[PendingInvocation]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(run_id)
        temporary = self.directory / f".{run_id}.{uuid4().hex}.tmp"
        try:
            temporary.write_text(json.dumps({
                "schema_version": 2,
                "run_id": run_id,
                "invocations": [item.to_dict() for item in invocations],
            }, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            temporary.replace(path)
        except Exception:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise

    def load_all(self, run_id: str) -> tuple[PendingInvocation, ...]:
        with self._lock:
            path = self._path(run_id)
            if not path.is_file():
                return ()
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("pending invocation record must be an object")
                entries = ([payload] if payload.get("schema_version", 1) == 1
                           else payload.get("invocations"))
                if payload.get("schema_version", 1) not in (1, 2) or not isinstance(entries, list):
                    raise ValueError("unsupported pending invocation schema_version")
                invocations = tuple(PendingInvocation(**entry) for entry in entries)
                if any(item.run_id != run_id for item in invocations):
                    raise ValueError("pending invocation run_id does not match filename")
                return invocations
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                raise ValueError("invalid pending invocation record") from exc

    def load(self, run_id: str) -> PendingInvocation | None:
        """Return the most recent invocation snapshot for the run, or None."""
        invocations = self.load_all(run_id)
        return invocations[-1] if invocations else None

    def load_for_request(self, run_id: str, request_id: str) -> PendingInvocation | None:
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request_id must be nonempty")
        for invocation in self.load_all(run_id):
            if invocation.request_id == request_id:
                return invocation
        return None

    def active_for_node(self, run_id: str, node_id: str) -> PendingInvocation | None:
        """The snapshot a re-executed node should continue from.

        Prefers a still-unfinished snapshot (``PENDING``/``STARTED``) for the
        node; otherwise the latest one, so a completed result can be reused
        after an interrupted continuation.
        """
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError("node_id must be nonempty")
        matching = [item for item in self.load_all(run_id) if item.node_id == node_id]
        if not matching:
            return None
        for item in reversed(matching):
            if item.execution_state in ("PENDING", "STARTED"):
                return item
        return matching[-1]

    def for_node_visit(self, run_id: str, node_id: str, state_version: int) -> PendingInvocation | None:
        """Select facts for a checkpoint replay, never for a later graph visit."""
        if not isinstance(state_version, int) or isinstance(state_version, bool) or state_version < 0:
            raise ValueError("state_version must be a nonnegative integer")
        pending = self.active_for_node(run_id, node_id)
        if pending is None:
            return None
        if pending.state_version is None:
            raise ApprovalConflictError("APPROVAL_VISIT_UNKNOWN")
        if pending.state_version == state_version:
            return pending
        if pending.execution_state == "COMPLETED" and pending.state_version < state_version:
            return None
        raise ApprovalConflictError("APPROVAL_REQUEST_MISMATCH")

    def public_state(self, run_id: str) -> dict[str, Any] | None:
        """Argument-free request summary of the latest snapshot for operator surfaces."""
        invocation = self.load(run_id)
        if invocation is None:
            return None
        state = {
            "request_id": invocation.request_id,
            "tool_name": invocation.tool_name,
            "argument_digest": invocation.argument_digest,
            "node_id": invocation.node_id,
            "execution_state": invocation.execution_state,
        }
        if invocation.tool_result is not None and invocation.tool_result.get("resolved_by"):
            state["resolved_by"] = invocation.tool_result["resolved_by"]
        return state

    def claim_execution(self, decision: ApprovalDecision) -> PendingInvocation:
        """Persist STARTED before any external side effect; never claim twice."""
        if not isinstance(decision, ApprovalDecision) or decision.decision != "APPROVED":
            raise ApprovalConflictError("APPROVAL_NOT_GRANTED")
        with self._lock:
            pending = self.load_for_request(decision.run_id, decision.request_id)
            if pending is None:
                raise ApprovalConflictError("PENDING_INVOCATION_NOT_FOUND")
            if (pending.tool_name != decision.tool_name
                    or pending.argument_digest != decision.argument_digest):
                raise ApprovalConflictError("APPROVAL_REQUEST_MISMATCH")
            if pending.execution_state == "STARTED":
                raise ApprovalConflictError("TOOL_OUTCOME_UNKNOWN")
            if pending.execution_state == "COMPLETED":
                raise ApprovalConflictError("TOOL_ALREADY_EXECUTED")
            started = replace(pending, execution_state="STARTED")
            self._replace(decision.run_id, started)
            return started

    def record_result(self, decision: ApprovalDecision, result: ToolResult) -> PendingInvocation:
        if not isinstance(decision, ApprovalDecision) or decision.decision != "APPROVED":
            raise ApprovalConflictError("APPROVAL_NOT_GRANTED")
        if not isinstance(result, ToolResult) or result.status not in (ToolStatus.COMPLETED, ToolStatus.FAILED):
            raise ValueError("result must be a completed or failed ToolResult")
        with self._lock:
            pending = self.load_for_request(decision.run_id, decision.request_id)
            if pending is None or pending.tool_name != decision.tool_name \
                    or pending.argument_digest != decision.argument_digest:
                raise ApprovalConflictError("APPROVAL_REQUEST_MISMATCH")
            if pending.execution_state == "COMPLETED":
                raise ApprovalConflictError("TOOL_ALREADY_EXECUTED")
            if pending.execution_state != "STARTED" or result.name != pending.tool_name:
                raise ApprovalConflictError("TOOL_EXECUTION_NOT_STARTED")
            completed = replace(pending, execution_state="COMPLETED", tool_result={
                "name": result.name, "status": result.status.value,
                "output": dict(result.output), "reason_code": result.reason_code,
                "error_type": result.error_type,
            })
            self._replace(decision.run_id, completed)
            return completed

    def resolve_unknown(self, decision: ApprovalDecision, outcome: str, *,
                        output: Mapping[str, Any] | None = None,
                        reason_code: str | None = None,
                        error_type: str | None = None) -> PendingInvocation:
        """Record a human-verified outcome for a STARTED invocation.

        After a crash the external side effect may or may not have happened;
        only an explicit operator reconciliation turns ``STARTED`` into a
        reusable ``COMPLETED`` snapshot. The recorded result is marked
        ``resolved_by: "OPERATOR"`` so audits can distinguish it from a
        tool-reported result.
        """
        if not isinstance(decision, ApprovalDecision) or decision.decision != "APPROVED":
            raise ApprovalConflictError("APPROVAL_NOT_GRANTED")
        if outcome not in ("COMPLETED", "FAILED"):
            raise ValueError("outcome must be COMPLETED or FAILED")
        if outcome == "COMPLETED":
            if reason_code is not None:
                raise ValueError("completed resolution cannot provide a reason_code")
            if output is None:
                output = {}
            if not isinstance(output, Mapping):
                raise ValueError("output must be a mapping when provided")
        else:
            if not isinstance(reason_code, str) or not reason_code.strip():
                raise ValueError("failed resolution requires a reason_code")
            output = {}
        with self._lock:
            pending = self.load_for_request(decision.run_id, decision.request_id)
            if pending is None or pending.tool_name != decision.tool_name \
                    or pending.argument_digest != decision.argument_digest:
                raise ApprovalConflictError("APPROVAL_REQUEST_MISMATCH")
            if pending.execution_state == "COMPLETED":
                raise ApprovalConflictError("TOOL_ALREADY_EXECUTED")
            if pending.execution_state != "STARTED":
                raise ApprovalConflictError("TOOL_EXECUTION_NOT_STARTED")
            resolved = replace(pending, execution_state="COMPLETED", tool_result={
                "name": pending.tool_name, "status": outcome,
                "output": dict(output), "reason_code": reason_code,
                "error_type": error_type, "resolved_by": "OPERATOR",
            })
            self._replace(decision.run_id, resolved)
            return resolved
