from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
import re


SCORE_MIN = 0
SCORE_MAX = 100
CONFIDENCE_THRESHOLD = 0.80
RELATIONSHIP_EVENT_SCHEMA_VERSION = 1
_EVENT_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{3,127}$")
_OPAQUE_SCOPE_RE = re.compile(r"^[a-f0-9]{16,64}$")
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_HOSTS = {"codex", "openclaw", "hermes", "claude"}
_STARTING_MODES = {"natural", "familiar"}


class RelationshipError(ValueError):
    """关系事件或状态不满足公开协议。"""


def validate_source_host(value: str) -> str:
    if value not in _HOSTS:
        raise RelationshipError("source_host 不受支持")
    return value


def validate_scope_id(value: str) -> str:
    if not _OPAQUE_SCOPE_RE.fullmatch(value):
        raise RelationshipError("scope_id 必须是哈希后的不透明标识")
    return value


class RelationshipEventType(str, Enum):
    WARM_EXCHANGE = "warm_exchange"
    EXPLICIT_APPRECIATION = "explicit_appreciation"
    PREFERENCE_RESPECTED = "preference_respected"
    BOUNDARY_RESPECTED = "boundary_respected"
    EXPLICIT_DISCOMFORT = "explicit_discomfort"
    EXPLICIT_DISTANCE = "explicit_distance"
    REPAIR = "repair"


class Atmosphere(str, Enum):
    NEUTRAL = "neutral"
    PLAYFUL = "playful"
    TENDER = "tender"
    STRAINED = "strained"
    REPAIR = "repair"


@dataclass(frozen=True)
class RelationshipPolicy:
    starting_mode: str = "natural"
    romance_enabled: bool = False

    def __post_init__(self) -> None:
        if self.starting_mode not in _STARTING_MODES:
            raise RelationshipError("starting_mode 只支持 natural 或 familiar")
        if not isinstance(self.romance_enabled, bool):
            raise RelationshipError("romance_enabled 必须是布尔值")


@dataclass(frozen=True)
class RelationshipDelta:
    familiarity: int = 0
    trust: int = 0
    closeness: int = 0

    def __add__(self, other: "RelationshipDelta") -> "RelationshipDelta":
        return RelationshipDelta(
            familiarity=self.familiarity + other.familiarity,
            trust=self.trust + other.trust,
            closeness=self.closeness + other.closeness,
        )


@dataclass(frozen=True)
class RelationshipState:
    familiarity: int
    trust: int
    closeness: int
    revision: int
    updated_at: datetime

    def __post_init__(self) -> None:
        for label, value in (
            ("familiarity", self.familiarity),
            ("trust", self.trust),
            ("closeness", self.closeness),
        ):
            if not isinstance(value, int) or not SCORE_MIN <= value <= SCORE_MAX:
                raise RelationshipError(f"{label} 必须是 0–100 的整数")
        if not isinstance(self.revision, int) or self.revision < 0:
            raise RelationshipError("revision 必须是非负整数")
        _aware_utc(self.updated_at, "updated_at")

    @classmethod
    def initial(
        cls,
        *,
        policy: RelationshipPolicy | None = None,
        now: datetime | None = None,
    ) -> "RelationshipState":
        selected = policy or RelationshipPolicy()
        timestamp = _aware_utc(now or datetime.now(UTC), "now")
        if selected.starting_mode == "familiar":
            return cls(20, 25, 15, 0, timestamp)
        return cls(5, 10, 5, 0, timestamp)


@dataclass(frozen=True)
class RelationshipEvent:
    event_id: str
    event_type: RelationshipEventType
    occurred_at: datetime
    confidence: float
    source_host: str
    scope_id: str
    reason_code: str
    schema_version: int = RELATIONSHIP_EVENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not _EVENT_ID_RE.fullmatch(self.event_id):
            raise RelationshipError("event_id 必须是 4–128 位不透明标识")
        if not isinstance(self.event_type, RelationshipEventType):
            raise RelationshipError("event_type 不受支持")
        _aware_utc(self.occurred_at, "occurred_at")
        if not isinstance(self.confidence, (int, float)) or not 0 <= self.confidence <= 1:
            raise RelationshipError("confidence 必须在 0–1 之间")
        validate_source_host(self.source_host)
        validate_scope_id(self.scope_id)
        if not _REASON_CODE_RE.fullmatch(self.reason_code):
            raise RelationshipError("reason_code 必须是结构化代码")
        if self.schema_version != RELATIONSHIP_EVENT_SCHEMA_VERSION:
            raise RelationshipError("关系事件 schema_version 不受支持")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "occurred_at": self.occurred_at.astimezone(UTC).isoformat(),
            "confidence": float(self.confidence),
            "source_host": self.source_host,
            "scope_id": self.scope_id,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class AtmosphereState:
    value: Atmosphere
    scope_id: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.value, Atmosphere):
            raise RelationshipError("未知当前氛围")
        if not _OPAQUE_SCOPE_RE.fullmatch(self.scope_id):
            raise RelationshipError("氛围 scope_id 必须是不透明标识")
        _aware_utc(self.expires_at, "expires_at")


