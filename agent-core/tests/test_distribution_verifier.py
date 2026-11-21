"""Distribution evidence must describe the installed bytes and dependency closure."""

import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import verify_distribution as verifier


class DistributionVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.wheel = self.root / "agent_core-0.4.0-py3-none-any.whl"
        self.wheel.write_bytes(b"first-build")
        self.installed_bytes = None
        self.missing_dependency = False

    def command(self, args, **kwargs):
        if "install" in args:
            self.installed_bytes = Path(args[-1]).read_bytes()
        elif "check" in args and self.missing_dependency:
            raise subprocess.CalledProcessError(1, args)
        elif "-m" not in args:
            self.smoke(args, **kwargs)
        return subprocess.CompletedProcess(args, 0)

    def smoke(self, args, **kwargs):
        (Path(args[-1]) / "installed.json").write_text(
            json.dumps({"overall": "PASS", "checks": [{"name": "synthetic smoke", "passed": True}]}),
            encoding="utf-8")

    def run_verifier(self, create=None):
        with patch.object(verifier.venv.EnvBuilder, "create", side_effect=create), \
                patch.object(verifier.subprocess, "run", side_effect=self.command), \
                patch.object(verifier, "run_smoke", side_effect=self.smoke):
            return verifier.verify(self.wheel, self.root / "reports")

    def test_report_hash_matches_installed_snapshot_when_source_changes(self):
        def replace_source(_environment):
            self.wheel.write_bytes(b"replacement-build")

        _, report = self.run_verifier(create=replace_source)
        self.assertEqual(report["overall"], "PASS")
        self.assertEqual(report["wheel_sha256"], hashlib.sha256(self.installed_bytes).hexdigest())

    def test_uninstalled_declared_dependency_is_a_failure(self):
        self.missing_dependency = True
        _, report = self.run_verifier()
        self.assertEqual(report["overall"], "FAIL")

    def test_smoke_timeout_stops_descendant_server(self):
        address_file = self.root / "server.json"
        child = ("import json, os, socket, time; from pathlib import Path; "
                 "s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen(); "
                 f"Path({str(address_file)!r}).write_text(json.dumps([os.getpid(), s.getsockname()[1]])); "
                 "time.sleep(60)")
        parent = f"import subprocess, sys; subprocess.run([sys.executable, '-c', {child!r}])"
        with self.assertRaises(subprocess.TimeoutExpired):
            verifier.run_smoke([sys.executable, "-c", parent], cwd=self.root, env=os.environ.copy(),
                               stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, timeout=3)
        pid, port = json.loads(address_file.read_text())

        def cleanup():
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10)
            else:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        self.addCleanup(cleanup)
        with socket.socket() as connection:
            connection.settimeout(1)
            self.assertNotEqual(connection.connect_ex(("127.0.0.1", port)), 0, "server survived smoke timeout")


if __name__ == "__main__":
    unittest.main()
