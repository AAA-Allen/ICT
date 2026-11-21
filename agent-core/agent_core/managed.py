"""Node-scoped public calls backed by the shared Harness pipelines."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence, TYPE_CHECKING, NoReturn

from .approval import ApprovalConflictError, argument_digest
from .harness import (
    ContextAssembler, ContextLimits, DefaultToolPolicy, HarnessBudget, HarnessLimits,
    HarnessLimitError, ModelInvocationPipeline, ModelRetryPolicy,
    ToolInvocationPipeline, ToolRetryPolicy,
)
from .results import NodeResult, NodeStatus
from .runtime import _json_snapshot
from .tools import ToolResult, ToolStatus

if TYPE_CHECKING:
    from .agent import AgentNodeContext


@dataclass(frozen=True)
class NodeCallPolicy:
    """Assembly-owned permissions and attempt limits for one node visit."""

    allowed_tools: frozenset[str] = frozenset()
    capabilities: frozenset[str] = frozenset()
    limits: HarnessLimits = field(default_factory=lambda: HarnessLimits(1, 1))
    model_retry: ModelRetryPolicy = field(default_factory=ModelRetryPolicy)
    tool_retry: ToolRetryPolicy = field(default_factory=ToolRetryPolicy)
    context: ContextLimits = field(default_factory=ContextLimits)

    def __post_init__(self) -> None:
        for name in ("allowed_tools", "capabilities"):
            values = getattr(self, name)
            if not isinstance(values, frozenset) or any(
                    not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"{name} must be a frozenset of nonempty strings")
        for name, kind in (("limits", HarnessLimits), ("model_retry", ModelRetryPolicy),
                           ("tool_retry", ToolRetryPolicy), ("context", ContextLimits)):
            if not isinstance(getattr(self, name), kind):
                raise TypeError(f"{name} must be {kind.__name__}")


class ManagedCallStopped(Exception):
    """A managed call stopped the node; the Agent shell preserves its result."""

    def __init__(self, result: NodeResult) -> None:
        if not isinstance(result, NodeResult) or result.status is NodeStatus.SUCCEEDED:
            raise ValueError("managed stop requires a non-success NodeResult")
        super().__init__(result.reason_code)
        self.result = result
        self.reason_code = result.reason_code


class NodeCalls:
    """One logical public call per visit, with shared pipeline construction.

    Arbitrary multi-call Python replay is intentionally not supported. Use
    graph nodes for sequential external operations; retries stay in Harness.
    """

    __slots__ = ("_context", "_policy", "_budget", "_stop_result", "_used", "_closed")

    def __init__(self, context: AgentNodeContext, policy: NodeCallPolicy | None) -> None:
        if policy is not None and not isinstance(policy, NodeCallPolicy):
            raise TypeError("policy must be NodeCallPolicy")
        self._context = context
        self._policy = policy
        self._budget = HarnessBudget(policy.limits if policy is not None else HarnessLimits(1, 1))
        self._stop_result: NodeResult | None = None
        self._used = False
        self._closed = False

    @property
    def stop_result(self) -> NodeResult | None:
        return self._stop_result

    def close(self) -> None:
        self._closed = True

    def _stop(self, result: NodeResult) -> NoReturn:
        if self._stop_result is None:
            self._stop_result = result
        raise ManagedCallStopped(self._stop_result)

    def _begin(self) -> None:
        if self._closed:
            self._stop(NodeResult.failed("MANAGED_CALLS_CLOSED"))
        if self._stop_result is not None:
            raise ManagedCallStopped(self._stop_result)
        if self._policy is None:
            self._stop(NodeResult.blocked("MANAGED_CALLS_NOT_CONFIGURED"))
        if self._used:
            self._stop(NodeResult.failed("MULTIPLE_MANAGED_CALLS"))
        self._used = True
        if self._context.cancel_requested:
            self._stop(NodeResult.failed("CANCEL_REQUESTED"))

    def _tool_pipeline(self) -> ToolInvocationPipeline:
        ctx, policy = self._context, self._policy
        return ToolInvocationPipeline(
            ctx.tools, granted_capabilities=policy.capabilities, budget=self._budget,
            audit=ctx.audit, run_id=ctx.run_id, node_id=ctx.node_id,
            execution_id=ctx.execution_id, policy=DefaultToolPolicy(policy.allowed_tools),
            pending_invocations=ctx.pending_invocations, retry_policy=policy.tool_retry,
            retry_allowed=lambda: not ctx.cancel_requested, run_budget=ctx.run_budget,
            state_version=ctx.state_version, invoke_allowed=lambda: not ctx.cancel_requested,
        )

    def _model_pipeline(self, complete: Callable, *, retry_allowed: Callable | None = None) -> ModelInvocationPipeline:
        ctx, policy = self._context, self._policy

        def invoke(messages, schemas):
            if ctx.cancel_requested:
                raise HarnessLimitError("CANCEL_REQUESTED")
            return complete(messages, schemas)

        return ModelInvocationPipeline(
            invoke, self._budget, audit=ctx.audit, run_id=ctx.run_id,
            node_id=ctx.node_id, execution_id=ctx.execution_id,
            context=ContextAssembler(policy.context), retry_policy=policy.model_retry,
            retry_allowed=lambda exc: not ctx.cancel_requested and
            (retry_allowed is None or retry_allowed(exc)), run_budget=ctx.run_budget,
        )

    def model(self, messages: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        """Return a JSON reply, or stop the node with an explicit result."""
        self._begin()
        ctx = self._context
        if ctx.model is None:
            self._stop(NodeResult.blocked("MODEL_NOT_CONFIGURED"))
        if ctx.pending_invocations is not None:
            try:
                saved = ctx.pending_invocations.for_node_visit(ctx.run_id, ctx.node_id, ctx.state_version)
            except ApprovalConflictError as exc:
                self._stop(NodeResult.blocked(exc.reason_code))
            except (ValueError, AttributeError):
                self._stop(NodeResult.failed("PENDING_INVOCATION_INVALID"))
            if saved is not None:
                self._stop(NodeResult.blocked("APPROVAL_REQUEST_MISMATCH"))
        stream_fn = getattr(ctx.model, "complete_stream", None)
        bus = None
        emitted = False

        def on_delta(text):
            nonlocal emitted
            emitted = True
            bus.publish(ctx.run_id, ctx.node_id, text)

        try:
            if callable(stream_fn):
                from .streaming import default_bus

                bus = default_bus()
                bus.open(ctx.run_id)
                complete = lambda current, schemas: stream_fn(current, on_delta=on_delta)
            else:
                complete = lambda current, schemas: ctx.model.complete(current)
            reply = self._model_pipeline(complete, retry_allowed=lambda exc: not emitted).complete(messages, ())
        except Exception as exc:
            reason = getattr(exc, "reason_code", None)
            self._stop(NodeResult.failed(reason if isinstance(reason, str) and reason.strip()
                                         else "MODEL_REQUEST_FAILED"))
        finally:
            if bus is not None:
                bus.close(ctx.run_id, retain_active=True)
        if not isinstance(reply, Mapping) or not isinstance(reply.get("content"), str):
            self._stop(NodeResult.failed("MODEL_INVALID_REPLY"))
        try:
            return _json_snapshot(reply)
        except (TypeError, ValueError, OverflowError):
            self._stop(NodeResult.failed("MODEL_INVALID_REPLY"))

    def tool(self, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return a tool output, or stop on failure, denial or approval wait."""
        self._begin()
        result = self._tool_result(name, arguments, into="tool_result", route=None)
        if result.status is not NodeStatus.SUCCEEDED:
            self._stop(result)
        return _json_snapshot(result.updates["tool_result"])

    def _tool_result(self, name: str, arguments: Mapping[str, Any], *, into: str, route: str | None) -> NodeResult:
        """Shared explicit-tool execution and M8 approval recovery."""
        ctx = self._context
        if ctx.cancel_requested:
            return NodeResult.failed("CANCEL_REQUESTED")
        if ctx.tools is None:
            return NodeResult.failed("TOOL_REGISTRY_MISSING")
        if not isinstance(arguments, Mapping):
            return NodeResult.blocked("INVALID_ARGUMENTS")
        try:
            arguments = _json_snapshot(arguments)
        except (TypeError, ValueError, OverflowError):
            return NodeResult.blocked("INVALID_ARGUMENTS")
        pipeline = self._tool_pipeline()
        saved = None
        if ctx.pending_invocations is not None:
            try:
                saved = ctx.pending_invocations.for_node_visit(ctx.run_id, ctx.node_id, ctx.state_version)
            except ApprovalConflictError as exc:
                return NodeResult.blocked(exc.reason_code)
            except (ValueError, AttributeError):
                return NodeResult.failed("PENDING_INVOCATION_INVALID")
        if saved is not None:
            if (saved.tool_name != name or saved.argument_digest != argument_digest(arguments)
                    or saved.into != into or saved.route != route):
                return NodeResult.blocked("APPROVAL_REQUEST_MISMATCH")
            policy_reason = DefaultToolPolicy(self._policy.allowed_tools).authorize(name, arguments, ctx.tools)
            if policy_reason is not None:
                return NodeResult.blocked(policy_reason)
            tool = ctx.tools.get(name)
            if tool is not None and not tool.required_capabilities <= self._policy.capabilities:
                return NodeResult.blocked("CAPABILITY_DENIED")
            if ctx.approval_decisions is None:
                return NodeResult.blocked("APPROVAL_REQUIRED")
            reason = pipeline.validate_pending(saved, ctx.approval_decisions)
            if reason is not None:
                return NodeResult.blocked(reason)
            if saved.execution_state == "STARTED":
                return NodeResult.blocked("TOOL_OUTCOME_UNKNOWN")
            reason = pipeline.restore_budget(saved)
            if reason is not None:
                return NodeResult.failed(reason)
            if saved.execution_state == "COMPLETED":
                try:
                    stored = saved.tool_result
                    result = ToolResult(stored["name"], ToolStatus(stored["status"]), stored["output"],
                                        stored["reason_code"], stored["error_type"])
                    if result.name != name or result.status not in (ToolStatus.COMPLETED, ToolStatus.FAILED):
                        raise ValueError("stored tool result mismatch")
                except (KeyError, TypeError, ValueError):
                    return NodeResult.failed("PENDING_INVOCATION_INVALID")
            else:
                result = pipeline.invoke(name, arguments, approval_decisions=ctx.approval_decisions,
                                         pending_request_id=saved.request_id)
        else:
            result = pipeline.invoke(name, arguments)
        if result.status is ToolStatus.WAITING_APPROVAL:
            if not pipeline.save_pending(result.output, [], [{"name": name, "arguments": arguments}], [],
                                         into=into, route=route):
                return NodeResult.failed("PENDING_INVOCATION_SAVE_FAILED")
            return NodeResult.waiting_approval(result.output)
        if result.status is ToolStatus.COMPLETED:
            return NodeResult.success({into: dict(result.output)}, route=route)
        if result.status is ToolStatus.BLOCKED:
            return NodeResult.blocked(result.reason_code or "TOOL_BLOCKED")
        return NodeResult.failed(result.reason_code or "TOOL_FAILED")
