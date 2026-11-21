"""Harness M0 component contracts, independent of domain applications."""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from agent_core import (
    AgentNodeContext,
    DefaultModelToolLoop,
    DefaultToolPolicy,
    ContextAssembler,
    ContextLimits,
    ContextLimitError,
    HarnessBudget,
    HarnessLimits,
    RunBudget,
    ModelInvocationPipeline,
    ModelRetryPolicy,
    NodeResult,
    ToolRegistry,
    Tool,
    ToolIdempotency,
    ToolInvocationPipeline,
    ToolRetryPolicy,
    ToolResult,
    ToolStatus,
    FileHarnessAuditStore,
    HarnessAudit,
    HarnessAuditError,
    InMemoryHarnessAuditStore,
)
from agent_core.nodes import agent_loop_node


class HarnessBudgetTests(unittest.TestCase):
    def test_unrecorded_model_result_remains_unknown_on_resume(self) -> None:
        class FailingAudit(HarnessAudit):
            def record(self, run_id, event_type, status, **kwargs):
                if event_type == "model.completed":
                    raise HarnessAuditError("AUDIT_WRITE_FAILED")
                return super().record(run_id, event_type, status, **kwargs)

        store = InMemoryHarnessAuditStore()
        audit = FailingAudit(store)
        budget = RunBudget(HarnessLimits(2, 1), audit, "unrecorded")
        model = ModelInvocationPipeline(
            lambda messages, schemas: {"content": "ok"},
            HarnessBudget(HarnessLimits(1, 1)), audit=audit,
            run_id="unrecorded", run_budget=budget,
        )
        with self.assertRaises(HarnessAuditError):
            model.complete([], ())
        recovered_audit = HarnessAudit(store)
        with self.assertRaisesRegex(Exception, "MODEL_OUTCOME_UNKNOWN"):
            RunBudget(HarnessLimits(2, 1), recovered_audit, "unrecorded").claim(
                "model", "new-call", node_id=None, execution_id=None)

    def test_run_budget_survives_audit_reload_and_blocks_unknown_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            limits = HarnessLimits(1, 1)
            audit = HarnessAudit(FileHarnessAuditStore(directory))
            budget = RunBudget(limits, audit, "durable-budget")
            model = ModelInvocationPipeline(
                lambda messages, schemas: {"content": "ok"},
                HarnessBudget(HarnessLimits(1, 1)), audit=audit,
                run_id="durable-budget", run_budget=budget,
            )
            model.complete([], ())
            recovered_audit = HarnessAudit(FileHarnessAuditStore(directory))
            recovered = RunBudget(limits, recovered_audit, "durable-budget")
            with self.assertRaisesRegex(Exception, "MAX_MODEL_CALLS"):
                recovered.claim("model", "second", node_id="next", execution_id="next")
            with self.assertRaisesRegex(Exception, "RUN_BUDGET_LIMIT_MISMATCH"):
                RunBudget(HarnessLimits(2, 1), recovered_audit, "durable-budget").claim(
                    "model", "second", node_id=None, execution_id=None)

            pending = RunBudget(limits, recovered_audit, "unknown-budget")
            pending.claim("model", "possibly-sent", node_id="ask", execution_id="first")
            after_restart = RunBudget(limits, HarnessAudit(FileHarnessAuditStore(directory)),
                                      "unknown-budget")
            with self.assertRaisesRegex(Exception, "MODEL_OUTCOME_UNKNOWN"):
                after_restart.claim("model", "retry", node_id="ask", execution_id="second")

    def test_limits_require_positive_integers(self) -> None:
        for kwargs in (
            {"max_model_calls": 0, "max_tool_calls": 1},
            {"max_model_calls": 1, "max_tool_calls": 0},
            {"max_model_calls": True, "max_tool_calls": 1},
            {"max_model_calls": 1, "max_tool_calls": 1.5},
        ):
            with self.assertRaises(ValueError):
                HarnessLimits(**kwargs)

    def test_budget_stops_at_each_limit(self) -> None:
        budget = HarnessBudget(HarnessLimits(max_model_calls=2, max_tool_calls=1))
        self.assertTrue(budget.claim_model_call())
        self.assertTrue(budget.claim_model_call())
        self.assertFalse(budget.claim_model_call())
        self.assertTrue(budget.claim_tool_call())
        self.assertFalse(budget.claim_tool_call())
        self.assertEqual((budget.model_calls, budget.tool_calls), (2, 1))
        self.assertEqual(budget.usage(), {
            "model_calls": 2, "remaining_model_calls": 0,
            "tool_calls": 1, "remaining_tool_calls": 0,
        })

    def test_budget_requires_limits(self) -> None:
        with self.assertRaises(TypeError):
            HarnessBudget("limits")