@dataclass(frozen=True)
class ReductionContext:
    duplicate: bool = False
    daily_positive_delta: RelationshipDelta = RelationshipDelta()
    daily_negative_delta: RelationshipDelta = RelationshipDelta()
    last_same_type_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.last_same_type_at is not None:
            _aware_utc(self.last_same_type_at, "last_same_type_at")


@dataclass(frozen=True)
class ReductionResult:
    state: RelationshipState
    applied: bool
    reason: str
    delta: RelationshipDelta = RelationshipDelta()
    atmosphere: AtmosphereState | None = None


@dataclass(frozen=True)
class RelationshipProjection:
    state: RelationshipState
    label: str
    atmosphere: Atmosphere
    photo_bands: tuple[str, ...]


@dataclass(frozen=True)
class _EventRule:
    delta: RelationshipDelta
    atmosphere: Atmosphere
    atmosphere_ttl: timedelta
    cooldown: timedelta


_RULES = {
    RelationshipEventType.WARM_EXCHANGE: _EventRule(
        RelationshipDelta(1, 0, 1), Atmosphere.PLAYFUL, timedelta(hours=8), timedelta(hours=6)
    ),
    RelationshipEventType.EXPLICIT_APPRECIATION: _EventRule(
        RelationshipDelta(1, 1, 1), Atmosphere.TENDER, timedelta(hours=12), timedelta(hours=12)
    ),
    RelationshipEventType.PREFERENCE_RESPECTED: _EventRule(
        RelationshipDelta(1, 1, 0), Atmosphere.NEUTRAL, timedelta(hours=6), timedelta(hours=12)
    ),
    RelationshipEventType.BOUNDARY_RESPECTED: _EventRule(
        RelationshipDelta(0, 2, 0), Atmosphere.NEUTRAL, timedelta(hours=6), timedelta(hours=12)
    ),
    RelationshipEventType.EXPLICIT_DISCOMFORT: _EventRule(
        RelationshipDelta(0, -2, -1), Atmosphere.STRAINED, timedelta(hours=24), timedelta(0)
    ),
    RelationshipEventType.EXPLICIT_DISTANCE: _EventRule(
        RelationshipDelta(0, -3, -3), Atmosphere.STRAINED, timedelta(hours=24), timedelta(0)
    ),
    RelationshipEventType.REPAIR: _EventRule(
        RelationshipDelta(0, 1, 1), Atmosphere.REPAIR, timedelta(hours=8), timedelta(hours=12)
    ),
}
_DAILY_POSITIVE_CAP = RelationshipDelta(3, 4, 4)
_DAILY_NEGATIVE_CAP = RelationshipDelta(3, 4, 4)


def _aware_utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RelationshipError(f"{label} 必须包含时区")
    return value.astimezone(UTC)


def _clamp_score(value: int) -> int:
    return min(SCORE_MAX, max(SCORE_MIN, value))


def _bounded_component(
    proposed: int,
    positive_used: int,
    negative_used: int,
    positive_cap: int,
    negative_cap: int,
) -> int:
    if proposed > 0:
        return min(proposed, max(0, positive_cap - max(0, positive_used)))
    if proposed < 0:
        remaining = max(0, negative_cap - max(0, negative_used))
        return -min(-proposed, remaining)
    return 0


