"""Synthetic browser fixture and offline asset contract."""

import json
from http.client import HTTPConnection
from pathlib import Path
import tempfile
import threading
import unittest

from tools.browser_smoke_server import SMOKE_TOKEN, STREAM_TEXT, create_smoke_server


class BrowserSmokeFixtureTests(unittest.TestCase):
    def test_fixture_serves_offline_assets_and_marks_scripted_output(self):
        with tempfile.TemporaryDirectory() as directory:
            server = create_smoke_server(directory, port=0, stream_delay=0)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()

            def request(method, url, payload=None):
                connection = HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
                try:
                    headers = {'Content-Type': 'application/json'} if payload is not None else {}
                    connection.request(method, url, json.dumps(payload) if payload is not None else None, headers)
                    response = connection.getresponse()
                    return response.status, dict(response.getheaders()), response.read()
                finally:
                    connection.close()

            try:
                for url, name in (('/', 'index.html'), ('/app.js', 'app.js'),
                                  ('/styles.css', 'styles.css'),
                                  ('/vendor/lucide-0.468.0.min.js', 'vendor/lucide-0.468.0.min.js')):
                    status, headers, body = request('GET', url)
                    self.assertEqual(status, 200)
                    self.assertEqual(body, (Path('frontend') / name).read_bytes())
                    self.assertIn("default-src 'self'", headers['Content-Security-Policy'])
                status, _, body = request('GET', '/api/health')
                self.assertEqual(status, 200)
                health = json.loads(body)
                self.assertIs(health['approval_auth_required'], True)
                self.assertNotIn(SMOKE_TOKEN, body.decode())
                self.assertIn('scripted-stream', health['agents'])
                status, _, body = request('POST', '/api/runs', {
                    'agent': 'scripted-stream', 'input': {'task': 'synthetic browser smoke'}})
                self.assertEqual(status, 200)
                run = json.loads(body)['run']
                self.assertEqual(run['status'], 'COMPLETED')
                self.assertEqual(run['state']['summary'], STREAM_TEXT)
                self.assertEqual(run['state']['model_kind'], 'offline-scripted')
                self.assertEqual(run['state']['model_quality'], 'NOT_EVALUATED_SCRIPTED')
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=5)
