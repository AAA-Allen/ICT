"""Domain-neutral agent workflow runtime."""

try:
    from importlib.metadata import version as _package_version

    __version__ = _package_version("agent-core")
except Exception:  # pragma: no cover - package not installed (repo checkout)
    __version__ = "0.4.0"

from .persistence import RunStore
from .approval import (
    ApprovalConflictError, ApprovalDecision, FileApprovalDecisionStore,
    FilePendingInvocationStore, PendingInvocation,
)
from .runtime import RunConflictError, RunContext, RunResult, RunStatus, Step, StepOutcome, StepStatus, TraceEvent, WorkflowRunner
from .tools import Tool, ToolIdempotency, ToolRegistry, ToolResult, ToolStatus, graph_tool_node, tool_step
from .graph import CompiledGraph, Edge, Graph, Node, NodeContext
from .results import NodeResult, NodeStatus
from .runtime import Runtime
from .checkpoint import Checkpoint, CheckpointStore, FileCheckpointStore, InMemoryCheckpointStore
from .lifecycle import LifecycleEvent, LifecycleStatus, RunLifecycle
from .harness import (
    ContextAssembler, ContextLimitError, ContextLimits, DefaultModelToolLoop, DefaultToolPolicy, FileHarnessAuditStore, HarnessAudit, HarnessAuditError,
    HarnessBudget, HarnessEvent, HarnessLimits, InMemoryHarnessAuditStore, RunBudget,
    ModelInvocationPipeline, ModelRetryPolicy, ModelToolLoop, ToolInvocationPipeline, ToolInvoker, ToolRetryPolicy,
)
from .agent import (
    Agent, AgentBuilder, AgentNode, AgentNodeContext, AgentResult, AgentSpec,
    InMemoryMemory, MemoryStore, ModelAdapter,
)
from .managed import ManagedCallStopped, NodeCallPolicy

__all__ = [
    "ApprovalConflictError", "ApprovalDecision", "FileApprovalDecisionStore",
    "FilePendingInvocationStore", "PendingInvocation",
    "RunContext", "RunResult", "RunStatus", "Step", "StepOutcome", "StepStatus",
    "RunConflictError",
    "TraceEvent", "WorkflowRunner", "Tool", "ToolIdempotency", "ToolRegistry", "ToolResult", "ToolStatus", "tool_step", "graph_tool_node",
    "RunStore",
    "Graph", "CompiledGraph", "Node", "Edge", "NodeContext", "Runtime",
    "NodeResult", "NodeStatus",
    "Checkpoint", "CheckpointStore", "InMemoryCheckpointStore", "FileCheckpointStore",
    "LifecycleEvent", "LifecycleStatus", "RunLifecycle",
    "ContextAssembler", "ContextLimitError", "ContextLimits", "DefaultModelToolLoop", "DefaultToolPolicy", "HarnessBudget", "HarnessLimits", "RunBudget", "ModelInvocationPipeline", "ModelRetryPolicy", "ModelToolLoop", "ToolInvocationPipeline", "ToolInvoker", "ToolRetryPolicy",
    "HarnessAudit", "HarnessAuditError", "HarnessEvent", "InMemoryHarnessAuditStore", "FileHarnessAuditStore",
    "Agent", "AgentBuilder", "AgentNode", "AgentNodeContext", "AgentResult", "AgentSpec",
    "InMemoryMemory", "MemoryStore", "ModelAdapter",
    "ManagedCallStopped", "NodeCallPolicy",
    "__version__",
]
