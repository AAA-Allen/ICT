"""Public managed calls: permissions, stops, attempts and durable approvals."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_core import (
    AgentBuilder, ContextLimits, FileApprovalDecisionStore, FileCheckpointStore,
    FileHarnessAuditStore, FilePendingInvocationStore, HarnessAudit, HarnessLimits,
    ManagedCallStopped, ModelRetryPolicy, NodeCallPolicy, NodeResult, RunStatus,
    RunStore, Runtime, Tool, ToolRegistry, ToolRetryPolicy,
)
from agent_core.nodes import llm_node, subagent_node, tool_node
from agent_core.streaming import default_bus


class ManagedCallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.effects = []
        self.model_requests = []
        self.tools = ToolRegistry()
        self.tools.register(Tool("echo", lambda args: self.effects.append(args) or args, read_only=True))
        self.tools.register(Tool("write", lambda args: self.effects.append(args) or {"saved": True},
                                 side_effect=True, requires_approval=True))

    def model(self):
        requests = self.model_requests

        class Model:
            def complete(self, messages):
                requests.append(messages)
                return {"content": "done", "usage": {"prompt_tokens": 3, "completion_tokens": 1}}

        return Model()

    def build(self, nodes, *, model=None, limits=None):
        checkpoints = FileCheckpointStore(self.path / "checkpoints")
        runtime = Runtime(store=RunStore(self.path / "runs"), checkpoint_store=checkpoints)
        audit = HarnessAudit(FileHarnessAuditStore(self.path / "audit"))
        pending = FilePendingInvocationStore(self.path / "pending")
        decisions = FileApprovalDecisionStore(self.path / "decisions")
        builder = AgentBuilder("managed", tools=self.tools, model=model, audit=audit,
                               run_limits=limits or HarnessLimits(8, 8),
                               pending_invocations=pending, approval_decisions=decisions)
        for index, (name, handler, policy) in enumerate(nodes):
            builder.add_node(name, handler, calls=policy)
            if index:
                builder.add_edge(nodes[index - 1][0], name)
        builder.set_entry(nodes[0][0])
        return builder.build(runtime=runtime), runtime, checkpoints, audit, pending, decisions

    def test_model_reply_preserves_real_usage_and_audits_only_summary(self):
        def custom(ctx):
            reply = ctx.calls.model([{"role": "user", "content": "private-input"}])
            return NodeResult.success({"answer": reply["content"], "usage": reply["usage"]})

        agent, _, _, audit, _, _ = self.build([("ask", custom, NodeCallPolicy())], model=self.model())
        result = agent.run({})
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.state["usage"], {"prompt_tokens": 3, "completion_tokens": 1})
        events = audit.events(result.run_id)
        self.assertEqual([e.event_type for e in events],
                         ["model.requested", "run_budget.claimed", "model.completed", "run_budget.settled"])
        self.assertEqual(events[2].summary["usage"], result.state["usage"])
        self.assertNotIn("private-input", str(events))

    def test_custom_and_builtin_tools_share_exposure_and_capability_gates(self):
        self.tools.register(Tool("private", lambda args: self.fail("must not execute"),
                                 required_capabilities=frozenset({"write"})))
        for builtin in (False, True):
            with self.subTest(builtin=builtin):
                handler = (tool_node({"name": "private"}) if builtin else
                           lambda ctx: NodeResult.success(ctx.calls.tool("private", {})))
                policy = None if builtin else NodeCallPolicy(allowed_tools=frozenset({"private"}))
                agent, _, _, audit, _, _ = self.build([("call", handler, policy)])
                result = agent.run({})
                self.assertEqual(result.reason_code, "CAPABILITY_DENIED")
                self.assertEqual(audit.events(result.run_id)[-2].event_type, "tool.blocked")
        def hidden(ctx):
            return NodeResult.success(ctx.calls.tool("echo", {}))
        agent, _, _, _, _, _ = self.build([("hidden", hidden, NodeCallPolicy())])
        self.assertEqual(agent.run({}).reason_code, "TOOL_NOT_EXPOSED")
        self.assertEqual(self.effects, [])

    def test_missing_policy_is_explicitly_blocked(self):
        agent, _, _, _, _, _ = self.build([
            ("call", lambda ctx: NodeResult.success(ctx.calls.tool("echo", {})), None),
        ])
        result = agent.run({})
        self.assertEqual((result.status, result.reason_code),
                         (RunStatus.BLOCKED, "MANAGED_CALLS_NOT_CONFIGURED"))
        self.assertEqual(self.effects, [])

    def test_second_logical_call_stops_before_second_external_operation(self):
        def twice(ctx):
            ctx.calls.tool("echo", {"first": True})
            ctx.calls.tool("echo", {"second": True})
            return NodeResult.success({"pretend": True})

        policy = NodeCallPolicy(allowed_tools=frozenset({"echo"}), limits=HarnessLimits(4, 4))
        agent, _, _, _, _, _ = self.build([("call", twice, policy)])
        result = agent.run({})
        self.assertEqual(result.reason_code, "MULTIPLE_MANAGED_CALLS")
        self.assertEqual(self.effects, [{"first": True}])
        self.assertNotIn("pretend", result.state)

    def test_approval_cannot_be_swallowed_and_recovers_after_restart(self):
        def custom(ctx):
            try:
                result = ctx.calls.tool("write", {"record": ctx.state["record"]})
            except ManagedCallStopped:
                return NodeResult.success({"pretend": True})
            return NodeResult.success({"saved": result["saved"]})

        nodes = [("write", custom, NodeCallPolicy(allowed_tools=frozenset({"write"})))]
        agent, _, _, _, _, decisions = self.build(nodes)
        waiting = agent.run({"record": "private-record"})
        self.assertEqual(waiting.status, RunStatus.WAITING_APPROVAL)
        self.assertNotIn("pretend", waiting.state)
        self.assertNotIn("private-record", str(waiting.run.pending_approval))
        self.assertEqual(self.effects, [])
        decisions.decide(waiting.run, waiting.run.pending_approval["request_id"], "APPROVED")
        agent, runtime, checkpoints, audit, _, decisions = self.build(nodes)
        result = runtime.resume_approved(agent.graph, checkpoints.load(waiting.run_id), decisions=decisions)
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertTrue(result.state["saved"])
        self.assertEqual(self.effects, [{"record": "private-record"}])
        self.assertEqual(sum(e.event_type == "run_budget.claimed" for e in audit.events(waiting.run_id)), 1)

    def test_unknown_approved_result_is_not_repeated(self):
        def custom(ctx):
            return NodeResult.success(ctx.calls.tool("write", {}))

        nodes = [("write", custom, NodeCallPolicy(allowed_tools=frozenset({"write"})))]
        agent, _, _, _, pending, decisions = self.build(nodes)
        waiting = agent.run({})
        decision = decisions.decide(waiting.run, waiting.run.pending_approval["request_id"], "APPROVED")
        pending.claim_execution(decision)
        agent, runtime, checkpoints, _, _, decisions = self.build(nodes)
        result = runtime.resume_approved(agent.graph, checkpoints.load(waiting.run_id), decisions=decisions)
        self.assertEqual(result.reason_code, "TOOL_OUTCOME_UNKNOWN")
        self.assertEqual(self.effects, [])

    def test_failure_cannot_be_swallowed_or_used_to_call_another_tool(self):
        def custom(ctx):
            try:
                ctx.calls.tool("write", {})
            except Exception:
                try:
                    ctx.calls.tool("echo", {})
                except Exception:
                    pass
            return NodeResult.success({"pretend": True})

        agent, _, _, _, _, _ = self.build([
            ("call", custom, NodeCallPolicy(allowed_tools=frozenset({"echo"}))),
        ])
        result = agent.run({})
        self.assertEqual((result.status, result.reason_code), (RunStatus.BLOCKED, "TOOL_NOT_EXPOSED"))
        self.assertEqual(self.effects, [])
        self.assertNotIn("pretend", result.state)

    def test_model_retry_consumes_attempts_from_shared_run_budget(self):
        requests = []

        class Timeout(Exception):
            reason_code = "MODEL_TIMEOUT"

        class Model:
            def complete(self, messages):
                requests.append(messages)
                if len(requests) == 1:
                    raise Timeout()
                return {"content": "ok"}

        def custom(ctx):
            return NodeResult.success({"answer": ctx.calls.model([{"role": "user", "content": "hi"}])["content"]})

        policy = NodeCallPolicy(limits=HarnessLimits(2, 1), model_retry=ModelRetryPolicy(1))
        agent, _, _, audit, _, _ = self.build([("ask", custom, policy)], model=Model(), limits=HarnessLimits(2, 1))
        result = agent.run({})
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(len(requests), 2)
        self.assertEqual(sum(e.event_type == "run_budget.claimed" for e in audit.events(result.run_id)), 2)

    def test_partial_stream_is_published_and_never_retried(self):
        requests = []

        class Timeout(Exception):
            reason_code = "MODEL_TIMEOUT"

        class Model:
            def complete_stream(self, messages, on_delta):
                requests.append(messages)
                on_delta("partial")
                raise Timeout()

        def custom(ctx):
            ctx.calls.model([{"role": "user", "content": "hi"}])
            return NodeResult.success()

        policy = NodeCallPolicy(limits=HarnessLimits(2, 1), model_retry=ModelRetryPolicy(1))
        agent, _, _, _, _, _ = self.build([("ask", custom, policy)], model=Model())
        result = agent.run({})
        self.addCleanup(default_bus().forget, result.run_id)
        self.assertEqual(result.reason_code, "MODEL_TIMEOUT")
        self.assertEqual(len(requests), 1)
        events, _, closed = default_bus().history(result.run_id)
        self.assertEqual(events, [("ask", "partial")])
        self.assertTrue(closed)

    def test_custom_model_context_trimming_matches_builtin(self):
        def custom(ctx):
            reply = ctx.calls.model([
                {"role": "system", "content": "system"},
                {"role": "user", "content": "old"},
                {"role": "assistant", "content": "old reply"},
                {"role": "user", "content": "current"},
            ])
            return NodeResult.success({"answer": reply["content"]})

        agent, _, _, audit, _, _ = self.build([
            ("ask", custom, NodeCallPolicy(context=ContextLimits(max_messages=2))),
        ], model=self.model())
        result = agent.run({})
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual([m["content"] for m in self.model_requests[0]], ["system", "current"])
        self.assertEqual(audit.events(result.run_id)[0].summary["dropped_message_count"], 2)

    def test_builtin_and_custom_model_nodes_share_run_limit(self):
        def custom(ctx):
            return NodeResult.success({"answer": ctx.calls.model([{"role": "user", "content": "custom"}])["content"]})

        agent, _, _, _, _, _ = self.build([
            ("first", llm_node({"prompt": "builtin"}), None),
            ("second", custom, NodeCallPolicy()),
        ], model=self.model(), limits=HarnessLimits(1, 1))
        result = agent.run({})
        self.assertEqual(result.reason_code, "MAX_MODEL_CALLS")
        self.assertEqual(len(self.model_requests), 1)

    def test_builtin_and_custom_tools_share_run_limit(self):
        agent, _, _, _, _, _ = self.build([
            ("first", tool_node({"name": "echo"}), None),
            ("second", lambda ctx: NodeResult.success(ctx.calls.tool("echo", {})),
             NodeCallPolicy(allowed_tools=frozenset({"echo"}))),
        ], limits=HarnessLimits(1, 1))
        self.assertEqual(agent.run({}).reason_code, "MAX_TOOL_CALLS")
        self.assertEqual(len(self.effects), 1)

    def test_audit_failure_stops_before_tool_execution(self):
        agent, _, _, _, _, _ = self.build([
            ("call", lambda ctx: NodeResult.success(ctx.calls.tool("echo", {})),
             NodeCallPolicy(allowed_tools=frozenset({"echo"}))),
        ])
        with patch("agent_core.harness.FileHarnessAuditStore.append", side_effect=OSError("disk full")):
            result = agent.run({})
        self.assertEqual(result.reason_code, "AUDIT_WRITE_FAILED")
        self.assertEqual(self.effects, [])

    def test_cancellation_before_managed_call_does_not_invoke_adapter(self):
        for kind in ("tool", "model"):
            with self.subTest(kind=kind):
                runtime_ref = []

                def custom(ctx):
                    runtime_ref[0].cancel(ctx.run_id)
                    if kind == "tool":
                        ctx.calls.tool("echo", {})
                    else:
                        ctx.calls.model([{"role": "user", "content": "hi"}])
                    return NodeResult.success()

                agent, runtime, _, _, _, _ = self.build([
                    ("call", custom, NodeCallPolicy(allowed_tools=frozenset({"echo"}))),
                ], model=self.model())
                runtime_ref.append(runtime)
                self.assertEqual(agent.run({}).status, RunStatus.CANCELED)
                self.assertEqual(self.effects, [])
                self.assertEqual(self.model_requests, [])

    def test_call_service_cannot_escape_completed_node(self):
        captured = []

        def custom(ctx):
            captured.append(ctx.calls)
            return NodeResult.success(ctx.calls.tool("echo", {}))

        agent, _, _, _, _, _ = self.build([
            ("call", custom, NodeCallPolicy(allowed_tools=frozenset({"echo"}))),
        ])
        self.assertEqual(agent.run({}).status, RunStatus.COMPLETED)
        with self.assertRaises(ManagedCallStopped) as stopped:
            captured[0].tool("echo", {})
        self.assertEqual(stopped.exception.reason_code, "MANAGED_CALLS_CLOSED")
        self.assertEqual(len(self.effects), 1)

    def test_sync_subagent_custom_call_inherits_parent_run_budget(self):
        def ask(ctx):
            return NodeResult.success({"answer": ctx.calls.model([{"role": "user", "content": "hi"}])["content"]})

        child = (AgentBuilder("child", model=self.model()).add_node("ask", ask, calls=NodeCallPolicy())
                 .set_entry("ask").build(runtime=Runtime(store=RunStore(self.path / "child"))))
        agent, _, _, audit, _, _ = self.build([
            ("first", ask, NodeCallPolicy()),
            ("child", subagent_node({"agent": "child"}, resolver=lambda name, memory: child), None),
        ], model=self.model(), limits=HarnessLimits(2, 1))
        result = agent.run({})
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(len(self.model_requests), 2)
        claims = [e for e in audit.events(result.run_id) if e.event_type == "run_budget.claimed"]
        self.assertEqual(len(claims), 2)
        self.assertNotEqual(claims[1].summary["source_run_id"], result.run_id)

    def test_policy_is_validated_at_assembly(self):
        for kwargs in ({"allowed_tools": ["echo"]}, {"capabilities": frozenset({""})},
                       {"limits": None}, {"model_retry": None}):
            with self.subTest(kwargs=kwargs), self.assertRaises((ValueError, TypeError)):
                NodeCallPolicy(**kwargs)
        with self.assertRaises(TypeError):
            AgentBuilder("bad").add_node("call", lambda ctx: NodeResult.success(), calls={})

    def test_tool_retry_shares_attempt_budget_and_obeys_read_only_flag(self):
        class Timeout(Exception):
            reason_code = "TOOL_TIMEOUT"

        def read(args):
            self.effects.append(args)
            if len(self.effects) == 1:
                raise Timeout()
            return {"ok": True}

        self.tools.register(Tool("read", read, read_only=True))
        policy = NodeCallPolicy(allowed_tools=frozenset({"read"}), limits=HarnessLimits(1, 2),
                                tool_retry=ToolRetryPolicy(1))
        agent, _, _, audit, _, _ = self.build([
            ("read", lambda ctx: NodeResult.success(ctx.calls.tool("read", {})), policy),
        ], limits=HarnessLimits(1, 2))
        result = agent.run({})
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(len(self.effects), 2)
        self.assertEqual(sum(e.event_type == "run_budget.claimed" for e in audit.events(result.run_id)), 2)

    def test_completed_approved_result_replayed_without_extra_call_or_claim(self):
        nodes = [("write", lambda ctx: NodeResult.success(ctx.calls.tool("write", {})),
                  NodeCallPolicy(allowed_tools=frozenset({"write"})))]
        agent, runtime, checkpoints, _, _, decisions = self.build(nodes)
        waiting = agent.run({})
        decisions.decide(waiting.run, waiting.run.pending_approval["request_id"], "APPROVED")
        with patch.object(checkpoints, "save", side_effect=OSError("checkpoint unavailable")):
            failed = runtime.resume_approved(agent.graph, checkpoints.load(waiting.run_id), decisions=decisions)
        self.assertEqual(failed.reason_code, "CHECKPOINT_SAVE_FAILED")
        agent, runtime, checkpoints, audit, _, _ = self.build(nodes)
        recovered = runtime.resume(agent.graph, checkpoints.load(waiting.run_id))
        self.assertEqual(recovered.status, RunStatus.COMPLETED)
        self.assertEqual(self.effects, [{}])
        self.assertEqual(sum(e.event_type == "run_budget.claimed" for e in audit.events(waiting.run_id)), 1)

    def test_approved_call_cannot_be_changed_to_a_model_call_on_recovery(self):
        agent, _, _, _, _, decisions = self.build([
            ("call", lambda ctx: NodeResult.success(ctx.calls.tool("write", {})),
             NodeCallPolicy(allowed_tools=frozenset({"write"}))),
        ])
        waiting = agent.run({})
        decisions.decide(waiting.run, waiting.run.pending_approval["request_id"], "APPROVED")
        agent, runtime, checkpoints, _, _, _ = self.build([
            ("call", lambda ctx: NodeResult.success(ctx.calls.model([{"role": "user", "content": "changed"}])),
             NodeCallPolicy()),
        ], model=self.model())
        result = runtime.resume_approved(agent.graph, checkpoints.load(waiting.run_id), decisions=decisions)
        self.assertEqual(result.reason_code, "APPROVAL_REQUEST_MISMATCH")
        self.assertEqual(self.effects, [])
        self.assertEqual(self.model_requests, [])

    def test_call_policy_and_stop_cannot_be_replaced_through_public_attributes(self):
        def custom(ctx):
            with self.assertRaises(AttributeError):
                ctx.calls.policy = NodeCallPolicy(allowed_tools=frozenset({"write"}))
            try:
                ctx.calls.tool("write", {})
            except ManagedCallStopped:
                with self.assertRaises(AttributeError):
                    ctx.calls.stop_result = None
            return NodeResult.success({"pretend": True})

        agent, _, _, _, _, _ = self.build([("call", custom, NodeCallPolicy())])
        result = agent.run({})
        self.assertEqual(result.reason_code, "TOOL_NOT_EXPOSED")
        self.assertEqual(self.effects, [])
        self.assertNotIn("pretend", result.state)

    def test_captured_context_cannot_create_calls_after_node_exit(self):
        contexts = []

        def custom(ctx):
            contexts.append(ctx)
            return NodeResult.success()

        agent, _, _, _, _, _ = self.build([
            ("call", custom, NodeCallPolicy(allowed_tools=frozenset({"echo"}))),
        ])
        self.assertEqual(agent.run({}).status, RunStatus.COMPLETED)
        with self.assertRaises(ManagedCallStopped) as stopped:
            contexts[0].calls.tool("echo", {})
        self.assertEqual(stopped.exception.reason_code, "MANAGED_CALLS_CLOSED")
        self.assertEqual(self.effects, [])

    def test_stream_deltas_survive_multiple_model_nodes_in_one_run(self):
        class Model:
            def complete_stream(self, messages, on_delta):
                on_delta("text")
                return {"content": "text"}

        def custom(ctx):
            return NodeResult.success({"answer": ctx.calls.model([{"role": "user", "content": "hi"}])["content"]})

        agent, _, _, _, _, _ = self.build([
            ("first", llm_node({"prompt": "builtin"}), None),
            ("second", custom, NodeCallPolicy()),
        ], model=Model())
        result = agent.run({})
        self.addCleanup(default_bus().forget, result.run_id)
        self.assertEqual(result.status, RunStatus.COMPLETED)
        events, _, closed = default_bus().history(result.run_id)
        self.assertEqual(events, [("first", "text"), ("second", "text")])
        self.assertTrue(closed)


if __name__ == "__main__":
    unittest.main()
