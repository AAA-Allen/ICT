"""Single-instance SQLite storage for uploaded videos and analysis tasks."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


LOG = logging.getLogger("fitness_backend.storage")
UPLOAD_TEMP_RE = re.compile(r"^\.[0-9a-f]{32}\.upload$")
RESULT_TEMP_RE = re.compile(r"^[0-9a-f]{32}\.json$")


class Storage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.videos = root / "videos"
        self.results = root / "results"
        self.videos.mkdir(parents=True, exist_ok=True)
        self.results.mkdir(parents=True, exist_ok=True)
        self.db = root / "fitness.sqlite3"
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS videos (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                    filename TEXT NOT NULL, content_type TEXT NOT NULL,
                    size INTEGER NOT NULL, sha256 TEXT NOT NULL,
                    path TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS video_upload_keys (
                    session_id TEXT NOT NULL, upload_key TEXT NOT NULL,
                    video_id TEXT NOT NULL REFERENCES videos(id),
                    PRIMARY KEY (session_id, upload_key)
                );
                CREATE TABLE IF NOT EXISTS analyses (
                    id TEXT PRIMARY KEY, video_id TEXT NOT NULL REFERENCES videos(id),
                    session_id TEXT NOT NULL, exercise_id TEXT NOT NULL,
                    camera_view TEXT NOT NULL, status TEXT NOT NULL,
                    reason_code TEXT, algorithm_json TEXT, agent_json TEXT,
                    agent_status TEXT NOT NULL DEFAULT 'NOT_AVAILABLE',
                    agent_run_id TEXT, agent_reason_code TEXT,
                    idempotency_key TEXT UNIQUE, cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(analyses)")}
            for name, definition in (
                ("agent_status", "TEXT NOT NULL DEFAULT 'NOT_AVAILABLE'"),
                ("agent_run_id", "TEXT"), ("agent_reason_code", "TEXT"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE analyses ADD COLUMN {name} {definition}")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def add_video(self, video: dict[str, Any], upload_key: str | None = None) -> tuple[dict[str, Any], bool]:
        with self.connect() as conn:
            if upload_key is not None:
                existing = conn.execute("""
                    SELECT v.* FROM video_upload_keys AS k
                    JOIN videos AS v ON v.id=k.video_id
                    WHERE k.session_id=? AND k.upload_key=?
                """, (video["session_id"], upload_key)).fetchone()
                if existing is not None:
                    return dict(existing), False
            try:
                conn.execute("""
                    INSERT INTO videos (id, session_id, filename, content_type, size, sha256, path)
                    VALUES (:id, :session_id, :filename, :content_type, :size, :sha256, :path)
                """, video)
                if upload_key is not None:
                    conn.execute("""
                        INSERT INTO video_upload_keys (session_id, upload_key, video_id)
                        VALUES (?, ?, ?)
                    """, (video["session_id"], upload_key, video["id"]))
            except sqlite3.IntegrityError:
                conn.rollback()
                if upload_key is None:
                    raise
                existing = conn.execute("""
                    SELECT v.* FROM video_upload_keys AS k
                    JOIN videos AS v ON v.id=k.video_id
                    WHERE k.session_id=? AND k.upload_key=?
                """, (video["session_id"], upload_key)).fetchone()
                if existing is None:
                    raise
                return dict(existing), False
        return video, True

    def video(self, video_id: str, session_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM videos WHERE id=? AND session_id=?", (video_id, session_id)
            ).fetchone()
        return dict(row) if row else None

    def clean_interrupted_files(self) -> None:
        for directory, pattern in ((self.videos, UPLOAD_TEMP_RE),
                                   (self.results, RESULT_TEMP_RE)):
            for path in directory.iterdir():
                if path.is_file() and pattern.fullmatch(path.name):
                    try:
                        path.unlink()
                    except OSError:
                        LOG.warning("could not remove interrupted temporary file: %s", path)

    def add_analysis(self, task: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        with self.connect() as conn:
            try:
                conn.execute("""
                    INSERT INTO analyses (id, video_id, session_id, exercise_id, camera_view,
                                          status, idempotency_key)
                    VALUES (:id, :video_id, :session_id, :exercise_id, :camera_view,
                            :status, :idempotency_key)
                """, task)
            except sqlite3.IntegrityError:
                if task["idempotency_key"] is None:
                    raise
                existing = conn.execute(
                    "SELECT * FROM analyses WHERE idempotency_key=?", (task["idempotency_key"],)
                ).fetchone()
                if existing is None:
                    raise
                return self._decode(existing), False
        return self.analysis(task["id"], task["session_id"]), True

    def analysis(self, task_id: str, session_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM analyses WHERE id=? AND session_id=?", (task_id, session_id)
            ).fetchone()
        return self._decode(row) if row else None

    def list_analyses(self, session_id: str, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        with self.connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM analyses WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            rows = conn.execute("""
                SELECT id, video_id, exercise_id, camera_view, status, reason_code,
                       cancel_requested, created_at, updated_at
                FROM analyses WHERE session_id=?
                ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?
            """, (session_id, limit, offset)).fetchall()
        return [dict(row) for row in rows], total

    def transition(self, task_id: str, from_status: str, to_status: str,
                   *, reason_code: str | None = None,
                   algorithm: dict[str, Any] | None = None) -> bool:
        with self.connect() as conn:
            changed = conn.execute("""
                UPDATE analyses SET status=?, reason_code=?, algorithm_json=?,
                       updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status=? AND cancel_requested=0
            """, (to_status, reason_code,
                  json.dumps(algorithm, ensure_ascii=False, allow_nan=False) if algorithm is not None else None,
                  task_id, from_status)).rowcount
        return bool(changed)

    def start_agent(self, task_id: str, algorithm: dict[str, Any], run_id: str | None) -> bool:
        with self.connect() as conn:
            changed = conn.execute("""
                UPDATE analyses SET status='AGENT_PROCESSING', algorithm_json=?,
                    agent_status=?, agent_run_id=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='ANALYZING' AND cancel_requested=0
            """, (json.dumps(algorithm, ensure_ascii=False, allow_nan=False),
                  'RUNNING' if run_id else 'NOT_CONFIGURED', run_id, task_id)).rowcount
        return bool(changed)

    def finish_agent(self, task_id: str, agent: dict[str, Any] | None,
                     reason_code: str | None) -> bool:
        with self.connect() as conn:
            changed = conn.execute("""
                UPDATE analyses SET status='COMPLETED', agent_json=?,
                    agent_status=?, agent_reason_code=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='AGENT_PROCESSING' AND cancel_requested=0
            """, (json.dumps(agent, ensure_ascii=False, allow_nan=False) if agent is not None else None,
                  'COMPLETED' if agent is not None else 'NOT_CONFIGURED' if reason_code == 'AGENT_NOT_CONFIGURED' else 'FAILED',
                  reason_code, task_id)).rowcount
        return bool(changed)

    def cancel(self, task_id: str, session_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            conn.execute("""
                UPDATE analyses SET cancel_requested=1,
                    status=CASE WHEN status='QUEUED' THEN 'CANCELED' ELSE status END,
                    reason_code=CASE WHEN status='QUEUED' THEN 'USER_CANCELED' ELSE reason_code END,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND session_id=? AND status IN ('QUEUED','ANALYZING','AGENT_PROCESSING')
            """, (task_id, session_id))
        return self.analysis(task_id, session_id)

    def finish_cancel(self, task_id: str) -> bool:
        with self.connect() as conn:
            changed = conn.execute("""
                UPDATE analyses SET status='CANCELED', reason_code='USER_CANCELED',
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status IN ('ANALYZING','AGENT_PROCESSING') AND cancel_requested=1
            """, (task_id,)).rowcount
        return bool(changed)

    def cancel_requested(self, task_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT cancel_requested FROM analyses WHERE id=?", (task_id,)
            ).fetchone()
        return bool(row and row[0])

    def recover_interrupted(self) -> None:
        with self.connect() as conn:
            conn.execute("""
                UPDATE analyses SET status='CANCELED', reason_code='USER_CANCELED',
                    updated_at=CURRENT_TIMESTAMP
                WHERE status IN ('QUEUED','ANALYZING','AGENT_PROCESSING') AND cancel_requested=1
            """)
            conn.execute("""
                UPDATE analyses SET status='FAILED', reason_code='SERVER_RESTARTED',
                    updated_at=CURRENT_TIMESTAMP
                WHERE status IN ('QUEUED','ANALYZING')
            """)
            conn.execute("""
                UPDATE analyses SET status='COMPLETED', agent_status='FAILED',
                    agent_reason_code='SERVER_RESTARTED', updated_at=CURRENT_TIMESTAMP
                WHERE status='AGENT_PROCESSING'
            """)

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["algorithm"] = json.loads(item.pop("algorithm_json")) if item["algorithm_json"] else None
        item["agent"] = json.loads(item.pop("agent_json")) if item["agent_json"] else None
        item["cancel_requested"] = bool(item["cancel_requested"])
        return item
