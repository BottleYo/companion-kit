from pathlib import Path
import tempfile
import unittest

from companion_kit.config import ConfigError, load_profile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_PROFILE = (
    PROJECT_ROOT / "skills" / "virtual-companion" / "assets" / "demo_companion.toml"
)


class ConfigTests(unittest.TestCase):
    def test_loads_generic_demo_profile(self) -> None:
        profile = load_profile(DEMO_PROFILE)

        self.assertEqual(profile.schema_version, 2)
        self.assertEqual(profile.id, "demo_companion")
        self.assertEqual(profile.display_name, "示例陪伴对象")
        self.assertTrue(profile.visual.identity_anchor)
        self.assertEqual(profile.visual.reference_ids, ())
        self.assertEqual(profile.visual.identity_version, 1)
        self.assertEqual(profile.relationship.starting_mode, "natural")
        self.assertFalse(profile.relationship.romance_enabled)

    def test_version_one_profile_gets_safe_relationship_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.toml"
            path.write_text(
                """
schema_version = 1
id = "demo_companion"
display_name = "Demo"

[persona]
traits = ["friendly"]
speaking_style = "clear"
boundaries = []

[visual]
identity_anchor = "demo-v1"
appearance = "fictional adult"
default_style = "natural"
reference_ids = []
""".strip(),
                encoding="utf-8",
            )

            profile = load_profile(path)

            self.assertEqual(profile.schema_version, 1)
            self.assertEqual(profile.visual.identity_version, 1)
            self.assertEqual(profile.relationship.starting_mode, "natural")
            self.assertFalse(profile.relationship.romance_enabled)

    def test_rejects_secrets_in_persona_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unsafe.toml"
            path.write_text(
                """
schema_version = 1
id = "demo_companion"
display_name = "Demo"
api_key = "must-not-live-here"

[persona]
traits = ["friendly"]
speaking_style = "clear"
boundaries = []

[visual]
identity_anchor = "demo-v1"
appearance = "fictional adult"
default_style = "natural"
reference_ids = []
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigError, "敏感字段"):
                load_profile(path)

    def test_rejects_reference_file_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unsafe.toml"
            private_path = Path("/").joinpath("private", "photo.png")
            path.write_text(
                f"""
schema_version = 1
id = "demo_companion"
display_name = "Demo"

[persona]
traits = ["friendly"]
speaking_style = "clear"
boundaries = []

[visual]
identity_anchor = "demo-v1"
appearance = "fictional adult"
default_style = "natural"
reference_path = "{private_path}"
reference_ids = []
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigError, "路径"):
                load_profile(path)

    def test_rejects_path_hidden_in_allowed_text_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unsafe.toml"
            private_path = Path("/").joinpath(
                "Users", "example", "private", "portrait.jpg"
            )
            path.write_text(
                f"""
schema_version = 1
id = "demo_companion"
display_name = "Demo"

[persona]
traits = ["friendly"]
speaking_style = "clear"
boundaries = []

[visual]
identity_anchor = "demo-v1"
appearance = "reference at {private_path}"
default_style = "natural"
reference_ids = []
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigError, "本机路径"):
                load_profile(path)

    def test_rejects_non_string_persona_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unsafe.toml"
            path.write_text(
                """
schema_version = 1
id = "demo_companion"
display_name = "Demo"

[persona]
traits = ["friendly", 7]
speaking_style = "clear"
boundaries = []

[visual]
identity_anchor = "demo-v1"
appearance = "fictional adult"
default_style = "natural"
reference_ids = []
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigError, "字符串数组"):
                load_profile(path)


if __name__ == "__main__":
    unittest.main()
