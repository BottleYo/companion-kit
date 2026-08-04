from datetime import UTC, datetime, timedelta
import unittest

from companion_kit.relationship import (
    Atmosphere,
    RelationshipDelta,
    RelationshipEvent,
    RelationshipEventType,
    RelationshipPolicy,
    RelationshipReducer,
    RelationshipState,
    ReductionContext,
    active_atmosphere,
    project_relationship,
)


NOW = datetime(2026, 8, 4, 10, 0, tzinfo=UTC)


def event(
    event_type: RelationshipEventType,
    *,
    event_id: str = "event-0001",
    confidence: float = 1.0,
    occurred_at: datetime = NOW,
) -> RelationshipEvent:
    return RelationshipEvent(
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_at,
        confidence=confidence,
        source_host="codex",
        scope_id="a" * 32,
        reason_code="explicit_user_signal",
    )


class RelationshipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reducer = RelationshipReducer()
        self.initial = RelationshipState.initial()

    def test_low_confidence_candidate_cannot_change_relationship(self) -> None:
        result = self.reducer.reduce(
            self.initial,
            event(RelationshipEventType.WARM_EXCHANGE, confidence=0.79),
            ReductionContext(),
        )

        self.assertFalse(result.applied)
        self.assertEqual(result.reason, "confidence_below_threshold")
        self.assertEqual(result.state, self.initial)

    def test_explicit_positive_event_uses_bounded_deterministic_delta(self) -> None:
        candidate = event(RelationshipEventType.EXPLICIT_APPRECIATION)
        result = self.reducer.reduce(
            self.initial,
            candidate,
            ReductionContext(),
        )

        self.assertTrue(result.applied)
        self.assertEqual(result.delta.familiarity, 1)
        self.assertEqual(result.delta.trust, 1)
        self.assertEqual(result.delta.closeness, 1)
        self.assertEqual(result.atmosphere.value, Atmosphere.TENDER)
        self.assertEqual(candidate.to_dict()["schema_version"], 1)

    def test_duplicate_and_cooldown_are_fail_closed(self) -> None:
        duplicate = self.reducer.reduce(
            self.initial,
            event(RelationshipEventType.WARM_EXCHANGE),
            ReductionContext(duplicate=True),
        )
        cooldown = self.reducer.reduce(
            self.initial,
            event(RelationshipEventType.WARM_EXCHANGE),
            ReductionContext(last_same_type_at=NOW - timedelta(hours=1)),
        )

        self.assertEqual(duplicate.reason, "duplicate_event")
        self.assertEqual(cooldown.reason, "event_cooldown_active")
        self.assertFalse(duplicate.applied)
        self.assertFalse(cooldown.applied)

    def test_global_out_of_order_event_is_rejected(self) -> None:
        newer_state = RelationshipState(
            familiarity=20,
            trust=20,
            closeness=20,
            revision=2,
            updated_at=NOW,
        )

        result = self.reducer.reduce(
            newer_state,
            event(
                RelationshipEventType.EXPLICIT_APPRECIATION,
                occurred_at=NOW - timedelta(seconds=1),
            ),
            ReductionContext(),
        )

        self.assertFalse(result.applied)
        self.assertEqual(result.reason, "out_of_order_event")

        same_timestamp = self.reducer.reduce(
            newer_state,
            event(RelationshipEventType.EXPLICIT_DISTANCE, occurred_at=NOW),
            ReductionContext(),
        )
        self.assertFalse(same_timestamp.applied)
        self.assertEqual(same_timestamp.reason, "out_of_order_event")

    def test_positive_and_negative_daily_caps_are_independent(self) -> None:
        result = self.reducer.reduce(
            self.initial,
            event(RelationshipEventType.EXPLICIT_DISTANCE),
            ReductionContext(
                daily_positive_delta=RelationshipDelta(3, 4, 4),
                daily_negative_delta=RelationshipDelta(),
            ),
        )

        self.assertTrue(result.applied)
        self.assertEqual(result.delta.trust, -3)
        self.assertEqual(result.delta.closeness, -3)

    def test_long_term_dimensions_do_not_decay_when_user_is_absent(self) -> None:
        state = RelationshipState(
            familiarity=48,
            trust=64,
            closeness=52,
            revision=9,
            updated_at=NOW,
        )
        projection = project_relationship(
            state,
            RelationshipPolicy(),
            atmosphere=None,
            now=NOW + timedelta(days=365),
        )

        self.assertEqual(projection.state, state)
        self.assertEqual(projection.label, "逐渐亲近")

    def test_atmosphere_expires_without_touching_long_term_state(self) -> None:
        result = self.reducer.reduce(
            self.initial,
            event(RelationshipEventType.EXPLICIT_DISCOMFORT),
            ReductionContext(),
        )

        self.assertEqual(
            active_atmosphere(result.atmosphere, NOW + timedelta(hours=1)),
            Atmosphere.STRAINED,
        )
        self.assertEqual(
            active_atmosphere(result.atmosphere, NOW + timedelta(days=2)),
            Atmosphere.NEUTRAL,
        )

    def test_romantic_projection_requires_user_controlled_opt_in(self) -> None:
        state = RelationshipState(
            familiarity=80,
            trust=85,
            closeness=82,
            revision=20,
            updated_at=NOW,
        )

        default_projection = project_relationship(
            state,
            RelationshipPolicy(romance_enabled=False),
            atmosphere=None,
            now=NOW,
        )
        opted_in_projection = project_relationship(
            state,
            RelationshipPolicy(romance_enabled=True),
            atmosphere=None,
            now=NOW,
        )

        self.assertEqual(default_projection.label, "很亲近")
        self.assertNotIn("romantic", default_projection.photo_bands)
        self.assertEqual(opted_in_projection.label, "伴侣式亲近")
        self.assertIn("romantic", opted_in_projection.photo_bands)
        self.assertIn("intimate_non_explicit", opted_in_projection.photo_bands)


if __name__ == "__main__":
    unittest.main()
