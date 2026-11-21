"""Approval demo server: a side-effectful tool that waits for human approval.

Start it with ``python examples/approval_server.py``, open the workbench,
submit a task, then approve the wait from the UI (or via
``POST /api/runs/{run_id}/approval-decision`` followed by the explicit
``POST /api/runs/{run_id}/resume``). The demo model is scripted, so no real
model endpoint is needed.
"""

from pathlib import Path

from agent_core import Tool, ToolRegistry
from agent_core.server import create_server, register_agents_from_config


class ScriptedArchiveModel:
    """Demo model: requests the archive tool once, then summarizes."""

    def complete_with_tools(self, messages, schemas):
        if not any(message.get("role") == "tool" for message in messages):
            return {"tool_calls": [{"id": "call-1", "name": "archive_record",
                                    "arguments": {"record": "demo-record"}}]}
        return {"content": "记录已归档并核对完毕。"}


def main() -> int:
    tools = ToolRegistry()
    tools.register(Tool(
        "archive_record",
        lambda args: {"archived": True, "record": args.get("record")},
        side_effect=True, requires_approval=True,
    ))
    server = create_server(port=8000)
    register_agents_from_config(
        str(Path(__file__).parent / "approval_agent.json"),
        server.registry, runtime=server.runtime, tools=tools,
        model=ScriptedArchiveModel(), audit=server.harness_audit,
        pending_invocations=server.pending_invocations,
        approval_decisions=server.approval_decisions,
    )
    bound_host, bound_port = server.server_address[:2]
    print(f"approval demo listening on http://{bound_host}:{bound_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