class ModelInvocationPipelineTests(unittest.TestCase):
    def test_model_retry_cancellation_during_backoff_stops_next_attempt(self) -> None:
        class Timeout(Exception):
            reason_code = "MODEL_TIMEOUT"

        cancelled = False
        calls = []
        audit = HarnessAudit(InMemoryHarnessAuditStore())

        def invoke(messages, schemas):
            calls.append(True)
            raise Timeout("timeout")

        def cancel_during_wait(seconds):
            nonlocal cancelled
            cancelled = True

        pipeline = ModelInvocationPipeline(
            invoke, HarnessBudget(HarnessLimits(3, 1)),
            audit=audit, run_id="model-cancel",
            retry_policy=ModelRetryPolicy(2, 0.1),
            retry_allowed=lambda error: not cancelled,
        )
        with patch("agent_core.harness.sleep", side_effect=cancel_during_wait):
            with self.assertRaises(Timeout):
                pipeline.complete([], ())
        self.assertEqual(len(calls), 1)
        self.assertEqual(audit.events("model-cancel")[-1].event_type,
                         "model.retry_cancelled")

    def test_retry_policy_validates_bounds(self) -> None:
        for values in ({"max_retries": -1}, {"max_retries": 6},
                       {"max_retries": True}, {"backoff_seconds": -1},
                       {"backoff_seconds": 31}, {"backoff_seconds": True}):
            with self.assertRaises(ValueError):
                ModelRetryPolicy(**values)
        with self.assertRaises(TypeError):
            ModelInvocationPipeline(lambda messages, schemas: {},
                                    HarnessBudget(HarnessLimits(1, 1)), retry_policy="retry")

    def test_transient_failure_retries_with_attempt_audit_and_budget(self) -> None:
        class ModelError(Exception):
            reason_code = "MODEL_TIMEOUT"

        calls = 0

        def complete(messages, schemas):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise ModelError("timeout")
            return {"content": "ok"}

        audit = HarnessAudit(InMemoryHarnessAuditStore())
        budget = HarnessBudget(HarnessLimits(3, 1))
        pipeline = ModelInvocationPipeline(
            complete, budget, audit=audit, run_id="retry-run",
            retry_policy=ModelRetryPolicy(2, 0.25),
        )
        with patch("agent_core.harness.sleep") as wait:
            self.assertEqual(pipeline.complete([{"role": "user", "content": "hi"}], ()),
                             {"content": "ok"})
        self.assertEqual(calls, 3)
        self.assertEqual(budget.model_calls, 3)
        self.assertEqual([call.args[0] for call in wait.call_args_list], [0.25, 0.5])
        events = audit.events("retry-run")
        self.assertEqual([event.event_type for event in events], [
            "model.requested", "model.attempt_failed", "model.attempt_failed",
            "model.attempt_completed", "model.completed",
        ])
        self.assertEqual([event.summary["attempt"] for event in events[1:4]], [1, 2, 3])
        self.assertEqual([event.summary["budget"]["model_calls"] for event in events[1:]],
                         [1, 2, 3, 3])
        self.assertEqual(events[-1].summary["budget"]["remaining_model_calls"], 0)
        self.assertEqual(len({event.call_id for event in events}), 1)
        self.assertEqual(events[1].reason_code, "MODEL_TIMEOUT")

    def test_nontransient_or_exhausted_budget_never_retries(self) -> None:
        for reason, budget_calls in (("MODEL_HTTP_ERROR", 3), ("MODEL_TIMEOUT", 1)):
            class ModelError(Exception):
                reason_code = reason

            calls = []

            def complete(messages, schemas):
                calls.append(True)
                raise ModelError("failed")

            audit = HarnessAudit(InMemoryHarnessAuditStore())
            pipeline = ModelInvocationPipeline(
                complete, HarnessBudget(HarnessLimits(budget_calls, 1)),
                audit=audit, run_id="no-retry-run",
                retry_policy=ModelRetryPolicy(2, 1),
            )
            with patch("agent_core.harness.sleep") as wait:
                with self.assertRaises(ModelError):
                    pipeline.complete([], ())
            self.assertEqual(len(calls), 1)
            wait.assert_not_called()
            self.assertEqual([event.event_type for event in audit.events("no-retry-run")], [
                "model.requested", "model.attempt_failed", "model.failed",
            ])

    def test_retry_limit_preserves_final_error(self) -> None:
        class ModelError(Exception):
            reason_code = "MODEL_UNREACHABLE"

        calls = []

        def complete(messages, schemas):
            calls.append(True)
            raise ModelError("offline")

        audit = HarnessAudit(InMemoryHarnessAuditStore())
        budget = HarnessBudget(HarnessLimits(4, 1))
        pipeline = ModelInvocationPipeline(
            complete, budget, audit=audit, run_id="limit-run",
            retry_policy=ModelRetryPolicy(2),
        )
        with self.assertRaises(ModelError):
            pipeline.complete([], ())
        self.assertEqual((len(calls), budget.model_calls), (3, 3))
        events = audit.events("limit-run")
        self.assertEqual([event.event_type for event in events], [
            "model.requested", "model.attempt_failed", "model.attempt_failed",
            "model.attempt_failed", "model.failed",
        ])
        self.assertEqual(events[-2].summary["retry"], False)

    def test_attempt_audit_failure_stops_before_retry(self) -> None:
        class ModelError(Exception):
            reason_code = "MODEL_TIMEOUT"

        class BrokenAudit(HarnessAudit):
            def record(self, run_id, event_type, status, **kwargs):
                if event_type == "model.attempt_failed":
                    raise HarnessAuditError("AUDIT_WRITE_FAILED")
                return super().record(run_id, event_type, status, **kwargs)

        calls = []

        def complete(messages, schemas):
            calls.append(True)
            raise ModelError("timeout")

        pipeline = ModelInvocationPipeline(
            complete, HarnessBudget(HarnessLimits(3, 1)),
            audit=BrokenAudit(), run_id="audit-failure",
            retry_policy=ModelRetryPolicy(2, 1),
        )
        with patch("agent_core.harness.sleep") as wait:
            with self.assertRaises(HarnessAuditError):
                pipeline.complete([], ())
        self.assertEqual(len(calls), 1)
        wait.assert_not_called()

    def test_audit_reports_trimmed_messages_and_context_rejection(self) -> None:
        audit = HarnessAudit(InMemoryHarnessAuditStore())
        seen: list[list[dict]] = []
        messages = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "now"},
        ]
        pipeline = ModelInvocationPipeline(
            lambda current, schemas: seen.append(current) or {"content": "ok"},
            HarnessBudget(HarnessLimits(2, 1)), audit=audit, run_id="trim-run",
            context=ContextAssembler(ContextLimits(max_messages=1)),
        )
        pipeline.complete(messages, ())
        self.assertEqual(seen, [messages[-1:]])
        self.assertEqual(audit.events("trim-run")[0].summary["dropped_message_count"], 2)

        rejected = ModelInvocationPipeline(
            lambda current, schemas: self.fail("model must not be called"),
            HarnessBudget(HarnessLimits(1, 1)), audit=audit, run_id="reject-run",
            context=ContextAssembler(ContextLimits(max_chars=1)),
        )
        with self.assertRaises(ContextLimitError):
            rejected.complete(messages, ())
        event = audit.events("reject-run")[0]
        self.assertEqual((event.event_type, event.reason_code),
                         ("model.rejected", "CONTEXT_LIMIT_EXCEEDED"))

    def test_context_assembler_keeps_system_and_latest_user(self) -> None:
        assembler = ContextAssembler(ContextLimits(max_messages=3))
        messages = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "latest"},
        ]
        self.assertEqual(assembler.assemble(messages), [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "latest"},
        ])

    def test_context_keeps_tool_call_and_result_together(self) -> None:
        messages = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "now"},
            {"role": "assistant", "tool_calls": [{"id": "call-1"}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "result"},
        ]
        self.assertEqual(ContextAssembler(ContextLimits(max_messages=4)).assemble(messages),
                         [messages[0], *messages[3:]])
        with self.assertRaises(ContextLimitError):
            ContextAssembler(ContextLimits(max_messages=3)).assemble(messages)

    def test_context_limits_validate_positive_integers(self) -> None:
        with self.assertRaises(ValueError):
            ContextLimits(max_messages=0)
        with self.assertRaises(ValueError):
            ContextLimits(max_chars=True)

    def test_context_char_limit_keeps_complete_current_turn(self) -> None:
        messages = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "now"},
        ]
        current_size = ContextAssembler._chars(messages[-1:])
        self.assertEqual(ContextAssembler(ContextLimits(max_chars=current_size)).assemble(messages),
                         messages[-1:])
        with self.assertRaises(ContextLimitError):
            ContextAssembler(ContextLimits(max_chars=current_size - 1)).assemble(messages)

    def test_claims_before_call_and_maps_budget_exhaustion(self) -> None:
        calls: list[str] = []

        def complete(messages, schemas):
            calls.append("called")
            return {"content": "ok"}

        pipeline = ModelInvocationPipeline(
            complete, HarnessBudget(HarnessLimits(max_model_calls=1, max_tool_calls=1))
        )
        self.assertEqual(pipeline.complete([], []), {"content": "ok"})
        with self.assertRaisesRegex(Exception, "MAX_TOOL_ROUNDS"):
            pipeline.complete([], [])
        self.assertEqual(calls, ["called"])

    def test_model_exception_preserves_exception_for_loop_mapping(self) -> None:
        class ModelError(Exception):
            reason_code = "MODEL_UNAVAILABLE"

        def complete(messages, schemas):
            raise ModelError("offline")

        pipeline = ModelInvocationPipeline(
            complete, HarnessBudget(HarnessLimits(max_model_calls=1, max_tool_calls=1))
        )
        with self.assertRaises(ModelError):
            pipeline.complete([], [])


