"""HTTP contract checks for the local fitness backend."""

from __future__ import annotations

import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fitness_backend.algorithm import AlgorithmError, SquatAlgorithm, validate_result
from fitness_backend.server import FitnessServer
from fitness_backend.storage import Storage


MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isom"


class FitnessBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "FITNESS_ALGORITHM_SCRIPT": "", "FITNESS_ALGORITHM_MODEL": "",
            "FITNESS_ALGORITHM_RUN": "",
        })
        self.env.start()
        self.server = FitnessServer(("127.0.0.1", 0), Path(self.temp.name))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.env.stop()
        self.temp.cleanup()

    def request(self, method: str, path: str, body: bytes | dict | None = None,
                *, session: str = "session-a", headers: dict | None = None) -> tuple[int, dict]:
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
            headers = {"Content-Type": "application/json", **(headers or {})}
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers={
                "X-Session-Id": session, **(headers or {}),
            })
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def upload(self, *, key: str | None = None, body: bytes = MP4) -> str:
        headers = {"Content-Type": "video/mp4"}
        if key is not None:
            headers["X-Upload-Key"] = key
        status, payload = self.request("POST", "/api/videos", body, headers=headers)
        self.assertEqual(status, 201)
        return payload["video_id"]

    def wait_for(self, task_id: str, expected: str) -> dict:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, payload = self.request("GET", f"/api/analyses/{task_id}")
            self.assertEqual(status, 200)
            if payload["task"]["status"] == expected:
                return payload["task"]
            time.sleep(0.02)
        self.fail(f"task {task_id} did not reach {expected}")

    def test_upload_is_persisted_and_rejects_wrong_signature(self) -> None:
        video_id = self.upload()
        video = self.server.storage.video(video_id, "session-a")
        self.assertEqual(Path(video["path"]).read_bytes(), MP4)
        self.assertIsNone(self.server.storage.video(video_id, "session-b"))
        status, payload = self.request("POST", "/api/videos", b"not a video",
                                       headers={"Content-Type": "video/mp4"})
        self.assertEqual((status, payload["error"]["code"]), (400, "INVALID_VIDEO"))
        self.assertEqual(len(list(self.server.storage.videos.iterdir())), 1)

    def test_upload_key_reuses_video_and_rejects_changed_content(self) -> None:
        video_id = self.upload(key="upload-1")
        status, reused = self.request("POST", "/api/videos", MP4, headers={
            "Content-Type": "video/mp4", "X-Upload-Key": "upload-1",
        })
        self.assertEqual(status, 200)
        self.assertTrue(reused["reused"])
        self.assertEqual(reused["video_id"], video_id)
        changed = MP4 + b"different"
        status, conflict = self.request("POST", "/api/videos", changed, headers={
            "Content-Type": "video/mp4", "X-Upload-Key": "upload-1",
        })
        self.assertEqual((status, conflict["error"]["code"]), (409, "UPLOAD_KEY_CONFLICT"))
        self.assertEqual(len(list(self.server.storage.videos.iterdir())), 1)

    def test_video_metadata_is_session_scoped_and_detects_missing_file(self) -> None:
        video_id = self.upload(key="upload-2")
        status, payload = self.request("GET", f"/api/videos/{video_id}")
        self.assertEqual(status, 200)
        self.assertTrue(payload["video"]["available"])
        self.assertNotIn("path", payload["video"])
        status, missing = self.request("GET", f"/api/videos/{video_id}", session="session-b")
        self.assertEqual((status, missing["error"]["code"]), (404, "VIDEO_NOT_FOUND"))
        Path(self.server.storage.video(video_id, "session-a")["path"]).unlink()
        status, payload = self.request("GET", f"/api/videos/{video_id}")
        self.assertEqual(status, 200)
        self.assertFalse(payload["video"]["available"])
        status, unavailable = self.request("POST", "/api/analyses", {
            "video_id": video_id, "exercise_id": "normal_squat",
        })
        self.assertEqual((status, unavailable["error"]["code"]), (409, "VIDEO_UNAVAILABLE"))

    def test_stalled_upload_times_out_and_removes_temporary_file(self) -> None:
        with patch("fitness_backend.server.UPLOAD_IDLE_TIMEOUT_SECONDS", 0.1):
            with socket.create_connection(("127.0.0.1", self.port), timeout=3) as client:
                client.sendall(b"POST /api/videos HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                               b"X-Session-Id: session-a\r\nContent-Type: video/mp4\r\n"
                               b"Content-Length: 100\r\n\r\n" + MP4)
                response = client.recv(4096)
        self.assertIn(b"408 Request Timeout", response)
        self.assertEqual(list(self.server.storage.videos.iterdir()), [])

    def test_unconfigured_algorithm_fails_without_fabricated_result(self) -> None:
        video_id = self.upload()
        body = {"video_id": video_id, "exercise_id": "normal_squat",
                "camera_view": "side", "idempotency_key": "attempt-1"}
        status, created = self.request("POST", "/api/analyses", body)
        self.assertEqual(status, 202)
        task_id = created["task"]["task_id"]
        task = self.wait_for(task_id, "FAILED")
        self.assertEqual(task["reason_code"], "ALGORITHM_NOT_CONFIGURED")
        self.assertIsNone(task["algorithm"])
        self.assertIsNone(task["agent"])
        self.assertEqual(task["video_id"], video_id)
        status, reused = self.request("POST", "/api/analyses", body)
        self.assertEqual(status, 200)
        self.assertTrue(reused["reused"])
        self.assertEqual(reused["task"]["task_id"], task_id)
        status, conflict = self.request("POST", "/api/analyses", {
            **body, "camera_view": "front",
        })
        self.assertEqual((status, conflict["error"]["code"]), (409, "IDEMPOTENCY_CONFLICT"))
        status, missing = self.request("GET", f"/api/analyses/{task_id}", session="session-b")
        self.assertEqual((status, missing["error"]["code"]), (404, "TASK_NOT_FOUND"))

    def test_algorithm_result_is_separate_from_agent_result(self) -> None:
        class Algorithm:
            configured = True

            def analyze(self, **kwargs):
                return {"algorithm_version": "fixture-v1", "results": {"angle_label": "unknown"}}

        self.server.algorithm = Algorithm()
        video_id = self.upload()
        status, payload = self.request("POST", "/api/analyses", {
            "video_id": video_id, "exercise_id": "normal_squat",
        })
        self.assertEqual(status, 202)
        task = self.wait_for(payload["task"]["task_id"], "COMPLETED")
        self.assertEqual(task["algorithm"]["results"]["angle_label"], "unknown")
        self.assertEqual(task["agent_status"], "NOT_CONFIGURED")
        self.assertIsNone(task["agent"])

    def test_algorithm_reported_failure_does_not_complete_task(self) -> None:
        class Algorithm:
            configured = True

            def analyze(self, **kwargs):
                return {"status": "failed", "results": {"angle_label": "unknown"}}

        self.server.algorithm = Algorithm()
        video_id = self.upload()
        _, payload = self.request("POST", "/api/analyses", {
            "video_id": video_id, "exercise_id": "normal_squat",
        })
        task = self.wait_for(payload["task"]["task_id"], "FAILED")
        self.assertEqual(task["reason_code"], "ALGORITHM_REPORTED_FAILURE")
        self.assertIsNone(task["algorithm"])

    def test_invalid_camera_view_type_is_bad_request(self) -> None:
        video_id = self.upload()
        status, payload = self.request("POST", "/api/analyses", {
            "video_id": video_id, "exercise_id": "normal_squat", "camera_view": [],
        })
        self.assertEqual((status, payload["error"]["code"]), (400, "INVALID_CAMERA_VIEW"))

    def test_cancel_running_task_reaches_terminal_state(self) -> None:
        started = threading.Event()

        class Algorithm:
            configured = True

            def analyze(self, **kwargs):
                started.set()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and not kwargs["cancelled"]():
                    time.sleep(0.01)
                return {"results": {"angle_label": "unknown"}}

        self.server.algorithm = Algorithm()
        video_id = self.upload()
        _, payload = self.request("POST", "/api/analyses", {
            "video_id": video_id, "exercise_id": "normal_squat",
        })
        self.assertTrue(started.wait(2))
        task_id = payload["task"]["task_id"]
        status, _ = self.request("POST", f"/api/analyses/{task_id}/cancel")
        self.assertEqual(status, 200)
        task = self.wait_for(task_id, "CANCELED")
        self.assertEqual(task["reason_code"], "USER_CANCELED")
        self.assertIsNone(task["algorithm"])

    def test_restart_marks_unfinished_tasks_failed(self) -> None:
        video_id = self.upload()
        self.server.storage.add_analysis({
            "id": "a" * 32, "video_id": video_id, "session_id": "session-a",
            "exercise_id": "normal_squat", "camera_view": "unknown",
            "status": "QUEUED", "idempotency_key": None,
        })
        Storage(Path(self.temp.name)).recover_interrupted()
        task = self.server.storage.analysis("a" * 32, "session-a")
        self.assertEqual((task["status"], task["reason_code"]), ("FAILED", "SERVER_RESTARTED"))

    def test_restart_cleans_only_interrupted_temporary_files(self) -> None:
        upload = self.server.storage.videos / ("." + "a" * 32 + ".upload")
        result = self.server.storage.results / f"{'b' * 32}.json"
        unrelated = self.server.storage.videos / "keep.txt"
        for path in (upload, result, unrelated):
            path.write_bytes(b"temporary")
        self.server.storage.clean_interrupted_files()
        self.assertFalse(upload.exists())
        self.assertFalse(result.exists())
        self.assertTrue(unrelated.exists())

    def test_second_server_cannot_recover_active_instance_tasks(self) -> None:
        video_id = self.upload()
        self.server.storage.add_analysis({
            "id": "b" * 32, "video_id": video_id, "session_id": "session-a",
            "exercise_id": "normal_squat", "camera_view": "unknown",
            "status": "QUEUED", "idempotency_key": None,
        })
        with self.assertRaisesRegex(RuntimeError, "another fitness backend instance"):
            FitnessServer(("127.0.0.1", 0), Path(self.temp.name))
        task = self.server.storage.analysis("b" * 32, "session-a")
        self.assertEqual(task["status"], "QUEUED")

    def test_scheduling_failure_is_persisted(self) -> None:
        video_id = self.upload()
        with patch.object(self.server.workers, "submit", side_effect=RuntimeError("closed")), self.assertLogs("fitness_backend", level="ERROR"):
            status, payload = self.request("POST", "/api/analyses", {
                "video_id": video_id, "exercise_id": "normal_squat",
            })
        self.assertEqual(status, 202)
        task_id = payload["task"]["task_id"]
        task = self.server.storage.analysis(task_id, "session-a")
        self.assertEqual((task["status"], task["reason_code"]),
                         ("FAILED", "SCHEDULING_FAILED"))

    def test_list_analyses_is_paginated_and_scoped_to_session(self) -> None:
        video_id = self.upload()
        for _ in range(2):
            status, _ = self.request("POST", "/api/analyses", {
                "video_id": video_id, "exercise_id": "normal_squat",
            })
            self.assertEqual(status, 202)
        status, payload = self.request("GET", "/api/analyses?limit=1&offset=0")
        self.assertEqual(status, 200)
        self.assertEqual(payload["total"], 2)
        self.assertEqual(len(payload["tasks"]), 1)
        self.assertNotIn("algorithm", payload["tasks"][0])
        status, other = self.request("GET", "/api/analyses", session="session-b")
        self.assertEqual(status, 200)
        self.assertEqual(other["total"], 0)
        status, invalid = self.request("GET", "/api/analyses?limit=101")
        self.assertEqual((status, invalid["error"]["code"]), (400, "INVALID_QUERY"))


