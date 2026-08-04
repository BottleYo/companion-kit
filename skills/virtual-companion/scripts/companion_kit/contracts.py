from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class HostClass(str, Enum):
    EVENT = "event"
    DESKTOP = "desktop"


class DecisionKind(str, Enum):
    PASS_THROUGH = "pass_through"
    CLARIFY = "clarify"
    PHOTO_PLAN = "photo_plan"
    BLOCKED = "blocked"


class PhotoJobStage(str, Enum):
    PLANNED = "planned"
    AUTHORIZED = "authorized"
    GENERATING = "generating"
    GENERATED = "generated"
    DELIVERY_UNKNOWN = "delivery_unknown"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class RequestEnvelope:
    text: str
    session_id: str
    source: str


@dataclass(frozen=True)
class HostCapabilities:
    host_class: HostClass
    can_execute_tasks: bool
    can_generate_images: bool
    can_deliver_images: bool
    has_current_reply_target: bool
    can_attach_local_artifacts: bool


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    reason: str
    persona_context: str
    photo_prompt: str = ""
    delivery_mode: str = ""
    reference_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "reason": self.reason,
            "persona_context": self.persona_context,
            "photo_prompt": self.photo_prompt,
            "delivery_mode": self.delivery_mode,
            "reference_ids": list(self.reference_ids),
        }
