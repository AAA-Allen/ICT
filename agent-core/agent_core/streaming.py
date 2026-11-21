"""In-process token streaming bus between model adapters and SSE consumers.

Nodes publish model deltas keyed by ``run_id``; the HTTP layer replays the
run's retained history on connect and follows live. Cursors survive eviction
and node transitions; readers receive an explicit gap when history is lost.
Event counts, payload bytes, cached runs and finished retention are bounded.
Everything here is process-local by design.
"""

from __future__ import annotations

import threading
import re
from uuid import uuid4
from collections import deque
from time import monotonic
import math

MAX_HISTORY_PER_RUN = 10_000


class StreamLimitError(Exception):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


class TokenBus:
    """Fan-out buffer of model token deltas per run."""

    def __init__(self, *, max_history: int = MAX_HISTORY_PER_RUN, max_runs: int = 128,
                 max_event_bytes: int = 262_144, max_run_bytes: int = 4_194_304,
                 max_total_bytes: int = 33_554_432, retention_seconds: float = 300,
                 clock=monotonic) -> None:
        for value in (max_history, max_runs, max_event_bytes, max_run_bytes, max_total_bytes):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError("stream capacities must be positive integers")
        if not max_event_bytes <= max_run_bytes <= max_total_bytes:
            raise ValueError("event <= run <= total byte capacities required")
        if (not isinstance(retention_seconds, (int, float)) or isinstance(retention_seconds, bool)
                or not math.isfinite(retention_seconds) or retention_seconds < 0):
            raise ValueError("retention_seconds must be finite and nonnegative")
        if not callable(clock):
            raise ValueError("clock must be callable")
        self._max_history = max_history
        self._max_runs, self._max_event_bytes = max_runs, max_event_bytes
        self._max_run_bytes, self._max_total_bytes = max_run_bytes, max_total_bytes
        self._retention, self._clock = retention_seconds, clock
        self._bytes = 0
        self._runs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _remove(self, run_id):
        self._bytes -= self._runs.pop(run_id)["bytes"]

    def _prune(self):
        now = self._clock()
        for run_id, entry in list(self._runs.items()):
            if not entry["active"] and now - entry["finished_at"] >= self._retention:
                self._remove(run_id)

    def _reclaim(self, *, incoming_bytes=0, incoming_run=False, exclude=None):
        candidates = sorted((entry["finished_at"], run_id) for run_id, entry in self._runs.items()
                            if not entry["active"] and run_id != exclude)
        while (self._bytes + incoming_bytes > self._max_total_bytes or
               len(self._runs) + int(incoming_run) > self._max_runs):
            if not candidates:
                raise StreamLimitError("STREAM_CAPACITY_EXCEEDED")
            _, run_id = candidates.pop(0)
            self._remove(run_id)

    def _drop_left(self, entry):
        _, node, text = entry["events"].popleft()
        size = len(node.encode("utf-8")) + len(text.encode("utf-8"))
        entry["bytes"] -= size
        self._bytes -= size
        entry["truncated"] = True

    def open(self, run_id: str) -> None:
        """Begin a model node's stream, retaining earlier nodes' history."""
        with self._lock:
            self._prune()
            if run_id not in self._runs:
                self._reclaim(incoming_run=True)
            entry = self._runs.setdefault(run_id, {
                "events": deque(),
                "truncated": False,
                "closed": False,
                "epoch": uuid4().hex,
                "sequence": 0,
                "bytes": 0,
                "active": True,
                "finished_at": None,
            })
            entry["closed"] = False
            entry["active"] = True
            entry["finished_at"] = None

    def publish(self, run_id: str, node_id: str, text: str) -> None:
        with self._lock:
            self._prune()
            entry = self._runs.get(run_id)
            if entry is None or entry["closed"]:
                return
            size = len(node_id.encode("utf-8")) + len(text.encode("utf-8"))
            if size > self._max_event_bytes:
                raise StreamLimitError("STREAM_EVENT_TOO_LARGE")
            removed_bytes = removed_events = 0
            for _, node, previous in entry["events"]:
                if (len(entry["events"]) - removed_events < self._max_history and
                        entry["bytes"] - removed_bytes + size <= self._max_run_bytes):
                    break
                removed_bytes += len(node.encode("utf-8")) + len(previous.encode("utf-8"))
                removed_events += 1
            # Admit before mutating this active window: a rejected chunk must
            # not discard its previously accepted history.
            self._reclaim(incoming_bytes=size - removed_bytes, exclude=run_id)
            for _ in range(removed_events):
                self._drop_left(entry)
            entry["sequence"] += 1
            entry["events"].append((entry["sequence"], node_id, text))
            entry["bytes"] += size
            self._bytes += size

    def close(self, run_id: str, *, retain_active: bool = False) -> None:
        with self._lock:
            entry = self._runs.get(run_id)
            if entry is not None:
                entry["closed"] = True
                if not retain_active:
                    entry["active"] = False
                    entry["finished_at"] = self._clock()
            self._prune()

    def forget(self, run_id: str) -> None:
        with self._lock:
            if run_id in self._runs:
                self._remove(run_id)

    def stats(self) -> dict:
        """Payload accounting after lazy expiry; excludes Python object overhead."""
        with self._lock:
            self._prune()
            return {"runs": len(self._runs), "active_runs": sum(e["active"] for e in self._runs.values()),
                    "events": sum(len(e["events"]) for e in self._runs.values()), "payload_bytes": self._bytes}

    def history(self, run_id: str, *, after: int = 0) -> tuple[list[tuple[str, str]], bool, bool]:
        """Return events after absolute sequence ``after``, plus (truncated, closed)."""
        with self._lock:
            self._prune()
            entry = self._runs.get(run_id)
            if entry is None:
                return [], False, False
            events = list(entry["events"])
            return [(node, text) for sequence, node, text in events if sequence > after], entry["truncated"], entry["closed"]

    def read(self, run_id: str, *, after: str | None = None) -> dict:
        """Read a bounded window with opaque, incarnation-scoped cursors.

        A gap cursor points immediately before the retained window. Missing
        memory clears it, so neither a restart nor forget implies replay.
        """
        parsed = None
        if after is not None:
            if not isinstance(after, str) or re.fullmatch(r"[0-9a-f]{32}:(0|[1-9][0-9]{0,19})", after) is None:
                raise ValueError("invalid stream cursor")
            epoch, sequence = after.split(":")
            parsed = (epoch, int(sequence))
        with self._lock:
            self._prune()
            entry = self._runs.get(run_id)
            if entry is None:
                return {"events": [], "cursor": None, "gap": "HISTORY_UNAVAILABLE" if parsed else None,
                        "closed": False}
            epoch = entry["epoch"]
            first = entry["events"][0][0] if entry["events"] else entry["sequence"] + 1
            position = parsed[1] if parsed else 0
            gap = None
            if parsed and parsed[0] != epoch:
                gap = "STREAM_RESET"
            elif position > entry["sequence"]:
                gap = "CURSOR_AHEAD"
            elif position < first - 1:
                gap = "HISTORY_TRUNCATED"
            if gap:
                position = first - 1
            return {"events": [{"cursor": f"{epoch}:{sequence}", "node_id": node, "text": text}
                               for sequence, node, text in entry["events"] if sequence > position],
                    "cursor": f"{epoch}:{position}", "gap": gap, "closed": entry["closed"]}


_BUS: TokenBus | None = None
_BUS_LOCK = threading.Lock()


def default_bus() -> TokenBus:
    """Process-wide bus shared by nodes and the HTTP layer."""
    global _BUS
    with _BUS_LOCK:
        if _BUS is None:
            _BUS = TokenBus()
    return _BUS


def finish_run_stream(run_id: str) -> None:
    """Release an existing stream at a runtime boundary without creating a bus."""
    if _BUS is not None:
        _BUS.close(run_id)


def configure_default_bus(**limits) -> TokenBus:
    """Configure at application startup, before creating stream producers."""
    global _BUS
    candidate = TokenBus(**limits)
    with _BUS_LOCK:
        if _BUS is not None and _BUS.stats()["runs"]:
            raise ValueError("configure stream limits before opening streams")
        _BUS = candidate
    return candidate


__all__ = ["TokenBus", "StreamLimitError", "default_bus", "configure_default_bus", "MAX_HISTORY_PER_RUN"]