class ToolInvocationPipelineTests(unittest.TestCase):
    def test_tool_retry_policy_validates_bounds(self) -> None:
        for values in ({"max_retries": -1}, {"max_retries": 6},
                       {"max_retries": True}, {"backoff_seconds": -1},
                       {"backoff_seconds": 31}):
            with self.assertRaises(ValueError):
                ToolRetryPolicy(**values)

    def test_idempotency_lookup_failure_prevents_side_effect(self) -> None:
        calls = []

        def lookup(key):
            raise OSError("external state unavailable")

        registry = ToolRegistry()
        registry.register(Tool("write", lambda args: self.fail("unkeyed call"),
                               side_effect=True,
                               idempotency=ToolIdempotency(
                                   lambda args, key: calls.append(key) or {}, lookup)))
        pipeline = ToolInvocationPipeline(
            registry, granted_capabilities=frozenset(),
            budget=HarnessBudget(HarnessLimits(1, 3)),
            retry_policy=ToolRetryPolicy(2),
        )
        result = pipeline.invoke("write", {})
        self.assertEqual(result.reason_code, "IDEMPOTENCY_CHECK_FAILED")
        self.assertEqual(calls, [])

    def test_tool_attempt_audit_failure_prevents_retry(self) -> None:
        class Timeout(Exception):
            reason_code = "TOOL_TIMEOUT"

        class BrokenAudit(HarnessAudit):
            def record(self, run_id, event_type, status, **kwargs):
                if event_type == "tool.attempt_failed":
                    raise HarnessAuditError("AUDIT_WRITE_FAILED")
                return super().record(run_id, event_type, status, **kwargs)

        calls = []

        def invoke(arguments):
            calls.append(True)
            raise Timeout("timeout")

        registry = ToolRegistry()
        registry.register(Tool("read", invoke, read_only=True))
        pipeline = ToolInvocationPipeline(
            registry, granted_capabilities=frozenset(),
            budget=HarnessBudget(HarnessLimits(1, 3)),
            audit=BrokenAudit(), run_id="broken-tool-audit",
            retry_policy=ToolRetryPolicy(2, 1),
        )
        with patch("agent_core.harness.sleep") as wait:
            result = pipeline.invoke("read", {})
        self.assertEqual(result.reason_code, "AUDIT_WRITE_FAILED")
        self.assertEqual(len(calls), 1)
        wait.assert_not_called()

    def test_read_only_tool_retries_transient_failure_with_attempt_audit(self) -> None:
        class Timeout(Exception):
            reason_code = "TOOL_TIMEOUT"

        calls = []

        def invoke(arguments):
            calls.append(dict(arguments))
            if len(calls) == 1:
                raise Timeout("timeout")
            return {"ok": True}

        registry = ToolRegistry()
        registry.register(Tool("read", invoke, read_only=True))
        audit = HarnessAudit(InMemoryHarnessAuditStore())
        budget = HarnessBudget(HarnessLimits(1, 2))
        pipeline = ToolInvocationPipeline(
            registry, granted_capabilities=frozenset(), budget=budget,
            audit=audit, run_id="read-retry", retry_policy=ToolRetryPolicy(1, 0.25),
        )
        with patch("agent_core.harness.sleep") as wait:
            result = pipeline.invoke("read", {})
        self.assertEqual(result.status, ToolStatus.COMPLETED)
        self.assertEqual(len(calls), 2)
        self.assertEqual(budget.tool_calls, 2)
        wait.assert_called_once_with(0.25)
        events = audit.events("read-retry")
        self.assertEqual([event.event_type for event in events], [
            "tool.requested", "tool.attempt_failed", "tool.attempt_completed", "tool.completed",
        ])
        self.assertEqual(len({event.call_id for event in events}), 1)
        self.assertEqual([event.summary["budget"]["tool_calls"] for event in events[1:]],
                         [1, 2, 2])
        self.assertEqual(events[-1].summary["budget"]["remaining_tool_calls"], 0)

    def test_side_effect_retry_reconciles_with_stable_key(self) -> None:
        class Timeout(Exception):
            reason_code = "TOOL_TIMEOUT"

        external: dict[str, dict] = {}
        keys: list[str] = []

        def invoke(arguments, key):
            keys.append(key)
            external[key] = {"saved": True}
            raise Timeout("reply lost after commit")

        registry = ToolRegistry()
        registry.register(Tool("write", lambda args: self.fail("unkeyed call"),
                               side_effect=True,
                               idempotency=ToolIdempotency(invoke, external.get)))
        audit = HarnessAudit(InMemoryHarnessAuditStore())
        pipeline = ToolInvocationPipeline(
            registry, granted_capabilities=frozenset(),
            budget=HarnessBudget(HarnessLimits(1, 2)), audit=audit,
            run_id="write-retry", retry_policy=ToolRetryPolicy(1),
        )
        result = pipeline.invoke("write", {"private": "value"})
        self.assertEqual(result.output, {"saved": True})
        self.assertEqual(len(keys), 1)
        self.assertTrue(keys[0].startswith("write-retry:"))
        self.assertNotIn(keys[0], str(audit.events("write-retry")))

    def test_side_effect_retry_reuses_same_key_when_lookup_is_empty(self) -> None:
        class Timeout(Exception):
            reason_code = "TOOL_TIMEOUT"

        keys = []

        def invoke(arguments, key):
            keys.append(key)
            if len(keys) == 1:
                raise Timeout("timeout before commit")
            return {"saved": True}

        registry = ToolRegistry()
        registry.register(Tool("write", lambda args: self.fail("unkeyed call"),
                               side_effect=True,
                               idempotency=ToolIdempotency(invoke, lambda key: None)))
        pipeline = ToolInvocationPipeline(
            registry, granted_capabilities=frozenset(),
            budget=HarnessBudget(HarnessLimits(1, 2)),
            run_id="same-key", node_id="loop", retry_policy=ToolRetryPolicy(1),
        )
        self.assertEqual(pipeline.invoke("write", {"value": 1}).status, ToolStatus.COMPLETED)
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1])

    def test_unsafe_or_unapproved_tool_never_retries(self) -> None:
        class Timeout(Exception):
            reason_code = "TOOL_TIMEOUT"

        for approved in (False, True):
            calls = []

            def invoke(arguments):
                calls.append(True)
                raise Timeout("timeout")

            registry = ToolRegistry()
            registry.register(Tool("write", invoke, side_effect=True,
                                   requires_approval=approved))
            pipeline = ToolInvocationPipeline(
                registry, granted_capabilities=frozenset(),
                budget=HarnessBudget(HarnessLimits(1, 3)),
                retry_policy=ToolRetryPolicy(2),
            )
            result = pipeline.invoke("write", {})
            self.assertEqual(result.status, ToolStatus.WAITING_APPROVAL if approved
                             else ToolStatus.FAILED)
            self.assertEqual(len(calls), 0 if approved else 1)

    def test_tool_retry_stops_on_budget_and_nontransient_failure(self) -> None:
        for reason, budget_limit in (("TOOL_TIMEOUT", 1), ("INVALID_ARGUMENTS", 3)):
            class Failure(Exception):
                reason_code = reason

            calls = []

            def invoke(arguments):
                calls.append(True)
                raise Failure("failed")

            registry = ToolRegistry()
            registry.register(Tool("read", invoke, read_only=True))
            pipeline = ToolInvocationPipeline(
                registry, granted_capabilities=frozenset(),
                budget=HarnessBudget(HarnessLimits(1, budget_limit)),
                retry_policy=ToolRetryPolicy(2, 1),
            )
            with patch("agent_core.harness.sleep") as wait:
                result = pipeline.invoke("read", {})
            self.assertEqual(result.reason_code, reason)
            self.assertEqual(len(calls), 1)
            wait.assert_not_called()

    def test_cancelled_retry_does_not_call_tool_again(self) -> None:
        class Timeout(Exception):
            reason_code = "TOOL_TIMEOUT"

        calls = []

        def invoke(arguments):
            calls.append(True)
            raise Timeout("timeout")

        registry = ToolRegistry()
        registry.register(Tool("read", invoke, read_only=True))
        pipeline = ToolInvocationPipeline(
            registry, granted_capabilities=frozenset(),
            budget=HarnessBudget(HarnessLimits(1, 3)),
            retry_policy=ToolRetryPolicy(2, 1), retry_allowed=lambda: False,
        )
        with patch("agent_core.harness.sleep") as wait:
            result = pipeline.invoke("read", {})
        self.assertEqual(result.reason_code, "TOOL_TIMEOUT")
        self.assertEqual(len(calls), 1)
        wait.assert_not_called()

    def test_tool_retry_cancellation_during_backoff_stops_next_attempt(self) -> None:
        class Timeout(Exception):
            reason_code = "TOOL_TIMEOUT"

        cancelled = False
        calls = []
        registry = ToolRegistry()

        def invoke(arguments):
            calls.append(True)
            raise Timeout("timeout")

        def cancel_during_wait(seconds):
            nonlocal cancelled
            cancelled = True

        registry.register(Tool("read", invoke, read_only=True))
        audit = HarnessAudit(InMemoryHarnessAuditStore())
        pipeline = ToolInvocationPipeline(
            registry, granted_capabilities=frozenset(),
            budget=HarnessBudget(HarnessLimits(1, 3)),
            audit=audit, run_id="tool-cancel",
            retry_policy=ToolRetryPolicy(2, 0.1),
            retry_allowed=lambda: not cancelled,
        )
        with patch("agent_core.harness.sleep", side_effect=cancel_during_wait):
            result = pipeline.invoke("read", {})
        self.assertEqual(result.reason_code, "TOOL_TIMEOUT")
        self.assertEqual(len(calls), 1)
        self.assertIn("tool.retry_cancelled",
                      [event.event_type for event in audit.events("tool-cancel")])

    def test_policy_blocks_tools_not_exposed_by_current_node(self) -> None:
        calls: list[str] = []

        class Registry:
            def invoke(self, name, arguments, *, granted_capabilities):
                calls.append(name)
                return ToolResult(name, ToolStatus.COMPLETED, {})

        pipeline = ToolInvocationPipeline(
            Registry(), granted_capabilities=frozenset(),
            budget=HarnessBudget(HarnessLimits(max_model_calls=1, max_tool_calls=1)),
            policy=DefaultToolPolicy(frozenset({"allowed"})),
        )
        result = pipeline.invoke("hidden", {})
        self.assertEqual(result.status, ToolStatus.BLOCKED)
        self.assertEqual(result.reason_code, "TOOL_NOT_EXPOSED")
        self.assertEqual(calls, [])

    def test_budget_is_claimed_before_registry_call(self) -> None:
        calls: list[tuple[str, dict]] = []

        class Registry:
            def invoke(self, name, arguments, *, granted_capabilities):
                calls.append((name, dict(arguments)))
                return ToolResult(name, ToolStatus.COMPLETED, {"ok": True})

        pipeline = ToolInvocationPipeline(
            Registry(),
            granted_capabilities=frozenset(),
            budget=HarnessBudget(HarnessLimits(max_model_calls=1, max_tool_calls=1)),
        )
        self.assertEqual(pipeline.invoke("echo", {}).status, ToolStatus.COMPLETED)
        exhausted = pipeline.invoke("echo", {})
        self.assertEqual(exhausted.reason_code, "MAX_TOOL_CALLS")
        self.assertEqual(calls, [("echo", {})])

    def test_invalid_registry_result_is_failed(self) -> None:
        class Registry:
            def invoke(self, name, arguments, *, granted_capabilities):
                return {"ok": True}

        pipeline = ToolInvocationPipeline(
            Registry(), granted_capabilities=frozenset(),
            budget=HarnessBudget(HarnessLimits(max_model_calls=1, max_tool_calls=1)),
        )
        result = pipeline.invoke("echo", {})
        self.assertEqual(result.status, ToolStatus.FAILED)
        self.assertEqual(result.reason_code, "INVALID_TOOL_RESULT")

    def test_registry_exception_becomes_failed_reason(self) -> None:
        class Registry:
            def invoke(self, name, arguments, *, granted_capabilities):
                raise RuntimeError("registry broke")

        pipeline = ToolInvocationPipeline(
            Registry(), granted_capabilities=frozenset(),
            budget=HarnessBudget(HarnessLimits(max_model_calls=1, max_tool_calls=1)),
        )
        result = pipeline.invoke("echo", {})
        self.assertEqual(result.status, ToolStatus.FAILED)
        self.assertEqual(result.reason_code, "TOOL_EXCEPTION")


