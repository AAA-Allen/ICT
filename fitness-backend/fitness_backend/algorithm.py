"""Adapter for the algorithm teammate's offline squat CLI, when available."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable


MAX_RESULT_BYTES = 2 * 1024 * 1024


class AlgorithmError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def validate_result(result: Any) -> dict[str, Any]:
    """Accept structured, bounded evidence without assuming an unconfirmed schema."""
    if not isinstance(result, dict):
        raise AlgorithmError("INVALID_ALGORITHM_RESULT")
    status = result.get("status")
    if status is not None:
        if not isinstance(status, str):
            raise AlgorithmError("INVALID_ALGORITHM_RESULT")
        status = status.lower()
        if status == "failed":
            raise AlgorithmError("ALGORITHM_REPORTED_FAILURE")
        if status == "quality_insufficient":
            raise AlgorithmError("QUALITY_INSUFFICIENT")
        if status != "completed":
            raise AlgorithmError("INVALID_ALGORITHM_RESULT")
    if any(key in result and not isinstance(result[key], dict)
           for key in ("results", "guidance", "confidence")):
        raise AlgorithmError("INVALID_ALGORITHM_RESULT")
    if "algorithm_version" in result and not isinstance(result["algorithm_version"], str):
        raise AlgorithmError("INVALID_ALGORITHM_RESULT")
    if not any(isinstance(result.get(key), dict) and result[key]
               for key in ("results", "guidance")):
        raise AlgorithmError("INVALID_ALGORITHM_RESULT")
    try:
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AlgorithmError("INVALID_ALGORITHM_RESULT") from exc
    if len(encoded.encode("utf-8")) > MAX_RESULT_BYTES:
        raise AlgorithmError("ALGORITHM_RESULT_TOO_LARGE")
    return json.loads(encoded)


class SquatAlgorithm:
    def __init__(self) -> None:
        bundle = Path(__file__).resolve().parents[2] / "squat-algorithm-handoff-20261119"
        script = os.environ.get("FITNESS_ALGORITHM_SCRIPT", str(bundle / "scripts/squat_offline_api.py"))
        model = os.environ.get("FITNESS_ALGORITHM_MODEL", str(bundle / "models/pose_landmarker_lite.task"))
        self.script = Path(script) if script else None
        self.model = Path(model) if model else None
        self.run = os.getenv("FITNESS_ALGORITHM_RUN")
        bundled_python = bundle / ".venv/Scripts/python.exe"
        self.python = os.getenv("FITNESS_ALGORITHM_PYTHON", str(bundled_python) if bundled_python.is_file() else sys.executable)
        self.timeout_seconds = 180

    @property
    def configured(self) -> bool:
        return bool(self.script and self.model and self.script.is_file() and self.model.is_file())

    def analyze(self, *, video: Path, output: Path, exercise_id: str,
                camera_view: str, cancelled: Callable[[], bool]) -> dict[str, Any]:
        if not self.configured:
            raise AlgorithmError("ALGORITHM_NOT_CONFIGURED")
        if cancelled():
            raise AlgorithmError("USER_CANCELED")
        command = [self.python, "-X", "utf8", str(self.script), "--video", str(video),
                   "--model", str(self.model),
                   "--action", exercise_id, "--camera-view", camera_view,
                   "--output", str(output)]
        if self.run:
            command.extend(["--run", self.run])
        command.append("--all-labels")
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + self.timeout_seconds
        try:
            while process.poll() is None:
                reason = "USER_CANCELED" if cancelled() else "ALGORITHM_TIMEOUT" if time.monotonic() >= deadline else None
                if reason:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise AlgorithmError(reason)
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
            if process.returncode != 0:
                raise AlgorithmError("ALGORITHM_FAILED")
            if cancelled():
                raise AlgorithmError("USER_CANCELED")
            try:
                with output.open("rb") as stream:
                    raw = stream.read(MAX_RESULT_BYTES + 1)
                if len(raw) > MAX_RESULT_BYTES:
                    raise AlgorithmError("ALGORITHM_RESULT_TOO_LARGE")
                result = json.loads(raw.decode("utf-8"))
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                raise AlgorithmError("INVALID_ALGORITHM_RESULT") from exc
            if not isinstance(result, dict):
                raise AlgorithmError("INVALID_ALGORITHM_RESULT")
            if result.get("version") == "squat-offline-api-v1":
                if result.get("status") == "insufficient_pose":
                    raise AlgorithmError("QUALITY_INSUFFICIENT")
                if (result.get("status") != "usable_candidate"
                        or not isinstance(result.get("quality"), dict)
                        or result["quality"].get("status") != "usable_candidate"):
                    raise AlgorithmError("INVALID_ALGORITHM_RESULT")
                # Keep the business task status separate from evidence certainty.
                result = {
                    "status": "completed", "algorithm_version": result["version"],
                    "guidance": result.get("guidance"),
                    "results": {key: result[key] for key in (
                        "quality", "cycles", "features", "biomechanics", "limitations"
                    ) if key in result},
                }
            public = {key: result[key] for key in (
                "status", "algorithm_version", "results", "confidence", "guidance"
            ) if key in result}
            return validate_result(public)
        finally:
            output.unlink(missing_ok=True)
