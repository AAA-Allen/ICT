"""Domain-neutral tool registration and fail-closed invocation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import threading
from typing import Any, Callable, Mapping

from .runtime import RunContext, Step, StepOutcome, StepStatus, _json_snapshot
from .graph import NodeContext
from .results import NodeResult, NodeStatus


class ToolStatus(str, Enum):
    COMPLETED = "COMPLETED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ToolIdempotency:
    """Application-owned keyed invocation and external result lookup."""

    invoke: Callable[[Mapping[str, Any], str], Mapping[str, Any]]
    lookup: Callable[[str], Mapping[str, Any] | None]

    def __post_init__(self) -> None:
        if not callable(self.invoke) or not callable(self.lookup):
            raise TypeError("idempotency invoke and lookup must be callable")


@dataclass(frozen=True)
class Tool:
    name: str
    invoke: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    required_capabilities: frozenset[str] = field(default_factory=frozenset)
    read_only: bool = False
    side_effect: bool = False
    requires_approval: bool = False
    idempotency: ToolIdempotency | None = None
    approval_description: str | None = None
    approval_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (not isinstance(self.approval_fields, tuple) or len(self.approval_fields) > 8
                or any(not isinstance(key, str) or not key.isidentifier() or len(key) > 64
                       for key in self.approval_fields)
                or len(set(self.approval_fields)) != len(self.approval_fields)):
            raise ValueError('approval_fields must be a tuple of distinct top-level field names')
        if self.approval_description is not None:
            if (not self.requires_approval or not isinstance(self.approval_description, str)
                    or not 1 <= len(self.approval_description.strip()) <= 500):
                raise ValueError('approval description requires an approval tool and bounded text')
        elif self.approval_fields:
            raise ValueError('approval_fields require an approval description')
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("tool name must be nonempty")
        if not callable(self.invoke):
            raise TypeError("tool invoke must be callable")
        if not isinstance(self.required_capabilities, frozenset) or any(
            not isinstance(item, str) or not item.strip() for item in self.required_capabilities
        ):
            raise ValueError("required_capabilities must be a frozenset of nonempty strings")
        for value, name in ((self.read_only, "read_only"), (self.side_effect, "side_effect"),
                            (self.requires_approval, "requires_approval")):
            if not isinstance(value, bool):
                raise TypeError(f"{name} must be a boolean")
        if self.read_only and self.side_effect:
            raise ValueError("read_only and side_effect cannot both be true")
        if self.requires_approval and not self.side_effect:
            raise ValueError("requires_approval requires side_effect=True")
        if self.idempotency is not None and (
                not isinstance(self.idempotency, ToolIdempotency) or not self.side_effect):
            raise ValueError("idempotency requires a side-effect tool and ToolIdempotency")


@dataclass(frozen=True)
class ToolResult:
    name: str
    status: ToolStatus
    output: Mapping[str, Any] = field(default_factory=dict)
    reason_code: str | None = None
    error_type: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("tool result name must be nonempty")
        if not isinstance(self.status, ToolStatus):
            raise TypeError("tool result status must be a ToolStatus")
        if not isinstance(self.output, Mapping):
            raise TypeError("tool result output must be a mapping")
        if self.reason_code is not None and (
            not isinstance(self.reason_code, str) or not self.reason_code.strip()
        ):
            raise ValueError("tool result reason_code must be nonempty")
        if self.error_type is not None and (
            not isinstance(self.error_type, str) or not self.error_type.strip()
        ):
            raise ValueError("tool result error_type must be nonempty")
        if self.status is ToolStatus.COMPLETED and self.reason_code is not None:
            raise ValueError("completed tools cannot provide a reason_code")
        if self.status is not ToolStatus.COMPLETED and not self.reason_code:
            raise ValueError("non-completed tools require a reason_code")
        if self.status is not ToolStatus.FAILED and self.error_type is not None:
            raise ValueError("only failed tools can provide an error_type")
        object.__setattr__(self, "output", _json_snapshot(self.output))


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._lock = threading.RLock()

    def register(self, tool: Tool) -> None:
        if not isinstance(tool, Tool):
            raise TypeError("tool must be a Tool")
        with self._lock:
            if tool.name in self._tools:
                raise ValueError(f"tool already registered: {tool.name}")
            self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        if not isinstance(name, str):
            return None
        with self._lock:
            return self._tools.get(name)

    def describe(self, names: set[str] | frozenset[str] | None = None) -> tuple[dict[str, Any], ...]:
        with self._lock:
            tools = [tool for tool_name, tool in self._tools.items()
                     if names is None or tool_name in names]
        return tuple({
            "name": tool.name,
            "required_capabilities": sorted(tool.required_capabilities),
            "read_only": tool.read_only,
            "side_effect": tool.side_effect,
            "requires_approval": tool.requires_approval,
            "idempotent": tool.idempotency is not None,
        } for tool in tools)

    def invoke(
        self, name: str, arguments: Mapping[str, Any], *,
        granted_capabilities: frozenset[str] = frozenset(),
    ) -> ToolResult:
        if not isinstance(name, str) or not name.strip():
            return ToolResult("<invalid>", ToolStatus.BLOCKED, reason_code="INVALID_TOOL_NAME")
        with self._lock:
            tool = self._tools.get(name)
        if tool is None:
            return ToolResult(name, ToolStatus.BLOCKED, reason_code="TOOL_NOT_FOUND")
        if (not isinstance(granted_capabilities, frozenset)
                or any(not isinstance(item, str) for item in granted_capabilities)
                or not tool.required_capabilities <= granted_capabilities):
            return ToolResult(name, ToolStatus.BLOCKED, reason_code="CAPABILITY_DENIED")
        if tool.requires_approval:
            return ToolResult(name, ToolStatus.BLOCKED, reason_code="TOOL_APPROVAL_REQUIRED")
        return self._invoke_tool(tool, arguments)

    def invoke_idempotent(
        self, name: str, arguments: Mapping[str, Any], key: str, *,
        granted_capabilities: frozenset[str] = frozenset(),
    ) -> ToolResult:
        if not isinstance(name, str) or not name.strip():
            return ToolResult("<invalid>", ToolStatus.BLOCKED, reason_code="INVALID_TOOL_NAME")
        if not isinstance(key, str) or not key.strip():
            return ToolResult(name, ToolStatus.BLOCKED, reason_code="INVALID_IDEMPOTENCY_KEY")
        with self._lock:
            tool = self._tools.get(name)
        if tool is None:
            return ToolResult(name, ToolStatus.BLOCKED, reason_code="TOOL_NOT_FOUND")
        if tool.requires_approval:
            return ToolResult(name, ToolStatus.BLOCKED, reason_code="TOOL_APPROVAL_REQUIRED")
        if tool.idempotency is None:
            return ToolResult(name, ToolStatus.BLOCKED, reason_code="IDEMPOTENCY_NOT_CONFIGURED")
        if (not isinstance(granted_capabilities, frozenset)
                or any(not isinstance(item, str) for item in granted_capabilities)
                or not tool.required_capabilities <= granted_capabilities):
            return ToolResult(name, ToolStatus.BLOCKED, reason_code="CAPABILITY_DENIED")
        try:
            safe_arguments = _json_snapshot(arguments)
        except (TypeError, ValueError, OverflowError):
            return ToolResult(name, ToolStatus.BLOCKED, reason_code="INVALID_ARGUMENTS")
        try:
            previous = tool.idempotency.lookup(key)
        except Exception:
            return ToolResult(name, ToolStatus.FAILED, reason_code="IDEMPOTENCY_CHECK_FAILED")
        if previous is not None:
            return self._tool_output(name, previous)
        try:
            output = tool.idempotency.invoke(safe_arguments, key)
        except Exception as exc:
            reason = getattr(exc, "reason_code", None)
            return ToolResult(name, ToolStatus.FAILED,
                              reason_code=reason if isinstance(reason, str) and reason.strip()
                              else "TOOL_EXCEPTION", error_type=type(exc).__name__)
        return self._tool_output(name, output)

    @staticmethod
    def _tool_output(name: str, output: Any) -> ToolResult:
        if not isinstance(output, Mapping):
            return ToolResult(name, ToolStatus.FAILED, reason_code="INVALID_TOOL_OUTPUT")
        try:
            return ToolResult(name, ToolStatus.COMPLETED, output)
        except (TypeError, ValueError, OverflowError):
            return ToolResult(name, ToolStatus.FAILED, reason_code="INVALID_TOOL_OUTPUT")

    def invoke_approved(
        self, name: str, arguments: Mapping[str, Any], *,
        run_id: str, decisions: Any, pending_invocations: Any,
        granted_capabilities: frozenset[str] = frozenset(),
        request_id: str | None = None,
    ) -> ToolResult:
        """Execute one approved side effect after its STARTED claim is durable."""
        from .approval import ApprovalConflictError, argument_digest

        if not isinstance(name, str) or not name.strip():
            return ToolResult("<invalid>", ToolStatus.BLOCKED, reason_code="INVALID_TOOL_NAME")
        with self._lock:
            tool = self._tools.get(name)
        if tool is None:
            return ToolResult(name, ToolStatus.BLOCKED, reason_code="TOOL_NOT_FOUND")
        if not tool.requires_approval:
            return ToolResult(name, ToolStatus.BLOCKED, reason_code="TOOL_APPROVAL_NOT_REQUIRED")
        if (not isinstance(granted_capabilities, frozenset)
                or any(not isinstance(item, str) for item in granted_capabilities)
                or not tool.required_capabilities <= granted_capabilities):
            return ToolResult(name, ToolStatus.BLOCKED, reason_code="CAPABILITY_DENIED")
        if not isinstance(arguments, Mapping):
            return ToolResult(name, ToolStatus.BLOCKED, reason_code="INVALID_ARGUMENTS")
        try:
            safe_arguments = _json_snapshot(arguments)
        except (TypeError, ValueError, OverflowError):
            return ToolResult(name, ToolStatus.BLOCKED, reason_code="INVALID_ARGUMENTS")
        decision = (decisions.load_for_request(run_id, request_id)
                    if request_id is not None else decisions.load(run_id))
        if decision is None or decision.decision != "APPROVED":
            raise ApprovalConflictError("APPROVAL_NOT_GRANTED")
        if decision.tool_name != name or decision.argument_digest != argument_digest(safe_arguments):
            raise ApprovalConflictError("APPROVAL_REQUEST_MISMATCH")
        pending_invocations.claim_execution(decision)
        result = self._invoke_tool(tool, safe_arguments)
        pending_invocations.record_result(decision, result)
        return result

    @staticmethod
    def _invoke_tool(tool: Tool, arguments: Mapping[str, Any]) -> ToolResult:
        name = tool.name
        if not isinstance(arguments, Mapping):
            return ToolResult(name, ToolStatus.BLOCKED, reason_code="INVALID_ARGUMENTS")
        try:
            safe_arguments = _json_snapshot(arguments)
        except (TypeError, ValueError, OverflowError):
            return ToolResult(name, ToolStatus.BLOCKED, reason_code="INVALID_ARGUMENTS")
        try:
            output = tool.invoke(safe_arguments)
        except Exception as exc:
            reason = getattr(exc, "reason_code", None)
            if isinstance(reason, str) and reason.strip():
                return ToolResult(name, ToolStatus.FAILED, reason_code=reason,
                                  error_type=type(exc).__name__)
            return ToolResult(name, ToolStatus.FAILED, reason_code="TOOL_EXCEPTION",
                              error_type=type(exc).__name__)
        if not isinstance(output, Mapping):
            return ToolResult(name, ToolStatus.FAILED, reason_code="INVALID_TOOL_OUTPUT")
        try:
            safe_output = _json_snapshot(output)
        except (TypeError, ValueError, OverflowError):
            return ToolResult(name, ToolStatus.FAILED, reason_code="INVALID_TOOL_OUTPUT")
        return ToolResult(name, ToolStatus.COMPLETED, safe_output)


def tool_step(
    step_name: str, tool_name: str, registry: ToolRegistry,
    arguments: Callable[[RunContext], Mapping[str, Any]], *,
    granted_capabilities: frozenset[str] = frozenset(),
) -> Step:
    """Bind a registered tool to a workflow step with fixed capabilities."""
    if not isinstance(registry, ToolRegistry):
        raise TypeError("registry must be a ToolRegistry")
    if not callable(arguments):
        raise TypeError("arguments must be callable")

    def execute(context: RunContext) -> StepOutcome:
        result = registry.invoke(
            tool_name, arguments(context), granted_capabilities=granted_capabilities,
        )
        status = {
            ToolStatus.COMPLETED: StepStatus.COMPLETED,
            ToolStatus.BLOCKED: StepStatus.BLOCKED,
            ToolStatus.FAILED: StepStatus.FAILED,
        }[result.status]
        return StepOutcome(status, result.output, result.reason_code)

    return Step(step_name, execute)


def graph_tool_node(
    tool_name: str,
    registry: ToolRegistry,
    arguments: Callable[[NodeContext], Mapping[str, Any]],
    *,
    granted_capabilities: frozenset[str] = frozenset(),
) -> Callable[[NodeContext], NodeResult]:
    """Adapt a registered Tool into a v0.1 Graph node."""
    if not isinstance(registry, ToolRegistry):
        raise TypeError("registry must be a ToolRegistry")
    if not callable(arguments):
        raise TypeError("arguments must be callable")

    def execute(context: NodeContext) -> NodeResult:
        result = registry.invoke(
            tool_name, arguments(context), granted_capabilities=granted_capabilities,
        )
        status = {
            ToolStatus.COMPLETED: NodeStatus.SUCCEEDED,
            ToolStatus.BLOCKED: NodeStatus.BLOCKED,
            ToolStatus.FAILED: NodeStatus.FAILED,
        }[result.status]
        return NodeResult(
            status, result.output, reason_code=result.reason_code,
            output=result.output,
        )

    return execute