class HarnessAuditTests(unittest.TestCase):
    def test_records_only_adapter_supplied_usage(self) -> None:
        audit = HarnessAudit(InMemoryHarnessAuditStore())
        pipeline = ModelInvocationPipeline(
            lambda messages, schemas: {"content": "ok", "usage": {
                "prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6,
                "invalid": "ignored",
            }},
            HarnessBudget(HarnessLimits(1, 1)), audit=audit, run_id="usage-run",
        )
        pipeline.complete([{"role": "user", "content": "hello"}], ())
        self.assertEqual(audit.events("usage-run")[-1].summary["usage"], {
            "prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6,
        })

    def test_pipeline_records_ordered_model_and_tool_facts(self) -> None:
        audit = HarnessAudit(InMemoryHarnessAuditStore())
        budget = HarnessBudget(HarnessLimits(max_model_calls=2, max_tool_calls=2))

        def complete(messages, schemas):
            return {"content": "ok"}

        model = ModelInvocationPipeline(
            complete, budget, audit=audit, run_id="audit-run", node_id="loop", execution_id="exec",
        )
        registry = ToolRegistry()
        registry.register(Tool("echo", lambda args: {"value": args["value"]}))
        tool = ToolInvocationPipeline(
            registry, granted_capabilities=frozenset(), budget=budget,
            audit=audit, run_id="audit-run", node_id="loop", execution_id="exec",
        )
        model.complete([], [])
        tool.invoke("echo", {"value": "x"})
        events = audit.events("audit-run")
        self.assertEqual([event.sequence for event in events], [1, 2, 3, 4])
        self.assertEqual([event.event_type for event in events], [
            "model.requested", "model.completed", "tool.requested", "tool.completed",
        ])
        self.assertEqual(events[0].call_id, events[1].call_id)
        self.assertEqual(events[2].call_id, events[3].call_id)
        self.assertEqual(events[1].summary["budget"], {
            "model_calls": 1, "remaining_model_calls": 1,
            "tool_calls": 0, "remaining_tool_calls": 2,
        })
        self.assertEqual(events[3].summary["budget"], {
            "model_calls": 1, "remaining_model_calls": 1,
            "tool_calls": 1, "remaining_tool_calls": 1,
        })

    def test_budget_rejections_record_remaining_quota(self) -> None:
        audit = HarnessAudit(InMemoryHarnessAuditStore())
        budget = HarnessBudget(HarnessLimits(1, 1))
        model = ModelInvocationPipeline(
            lambda messages, schemas: {"content": "ok"}, budget,
            audit=audit, run_id="quota-run",
        )
        registry = ToolRegistry()
        registry.register(Tool("echo", lambda args: {}))
        tool = ToolInvocationPipeline(
            registry, granted_capabilities=frozenset(), budget=budget,
            audit=audit, run_id="quota-run",
        )
        model.complete([], ())
        with self.assertRaisesRegex(Exception, "MAX_TOOL_ROUNDS"):
            model.complete([], ())
        tool.invoke("echo", {})
        self.assertEqual(tool.invoke("echo", {}).reason_code, "MAX_TOOL_CALLS")
        rejected = [event for event in audit.events("quota-run")
                    if event.event_type.endswith("rejected")]
        self.assertEqual([event.summary["budget"] for event in rejected], [
            {"model_calls": 1, "remaining_model_calls": 0,
             "tool_calls": 0, "remaining_tool_calls": 1},
            {"model_calls": 1, "remaining_model_calls": 0,
             "tool_calls": 1, "remaining_tool_calls": 0},
        ])

    def test_file_store_survives_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            audit = HarnessAudit(FileHarnessAuditStore(path))
            audit.record("persisted", "tool.requested", "PENDING", call_id="call-1",
                         summary={"tool": "echo"})
            reloaded = HarnessAudit(FileHarnessAuditStore(path))
            self.assertEqual(reloaded.events("persisted")[0].summary, {"tool": "echo"})

    def test_audit_write_failure_has_stable_reason(self) -> None:
        class BrokenStore:
            def load(self, run_id):
                return ()
            def append(self, event):
                raise OSError("disk full")

        audit = HarnessAudit(BrokenStore())
        with self.assertRaisesRegex(Exception, "AUDIT_WRITE_FAILED"):
            audit.record("broken", "model.requested", "PENDING", call_id="call-1")

    def test_agent_loop_wires_context_audit_into_pipelines(self) -> None:
        audit = HarnessAudit(InMemoryHarnessAuditStore())

        class Model:
            def __init__(self):
                self.calls = 0
            def complete_with_tools(self, messages, schemas):
                self.calls += 1
                return {"content": "answer"}

        node = agent_loop_node({"prompt": "hello", "tools": [{"name": "echo"}], "route": "done"})
        result = node(AgentNodeContext(
            run_id="wired-audit", node_id="loop", execution_id="exec", state_version=0,
            input={}, state={}, model=Model(), tools=ToolRegistry(), audit=audit,
        ))
        self.assertEqual(result.status.value, "SUCCEEDED")
        self.assertEqual([event.event_type for event in audit.events("wired-audit")],
                         ["model.requested", "model.completed"])


