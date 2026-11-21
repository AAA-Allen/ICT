"""Cursor retention, SSE reconnect and process-local reset contracts."""

from http.client import HTTPConnection
import json
import unittest
from unittest.mock import patch

from agent_core.streaming import TokenBus
from tests.test_server import ServerTestBase


class CursorTests(unittest.TestCase):
    def test_absolute_history_continues_after_eviction(self):
        bus = TokenBus(max_history=2)
        bus.open('r')
        for text in 'ab':
            bus.publish('r', 'one', text)
        cursor = bus.read('r')['events'][-1]['cursor']
        for text in 'cd':
            bus.publish('r', 'two', text)
        window = bus.read('r', after=cursor)
        self.assertIsNone(window['gap'])
        self.assertEqual([e['text'] for e in window['events']], ['c', 'd'])
        self.assertEqual(bus.history('r', after=2)[0], [('two', 'c'), ('two', 'd')])

    def test_late_subscriber_and_expired_cursor_get_gap(self):
        bus = TokenBus(max_history=2)
        bus.open('r')
        bus.publish('r', 'n', 'a')
        old = bus.read('r')['events'][0]['cursor']
        for text in 'bcd':
            bus.publish('r', 'n', text)
        for cursor in (None, old):
            window = bus.read('r', after=cursor)
            self.assertEqual(window['gap'], 'HISTORY_TRUNCATED')
            self.assertEqual([e['text'] for e in window['events']], ['c', 'd'])
            self.assertIsNone(bus.read('r', after=window['cursor'])['gap'])

    def test_node_close_reopen_preserves_identity_and_progress(self):
        bus = TokenBus()
        bus.open('r')
        bus.publish('r', 'first', 'a')
        cursor = bus.read('r')['events'][-1]['cursor']
        bus.close('r')
        bus.open('r')
        bus.publish('r', 'second', 'b')
        window = bus.read('r', after=cursor)
        self.assertIsNone(window['gap'])
        self.assertEqual(window['events'][0]['node_id'], 'second')
        self.assertEqual(window['events'][0]['cursor'].split(':')[0], cursor.split(':')[0])

    def test_missing_memory_and_recreated_stream_do_not_claim_replay(self):
        bus = TokenBus()
        bus.open('r')
        bus.publish('r', 'n', 'old')
        cursor = bus.read('r')['events'][0]['cursor']
        bus.forget('r')
        self.assertEqual(bus.read('r', after=cursor)['gap'], 'HISTORY_UNAVAILABLE')
        bus.open('r')
        bus.publish('r', 'n', 'new')
        self.assertEqual(bus.read('r', after=cursor)['gap'], 'STREAM_RESET')

    def test_future_and_invalid_cursor(self):
        bus = TokenBus()
        bus.open('r')
        cursor = bus.read('r')['cursor'].split(':')[0] + ':99'
        self.assertEqual(bus.read('r', after=cursor)['gap'], 'CURSOR_AHEAD')
        for value in ('bad', 'a' * 32 + ':-1', 'a' * 32 + ':' + '9' * 100):
            with self.assertRaises(ValueError):
                bus.read('r', after=value)


