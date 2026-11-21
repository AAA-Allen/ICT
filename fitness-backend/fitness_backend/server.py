"""Local first fitness API for one real squat analysis workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from agent_core.server import StoreInstanceLock

from .algorithm import AlgorithmError, SquatAlgorithm, validate_result
from .agent import SquatFeedbackAgent
from .storage import Storage
from .speech import SpeechError, SpeechSynthesizer


LOG = logging.getLogger("fitness_backend")
MAX_VIDEO_BYTES = 100 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024
UPLOAD_IDLE_TIMEOUT_SECONDS = 15
UPLOAD_TOTAL_TIMEOUT_SECONDS = 180
SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
TASK_RE = re.compile(r"^/api/analyses/([0-9a-f]{32})(/cancel)?$")
VIDEO_RE = re.compile(r"^/api/videos/([0-9a-f]{32})$")
VIDEO_TYPES = {"video/mp4": ".mp4", "video/webm": ".webm"}
CAMERA_VIEWS = {"front", "side", "oblique", "front_or_oblique", "unknown"}
ALLOWED_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class FitnessServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], data_dir: Path) -> None:
        if address[0] not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("fitness backend currently supports loopback binding only")
        self._instance_lock = StoreInstanceLock(data_dir)
        try:
            self._instance_lock.acquire()
        except RuntimeError as exc:
            raise RuntimeError(f"another fitness backend instance is using {data_dir}") from exc
        try:
            self.storage = Storage(data_dir)
            self.storage.recover_interrupted()
            self.storage.clean_interrupted_files()
            self.algorithm = SquatAlgorithm()
            self.feedback_agent = SquatFeedbackAgent()
            self.speech = SpeechSynthesizer()
            self.workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fitness-analysis")
            super().__init__(address, FitnessHandler)
        except Exception:
            if hasattr(self, "workers"):
                self.workers.shutdown(wait=True, cancel_futures=True)
            self._instance_lock.release()
            raise

    def server_close(self) -> None:
        try:
            self.workers.shutdown(wait=True, cancel_futures=True)
            super().server_close()
        finally:
            self._instance_lock.release()

    def analyze(self, task_id: str, session_id: str) -> None:
        task = self.storage.analysis(task_id, session_id)
        if task is None or not self.storage.transition(task_id, "QUEUED", "ANALYZING"):
            return
        video = self.storage.video(task["video_id"], session_id)
        if video is None:
            self.storage.transition(task_id, "ANALYZING", "FAILED", reason_code="VIDEO_NOT_FOUND")
            return
        video_path = Path(video["path"])
        if not video_path.is_file():
            if not self.storage.transition(task_id, "ANALYZING", "FAILED",
                                           reason_code="VIDEO_UNAVAILABLE"):
                self.storage.finish_cancel(task_id)
            return
        try:
            result = self.algorithm.analyze(
                video=video_path, output=self.storage.results / f"{task_id}.json",
                exercise_id=task["exercise_id"], camera_view=task["camera_view"],
                cancelled=lambda: self.storage.cancel_requested(task_id),
            )
            result = validate_result(result)
        except AlgorithmError as exc:
            if exc.code == "USER_CANCELED":
                self.storage.finish_cancel(task_id)
            else:
                if not self.storage.transition(task_id, "ANALYZING", "FAILED", reason_code=exc.code):
                    self.storage.finish_cancel(task_id)
            return
        except Exception:
            LOG.exception("analysis task %s failed", task_id)
            if not self.storage.transition(task_id, "ANALYZING", "FAILED", reason_code="ALGORITHM_FAILED"):
                self.storage.finish_cancel(task_id)
            return
        run_id = uuid4().hex if self.feedback_agent.configured else None
        if not self.storage.start_agent(task_id, result, run_id):
            self.storage.finish_cancel(task_id)
            return
        try:
            feedback, reason = self.feedback_agent.run(
                exercise_id=task["exercise_id"], camera_view=task["camera_view"],
                algorithm=result, run_id=run_id)
        except Exception:
            LOG.exception("agent run %s failed", run_id)
            feedback, reason = None, "AGENT_FAILED"
        if not self.storage.finish_agent(task_id, feedback, reason):
            self.storage.finish_cancel(task_id)


class FitnessHandler(BaseHTTPRequestHandler):
    server: FitnessServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, error: ApiError) -> None:
        self.close_connection = True
        self._send(error.status, {"error": {"code": error.code, "message": error.message}})

    def _guard_origin(self) -> None:
        origin = self.headers.get("Origin")
        if origin and origin not in ALLOWED_ORIGINS:
            raise ApiError(HTTPStatus.FORBIDDEN, "FORBIDDEN_ORIGIN", "origin is not allowed")

    def _session(self) -> str:
        value = self.headers.get("X-Session-Id", "")
        if not SESSION_RE.fullmatch(value):
            raise ApiError(HTTPStatus.BAD_REQUEST, "INVALID_SESSION_ID", "X-Session-Id is required")
        return value

    def _length(self, limit: int) -> int:
        raw = self.headers.get("Content-Length")
        if raw is None:
            raise ApiError(HTTPStatus.LENGTH_REQUIRED, "CONTENT_LENGTH_REQUIRED", "Content-Length is required")
        try:
            value = int(raw)
        except ValueError:
            raise ApiError(HTTPStatus.BAD_REQUEST, "INVALID_CONTENT_LENGTH", "invalid Content-Length")
        if value < 1 or value > limit:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "BODY_SIZE_INVALID", "request body size is invalid")
        return value

    def _json(self) -> dict:
        length = self._length(MAX_JSON_BYTES)
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError(HTTPStatus.BAD_REQUEST, "INVALID_JSON", "invalid JSON body")
        if not isinstance(value, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "INVALID_JSON", "JSON body must be an object")
        return value

    def do_OPTIONS(self) -> None:  # noqa: N802
        try:
            self._guard_origin()
            self.send_response(HTTPStatus.NO_CONTENT)
            origin = self.headers.get("Origin")
            if origin in ALLOWED_ORIGINS:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Session-Id, X-Filename, X-Upload-Key")
                self.send_header("Vary", "Origin")
            self.send_header("Content-Length", "0")
            self.end_headers()
        except ApiError as exc:
            self._error(exc)

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        try:
            if path == "/api/health":
                self._send(HTTPStatus.OK, {
                    "status": "ok", "algorithm_configured": self.server.algorithm.configured,
                    "agent_configured": self.server.feedback_agent.configured,
                    "speech_configured": self.server.speech.configured,
                })
            elif path == "/api/analyses":
                query = parse_qs(urlsplit(self.path).query)
                limit = self._page_number(query, "limit", 20, 1, 100)
                offset = self._page_number(query, "offset", 0, 0, 1_000_000)
                tasks, total = self.server.storage.list_analyses(self._session(), limit, offset)
                self._send(HTTPStatus.OK, {
                    "tasks": [{"task_id": item["id"],
                               **{key: item[key] for key in (
                                   "video_id", "exercise_id", "camera_view", "status",
                                   "reason_code", "created_at", "updated_at")},
                               "cancel_requested": bool(item["cancel_requested"])} for item in tasks],
                    "total": total, "limit": limit, "offset": offset,
                })
            elif match := VIDEO_RE.fullmatch(path):
                video = self.server.storage.video(match.group(1), self._session())
                if video is None:
                    raise ApiError(HTTPStatus.NOT_FOUND, "VIDEO_NOT_FOUND", "video not found")
                self._send(HTTPStatus.OK, {"video": self._public_video(video)})
            elif match := TASK_RE.fullmatch(path):
                if match.group(2):
                    raise ApiError(HTTPStatus.NOT_FOUND, "NOT_FOUND", "unknown endpoint")
                task = self.server.storage.analysis(match.group(1), self._session())
                if task is None:
                    raise ApiError(HTTPStatus.NOT_FOUND, "TASK_NOT_FOUND", "analysis task not found")
                self._send(HTTPStatus.OK, {"task": self._public_task(task)})
            else:
                raise ApiError(HTTPStatus.NOT_FOUND, "NOT_FOUND", "unknown endpoint")
        except ApiError as exc:
            self._error(exc)
        except Exception:
            LOG.exception("GET %s failed", path)
            self._error(ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", "internal server error"))

    @staticmethod
    def _page_number(query: dict, name: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(query[name][0]) if name in query else default
        except (ValueError, IndexError):
            raise ApiError(HTTPStatus.BAD_REQUEST, "INVALID_QUERY", f"{name} must be an integer")
        if not minimum <= value <= maximum:
            raise ApiError(HTTPStatus.BAD_REQUEST, "INVALID_QUERY", f"{name} is out of range")
        return value

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        try:
            self._guard_origin()
            if path == "/api/videos":
                self._upload()
            elif path == "/api/speech":
                self._session()
                body = self._json()
                try:
                    audio = self.server.speech.synthesize(body.get("text"))
                except SpeechError as exc:
                    code = str(exc)
                    status = (HTTPStatus.BAD_REQUEST if code == "SPEECH_INVALID_TEXT"
                              else HTTPStatus.SERVICE_UNAVAILABLE if code == "SPEECH_NOT_CONFIGURED"
                              else HTTPStatus.BAD_GATEWAY)
                    raise ApiError(status, code, code) from None
                self._send(HTTPStatus.OK, audio)
            elif path == "/api/analyses":
                self._create_analysis()
            elif (match := TASK_RE.fullmatch(path)) and match.group(2) == "/cancel":
                task = self.server.storage.cancel(match.group(1), self._session())
                if task is None:
                    raise ApiError(HTTPStatus.NOT_FOUND, "TASK_NOT_FOUND", "analysis task not found")
                self._send(HTTPStatus.OK, {"task": self._public_task(task)})
            else:
                raise ApiError(HTTPStatus.NOT_FOUND, "NOT_FOUND", "unknown endpoint")
        except ApiError as exc:
            self._error(exc)
        except Exception:
            LOG.exception("POST %s failed", path)
            self._error(ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", "internal server error"))

    def _upload(self) -> None:
        session_id = self._session()
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].lower()
        extension = VIDEO_TYPES.get(content_type)
        if extension is None:
            raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "UNSUPPORTED_VIDEO_TYPE", "use video/mp4 or video/webm")
        length = self._length(MAX_VIDEO_BYTES)
        filename = self.headers.get("X-Filename", "upload" + extension)
        if len(filename) > 128 or any(ord(char) < 32 or char in "/\\" for char in filename):
            raise ApiError(HTTPStatus.BAD_REQUEST, "INVALID_FILENAME", "invalid video filename")
        upload_key = self.headers.get("X-Upload-Key")
        if upload_key is not None and not SESSION_RE.fullmatch(upload_key):
            raise ApiError(HTTPStatus.BAD_REQUEST, "INVALID_UPLOAD_KEY", "X-Upload-Key is invalid")
        video_id = uuid4().hex
        target = self.server.storage.videos / f"{video_id}{extension}"
        temporary = self.server.storage.videos / f".{video_id}.upload"
        digest = hashlib.sha256()
        remaining = length
        signature = bytearray()
        signature_size = 8 if extension == ".mp4" else 4
        deadline = time.monotonic() + UPLOAD_TOTAL_TIMEOUT_SECONDS
        previous_timeout = self.connection.gettimeout()
        try:
            with temporary.open("xb") as stream:
                while remaining:
                    remaining_time = deadline - time.monotonic()
                    if remaining_time <= 0:
                        raise ApiError(HTTPStatus.REQUEST_TIMEOUT, "UPLOAD_TIMEOUT", "upload timed out")
                    self.connection.settimeout(min(UPLOAD_IDLE_TIMEOUT_SECONDS, remaining_time))
                    try:
                        chunk = self.rfile.read1(min(1024 * 1024, remaining))
                    except TimeoutError as exc:
                        raise ApiError(HTTPStatus.REQUEST_TIMEOUT, "UPLOAD_TIMEOUT", "upload timed out") from exc
                    if not chunk:
                        raise ApiError(HTTPStatus.BAD_REQUEST, "INCOMPLETE_UPLOAD", "upload ended early")
                    if len(signature) < signature_size:
                        signature.extend(chunk[:signature_size - len(signature)])
                    if len(signature) >= signature_size:
                        valid = signature[4:8] == b"ftyp" if extension == ".mp4" else signature == b"\x1a\x45\xdf\xa3"
                        if not valid:
                            raise ApiError(HTTPStatus.BAD_REQUEST, "INVALID_VIDEO", "video signature does not match Content-Type")
                    stream.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
            if len(signature) < signature_size:
                raise ApiError(HTTPStatus.BAD_REQUEST, "INVALID_VIDEO", "video signature does not match Content-Type")
            temporary.replace(target)
            video, created = self.server.storage.add_video({
                "id": video_id, "session_id": session_id, "filename": filename,
                "content_type": content_type, "size": length,
                "sha256": digest.hexdigest(), "path": str(target.resolve()),
            }, upload_key)
            if not created:
                target.unlink()
                if not Path(video["path"]).is_file():
                    raise ApiError(HTTPStatus.CONFLICT, "VIDEO_UNAVAILABLE",
                                   "previously uploaded video is unavailable")
                if (video["size"] != length or video["content_type"] != content_type
                        or video["sha256"] != digest.hexdigest()):
                    raise ApiError(HTTPStatus.CONFLICT, "UPLOAD_KEY_CONFLICT",
                                   "upload key was used with different video content")
        except Exception:
            temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise
        finally:
            self.connection.settimeout(previous_timeout)
        self._send(HTTPStatus.CREATED if created else HTTPStatus.OK, {
            "video_id": video["id"], "size": video["size"],
            "content_type": video["content_type"], "sha256": video["sha256"],
            "reused": not created,
        })

    def _create_analysis(self) -> None:
        session_id = self._session()
        body = self._json()
        video_id = body.get("video_id")
        if not isinstance(video_id, str) or not re.fullmatch(r"[0-9a-f]{32}", video_id):
            raise ApiError(HTTPStatus.BAD_REQUEST, "INVALID_VIDEO_ID", "video_id is invalid")
        video = self.server.storage.video(video_id, session_id)
        if video is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "VIDEO_NOT_FOUND", "video not found")
        if not Path(video["path"]).is_file():
            raise ApiError(HTTPStatus.CONFLICT, "VIDEO_UNAVAILABLE", "uploaded video is unavailable")
        exercise_id = body.get("exercise_id")
        if exercise_id not in ("normal_squat", "wide_squat", "narrow_squat"):
            raise ApiError(HTTPStatus.BAD_REQUEST, "UNSUPPORTED_EXERCISE", "unsupported squat exercise")
        camera_view = body.get("camera_view", "unknown")
        if not isinstance(camera_view, str) or camera_view not in CAMERA_VIEWS:
            raise ApiError(HTTPStatus.BAD_REQUEST, "INVALID_CAMERA_VIEW", "camera_view is invalid")
        key = body.get("idempotency_key")
        if key is not None and (not isinstance(key, str) or not SESSION_RE.fullmatch(key)):
            raise ApiError(HTTPStatus.BAD_REQUEST, "INVALID_IDEMPOTENCY_KEY", "idempotency_key is invalid")
        task, created = self.server.storage.add_analysis({
            "id": uuid4().hex, "video_id": video_id, "session_id": session_id,
            "exercise_id": exercise_id, "camera_view": camera_view,
            "status": "QUEUED", "idempotency_key": f"{session_id}:{key}" if key else None,
        })
        if not created and (task["video_id"] != video_id or task["exercise_id"] != exercise_id
                            or task["camera_view"] != camera_view):
            raise ApiError(HTTPStatus.CONFLICT, "IDEMPOTENCY_CONFLICT", "key was used with different parameters")
        if created:
            try:
                self.server.workers.submit(self.server.analyze, task["id"], session_id)
            except RuntimeError:
                LOG.exception("could not schedule analysis task %s", task["id"])
                self.server.storage.transition(task["id"], "QUEUED", "FAILED",
                                               reason_code="SCHEDULING_FAILED")
                task = self.server.storage.analysis(task["id"], session_id)
        self._send(HTTPStatus.ACCEPTED if created else HTTPStatus.OK,
                   {"task": self._public_task(task), "reused": not created})

    @staticmethod
    def _public_video(video: dict) -> dict:
        return {
            "video_id": video["id"], "filename": video["filename"],
            "content_type": video["content_type"], "size": video["size"],
            "sha256": video["sha256"], "created_at": video["created_at"],
            "available": Path(video["path"]).is_file(),
        }

    @staticmethod
    def _public_task(task: dict) -> dict:
        return {
            "task_id": task["id"], "video_id": task["video_id"],
            "exercise_id": task["exercise_id"], "camera_view": task["camera_view"],
            "status": task["status"], "reason_code": task["reason_code"],
            "algorithm": task["algorithm"], "agent": task["agent"],
            "agent_status": task["agent_status"],
            "agent_run_id": task["agent_run_id"],
            "agent_reason_code": task["agent_reason_code"],
            "cancel_requested": task["cancel_requested"],
            "created_at": task["created_at"], "updated_at": task["updated_at"],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local fitness video analysis API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8082)
    parser.add_argument("--data-dir", type=Path, default=Path("outputs/fitness_backend"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    server = FitnessServer((args.host, args.port), args.data_dir)
    LOG.info("fitness backend listening on http://%s:%s", *server.server_address[:2])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
