"""Evidence-bound coaching feedback built on the shared Agent runtime."""

from __future__ import annotations

import json
from typing import Any

from agent_core import AgentBuilder, NodeResult
from agent_core.models import ModelRequestError, model_from_environment
from agent_core.runtime import RunStatus


INSTRUCTIONS = (
    "You are a squat coaching assistant. Use only the supplied algorithm JSON as evidence. "
    "Return one JSON object with a short 'summary' string and a 'suggestions' array. "
    "Each suggestion has a 'text' string and a nonempty 'evidence_paths' array of JSON "
    "pointers into the algorithm object (for example /results/knee). Do not infer a fault "
    "from unknown, missing, or uncertain evidence. An empty suggestions array is valid. "
    "Do not include diagnosis, claims of certainty, or markdown."
    " Answer in Chinese. Candidate observations require human review and must not be "
    "presented as confirmed faults. Kinematic angles describe model estimates; "
    "they do not establish joint forces, injury risk, or a fault. Respect metric "
    "status and camera-view limitations even when other numeric fields exist. "
    "Keep the summary evidence-bound too."
)


def _evidence_at(data: dict[str, Any], pointer: str) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("invalid evidence pointer")
    value: Any = data
    for part in pointer[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and key.isdecimal() and int(key) < len(value):
            value = value[int(key)]
        else:
            raise ValueError("missing evidence")
    if (value is None or isinstance(value, (dict, list))
            or isinstance(value, str) and value.strip().lower() in
            {"unknown", "unavailable", "insufficient"}):
        raise ValueError("inconclusive evidence")
    return value


def _validate_feedback(raw: Any, algorithm: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {"summary", "suggestions"}:
        raise ValueError("invalid feedback shape")
    summary, suggestions = raw["summary"], raw["suggestions"]
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 500:
        raise ValueError("invalid summary")
    if not isinstance(suggestions, list) or len(suggestions) > 8:
        raise ValueError("invalid suggestions")
    for item in suggestions:
        if not isinstance(item, dict) or set(item) != {"text", "evidence_paths"}:
            raise ValueError("invalid suggestion")
        if not isinstance(item["text"], str) or not item["text"].strip() or len(item["text"]) > 500:
            raise ValueError("invalid suggestion text")
        paths = item["evidence_paths"]
        if not isinstance(paths, list) or not 1 <= len(paths) <= 8:
            raise ValueError("invalid evidence paths")
        for path in paths:
            _evidence_at(algorithm, path)
    return raw


class SquatFeedbackAgent:
    def __init__(self) -> None:
        model = model_from_environment()
        self.configured = model is not None
        if model is None:
            self.agent = None
            return

        def feedback(context):
            try:
                response = context.model.complete([
                    {"role": "system", "content": context.instructions},
                    {"role": "user", "content": json.dumps(context.input, ensure_ascii=False)},
                ])
            except ModelRequestError as exc:
                return NodeResult.failed(exc.reason_code)
            content = response.get("content")
            if not isinstance(content, str) or len(content) > 32768:
                return NodeResult.failed("AGENT_INVALID_RESPONSE")
            try:
                parsed = _validate_feedback(json.loads(content), context.input["algorithm"])
            except (ValueError, TypeError, KeyError):
                return NodeResult.failed("AGENT_INVALID_RESPONSE")
            return NodeResult.success({"feedback": parsed})

        self.agent = (AgentBuilder("squat-feedback", instructions=INSTRUCTIONS, model=model)
                      .add_node("feedback", feedback).set_entry("feedback").build())

    def run(self, *, exercise_id: str, camera_view: str, algorithm: dict[str, Any],
            run_id: str | None) -> tuple[dict[str, Any] | None, str | None]:
        if self.agent is None:
            return None, "AGENT_NOT_CONFIGURED"
        if run_id is None:
            raise ValueError("configured agent requires run_id")
        result = self.agent.run({"exercise_id": exercise_id,
                                 "camera_view": camera_view, "algorithm": algorithm}, run_id=run_id)
        if result.status is not RunStatus.COMPLETED:
            return None, result.reason_code or "AGENT_FAILED"
        return dict(result.state["feedback"]), None