class CursorHttpTests(ServerTestBase):
    def setUp(self):
        super().setUp()
        self.bus = TokenBus(max_history=2)
        patcher = patch('agent_core.streaming._BUS', self.bus)
        patcher.start()
        self.addCleanup(patcher.stop)
        _, payload = self.request('POST', '/api/runs', json.dumps({'agent': 'workbench', 'input': {'task': 'cursor'}}))
        self.run_id = payload['run']['run_id']
        self.path = f'/api/runs/{self.run_id}/events/stream'

    def frames(self, headers=None, suffix=''):
        status, raw = self.request('GET', self.path + suffix, extra_headers=headers)
        self.assertEqual(status, 200)
        frames = []
        for block in raw.decode().split('\n\n'):
            fields = dict(line.split(': ', 1) for line in block.splitlines() if ': ' in line)
            if 'data' in fields:
                fields['data'] = json.loads(fields['data'])
                frames.append(fields)
        return frames

    def test_header_resume_overrides_initial_query_without_duplicate_tokens(self):
        self.bus.open(self.run_id)
        for text in 'ab':
            self.bus.publish(self.run_id, 'ask', text)
        first = [f for f in self.frames() if f['event'] == 'token']
        self.assertEqual([f['data']['text'] for f in first], ['a', 'b'])
        self.assertEqual(first[0]['id'], first[0]['data']['cursor'])
        self.bus.publish(self.run_id, 'ask', 'c')
        resumed = self.frames({'Last-Event-ID': first[-1]['id']}, '?cursor=' + first[0]['id'])
        self.assertEqual([f['data']['text'] for f in resumed if f['event'] == 'token'], ['c'])
        self.assertEqual(resumed[-1]['event'], 'done')

    def test_gap_is_sent_before_retained_tokens_and_reset_clears_sse_id(self):
        self.bus.open(self.run_id)
        for text in 'abc':
            self.bus.publish(self.run_id, 'ask', text)
        frames = [f for f in self.frames() if f['event'] in ('gap', 'token')]
        self.assertEqual(frames[0]['data']['reason_code'], 'HISTORY_TRUNCATED')
        self.assertEqual([f['data']['text'] for f in frames[1:]], ['b', 'c'])
        cursor = frames[-1]['id']
        self.bus.forget(self.run_id)
        frames = self.frames({'Last-Event-ID': cursor})
        gap = next(f for f in frames if f['event'] == 'gap')
        self.assertEqual(gap['data']['reason_code'], 'HISTORY_UNAVAILABLE')
        self.assertEqual(gap['id'], '')

    def test_invalid_cursor_returns_json_before_stream_headers(self):
        status, payload = self.request('GET', self.path, extra_headers={'Last-Event-ID': 'invalid'})
        self.assertEqual(status, 400)
        self.assertEqual(payload['error']['code'], 'INVALID_STREAM_CURSOR')

    def test_terminal_status_drains_token_published_after_first_snapshot(self):
        self.bus.open(self.run_id)
        self.bus.publish(self.run_id, 'ask', 'a')
        original = self.bus.read
        calls = 0

        def racing_read(*args, **kwargs):
            nonlocal calls
            window = original(*args, **kwargs)
            calls += 1
            if calls == 2:  # The first read only validates the cursor before headers.
                self.bus.publish(self.run_id, 'ask', 'last')
            return window

        with patch.object(self.bus, 'read', side_effect=racing_read):
            frames = self.frames()
        self.assertEqual([f['data']['text'] for f in frames if f['event'] == 'token'], ['a', 'last'])

    def test_approval_pause_and_resume_stream_only_new_node_tokens(self):
        from agent_core import Tool, ToolRegistry
        from agent_core.nodes import build_agent_from_config
        from tests.test_streaming import StreamingModel

        effects = []
        tools = ToolRegistry()
        tools.register(Tool('write', lambda args: effects.append(True) or {},
                            side_effect=True, requires_approval=True))
        agent = build_agent_from_config({
            'name': 'cursor-approval', 'entry': 'first',
            'nodes': [
                {'id': 'first', 'type': 'llm', 'params': {'prompt': 'before', 'into': 'before'}},
                {'id': 'write', 'type': 'tool', 'params': {'name': 'write'}},
                {'id': 'second', 'type': 'llm', 'params': {'prompt': 'after', 'into': 'after'}},
            ], 'edges': [{'source': 'first', 'target': 'write'}, {'source': 'write', 'target': 'second'}],
        }, runtime=self.server.runtime, tools=tools, model=StreamingModel(('x',)),
            audit=self.server.harness_audit, pending_invocations=self.server.pending_invocations,
            approval_decisions=self.server.approval_decisions)
        self.server.registry.register(agent)
        _, payload = self.post_json('/api/runs', {'agent': 'cursor-approval', 'input': {}})
        self.run_id = payload['run']['run_id']
        self.path = f'/api/runs/{self.run_id}/events/stream'
        first = self.frames()
        self.assertEqual(first[-1]['data']['status'], 'WAITING_APPROVAL')
        cursor = next(f['id'] for f in first if f['event'] == 'token')
        self.assertEqual(effects, [])
        self.post_json(f'/api/runs/{self.run_id}/approval-decision', {
            'request_id': payload['run']['pending_approval']['request_id'], 'decision': 'APPROVED'})
        self.post_json(f'/api/runs/{self.run_id}/resume', {})
        resumed = self.frames({'Last-Event-ID': cursor})
        self.assertEqual([f['data']['node_id'] for f in resumed if f['event'] == 'token'], ['second'])
        self.assertFalse(any(f['event'] == 'gap' for f in resumed))
        self.assertEqual(effects, [True])
        self.assertEqual(resumed[-1]['data']['status'], 'COMPLETED')

    def test_open_http_subscriber_reads_new_token_after_eviction(self):
        run_id = 'active-cursor-test'
        self.server.track_active(run_id)
        self.bus.open(run_id)
        for text in 'ab':
            self.bus.publish(run_id, 'ask', text)
        connection = HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            connection.request('GET', f'/api/runs/{run_id}/events/stream')
            response = connection.getresponse()

            def token():
                while True:
                    line = response.readline().decode()
                    if line.startswith('data: '):
                        item = json.loads(line[6:])
                        if 'text' in item:
                            return item['text']

            self.assertEqual([token(), token()], ['a', 'b'])
            self.bus.publish(run_id, 'ask', 'c')
            self.assertEqual(token(), 'c')
        finally:
            self.server.release_active(run_id)
            connection.close()
