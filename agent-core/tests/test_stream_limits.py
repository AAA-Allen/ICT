"""Retention and response limits use deterministic clocks and local HTTP."""

import json
import unittest
from unittest.mock import patch

from agent_core import AgentBuilder, NodeCallPolicy, NodeResult, Runtime, Tool, ToolRegistry
from agent_core.models import OpenAICompatibleModel, ModelRequestError
from agent_core.streaming import TokenBus, StreamLimitError
from tests.test_models import FakeOpenAIServer


class RetentionTests(unittest.TestCase):
    def bus(self, **kwargs):
        return TokenBus(max_event_bytes=8, max_run_bytes=16, max_total_bytes=32, **kwargs)

    def test_expiry_reclaims_closed_but_not_active_and_reports_gap(self):
        now = [0]
        bus = self.bus(retention_seconds=5, clock=lambda: now[0])
        bus.open('done')
        bus.publish('done', 'n', 'a')
        cursor = bus.read('done')['events'][0]['cursor']
        bus.close('done')
        bus.open('active')
        now[0] = 6
        self.assertEqual(bus.stats()['runs'], 1)
        self.assertEqual(bus.stats()['active_runs'], 1)
        self.assertEqual(bus.read('done', after=cursor)['gap'], 'HISTORY_UNAVAILABLE')

    def test_count_pressure_reclaims_finished_first_and_refuses_active(self):
        bus = self.bus(max_runs=2)
        bus.open('active')
        bus.open('done')
        bus.close('done')
        bus.open('new')
        self.assertEqual(bus.stats()['runs'], 2)
        self.assertFalse(bus.read('done')['closed'])
        with self.assertRaises(StreamLimitError) as caught:
            bus.open('overflow')
        self.assertEqual(caught.exception.reason_code, 'STREAM_CAPACITY_EXCEEDED')
        self.assertEqual(bus.stats()['active_runs'], 2)

    def test_payload_byte_window_counts_utf8_and_preserves_sequence(self):
        bus = TokenBus(max_event_bytes=8, max_run_bytes=8, max_total_bytes=16)
        bus.open('r')
        bus.publish('r', 'n', '你好')  # 7 bytes including node identity
        cursor = bus.read('r')['events'][0]['cursor']
        bus.publish('r', 'n', '世界')
        self.assertEqual(bus.stats()['payload_bytes'], 7)
        self.assertEqual(bus.history('r', after=1)[0], [('n', '世界')])
        self.assertEqual(bus.read('r')['gap'], 'HISTORY_TRUNCATED')
        self.assertIsNone(bus.read('r', after=cursor)['gap'])
        with self.assertRaises(StreamLimitError):
            bus.publish('r', 'n', 'long' * 3)
        self.assertEqual(bus.stats()['payload_bytes'], 7)

    def test_global_byte_pressure_does_not_erase_active_stream(self):
        bus = TokenBus(max_event_bytes=4, max_run_bytes=4, max_total_bytes=4)
        bus.open('first')
        bus.publish('first', 'n', 'abc')
        cursor = bus.read('first')['events'][0]['cursor']
        bus.open('second')
        with self.assertRaises(StreamLimitError):
            bus.publish('second', 'n', 'x')
        self.assertEqual(bus.read('first')['events'][0]['cursor'], cursor)
        bus.close('first')
        bus.publish('second', 'n', 'x')
        self.assertEqual(bus.stats()['payload_bytes'], 2)

    def test_runtime_keeps_stream_between_nodes_and_releases_on_exit(self):
        now = [0]
        bus = self.bus(retention_seconds=1, clock=lambda: now[0])

        class Model:
            def complete_stream(self, messages, on_delta):
                on_delta('x')
                return {'content': 'x'}

        def ask(ctx):
            ctx.calls.model([{'role': 'user', 'content': 'hi'}])
            return NodeResult.success({})

        def between(ctx):
            now[0] = 100
            self.assertEqual(bus.stats()['active_runs'], 1)
            self.assertEqual(bus.stats()['events'], 1)
            return NodeResult.success({})

        agent = (AgentBuilder('retention', model=Model())
                 .add_node('first', ask, calls=NodeCallPolicy()).add_node('between', between)
                 .add_node('second', ask, calls=NodeCallPolicy()).set_entry('first')
                 .add_edge('first', 'between').add_edge('between', 'second').build())
        with patch('agent_core.streaming._BUS', bus):
            result = agent.run({})
        self.assertEqual(result.status.value, 'COMPLETED')
        self.assertEqual(bus.stats()['active_runs'], 0)
        self.assertEqual(bus.stats()['events'], 2)
        now[0] = 102
        self.assertEqual(bus.stats()['runs'], 0)

    def test_rejected_replacement_does_not_discard_previous_window(self):
        bus = TokenBus(max_event_bytes=4, max_run_bytes=4, max_total_bytes=6)
        bus.open('one')
        bus.open('two')
        bus.publish('one', 'n', 'x')
        bus.publish('two', 'n', 'yz')
        with self.assertRaises(StreamLimitError):
            bus.publish('one', 'n', 'abc')
        self.assertEqual(bus.history('one')[0], [('n', 'x')])
        self.assertEqual(bus.stats()['payload_bytes'], 5)

    def test_invalid_capacities(self):
        for kwargs in ({'max_runs': True}, {'max_history': 0}, {'retention_seconds': float('nan')},
                       {'max_event_bytes': 100, 'max_run_bytes': 10}):
            with self.assertRaises(ValueError):
                TokenBus(**kwargs)

    def test_many_finished_runs_stay_within_caps_and_forget_updates_accounting(self):
        bus = self.bus(max_runs=3)
        for index in range(100):
            run_id = str(index)
            bus.open(run_id)
            bus.publish(run_id, 'n', 'x')
            bus.close(run_id)
            self.assertLessEqual(bus.stats()['runs'], 3)
            self.assertLessEqual(bus.stats()['payload_bytes'], 32)
        bus.forget('99')
        self.assertEqual(bus.stats()['payload_bytes'], 4)

    def test_public_configuration_is_startup_only(self):
        from agent_core.streaming import configure_default_bus, default_bus
        with patch('agent_core.streaming._BUS', None):
            configured = configure_default_bus(max_runs=2)
            self.assertIs(default_bus(), configured)
            configured.open('active')
            with self.assertRaises(ValueError):
                configure_default_bus(max_runs=3)


class ResponseLimitTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOpenAIServer().start()
        self.addCleanup(self.fake.stop)

    def model(self, **kwargs):
        return OpenAICompatibleModel(self.fake.base_url, 'test-only', 'limits', **kwargs)

    def test_plain_and_tool_reply_share_wire_limit(self):
        self.fake.raw = json.dumps({'choices': [{'message': {'content': 'x' * 1000}}]}).encode()
        model = self.model(max_response_bytes=100)
        for method in (lambda: model.complete([{'role': 'user', 'content': 'x'}]),
                       lambda: model.complete_with_tools([{'role': 'user', 'content': 'x'}], [{'name': 'write'}])):
            with self.assertRaises(ModelRequestError) as caught:
                method()
            self.assertEqual(caught.exception.reason_code, 'MODEL_RESPONSE_TOO_LARGE')

    def test_stream_frame_limit_blocks_callback(self):
        self.fake.stream_chunks = ['x' * 200]
        emitted = []
        with self.assertRaises(ModelRequestError) as caught:
            self.model(max_stream_frame_bytes=80).complete_stream(
                [{'role': 'user', 'content': 'x'}], on_delta=emitted.append)
        self.assertEqual(caught.exception.reason_code, 'MODEL_RESPONSE_TOO_LARGE')
        self.assertEqual(emitted, [])

    def test_total_stream_limit_preserves_only_already_delivered_prefix(self):
        self.fake.stream_chunks = ['a' * 20, 'b' * 20, 'c' * 20]
        emitted = []
        with self.assertRaises(ModelRequestError) as caught:
            self.model(max_response_bytes=100).complete_stream(
                [{'role': 'user', 'content': 'x'}], on_delta=emitted.append)
        self.assertEqual(caught.exception.reason_code, 'MODEL_RESPONSE_TOO_LARGE')
        self.assertEqual(emitted, ['a' * 20])

    def test_oversize_tool_reply_stops_before_side_effect(self):
        self.fake.raw = json.dumps({'choices': [{'message': {'tool_calls': [
            {'id': 'one', 'function': {'name': 'write', 'arguments': json.dumps({'text': 'x' * 200})}}]}}]}).encode()
        from agent_core.nodes import build_agent_from_config
        effects = []
        tools = ToolRegistry()
        tools.register(Tool('write', lambda args: effects.append(args) or {}))
        agent = build_agent_from_config({'name': 'bounded', 'entry': 'ask', 'nodes': [
            {'id': 'ask', 'type': 'agent_loop', 'params': {'prompt': 'x', 'tools': [{'name': 'write'}]}}]},
            runtime=Runtime(), model=self.model(max_response_bytes=100), tools=tools)
        result = agent.run({})
        self.assertEqual(result.reason_code, 'MODEL_RESPONSE_TOO_LARGE')
        self.assertEqual(effects, [])

    def test_limits_validate(self):
        for kwargs in ({'max_response_bytes': 0}, {'max_stream_frame_bytes': True}):
            with self.assertRaises(ValueError):
                self.model(**kwargs)

    def test_partial_stream_limit_prevents_following_tool_and_retries(self):
        from agent_core import ModelRetryPolicy
        self.fake.stream_chunks = ['a' * 20, 'b' * 20, 'c' * 20]
        effects = []

        def ask(ctx):
            ctx.calls.model([{'role': 'user', 'content': 'x'}])
            return NodeResult.success({})

        agent = (AgentBuilder('limited-stream', model=self.model(max_response_bytes=100))
                 .add_node('ask', ask, calls=NodeCallPolicy(model_retry=ModelRetryPolicy(max_retries=2)))
                 .add_node('effect', lambda ctx: effects.append(True) or NodeResult.success({}))
                 .set_entry('ask').add_edge('ask', 'effect').build())
        result = agent.run({})
        self.assertEqual(result.reason_code, 'MODEL_RESPONSE_TOO_LARGE')
        self.assertEqual(effects, [])
        self.assertEqual(len(self.fake.requests), 1)
