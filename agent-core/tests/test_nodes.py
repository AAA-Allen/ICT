"""Declarative node factory library contract tests."""

import json
import tempfile
import unittest
from pathlib import Path

from agent_core import (AgentBuilder, AgentNodeContext, FileCheckpointStore,
                        FileHarnessAuditStore, HarnessAudit, InMemoryHarnessAuditStore, NodeResult,
                        RunStatus, Runtime, RunStore, Tool, ToolRegistry)
from agent_core.nodes import (
    NODE_FACTORIES,
    build_agent_from_config,
    register_node_factory,
    subagent_node,
)

WORKBENCH_CONFIG = {
    "name": "config-agent",
    "instructions": "配置装配的演示 Agent",
    "description": "assembled from nodes.py",
    "metadata": {"kind": "declarative"},
    "entry": "intake",
    "nodes": [
        {"id": "intake", "type": "validate_input",
         "params": {"field": "task", "blocked_reason": "EMPTY_TASK", "route": "plan"}},
        {"id": "plan", "type": "split_steps",
         "params": {"field": "task", "into": "plan", "count_key": "step_count", "route": "stats"}},
        {"id": "stats", "type": "tool",
         "params": {"name": "text_stats", "arguments": {"task": "{task}"}, "output_key": "text_stats", "route": "summary"}},
        {"id": "summary", "type": "template",
         "params": {"updates": {"summary": "任务“{task}”已拆解为 {step_count} 个步骤。"}}},
    ],
    "edges": [
        {"source": "intake", "target": "plan", "when": "plan"},
        {"source": "plan", "target": "stats", "when": "stats"},
        {"source": "stats", "target": "summary", "when": "summary"},
    ],
}


def demo_tools() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        Tool("text_stats", lambda args: {"chars": len(args["task"]), "words": len(args["task"].split())})
    )
    return registry


class ConfigAssemblyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = Runtime()

    def test_run_budget_limits_model_calls_across_nodes(self) -> None:
        config = {
            "name": "limited", "entry": "first",
            "harness": {"max_model_calls": 1, "max_tool_calls": 1},
            "nodes": [
                {"id": "first", "type": "llm", "params": {"prompt": "one", "route": "second"}},
                {"id": "second", "type": "llm", "params": {"prompt": "two"}},
            ],
            "edges": [{"source": "first", "target": "second", "when": "second"}],
        }
        class Model:
            def __init__(self):
                self.calls = 0
            def complete(self, messages):
                self.calls += 1
                return {"content": "ok"}

        model = Model()
        with tempfile.TemporaryDirectory() as directory:
            audit = HarnessAudit(FileHarnessAuditStore(directory))
            agent = build_agent_from_config(config, runtime=self.runtime, model=model, audit=audit)
            result = agent.run({})
            self.assertEqual((result.status, result.reason_code),
                             (RunStatus.FAILED, "MAX_MODEL_CALLS"))
            self.assertEqual(model.calls, 1)
            self.assertEqual(len([event for event in audit.events(result.run_id)
                                  if event.event_type == "run_budget.claimed"]), 1)
            self.assertEqual(audit.events(result.run_id)[-1].reason_code, "MAX_MODEL_CALLS")
        with self.assertRaises(ValueError):
            build_agent_from_config(config, runtime=self.runtime, model=model)
        with self.assertRaises(ValueError):
            build_agent_from_config(config, runtime=self.runtime, model=model,
                                    audit=HarnessAudit(InMemoryHarnessAuditStore()))

    def test_run_budget_survives_checkpoint_resume(self) -> None:
        config = {
            "name": "limited-resume", "entry": "first",
            "harness": {"max_model_calls": 1, "max_tool_calls": 2},
            "nodes": [
                {"id": "first", "type": "llm", "params": {"prompt": "one", "route": "second"}},
                {"id": "second", "type": "llm", "params": {"prompt": "two"}},
            ],
            "edges": [{"source": "first", "target": "second", "when": "second"}],
        }
        class Model:
            def __init__(self):
                self.calls = 0
            def complete(self, messages):
                self.calls += 1
                return {"content": "ok"}

        with tempfile.TemporaryDirectory() as directory:
            checkpoint_store = FileCheckpointStore(Path(directory) / "checkpoints")
            audit_path = Path(directory) / "audit"
            model = Model()
            runtime = Runtime(checkpoint_store=checkpoint_store)
            agent = build_agent_from_config(
                config, runtime=runtime, model=model,
                audit=HarnessAudit(FileHarnessAuditStore(audit_path)))
            result = agent.run({})
            self.assertEqual(result.reason_code, "MAX_MODEL_CALLS")
            checkpoint = checkpoint_store.load(result.run_id)
            self.assertEqual(checkpoint.next_node, "second")
            recovered_runtime = Runtime(checkpoint_store=FileCheckpointStore(
                Path(directory) / "checkpoints"))
            recovered = build_agent_from_config(
                config, runtime=recovered_runtime, model=model,
                audit=HarnessAudit(FileHarnessAuditStore(audit_path)))
            resumed = recovered_runtime.resume(recovered.graph, checkpoint)
            self.assertEqual(resumed.reason_code, "MAX_MODEL_CALLS")
            self.assertEqual(model.calls, 1)

    def test_run_budget_limits_tools_across_loop_nodes(self) -> None:
        config = {
            "name": "limited-tools", "entry": "first",
            "harness": {"max_model_calls": 4, "max_tool_calls": 1},
            "nodes": [
                {"id": "first", "type": "agent_loop", "params": {
                    "prompt": "one", "tools": [{"name": "read"}],
                    "max_rounds": 2, "route": "second"}},
                {"id": "second", "type": "agent_loop", "params": {
                    "prompt": "two", "tools": [{"name": "read"}],
                    "max_rounds": 2}},
            ],
            "edges": [{"source": "first", "target": "second", "when": "second"}],
        }
        class Model:
            def complete_with_tools(self, messages, schemas):
                if any(message.get("role") == "tool" for message in messages):
                    return {"content": "done"}
                return {"tool_calls": [{"id": "read-1", "name": "read", "arguments": {}}]}

        calls = []
        registry = ToolRegistry()
        registry.register(Tool("read", lambda args: calls.append(True) or {}, read_only=True))
        with tempfile.TemporaryDirectory() as directory:
            audit = HarnessAudit(FileHarnessAuditStore(directory))
            result = build_agent_from_config(
                config, runtime=self.runtime, tools=registry, model=Model(),
                audit=audit).run({})
            self.assertEqual((result.status, result.reason_code),
                             (RunStatus.FAILED, "MAX_TOOL_CALLS"))
            self.assertEqual(calls, [True])
            self.assertEqual(audit.events(result.run_id)[-1].reason_code, "MAX_TOOL_CALLS")

    def test_parent_run_budget_counts_subagent_model_calls(self) -> None:
        child_config = {"name": "child", "entry": "ask", "nodes": [
            {"id": "ask", "type": "llm", "params": {"prompt": "child"}},
        ]}
        parent_config = {
            "name": "parent", "entry": "ask",
            "harness": {"max_model_calls": 1, "max_tool_calls": 1},
            "nodes": [
                {"id": "ask", "type": "llm", "params": {"prompt": "parent", "route": "child"}},
                {"id": "child", "type": "subagent", "params": {"agent": "child"}},
            ],
            "edges": [{"source": "ask", "target": "child", "when": "child"}],
        }
        class Model:
            def __init__(self):
                self.calls = 0
            def complete(self, messages):
                self.calls += 1
                return {"content": "ok"}

        with tempfile.TemporaryDirectory() as directory:
            model = Model()
            child = build_agent_from_config(child_config,
                                            runtime=Runtime(store=RunStore(Path(directory) / "runs")),
                                            model=model)
            audit = HarnessAudit(FileHarnessAuditStore(directory))
            parent = build_agent_from_config(
                parent_config, runtime=Runtime(), model=model, audit=audit,
                agent_resolver=lambda name, memory=None: child)
            result = parent.run({})
            self.assertEqual((result.status, result.reason_code),
                             (RunStatus.FAILED, "MAX_MODEL_CALLS"))
            self.assertEqual(model.calls, 1)
            self.assertEqual(child.run({}).status, RunStatus.COMPLETED)
            self.assertEqual(model.calls, 2)
            claims = [event for event in audit.events(result.run_id)
                      if event.event_type == "run_budget.claimed"]
            self.assertEqual(len(claims), 1)
            self.assertEqual(claims[0].summary["source_run_id"], result.run_id)

    def test_parent_run_budget_records_subagent_source_run(self) -> None:
        child_config = {"name": "child", "entry": "ask", "nodes": [
            {"id": "ask", "type": "llm", "params": {"prompt": "child"}},
        ]}
        parent_config = {
            "name": "parent", "entry": "child",
            "harness": {"max_model_calls": 1, "max_tool_calls": 1},
            "nodes": [{"id": "child", "type": "subagent", "params": {"agent": "child"}}],
        }
        class Model:
            def complete(self, messages):
                return {"content": "ok"}

        with tempfile.TemporaryDirectory() as directory:
            child = build_agent_from_config(child_config,
                                            runtime=Runtime(store=RunStore(Path(directory) / "runs")),
                                            model=Model())
            audit = HarnessAudit(FileHarnessAuditStore(directory))
            parent = build_agent_from_config(
                parent_config, runtime=Runtime(), audit=audit,
                agent_resolver=lambda name, memory=None: child)
            result = parent.run({})
            self.assertEqual(result.status, RunStatus.COMPLETED)
            child_run_id = result.state["sub_result"]["run_id"]
            claims = [event for event in audit.events(result.run_id)
                      if event.event_type == "run_budget.claimed"]
            self.assertEqual([event.summary["source_run_id"] for event in claims],
                             [child_run_id])

    def test_parent_run_budget_limits_subagent_tools(self) -> None:
        loop_params = {"prompt": "read", "tools": [{"name": "read"}],
                       "max_rounds": 2}
        child_config = {"name": "child", "entry": "loop", "nodes": [
            {"id": "loop", "type": "agent_loop", "params": loop_params},
        ]}
        parent_config = {
            "name": "parent", "entry": "loop",
            "harness": {"max_model_calls": 4, "max_tool_calls": 1},
            "nodes": [
                {"id": "loop", "type": "agent_loop", "params": {
                    **loop_params, "route": "child"}},
                {"id": "child", "type": "subagent", "params": {"agent": "child"}},
            ],
            "edges": [{"source": "loop", "target": "child", "when": "child"}],
        }
        class Model:
            def complete_with_tools(self, messages, schemas):
                if any(message.get("role") == "tool" for message in messages):
                    return {"content": "done"}
                return {"tool_calls": [{"id": "read-1", "name": "read", "arguments": {}}]}

        with tempfile.TemporaryDirectory() as directory:
            calls = []
            tools = ToolRegistry()
            tools.register(Tool("read", lambda args: calls.append(True) or {}, read_only=True))
            child = build_agent_from_config(child_config,
                                            runtime=Runtime(store=RunStore(Path(directory) / "runs")),
                                            model=Model(), tools=tools)
            audit = HarnessAudit(FileHarnessAuditStore(directory))
            parent = build_agent_from_config(
                parent_config, runtime=Runtime(), model=Model(), tools=tools,
                audit=audit, agent_resolver=lambda name, memory=None: child)
            result = parent.run({})
            self.assertEqual((result.status, result.reason_code),
                             (RunStatus.FAILED, "MAX_TOOL_CALLS"))
            self.assertEqual(calls, [True])
            claims = [event for event in audit.events(result.run_id)
                      if event.event_type == "run_budget.claimed"
                      and event.summary["kind"] == "tool"]
            self.assertEqual(len(claims), 1)

    def test_parent_budget_applies_to_subagent_after_checkpoint_resume(self) -> None:
        child_config = {"name": "child", "entry": "ask", "nodes": [
            {"id": "ask", "type": "llm", "params": {"prompt": "child"}},
        ]}
        parent_config = {
            "name": "parent", "entry": "ask",
            "harness": {"max_model_calls": 1, "max_tool_calls": 1},
            "nodes": [
                {"id": "ask", "type": "llm", "params": {"prompt": "parent", "route": "child"}},
                {"id": "child", "type": "subagent", "params": {"agent": "child"}},
            ],
            "edges": [{"source": "ask", "target": "child", "when": "child"}],
        }
        class Model:
            def __init__(self):
                self.calls = 0
            def complete(self, messages):
                self.calls += 1
                return {"content": "ok"}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            model = Model()
            child = build_agent_from_config(child_config,
                                            runtime=Runtime(store=RunStore(path / "runs")),
                                            model=model)
            checkpoints = FileCheckpointStore(path / "checkpoints")
            parent = build_agent_from_config(
                parent_config, runtime=Runtime(checkpoint_store=checkpoints),
                model=model, audit=HarnessAudit(FileHarnessAuditStore(path / "audit")),
                agent_resolver=lambda name, memory=None: child)
            result = parent.run({})
            checkpoint = checkpoints.load(result.run_id)
            self.assertEqual(checkpoint.next_node, "child")
            recovered_runtime = Runtime(checkpoint_store=FileCheckpointStore(path / "checkpoints"))
            recovered = build_agent_from_config(
                parent_config, runtime=recovered_runtime, model=model,
                audit=HarnessAudit(FileHarnessAuditStore(path / "audit")),
                agent_resolver=lambda name, memory=None: child)
            resumed = recovered_runtime.resume(recovered.graph, checkpoint)
            self.assertEqual(resumed.reason_code, "MAX_MODEL_CALLS")
            self.assertEqual(model.calls, 1)

    def test_config_agent_completes_with_expected_state(self) -> None:
        agent = build_agent_from_config(WORKBENCH_CONFIG, runtime=self.runtime, tools=demo_tools())
        result = agent.run({"task": "整理项目进展;准备评审"})
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.state["step_count"], 2)
        self.assertEqual(result.state["text_stats"], {"chars": 11, "words": 1})
        self.assertEqual(result.state["summary"], "任务“整理项目进展;准备评审”已拆解为 2 个步骤。")
        self.assertEqual(agent.describe()["entry"], "intake")
        self.assertEqual(agent.spec.metadata, {"kind": "declarative"})

    def test_config_agent_blocks_on_empty_field(self) -> None:
        agent = build_agent_from_config(WORKBENCH_CONFIG, runtime=self.runtime, tools=demo_tools())
        result = agent.run({"task": "   "})
        self.assertEqual(result.status, RunStatus.BLOCKED)
        self.assertEqual(result.reason_code, "EMPTY_TASK")
        self.assertEqual(result.stopped_at, "intake")

    def test_template_missing_state_key_fails_closed(self) -> None:
        config = {
            "name": "template-agent",
            "entry": "render",
            "nodes": [
                {"id": "render", "type": "template",
                 "params": {"updates": {"text": "你好 {missing_key}"}}},
            ],
        }
        agent = build_agent_from_config(config, runtime=self.runtime)
        result = agent.run({})
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.reason_code, "TEMPLATE_KEY_MISSING")

    def test_template_rejects_attribute_access_placeholders(self) -> None:
        config = {
            "name": "template-agent",
            "entry": "render",
            "nodes": [
                {"id": "render", "type": "template",
                 "params": {"updates": {"text": "{task.__class__}"}}},
            ],
        }
        agent = build_agent_from_config(config, runtime=self.runtime)
        result = agent.run({"task": "hello"})
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.reason_code, "TEMPLATE_KEY_MISSING")

    def test_tool_node_without_registry_fails_closed(self) -> None:
        config = {
            "name": "tool-agent",
            "entry": "call",
            "nodes": [
                {"id": "call", "type": "tool",
                 "params": {"name": "text_stats", "arguments": {}}},
            ],
        }
        agent = build_agent_from_config(config, runtime=self.runtime)
        result = agent.run({})
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.reason_code, "TOOL_REGISTRY_MISSING")

    def test_tool_node_reports_missing_argument_key(self) -> None:
        config = {
            "name": "tool-agent",
            "entry": "call",
            "nodes": [
                {"id": "call", "type": "tool",
                 "params": {"name": "text_stats", "arguments": {"task": "{absent}"}}},
            ],
        }
        agent = build_agent_from_config(config, runtime=self.runtime, tools=demo_tools())
        result = agent.run({})
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.reason_code, "TOOL_ARGUMENT_MISSING")

    def test_route_by_value_selects_and_falls_back(self) -> None:
        config = {
            "name": "router-agent",
            "entry": "route",
            "nodes": [
                {"id": "route", "type": "route_by_value",
                 "params": {"field": "lane", "routes": {"fast": "quick", "slow": "thorough"},
                            "default_route": "thorough"}},
                {"id": "quick", "type": "template", "params": {"updates": {"path": "quick"}}},
                {"id": "thorough", "type": "template", "params": {"updates": {"path": "thorough"}}},
            ],
            "edges": [
                {"source": "route", "target": "quick", "when": "quick"},
                {"source": "route", "target": "thorough", "when": "thorough"},
            ],
        }
        agent = build_agent_from_config(config, runtime=self.runtime)
        self.assertEqual(agent.run({"lane": "fast"}).state["path"], "quick")
        self.assertEqual(agent.run({"lane": "unknown"}).state["path"], "thorough")
        self.assertEqual(agent.run({}).state["path"], "thorough")

    def test_route_by_value_without_default_blocks(self) -> None:
        config = {
            "name": "router-agent",
            "entry": "route",
            "nodes": [
                {"id": "route", "type": "route_by_value",
                 "params": {"field": "lane", "routes": {"fast": "quick"}}},
                {"id": "quick", "type": "template", "params": {"updates": {"path": "quick"}}},
            ],
            "edges": [{"source": "route", "target": "quick", "when": "quick"}],
        }
        agent = build_agent_from_config(config, runtime=self.runtime)
        blocked = agent.run({})
        self.assertEqual(blocked.status, RunStatus.BLOCKED)
        self.assertEqual(blocked.reason_code, "ROUTE_UNRESOLVED")

    def test_subagent_delegates_and_hoists_summary(self) -> None:
        delegate_config = {
            "name": "support-triage",
            "entry": "intake",
            "nodes": [
                {"id": "intake", "type": "validate_input",
                 "params": {"field": "task", "blocked_reason": "EMPTY_TASK", "route": "render"}},
                {"id": "render", "type": "template",
                 "params": {"updates": {"summary": "子处理:{task}"}}},
            ],
            "edges": [{"source": "intake", "target": "render", "when": "render"}],
        }
        runtime = Runtime()
        delegate = build_agent_from_config(delegate_config, runtime=runtime)
        orchestrator_config = {
            "name": "orchestrator",
            "entry": "intake",
            "nodes": [
                {"id": "intake", "type": "validate_input",
                 "params": {"field": "task", "blocked_reason": "EMPTY_TASK", "route": "delegate"}},
                {"id": "delegate", "type": "subagent",
                 "params": {"agent": "support-triage", "input": {"task": "{task}"},
                            "output_key": "delegated", "summary_into": "delegated_summary",
                            "route": "render"}},
                {"id": "render", "type": "template",
                 "params": {"updates": {"summary": "委派完成:{delegated_summary}"}}},
            ],
            "edges": [
                {"source": "intake", "target": "delegate", "when": "delegate"},
                {"source": "delegate", "target": "render", "when": "render"},
            ],
        }
        agent = build_agent_from_config(
            orchestrator_config, runtime=runtime,
            agent_resolver=lambda name, memory=None: delegate if name == "support-triage" else None,
        )
        result = agent.run({"task": "修复工单"})
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.state["delegated_summary"], "子处理:修复工单")
        self.assertEqual(result.state["summary"], "委派完成:子处理:修复工单")
        delegated = result.state["delegated"]
        self.assertEqual(delegated["status"], "COMPLETED")
        self.assertTrue(delegated["run_id"])
        self.assertNotEqual(delegated["run_id"], result.run_id)

    def test_subagent_replay_reuses_persisted_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            child = (AgentBuilder("child")
                     .add_node("write", lambda ctx: calls.append(True) or
                               NodeResult.success({"summary": "done"}))
                     .set_entry("write")
                     .build(runtime=Runtime(store=RunStore(Path(directory) / "runs"))))
            audit = HarnessAudit(FileHarnessAuditStore(Path(directory) / "audit"))
            node = subagent_node({"agent": "child"}, resolver=lambda name, memory: child)
            context = AgentNodeContext(run_id="parent", node_id="delegate",
                                       execution_id="first", state_version=2,
                                       input={}, state={}, audit=audit)
            first = node(context)
            replay = node(AgentNodeContext(run_id="parent", node_id="delegate",
                                           execution_id="replay", state_version=2,
                                           input={}, state={}, audit=audit))
            self.assertEqual(first.status, replay.status)
            self.assertEqual(first.updates, replay.updates)
            self.assertEqual(calls, [True])
            self.assertEqual([event.event_type for event in audit.events("parent")], [
                "subagent.requested", "subagent.completed", "subagent.reused",
            ])

    def test_subagent_replay_blocks_when_result_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            class InterruptedChild:
                def __init__(self):
                    self.runtime = Runtime(store=RunStore(Path(directory) / "runs"))
                    self.calls = 0
                def run(self, arguments, *, run_id=None):
                    self.calls += 1
                    raise OSError("result was not persisted")

            child = InterruptedChild()
            audit = HarnessAudit(FileHarnessAuditStore(Path(directory) / "audit"))
            node = subagent_node({"agent": "child"}, resolver=lambda name, memory: child)
            context = AgentNodeContext(run_id="parent", node_id="delegate",
                                       execution_id="first", state_version=0,
                                       input={}, state={}, audit=audit)
            self.assertEqual(node(context).reason_code, "SUBAGENT_PERSISTENCE_FAILED")
            replay = node(context)
            self.assertEqual(replay.reason_code, "SUBAGENT_OUTCOME_UNKNOWN")
            self.assertEqual(child.calls, 1)

    def test_checkpoint_resume_reuses_completed_subagent(self) -> None:
        class FailingFinalCheckpoint(FileCheckpointStore):
            def save(self, checkpoint):
                if checkpoint.next_node is None:
                    raise OSError("parent final checkpoint failed")
                return super().save(checkpoint)

        config = {
            "name": "parent", "entry": "start",
            "nodes": [
                {"id": "start", "type": "template", "params": {
                    "updates": {"ready": "yes"}, "route": "delegate"}},
                {"id": "delegate", "type": "subagent", "params": {"agent": "child"}},
            ],
            "edges": [{"source": "start", "target": "delegate", "when": "delegate"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            calls = []
            child = (AgentBuilder("child")
                     .add_node("write", lambda ctx: calls.append(True) or
                               NodeResult.success({"summary": "done"}))
                     .set_entry("write")
                     .build(runtime=Runtime(store=RunStore(path / "runs"))))
            audit = HarnessAudit(FileHarnessAuditStore(path / "audit"))
            parent = build_agent_from_config(
                config, runtime=Runtime(checkpoint_store=FailingFinalCheckpoint(
                    path / "checkpoints")), audit=audit,
                agent_resolver=lambda name, memory=None: child)
            first = parent.run({})
            self.assertEqual(first.reason_code, "CHECKPOINT_SAVE_FAILED")
            checkpoint = FileCheckpointStore(path / "checkpoints").load(first.run_id)
            self.assertEqual(checkpoint.next_node, "delegate")

            recovered_runtime = Runtime(checkpoint_store=FileCheckpointStore(path / "checkpoints"))
            recovered = build_agent_from_config(
                config, runtime=recovered_runtime,
                audit=HarnessAudit(FileHarnessAuditStore(path / "audit")),
                agent_resolver=lambda name, memory=None: child)
            resumed = recovered_runtime.resume(recovered.graph, checkpoint)
            self.assertEqual(resumed.status, RunStatus.COMPLETED)
            self.assertEqual(calls, [True])
            self.assertEqual(resumed.state["sub_result"]["state"]["summary"], "done")

    def test_subagent_replay_rejects_changed_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            child = (AgentBuilder("child")
                     .add_node("done", lambda ctx: calls.append(True) or NodeResult.success({}))
                     .set_entry("done")
                     .build(runtime=Runtime(store=RunStore(Path(directory) / "runs"))))
            audit = HarnessAudit(FileHarnessAuditStore(Path(directory) / "audit"))
            context = AgentNodeContext(run_id="parent", node_id="delegate",
                                       execution_id="first", state_version=0,
                                       input={}, state={"task": "first"}, audit=audit)
            first = subagent_node({"agent": "child", "input": {"task": "{task}"}},
                                  resolver=lambda name, memory: child)
            self.assertEqual(first(context).status.value, "SUCCEEDED")
            changed = AgentNodeContext(run_id="parent", node_id="delegate",
                                       execution_id="replay", state_version=0,
                                       input={}, state={"task": "different"}, audit=audit)
            self.assertEqual(first(changed).reason_code, "SUBAGENT_REQUEST_MISMATCH")
            self.assertEqual(calls, [True])

    def test_subagent_audit_failure_prevents_execution(self) -> None:
        from agent_core import HarnessAuditError

        class BrokenAudit(HarnessAudit):
            def record(self, run_id, event_type, status, **kwargs):
                if event_type == "subagent.requested":
                    raise HarnessAuditError("AUDIT_WRITE_FAILED")
                return super().record(run_id, event_type, status, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            calls = []
            child = (AgentBuilder("child")
                     .add_node("done", lambda ctx: calls.append(True) or NodeResult.success({}))
                     .set_entry("done")
                     .build(runtime=Runtime(store=RunStore(Path(directory) / "runs"))))
            audit = BrokenAudit(FileHarnessAuditStore(Path(directory) / "audit"))
            node = subagent_node({"agent": "child"}, resolver=lambda name, memory: child)
            context = AgentNodeContext(run_id="parent", node_id="delegate",
                                       execution_id="first", state_version=0,
                                       input={}, state={}, audit=audit)
            self.assertEqual(node(context).reason_code, "AUDIT_WRITE_FAILED")
            self.assertEqual(calls, [])

    def test_legacy_subagent_without_result_store_still_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            child = (AgentBuilder("child")
                     .add_node("done", lambda ctx: calls.append(True) or NodeResult.success({}))
                     .set_entry("done").build(runtime=Runtime()))
            audit = HarnessAudit(FileHarnessAuditStore(directory))
            node = subagent_node({"agent": "child"}, resolver=lambda name, memory: child)
            context = AgentNodeContext(run_id="parent", node_id="delegate",
                                       execution_id="first", state_version=0,
                                       input={}, state={}, audit=audit)
            self.assertEqual(node(context).status.value, "SUCCEEDED")
            self.assertEqual(calls, [True])

    def test_budgeted_subagent_requires_durable_result_store(self) -> None:
        config = {
            "name": "parent", "entry": "delegate",
            "harness": {"max_model_calls": 1, "max_tool_calls": 1},
            "nodes": [{"id": "delegate", "type": "subagent",
                       "params": {"agent": "child"}}],
        }
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            child = (AgentBuilder("child")
                     .add_node("done", lambda ctx: calls.append(True) or NodeResult.success({}))
                     .set_entry("done").build(runtime=Runtime()))
            parent = build_agent_from_config(
                config, runtime=Runtime(),
                audit=HarnessAudit(FileHarnessAuditStore(directory)),
                agent_resolver=lambda name, memory=None: child)
            result = parent.run({})
            self.assertEqual((result.status, result.reason_code),
                             (RunStatus.BLOCKED, "SUBAGENT_PERSISTENCE_REQUIRED"))
            self.assertEqual(calls, [])

    def test_subagent_reuses_result_after_completion_audit_failure(self) -> None:
        from agent_core import HarnessAuditError

        class FailOnceAudit(HarnessAudit):
            failed = False
            def record(self, run_id, event_type, status, **kwargs):
                if event_type == "subagent.completed" and not self.failed:
                    self.failed = True
                    raise HarnessAuditError("AUDIT_WRITE_FAILED")
                return super().record(run_id, event_type, status, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            calls = []
            child = (AgentBuilder("child")
                     .add_node("done", lambda ctx: calls.append(True) or NodeResult.success({}))
                     .set_entry("done")
                     .build(runtime=Runtime(store=RunStore(Path(directory) / "runs"))))
            audit_path = Path(directory) / "audit"
            audit = FailOnceAudit(FileHarnessAuditStore(audit_path))
            node = subagent_node({"agent": "child"}, resolver=lambda name, memory: child)
            context = AgentNodeContext(run_id="parent", node_id="delegate",
                                       execution_id="first", state_version=0,
                                       input={}, state={}, audit=audit)
            self.assertEqual(node(context).reason_code, "AUDIT_WRITE_FAILED")
            recovered = AgentNodeContext(run_id="parent", node_id="delegate",
                                         execution_id="replay", state_version=0,
                                         input={}, state={},
                                         audit=HarnessAudit(FileHarnessAuditStore(audit_path)))
            self.assertEqual(node(recovered).status.value, "SUCCEEDED")
            self.assertEqual(calls, [True])

    def test_subagent_propagates_blocked_and_missing(self) -> None:
        blocked_delegate = (
            AgentBuilder("blocked-delegate")
            .add_node("gate", lambda ctx: NodeResult.blocked("DELEGATE_GATE"))
            .set_entry("gate")
            .build(runtime=Runtime())
        )
        runtime = Runtime()
        config = {
            "name": "caller",
            "entry": "call",
            "nodes": [
                {"id": "call", "type": "subagent",
                 "params": {"agent": "blocked-delegate", "input": {}}},
            ],
        }
        agent = build_agent_from_config(
            config, runtime=runtime,
            agent_resolver=lambda name, memory=None: blocked_delegate,
        )
        result = agent.run({})
        self.assertEqual(result.status, RunStatus.BLOCKED)
        self.assertEqual(result.reason_code, "DELEGATE_GATE")

        missing = build_agent_from_config(config, runtime=runtime, agent_resolver=lambda name, memory=None: None)
        result = missing.run({})
        self.assertEqual(result.status, RunStatus.BLOCKED)
        self.assertEqual(result.reason_code, "SUBAGENT_NOT_FOUND")

    def test_subagent_requires_resolver_at_assembly(self) -> None:
        config = {
            "name": "caller",
            "entry": "call",
            "nodes": [{"id": "call", "type": "subagent", "params": {"agent": "any"}}],
        }
        with self.assertRaises(ValueError):
            build_agent_from_config(config, runtime=Runtime())

    def test_llm_node_replies_and_fails_closed(self) -> None:
        class FakeModel:
            def __init__(self, error=None):
                self.calls = []
                self.error = error

            def complete(self, messages, *, tools=None, context=None):
                self.calls.append(messages)
                if self.error:
                    raise self.error
                return {"content": f"回答:{messages[-1]['content']}"}

        from agent_core.models import ModelRequestError

        config = {
            "name": "asker",
            "entry": "ask",
            "nodes": [
                {"id": "ask", "type": "llm",
                 "params": {"system": "简洁回答", "prompt": "问题:{q}", "into": "answer"}},
            ],
        }

        # 未绑定模型 → BLOCKED,不触网
        agent = build_agent_from_config(config, runtime=Runtime())
        result = agent.run({"q": "你好"})
        self.assertEqual(result.status, RunStatus.BLOCKED)
        self.assertEqual(result.reason_code, "MODEL_NOT_CONFIGURED")

        # 正常回复写进状态
        model = FakeModel()
        agent = build_agent_from_config(config, runtime=Runtime(), model=model)
        result = agent.run({"q": "你好"})
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.state["answer"], "回答:问题:你好")
        self.assertEqual(model.calls[0][0], {"role": "system", "content": "简洁回答"})

        # 传输失败 → FAILED 带原因码
        agent = build_agent_from_config(
            config, runtime=Runtime(),
            model=FakeModel(error=ModelRequestError("MODEL_HTTP_ERROR", "http 500")),
        )
        result = agent.run({"q": "你好"})
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.reason_code, "MODEL_HTTP_ERROR")

        # 模板缺键 → FAILED
        result = build_agent_from_config(config, runtime=Runtime(), model=FakeModel()).run({})
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.reason_code, "PROMPT_KEY_MISSING")

    def test_llm_retry_config_retries_transient_nonstream_failure(self) -> None:
        from agent_core import HarnessAudit, InMemoryHarnessAuditStore
        from agent_core.models import ModelRequestError

        class Model:
            calls = 0

            def complete(self, messages):
                self.calls += 1
                if self.calls == 1:
                    raise ModelRequestError("MODEL_TIMEOUT", "timeout")
                return {"content": "ok"}

        config = {"name": "retry", "entry": "ask", "nodes": [{
            "id": "ask", "type": "llm", "params": {
                "prompt": "hello", "model_max_retries": 1,
            },
        }]}
        model = Model()
        audit = HarnessAudit(InMemoryHarnessAuditStore())
        result = build_agent_from_config(config, runtime=Runtime(), model=model,
                                         audit=audit).run({}, run_id="llm-retry")
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.state["reply"], "ok")
        self.assertEqual(model.calls, 2)
        self.assertEqual([event.event_type for event in audit.events("llm-retry")], [
            "model.requested", "model.attempt_failed", "model.attempt_completed",
            "model.completed",
        ])
        for value in (-1, 6, True):
            config["nodes"][0]["params"]["model_max_retries"] = value
            with self.assertRaises(ValueError):
                build_agent_from_config(config, runtime=Runtime(), model=model)

    def test_llm_stream_retries_only_before_first_delta(self) -> None:
        from agent_core import HarnessAudit, InMemoryHarnessAuditStore
        from agent_core.models import ModelRequestError

        class Model:
            def __init__(self, partial: bool):
                self.partial = partial
                self.calls = 0

            def complete_stream(self, messages, on_delta):
                self.calls += 1
                if self.calls == 1:
                    if self.partial:
                        on_delta("partial")
                    raise ModelRequestError("MODEL_TIMEOUT", "timeout")
                on_delta("done")
                return {"content": "done"}

        config = {"name": "stream-retry", "entry": "ask", "nodes": [{
            "id": "ask", "type": "llm", "params": {
                "prompt": "hello", "model_max_retries": 1,
            },
        }]}
        for partial, expected_status, expected_calls in (
                (False, RunStatus.COMPLETED, 2), (True, RunStatus.FAILED, 1)):
            model = Model(partial)
            audit = HarnessAudit(InMemoryHarnessAuditStore())
            result = build_agent_from_config(config, runtime=Runtime(), model=model,
                                             audit=audit).run({})
            self.assertEqual(result.status, expected_status)
            self.assertEqual(model.calls, expected_calls)
            failure = next(event for event in audit.events(result.run_id)
                           if event.event_type == "model.attempt_failed")
            self.assertEqual(failure.summary["retry"], not partial)
            if partial:
                self.assertEqual(result.reason_code, "MODEL_TIMEOUT")

    def test_tool_node_capabilities_must_be_declared(self) -> None:
        registry = ToolRegistry()
        registry.register(
            Tool("guarded", lambda args: {"ok": True}, required_capabilities=frozenset({"restricted"}))
        )
        config = {
            "name": "caller",
            "entry": "call",
            "nodes": [{"id": "call", "type": "tool", "params": {"name": "guarded", "arguments": {}}}],
        }
        # 未声明能力 → BLOCKED,工具未被调用
        agent = build_agent_from_config(config, runtime=Runtime(), tools=registry)
        result = agent.run({})
        self.assertEqual(result.status, RunStatus.BLOCKED)
        self.assertEqual(result.reason_code, "CAPABILITY_DENIED")
        # 显式声明 → 成功
        config["nodes"][0]["params"]["capabilities"] = ["restricted"]
        agent = build_agent_from_config(config, runtime=Runtime(), tools=registry)
        result = agent.run({})
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.state["guarded"], {"ok": True})
        # 声明不覆盖所需能力 → 仍拒绝
        config["nodes"][0]["params"]["capabilities"] = ["other"]
        agent = build_agent_from_config(config, runtime=Runtime(), tools=registry)
        result = agent.run({})
        self.assertEqual(result.reason_code, "CAPABILITY_DENIED")
        # 非法能力参数 → 装配期拒绝
        config["nodes"][0]["params"]["capabilities"] = "restricted"
        with self.assertRaises(ValueError):
            build_agent_from_config(config, runtime=Runtime(), tools=registry)

    def test_llm_history_key_enables_multi_turn(self) -> None:
        class RecordingModel:
            def __init__(self):
                self.turns = []

            def complete(self, messages):
                self.turns.append([dict(m) for m in messages])
                return {"content": f"回复{len(self.turns)}"}

        config = {
            "name": "chat",
            "entry": "ask",
            "nodes": [{"id": "ask", "type": "llm",
                       "params": {"prompt": "{q}", "into": "answer", "history_key": "messages"}}],
        }
        from agent_core import InMemoryMemory

        memory = InMemoryMemory()
        model = RecordingModel()
        agent = build_agent_from_config(config, runtime=Runtime(), model=model, memory=memory)

        first = agent.run({"q": "第一问"})
        self.assertEqual(first.status, RunStatus.COMPLETED)
        self.assertEqual(model.turns[0], [{"role": "user", "content": "第一问"}])

        second = agent.run({"q": "第二问"})
        self.assertEqual(second.status, RunStatus.COMPLETED)
        self.assertEqual(
            model.turns[1],
            [
                {"role": "user", "content": "第一问"},
                {"role": "assistant", "content": "回复1"},
                {"role": "user", "content": "第二问"},
            ],
        )
        # 会话快照可见完整历史
        self.assertEqual(len(memory.snapshot()["messages"]), 4)

    def test_llm_context_limit_trims_history_without_rewriting_memory(self) -> None:
        from agent_core import InMemoryMemory

        class Model:
            def __init__(self):
                self.messages = None

            def complete(self, messages):
                self.messages = messages
                return {"content": "answer"}

        prior = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old answer"},
        ]
        memory = InMemoryMemory({"messages": prior})
        model = Model()
        config = {"name": "chat", "entry": "ask", "nodes": [{
            "id": "ask", "type": "llm", "params": {
                "system": "rules", "prompt": "{q}", "history_key": "messages",
                "max_context_messages": 2,
            },
        }]}
        agent = build_agent_from_config(config, runtime=Runtime(), model=model, memory=memory)
        result = agent.run({"q": "now"})
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(model.messages, [
            {"role": "system", "content": "rules"}, {"role": "user", "content": "now"},
        ])
        self.assertEqual(memory.read("messages")[:2], prior)

    def test_llm_context_limit_failure_preserves_history(self) -> None:
        from agent_core import InMemoryMemory

        memory = InMemoryMemory({"messages": [{"role": "user", "content": "old"}]})
        config = {"name": "chat", "entry": "ask", "nodes": [{
            "id": "ask", "type": "llm", "params": {
                "system": "rules", "prompt": "{q}", "history_key": "messages",
                "max_context_messages": 1,
            },
        }]}
        agent = build_agent_from_config(config, runtime=Runtime(), model=_EchoLLM(), memory=memory)
        result = agent.run({"q": "now"})
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.reason_code, "CONTEXT_LIMIT_EXCEEDED")
        self.assertEqual(memory.read("messages"), [{"role": "user", "content": "old"}])

    def test_llm_records_real_usage_in_harness_audit(self) -> None:
        from agent_core import HarnessAudit, InMemoryHarnessAuditStore

        class Model:
            def complete(self, messages):
                return {"content": "answer", "usage": {"prompt_tokens": 3}}

        audit = HarnessAudit(InMemoryHarnessAuditStore())
        config = {"name": "chat", "entry": "ask", "nodes": [{
            "id": "ask", "type": "llm", "params": {"prompt": "{q}"},
        }]}
        agent = build_agent_from_config(config, runtime=Runtime(), model=Model(), audit=audit)
        result = agent.run({"q": "hello"}, run_id="llm-usage")
        self.assertEqual(result.status, RunStatus.COMPLETED)
        events = audit.events("llm-usage")
        self.assertEqual([event.event_type for event in events], ["model.requested", "model.completed"])
        self.assertEqual(events[-1].summary["usage"], {"prompt_tokens": 3})

    def test_llm_history_requires_session_memory(self) -> None:
        config = {
            "name": "chat",
            "entry": "ask",
            "nodes": [{"id": "ask", "type": "llm",
                       "params": {"prompt": "{q}", "history_key": "messages"}}],
        }
        agent = build_agent_from_config(config, runtime=Runtime(), model=_EchoLLM())
        result = agent.run({"q": "x"})
        self.assertEqual(result.status, RunStatus.BLOCKED)
        self.assertEqual(result.reason_code, "SESSION_MEMORY_REQUIRED")

    def test_llm_history_invalid_fails_closed(self) -> None:
        from agent_core import InMemoryMemory

        memory = InMemoryMemory({"messages": [{"role": "user"}]})
        config = {
            "name": "chat",
            "entry": "ask",
            "nodes": [{"id": "ask", "type": "llm",
                       "params": {"prompt": "{q}", "history_key": "messages"}}],
        }
        agent = build_agent_from_config(config, runtime=Runtime(), model=_EchoLLM(), memory=memory)
        result = agent.run({"q": "x"})
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.reason_code, "HISTORY_INVALID")

    def test_example_config_file_runs_end_to_end(self) -> None:
        config = json.loads(Path("examples/support_agent.json").read_text(encoding="utf-8"))
        agent = build_agent_from_config(config, runtime=self.runtime, tools=demo_tools())
        result = agent.run({"task": "无法登录;密码重置邮件未收到"})
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.state["step_count"], 2)
        self.assertIn("已拆解为 2 个步骤", result.state["summary"])


class ConfigValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = Runtime()

    def test_rejects_invalid_configs(self) -> None:
        base = dict(WORKBENCH_CONFIG)
        cases = [
            ("no name", {**base, "name": "  "}),
            ("no nodes", {**base, "nodes": []}),
            ("unknown type", {**base, "nodes": [{**base["nodes"][0], "type": "magic"}]}),
            ("duplicate id", {**base, "nodes": [base["nodes"][0], base["nodes"][0]]}),
            ("entry unknown", {**base, "entry": "ghost"}),
            ("edge unknown target", {**base, "edges": [{"source": "intake", "target": "ghost"}]}),
            ("bad params", {**base, "nodes": [
                {"id": "intake", "type": "validate_input", "params": {"field": ""}},
            ]}),
            ("nodes not list", {**base, "nodes": "intake"}),
        ]
        for label, config in cases:
            with self.assertRaises(ValueError, msg=label):
                build_agent_from_config(config, runtime=self.runtime)

    def test_factory_param_validation_at_assembly_time(self) -> None:
        with self.assertRaises(ValueError):
            NODE_FACTORIES["split_steps"]({"field": "task", "max_length": -1})
        with self.assertRaises(ValueError):
            NODE_FACTORIES["template"]({"updates": {}})
        with self.assertRaises(ValueError):
            NODE_FACTORIES["route_by_value"]({"field": "lane", "routes": {}})

    def test_register_node_factory_and_duplicate_guard(self) -> None:
        calls: list[str] = []

        def upper_node(params):
            field = params["field"]

            def execute(ctx):
                calls.append(ctx.input[field])
                return NodeResult.success({field: str(ctx.input[field]).upper()})

            return execute

        try:
            register_node_factory("upper_case", upper_node)
            with self.assertRaises(ValueError):
                register_node_factory("upper_case", upper_node)
            config = {
                "name": "custom-agent",
                "entry": "upper",
                "nodes": [{"id": "upper", "type": "upper_case", "params": {"field": "word"}}],
            }
            agent = build_agent_from_config(config, runtime=self.runtime)
            result = agent.run({"word": "hello"})
            self.assertEqual(result.status, RunStatus.COMPLETED)
            self.assertEqual(result.state["word"], "HELLO")
            self.assertEqual(calls, ["hello"])
        finally:
            NODE_FACTORIES.pop("upper_case", None)


