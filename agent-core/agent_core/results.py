"""Public result contracts for the graph runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from enum import Enum
from typing import Any, Mapping
from .approval_display import public_request


class NodeStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class NodeResult:
    """The only value a graph node may return to the runtime.

    ``updates`` is committed only for ``SUCCEEDED`` results. ``simulated`` is
    retained in the run result and never implies that an external side effect
    actually occurred.
    """

    status: NodeStatus
    updates: Mapping[str, Any] = field(default_factory=dict)
    route: str | None = None
    reason_code: str | None = None
    output: Any = None
    simulated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.status, NodeStatus):
            raise TypeError("status must be a NodeStatus")
        if not isinstance(self.updates, Mapping):
            raise TypeError("updates must be a mapping")
        if self.route is not None and (
            not isinstance(self.route, str) or not self.route.strip()
        ):
            raise ValueError("route must be a nonempty string when provided")
        if self.reason_code is not None and (
            not isinstance(self.reason_code, str) or not self.reason_code.strip()
        ):
            raise ValueError("reason_code must be a nonempty string when provided")
        if self.status is NodeStatus.SUCCEEDED and self.reason_code is not None:
            raise ValueError("successful nodes cannot provide a reason_code")
        if self.status is not NodeStatus.SUCCEEDED and not self.reason_code:
            raise ValueError("non-successful nodes require a reason_code")
        if self.status is not NodeStatus.SUCCEEDED and self.route is not None:
            raise ValueError("blocked or failed nodes cannot provide a route")
        if self.status is NodeStatus.WAITING_APPROVAL:
            if self.reason_code != "APPROVAL_REQUIRED" or not isinstance(self.output, Mapping):
                raise ValueError("waiting approval requires a request and APPROVAL_REQUIRED")
            required = ("request_id", "tool_name", "argument_digest")
            if any(not isinstance(self.output.get(key), str) or not self.output[key].strip()
                   for key in required):
                raise ValueError("waiting approval request is incomplete")
        if not isinstance(self.simulated, bool):
            raise TypeError("simulated must be a bool")
        object.__setattr__(self, "updates", deepcopy(dict(self.updates)))
        if self.status is NodeStatus.WAITING_APPROVAL:
            object.__setattr__(self, "output", public_request(self.output))
        else:
            object.__setattr__(self, "output", deepcopy(self.output))

    @classmethod
    def success(
        cls,
        updates: Mapping[str, Any] | None = None,
        *,
        route: str | None = None,
        output: Any = None,
        simulated: bool = False,
    ) -> "NodeResult":
        return cls(NodeStatus.SUCCEEDED, updates or {}, route, None, output, simulated)

    @classmethod
    def blocked(cls, reason_code: str, *, output: Any = None) -> "NodeResult":
        return cls(NodeStatus.BLOCKED, {}, None, reason_code, output, False)

    @classmethod
    def failed(cls, reason_code: str, *, output: Any = None) -> "NodeResult":
        return cls(NodeStatus.FAILED, {}, None, reason_code, output, False)

    @classmethod
    def waiting_approval(cls, request: Mapping[str, Any]) -> "NodeResult":
        if not isinstance(request, Mapping):
            raise TypeError("approval request must be a mapping")
        required = ("request_id", "tool_name", "argument_digest")
        if any(not isinstance(request.get(key), str) or not request[key].strip() for key in required):
            raise ValueError("approval request requires request_id, tool_name and argument_digest")
        return cls(NodeStatus.WAITING_APPROVAL, reason_code="APPROVAL_REQUIRED",
                   output=public_request(request))
