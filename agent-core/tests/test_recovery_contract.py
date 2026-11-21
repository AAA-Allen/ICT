"""Recovery contracts shared by explicit tools and model tool loops."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agent_core import (
    AgentBuilder, FileCheckpointStore, FileHarnessAuditStore, HarnessAudit,
    HarnessAuditError, HarnessLimits, NodeResult, RunStatus, Runtime, RunStore, Tool, ToolRegistry,
)
from agent_core.approval import FileApprovalDecisionStore, FilePendingInvocationStore
from agent_core.nodes import agent_loop_node, tool_node, llm_node, subagent_node


class RecoveryContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.effects = []
        self.model_calls = []
        self.tools = ToolRegistry()
        self.tools.register(Tool("write", lambda args: self.effects.append(args) or {"saved": True},
                                 side_effect=True, requires_approval=True))

    def build(self, kind, *, cycle=False, loop_params=None):
        checkpoints = FileCheckpointStore(self.path / "checkpoints")
        runtime = Runtime(store=RunStore(self.path / "runs"), checkpoint_store=checkpoints)
        audit = HarnessAudit(FileHarnessAuditStore(self.path / "audit"))
        pending = FilePendingInvocationStore(self.path / "pending")
        decisions = FileApprovalDecisionStore(self.path / "decisions")
        model_calls = self.model_calls

        class Model:
            def complete_with_tools(self, messages, schemas):
                model_calls.append(messages)
                if messages[-1]["role"] == "tool":
                    return {"content": "done"}
                return {"tool_calls": [{"id": "write-call", "name": "write", "arguments": {}}]}

        node = (tool_node({"name": "write"}) if kind == "tool" else
                agent_loop_node({"prompt": "write", "tools": [{"name": "write"}], **(loop_params or {})}))
        builder = AgentBuilder(
            "recovery", tools=self.tools, model=Model(), audit=audit,
            run_limits=HarnessLimits(16, 4), pending_invocations=pending,
            approval_decisions=decisions,
        ).add_node("work", node).set_entry("work")
        if cycle:
            def gate(ctx):
                visits = ctx.state.get("visits", 0) + 1
                return NodeResult.success({"visits": visits}, route="again" if visits < 2 else "done")

            builder.add_node("gate", gate).add_node("end", lambda ctx: NodeResult.success())
            builder.add_edge("work", "gate")
            builder.add_edge("gate", "work", when="again")
            builder.add_edge("gate", "end", when="done")
        return builder.build(runtime=runtime), runtime, checkpoints, audit, pending, decisions

    def check_two_visits(self, kind):
        agent, _, _, _, _, decisions = self.build(kind, cycle=True)
        first = agent.run({})
        self.assertEqual(first.status, RunStatus.WAITING_APPROVAL)
        request1 = first.run.pending_approval["request_id"]
        decisions.decide(first.run, request1, "APPROVED")
        agent, runtime, checkpoints, _, pending, decisions = self.build(kind, cycle=True)
        second = runtime.resume_approved(agent.graph, checkpoints.load(first.run_id), decisions=decisions)
        self.assertEqual(second.status, RunStatus.WAITING_APPROVAL)
        self.assertEqual(len(self.effects), 1)
        request2 = second.pending_approval["request_id"]
        self.assertNotEqual(request1, request2)
        decisions.decide(second, request2, "APPROVED")
        agent, runtime, checkpoints, audit, pending, decisions = self.build(kind, cycle=True)
        final = runtime.resume_approved(agent.graph, checkpoints.load(first.run_id), decisions=decisions)
        self.assertEqual(final.status, RunStatus.COMPLETED)
        self.assertEqual(len(self.effects), 2)
        snapshots = pending.load_all(first.run_id)
        self.assertEqual([item.state_version for item in snapshots], [0, 2])
        self.assertEqual([item.execution_state for item in snapshots], ["COMPLETED", "COMPLETED"])
        claims = [e for e in audit.events(first.run_id) if e.event_type == "run_budget.claimed"]
        self.assertEqual(sum(e.summary["kind"] == "tool" for e in claims), 2)
        if kind == "loop":
            self.assertEqual(len(self.model_calls), 4)
            self.assertEqual(sum(e.summary["kind"] == "model" for e in claims), 4)

    def test_explicit_tool_cycle_requests_fresh_approval_after_restart(self):
        self.check_two_visits("tool")

    def test_model_loop_cycle_requests_fresh_approval_after_restart(self):
        self.check_two_visits("loop")

    def test_legacy_snapshot_is_readable_but_automatic_visit_is_unknown(self):
        for kind in ("tool", "loop"):
            with self.subTest(kind=kind):
                agent, _, _, _, pending, decisions = self.build(kind)
                waiting = agent.run({})
                decision = decisions.decide(waiting.run, waiting.run.pending_approval["request_id"], "APPROVED")
                # Emulate a historical record with no visit identity.
                path = self.path / "pending" / f"{waiting.run_id}.json"
                import json
                payload = json.loads(path.read_text(encoding="utf-8"))
                for entry in payload.get("invocations", [payload]):
                    entry.pop("state_version", None)
                path.write_text(json.dumps(payload), encoding="utf-8")
                self.assertIsNone(pending.load(waiting.run_id).state_version)
                agent, runtime, checkpoints, _, _, _ = self.build(kind)
                result = runtime.resume_approved(agent.graph, checkpoints.load(waiting.run_id), decisions=decisions)
                self.assertEqual(result.reason_code, "APPROVAL_VISIT_UNKNOWN")
                self.assertEqual(self.effects, [])
                self.assertEqual(decision.request_id, waiting.run.pending_approval["request_id"])

    def test_loop_changed_output_route_or_tool_exposure_blocks_recovery(self):
        for params, reason in (({"into": "other"}, "APPROVAL_REQUEST_MISMATCH"),
                               ({"route": "other"}, "APPROVAL_REQUEST_MISMATCH"),
                               ({"tools": [{"name": "other"}]}, "TOOL_NOT_EXPOSED")):
            with self.subTest(params=params):
                agent, _, _, _, _, decisions = self.build("loop")
                waiting = agent.run({})
                decisions.decide(waiting.run, waiting.run.pending_approval["request_id"], "APPROVED")
                agent, runtime, checkpoints, _, _, _ = self.build("loop", loop_params=params)
                result = runtime.resume_approved(agent.graph, checkpoints.load(waiting.run_id), decisions=decisions)
                self.assertEqual(result.reason_code, reason)
                self.assertEqual(self.effects, [])

    def test_unfinished_visit_cannot_be_resumed_at_a_different_boundary(self):
        for kind in ("tool", "loop"):
            with self.subTest(kind=kind):
                agent, _, _, _, _, decisions = self.build(kind)
                waiting = agent.run({})
                decisions.decide(waiting.run, waiting.run.pending_approval["request_id"], "APPROVED")
                agent, runtime, checkpoints, _, _, _ = self.build(kind)
                checkpoint = checkpoints.load(waiting.run_id)
                moved = replace(checkpoint, state_version=checkpoint.state_version + 1)
                result = runtime.resume_approved(agent.graph, moved, decisions=decisions)
                self.assertEqual(result.reason_code, "APPROVAL_REQUEST_MISMATCH")
                self.assertEqual(self.effects, [])


class SimulatedCrash(BaseException):
    """Bypass normal exception-to-result handling, as process loss would."""


class RecoveryFaultMatrixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.calls = []

    def build(self, kind, *, tail=False):
        checkpoints = FileCheckpointStore(self.path / "checkpoints")
        runtime = Runtime(store=RunStore(self.path / "runs"), checkpoint_store=checkpoints)
        audit = HarnessAudit(FileHarnessAuditStore(self.path / "audit"))
        pending = FilePendingInvocationStore(self.path / "pending")
        decisions = FileApprovalDecisionStore(self.path / "decisions")
        calls = self.calls

        class Model:
            def complete(self, messages):
                calls.append("model")
                return {"content": "done"}

        registry = ToolRegistry()
        registry.register(Tool("read", lambda args: calls.append("read") or {"ok": True}, read_only=True))
        registry.register(Tool("write", lambda args: calls.append("write") or {"ok": True},
                               side_effect=True, requires_approval=True))
        builder = AgentBuilder("faults", model=Model(), tools=registry, audit=audit,
                               run_limits=HarnessLimits(4, 4), pending_invocations=pending,
                               approval_decisions=decisions)
        if kind == "model":
            work = llm_node({"prompt": "work"})
        elif kind == "child":
            child_runtime = Runtime(store=RunStore(self.path / "child-runs"))
            child = (AgentBuilder("child", model=Model()).add_node("ask", llm_node({"prompt": "child"}))
                     .set_entry("ask").build(runtime=child_runtime))
            work = subagent_node({"agent": "child"}, resolver=lambda name, memory: child)
        else:
            work = tool_node({"name": "write" if kind == "approved" else "read"})
        builder.add_node("seed", lambda ctx: NodeResult.success()).add_node("work", work)
        builder.add_edge("seed", "work").set_entry("seed")
        if tail:
            builder.add_node("tail", tool_node({"name": "read"})).add_edge("work", "tail")
        agent = builder.build(runtime=runtime)
        return agent, runtime, checkpoints, audit, pending, decisions

    def resume(self, kind, run_id):
        agent, runtime, checkpoints, _, _, _ = self.build(kind)
        return runtime.resume(agent.graph, checkpoints.load(run_id))

    def test_claim_write_failure_prevents_external_calls_and_can_retry(self):
        for kind in ("model", "read"):
            with self.subTest(kind=kind):
                agent, _, _, audit, _, _ = self.build(kind)
                record = audit.record

                def fail_claim(run_id, event_type, status, **kwargs):
                    if event_type == "run_budget.claimed":
                        raise HarnessAuditError("AUDIT_WRITE_FAILED")
                    return record(run_id, event_type, status, **kwargs)

                before = len(self.calls)
                with patch.object(audit, "record", side_effect=fail_claim):
                    result = agent.run({})
                self.assertEqual(result.reason_code, "AUDIT_WRITE_FAILED")
                self.assertEqual(len(self.calls), before)
                self.assertEqual(self.resume(kind, result.run_id).status, RunStatus.COMPLETED)
                self.assertEqual(len(self.calls), before + 1)

    def test_crash_after_claim_prevents_automatic_resend(self):
        for kind in ("model", "read"):
            with self.subTest(kind=kind):
                agent, _, _, audit, _, _ = self.build(kind)
                record = audit.record

                def crash_after_claim(run_id, event_type, status, **kwargs):
                    event = record(run_id, event_type, status, **kwargs)
                    if event_type == "run_budget.claimed":
                        raise SimulatedCrash()
                    return event

                with patch.object(audit, "record", side_effect=crash_after_claim):
                    with self.assertRaises(SimulatedCrash):
                        agent.run({}, run_id=f"claimed-{kind}")
                result = self.resume(kind, f"claimed-{kind}")
                self.assertEqual(result.reason_code, "MODEL_OUTCOME_UNKNOWN" if kind == "model" else "TOOL_OUTCOME_UNKNOWN")
                self.assertEqual(self.calls, [])

    def test_lost_result_or_settlement_blocks_new_calls_after_restart(self):
        for kind in ("model", "read"):
            for failure in ("result", "settlement"):
                with self.subTest(kind=kind, failure=failure):
                    agent, _, _, audit, _, _ = self.build(kind)
                    record = audit.record
                    completed = "model.completed" if kind == "model" else "tool.completed"
                    fail_event = completed if failure == "result" else "run_budget.settled"

                    def fail_write(run_id, event_type, status, **kwargs):
                        if event_type == fail_event:
                            raise HarnessAuditError("AUDIT_WRITE_FAILED")
                        return record(run_id, event_type, status, **kwargs)

                    before = len(self.calls)
                    with patch.object(audit, "record", side_effect=fail_write):
                        result = agent.run({})
                    self.assertEqual(result.reason_code, "AUDIT_WRITE_FAILED")
                    recovered = self.resume(kind, result.run_id)
                    self.assertEqual(recovered.reason_code, "MODEL_OUTCOME_UNKNOWN" if kind == "model" else "TOOL_OUTCOME_UNKNOWN")
                    self.assertEqual(len(self.calls), before + 1)

    def test_approved_result_save_failure_does_not_repeat_side_effect(self):
        agent, runtime, checkpoints, _, pending, decisions = self.build("approved")
        waiting = agent.run({})
        decisions.decide(waiting.run, waiting.run.pending_approval["request_id"], "APPROVED")
        with patch.object(pending, "record_result", side_effect=OSError("disk full")):
            failed = runtime.resume_approved(agent.graph, checkpoints.load(waiting.run_id), decisions=decisions)
        self.assertEqual(failed.reason_code, "TOOL_OUTCOME_UNKNOWN")
        self.assertEqual(self.calls, ["write"])
        self.assertEqual(self.resume("approved", waiting.run_id).reason_code, "TOOL_OUTCOME_UNKNOWN")
        self.assertEqual(self.calls, ["write"])

    def test_approved_result_reused_after_checkpoint_write_failure(self):
        agent, runtime, checkpoints, audit, _, decisions = self.build("approved")
        waiting = agent.run({})
        decisions.decide(waiting.run, waiting.run.pending_approval["request_id"], "APPROVED")
        with patch.object(checkpoints, "save", side_effect=OSError("disk full")):
            failed = runtime.resume_approved(agent.graph, checkpoints.load(waiting.run_id), decisions=decisions)
        self.assertEqual(failed.reason_code, "CHECKPOINT_SAVE_FAILED")
        self.assertEqual(self.calls, ["write"])
        self.assertEqual(self.resume("approved", waiting.run_id).status, RunStatus.COMPLETED)
        self.assertEqual(self.calls, ["write"])
        self.assertEqual(sum(e.event_type == "run_budget.claimed" for e in audit.events(waiting.run_id)), 1)

    def test_failed_started_write_prevents_side_effect_and_allows_explicit_retry(self):
        agent, runtime, checkpoints, _, pending, decisions = self.build("approved")
        waiting = agent.run({})
        decisions.decide(waiting.run, waiting.run.pending_approval["request_id"], "APPROVED")
        with patch.object(pending, "claim_execution", side_effect=OSError("disk full")):
            failed = runtime.resume_approved(agent.graph, checkpoints.load(waiting.run_id), decisions=decisions)
        self.assertEqual(failed.reason_code, "PENDING_INVOCATION_SAVE_FAILED")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.resume("approved", waiting.run_id).status, RunStatus.COMPLETED)
        self.assertEqual(self.calls, ["write"])

    def test_approved_settlement_failure_blocks_subsequent_call_on_replay(self):
        agent, runtime, checkpoints, audit, _, decisions = self.build("approved", tail=True)
        waiting = agent.run({})
        decisions.decide(waiting.run, waiting.run.pending_approval["request_id"], "APPROVED")
        record = audit.record

        def fail_settlement(run_id, event_type, status, **kwargs):
            if event_type == "run_budget.settled":
                raise HarnessAuditError("AUDIT_WRITE_FAILED")
            return record(run_id, event_type, status, **kwargs)

        with patch.object(audit, "record", side_effect=fail_settlement):
            failed = runtime.resume_approved(agent.graph, checkpoints.load(waiting.run_id), decisions=decisions)
        self.assertEqual(failed.reason_code, "AUDIT_WRITE_FAILED")
        agent, runtime, checkpoints, _, _, _ = self.build("approved", tail=True)
        recovered = runtime.resume(agent.graph, checkpoints.load(waiting.run_id))
        self.assertEqual(recovered.reason_code, "TOOL_OUTCOME_UNKNOWN")
        self.assertEqual(self.calls, ["write"])

    def test_settled_model_and_read_replay_consumes_another_attempt(self):
        for kind in ("model", "read"):
            with self.subTest(kind=kind):
                agent, _, checkpoints, audit, _, _ = self.build(kind)
                save = checkpoints.save

                def fail_final(checkpoint):
                    if checkpoint.next_node is None:
                        raise OSError("checkpoint unavailable")
                    save(checkpoint)

                before = len(self.calls)
                with patch.object(checkpoints, "save", side_effect=fail_final):
                    failed = agent.run({})
                self.assertEqual(failed.reason_code, "CHECKPOINT_SAVE_FAILED")
                self.assertEqual(self.resume(kind, failed.run_id).status, RunStatus.COMPLETED)
                self.assertEqual(len(self.calls), before + 2)
                self.assertEqual(sum(e.event_type == "run_budget.claimed" for e in audit.events(failed.run_id)), 2)

    def test_missing_child_result_is_not_redelegated(self):
        agent, _, _, _, _, _ = self.build("child")
        save = RunStore.save

        def lose_child_result(store, result):
            if store.directory == self.path / "child-runs":
                raise OSError("child result unavailable")
            return save(store, result)

        with patch.object(RunStore, "save", new=lose_child_result):
            failed = agent.run({})
        self.assertEqual(failed.reason_code, "SUBAGENT_PERSISTENCE_FAILED")
        self.assertEqual(self.calls, ["model"])
        self.assertEqual(self.resume("child", failed.run_id).reason_code, "SUBAGENT_OUTCOME_UNKNOWN")
        self.assertEqual(self.calls, ["model"])

    def test_child_result_reused_after_parent_checkpoint_failure(self):
        agent, _, checkpoints, _, _, _ = self.build("child")
        save = checkpoints.save

        def fail_final(checkpoint):
            if checkpoint.next_node is None:
                raise OSError("checkpoint unavailable")
            save(checkpoint)

        with patch.object(checkpoints, "save", side_effect=fail_final):
            failed = agent.run({})
        self.assertEqual(failed.reason_code, "CHECKPOINT_SAVE_FAILED")
        self.assertEqual(self.resume("child", failed.run_id).status, RunStatus.COMPLETED)
        self.assertEqual(self.calls, ["model"])


if __name__ == "__main__":
    unittest.main()
