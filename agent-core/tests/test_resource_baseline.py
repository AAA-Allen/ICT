"""Baseline reports must distinguish real checks from synthetic task quality."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.resource_baseline import BaselineModel, run_baseline


class ResourceBaselineTests(unittest.TestCase):
    def test_http_smoke_records_calls_limits_storage_and_shutdown(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory, report = run_baseline(Path(temporary), rounds=2)
            self.assertEqual(report['profile'], 'SMOKE')
            self.assertEqual(report['overall'], 'PASS', report['checks'])
            self.assertEqual(report['external_model_calls'], 0)
            self.assertEqual(report['observations']['completed_runs'], 2)
            self.assertEqual(report['observations']['model_calls'], 2)
            self.assertEqual(report['observations']['tool_calls'], 2)
            self.assertEqual(report['samples'][-1]['cache']['payload_bytes'], 0)
            self.assertGreater(report['samples'][-1]['storage']['files'], 0)
            saved = json.loads((directory / 'report.json').read_text(encoding='utf-8'))
            spec = json.loads((directory / 'spec.json').read_text())
            self.assertEqual(saved['spec'], spec)

    def test_failed_model_task_cannot_pass_baseline(self):
        def fail(model, messages, on_delta):
            model.calls += 1
            return {'content': 42}

        with tempfile.TemporaryDirectory() as temporary, patch.object(BaselineModel, 'complete_stream', new=fail):
            _, report = run_baseline(Path(temporary), rounds=2)
        self.assertEqual(report['overall'], 'FAIL')
        self.assertFalse(report['checks']['completed_runs'])
        self.assertEqual(report['observations']['model_calls'], 2)
        self.assertEqual(report['observations']['tool_calls'], 0)

    def test_server_thread_start_failure_is_reported_without_waiting_for_shutdown(self):
        with tempfile.TemporaryDirectory() as temporary, patch(
                'examples.resource_baseline.threading.Thread.start', side_effect=RuntimeError('start failed')):
            directory, report = run_baseline(Path(temporary), rounds=2)
        self.assertEqual(report['overall'], 'FAIL')
        self.assertEqual(report['error_type'], 'RuntimeError')
        self.assertFalse(report['checks']['execution_completed'])
        self.assertEqual(report['observations']['post_attempts'], 0)