class _EchoLLM:
    def complete(self, messages):
        return {"content": f"回复:{messages[-1]['content']}"}


class AgentLoopTests(unittest.TestCase):
    LOOP_CONFIG = {
        "name": "tool-agent",
        "entry": "loop",
        "nodes": [{
            "id": "loop", "type": "agent_loop",
            "params": {
                "prompt": "问题:{q}",
                "tools": [{"name": "echo", "description": "回显输入", "parameters": {"value": "string"}}],
                "capabilities": ["basic"],
                "max_rounds": 3,
                "into": "answer",
            },
        }],
    }

    def setUp(self) -> None:
        self.registry = ToolRegistry()
        self.registry.register(Tool("echo", lambda args: {"echo": args["value"]}))
        self.runtime = Runtime()

    def build(self, model, config=None):
        return build_agent_from_config(config or self.LOOP_CONFIG, runtime=self.runtime,
                                       tools=self.registry, model=model)

    def test_tool_retry_config_counts_attempts_and_validates_limits(self) -> None:
        class Timeout(Exception):
            reason_code = "TOOL_TIMEOUT"

        calls = []

        def read(arguments):
            calls.append(True)
            if len(calls) == 1:
                raise Timeout("timeout")
            return {"value": "ok"}

        self.registry.register(Tool("read", read, read_only=True))
        config = {"name": "retry-loop", "entry": "loop", "nodes": [{
            "id": "loop", "type": "agent_loop", "params": {
                "prompt": "read", "tools": [{"name": "read"}],
                "max_rounds": 2, "max_tool_calls": 2, "tool_max_retries": 1,
            },
        }]}

        class Model:
            def complete_with_tools(self, messages, schemas):
                if any(message.get("role") == "tool" for message in messages):
                    return {"content": "done"}
                return {"tool_calls": [{"id": "call-1", "name": "read", "arguments": {}}]}

        result = self.build(Model(), config).run({})
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(calls, [True, True])
        for value in (-1, 6, True):
            config["nodes"][0]["params"]["tool_max_retries"] = value
            with self.assertRaises(ValueError):
                self.build(Model(), config)

    def test_loop_calls_tool_then_answers_with_audit(self) -> None:
        class Model:
            def __init__(self):
                self.calls = []

            def complete_with_tools(self, messages, tool_schemas):
                self.calls.append(([dict(m) for m in messages], [dict(s) for s in tool_schemas]))
                if len(self.calls) == 1:
                    return {"tool_calls": [{"id": "call-1", "name": "echo", "arguments": {"value": "hi"}}]}
                return {"content": "工具说:hi"}

        model = Model()
        result = self.build(model).run({"q": "测试"})
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.state["answer"], "工具说:hi")
        self.assertEqual(result.state["answer_rounds"],
                         [{"tool": "echo", "arguments": {"value": "hi"}, "output": {"echo": "hi"}}])
        # 第二轮的 messages 含 assistant 工具调用与 tool 结果
        second_messages = model.calls[1][0]
        self.assertEqual(second_messages[-2]["role"], "assistant")
        self.assertEqual(second_messages[-2]["tool_calls"][0]["function"]["name"], "echo")
        self.assertEqual(second_messages[-1]["role"], "tool")
        self.assertEqual(second_messages[-1]["tool_call_id"], "call-1")
        # 模型看到声明的 schema
        self.assertEqual(model.calls[0][1][0]["name"], "echo")

    def test_capability_must_be_declared(self) -> None:
        self.registry = ToolRegistry()
        self.registry.register(Tool("echo", lambda args: {"echo": args["value"]},
                                    required_capabilities=frozenset({"secret"})))

        class Model:
            def complete_with_tools(self, messages, schemas):
                return {"tool_calls": [{"id": "c1", "name": "echo", "arguments": {"value": "x"}}]}

        result = self.build(Model()).run({"q": "x"})
        self.assertEqual(result.status, RunStatus.BLOCKED)
        self.assertEqual(result.reason_code, "CAPABILITY_DENIED")

    def test_unknown_tool_blocks(self) -> None:
        class Model:
            def complete_with_tools(self, messages, schemas):
                return {"tool_calls": [{"id": "c1", "name": "ghost", "arguments": {}}]}

        result = self.build(Model()).run({"q": "x"})
        self.assertEqual(result.status, RunStatus.BLOCKED)
        self.assertEqual(result.reason_code, "TOOL_NOT_FOUND")

    def test_tool_failure_propagates_reason_code(self) -> None:
        class Boom(Exception):
            reason_code = "ECHO_EXPLODED"

        self.registry = ToolRegistry()
        self.registry.register(Tool("echo", lambda args: (_ for _ in ()).throw(Boom())))

        class Model:
            def complete_with_tools(self, messages, schemas):
                return {"tool_calls": [{"id": "c1", "name": "echo", "arguments": {}}]}

        result = self.build(Model()).run({"q": "x"})
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.reason_code, "ECHO_EXPLODED")

    def test_max_rounds_exhaustion_fails(self) -> None:
        class Model:
            def complete_with_tools(self, messages, schemas):
                return {"tool_calls": [{"id": "call-again", "name": "echo", "arguments": {"value": "x"}}]}

        result = self.build(Model()).run({"q": "x"})
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.reason_code, "MAX_TOOL_ROUNDS")

    def test_model_without_tool_support_blocks(self) -> None:
        class PlainModel:
            def complete(self, messages):
                return {"content": "no tools here"}

        result = self.build(PlainModel()).run({"q": "x"})
        self.assertEqual(result.status, RunStatus.BLOCKED)
        self.assertEqual(result.reason_code, "MODEL_TOOLS_UNSUPPORTED")

    def test_model_error_maps_reason_code(self) -> None:
        from agent_core.models import ModelRequestError

        class Model:
            def complete_with_tools(self, messages, schemas):
                raise ModelRequestError("MODEL_TIMEOUT", "timed out")

        result = self.build(Model()).run({"q": "x"})
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.reason_code, "MODEL_TIMEOUT")

    def test_assembly_validates_params(self) -> None:
        for bad in (
            {"tools": []},
            {"tools": "echo"},
            {"tools": [{"name": ""}]},
            {"tools": [{"name": "echo", "parameters": "x"}]},
            {"tools": [{"name": "echo"}, {"name": "echo"}]},
            {"tools": [{"name": "echo", "parameters": {"type": "array"}}]},
            {"tools": [{"name": "echo", "parameters": {"required": "value"}}]},
            {"capabilities": "basic"},
            {"max_rounds": 0},
            {"max_rounds": True},
        ):
            config = {
                "name": "bad", "entry": "loop",
                "nodes": [{"id": "loop", "type": "agent_loop",
                           "params": {"prompt": "p", **bad}}],
            }
            with self.assertRaises(ValueError, msg=bad):
                build_agent_from_config(config, runtime=Runtime(), tools=self.registry)


if __name__ == "__main__":
    unittest.main()
