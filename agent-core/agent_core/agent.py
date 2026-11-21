"""Composable Agent shell built on top of the graph runtime.

The shell deliberately does not implement an LLM, planner, or transport. Those
are application concerns and are injected through small protocols. This keeps
the runtime deterministic while giving applications one place to assemble an
agent's instructions, tools, model, memory, and graph.
"""

from __future__ import annotations

import copy
import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

from .graph import Graph, NodeContext
from .results import NodeResult
from .runtime import RunResult, Runtime, _json_snapshot
from .tools import ToolRegistry
from .harness import HarnessAudit, HarnessLimits, RunBudget
from .approval import FileApprovalDecisionStore, FilePendingInvocationStore
from .managed import ManagedCallStopped, NodeCallPolicy, NodeCalls


_active_run_budget: ContextVar[RunBudget | None] = ContextVar("active_run_budget", default=None)


class ModelAdapter(Protocol):
    """Application supplied model boundary.

    Implementations may return any JSON serializable mapping with a string
    ``content`` entry. The shell does not call the adapter automatically;
    nodes decide when model work is part of their workflow. Failures should
    raise; an exception carrying a ``reason_code`` string attribute is
    reported as that reason code, anything else as ``MODEL_REQUEST_FAILED``.
    """

    def complete(self, messages: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]: ...


class MemoryStore(Protocol):
    """Minimal memory boundary available to Agent nodes."""

    def read(self, key: str, default: Any = None) -> Any: ...

    def write(self, key: str, value: Any) -> None: ...


class InMemoryMemory:
    """Small process-local memory implementation for development and tests."""

    def __init__(self, initial: Mapping[str, Any] | None = None) -> None:
        if initial is not None and not isinstance(initial, Mapping):
            raise TypeError("initial memory must be a mapping")
        self._values = _json_snapshot(initial or {})
        self._lock = threading.RLock()

    def read(self, key: str, default: Any = None) -> Any:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("memory key must be nonempty")
        with self._lock:
            return copy.deepcopy(self._values.get(key, default))

    def write(self, key: str, value: Any) -> None:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("memory key must be nonempty")
        with self._lock:
            self._values[key] = _json_snapshot({"value": value})["value"]

    def snapshot(self) -> dict[str, Any]:
        """Return an isolated copy of all stored values for inspection."""
        with self._lock:
            return _json_snapshot(self._values)


@dataclass(frozen=True)
class AgentNodeContext:
    """Node context plus the dependencies assembled by an :class:`Agent`."""

    run_id: str
    node_id: str
    execution_id: str
    state_version: int
    input: Mapping[str, Any]
    state: Mapping[str, Any]
    instructions: str = ""
    agent_metadata: Mapping[str, Any] = field(default_factory=dict)
    model: ModelAdapter | None = None
    tools: ToolRegistry | None = None
    memory: MemoryStore | None = None
    audit: HarnessAudit | None = None
    run_budget: RunBudget | None = None
    pending_invocations: FilePendingInvocationStore | None = None
    approval_decisions: "FileApprovalDecisionStore | None" = None
    cancel_requested_fn: Callable[[], bool] | None = None
    call_policy: NodeCallPolicy | None = None
    _managed_calls: NodeCalls | None = field(default=None, init=False, repr=False, compare=False)
    _calls_closed: bool = field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.cancel_requested_fn is not None and not callable(self.cancel_requested_fn):
            raise TypeError("cancel_requested_fn must be callable when provided")
        if self.call_policy is not None and not isinstance(self.call_policy, NodeCallPolicy):
            raise TypeError("call_policy must be NodeCallPolicy")

    @property
    def calls(self) -> NodeCalls:
        """Managed model/tool calls using this node's assembly-owned policy."""
        if self._managed_calls is None:
            object.__setattr__(self, "_managed_calls", NodeCalls(self, self.call_policy))
            if self._calls_closed:
                self._managed_calls.close()
        return self._managed_calls

    @property
    def cancel_requested(self) -> bool:
        """Whether the runtime has requested cooperative cancellation."""
        return False if self.cancel_requested_fn is None else bool(self.cancel_requested_fn())


AgentNode = Callable[[AgentNodeContext], NodeResult]


@dataclass(frozen=True)
class AgentSpec:
    name: str
    instructions: str = ""
    description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("agent name must be nonempty")
        if not isinstance(self.instructions, str) or not isinstance(self.description, str):
            raise TypeError("instructions and description must be strings")
        object.__setattr__(self, "metadata", _json_snapshot(self.metadata))


@dataclass(frozen=True)
class AgentResult:
    """Agent-facing wrapper that preserves the complete runtime result."""

    agent: AgentSpec
    run: RunResult

    @property
    def run_id(self) -> str:
        return self.run.run_id

    @property
    def status(self):
        return self.run.status

    @property
    def reason_code(self) -> str | None:
        return self.run.reason_code

    @property
    def stopped_at(self) -> str | None:
        return self.run.stopped_at

    @property
    def state(self) -> Mapping[str, Any]:
        return self.run.state

    @property
    def trace(self):
        return self.run.trace


