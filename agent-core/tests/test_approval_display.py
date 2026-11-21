"""Approval explanations are explicit public projections of a bound request."""

import json
from pathlib import Path
import tempfile
import unittest

from agent_core import (Tool, ToolRegistry, HarnessBudget, HarnessLimits, ToolInvocationPipeline,
                        NodeResult, RunStore)
from agent_core.approval_display import clean_display
from examples.reference_agent import build_app
from tests.test_server import ServerTestBase


class ApprovalDisplayTests(unittest.TestCase):
    def request(self, value='review.md'):
        tools = ToolRegistry()
        tools.register(Tool('write', lambda args: self.fail('must not execute'), side_effect=True,
                            requires_approval=True, approval_description='Create one draft without overwriting.',
                            approval_fields=('filename',)))
        pipeline = ToolInvocationPipeline(tools, granted_capabilities=frozenset(), budget=HarnessBudget(HarnessLimits(1, 1)))
        return pipeline.invoke('write', {'filename': value, 'private': 'secret-private-argument'})

    def test_only_declared_fields_survive_node_result_and_persistence(self):
        result = self.request()
        public = NodeResult.waiting_approval(result.output)
        self.assertEqual(public.output['display']['fields'], {'filename': 'review.md'})
        self.assertNotIn('secret-private-argument', json.dumps(public.output))
        for invalid in ({'nested': 'secret'}, 'x' * 201, 123):
            blocked = self.request(invalid)
            self.assertEqual(blocked.reason_code, 'APPROVAL_DISPLAY_INVALID')

    def test_metadata_and_public_shape_are_bounded(self):
        for kwargs in ({'approval_fields': ('filename',)},
                       {'approval_description': 'text', 'approval_fields': ('a', 'a')},
                       {'approval_description': 'x' * 501}):
            with self.assertRaises(ValueError):
                Tool('write', lambda args: {}, side_effect=True, requires_approval=True, **kwargs)
        with self.assertRaises(ValueError):
            clean_display({'description': 'text', 'fields': {'private': {'nested': 'secret'}}})

    def test_legacy_requests_preserve_three_fields_and_ignore_unknown_arguments(self):
        result = NodeResult.waiting_approval({'request_id': 'id', 'tool_name': 'write', 'argument_digest': 'sha',
                                            'arguments': {'private': 'secret'}})
        self.assertEqual(set(result.output), {'request_id', 'tool_name', 'argument_digest'})

    def test_reference_waiting_display_is_durable_and_not_regenerated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs = root / 'docs'
            docs.mkdir()
            (docs / 'note.md').write_text('An evidence line.', encoding='utf-8')
            agent, _, _, _ = build_app(root / 'workspace', docs)
            waiting = agent.run({'task': 'Review notes', 'filename': 'review.md'}).run
            restored = RunStore(root / 'workspace/state/runs').load(waiting.run_id)
            self.assertEqual(restored.pending_approval['display'], waiting.pending_approval['display'])
            self.assertEqual(restored.pending_approval['display']['fields'], {'filename': 'review.md'})
            self.assertNotIn('citations', json.dumps(restored.pending_approval))


class RecoveryExplanationHttpTests(ServerTestBase):
    def test_waiting_approved_and_rejected_recovery_facts(self):
        from agent_core.nodes import build_agent_from_config
        tools = ToolRegistry()
        tools.register(Tool('write', lambda args: {}, side_effect=True, requires_approval=True,
                            approval_description='Create a test draft.', approval_fields=('filename',)))
        agent = build_agent_from_config({'name': 'explain', 'entry': 'write', 'nodes': [
            {'id': 'write', 'type': 'tool', 'params': {'name': 'write',
             'arguments': {'filename': 'test.md', 'private': 'hidden'}}}]},
            runtime=self.server.runtime, tools=tools, audit=self.server.harness_audit,
            pending_invocations=self.server.pending_invocations, approval_decisions=self.server.approval_decisions)
        self.server.registry.register(agent)
        _, payload = self.post_json('/api/runs', {'agent': 'explain', 'input': {}})
        run = payload['run']
        self.assertFalse(run['recovery']['allowed'])
        self.assertEqual(run['recovery']['detail'], 'APPROVAL_PENDING')
        self.assertEqual(run['pending_approval']['display']['fields'], {'filename': 'test.md'})
        self.assertNotIn('hidden', json.dumps(run['pending_approval']))
        self.post_json(f"/api/runs/{run['run_id']}/approval-decision", {
            'request_id': run['pending_approval']['request_id'], 'decision': 'APPROVED'})
        _, payload = self.request('GET', f"/api/runs/{run['run_id']}")
        self.assertTrue(payload['run']['recovery']['allowed'])
        self.assertTrue(payload['run']['resumable'])
        _, payload = self.post_json('/api/runs', {'agent': 'explain', 'input': {}})
        run = payload['run']
        self.post_json(f"/api/runs/{run['run_id']}/approval-decision", {
            'request_id': run['pending_approval']['request_id'], 'decision': 'REJECTED'})
        _, payload = self.request('GET', f"/api/runs/{run['run_id']}")
        self.assertEqual(payload['run']['recovery']['reason_code'], 'APPROVAL_REJECTED')
        code, refused = self.post_json(f"/api/runs/{run['run_id']}/resume", {})
        self.assertEqual(code, 409)
        self.assertEqual(refused['error']['code'], payload['run']['recovery']['reason_code'])

    def test_checkpoint_unavailable_explains_existing_refusal(self):
        self.register_recovery_agent()
        _, payload = self.post_json('/api/runs', {'agent': 'recovery-router', 'input': {'task': 'test'}})
        run_id = payload['run']['run_id']
        self.server.checkpoint_store.delete(run_id)
        _, payload = self.request('GET', f'/api/runs/{run_id}')
        self.assertFalse(payload['run']['resumable'])
        self.assertEqual(payload['run']['recovery']['detail'], 'CHECKPOINT_MISSING')
        code, refused = self.post_json(f'/api/runs/{run_id}/resume', {})
        self.assertEqual(code, 409)
        self.assertEqual(refused['error']['code'], payload['run']['recovery']['reason_code'])
