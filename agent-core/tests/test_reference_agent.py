"""Reference application uses real Harness/approval persistence, scripted models."""

import hashlib
from contextlib import redirect_stderr
from http.client import HTTPConnection
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from agent_core import FileCheckpointStore
from examples.reference_agent import (OfflineReviewModel, ReferenceError, build_app,
                                      create_reference_server, main, read_document, roots, validate_proposal)


class ReferenceAgentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.docs = self.root / 'docs'
        self.docs.mkdir()
        (self.docs / 'guide.md').write_text('Verify the checks.\nRollback to the previous release.\n', encoding='utf-8')
        self.workspace = self.root / 'app'

    def start(self, model=None, filename='review.md'):
        app = build_app(self.workspace, self.docs, model=model)
        result = app[0].run({'task': 'Review the supplied notes.', 'filename': filename}).run
        return app, result

    def approve(self, app, result):
        agent, runtime, _, decisions = app
        decisions.decide(result, result.pending_approval['request_id'], 'APPROVED')
        checkpoint = FileCheckpointStore(self.workspace / 'state/checkpoints').load(result.run_id)
        return runtime.resume_approved(agent.graph, checkpoint, decisions=decisions)

    def test_restart_approval_saves_exact_preview_without_repeating_model(self):
        model = OfflineReviewModel()
        (_, _, audit, _), waiting = self.start(model)
        self.assertEqual(waiting.status.value, 'WAITING_APPROVAL')
        self.assertEqual(model.calls, 3)
        self.assertEqual(list((self.workspace / 'drafts').iterdir()), [])
        second_model = OfflineReviewModel()
        app = build_app(self.workspace, self.docs, model=second_model)
        result = self.approve(app, waiting)
        self.assertEqual(result.status.value, 'COMPLETED')
        self.assertEqual(second_model.calls, 0)
        data = (self.workspace / 'drafts/review.md').read_bytes()
        self.assertEqual(data.decode('utf-8'), waiting.state['draft']['preview'])
        self.assertEqual(hashlib.sha256(data).hexdigest(), result.state['saved_draft']['sha256'])
        events = audit.events(result.run_id)
        self.assertEqual(sum(e.event_type == 'tool.completed' and e.summary.get('tool') == 'save_draft' for e in events), 1)

    def test_rejection_never_writes(self):
        app, waiting = self.start()
        result = app[1].reject_approval(waiting.run_id, waiting.pending_approval['request_id'], app[3])
        self.assertEqual(result.reason_code, 'APPROVAL_REJECTED')
        self.assertEqual(list((self.workspace / 'drafts').iterdir()), [])

    def test_saved_result_reused_after_checkpoint_failure_without_overwrite(self):
        app, waiting = self.start()
        # The write completes, but committing the next graph boundary fails.
        with patch.object(FileCheckpointStore, 'save', side_effect=OSError('checkpoint unavailable')):
            failed = self.approve(app, waiting)
        self.assertEqual(failed.reason_code, 'CHECKPOINT_SAVE_FAILED')
        target = self.workspace / 'drafts/review.md'
        self.assertTrue(target.exists())
        before = target.stat().st_mtime_ns
        model = OfflineReviewModel()
        agent, runtime, audit, _ = build_app(self.workspace, self.docs, model=model)
        checkpoint = FileCheckpointStore(self.workspace / 'state/checkpoints').load(waiting.run_id)
        result = runtime.resume(agent.graph, checkpoint)
        self.assertEqual(result.status.value, 'COMPLETED')
        self.assertEqual(model.calls, 0)
        self.assertEqual(target.stat().st_mtime_ns, before)
        self.assertEqual(sum(e.event_type == 'tool.completed' and e.summary.get('tool') == 'save_draft'
                             for e in audit.events(result.run_id)), 1)

    def test_document_change_during_approval_stops_write(self):
        app, waiting = self.start()
        (self.docs / 'guide.md').write_text('Changed evidence.', encoding='utf-8')
        result = self.approve(app, waiting)
        self.assertEqual(result.reason_code, 'SOURCE_CHANGED')
        self.assertFalse((self.workspace / 'drafts/review.md').exists())

    def test_existing_draft_is_not_overwritten(self):
        app, waiting = self.start()
        target = self.workspace / 'drafts/review.md'
        target.write_text('existing', encoding='utf-8')
        result = self.approve(app, waiting)
        self.assertEqual(result.reason_code, 'DRAFT_EXISTS')
        self.assertEqual(target.read_text(), 'existing')

    def test_traversal_absolute_and_windows_device_names_are_denied(self):
        for name in ('../outside.md', '/tmp/other.md', r'C:\other.md', 'x/y.md', 'CON.md', 'NUL.md', 'x.md:stream'):
            with self.subTest(name=name), self.assertRaises(ReferenceError) as caught:
                read_document(self.docs.resolve(), name)
            self.assertEqual(caught.exception.reason_code, 'PATH_DENIED')

    def test_model_read_escape_and_write_tool_escalation_are_stopped(self):
        class Attack:
            def __init__(self, name, args):
                self.name, self.args = name, args

            def complete_with_tools(self, messages, schemas):
                return {'tool_calls': [{'id': 'bad', 'name': self.name, 'arguments': self.args}]}

        for name, args, reason in (('read_document', {'filename': '../outside.md'}, 'PATH_DENIED'),
                                   ('save_draft', {}, 'TOOL_NOT_EXPOSED')):
            with self.subTest(name=name):
                _, result = self.start(Attack(name, args))
                self.assertEqual(result.reason_code, reason)
                self.assertEqual(list((self.workspace / 'drafts').iterdir()), [])

    def test_invalid_evidence_cannot_reach_approval(self):
        class Forged(OfflineReviewModel):
            def complete_with_tools(inner, messages, schemas):
                reply = super(Forged, inner).complete_with_tools(messages, schemas)
                if 'content' in reply:
                    proposal = json.loads(reply['content'])
                    proposal['citations'][0]['quote'] = 'An invented quotation.'
                    reply['content'] = json.dumps(proposal)
                return reply

        _, result = self.start(Forged())
        self.assertEqual(result.reason_code, 'INVALID_CITATION')
        self.assertEqual(list((self.workspace / 'drafts').iterdir()), [])

    def test_redirected_document_is_not_read(self):
        outside = self.root / 'outside.md'
        outside.write_text('private', encoding='utf-8')
        try:
            (self.docs / 'link.md').symlink_to(outside)
        except OSError:
            self.skipTest('symlink creation unavailable')
        with self.assertRaises(ReferenceError) as caught:
            read_document(self.docs.resolve(), 'link.md')
        self.assertEqual(caught.exception.reason_code, 'PATH_DENIED')

    def test_oversized_document_and_overlapping_roots_are_refused(self):
        (self.docs / 'large.md').write_bytes(b'x' * 65_537)
        with self.assertRaises(ReferenceError) as caught:
            read_document(self.docs.resolve(), 'large.md')
        self.assertEqual(caught.exception.reason_code, 'DOCUMENT_TOO_LARGE')
        with self.assertRaises(ValueError):
            roots(self.docs / 'output', self.docs)

    def test_real_mode_missing_configuration_does_not_create_workspace(self):
        env = {key: value for key, value in os.environ.items() if not key.startswith('AGENT_MODEL_')}
        with patch.dict(os.environ, env, clear=True):
            code = main(['--mode', 'real', '--workspace', str(self.workspace)])
        self.assertEqual(code, 2)
        self.assertFalse(self.workspace.exists())

    def test_http_task_only_input_approval_and_restart_uses_shared_persistence(self):
        server = create_reference_server(self.workspace, self.docs, port=0)

        def start(server):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            return thread

        def request(server, path, payload):
            connection = HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
            try:
                connection.request('POST', path, json.dumps(payload), {'Content-Type': 'application/json'})
                response = connection.getresponse()
                self.assertIn(response.status, (200, 202))
                return json.loads(response.read())
            finally:
                connection.close()

        thread = start(server)
        try:
            payload = request(server, '/api/runs', {'agent': 'document-review', 'input': {'task': 'Review notes'}})
            run = payload['run']
            self.assertEqual(run['status'], 'WAITING_APPROVAL')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        server = create_reference_server(self.workspace, self.docs, port=0)
        thread = start(server)
        try:
            request(server, f"/api/runs/{run['run_id']}/approval-decision", {
                'request_id': run['pending_approval']['request_id'], 'decision': 'APPROVED'})
            result = request(server, f"/api/runs/{run['run_id']}/resume", {})['run']
            self.assertEqual(result['status'], 'COMPLETED')
            self.assertTrue((self.workspace / 'drafts' / result['state']['saved_draft']['filename']).exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_token_protected_reference_workflow_saves_only_after_authenticated_resume(self):
        token = 'synthetic-reference-token'
        server = create_reference_server(self.workspace, self.docs, port=0, approval_token=token)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request(method, path, payload=None, credential=None):
            connection = HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
            try:
                headers = {'Content-Type': 'application/json'}
                if credential is not None:
                    headers['Authorization'] = f'Bearer {credential}'
                connection.request(method, path, json.dumps(payload) if payload is not None else None, headers)
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        try:
            self.assertIs(request('GET', '/api/health')[1]['approval_auth_required'], True)
            status, payload = request('POST', '/api/runs', {
                'agent': 'document-review', 'input': {'task': 'Review notes', 'filename': 'protected.md'}})
            self.assertEqual(status, 200)
            run = payload['run']
            base = f"/api/runs/{run['run_id']}"
            decision = {'request_id': run['pending_approval']['request_id'], 'decision': 'APPROVED'}
            self.assertEqual(request('POST', base + '/approval-decision', decision)[0], 401)
            self.assertEqual(request('POST', base + '/approval-decision', decision, 'wrong')[0], 401)
            self.assertEqual(request('GET', base)[1]['run']['status'], 'WAITING_APPROVAL')
            self.assertEqual(request('POST', base + '/approval-decision', decision, token)[0], 202)
            self.assertEqual(list((self.workspace / 'drafts').iterdir()), [])
            self.assertEqual(request('POST', base + '/resume', {}, 'wrong')[0], 401)
            current = request('GET', base)[1]['run']
            self.assertEqual(current['status'], 'WAITING_APPROVAL')
            self.assertTrue(current['recovery']['allowed'])
            status, payload = request('POST', base + '/resume', {}, token)
            self.assertEqual((status, payload['run']['status']), (200, 'COMPLETED'))
            self.assertEqual((self.workspace / 'drafts/protected.md').read_text(encoding='utf-8'),
                             run['state']['draft']['preview'])
            for record in (self.workspace / 'state').rglob('*.json'):
                self.assertNotIn(token, record.read_text(encoding='utf-8'))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_cli_missing_token_fails_before_creating_workspace(self):
        for value in (None, '', '   '):
            with self.subTest(value=value):
                env = {} if value is None else {'REFERENCE_TEST_TOKEN': value}
                stderr = io.StringIO()
                with patch.dict(os.environ, env, clear=True), redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as caught:
                        main(['--action', 'serve', '--workspace', str(self.workspace),
                              '--approval-token-env', 'REFERENCE_TEST_TOKEN'])
                self.assertEqual(caught.exception.code, 2)
                self.assertIn('must be set and nonempty', stderr.getvalue())
                self.assertFalse(self.workspace.exists())