class AgentBuilder:
    """Build an Agent graph while keeping application dependencies explicit."""

    def __init__(
        self,
        name: str,
        *,
        instructions: str = "",
        description: str = "",
        model: ModelAdapter | None = None,
        tools: ToolRegistry | None = None,
        memory: MemoryStore | None = None,
        audit: HarnessAudit | None = None,
        run_limits: HarnessLimits | None = None,
        pending_invocations: FilePendingInvocationStore | None = None,
        approval_decisions: FileApprovalDecisionStore | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.spec = AgentSpec(name, instructions, description, metadata or {})
        self.model = model
        self.tools = tools
        self.memory = memory
        self.audit = audit
        if run_limits is not None and (not isinstance(run_limits, HarnessLimits)
                                       or audit is None or not getattr(audit.store, "durable", False)):
            raise ValueError("run_limits requires HarnessLimits and a durable audit store")
        self.run_limits = run_limits
        self.pending_invocations = pending_invocations
        self.approval_decisions = approval_decisions
        self._graph = Graph()

    def add_node(self, node_id: str, execute: AgentNode, *, calls: NodeCallPolicy | None = None) -> "AgentBuilder":
        if not callable(execute):
            raise TypeError("agent node execute must be callable")
        if calls is not None and not isinstance(calls, NodeCallPolicy):
            raise TypeError("calls must be NodeCallPolicy")

        def runtime_node(context: NodeContext) -> NodeResult:
            inherited = _active_run_budget.get()
            budget = inherited or (RunBudget(self.run_limits, self.audit, context.run_id)
                                   if self.run_limits is not None else None)
            token = _active_run_budget.set(budget)
            try:
                ctx = AgentNodeContext(
                    run_id=context.run_id,
                    node_id=context.node_id,
                    execution_id=context.execution_id,
                    state_version=context.state_version,
                    input=context.input,
                    state=context.state,
                    instructions=self.spec.instructions,
                    agent_metadata=self.spec.metadata,
                    model=self.model,
                    tools=self.tools,
                    memory=self.memory,
                    audit=self.audit or (budget.audit if budget is not None else None),
                    run_budget=budget,
                    pending_invocations=self.pending_invocations,
                    approval_decisions=self.approval_decisions,
                    cancel_requested_fn=getattr(context, "_cancel_requested", None),
                    call_policy=calls,
                )
                try:
                    result = execute(ctx)
                except ManagedCallStopped as exc:
                    result = exc.result
                except Exception:
                    if ctx._managed_calls is not None and ctx._managed_calls.stop_result is not None:
                        return ctx._managed_calls.stop_result
                    raise
                finally:
                    object.__setattr__(ctx, "_calls_closed", True)
                    if ctx._managed_calls is not None:
                        ctx._managed_calls.close()
                if ctx._managed_calls is not None and ctx._managed_calls.stop_result is not None:
                    return ctx._managed_calls.stop_result
                return result
            finally:
                _active_run_budget.reset(token)

        self._graph.add_node(node_id, runtime_node)
        return self

    def set_entry(self, node_id: str) -> "AgentBuilder":
        self._graph.set_entry(node_id)
        return self

    def add_edge(self, source: str, target: str, *, when: str | None = None) -> "AgentBuilder":
        self._graph.add_edge(source, target, when=when)
        return self

    def build(self, *, runtime: Runtime | None = None) -> "Agent":
        return Agent(
            self.spec,
            self._graph.compile(),
            model=self.model,
            tools=self.tools,
            memory=self.memory,
            audit=self.audit,
            run_limits=self.run_limits,
            pending_invocations=self.pending_invocations,
            approval_decisions=self.approval_decisions,
            runtime=runtime or Runtime(),
        )


class Agent:
    """Configured agent that executes a compiled graph."""

    def __init__(
        self,
        spec: AgentSpec,
        graph: Any,
        *,
        model: ModelAdapter | None = None,
        tools: ToolRegistry | None = None,
        memory: MemoryStore | None = None,
        audit: HarnessAudit | None = None,
        run_limits: HarnessLimits | None = None,
        pending_invocations: FilePendingInvocationStore | None = None,
        approval_decisions: FileApprovalDecisionStore | None = None,
        runtime: Runtime | None = None,
    ) -> None:
        self.spec = spec
        self.graph = graph
        self.model = model
        self.tools = tools
        self.memory = memory
        self.audit = audit
        self.run_limits = run_limits
        self.pending_invocations = pending_invocations
        self.approval_decisions = approval_decisions
        self.runtime = runtime or Runtime()

    def run(self, input_data: Mapping[str, Any], *, run_id: str | None = None) -> AgentResult:
        if not isinstance(input_data, Mapping):
            raise TypeError("input_data must be a mapping")
        run = self.runtime.run(self.graph, input_data, run_id=run_id)
        return AgentResult(self.spec, run)

    def describe(self) -> dict[str, Any]:
        """Return the compiled graph topology for introspection and UIs."""
        return {
            "entry": self.graph.entry,
            "nodes": list(self.graph.nodes),
            "edges": [
                {"source": edge.source, "target": edge.target, "when": edge.when}
                for edge in self.graph.edges
            ],
        }


__all__ = [
    "Agent", "AgentBuilder", "AgentNode", "AgentNodeContext", "AgentResult",
    "AgentSpec", "InMemoryMemory", "MemoryStore", "ModelAdapter",
]
