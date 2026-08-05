from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from companion_kit.codex_runtime import load_codex_runtime_context
from companion_kit.initializer import initialize_profile


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
            self.assertIn("完整使用 Codex 原有能力", rendered)
            self.assertIn("允许恋爱发展不等于已经是恋人", rendered)
            self.assertIn("更有个人感的日常照", rendered)
            self.assertIn("直接使用 Codex 内置图片生成能力", rendered)
            self.assertIn("不要检查或索要 OPENAI_API_KEY", rendered)
            self.assertNotIn(str(home), rendered)
            self.assertLessEqual(len(rendered), 6_000)

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
            self.assertNotIn(private_marker, completed.stdout)

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
            self.assertFalse(home.exists())


if __name__ == "__main__":
    unittest.main()