class LoopCompositionTests(unittest.TestCase):
    def test_custom_loop_can_be_injected_without_runtime_changes(self) -> None:
        seen: dict[str, object] = {}

        class CustomLoop:
            def run(self, complete, messages, schemas, pipeline, *, into, route):
                seen["schemas"] = list(schemas)
                seen["pipeline"] = pipeline
                return NodeResult.success({into: "custom"}, route=route)

        class Model:
            def complete_with_tools(self, messages, schemas):
                return {"content": "unused"}

        registry = ToolRegistry()
        node = agent_loop_node(
            {"prompt": "{q}", "tools": [{"name": "echo"}], "route": "done"},
            loop=CustomLoop(),
        )
        result = node(AgentNodeContext(
            run_id="harness-test", node_id="loop", execution_id="exec",
            state_version=0, input={"q": "hello"}, state={"q": "hello"},
            model=Model(), tools=registry,
        ))
        self.assertEqual(result.status.value, "SUCCEEDED")
        self.assertEqual(result.updates["answer"], "custom")
        self.assertEqual(seen["schemas"], [{"name": "echo"}])
        self.assertIsNotNone(seen["pipeline"])

    def test_default_loop_is_the_declared_default(self) -> None:
        self.assertIsInstance(DefaultModelToolLoop(), DefaultModelToolLoop)


if __name__ == "__main__":
    unittest.main()
