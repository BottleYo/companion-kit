from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from companion_kit.codex_runtime import load_codex_runtime_context
from companion_kit.companion_scope import CompanionScopeStore
from companion_kit.daily_look_store import DailyLookStore
from companion_kit.hook_health import HookHealthStore, SESSION_START
from companion_kit.identity_pack import BODY_SHAPE, PROFILE_FACE
from companion_kit.initializer import initialize_profile
from companion_kit.image_assets import ImageAssetStore
from companion_kit.profile_store import ProfileStore
from tests.png_fixture import tiny_png


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "virtual-companion"
HOOK = PROJECT_ROOT / "hooks" / "codex_context.py"


class CodexRuntimeTests(unittest.TestCase):
    def test_missing_profile_is_silent_and_does_not_create_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve() / "companion-home"
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                self.assertIsNone(load_codex_runtime_context())

            self.assertFalse(home.exists())

    def test_runtime_context_is_natural_bounded_and_task_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve() / "companion-home"
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                initialize_profile(
                    skill_root=SKILL_ROOT,
                    template_id="warm_healer",
                    display_name="小禾",
                    starting_mode="familiar",
                    romance_enabled=True,
                )
                runtime = load_codex_runtime_context(
                    now=datetime(2026, 8, 5, tzinfo=UTC)
                )

            self.assertIsNotNone(runtime)
            rendered = runtime.render()
            self.assertIn("小禾", rendered)
            self.assertIn("用户对这个人物的原始期待", rendered)
            self.assertIn("完整使用 Codex 原有能力", rendered)
            self.assertIn("允许恋爱发展不等于已经是恋人", rendered)
            self.assertIn("代码正确性、测试和用户的技术要求优先", rendered)
            self.assertNotIn("referenced_image_paths", rendered)
            self.assertNotIn("identity stage-native", rendered)
            self.assertNotIn("OPENAI_API_KEY", rendered)
            self.assertNotIn("修改 Codex 全局个性化", rendered)
            self.assertNotIn(str(home), rendered)
            self.assertLessEqual(len(rendered), 1_400)

    def test_maximum_persona_keeps_session_context_small_and_task_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve() / "companion-home"
            with patch.dict(os.environ, {"COMPANION_HOME": str(home)}, clear=True):
                initialize_profile(
                    skill_root=SKILL_ROOT,
                    template_id="warm_healer",
                    display_name="小禾",
                )
                runtime = load_codex_runtime_context()

            repeated = "细腻但不复述标签" * 100
            crowded = replace(
                runtime,
                profile=replace(
                    runtime.profile,
                    intent_summary=repeated,
                    traits=tuple(repeated for _ in range(12)),
                    speaking_style=repeated,
                    boundaries=tuple(repeated for _ in range(12)),
                    background=repeated,
                    values=tuple(repeated for _ in range(12)),
                    interests=tuple(repeated for _ in range(12)),
                    task_style=repeated,
                    visual=replace(runtime.profile.visual, appearance=repeated),
                ),
            )
            rendered = crowded.render(
                control_path=SKILL_ROOT / "scripts" / "companionctl.py",
                task_scope="codex-max-persona-task",
            )

            self.assertLessEqual(len(rendered), 1_400)
            self.assertIn("代码正确性、测试和用户的技术要求优先", rendered)
            self.assertNotIn("identity stage-native", rendered)
            self.assertNotIn("generated_images", rendered)

    def test_session_context_keeps_later_persona_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve() / "companion-home"
            with patch.dict(os.environ, {"COMPANION_HOME": str(home)}, clear=True):
                initialize_profile(
                    skill_root=SKILL_ROOT,
                    template_id="warm_healer",
                    display_name="小禾",
                )
                runtime = load_codex_runtime_context()

            bounded = replace(
                runtime,
                profile=replace(
                    runtime.profile,
                    boundaries=(
                        "不编造共同经历",
                        "不替用户做高风险决定",
                        "不把关系等级告诉用户",
                        "用户说停时立即停止暧昧表达",
                    ),
                ),
            )
            rendered = bounded.render()

            self.assertIn("用户说停时立即停止暧昧表达", rendered)
            self.assertLessEqual(len(rendered), 1_400)

    def test_hook_ignores_unrelated_input_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve() / "companion-home"
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                initialize_profile(
                    skill_root=SKILL_ROOT,
                    template_id="calm_partner",
                    display_name="阿序",
                )
                CompanionScopeStore().bind("opaque-session")

            environment = os.environ.copy()
            environment["COMPANION_HOME"] = str(home)
            environment["PLUGIN_ROOT"] = str(PROJECT_ROOT)
            environment.pop("COMPANION_PROFILE", None)
            private_marker = "PRIVATE_PROMPT_MUST_NOT_RETURN"
            completed = subprocess.run(
                ["python3", str(HOOK)],
                input=json.dumps(
                    {
                        "hook_event_name": "SessionStart",
                        "source": "startup",
                        "session_id": "opaque-session",
                        "prompt": private_marker,
                    }
                ),
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )

            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stderr, "")
            output = json.loads(completed.stdout)
            context = output["hookSpecificOutput"]["additionalContext"]
            self.assertIn("阿序", context)
            self.assertNotIn("generated_images", context)
            self.assertNotIn("referenced_image_paths", context)
            self.assertNotIn(private_marker, completed.stdout)
            self.assertLessEqual(len(context), 1_400)
            health = HookHealthStore(
                root=home / "system" / "hook-health",
                plugin_root=PROJECT_ROOT,
            ).status(SESSION_START)
            self.assertTrue(health.verified)

    def test_hook_without_profile_has_no_visible_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve() / "companion-home"
            environment = os.environ.copy()
            environment["COMPANION_HOME"] = str(home)
            environment["PLUGIN_ROOT"] = str(PROJECT_ROOT)
            environment.pop("COMPANION_PROFILE", None)
            completed = subprocess.run(
                ["python3", str(HOOK)],
                input=json.dumps(
                    {
                        "hook_event_name": "SessionStart",
                        "source": "startup",
                    }
                ),
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )

            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr, "")
            self.assertFalse((home / "profiles" / "default.toml").exists())
            health = HookHealthStore(
                root=home / "system" / "hook-health",
                plugin_root=PROJECT_ROOT,
            ).status(SESSION_START)
            self.assertTrue(health.verified)

    def test_locked_identity_exposes_private_reference_only_to_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve() / "companion-home"
            with patch.dict(os.environ, {"COMPANION_HOME": str(home)}, clear=True):
                initialize_profile(
                    skill_root=SKILL_ROOT,
                    template_id="warm_healer",
                    display_name="小禾",
                )
                store = ProfileStore(skill_root=SKILL_ROOT)
                snapshot = store.read()
                assets = ImageAssetStore(home / "private" / "images")
                candidate = assets.store_candidate(
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    image_bytes=tiny_png(),
                    task_scope="codex-task-one",
                )
                reference = assets.confirm_candidate(
                    candidate_id=candidate.candidate_id,
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    task_scope="codex-task-one",
                )
                store.bind_reference(
                    reference_id=reference.reference_id,
                    identity_version=1,
                    expected_version=snapshot.version,
                )

                runtime = load_codex_runtime_context()
                rendered = runtime.render(
                    control_path=SKILL_ROOT / "scripts" / "companionctl.py",
                    task_scope="codex-task-two",
                )
                photo_rendered = runtime.render_photo(
                    mode="new",
                    turn_token="ckp_" + "a" * 24,
                    control_path=SKILL_ROOT / "scripts" / "companionctl.py",
                    task_scope="codex-task-two",
                )
                runtime.identity_reference.unlink()
                session_runtime = load_codex_runtime_context(include_identity=False)
                session_rendered = session_runtime.render()

            self.assertNotIn(str(runtime.identity_reference), rendered)
            self.assertNotIn("referenced_image_paths", rendered)
            self.assertIn(str(runtime.identity_reference), photo_rendered)
            self.assertIn("referenced_image_paths", photo_rendered)
            self.assertIn("唯一身份参考包", photo_rendered)
            self.assertIn("COMPANION_KIT_PHOTO_V3", photo_rendered)
            self.assertIn('"action":"one_shot"', photo_rendered)
            self.assertIn('"makeup":"<enum>"', photo_rendered)
            self.assertIn("妆容不机械逐张换", photo_rendered)
            self.assertNotIn("OPENAI_API_KEY", photo_rendered)
            self.assertFalse(session_runtime.identity_reference_error)
            self.assertIn("小禾", session_rendered)
            self.assertNotIn("暂停拍照", session_rendered)

    def test_runtime_exposes_verified_pack_roles_and_fails_closed_as_one_unit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve() / "companion-home"
            with patch.dict(os.environ, {"COMPANION_HOME": str(home)}, clear=True):
                initialize_profile(
                    skill_root=SKILL_ROOT,
                    template_id="warm_healer",
                    display_name="小禾",
                )
                store = ProfileStore(skill_root=SKILL_ROOT)
                snapshot = store.read()
                assets = ImageAssetStore(home / "private" / "images")
                primary_candidate = assets.store_candidate(
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    image_bytes=tiny_png(rgba=b"\x20\x40\x60\xff"),
                    task_scope="primary-task",
                )
                primary = assets.confirm_candidate(
                    candidate_id=primary_candidate.candidate_id,
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    task_scope="primary-task",
                )
                store.bind_reference(
                    reference_id=primary.reference_id,
                    identity_version=1,
                    expected_version=snapshot.version,
                )
                side_candidate = assets.store_candidate(
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    image_bytes=tiny_png(rgba=b"\x60\x40\x20\xff"),
                    task_scope="side-task",
                    role=PROFILE_FACE,
                    primary_reference_id=primary.reference_id,
                )
                side = assets.confirm_candidate(
                    candidate_id=side_candidate.candidate_id,
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    task_scope="side-task",
                )
                body_candidate = assets.store_candidate(
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    image_bytes=tiny_png(rgba=b"\x10\x80\x30\xff"),
                    task_scope="body-task",
                    role=BODY_SHAPE,
                    primary_reference_id=primary.reference_id,
                )
                body = assets.confirm_candidate(
                    candidate_id=body_candidate.candidate_id,
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    task_scope="body-task",
                )

                runtime = load_codex_runtime_context()
                rendered = runtime.render(
                    control_path=SKILL_ROOT / "scripts" / "companionctl.py",
                    task_scope="next-task",
                )
                photo_rendered = runtime.render_photo(
                    mode="new",
                    turn_token="ckp_" + "b" * 24,
                    control_path=SKILL_ROOT / "scripts" / "companionctl.py",
                    task_scope="next-task",
                )

                self.assertIn(str(primary.reference_id), str(runtime.identity_pack.primary_reference_id))
                self.assertNotIn(str(side.path), rendered)
                self.assertNotIn(str(body.path), rendered)
                self.assertIn(str(side.path), photo_rendered)
                self.assertIn(str(body.path), photo_rendered)
                self.assertIn("主脸", photo_rendered)
                self.assertIn("侧脸", photo_rendered)
                self.assertIn("体型", photo_rendered)
                self.assertIn("最多使用两张", photo_rendered)
                self.assertIn("全身", photo_rendered)
                self.assertLessEqual(len(rendered), 1_400)

                repeated = "细腻但不复述标签" * 100
                crowded = replace(
                    runtime,
                    profile=replace(
                        runtime.profile,
                        intent_summary=repeated,
                        traits=tuple(repeated for _ in range(12)),
                        speaking_style=repeated,
                        boundaries=tuple(repeated for _ in range(12)),
                        background=repeated,
                        values=tuple(repeated for _ in range(12)),
                        interests=tuple(repeated for _ in range(12)),
                        task_style=repeated,
                        visual=replace(runtime.profile.visual, appearance=repeated),
                    ),
                )
                crowded_rendered = crowded.render_photo(
                    mode="new",
                    turn_token="ckp_" + "c" * 24,
                    control_path=SKILL_ROOT / "scripts" / "companionctl.py",
                    task_scope="next-task",
                )
                self.assertLessEqual(len(crowded_rendered), 3_200)
                self.assertIn("最多使用两张", crowded_rendered)
                self.assertIn("图片工具没有真正接收", crowded_rendered)

                side.path.unlink()
                broken = load_codex_runtime_context()
                broken_rendered = broken.render_photo(
                    mode="new",
                    turn_token="ckp_" + "d" * 24,
                )

            self.assertTrue(broken.identity_reference_error)
            self.assertIn("暂停", broken_rendered)
            self.assertNotIn(str(primary.path), broken_rendered)
            self.assertNotIn(str(side.path), broken_rendered)
            self.assertNotIn(str(body.path), broken_rendered)

    def test_photo_context_carries_today_look_without_locking_hair_makeup_or_expression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve() / "companion-home"
            with patch.dict(os.environ, {"COMPANION_HOME": str(home)}, clear=True):
                initialize_profile(
                    skill_root=SKILL_ROOT,
                    template_id="calm_partner",
                    display_name="简宁",
                )
                runtime = load_codex_runtime_context()
                look = DailyLookStore(
                    home / "private" / "daily-looks",
                    clock=lambda: datetime(2026, 8, 20, 8, 0, tzinfo=UTC),
                ).ensure_today(
                    profile_id=runtime.profile.id,
                    style_anchor=(
                        runtime.profile.visual.appearance
                        + "；"
                        + runtime.profile.visual.default_wardrobe
                    ),
                )
                assert look is not None

                rendered = runtime.render_photo(
                    mode="new",
                    turn_token="ckp_" + "e" * 24,
                    daily_look=look,
                )
                chat_context = runtime.render_daily_look(look)

            self.assertIn("COMPANION_KIT_PHOTO_V3", rendered)
            self.assertIn(look.look_id, rendered)
            self.assertIn(look.title, rendered)
            self.assertIn('"action":"use_daily"', rendered)
            self.assertIn("不固定发型、表情和妆容", rendered)
            self.assertLessEqual(len(rendered), 3_600)
            self.assertIn(look.title, chat_context)
            self.assertNotIn(look.look_id, chat_context)
            self.assertNotIn("Hook", chat_context)


if __name__ == "__main__":
    unittest.main()
