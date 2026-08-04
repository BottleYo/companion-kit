from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from companion_kit.cli import main
from companion_kit.cli import _config_path
from companion_kit.config import load_profile
from companion_kit.initializer import (
    BUILTIN_TEMPLATES,
    InitializationError,
    default_profile_path,
    initialize_profile,
    prompt_for_profile,
    safe_profile_path,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "virtual-companion"


class InitializerTests(unittest.TestCase):
    def test_system_temp_alias_is_not_treated_as_user_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "profile.toml"

            checked = safe_profile_path(candidate)

            self.assertEqual(checked.name, "profile.toml")

    def test_all_builtin_templates_are_valid_and_generic(self) -> None:
        self.assertGreaterEqual(len(BUILTIN_TEMPLATES), 4)

        for template in BUILTIN_TEMPLATES:
            with self.subTest(template=template.id):
                profile = load_profile(
                    SKILL_ROOT / "assets" / "templates" / template.filename
                )
                self.assertTrue(profile.display_name)
                self.assertIn("虚构成年", profile.visual.appearance)
                self.assertEqual(profile.visual.reference_ids, ())
                self.assertNotIn("手机", profile.visual.default_style)
                self.assertNotIn("自拍", profile.visual.default_style)

    def test_default_profile_path_uses_companion_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"COMPANION_HOME": tmp}):
                self.assertEqual(
                    default_profile_path(),
                    Path(os.path.abspath(tmp)) / "profiles" / "default.toml",
                )

    def test_event_hosts_get_independent_default_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(os.path.abspath(tmp))
            with patch.dict(os.environ, {"COMPANION_HOME": tmp}):
                self.assertEqual(
                    default_profile_path("openclaw"),
                    root / "hosts" / "openclaw" / "profiles" / "default.toml",
                )
                self.assertEqual(
                    default_profile_path("hermes"),
                    root / "hosts" / "hermes" / "profiles" / "default.toml",
                )
                self.assertNotEqual(
                    default_profile_path("openclaw"),
                    default_profile_path("hermes"),
                )

    def test_initialize_profile_uses_template_and_custom_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp).resolve() / "my-companion.toml"

            result = initialize_profile(
                skill_root=SKILL_ROOT,
                template_id="warm_healer",
                display_name="小禾",
                output=output,
            )

            profile = load_profile(output)
            self.assertEqual(result.output, output.resolve())
            self.assertEqual(result.template.id, "warm_healer")
            self.assertEqual(profile.display_name, "小禾")
            self.assertIn("温柔", profile.traits)

    def test_initialize_profile_accepts_simple_relationship_choices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp).resolve() / "my-companion.toml"

            initialize_profile(
                skill_root=SKILL_ROOT,
                template_id="warm_healer",
                display_name="小禾",
                starting_mode="familiar",
                romance_enabled=True,
                output=output,
            )

            profile = load_profile(output)
            self.assertEqual(profile.relationship.starting_mode, "familiar")
            self.assertTrue(profile.relationship.romance_enabled)

    def test_initialize_never_overwrites_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp).resolve() / "existing.toml"
            output.write_text("keep", encoding="utf-8")

            with self.assertRaisesRegex(InitializationError, "已存在"):
                initialize_profile(
                    skill_root=SKILL_ROOT,
                    template_id="warm_healer",
                    display_name=None,
                    output=output,
                )

            self.assertEqual(output.read_text(encoding="utf-8"), "keep")

    def test_force_rejects_symlinked_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            private_file = root / "private.toml"
            private_file.write_text("keep", encoding="utf-8")
            linked_output = root / "linked.toml"
            linked_output.symlink_to(private_file)

            with self.assertRaisesRegex(InitializationError, "符号链接"):
                initialize_profile(
                    skill_root=SKILL_ROOT,
                    template_id="warm_healer",
                    display_name=None,
                    output=linked_output,
                    force=True,
                )

            self.assertEqual(private_file.read_text(encoding="utf-8"), "keep")

    def test_rejects_symlinked_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            actual_directory = root / "actual"
            actual_directory.mkdir()
            linked_directory = root / "linked-directory"
            linked_directory.symlink_to(actual_directory, target_is_directory=True)

            with self.assertRaisesRegex(InitializationError, "符号链接"):
                initialize_profile(
                    skill_root=SKILL_ROOT,
                    template_id="warm_healer",
                    display_name=None,
                    output=linked_directory / "default.toml",
                )

            self.assertFalse((actual_directory / "default.toml").exists())

    def test_rejects_symlinked_default_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            actual_home = root / "actual-home"
            actual_home.mkdir()
            linked_home = root / "linked-home"
            linked_home.symlink_to(actual_home, target_is_directory=True)

            with patch.dict(os.environ, {"COMPANION_HOME": str(linked_home)}):
                with self.assertRaisesRegex(InitializationError, "符号链接"):
                    initialize_profile(
                        skill_root=SKILL_ROOT,
                        template_id="warm_healer",
                        display_name=None,
                    )

            self.assertFalse((actual_home / "profiles" / "default.toml").exists())

    def test_beginner_prompt_only_needs_template_and_name(self) -> None:
        answers = iter(["2", "小星"])
        shown: list[str] = []

        template_id, display_name = prompt_for_profile(
            input_fn=lambda prompt: next(answers),
            output_fn=shown.append,
        )

        self.assertEqual(template_id, BUILTIN_TEMPLATES[1].id)
        self.assertEqual(display_name, "小星")
        self.assertTrue(any("直接按回车" in line for line in shown))

    def test_prompt_skips_name_question_when_name_was_provided(self) -> None:
        answers = iter(["3"])

        template_id, display_name = prompt_for_profile(
            display_name="小序",
            input_fn=lambda prompt: next(answers),
            output_fn=lambda line: None,
        )

        self.assertEqual(template_id, "calm_partner")
        self.assertEqual(display_name, "小序")

    def test_cli_init_json_and_default_validate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"COMPANION_HOME": str(Path(tmp).resolve())}):
                output = StringIO()
                with redirect_stdout(output):
                    code = main(
                        [
                            "init",
                            "--template",
                            "calm_partner",
                            "--display-name",
                            "阿序",
                            "--json",
                        ]
                    )

                payload = json.loads(output.getvalue())
                self.assertEqual(code, 0)
                self.assertEqual(payload["template_id"], "calm_partner")
                self.assertEqual(payload["starting_mode"], "natural")
                self.assertFalse(payload["romance_enabled"])
                profile_path = Path(payload["output"])
                self.assertTrue(profile_path.is_file())
                if os.name != "nt":
                    self.assertEqual(
                        stat.S_IMODE(profile_path.parent.stat().st_mode),
                        0o700,
                    )
                    self.assertEqual(
                        stat.S_IMODE(profile_path.stat().st_mode),
                        0o600,
                    )

                validated = StringIO()
                with redirect_stdout(validated):
                    validate_code = main(["validate"])

                self.assertEqual(validate_code, 0)
                self.assertTrue(json.loads(validated.getvalue())["valid"])

    def test_config_path_priority_is_explicit_then_environment_then_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with patch.dict(
                os.environ,
                {
                    "COMPANION_HOME": str(root / "home"),
                    "COMPANION_PROFILE": str(root / "environment.toml"),
                },
            ):
                self.assertEqual(
                    _config_path(str(root / "explicit.toml")),
                    root / "explicit.toml",
                )
                self.assertEqual(
                    _config_path(None),
                    root / "environment.toml",
                )

            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(root / "home")},
                clear=True,
            ):
                self.assertEqual(
                    _config_path(None),
                    root / "home" / "profiles" / "default.toml",
                )
