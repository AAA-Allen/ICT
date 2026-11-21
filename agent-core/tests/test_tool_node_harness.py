"""Harness contracts for explicit, non-model-driven tool nodes."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agent_core import (
    AgentNodeContext, FileCheckpointStore, FileHarnessAuditStore, HarnessAudit, RunStatus,
    Runtime, RunStore, Tool, ToolRegistry,
)
from agent_core.approval import FileApprovalDecisionStore, FilePendingInvocationStore
from agent_core.nodes import build_agent_from_config, tool_node
from agent_core.results import NodeStatus


class ToolNodeHarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.calls = []
        self.tools = ToolRegistry()
        self.tools.register(Tool("echo", lambda args: self.calls.append(args) or args))

    def build(self, params=None, *, second=False, limit=None):
        runtime = Runtime(store=RunStore(self.path / "runs"),
                          checkpoint_store=FileCheckpointStore(self.path / "checkpoints"))
        audit = HarnessAudit(FileHarnessAuditStore(self.path / "audit"))
        pending = FilePendingInvocationStore(self.path / "pending")
        decisions = FileApprovalDecisionStore(self.path / "decisions")
        config = {"name": "explicit-tools", "entry": "first", "nodes": [
            {"id": "first", "type": "tool", "params": params or {"name": "echo"}},
        ]}
        if second:
            config["nodes"].append({"id": "second", "type": "tool", "params": {"name": "echo"}})
            config["edges"] = [{"source": "first", "target": "second"}]
        if limit is not None:
            config["harness"] = {"max_model_calls": 1, "max_tool_calls": limit}
        agent = build_agent_from_config(config, runtime=runtime, tools=self.tools, audit=audit,
                                        pending_invocations=pending, approval_decisions=decisions)
        return agent, runtime, audit, pending, decisions

    def test_tool_results_are_audited_without_private_values(self):
        agent, _, audit, _, _ = self.build({"name": "echo", "arguments": {"secret": "private-value"}})
        result = agent.run({})
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.state["echo"], {"secret": "private-value"})
        events = audit.events(result.run_id)
        self.assertEqual([e.event_type for e in events], ["tool.requested", "tool.completed"])
        self.assertEqual(events[0].call_id, events[1].call_id)
        self.assertEqual(events[0].node_id, "first")
        self.assertNotIn("private-value", str(events))

    def test_run_budget_covers_multiple_tool_nodes_and_restart(self):
        agent, _, audit, _, _ = self.build(second=True, limit=1)
        result = agent.run({})
        self.assertEqual((result.status, result.reason_code), (RunStatus.FAILED, "MAX_TOOL_CALLS"))
        recovered, runtime, _, _, _ = self.build(second=True, limit=1)
        checkpoint = FileCheckpointStore(self.path / "checkpoints").load(result.run_id)
        resumed = runtime.resume(recovered.graph, checkpoint)
        self.assertEqual(resumed.reason_code, "MAX_TOOL_CALLS")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(sum(e.event_type == "run_budget.claimed" for e in audit.events(result.run_id)), 1)

    def test_audit_failure_prevents_execution(self):
        agent, _, _, _, _ = self.build()
        with patch("agent_core.harness.FileHarnessAuditStore.append", side_effect=OSError("disk full")):
            result = agent.run({})
        self.assertEqual(result.reason_code, "AUDIT_WRITE_FAILED")
        self.assertEqual(self.calls, [])

    def test_capability_denial_is_audited(self):
        self.tools.register(Tool("private", lambda args: self.fail("must not execute"),
                                 required_capabilities=frozenset({"write"})))
        agent, _, audit, _, _ = self.build({"name": "private"})
        result = agent.run({})
        self.assertEqual(result.reason_code, "CAPABILITY_DENIED")
        self.assertEqual(audit.events(result.run_id)[-1].event_type, "tool.blocked")

    def test_opt_in_retry_consumes_attempt_budget(self):
        class Timeout(Exception):
            reason_code = "TOOL_TIMEOUT"

        def read(args):
            self.calls.append(args)
            if len(self.calls) == 1:
                raise Timeout()
            return {"ok": True}

        self.tools.register(Tool("read", read, read_only=True))
        agent, _, audit, _, _ = self.build({"name": "read", "max_tool_calls": 2,
                                          "tool_max_retries": 1}, limit=2)
        result = agent.run({})
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(sum(e.event_type == "run_budget.claimed" for e in audit.events(result.run_id)), 2)

    def test_approval_survives_restart_and_continues_to_next_tool(self):
        self.tools.register(Tool("write", lambda args: self.calls.append(args) or {"saved": True},
                                 side_effect=True, requires_approval=True))
        params = {"name": "write", "arguments": {"secret": "private-value"}}
        agent, _, _, pending, decisions = self.build(params, second=True, limit=2)
        waiting = agent.run({})
        self.assertEqual(waiting.status, RunStatus.WAITING_APPROVAL)
        self.assertEqual(self.calls, [])
        self.assertNotIn("private-value", str(waiting.run.pending_approval))
        decisions.decide(waiting.run, waiting.run.pending_approval["request_id"], "APPROVED")
        recovered, runtime, audit, _, recovered_decisions = self.build(params, second=True, limit=2)
        checkpoint = FileCheckpointStore(self.path / "checkpoints").load(waiting.run_id)
        result = runtime.resume_approved(recovered.graph, checkpoint, decisions=recovered_decisions)
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.state["write"], {"saved": True})
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(pending.load(waiting.run_id).execution_state, "COMPLETED")
        self.assertEqual(sum(e.event_type == "run_budget.claimed" for e in audit.events(waiting.run_id)), 2)

    def test_unknown_approved_outcome_is_not_repeated(self):
        self.tools.register(Tool("write", lambda args: self.fail("must not repeat"),
                                 side_effect=True, requires_approval=True))
        agent, _, _, pending, decisions = self.build({"name": "write"})
        waiting = agent.run({})
        decision = decisions.decide(waiting.run, waiting.run.pending_approval["request_id"], "APPROVED")
        pending.claim_execution(decision)
        recovered, runtime, _, _, _ = self.build({"name": "write"})
        checkpoint = FileCheckpointStore(self.path / "checkpoints").load(waiting.run_id)
        result = runtime.resume_approved(recovered.graph, checkpoint, decisions=decisions)
        self.assertEqual((result.status, result.reason_code), (RunStatus.BLOCKED, "TOOL_OUTCOME_UNKNOWN"))

    def test_approved_arguments_cannot_be_changed_on_restart(self):
        self.tools.register(Tool("write", lambda args: self.fail("changed call must not execute"),
                                 side_effect=True, requires_approval=True))
        agent, _, _, _, decisions = self.build({"name": "write", "arguments": {"value": "old"}})
        waiting = agent.run({})
        decisions.decide(waiting.run, waiting.run.pending_approval["request_id"], "APPROVED")
        recovered, runtime, _, _, _ = self.build({"name": "write", "arguments": {"value": "new"}})
        checkpoint = FileCheckpointStore(self.path / "checkpoints").load(waiting.run_id)
        result = runtime.resume_approved(recovered.graph, checkpoint, decisions=decisions)
        self.assertEqual(result.reason_code, "APPROVAL_REQUEST_MISMATCH")

    def test_checkpoint_replay_reuses_result_and_later_visit_needs_new_approval(self):
        self.tools.register(Tool("write", lambda args: self.calls.append(args) or {"saved": True},
                                 side_effect=True, requires_approval=True))
        agent, runtime, audit, pending, decisions = self.build({"name": "write"})
        waiting = agent.run({})
        decisions.decide(waiting.run, waiting.run.pending_approval["request_id"], "APPROVED")
        checkpoint = FileCheckpointStore(self.path / "checkpoints").load(waiting.run_id)
        runtime.resume_approved(agent.graph, checkpoint, decisions=decisions)
        context = AgentNodeContext(
            run_id=waiting.run_id, node_id="first", execution_id="replay",
            state_version=checkpoint.state_version, input={}, state={}, tools=self.tools,
            audit=audit, pending_invocations=pending, approval_decisions=decisions,
        )
        node = tool_node({"name": "write"})
        replayed = node(context)
        self.assertEqual(replayed.status, NodeStatus.SUCCEEDED)
        self.assertEqual(replayed.updates, {"write": {"saved": True}})
        next_visit = node(replace(context, state_version=context.state_version + 1))
        self.assertEqual(next_visit.status, NodeStatus.WAITING_APPROVAL)
        self.assertNotEqual(next_visit.output["request_id"], waiting.run.pending_approval["request_id"])
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
