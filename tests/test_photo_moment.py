from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from companion_kit.photo_moment import (
    PHOTO_ENVELOPE_SCHEMA_VERSION,
    PHOTO_MOMENT_SCHEMA_VERSION,
    PhotoMoment,
    encode_photo_envelope,
    normalize_photo_moment,
    parse_photo_envelope,
    parse_photo_envelope_details,
)
from companion_kit.daily_look import DailyLookDirective, DailyLookProposal
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
        "portrait_dynamics": "three_quarter_soft",
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
                identity_version=1,
            )

            self.assertTrue(
                store.latest_result_is_companion(
                    profile_id="private-persona",
                    session_id="private-session",
                    identity_version=1,
                )
            )
            self.assertEqual(
                store.latest_result_state(
                    profile_id="private-persona",
                    session_id="private-session",
                    identity_version=1,
                ),
                "companion",
            )
            store.verify_latest_companion_path(
                profile_id="private-persona",
                session_id="private-session",
                source_path=image,
                identity_version=1,
            )
            image.write_bytes(b"overwritten-generic-content")
            with self.assertRaises(PhotoMomentStoreError):
                store.verify_latest_companion_path(
                    profile_id="private-persona",
                    session_id="private-session",
                    source_path=image,
                    identity_version=1,
                )
            store.record_image_result(
                profile_id="private-persona",
                session_id="private-session",
                paths=(image,),
                is_companion=False,
                identity_version=1,
            )
            self.assertFalse(
                store.latest_result_is_companion(
                    profile_id="private-persona",
                    session_id="private-session",
                    identity_version=1,
                )
            )
            self.assertEqual(
                store.latest_result_state(
                    profile_id="private-persona",
                    session_id="private-session",
                    identity_version=1,
                ),
                "generic",
            )
            self.assertEqual(
                store.latest_result_state(
                    profile_id="private-persona",
                    session_id="another-session",
                    identity_version=1,
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
                identity_version=1,
            )
            result_files = tuple((root / "runtime" / "results").glob("*.json"))
            self.assertEqual(len(result_files), 1)
            current[0] += timedelta(hours=2, seconds=1)

            self.assertFalse(
                store.latest_result_is_companion(
                    profile_id="companion",
                    session_id="session",
                    identity_version=1,
                )
            )
            self.assertFalse(result_files[0].exists())

    def test_latest_result_cannot_cross_identity_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "photo-moments"
            image = Path(tmp).resolve() / "old-face.png"
            image.write_bytes(b"old-identity-result")
            store = PhotoMomentStore(root)
            store.record_image_result(
                profile_id="companion",
                session_id="same-session",
                paths=(image,),
                is_companion=True,
                identity_version=1,
            )

            self.assertEqual(
                store.latest_result_state(
                    profile_id="companion",
                    session_id="same-session",
                    identity_version=2,
                ),
                "missing",
            )
            with self.assertRaises(PhotoMomentStoreError):
                store.verify_latest_companion_path(
                    profile_id="companion",
                    session_id="same-session",
                    source_path=image,
                    identity_version=2,
                )

    def test_envelope_round_trip_is_removed_from_provider_prompt(self) -> None:
        original = "真实生活感的窗边自拍"
        payload = original + encode_photo_envelope("ckp_" + "a" * 24, moment())

        cleaned, parsed = parse_photo_envelope(
            payload,
            expected_token="ckp_" + "a" * 24,
        )

        self.assertEqual(cleaned, original)
        self.assertEqual(parsed, moment())
        self.assertEqual(PHOTO_MOMENT_SCHEMA_VERSION, 3)
        self.assertIn("COMPANION_KIT_PHOTO_V4", payload)
        self.assertIn('"makeup":"natural"', payload)
        self.assertIn('"portrait_dynamics":"three_quarter_soft"', payload)
        self.assertNotIn("COMPANION_KIT", cleaned)
        self.assertNotIn("turn_token", cleaned)

    def test_v4_envelope_carries_a_bounded_daily_look_directive(self) -> None:
        token = "ckp_" + "c" * 24
        directive = DailyLookDirective.from_dict(
            {
                "action": "replace_daily",
                "look_id": "look_" + "d" * 24,
                "proposal": DailyLookProposal.from_dict(
                    {
                        "title": "白衬衫日",
                        "palette": "白色、深蓝和银色",
                        "silhouette": "清楚的上短下长比例",
                        "hero_piece": "线条干净的白衬衫",
                        "accent": "深色腕表",
                    }
                ).to_dict(),
            }
        )
        payload = "自然生活感的自拍" + encode_photo_envelope(
            token,
            moment(),
            daily_look=directive,
        )

        cleaned, parsed_moment, parsed_look = parse_photo_envelope_details(
            payload,
            expected_token=token,
        )

        self.assertEqual(PHOTO_ENVELOPE_SCHEMA_VERSION, 4)
        self.assertIn("COMPANION_KIT_PHOTO_V4", payload)
        self.assertEqual(cleaned, "自然生活感的自拍")
        self.assertEqual(parsed_moment, moment())
        self.assertEqual(parsed_look, directive)
        self.assertEqual(
            parse_photo_envelope(payload, expected_token=token),
            ("自然生活感的自拍", moment()),
        )

    def test_legacy_v1_moment_and_envelope_remain_readable(self) -> None:
        token = "ckp_" + "b" * 24
        legacy_moment = moment().to_dict()
        legacy_moment.pop("makeup")
        legacy_moment.pop("portrait_dynamics")
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
        self.assertEqual(parsed.portrait_dynamics, "unspecified")
        self.assertEqual(PhotoMoment.from_dict(legacy_moment).makeup, "unspecified")

    def test_legacy_v2_and_v3_envelopes_remain_readable_after_v4_upgrade(self) -> None:
        token = "ckp_" + "d" * 24
        legacy_moment = moment().to_dict()
        legacy_moment.pop("portrait_dynamics")
        directive = DailyLookDirective.from_dict(
            {
                "action": "one_shot",
                "look_id": None,
                "proposal": None,
            }
        )
        for version, daily_look in ((2, None), (3, directive)):
            with self.subTest(version=version):
                payload: dict[str, object] = {
                    "schema_version": version,
                    "turn_token": token,
                    "photo_moment": legacy_moment,
                }
                if daily_look is not None:
                    payload["daily_look"] = daily_look.to_dict()
                envelope = (
                    f"旧版 V{version} 照片描述\n\n"
                    f"[[COMPANION_KIT_PHOTO_V{version}]]\n"
                    + json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + f"\n[[/COMPANION_KIT_PHOTO_V{version}]]"
                )

                cleaned, parsed, parsed_look = parse_photo_envelope_details(
                    envelope,
                    expected_token=token,
                )

                self.assertEqual(cleaned, f"旧版 V{version} 照片描述")
                self.assertEqual(parsed.portrait_dynamics, "unspecified")
                self.assertEqual(parsed_look, daily_look)

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
            portrait_dynamics="custom",
        )
        normalized = normalize_photo_moment(
            previous,
            recent=(previous,),
            allowed_intimacy_bands=("everyday",),
        )

        self.assertEqual(normalized.hairstyle, "custom")
        self.assertEqual(normalized.expression, "custom")
        self.assertEqual(normalized.makeup, "custom")
        self.assertEqual(normalized.portrait_dynamics, "custom")

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
            portrait_dynamics="side_glance_half_smile",
        ).render_image_constraints()

        self.assertIn("本次最终神态", rendered)
        self.assertIn("不对称的半笑", rendered)
        self.assertIn("本次最终妆容", rendered)
        self.assertIn("眼线或睫毛", rendered)
        self.assertIn("不套用统一瘦脸、大眼或网红脸", rendered)
        self.assertIn("主脸参考里可见的表情、头部姿态、视线、嘴角和妆容", rendered)

    def test_new_photo_enforces_tall_natural_adult_proportions_without_big_head(self) -> None:
        rendered = moment(framing="full").render_image_constraints()

        self.assertIn("偏高挑、修长", rendered)
        self.assertIn("头身比 1:7 到 1:8", rendered)
        self.assertIn("肩宽约为头宽的 1.6 到 2 倍", rendered)
        self.assertIn("脸部身份锁只锁五官和脸型", rendered)
        self.assertIn("避免 0.5x 超广角", rendered)
        self.assertIn("严禁大头娃娃", rendered)
        self.assertIn("不是缩小头部或暴力拉长四肢", rendered)

    def test_portrait_dynamics_explicitly_changes_head_gaze_and_smile(self) -> None:
        current = PhotoMoment.from_dict(
            {
                **moment().to_dict(),
                "portrait_dynamics": "side_glance_half_smile",
            }
        )
        rendered = current.render_image_constraints()

        self.assertIn("本次面部动态", rendered)
        self.assertIn("头部向一侧自然转动", rendered)
        self.assertIn("视线从侧面回到镜头附近", rendered)
        self.assertIn("不对称的半笑", rendered)
        self.assertIn("不得照抄主脸参考或上一张照片的头部角度、视线和嘴角弧度", rendered)

    def test_persona_expression_and_caption_act_create_attitude_without_generic_sweetness(self) -> None:
        current = moment(
            expression="self_assured",
            portrait_dynamics="soft_challenge",
            caption_act="persona_coax",
            intimacy_band="romantic",
        )

        image_context = current.render_image_constraints()
        caption_context = current.render_caption_context()

        self.assertIn("自信从容", image_context)
        self.assertIn("像在等对方接招", image_context)
        self.assertIn("这个 Persona 自己的方式", caption_context)
        self.assertIn("撒娇", caption_context)
        self.assertIn("不等于幼态化", caption_context)
        self.assertIn("服装不由亲密度单独决定", caption_context)

    def test_repeated_portrait_dynamics_rotates_to_a_compatible_variant(self) -> None:
        previous = PhotoMoment.from_dict(
            {
                **moment(expression="playful").to_dict(),
                "portrait_dynamics": "side_glance_half_smile",
            }
        )
        repeated = PhotoMoment.from_dict(
            {
                **moment(expression="playful").to_dict(),
                "portrait_dynamics": "side_glance_half_smile",
            }
        )

        normalized = normalize_photo_moment(
            repeated,
            recent=(previous,),
            allowed_intimacy_bands=("everyday",),
        )

        self.assertNotEqual(
            normalized.portrait_dynamics,
            previous.portrait_dynamics,
        )
        self.assertNotEqual(normalized.portrait_dynamics, "unspecified")
        self.assertEqual(normalized.expression, "thoughtful")
        self.assertIn(
            normalized.portrait_dynamics,
            {"quiet_off_camera", "downward_thoughtful", "calm_three_quarter"},
        )

    def test_legacy_history_is_migrated_without_losing_recent_moment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "photo-moments"
            store = PhotoMomentStore(root)
            history_path = store.history_path("companion")
            history_path.parent.mkdir(parents=True)
            legacy = moment(scene="home").to_dict()
            legacy.pop("makeup")
            legacy.pop("portrait_dynamics")
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
            self.assertEqual(loaded[0].portrait_dynamics, "unspecified")
            self.assertTrue(committed.committed)
            self.assertEqual(migrated["schema_version"], 3)
            self.assertEqual(migrated["recent"][0]["makeup"], "unspecified")
            self.assertEqual(
                migrated["recent"][0]["portrait_dynamics"],
                "unspecified",
            )
            self.assertEqual(migrated["recent"][1]["makeup"], "warm_tone")

    def test_v2_history_adds_portrait_dynamics_without_losing_existing_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "photo-moments"
            store = PhotoMomentStore(root)
            history_path = store.history_path("companion")
            history_path.parent.mkdir(parents=True)
            v2_moment = moment(
                scene="cafe",
                expression="thoughtful",
                makeup="warm_tone",
            ).to_dict()
            v2_moment.pop("portrait_dynamics")
            history_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "profile_digest": history_path.stem,
                        "recent": [v2_moment],
                    }
                ),
                encoding="utf-8",
            )

            loaded = store.recent(profile_id="companion")
            store.stage(
                profile_id="companion",
                session_id="session",
                tool_use_id="tool",
                photo_moment=moment(scene="street"),
            )
            committed = store.commit(
                profile_id="companion",
                session_id="session",
                tool_use_id="tool",
            )

            migrated = json.loads(history_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded[0].scene, "cafe")
            self.assertEqual(loaded[0].makeup, "warm_tone")
            self.assertEqual(loaded[0].portrait_dynamics, "unspecified")
            self.assertTrue(committed.committed)
            self.assertEqual(migrated["schema_version"], 3)
            self.assertEqual(migrated["recent"][0]["scene"], "cafe")
            self.assertEqual(migrated["recent"][0]["makeup"], "warm_tone")
            self.assertEqual(
                migrated["recent"][0]["portrait_dynamics"],
                "unspecified",
            )

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
