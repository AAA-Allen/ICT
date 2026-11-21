"""Public custom-node calls, using a clearly scripted model for local demo.

Run: python -m examples.managed_calls
Pass a real ModelAdapter to build_agent() to replace the scripted reply.
"""

import json
from pathlib import Path

from agent_core import (
    AgentBuilder, FileHarnessAuditStore, HarnessAudit, HarnessLimits,
    NodeCallPolicy, NodeResult, RunStore, Runtime, Tool, ToolRegistry,
)


class ScriptedSummaryModel:
    """A deterministic adapter for API verification, not an LLM."""

    def complete(self, messages):
        return {"content": f"Scripted summary: {messages[-1]['content']}"}


def build_agent(directory: Path, *, model=None):
    tools = ToolRegistry()
    tools.register(Tool("measure_text", lambda args: {"characters": len(args["text"])}, read_only=True))

    def measure(ctx):
        # Previously: ctx.tools.invoke(...); now policies and stops are shared.
        result = ctx.calls.tool("measure_text", {"text": ctx.state["text"]})
        return NodeResult.success({"characters": result["characters"]})

    def summarize(ctx):
        # Previously: ctx.model.complete(...); now budget/context/audit apply.
        reply = ctx.calls.model([{
            "role": "user", "content": f"Input contains {ctx.state['characters']} characters.",
        }])
        return NodeResult.success({"summary": reply["content"]})

    audit = HarnessAudit(FileHarnessAuditStore(directory))
    runtime = Runtime(store=RunStore(directory))
    return (AgentBuilder("managed-calls", tools=tools,
                         model=ScriptedSummaryModel() if model is None else model,
                         audit=audit, run_limits=HarnessLimits(1, 1))
            .add_node("measure", measure, calls=NodeCallPolicy(allowed_tools=frozenset({"measure_text"})))
            .add_node("summarize", summarize, calls=NodeCallPolicy())
            .set_entry("measure").add_edge("measure", "summarize").build(runtime=runtime))


if __name__ == "__main__":
    agent = build_agent(Path("outputs/examples/managed_calls"))
    result = agent.run({"text": "A custom node using the shared Harness."})
    print(json.dumps({"model_kind": "scripted-demo", "run_id": result.run_id,
                      "status": result.status.value, "state": result.state}, ensure_ascii=False, indent=2))
