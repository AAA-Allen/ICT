"""First M4 contract: a durable wait cannot be resumed without a decision."""

import tempfile
import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agent_core import (
    ApprovalConflictError, FileApprovalDecisionStore, FileCheckpointStore,
    FilePendingInvocationStore,
    Graph, HarnessAudit, InMemoryHarnessAuditStore,
    LifecycleStatus, NodeResult, NodeStatus, RunConflictError, RunResult,
    RunStatus, RunStore,
    Runtime, Tool, ToolRegistry,
)
from agent_core.approval import ApprovalDecision, PendingInvocation, argument_digest
from agent_core.harness import (DefaultToolPolicy, HarnessBudget, HarnessLimits,
                                DefaultModelToolLoop, ToolInvocationPipeline)
from agent_core.lifecycle import RunLifecycle
from agent_core.nodes import build_agent_from_config
from agent_core.tools import ToolResult, ToolStatus


class ApprovalWaitTests(unittest.TestCase):
    def test_pending_snapshot_model_call_count_validates_and_preserves_legacy_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pending = FilePendingInvocationStore(directory)
            arguments = {"record": "private"}
            snapshot = PendingInvocation(
                "legacy-budget", "loop", "request-1", "write_record",
                argument_digest(arguments), [],
                [{"id": "call-1", "name": "write_record", "arguments": arguments}],
                [], "answer", None,
            )
            pending.save(snapshot)
            self.assertIsNone(pending.load("legacy-budget").model_calls)
            self.assertIsNone(pending.load("legacy-budget").tool_calls)
            for count in (-1, True, 1.5):
                with self.assertRaises(ValueError):
                    replace(snapshot, model_calls=count)
                with self.assertRaises(ValueError):
                    replace(snapshot, tool_calls=count)
            budget = HarnessBudget(HarnessLimits(3, 3))
            pipeline = ToolInvocationPipeline(
                ToolRegistry(), granted_capabilities=frozenset(), budget=budget,
            )
            self.assertIsNone(pipeline.restore_budget(replace(snapshot, model_calls=2,
                                                                tool_calls=2)))
            self.assertEqual((budget.model_calls, budget.tool_calls), (2, 2))

    def test_default_loop_resumes_saved_calls_without_repeating_model_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending_store = FilePendingInvocationStore(root / "private_approvals")
            decisions = FileApprovalDecisionStore(root)
            calls: list[str] = []
            registry = ToolRegistry()
            registry.register(Tool("write_record", lambda args: calls.append("write") or
                                   {"saved": True}, side_effect=True, requires_approval=True))
            registry.register(Tool("read_record", lambda args: calls.append("read") or
                                   {"value": "ok"}, read_only=True))

            def pipeline(tool_limit: int = 2, node_id: str = "loop") -> ToolInvocationPipeline:
                return ToolInvocationPipeline(
                    registry, granted_capabilities=frozenset(),
                    budget=HarnessBudget(HarnessLimits(2, tool_limit)),
                    run_id="resume-loop", node_id=node_id,
                    policy=DefaultToolPolicy(frozenset({"write_record", "read_record"})),
                    pending_invocations=pending_store,
                )

            loop = DefaultModelToolLoop()
            first_model_calls = []

            def first_model(messages, schemas):
                first_model_calls.append(list(messages))
                return {"content": None, "tool_calls": [
                    {"id": "call-1", "name": "write_record", "arguments": {"record": "private"}},
                    {"id": "call-2", "name": "read_record", "arguments": {}},
                ]}

            waiting = loop.run(first_model, [{"role": "user", "content": "save"}], [],
                               pipeline(), into="answer", route=None)
            self.assertEqual(waiting.status, NodeStatus.WAITING_APPROVAL)
            self.assertEqual(len(first_model_calls), 1)
            self.assertEqual(calls, [])
            pending = FilePendingInvocationStore(pending_store.directory).load("resume-loop")
            decisions.decide(RunResult(
                "resume-loop", RunStatus.WAITING_APPROVAL, {}, (),
                pending_approval=waiting.output), pending.request_id, "APPROVED")
            final_messages: list[list[dict]] = []

            def final_model(messages, schemas):
                final_messages.append(list(messages))
                return {"content": "done"}

            resumed = loop.resume_pending(final_model, pending, [], pipeline(), decisions)
            self.assertEqual(resumed.status, NodeStatus.SUCCEEDED)
            self.assertEqual(resumed.updates["answer"], "done")
            self.assertEqual([item["tool"] for item in resumed.updates["answer_rounds"]],
                             ["write_record", "read_record"])
            self.assertEqual(calls, ["write", "read"])
            self.assertEqual(len(final_messages), 1)
            self.assertEqual([message["tool_call_id"] for message in final_messages[0]
                              if message["role"] == "tool"], ["call-1", "call-2"])

            completed = pending_store.load("resume-loop")
            again = loop.resume_pending(final_model, completed, [], pipeline(), decisions)
            self.assertEqual(again.status, NodeStatus.SUCCEEDED)
            self.assertEqual(calls.count("write"), 1)
            mismatch = loop.resume_pending(final_model, completed, [],
                                           pipeline(node_id="other"), decisions)
            self.assertEqual((mismatch.status, mismatch.reason_code),
                             (NodeStatus.BLOCKED, "APPROVAL_REQUEST_MISMATCH"))
            unapproved = loop.resume_pending(final_model, completed, [], pipeline(),
                                             FileApprovalDecisionStore(root / "other"))
            self.assertEqual((unapproved.status, unapproved.reason_code),
                             (NodeStatus.BLOCKED, "APPROVAL_NOT_GRANTED"))
            limited = loop.resume_pending(final_model, completed, [], pipeline(1), decisions)
            self.assertEqual((limited.status, limited.reason_code),
                             (NodeStatus.FAILED, "MAX_TOOL_CALLS"))
            self.assertEqual(calls.count("write"), 1)
            unknown = loop.resume_pending(final_model, replace(pending, execution_state="STARTED"),
                                          [], pipeline(), decisions)
            self.assertEqual((unknown.status, unknown.reason_code),
                             (NodeStatus.BLOCKED, "TOOL_OUTCOME_UNKNOWN"))
            self.assertEqual(calls.count("write"), 1)

    def test_registry_executes_approved_tool_once_with_persisted_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending = FilePendingInvocationStore(root / "private_approvals")
            decisions = FileApprovalDecisionStore(root)
            arguments = {"record": "private"}
            digest = argument_digest(arguments)
            pending.save(PendingInvocation(
                "approved-run", "loop", "request-1", "write_record", digest,
                [], [{"id": "call-1", "name": "write_record", "arguments": arguments}],
                [], "answer", None,
            ))
            waiting = RunResult("approved-run", RunStatus.WAITING_APPROVAL, {}, (),
                                pending_approval={"request_id": "request-1",
                                                  "tool_name": "write_record",
                                                  "argument_digest": digest})
            called: list[dict] = []
            registry = ToolRegistry()
            registry.register(Tool("write_record", lambda args: called.append(dict(args)) or
                                   {"saved": True}, required_capabilities=frozenset({"write"}),
                                   side_effect=True, requires_approval=True))
            self.assertEqual(registry.invoke("write_record", arguments,
                                            granted_capabilities=frozenset({"write"})).reason_code,
                             "TOOL_APPROVAL_REQUIRED")
            with self.assertRaises(ApprovalConflictError) as raised:
                registry.invoke_approved("write_record", arguments, run_id="approved-run",
                                         decisions=decisions, pending_invocations=pending,
                                         granted_capabilities=frozenset({"write"}))
            self.assertEqual(raised.exception.reason_code, "APPROVAL_NOT_GRANTED")
            decisions.decide(waiting, "request-1", "APPROVED")
            denied = registry.invoke_approved("write_record", arguments, run_id="approved-run",
                                              decisions=decisions, pending_invocations=pending)
            self.assertEqual(denied.reason_code, "CAPABILITY_DENIED")
            with self.assertRaises(ApprovalConflictError) as raised:
                registry.invoke_approved("write_record", {"record": "changed"},
                                         run_id="approved-run", decisions=decisions,
                                         pending_invocations=pending,
                                         granted_capabilities=frozenset({"write"}))
            self.assertEqual(raised.exception.reason_code, "APPROVAL_REQUEST_MISMATCH")
            self.assertEqual(called, [])
            self.assertEqual(pending.load("approved-run").execution_state, "PENDING")

            result = registry.invoke_approved("write_record", arguments, run_id="approved-run",
                                              decisions=decisions, pending_invocations=pending,
                                              granted_capabilities=frozenset({"write"}))
            self.assertEqual(result.output, {"saved": True})
            self.assertEqual(called, [arguments])
            self.assertEqual(FilePendingInvocationStore(pending.directory).load(
                "approved-run").tool_result["output"], {"saved": True})
            with self.assertRaises(ApprovalConflictError) as raised:
                registry.invoke_approved("write_record", arguments, run_id="approved-run",
                                         decisions=decisions, pending_invocations=pending,
                                         granted_capabilities=frozenset({"write"}))
            self.assertEqual(raised.exception.reason_code, "TOOL_ALREADY_EXECUTED")
            self.assertEqual(len(called), 1)

    def test_registry_claim_failure_prevents_approved_tool_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending = FilePendingInvocationStore(root / "private_approvals")
            decisions = FileApprovalDecisionStore(root)
            arguments = {"record": "private"}
            digest = argument_digest(arguments)
            pending.save(PendingInvocation(
                "failed-claim", "loop", "request-1", "write_record", digest,
                [], [{"id": "call-1", "name": "write_record", "arguments": arguments}],
                [], "answer", None,
            ))
            decisions.decide(RunResult(
                "failed-claim", RunStatus.WAITING_APPROVAL, {}, (),
                pending_approval={"request_id": "request-1", "tool_name": "write_record",
                                  "argument_digest": digest}), "request-1", "APPROVED")
            called: list[dict] = []
            registry = ToolRegistry()
            registry.register(Tool("write_record", lambda args: called.append(dict(args)) or {},
                                   side_effect=True, requires_approval=True))
            with patch.object(Path, "replace", side_effect=OSError("disk unavailable")):
                with self.assertRaises(OSError):
                    registry.invoke_approved("write_record", arguments, run_id="failed-claim",
                                             decisions=decisions, pending_invocations=pending)
            self.assertEqual(called, [])
            self.assertEqual(pending.load("failed-claim").execution_state, "PENDING")

    def test_registry_result_write_failure_leaves_outcome_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending = FilePendingInvocationStore(root / "private_approvals")
            decisions = FileApprovalDecisionStore(root)
            arguments = {"record": "private"}
            digest = argument_digest(arguments)
            pending.save(PendingInvocation(
                "unknown-outcome", "loop", "request-1", "write_record", digest,
                [], [{"id": "call-1", "name": "write_record", "arguments": arguments}],
                [], "answer", None,
            ))
            decisions.decide(RunResult(
                "unknown-outcome", RunStatus.WAITING_APPROVAL, {}, (),
                pending_approval={"request_id": "request-1", "tool_name": "write_record",
                                  "argument_digest": digest}), "request-1", "APPROVED")
            called: list[dict] = []
            registry = ToolRegistry()
            registry.register(Tool("write_record", lambda args: called.append(dict(args)) or {},
                                   side_effect=True, requires_approval=True))
            with patch.object(pending, "record_result", side_effect=OSError("disk unavailable")):
                with self.assertRaises(OSError):
                    registry.invoke_approved("write_record", arguments, run_id="unknown-outcome",
                                             decisions=decisions, pending_invocations=pending)
            self.assertEqual(called, [arguments])
            self.assertEqual(FilePendingInvocationStore(pending.directory).load(
                "unknown-outcome").execution_state, "STARTED")
            with self.assertRaises(ApprovalConflictError) as raised:
                registry.invoke_approved("write_record", arguments, run_id="unknown-outcome",
                                         decisions=decisions, pending_invocations=pending)
            self.assertEqual(raised.exception.reason_code, "TOOL_OUTCOME_UNKNOWN")
            self.assertEqual(len(called), 1)

    def test_approved_pipeline_retains_policy_and_audits_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending = FilePendingInvocationStore(root / "private_approvals")
            decisions = FileApprovalDecisionStore(root)
            arguments = {"record": "private"}
            digest = argument_digest(arguments)
            pending.save(PendingInvocation(
                "pipeline-run", "loop", "request-1", "write_record", digest,
                [], [{"id": "call-1", "name": "write_record", "arguments": arguments}],
                [], "answer", None,
            ))
            decisions.decide(RunResult(
                "pipeline-run", RunStatus.WAITING_APPROVAL, {}, (),
                pending_approval={"request_id": "request-1", "tool_name": "write_record",
                                  "argument_digest": digest}), "request-1", "APPROVED")
            called: list[dict] = []
            registry = ToolRegistry()
            registry.register(Tool("write_record", lambda args: called.append(dict(args)) or
                                   {"saved": True}, side_effect=True, requires_approval=True))
            audit = HarnessAudit(InMemoryHarnessAuditStore())
            pipeline = ToolInvocationPipeline(
                registry, granted_capabilities=frozenset(),
                budget=HarnessBudget(HarnessLimits(2, 2)), audit=audit,
                run_id="pipeline-run", node_id="loop",
                policy=DefaultToolPolicy(frozenset()), pending_invocations=pending,
            )
            denied = pipeline.invoke("write_record", arguments, approval_decisions=decisions)
            self.assertEqual(denied.reason_code, "TOOL_NOT_EXPOSED")
            self.assertEqual(called, [])
            pipeline = ToolInvocationPipeline(
                registry, granted_capabilities=frozenset(),
                budget=HarnessBudget(HarnessLimits(2, 3)), audit=audit,
                run_id="pipeline-run", node_id="loop",
                policy=DefaultToolPolicy(frozenset({"write_record"})),
                pending_invocations=pending,
            )
            with patch.object(Path, "replace", side_effect=OSError("disk unavailable")):
                failed_claim = pipeline.invoke("write_record", arguments,
                                               approval_decisions=decisions)
            self.assertEqual(failed_claim.reason_code, "PENDING_INVOCATION_SAVE_FAILED")
            self.assertEqual(called, [])
            result = pipeline.invoke("write_record", arguments, approval_decisions=decisions)
            self.assertEqual(result.status, ToolStatus.COMPLETED)
            self.assertEqual(called, [arguments])
            self.assertEqual([event.event_type for event in audit.events("pipeline-run")], [
                "tool.requested", "tool.blocked", "tool.requested", "tool.failed",
                "tool.requested", "tool.completed",
            ])
            repeated = pipeline.invoke("write_record", arguments, approval_decisions=decisions)
            self.assertEqual((repeated.status, repeated.reason_code),
                             (ToolStatus.BLOCKED, "TOOL_ALREADY_EXECUTED"))
            self.assertEqual(len(called), 1)

    def test_pending_execution_claim_survives_restart_and_never_repeats(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FilePendingInvocationStore(directory)
            arguments = {"record": "private"}
            digest = argument_digest(arguments)
            store.save(PendingInvocation(
                "claim-run", "loop", "request-1", "write_record", digest,
                [], [{"id": "call-1", "name": "write_record", "arguments": arguments}],
                [], "answer", None,
            ))
            approved = ApprovalDecision(
                "claim-run", "request-1", "write_record", digest, "APPROVED",
                datetime.now(timezone.utc).isoformat(),
            )
            rejected = ApprovalDecision(
                "claim-run", "request-1", "write_record", digest, "REJECTED",
                approved.decided_at,
            )
            mismatched = ApprovalDecision(
                "claim-run", "other-request", "write_record", digest, "APPROVED",
                approved.decided_at,
            )
            for decision, code in ((rejected, "APPROVAL_NOT_GRANTED"),
                                   (mismatched, "PENDING_INVOCATION_NOT_FOUND")):
                with self.assertRaises(ApprovalConflictError) as raised:
                    store.claim_execution(decision)
                self.assertEqual(raised.exception.reason_code, code)
            self.assertEqual(store.load("claim-run").execution_state, "PENDING")

            started = store.claim_execution(approved)
            self.assertEqual(started.execution_state, "STARTED")
            restarted = FilePendingInvocationStore(directory)
            self.assertEqual(restarted.load("claim-run").execution_state, "STARTED")
            with self.assertRaises(ApprovalConflictError) as raised:
                restarted.claim_execution(approved)
            self.assertEqual(raised.exception.reason_code, "TOOL_OUTCOME_UNKNOWN")

            result = ToolResult("write_record", ToolStatus.COMPLETED, {"saved": True})
            completed = restarted.record_result(approved, result)
            self.assertEqual(completed.tool_result["output"], {"saved": True})
            self.assertEqual(store.load("claim-run").execution_state, "COMPLETED")
            with self.assertRaises(ApprovalConflictError) as raised:
                store.claim_execution(approved)
            self.assertEqual(raised.exception.reason_code, "TOOL_ALREADY_EXECUTED")

    def test_pending_execution_rejects_invalid_result_and_failed_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FilePendingInvocationStore(directory)
            arguments = {"record": "private"}
            digest = argument_digest(arguments)
            store.save(PendingInvocation(
                "write-run", "loop", "request-1", "write_record", digest,
                [], [{"id": "call-1", "name": "write_record", "arguments": arguments}],
                [], "answer", None,
            ))
            approved = ApprovalDecision(
                "write-run", "request-1", "write_record", digest, "APPROVED",
                datetime.now(timezone.utc).isoformat(),
            )
            with patch.object(Path, "replace", side_effect=OSError("disk unavailable")):
                with self.assertRaises(OSError):
                    store.claim_execution(approved)
            self.assertEqual(FilePendingInvocationStore(directory).load("write-run").execution_state,
                             "PENDING")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

            with self.assertRaises(ApprovalConflictError) as raised:
                store.record_result(approved, ToolResult("write_record", ToolStatus.COMPLETED))
            self.assertEqual(raised.exception.reason_code, "TOOL_EXECUTION_NOT_STARTED")
            store.claim_execution(approved)
            with self.assertRaises(ValueError):
                store.record_result(approved, ToolResult(
                    "write_record", ToolStatus.BLOCKED, reason_code="DENIED"))
            with self.assertRaises(ApprovalConflictError) as raised:
                store.record_result(approved, ToolResult("another_tool", ToolStatus.COMPLETED))
            self.assertEqual(raised.exception.reason_code, "TOOL_EXECUTION_NOT_STARTED")
            failed = store.record_result(approved, ToolResult(
                "write_record", ToolStatus.FAILED, reason_code="TOOL_EXCEPTION",
                error_type="RuntimeError"))
            self.assertEqual(failed.tool_result["reason_code"], "TOOL_EXCEPTION")
            with self.assertRaises(ApprovalConflictError) as raised:
                store.record_result(approved, ToolResult("write_record", ToolStatus.COMPLETED))
            self.assertEqual(raised.exception.reason_code, "TOOL_ALREADY_EXECUTED")

    def test_pending_invocation_survives_restart_without_public_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pending = FilePendingInvocationStore(Path(directory) / "private_approvals")
            called: list[dict] = []

            class Model:
                def complete_with_tools(self, messages, schemas):
                    return {"tool_calls": [{"id": "model-call", "name": "write_record",
                                            "arguments": {"record": "private"}}]}

            registry = ToolRegistry()
            registry.register(Tool("write_record", lambda args: called.append(dict(args)) or {},
                                   side_effect=True, requires_approval=True))
            config = {"name": "writer", "entry": "loop", "nodes": [{
                "id": "loop", "type": "agent_loop",
                "params": {"prompt": "write", "tools": [{"name": "write_record"}]},
            }]}
            agent = build_agent_from_config(config, runtime=Runtime(), tools=registry,
                                            model=Model(), pending_invocations=pending)
            result = agent.run({}, run_id="pending-run")
            self.assertEqual(result.status, RunStatus.WAITING_APPROVAL, result.reason_code)
            self.assertEqual(called, [])
            restored = FilePendingInvocationStore(pending.directory).load("pending-run")
            self.assertEqual(restored.request_id, result.run.pending_approval["request_id"])
            self.assertEqual(restored.remaining_calls[0]["arguments"], {"record": "private"})
            self.assertNotIn("private", str(result.run.pending_approval))
            path = pending.directory / "pending-run.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["invocations"][0]["remaining_calls"][0]["arguments"]["record"] = "tampered"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid pending invocation record"):
                FilePendingInvocationStore(pending.directory).load("pending-run")

    def test_pending_snapshot_failure_prevents_unresumable_wait(self) -> None:
        class BrokenPending:
            def for_node_visit(self, run_id, node_id, state_version):
                return None

            def save(self, invocation):
                raise OSError("disk unavailable")

        class Model:
            def complete_with_tools(self, messages, schemas):
                return {"tool_calls": [{"id": "model-call", "name": "write_record",
                                        "arguments": {"record": "private"}}]}

        registry = ToolRegistry()
        registry.register(Tool("write_record", lambda args: self.fail("tool executed"),
                               side_effect=True, requires_approval=True))
        config = {"name": "writer", "entry": "loop", "nodes": [{
            "id": "loop", "type": "agent_loop",
            "params": {"prompt": "write", "tools": [{"name": "write_record"}]},
        }]}
        agent = build_agent_from_config(config, runtime=Runtime(), tools=registry,
                                        model=Model(), pending_invocations=BrokenPending())
        result = agent.run({}, run_id="snapshot-failed")
        self.assertEqual((result.status, result.reason_code),
                         (RunStatus.FAILED, "PENDING_INVOCATION_SAVE_FAILED"))

    def test_decision_is_durable_idempotent_and_bound_to_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RunStore(root)
            result = RunResult(
                "decision-run", RunStatus.WAITING_APPROVAL, {}, (),
                pending_approval={"request_id": "request-1", "tool_name": "write_record",
                                  "argument_digest": "sha256:abc"},
            )
            store.save(result)
            decisions = FileApprovalDecisionStore(root)
            approved = decisions.decide(result, "request-1", "APPROVED")
            self.assertEqual(decisions.decide(result, "request-1", "APPROVED"), approved)
            self.assertEqual(FileApprovalDecisionStore(root).load("decision-run"), approved)
            self.assertEqual(store.list_runs(), ["decision-run"])
            for request_id, choice, code in (
                ("wrong", "APPROVED", "APPROVAL_REQUEST_MISMATCH"),
                ("request-1", "REJECTED", "APPROVAL_DECISION_CONFLICT"),
            ):
                with self.assertRaises(ApprovalConflictError) as raised:
                    decisions.decide(result, request_id, choice)
                self.assertEqual(raised.exception.reason_code, code)

            different = RunResult(
                "decision-run", RunStatus.WAITING_APPROVAL, {}, (),
                pending_approval={"request_id": "request-1", "tool_name": "write_record",
                                  "argument_digest": "sha256:different"},
            )
            with self.assertRaises(ApprovalConflictError) as raised:
                decisions.decide(different, "request-1", "APPROVED")
            self.assertEqual(raised.exception.reason_code, "APPROVAL_REQUEST_MISMATCH")

    def test_decision_rejects_nonwaiting_run_and_corrupt_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            decisions = FileApprovalDecisionStore(directory)
            with self.assertRaises(ApprovalConflictError) as raised:
                decisions.decide(RunResult("completed", RunStatus.COMPLETED, {}, ()),
                                 "request-1", "APPROVED")
            self.assertEqual(raised.exception.reason_code, "RUN_NOT_WAITING_APPROVAL")
            (Path(directory) / "broken.approval.json").write_text("{bad", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid approval decision record"):
                decisions.load("broken")

    def test_waiting_result_rejects_missing_request_and_strips_extra_fields(self) -> None:
        with self.assertRaises(ValueError):
            NodeResult(NodeStatus.WAITING_APPROVAL, reason_code="APPROVAL_REQUIRED",
                       output={"request_id": "request-1"})
        request = NodeResult(NodeStatus.WAITING_APPROVAL,
                             reason_code="APPROVAL_REQUIRED", output={
                                 "request_id": "request-1", "tool_name": "write_record",
                                 "argument_digest": "sha256:abc", "arguments": {"secret": "omit"},
                             })
        self.assertNotIn("arguments", request.output)
        with self.assertRaises(ValueError):
            RunResult("missing-request", RunStatus.WAITING_APPROVAL, {}, ())

    def test_agent_loop_waits_before_side_effect_and_records_request(self) -> None:
        called: list[dict] = []

        class Model:
            def complete_with_tools(self, messages, schemas):
                return {"tool_calls": [{"id": "model-call", "name": "write_record",
                                        "arguments": {"record": "private"}}]}

        registry = ToolRegistry()
        registry.register(Tool("write_record", lambda args: called.append(dict(args)) or {},
                               side_effect=True, requires_approval=True))
        audit = HarnessAudit(InMemoryHarnessAuditStore())
        config = {"name": "writer", "entry": "loop", "nodes": [{
            "id": "loop", "type": "agent_loop",
            "params": {"prompt": "write", "tools": [{"name": "write_record"}]},
        }]}
        agent = build_agent_from_config(config, runtime=Runtime(), tools=registry,
                                        model=Model(), audit=audit)
        result = agent.run({}, run_id="approval-tool")
        self.assertEqual(result.status, RunStatus.WAITING_APPROVAL)
        self.assertEqual(called, [])
        self.assertEqual(result.run.pending_approval["tool_name"], "write_record")
        self.assertNotIn("private", str(result.run.pending_approval))
        self.assertEqual(audit.events("approval-tool")[-1].event_type,
                         "tool.waiting_approval")

    def test_unauthorized_tool_does_not_create_approval_request(self) -> None:
        class Model:
            def complete_with_tools(self, messages, schemas):
                return {"tool_calls": [{"id": "model-call", "name": "write_record",
                                        "arguments": {"record": "private"}}]}

        registry = ToolRegistry()
        registry.register(Tool("write_record", lambda args: self.fail("tool executed"),
                               required_capabilities=frozenset({"write"}),
                               side_effect=True, requires_approval=True))
        audit = HarnessAudit(InMemoryHarnessAuditStore())
        config = {"name": "writer", "entry": "loop", "nodes": [{
            "id": "loop", "type": "agent_loop",
            "params": {"prompt": "write", "tools": [{"name": "write_record"}]},
        }]}
        agent = build_agent_from_config(config, runtime=Runtime(), tools=registry,
                                        model=Model(), audit=audit)
        result = agent.run({}, run_id="approval-denied")
        self.assertEqual(result.status, RunStatus.BLOCKED)
        self.assertEqual(result.reason_code, "CAPABILITY_DENIED")
        self.assertIsNone(result.run.pending_approval)
        self.assertEqual(audit.events("approval-denied")[-1].event_type, "tool.blocked")

    def test_waiting_request_is_persisted_without_committing_node_updates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RunStore(root / "runs")
            checkpoints = FileCheckpointStore(root / "checkpoints")
            request = {
                "request_id": "approval-1", "tool_name": "write_record",
                "argument_digest": "sha256:abc", "arguments": {"secret": "omit"},
            }
            graph = Graph().add_node(
                "approve", lambda ctx: NodeResult.waiting_approval(request),
            ).set_entry("approve").compile()
            runtime = Runtime(store=store, checkpoint_store=checkpoints)
            result = runtime.run(graph, {"input": "original"}, run_id="waiting-run")

            self.assertEqual(result.status, RunStatus.WAITING_APPROVAL)
            self.assertEqual(result.reason_code, "APPROVAL_REQUIRED")
            self.assertEqual(result.state, {"input": "original"})
            self.assertEqual(result.pending_approval, {
                "request_id": "approval-1", "tool_name": "write_record",
                "argument_digest": "sha256:abc",
            })
            self.assertEqual(result.trace[-1].event_type, "run.waiting_approval")
            self.assertEqual(checkpoints.load("waiting-run").next_node, "approve")
            self.assertFalse(runtime.cancel("waiting-run"))

            restarted = Runtime(store=store, checkpoint_store=checkpoints)
            self.assertEqual(restarted.get_status("waiting-run"), LifecycleStatus.WAITING_APPROVAL)
            self.assertEqual(store.load("waiting-run").pending_approval, result.pending_approval)
            with self.assertRaises(RunConflictError) as raised:
                restarted.resume(graph, checkpoints.load("waiting-run"))
            self.assertEqual(raised.exception.reason_code, "APPROVAL_REQUIRED")

    def test_lifecycle_wait_is_distinct_from_terminal_failure(self) -> None:
        lifecycle = RunLifecycle("wait-lifecycle")
        lifecycle.transition(LifecycleStatus.RUNNING)
        lifecycle.transition(LifecycleStatus.WAITING_APPROVAL, reason_code="APPROVAL_REQUIRED")
        self.assertFalse(lifecycle.terminal)
        self.assertEqual(RunLifecycle.from_events(lifecycle.events).status,
                         LifecycleStatus.WAITING_APPROVAL)
        with self.assertRaises(ValueError):
            lifecycle.resume()


class ApprovalRecoveryTests(unittest.TestCase):
    def test_retry_budget_is_restored_after_approval_restart(self) -> None:
        from agent_core.models import ModelRequestError

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RunStore(root / "runs")
            checkpoints = FileCheckpointStore(root / "checkpoints")
            pending = FilePendingInvocationStore(root / "private")
            decisions = FileApprovalDecisionStore(root / "decisions")
            calls: list[dict] = []
            registry = ToolRegistry()
            registry.register(Tool("write_record", lambda args: calls.append(dict(args)) or
                                   {"saved": True}, side_effect=True, requires_approval=True))
            config = self._loop_config()
            params = config["nodes"][0]["params"]
            params.update({"max_rounds": 2, "model_max_retries": 1})

            class Model:
                def __init__(self):
                    self.calls = 0

                def complete_with_tools(self, messages, schemas):
                    self.calls += 1
                    if self.calls == 1:
                        raise ModelRequestError("MODEL_TIMEOUT", "timeout")
                    return {"tool_calls": [{"id": "call-1", "name": "write_record",
                                            "arguments": {"record": "private"}}]}

            model = Model()
            runtime = Runtime(store=store, checkpoint_store=checkpoints)
            agent = build_agent_from_config(config, runtime=runtime, tools=registry,
                                            model=model, pending_invocations=pending,
                                            approval_decisions=decisions)
            waiting = agent.run({}, run_id="retry-approved")
            self.assertEqual(waiting.status, RunStatus.WAITING_APPROVAL)
            self.assertEqual(model.calls, 2)
            self.assertEqual(pending.load("retry-approved").model_calls, 2)
            request = waiting.run.pending_approval
            decisions.decide(store.load("retry-approved"), request["request_id"], "APPROVED")

            restarted = Runtime(store=store, checkpoint_store=checkpoints)
            agent = build_agent_from_config(config, runtime=restarted, tools=registry,
                                            model=model, pending_invocations=pending,
                                            approval_decisions=decisions)
            resumed = restarted.resume_approved(agent.graph, checkpoints.load("retry-approved"),
                                                decisions=decisions)
            self.assertEqual((resumed.status, resumed.reason_code),
                             (RunStatus.FAILED, "MAX_TOOL_ROUNDS"))
            self.assertEqual(model.calls, 2)
            self.assertEqual(calls, [{"record": "private"}])

    """Explicit approved recovery: waiting runs continue with one side effect."""

    @staticmethod
    def _loop_config() -> dict:
        return {"name": "writer", "entry": "loop", "nodes": [
            {"id": "loop", "type": "agent_loop",
             "params": {"prompt": "write", "tools": [{"name": "write_record"}]}},
            {"id": "ack", "type": "template",
             "params": {"updates": {"ack": "wrote {answer}"}}},
        ], "edges": [{"source": "loop", "target": "ack"}]}

    @staticmethod
    def _deciding_model():
        class Model:
            def complete_with_tools(self, messages, schemas):
                if not any(message.get("role") == "tool" for message in messages):
                    return {"tool_calls": [{"id": "call-1", "name": "write_record",
                                            "arguments": {"record": "private"}}]}
                return {"content": "done"}

        return Model()

    def _approved_waiting_run(self, root: Path, calls: list[str], run_id: str):
        store = RunStore(root / "runs")
        checkpoints = FileCheckpointStore(root / "checkpoints")
        pending = FilePendingInvocationStore(root / "private")
        decisions = FileApprovalDecisionStore(root / "decisions")
        runtime = Runtime(store=store, checkpoint_store=checkpoints)
        registry = ToolRegistry()
        registry.register(Tool("write_record",
                               lambda args: calls.append(dict(args)) or {"saved": True},
                               side_effect=True, requires_approval=True))
        agent = build_agent_from_config(
            self._loop_config(), runtime=runtime, tools=registry,
            model=self._deciding_model(), pending_invocations=pending,
            approval_decisions=decisions,
        )
        result = agent.run({}, run_id=run_id)
        self.assertEqual(result.status, RunStatus.WAITING_APPROVAL, result.reason_code)
        self.assertEqual(calls, [])
        request = result.run.pending_approval
        stored = store.load(run_id)
        self.assertEqual(stored.pending_approval, request)
        decision = decisions.decide(stored, request["request_id"], "APPROVED")
        return store, checkpoints, pending, decisions, request, decision

    def test_approved_run_resumes_end_to_end_with_one_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls: list[str] = []
            store, checkpoints, pending, decisions, request, _decision = (
                self._approved_waiting_run(root, calls, "e2e-approved"))
            runtime = Runtime(store=store, checkpoint_store=checkpoints)
            agent = build_agent_from_config(
                self._loop_config(), runtime=runtime, tools=self._registry(calls),
                model=self._deciding_model(), pending_invocations=pending,
                approval_decisions=decisions,
            )
            resumed = runtime.resume_approved(
                agent.graph, checkpoints.load("e2e-approved"), decisions=decisions,
            )
            self.assertEqual(resumed.status, RunStatus.COMPLETED, resumed.reason_code)
            self.assertEqual(resumed.state["answer"], "done")
            self.assertEqual(resumed.state["ack"], "wrote done")
            self.assertEqual(calls, [{"record": "private"}])
            self.assertEqual(pending.load("e2e-approved").execution_state, "COMPLETED")
            resumed_types = [event.event_type for event in resumed.lifecycle_events]
            self.assertIn("run.resumed", resumed_types)
            resumed_event = resumed.lifecycle_events[resumed_types.index("run.resumed")]
            self.assertEqual(resumed_event.reason_code, "APPROVED")
            self.assertEqual(resumed_event.from_status, LifecycleStatus.WAITING_APPROVAL)
            # The completed run replaces the waiting record and stays resumable-free.
            self.assertEqual(store.load("e2e-approved").status, RunStatus.COMPLETED)

    def _registry(self, calls: list[str]) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(Tool("write_record",
                               lambda args: calls.append(dict(args)) or {"saved": True},
                               side_effect=True, requires_approval=True))
        return registry

    def test_approved_run_recovers_after_restart_without_repeating_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls: list[str] = []
            store, checkpoints, pending, decisions, request, _decision = (
                self._approved_waiting_run(root, calls, "restart-approved"))
            # A fresh process: new Runtime, stores and agent rebuilt from config.
            restarted = Runtime(store=store, checkpoint_store=checkpoints)
            agent = build_agent_from_config(
                self._loop_config(), runtime=restarted, tools=self._registry(calls),
                model=self._deciding_model(), pending_invocations=pending,
                approval_decisions=decisions,
            )
            self.assertEqual(restarted.get_status("restart-approved"),
                             LifecycleStatus.WAITING_APPROVAL)
            resumed = restarted.resume_approved(
                agent.graph, checkpoints.load("restart-approved"), decisions=decisions,
            )
            self.assertEqual(resumed.status, RunStatus.COMPLETED, resumed.reason_code)
            self.assertEqual(calls, [{"record": "private"}])

    def test_resume_approved_requires_matching_durable_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls: list[str] = []
            store, checkpoints, pending, decisions, request, _decision = (
                self._approved_waiting_run(root, calls, "guarded-run"))
            runtime = Runtime(store=store, checkpoint_store=checkpoints)
            agent = build_agent_from_config(
                self._loop_config(), runtime=runtime, tools=self._registry(calls),
                model=self._deciding_model(), pending_invocations=pending,
                approval_decisions=decisions,
            )
            checkpoint = checkpoints.load("guarded-run")
            with self.assertRaises(RunConflictError) as raised:
                Runtime(store=store, checkpoint_store=checkpoints).resume_approved(
                    agent.graph, checkpoint, decisions=FileApprovalDecisionStore(root / "other"))
            self.assertEqual(raised.exception.reason_code, "APPROVAL_REQUIRED")
            self.assertEqual(calls, [])
            # A decision bound to a different request cannot unlock the run.
            store.save(replace(store.load("guarded-run"),
                               pending_approval={"request_id": request["request_id"],
                                                 "tool_name": "other_tool",
                                                 "argument_digest":
                                                 request["argument_digest"]}))
            with self.assertRaises(RunConflictError) as raised:
                runtime.resume_approved(agent.graph, checkpoint, decisions=decisions)
            self.assertEqual(raised.exception.reason_code, "APPROVAL_REQUEST_MISMATCH")
            self.assertEqual(calls, [])
            with self.assertRaises(RunConflictError) as raised:
                runtime.resume_approved(agent.graph, checkpoints.load("guarded-run"),
                                        decisions=None)
            self.assertEqual(raised.exception.reason_code, "APPROVAL_REQUIRED")

    def test_started_outcome_stops_recovery_without_replaying_tool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls: list[str] = []
            store, checkpoints, pending, decisions, request, decision = (
                self._approved_waiting_run(root, calls, "started-run"))
            # Crash after the durable STARTED claim, before any recorded result.
            pending.claim_execution(decision)
            restarted = Runtime(store=store, checkpoint_store=checkpoints)
            agent = build_agent_from_config(
                self._loop_config(), runtime=restarted, tools=self._registry(calls),
                model=self._deciding_model(), pending_invocations=pending,
                approval_decisions=decisions,
            )
            resumed = restarted.resume_approved(
                agent.graph, checkpoints.load("started-run"), decisions=decisions,
            )
            self.assertEqual((resumed.status, resumed.reason_code),
                             (RunStatus.BLOCKED, "TOOL_OUTCOME_UNKNOWN"))
            self.assertEqual(calls, [])
            # The operator verifies the external system and records the outcome.
            resolved = pending.resolve_unknown(decision, "COMPLETED", output={"verified": True})
            self.assertEqual(resolved.tool_result["resolved_by"], "OPERATOR")
            with self.assertRaises(ApprovalConflictError) as raised:
                pending.resolve_unknown(decision, "FAILED", reason_code="LATE")
            self.assertEqual(raised.exception.reason_code, "TOOL_ALREADY_EXECUTED")
            # The verified outcome is reused; the tool never runs again.
            recovered = restarted.resume(agent.graph, checkpoints.load("started-run"))
            self.assertEqual(recovered.status, RunStatus.COMPLETED, recovered.reason_code)
            self.assertEqual(calls, [])
            rounds = recovered.state["answer_rounds"]
            self.assertEqual(rounds[0]["output"], {"verified": True})
            self.assertEqual(recovered.state["answer"], "done")

    def test_started_outcome_failed_resolution_fails_the_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls: list[str] = []
            store, checkpoints, pending, decisions, request, decision = (
                self._approved_waiting_run(root, calls, "failed-resolution"))
            # A PENDING invocation (no STARTED claim yet) cannot be reconciled.
            with self.assertRaises(ApprovalConflictError) as raised:
                pending.resolve_unknown(decision, "COMPLETED", output={})
            self.assertEqual(raised.exception.reason_code, "TOOL_EXECUTION_NOT_STARTED")
            pending.claim_execution(decision)
            restarted = Runtime(store=store, checkpoint_store=checkpoints)
            agent = build_agent_from_config(
                self._loop_config(), runtime=restarted, tools=self._registry(calls),
                model=self._deciding_model(), pending_invocations=pending,
                approval_decisions=decisions,
            )
            first = restarted.resume_approved(
                agent.graph, checkpoints.load("failed-resolution"), decisions=decisions)
            self.assertEqual((first.status, first.reason_code),
                             (RunStatus.BLOCKED, "TOOL_OUTCOME_UNKNOWN"))
            pending.resolve_unknown(decision, "FAILED", reason_code="OPERATOR_VERIFIED_FAILED")
            recovered = restarted.resume(agent.graph, checkpoints.load("failed-resolution"))
            self.assertEqual((recovered.status, recovered.reason_code),
                             (RunStatus.FAILED, "OPERATOR_VERIFIED_FAILED"))
            self.assertEqual(calls, [])

    def test_one_run_supports_sequential_approvals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_calls: list[dict] = []
            publish_calls: list[dict] = []

            class Model:
                def complete_with_tools(self, messages, schemas):
                    tool_replies = [m for m in messages if m.get("role") == "tool"]
                    if not tool_replies:
                        return {"tool_calls": [{"id": "call-1", "name": "archive_record",
                                                "arguments": {"a": 1}}]}
                    if len(tool_replies) == 1:
                        return {"tool_calls": [{"id": "call-2", "name": "publish_record",
                                                "arguments": {"b": 2}}]}
                    return {"content": "all done"}

            store = RunStore(root / "runs")
            checkpoints = FileCheckpointStore(root / "checkpoints")
            pending = FilePendingInvocationStore(root / "private")
            decisions = FileApprovalDecisionStore(root / "decisions")
            runtime = Runtime(store=store, checkpoint_store=checkpoints)
            registry = ToolRegistry()
            registry.register(Tool("archive_record",
                                   lambda args: archive_calls.append(dict(args)) or {"archived": True},
                                   side_effect=True, requires_approval=True))
            registry.register(Tool("publish_record",
                                   lambda args: publish_calls.append(dict(args)) or {"published": True},
                                   side_effect=True, requires_approval=True))
            config = {"name": "multi-wait", "entry": "loop", "nodes": [{
                "id": "loop", "type": "agent_loop",
                "params": {"prompt": "work", "tools": [{"name": "archive_record"},
                                                       {"name": "publish_record"}]},
            }]}
            agent = build_agent_from_config(config, runtime=runtime, tools=registry,
                                            model=Model(), pending_invocations=pending,
                                            approval_decisions=decisions)
            result = agent.run({}, run_id="multi-approval-run")
            self.assertEqual(result.status, RunStatus.WAITING_APPROVAL)
            first_request = result.run.pending_approval

            decisions.decide(store.load("multi-approval-run"),
                             first_request["request_id"], "APPROVED")
            second = runtime.resume_approved(agent.graph, checkpoints.load("multi-approval-run"),
                                             decisions=decisions)
            self.assertEqual(second.status, RunStatus.WAITING_APPROVAL, second.reason_code)
            self.assertEqual(archive_calls, [{"a": 1}])
            self.assertEqual(publish_calls, [])
            second_request = second.pending_approval
            self.assertNotEqual(second_request["request_id"], first_request["request_id"])

            decisions.decide(store.load("multi-approval-run"),
                             second_request["request_id"], "APPROVED")
            final = runtime.resume_approved(agent.graph, checkpoints.load("multi-approval-run"),
                                            decisions=decisions)
            self.assertEqual(final.status, RunStatus.COMPLETED, final.reason_code)
            self.assertEqual(final.state["answer"], "all done")
            self.assertEqual(archive_calls, [{"a": 1}])
            self.assertEqual(publish_calls, [{"b": 2}])
            recorded = decisions.load_decisions("multi-approval-run")
            self.assertEqual([item.request_id for item in recorded],
                             [first_request["request_id"], second_request["request_id"]])
            snapshots = pending.load_all("multi-approval-run")
            self.assertEqual([item.request_id for item in snapshots],
                             [first_request["request_id"], second_request["request_id"]])
            self.assertTrue(all(item.execution_state == "COMPLETED" for item in snapshots))
            self.assertEqual(decisions.load_for_request("multi-approval-run",
                                                        first_request["request_id"]).decision,
                             "APPROVED")
            self.assertEqual(pending.public_state("multi-approval-run")["request_id"],
                             second_request["request_id"])

    def test_v1_single_record_files_still_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            arguments = {"record": "private"}
            digest = argument_digest(arguments)
            decided_at = datetime.now(timezone.utc).isoformat()
            (root / "compat.approval.json").write_text(json.dumps({
                "run_id": "compat", "request_id": "request-1",
                "tool_name": "write_record", "argument_digest": digest,
                "decision": "APPROVED", "decided_at": decided_at,
            }, ensure_ascii=False), encoding="utf-8")
            decisions = FileApprovalDecisionStore(root)
            loaded = decisions.load("compat")
            self.assertEqual((loaded.request_id, loaded.decision), ("request-1", "APPROVED"))
            self.assertEqual(len(decisions.load_decisions("compat")), 1)
            (root / "private").mkdir()
            (root / "private" / "compat.json").write_text(json.dumps({
                "run_id": "compat", "node_id": "loop", "request_id": "request-1",
                "tool_name": "write_record", "argument_digest": digest,
                "messages": [], "remaining_calls": [
                    {"id": "call-1", "name": "write_record", "arguments": arguments}],
                "rounds": [], "into": "answer", "route": None,
            }, ensure_ascii=False), encoding="utf-8")
            pending = FilePendingInvocationStore(root / "private")
            invocation = pending.load("compat")
            self.assertEqual(invocation.request_id, "request-1")
            self.assertEqual(pending.load_for_request("compat", "request-1"), invocation)
            self.assertEqual(pending.active_for_node("compat", "loop"), invocation)


if __name__ == "__main__":
    unittest.main()
