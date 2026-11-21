"""Run a real video through agent-core orchestration and the fitness HTTP API."""
from __future__ import annotations

import argparse
import dataclasses
import http.client
import json
import os
import threading
import time
from pathlib import Path
from uuid import uuid4

from agent_core import AgentBuilder, NodeResult
from .server import FitnessServer


def main() -> int:
    workspace = Path(__file__).resolve().parents[2]
    package = workspace / "squat-algorithm-handoff-20261119"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, default=workspace / "Squat-Complete-Review-C-221/Squat-Complete-Review-C-221/videos/part1/00054719.mp4")
    parser.add_argument("--action", choices=("normal_squat", "wide_squat", "narrow_squat"), default="wide_squat")
    parser.add_argument("--camera-view", choices=("front", "side", "oblique", "unknown"), default="front")
    args = parser.parse_args()
    if not args.video.is_file():
        parser.error("video does not exist")
    os.environ.setdefault("FITNESS_ALGORITHM_SCRIPT", str(package / "scripts/squat_offline_api.py"))
    os.environ.setdefault("FITNESS_ALGORITHM_MODEL", str(package / "models/pose_landmarker_lite.task"))
    os.environ.setdefault("FITNESS_ALGORITHM_PYTHON", str(package / ".venv/Scripts/python.exe"))
    run_id = uuid4().hex
    folder = workspace / "fitness-backend/outputs/integration" / run_id
    server = FitnessServer(("127.0.0.1", 0), folder / "backend")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session = "integration-" + run_id
    transitions = []

    def request(method, path, body=None, headers=None):
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
            headers = {"Content-Type": "application/json", **(headers or {})}
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=15)
        try:
            connection.request(method, path, body, {"X-Session-Id": session, **(headers or {})})
            response = connection.getresponse()
            result = json.loads(response.read())
            if response.status >= 400:
                raise RuntimeError(result.get("error", {}).get("code", "HTTP_ERROR"))
            return result
        finally:
            connection.close()

    def upload(ctx):
        result = request("POST", "/api/videos", args.video.read_bytes(),
                         {"Content-Type": "video/mp4", "X-Filename": args.video.name})
        print("upload: COMPLETED", flush=True)
        return NodeResult.success({"video_id": result["video_id"]})

    def analyze(ctx):
        created = request("POST", "/api/analyses", {
            "video_id": ctx.state["video_id"], "exercise_id": args.action,
            "camera_view": args.camera_view, "idempotency_key": run_id,
        })
        task_id = created["task"]["task_id"]
        deadline = time.monotonic() + 260
        while time.monotonic() < deadline:
            task = request("GET", "/api/analyses/" + task_id)["task"]
            if not transitions or transitions[-1] != task["status"]:
                transitions.append(task["status"])
                print("analysis:", task["status"], flush=True)
            if task["status"] in ("COMPLETED", "FAILED", "CANCELED"):
                (folder / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
                if task["status"] != "COMPLETED":
                    return NodeResult.failed(task["reason_code"] or "ANALYSIS_FAILED")
                return NodeResult.success({"task": task})
            time.sleep(0.5)
        return NodeResult.failed("INTEGRATION_TIMEOUT")

    def verify(ctx):
        task = ctx.state["task"]
        evidence = task["algorithm"]
        if not evidence or "biomechanics" not in evidence.get("results", {}):
            return NodeResult.failed("MISSING_BIOMECHANICS")
        if not evidence.get("guidance", {}).get("observations"):
            return NodeResult.failed("MISSING_OBSERVATIONS")
        print("verify_evidence: COMPLETED; model feedback:", task["agent_status"], flush=True)
        return NodeResult.success({"evidence_verified": True})

    started = time.monotonic()
    try:
        health = request("GET", "/api/health")
        agent = (AgentBuilder("fitness-algorithm-integration")
                 .add_node("upload", upload).add_node("analyze", analyze)
                 .add_node("verify_evidence", verify).set_entry("upload")
                 .add_edge("upload", "analyze").add_edge("analyze", "verify_evidence").build())
        result = agent.run({"video_name": args.video.name}, run_id=run_id)
        report = {"run_id": run_id, "status": result.status.value,
                  "reason_code": result.reason_code, "health": health,
                  "duration_seconds": round(time.monotonic() - started, 3),
                  "task_status_transitions": transitions,
                  "trace": [dataclasses.asdict(event) for event in result.trace]}
        (folder / "run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({"run_id": run_id, "status": result.status.value,
                          "report": str(folder / "run.json")}, ensure_ascii=False), flush=True)
        return 0 if result.status.value == "COMPLETED" else 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
