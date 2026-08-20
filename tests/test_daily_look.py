from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from companion_kit.daily_look import (
    DailyLookDirective,
    DailyLookError,
    DailyLookProposal,
    companion_day_key,
)
from companion_kit.daily_look_store import DailyLookStore, DailyLookStoreError


class DailyLookTests(unittest.TestCase):
    def test_companion_day_changes_at_four_in_host_timezone(self) -> None:
        local = timezone(timedelta(hours=8))

        self.assertEqual(
            companion_day_key(datetime(2026, 8, 20, 3, 59, tzinfo=local)),
            "2026-08-19",
        )
        self.assertEqual(
            companion_day_key(datetime(2026, 8, 20, 4, 0, tzinfo=local)),
            "2026-08-20",
        )

    def test_directive_rejects_inconsistent_actions_and_private_paths(self) -> None:
        with self.assertRaises(DailyLookError):
            DailyLookDirective.from_dict(
                {"action": "use_daily", "look_id": None, "proposal": None}
            )
        with self.assertRaises(DailyLookError):
            DailyLookProposal.from_dict(
                {
                    "title": "临时主题",
                    "palette": "炭灰和银色",
                    "silhouette": "利落直线",
                    "hero_piece": str(
                        Path("/").joinpath("Users", "private", "secret.png")
                    ),
                    "accent": "细框眼镜",
                }
            )

    def test_same_day_reuses_one_planned_look_and_next_day_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            current = [datetime(2026, 8, 20, 8, 0, tzinfo=UTC)]
            store = DailyLookStore(Path(tmp) / "looks", clock=lambda: current[0])

            first = store.ensure_today(
                profile_id="private-persona",
                style_anchor="高冷、成熟，偏深色利落剪裁",
            )
            repeated = store.ensure_today(
                profile_id="private-persona",
                style_anchor="高冷、成熟，偏深色利落剪裁",
            )
            self.assertIsNotNone(first)
            self.assertEqual(first, repeated)
            self.assertEqual(first.status, "planned")

            current[0] += timedelta(days=1)
            second = store.ensure_today(
                profile_id="private-persona",
                style_anchor="高冷、成熟，偏深色利落剪裁",
            )
            self.assertIsNotNone(second)
            self.assertNotEqual(first.look_id, second.look_id)
            self.assertNotEqual(first.signature, second.signature)

    def test_recent_planner_avoids_exact_theme_repeats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            current = [datetime(2026, 8, 1, 8, 0, tzinfo=UTC)]
            store = DailyLookStore(Path(tmp) / "looks", clock=lambda: current[0])
            signatures: list[str] = []

            for _ in range(14):
                look = store.ensure_today(
                    profile_id="private-persona",
                    style_anchor="高冷御姐，克制利落，喜欢眼镜",
                )
                self.assertIsNotNone(look)
                signatures.append(look.signature)
                current[0] += timedelta(days=1)

            self.assertEqual(len(signatures), len(set(signatures)))

    def test_successful_photo_confirms_look_without_storing_private_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            current = [datetime(2026, 8, 20, 8, 0, tzinfo=UTC)]
            root = Path(tmp) / "looks"
            store = DailyLookStore(root, clock=lambda: current[0])
            look = store.ensure_today(
                profile_id="private-persona",
                style_anchor="温柔松弛的日常穿搭",
            )
            assert look is not None
            directive = DailyLookDirective.from_dict(
                {"action": "use_daily", "look_id": look.look_id, "proposal": None}
            )

            staged = store.stage_for_photo(
                profile_id="private-persona",
                session_id="private-session",
                tool_use_id="private-tool-call",
                directive=directive,
            )
            self.assertEqual(staged, look)
            result = store.commit_for_photo(
                profile_id="private-persona",
                session_id="private-session",
                tool_use_id="private-tool-call",
            )

            self.assertTrue(result.committed)
            self.assertEqual(result.look.status, "confirmed")
            serialized = "".join(
                path.read_text(encoding="utf-8") for path in root.rglob("*.json")
            )
            self.assertNotIn("private-persona", serialized)
            self.assertNotIn("private-session", serialized)
            self.assertNotIn("private-tool-call", serialized)

    def test_failed_photo_does_not_confirm_or_replace_daily_look(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = DailyLookStore(
                Path(tmp) / "looks",
                clock=lambda: datetime(2026, 8, 20, 8, 0, tzinfo=UTC),
            )
            original = store.ensure_today(
                profile_id="private-persona",
                style_anchor="清醒利落",
            )
            assert original is not None
            proposal = DailyLookProposal.from_dict(
                {
                    "title": "酒红小重点",
                    "palette": "炭灰、象牙白和少量酒红",
                    "silhouette": "结构感上装配直线下装",
                    "hero_piece": "酒红色针织上装",
                    "accent": "细金属框眼镜",
                }
            )
            directive = DailyLookDirective.from_dict(
                {
                    "action": "replace_daily",
                    "look_id": original.look_id,
                    "proposal": proposal.to_dict(),
                }
            )
            staged = store.stage_for_photo(
                profile_id="private-persona",
                session_id="private-session",
                tool_use_id="private-tool-call",
                directive=directive,
            )
            self.assertNotEqual(staged.look_id, original.look_id)

            store.discard_for_photo(
                session_id="private-session",
                tool_use_id="private-tool-call",
            )

            current = store.current(profile_id="private-persona")
            self.assertEqual(current, original)
            self.assertEqual(current.status, "planned")

    def test_successful_replace_is_optimistic_and_one_shot_is_non_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = DailyLookStore(
                Path(tmp) / "looks",
                clock=lambda: datetime(2026, 8, 20, 8, 0, tzinfo=UTC),
            )
            original = store.ensure_today(
                profile_id="private-persona",
                style_anchor="清醒利落",
            )
            assert original is not None
            proposal = DailyLookProposal.from_dict(
                {
                    "title": "白衬衫日",
                    "palette": "白色、深蓝和银色",
                    "silhouette": "清楚的上短下长比例",
                    "hero_piece": "线条干净的白衬衫",
                    "accent": "深色腕表",
                }
            )
            replacement = store.stage_for_photo(
                profile_id="private-persona",
                session_id="private-session",
                tool_use_id="private-tool-call",
                directive=DailyLookDirective.from_dict(
                    {
                        "action": "replace_daily",
                        "look_id": original.look_id,
                        "proposal": proposal.to_dict(),
                    }
                ),
            )
            result = store.commit_for_photo(
                profile_id="private-persona",
                session_id="private-session",
                tool_use_id="private-tool-call",
            )
            self.assertTrue(result.committed)
            self.assertEqual(result.look, replacement.with_status("confirmed"))
            self.assertEqual(store.current(profile_id="private-persona"), result.look)

            one_shot = DailyLookDirective.from_dict(
                {"action": "one_shot", "look_id": None, "proposal": None}
            )
            self.assertIsNone(
                store.stage_for_photo(
                    profile_id="private-persona",
                    session_id="another-session",
                    tool_use_id="another-call",
                    directive=one_shot,
                )
            )
            self.assertEqual(store.current(profile_id="private-persona"), result.look)

            with self.assertRaises(DailyLookStoreError):
                store.stage_for_photo(
                    profile_id="private-persona",
                    session_id="stale-session",
                    tool_use_id="stale-call",
                    directive=DailyLookDirective.from_dict(
                        {
                            "action": "replace_daily",
                            "look_id": original.look_id,
                            "proposal": proposal.to_dict(),
                        }
                    ),
                )

    def test_pause_preserves_history_and_resume_can_plan_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = DailyLookStore(
                Path(tmp) / "looks",
                clock=lambda: datetime(2026, 8, 20, 8, 0, tzinfo=UTC),
            )
            original = store.ensure_today(
                profile_id="private-persona",
                style_anchor="自然随性",
            )
            assert original is not None

            store.set_enabled(profile_id="private-persona", enabled=False)
            self.assertIsNone(
                store.ensure_today(
                    profile_id="private-persona",
                    style_anchor="自然随性",
                )
            )
            self.assertEqual(store.current(profile_id="private-persona"), original)

            store.set_enabled(profile_id="private-persona", enabled=True)
            self.assertEqual(
                store.ensure_today(
                    profile_id="private-persona",
                    style_anchor="自然随性",
                ),
                original,
            )

    def test_reroll_custom_note_and_remember_are_explicit_panel_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = DailyLookStore(
                Path(tmp) / "looks",
                clock=lambda: datetime(2026, 8, 20, 8, 0, tzinfo=UTC),
            )
            original = store.ensure_today(
                profile_id="private-persona",
                style_anchor="成熟自信",
            )
            assert original is not None
            rerolled = store.reroll_today(
                profile_id="private-persona",
                style_anchor="成熟自信",
                expected_look_id=original.look_id,
            )
            self.assertNotEqual(rerolled.look_id, original.look_id)
            self.assertEqual(rerolled.revision, original.revision + 1)

            custom = store.customize_today(
                profile_id="private-persona",
                note="更松弛一点，但保留细框眼镜",
                expected_look_id=rerolled.look_id,
            )
            self.assertEqual(custom.source, "user")
            self.assertIn("细框眼镜", custom.hero_piece)
            store.remember_current(
                profile_id="private-persona",
                expected_look_id=custom.look_id,
            )
            self.assertIn(
                custom.theme_id,
                store.snapshot(profile_id="private-persona").preferred_theme_ids,
            )

    def test_image_and_caption_context_keep_style_separate_from_identity_and_expression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = DailyLookStore(
                Path(tmp) / "looks",
                clock=lambda: datetime(2026, 8, 20, 8, 0, tzinfo=UTC),
            )
            look = store.ensure_today(
                profile_id="private-persona",
                style_anchor="高冷、成熟、利落",
            )
            assert look is not None

            image_context = look.render_image_constraints()
            caption_context = look.render_caption_context()
            self.assertIn("只固定今天的穿搭锚点", image_context)
            self.assertIn("不固定发型、表情和妆容", image_context)
            self.assertNotIn("脸型", image_context)
            self.assertIn(look.title, caption_context)
            self.assertIn("画面里确实可见", caption_context)


if __name__ == "__main__":
    unittest.main()