def _bounded_delta(
    proposed: RelationshipDelta,
    positive_used: RelationshipDelta,
    negative_used: RelationshipDelta,
) -> RelationshipDelta:
    return RelationshipDelta(
        familiarity=_bounded_component(
            proposed.familiarity,
            positive_used.familiarity,
            negative_used.familiarity,
            _DAILY_POSITIVE_CAP.familiarity,
            _DAILY_NEGATIVE_CAP.familiarity,
        ),
        trust=_bounded_component(
            proposed.trust,
            positive_used.trust,
            negative_used.trust,
            _DAILY_POSITIVE_CAP.trust,
            _DAILY_NEGATIVE_CAP.trust,
        ),
        closeness=_bounded_component(
            proposed.closeness,
            positive_used.closeness,
            negative_used.closeness,
            _DAILY_POSITIVE_CAP.closeness,
            _DAILY_NEGATIVE_CAP.closeness,
        ),
    )


class RelationshipReducer:
    """把概率性的候选事件限制为可重复、可审计的状态变化。"""

    def reduce(
        self,
        state: RelationshipState,
        event: RelationshipEvent,
        context: ReductionContext,
    ) -> ReductionResult:
        if context.duplicate:
            return ReductionResult(state, False, "duplicate_event")
        if event.confidence < CONFIDENCE_THRESHOLD:
            return ReductionResult(state, False, "confidence_below_threshold")
        if state.revision > 0 and event.occurred_at <= state.updated_at:
            return ReductionResult(state, False, "out_of_order_event")

        rule = _RULES[event.event_type]
        last = context.last_same_type_at
        if last is not None and event.occurred_at < last:
            return ReductionResult(state, False, "out_of_order_event")
        if last is not None and event.occurred_at - last < rule.cooldown:
            return ReductionResult(state, False, "event_cooldown_active")

        delta = _bounded_delta(
            rule.delta,
            context.daily_positive_delta,
            context.daily_negative_delta,
        )
        atmosphere = AtmosphereState(
            value=rule.atmosphere,
            scope_id=event.scope_id,
            expires_at=event.occurred_at + rule.atmosphere_ttl,
        )
        if delta == RelationshipDelta():
            return ReductionResult(
                state,
                False,
                "daily_delta_cap_reached",
                delta=delta,
                atmosphere=atmosphere,
            )

        updated = RelationshipState(
            familiarity=_clamp_score(state.familiarity + delta.familiarity),
            trust=_clamp_score(state.trust + delta.trust),
            closeness=_clamp_score(state.closeness + delta.closeness),
            revision=state.revision + 1,
            updated_at=event.occurred_at,
        )
        return ReductionResult(
            updated,
            True,
            "event_applied",
            delta=delta,
            atmosphere=atmosphere,
        )


def active_atmosphere(
    atmosphere: AtmosphereState | None,
    now: datetime | None = None,
) -> Atmosphere:
    timestamp = _aware_utc(now or datetime.now(UTC), "now")
    if atmosphere is None or atmosphere.expires_at <= timestamp:
        return Atmosphere.NEUTRAL
    return atmosphere.value


def project_relationship(
    state: RelationshipState,
    policy: RelationshipPolicy,
    *,
    atmosphere: AtmosphereState | None,
    now: datetime | None = None,
) -> RelationshipProjection:
    current = active_atmosphere(atmosphere, now)
    bands = ["everyday"]
    if state.familiarity >= 20 and state.trust >= 20:
        bands.append("personal")
    romantic_ready = (
        policy.romance_enabled
        and state.familiarity >= 35
        and state.trust >= 55
        and state.closeness >= 45
        and current not in {Atmosphere.STRAINED, Atmosphere.REPAIR}
    )
    if romantic_ready:
        bands.append("romantic")
    if (
        romantic_ready
        and state.familiarity >= 55
        and state.trust >= 75
        and state.closeness >= 70
    ):
        bands.append("intimate_non_explicit")

    if current is Atmosphere.STRAINED:
        label = "需要缓和"
    elif current is Atmosphere.REPAIR:
        label = "正在缓和"
    elif policy.romance_enabled and state.familiarity >= 55 and state.trust >= 75 and state.closeness >= 70:
        label = "伴侣式亲近"
    elif state.familiarity >= 55 and state.trust >= 70 and state.closeness >= 60:
        label = "很亲近"
    elif state.familiarity >= 35 and state.trust >= 45 and state.closeness >= 25:
        label = "逐渐亲近"
    elif state.familiarity >= 20:
        label = "渐渐熟悉"
    else:
        label = "刚认识"

    return RelationshipProjection(
        state=state,
        label=label,
        atmosphere=current,
        photo_bands=tuple(bands),
    )
