from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from companion_kit.photo_moment import (
    PHOTO_MOMENT_SCHEMA_VERSION,
    PhotoMoment,
    encode_photo_envelope,
    normalize_photo_moment,
    parse_photo_envelope,
)
from companion_kit.photo_moment_store import (
    PhotoMomentStore,
    PhotoMomentStoreError,
)


def moment(**overrides: object) -> PhotoMoment:
    values: dict[str, object] = {
        "mode": "new",
        "scene": "window",
        "activity": "getting_ready",
        "framing": "half",
        "hairstyle": "loose",
        "expression": "soft_smile",
        "makeup": "natural",
        "time_band": "day",
        "intimacy_band": "everyday",
        "caption_act": "share_detail",
        "identity_version": 1,
    }
    values.update(overrides)
    return PhotoMoment.from_dict(values)


class PhotoMomentTests(unittest.TestCase):
    def test_turn_ticket_is_opaque_profile_bound_and_short_lived(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            current = [datetime(2026, 8, 10, 9, 0, tzinfo=UTC)]
            root = Path(tmp).resolve() / "photo-moments"
            store = PhotoMomentStore(root, clock=lambda: current[0])
            ticket = store.issue_turn(
                profile_id="private-persona",
                session_id="private-session",
                turn_id="private-turn",
                mode="new",
                turn_token="ckp_" + "a" * 24,
            )

            self.assertEqual(
                store.turn_ticket(
                    session_id="private-session",
                    turn_id="private-turn",
                ),
                ticket,
            )
            self.assertTrue(store.ticket_matches_profile(ticket, "private-persona"))
            self.assertFalse(store.ticket_matches_profile(ticket, "another-persona"))
            serialized = "".join(
                path.read_text(encoding="utf-8")
                for path in root.rglob("*.json")
            )
            self.assertNotIn("private-persona", serialized)
            self.assertNotIn("private-session", serialized)
            self.assertNotIn("private-turn", serialized)
            ticket_files = tuple((root / "runtime" / "turns").glob("*.json"))
            self.assertEqual(len(ticket_files), 1)
            current[0] += timedelta(minutes=21)
            self.assertIsNone(
                store.turn_ticket(
                    session_id="private-session",
                    turn_id="private-turn",
                )
            )
            self.assertFalse(ticket_files[0].exists())

    def test_latest_result_binds_file_content_and_never_stores_private_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "photo-moments"
            image = Path(tmp).resolve() / "private-result.png"
            image.write_bytes(b"first-image-content")
            store = PhotoMomentStore(root)
            store.record_image_result(
                profile_id="private-persona",
                session_id="private-session",
                paths=(image,),
                is_companion=True,
            )

            self.assertTrue(
                store.latest_result_is_companion(
                    profile_id="private-persona",
                    session_id="private-session",
                )
            )
            self.assertEqual(
                store.latest_result_state(
                    profile_id="private-persona",
                    session_id="private-session",
                ),
                "companion",
            )
            store.verify_latest_companion_path(
                profile_id="private-persona",
                session_id="private-session",
                source_path=image,
            )
            image.write_bytes(b"overwritten-generic-content")
            with self.assertRaises(PhotoMomentStoreError):
                store.verify_latest_companion_path(
                    profile_id="private-persona",
                    session_id="private-session",
                    source_path=image,
                )
            store.record_image_result(
                profile_id="private-persona",
                session_id="private-session",
                paths=(image,),
                is_companion=False,
            )
            self.assertFalse(
                store.latest_result_is_companion(
                    profile_id="private-persona",
                    session_id="private-session",
                )
            )
            self.assertEqual(
                store.latest_result_state(
                    profile_id="private-persona",
                    session_id="private-session",
                ),
                "generic",
            )
            self.assertEqual(
                store.latest_result_state(
                    profile_id="private-persona",
                    session_id="another-session",
                ),
                "missing",
            )
            serialized = "".join(
                path.read_text(encoding="utf-8")
                for path in root.rglob("*.json")
            )
            self.assertNotIn("private-persona", serialized)
            self.assertNotIn("private-session", serialized)
            self.assertNotIn(str(image), serialized)

    def test_expired_latest_result_is_physically_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            current = [datetime(2026, 8, 10, 9, 0, tzinfo=UTC)]
            root = Path(tmp).resolve() / "photo-moments"
            image = Path(tmp).resolve() / "result.png"
            image.write_bytes(b"image-content")
            store = PhotoMomentStore(root, clock=lambda: current[0])
            store.record_image_result(
                profile_id="companion",
                session_id="session",
                paths=(image,),
                is_companion=True,
            )
            result_files = tuple((root / "runtime" / "results").glob("*.json"))
            self.assertEqual(len(result_files), 1)
            current[0] += timedelta(hours=2, seconds=1)

            self.assertFalse(
                store.latest_result_is_companion(
                    profile_id="companion",
                    session_id="session",
                )
            )
            self.assertFalse(result_files[0].exists())
    def test_envelope_round_trip_is_removed_from_provider_prompt(self) -> None:
        original = "真实生活感的窗边自拍"
        payload = original + encode_photo_envelope("ckp_" + "a" * 24, moment())

        cleaned, parsed = parse_photo_envelope(
            payload,
            expected_token="ckp_" + "a" * 24,
        )

        self.assertEqual(cleaned, original)
        self.assertEqual(parsed, moment())
        self.assertEqual(PHOTO_MOMENT_SCHEMA_VERSION, 2)
        self.assertIn("COMPANION_KIT_PHOTO_V2", payload)
        self.assertIn('"makeup":"natural"', payload)
        self.assertNotIn("COMPANION_KIT", cleaned)
        self.assertNotIn("turn_token", cleaned)

    def test_legacy_v1_moment_and_envelope_remain_readable(self) -> None:
        token = "ckp_" + "b" * 24
        legacy_moment = moment().to_dict()
        legacy_moment.pop("makeup")
        legacy_envelope = (
            "旧版照片描述\n\n[[COMPANION_KIT_PHOTO_V1]]\n"
            + json.dumps(
                {
                    "schema_version": 1,
                    "turn_token": token,
                    "photo_moment": legacy_moment,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n[[/COMPANION_KIT_PHOTO_V1]]"
        )

        cleaned, parsed = parse_photo_envelope(
            legacy_envelope,
            expected_token=token,
        )

        self.assertEqual(cleaned, "旧版照片描述")
        self.assertEqual(parsed.makeup, "unspecified")
        self.assertEqual(PhotoMoment.from_dict(legacy_moment).makeup, "unspecified")

    def test_normalization_clamps_intimacy_and_breaks_repeated_style(self) -> None:
        previous = moment(intimacy_band="everyday")
        repeated = moment(intimacy_band="intimate_non_explicit")

        normalized = normalize_photo_moment(
            repeated,
            recent=(previous,),
            allowed_intimacy_bands=("everyday", "personal"),
        )

        changed = sum(
            getattr(normalized, field) != getattr(previous, field)
            for field in ("scene", "activity", "framing", "hairstyle", "expression")
        )
        self.assertGreaterEqual(changed, 2)
        self.assertTrue(
            normalized.hairstyle != previous.hairstyle
            or normalized.expression != previous.expression
        )
        self.assertEqual(normalized.intimacy_band, "personal")
        self.assertIn("亲近但有分寸", normalized.render_image_constraints())
        self.assertNotIn("更亲密的非露骨氛围", normalized.render_image_constraints())

    def test_custom_user_axes_are_not_overridden_by_deduplication(self) -> None:
        previous = moment(
            hairstyle="custom",
            expression="custom",
            makeup="custom",
        )
        normalized = normalize_photo_moment(
            previous,
            recent=(previous,),
            allowed_intimacy_bands=("everyday",),
        )

        self.assertEqual(normalized.hairstyle, "custom")
        self.assertEqual(normalized.expression, "custom")
        self.assertEqual(normalized.makeup, "custom")

    def test_makeup_keeps_short_continuity_then_refreshes_with_new_context(self) -> None:
        first = moment(scene="home", makeup="natural")
        second = moment(scene="home", makeup="natural")

        continued = normalize_photo_moment(
            second,
            recent=(first,),
            allowed_intimacy_bands=("everyday",),
        )
        refreshed = normalize_photo_moment(
            moment(scene="street", time_band="night", makeup="natural"),
            recent=(first, second),
            allowed_intimacy_bands=("everyday",),
        )

        self.assertEqual(continued.makeup, "natural")
        self.assertNotEqual(refreshed.makeup, "natural")

    def test_same_photo_set_does_not_force_a_makeup_change(self) -> None:
        first = moment(scene="home", activity="getting_ready", makeup="natural")
        second = moment(scene="home", activity="getting_ready", makeup="natural")

        continued = normalize_photo_moment(
            moment(scene="home", activity="getting_ready", makeup="natural"),
            recent=(first, second),
            allowed_intimacy_bands=("everyday",),
        )

        self.assertEqual(continued.makeup, "natural")

    def test_edit_previous_preserves_unrequested_expression_and_makeup(self) -> None:
        rendered = moment(
            mode="edit_previous",
            expression="open_smile",
            makeup="evening",
        ).render_image_constraints()

        self.assertIn("只修改用户明确提出的部分", rendered)
        self.assertIn("其他发型、表情、妆容", rendered)
        self.assertNotIn("本次最终神态", rendered)
        self.assertNotIn("本次最终妆容", rendered)

    def test_final_expression_and_makeup_override_reference_appearance(self) -> None:
        rendered = moment(
            expression="playful",
            makeup="defined_eyes",
        ).render_image_constraints()

        self.assertIn("本次最终神态", rendered)
        self.assertIn("不对称的半笑", rendered)
        self.assertIn("本次最终妆容", rendered)
        self.assertIn("眼线或睫毛", rendered)
        self.assertIn("不套用统一瘦脸、大眼或网红脸", rendered)
        self.assertIn("主脸参考里可见的表情与妆容只是拍摄当时状态", rendered)

    def test_legacy_history_is_migrated_without_losing_recent_moment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "photo-moments"
            store = PhotoMomentStore(root)
            history_path = store.history_path("companion")
            history_path.parent.mkdir(parents=True)
            legacy = moment(scene="home").to_dict()
            legacy.pop("makeup")
            history_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "profile_digest": history_path.stem,
                        "recent": [legacy],
                    }
                ),
                encoding="utf-8",
            )

            loaded = store.recent(profile_id="companion")
            store.stage(
                profile_id="companion",
                session_id="session",
                tool_use_id="tool",
                photo_moment=moment(scene="street", makeup="warm_tone"),
            )
            committed = store.commit(
                profile_id="companion",
                session_id="session",
                tool_use_id="tool",
            )

            migrated = json.loads(history_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded[0].makeup, "unspecified")
            self.assertTrue(committed.committed)
            self.assertEqual(migrated["schema_version"], 2)
            self.assertEqual(migrated["recent"][0]["makeup"], "unspecified")
            self.assertEqual(migrated["recent"][1]["makeup"], "warm_tone")

    def test_store_keeps_only_four_successful_structured_moments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "photo-moments"
            store = PhotoMomentStore(root)
            for index in range(5):
                current = moment(
                    scene=("window", "street", "home", "outdoors", "workspace")[index]
                )
                store.stage(
                    profile_id="private-persona-name",
                    session_id="private-session-id",
                    tool_use_id=f"private-tool-{index}",
                    photo_moment=current,
                )
                result = store.commit(
                    profile_id="private-persona-name",
                    session_id="private-session-id",
                    tool_use_id=f"private-tool-{index}",
                )
                self.assertTrue(result.committed)

            recent = store.recent(
                profile_id="private-persona-name",
                identity_version=1,
            )

            self.assertEqual(len(recent), 4)
            self.assertEqual(recent[-1].scene, "workspace")
            serialized = "".join(
                path.read_text(encoding="utf-8")
                for path in root.rglob("*.json")
            )
            self.assertNotIn("private-persona-name", serialized)
            self.assertNotIn("private-session-id", serialized)
            self.assertNotIn("private-tool", serialized)
            self.assertNotIn("prompt", serialized)
            self.assertNotIn("generated_images", serialized)
            self.assertNotIn("/Users/", serialized)

    def test_parallel_pending_calls_receive_different_style_recipes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PhotoMomentStore(Path(tmp).resolve() / "photo-moments")
            first = store.stage_varied(
                profile_id="companion",
                session_id="session",
                tool_use_id="tool-one",
                photo_moment=moment(),
                allowed_intimacy_bands=("everyday",),
            )
            second = store.stage_varied(
                profile_id="companion",
                session_id="session",
                tool_use_id="tool-two",
                photo_moment=moment(),
                allowed_intimacy_bands=("everyday",),
            )

            self.assertNotEqual(first.hairstyle, second.hairstyle)
            self.assertNotEqual(first.expression, second.expression)

    def test_new_identity_version_does_not_inherit_old_style_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PhotoMomentStore(Path(tmp).resolve() / "photo-moments")
            old = moment(identity_version=1)
            store.stage(
                profile_id="companion",
                session_id="old-session",
                tool_use_id="old-tool",
                photo_moment=old,
            )
            store.commit(
                profile_id="companion",
                session_id="old-session",
                tool_use_id="old-tool",
            )
            fresh = moment(identity_version=2)

            staged = store.stage_varied(
                profile_id="companion",
                session_id="new-session",
                tool_use_id="new-tool",
                photo_moment=fresh,
                allowed_intimacy_bands=("everyday",),
            )

            self.assertEqual(staged, fresh)

    def test_next_photo_operation_removes_expired_pending_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            current = [datetime(2026, 8, 10, 9, 0, tzinfo=UTC)]
            root = Path(tmp).resolve() / "photo-moments"
            store = PhotoMomentStore(root, clock=lambda: current[0])
            store.stage(
                profile_id="companion",
                session_id="old-session",
                tool_use_id="old-tool",
                photo_moment=moment(),
            )
            self.assertEqual(len(tuple((root / "runtime").glob("*.json"))), 1)
            current[0] += timedelta(minutes=21)

            store.stage_varied(
                profile_id="companion",
                session_id="new-session",
                tool_use_id="new-tool",
                photo_moment=moment(),
                allowed_intimacy_bands=("everyday",),
            )

            self.assertEqual(len(tuple((root / "runtime").glob("*.json"))), 1)

    def test_newer_history_schema_is_preserved_while_current_caption_survives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "photo-moments"
            store = PhotoMomentStore(root)
            history_path = store.history_path("companion")
            history_path.parent.mkdir(parents=True)
            original = {
                "schema_version": 999,
                "profile_digest": history_path.stem,
                "recent": [],
            }
            history_path.write_text(json.dumps(original), encoding="utf-8")
            current = moment()
            store.stage(
                profile_id="companion",
                session_id="session",
                tool_use_id="tool",
                photo_moment=current,
            )

            result = store.commit(
                profile_id="companion",
                session_id="session",
                tool_use_id="tool",
            )

            self.assertFalse(result.committed)
            self.assertEqual(result.photo_moment, current)
            self.assertEqual(
                json.loads(history_path.read_text(encoding="utf-8")),
                original,
            )


if __name__ == "__main__":
    unittest.main()
