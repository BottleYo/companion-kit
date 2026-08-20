from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from companion_kit.companion_scope import CompanionScopeStore
from companion_kit.hook_health import (
    COMPANION_CONTEXT,
    SESSION_START,
    HookHealthStore,
)
from companion_kit.initializer import initialize_profile
from companion_kit.photo_moment_store import PhotoMomentStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "virtual-companion"
SESSION_HOOK = PROJECT_ROOT / "hooks" / "codex_context.py"
PROMPT_HOOK = PROJECT_ROOT / "hooks" / "codex_prompt_context.py"
IMAGE_GUARD_HOOK = PROJECT_ROOT / "hooks" / "codex_image_guard.py"


def _run_hook(
    path: Path,
    payload: dict[str, object],
    environment: dict[str, str],
):
    return subprocess.run(
        ["python3", str(path)],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
        env=environment,
        timeout=5,
    )


class CodexScopeHookTests(unittest.TestCase):
    def _environment(self, root: Path) -> tuple[dict[str, str], Path]:
        home = root / "companion-home"
        environment = os.environ.copy()
        environment.update(
            {
                "COMPANION_HOME": str(home),
                "PLUGIN_ROOT": str(PROJECT_ROOT),
            }
        )
        environment.pop("COMPANION_PROFILE", None)
        return environment, home

    def _initialize(self, home: Path) -> None:
        with patch.dict(
            os.environ,
            {"COMPANION_HOME": str(home)},
            clear=False,
        ):
            initialize_profile(
                skill_root=SKILL_ROOT,
                template_id="warm_healer",
                display_name="小禾",
            )

    def test_unbound_session_start_is_silent_but_health_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home = self._environment(root)
            self._initialize(home)

            completed = _run_hook(
                SESSION_HOOK,
                {
                    "hook_event_name": "SessionStart",
                    "session_id": "ordinary-coding-task",
                },
                environment,
            )

            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr, "")
            health = HookHealthStore(
                root=home / "system" / "hook-health",
                plugin_root=PROJECT_ROOT,
            )
            self.assertTrue(health.status(SESSION_START).verified)
            self.assertEqual(health.status(COMPANION_CONTEXT).state, "missing")

    def test_binding_prompt_injects_persona_and_persists_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home = self._environment(root)
            self._initialize(home)

            completed = _run_hook(
                PROMPT_HOOK,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "companion-task",
                    "turn_id": "bind-turn",
                    "prompt": "把这个任务设为陪伴任务",
                },
                environment,
            )

            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stderr, "")
            context = json.loads(completed.stdout)["hookSpecificOutput"][
                "additionalContext"
            ]
            self.assertIn("小禾", context)
            self.assertIn("完整使用 Codex 原有能力", context)
            self.assertNotIn("referenced_image_paths", context)
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=False,
            ):
                self.assertTrue(CompanionScopeStore().is_bound("companion-task"))
            health = HookHealthStore(
                root=home / "system" / "hook-health",
                plugin_root=PROJECT_ROOT,
            )
            self.assertTrue(health.status(COMPANION_CONTEXT).verified)

    def test_bound_session_start_injects_persona(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home = self._environment(root)
            self._initialize(home)
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=False,
            ):
                CompanionScopeStore().bind("companion-task")

            completed = _run_hook(
                SESSION_HOOK,
                {
                    "hook_event_name": "SessionStart",
                    "session_id": "companion-task",
                    "source": "resume",
                },
                environment,
            )

            context = json.loads(completed.stdout)["hookSpecificOutput"][
                "additionalContext"
            ]
            self.assertIn("小禾", context)
            self.assertNotIn("referenced_image_paths", context)

    def test_unbind_removes_scope_and_future_session_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home = self._environment(root)
            self._initialize(home)
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=False,
            ):
                CompanionScopeStore().bind("companion-task")

            unbound = _run_hook(
                PROMPT_HOOK,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "companion-task",
                    "turn_id": "unbind-turn",
                    "prompt": "退出陪伴任务",
                },
                environment,
            )
            session = _run_hook(
                SESSION_HOOK,
                {
                    "hook_event_name": "SessionStart",
                    "session_id": "companion-task",
                },
                environment,
            )

            unbind_context = json.loads(unbound.stdout)["hookSpecificOutput"][
                "additionalContext"
            ]
            self.assertIn("不再加载 Persona", unbind_context)
            self.assertEqual(session.stdout, "")
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=False,
            ):
                self.assertFalse(CompanionScopeStore().is_bound("companion-task"))

    def test_unbound_photo_remains_one_shot_and_does_not_bind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home = self._environment(root)
            self._initialize(home)

            completed = _run_hook(
                PROMPT_HOOK,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "one-shot-task",
                    "turn_id": "photo-turn",
                    "prompt": "拍一张你的自拍给我",
                },
                environment,
            )

            self.assertEqual(completed.returncode, 0)
            context = json.loads(completed.stdout)["hookSpecificOutput"][
                "additionalContext"
            ]
            self.assertIn("COMPANION_KIT_PHOTO_V3", context)
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=False,
            ):
                self.assertFalse(CompanionScopeStore().is_bound("one-shot-task"))
                self.assertIsNotNone(
                    PhotoMomentStore().turn_ticket(
                        session_id="one-shot-task",
                        turn_id="photo-turn",
                    )
                )

    def test_bind_plus_photo_stops_image_until_next_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home = self._environment(root)
            self._initialize(home)

            completed = _run_hook(
                PROMPT_HOOK,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "combined-task",
                    "turn_id": "combined-turn",
                    "prompt": "把这个任务设为陪伴任务，然后拍张自拍",
                },
                environment,
            )

            context = json.loads(completed.stdout)["hookSpecificOutput"][
                "additionalContext"
            ]
            self.assertIn("本轮不要调用 imagegen", context)
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=False,
            ):
                self.assertTrue(CompanionScopeStore().is_bound("combined-task"))
                self.assertIsNotNone(
                    PhotoMomentStore().turn_ticket(
                        session_id="combined-task", turn_id="combined-turn"
                    )
                )

            guarded = _run_hook(
                IMAGE_GUARD_HOOK,
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "combined-task",
                    "turn_id": "combined-turn",
                    "tool_use_id": "unexpected-image-call",
                    "tool_name": "image_gen__imagegen",
                    "tool_input": {"prompt": "一张人物自拍"},
                },
                environment,
            )
            decision = json.loads(guarded.stdout)["hookSpecificOutput"]
            self.assertEqual(decision["permissionDecision"], "deny")
            self.assertIn("缺少控制信封", decision["permissionDecisionReason"])

    def test_bound_task_generic_image_without_ticket_remains_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home = self._environment(root)
            self._initialize(home)
            with patch.dict(os.environ, {"COMPANION_HOME": str(home)}, clear=False):
                CompanionScopeStore().bind("companion-task")

            completed = _run_hook(
                IMAGE_GUARD_HOOK,
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "companion-task",
                    "turn_id": "generic-image-turn",
                    "tool_use_id": "generic-image-call",
                    "tool_name": "image_gen__imagegen",
                    "tool_input": {"prompt": "生成一张产品落地页背景图"},
                },
                environment,
            )
            self.assertEqual(completed.stdout, "")

    def test_unconfigured_meta_discussion_is_completely_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, _home = self._environment(root)

            for prompt in (
                "分析一下陪伴任务应该怎么实现",
                "帮我给这个产品拍张照片",
            ):
                with self.subTest(prompt=prompt):
                    completed = _run_hook(
                        PROMPT_HOOK,
                        {
                            "hook_event_name": "UserPromptSubmit",
                            "session_id": "ordinary-task",
                            "turn_id": "ordinary-turn",
                            "prompt": prompt,
                        },
                        environment,
                    )
                    self.assertEqual(completed.stdout, "")

    def test_binding_without_persona_does_not_persist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home = self._environment(root)

            completed = _run_hook(
                PROMPT_HOOK,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "empty-task",
                    "turn_id": "bind-turn",
                    "prompt": "把这个任务设为陪伴任务",
                },
                environment,
            )

            context = json.loads(completed.stdout)["hookSpecificOutput"][
                "additionalContext"
            ]
            self.assertIn("尚未配置 Persona", context)
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=False,
            ):
                self.assertFalse(CompanionScopeStore().is_bound("empty-task"))


if __name__ == "__main__":
    unittest.main()