class SquatAlgorithmTests(unittest.TestCase):
    def test_handoff_cli_without_index_normalizes_evidence_and_quality(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = root / "algorithm.py"
            script.write_text(
                "import json,sys\n"
                "assert '--run' not in sys.argv\n"
                "assert '--all-labels' in sys.argv\n"
                "json.dump({'version':'squat-offline-api-v1',"
                "'status':'usable_candidate','quality':{'status':'usable_candidate'},"
                "'guidance':{'observations':[]},"
                "'biomechanics':{'status':'estimated','metrics':{'angle':None}},"
                "'video_path':'private'},open(sys.argv[sys.argv.index('--output')+1],'w'))\n",
                encoding="utf-8",
            )
            model = root / "model.task"
            model.touch()
            with patch.dict(os.environ, {
                "FITNESS_ALGORITHM_SCRIPT": str(script), "FITNESS_ALGORITHM_MODEL": str(model),
                "FITNESS_ALGORITHM_RUN": "", "FITNESS_ALGORITHM_PYTHON": sys.executable,
            }):
                adapter = SquatAlgorithm()
            self.assertTrue(adapter.configured)
            result = adapter.analyze(video=root / "input.mp4", output=root / "result.json",
                                     exercise_id="wide_squat", camera_view="front", cancelled=lambda: False)
            self.assertEqual(result["status"], "completed")
            self.assertIsNone(result["results"]["biomechanics"]["metrics"]["angle"])
            self.assertNotIn("video_path", result)
            script.write_text(script.read_text().replace("'usable_candidate'", "'insufficient_pose'"))
            with self.assertRaises(AlgorithmError) as error:
                adapter.analyze(video=root / "input.mp4", output=root / "result.json",
                                exercise_id="wide_squat", camera_view="front", cancelled=lambda: False)
            self.assertEqual(error.exception.code, "QUALITY_INSUFFICIENT")

    def test_result_validation_preserves_status_and_rejects_quality_failure(self) -> None:
        result = validate_result({"status": "completed", "results": {"angle_label": "unknown"}})
        self.assertEqual(result["status"], "completed")
        with self.assertRaises(AlgorithmError) as error:
            validate_result({"status": "quality_insufficient", "results": {"angle_label": "unknown"}})
        self.assertEqual(error.exception.code, "QUALITY_INSUFFICIENT")

    def test_cli_adapter_filters_internal_fields(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = root / "algorithm.py"
            script.write_text(
                "import json, sys\n"
                "json.dump({'algorithm_version':'fixture-v1','results':{'angle_label':'unknown'},"
                "'video_path':'secret'}, open(sys.argv[sys.argv.index('--output')+1], 'w'))\n",
                encoding="utf-8",
            )
            model = root / "model.task"
            model.write_bytes(b"fixture")
            with patch.dict(os.environ, {
                "FITNESS_ALGORITHM_SCRIPT": str(script),
                "FITNESS_ALGORITHM_MODEL": str(model),
                "FITNESS_ALGORITHM_RUN": str(root),
                "FITNESS_ALGORITHM_PYTHON": sys.executable,
            }):
                algorithm = SquatAlgorithm()
            result = algorithm.analyze(video=root / "video.mp4", output=root / "result.json",
                                       exercise_id="normal_squat", camera_view="side",
                                       cancelled=lambda: False)
            self.assertEqual(result["results"]["angle_label"], "unknown")
            self.assertNotIn("video_path", result)
            self.assertFalse((root / "result.json").exists())


if __name__ == "__main__":
    unittest.main()
