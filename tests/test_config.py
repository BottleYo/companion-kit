from pathlib import Path
import tempfile
import unittest

from companion_kit.config import ConfigError, load_profile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_PROFILE = (
    PROJECT_ROOT / "skills" / "virtual-companion" / "assets" / "demo_companion.toml"
)


class ConfigTests(unittest.TestCase):
    def test_loads_schema_three_with_optional_visual_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "persona-v3.toml"
            path.write_text(
                """
schema_version = 3
id = "companion"
display_name = "岚"
template_id = "custom"
intent_summary = "高冷、成熟，但会认真帮忙"

[persona]
traits = ["克制", "成熟", "可靠"]
speaking_style = "话不多，熟悉后会露出温柔。"
boundaries = ["不编造共同经历", "具体任务优先准确完成"]
background = "有自己的审美和生活节奏，但不虚构现实身份。"
values = ["尊重", "诚实", "分寸"]
interests = ["阅读", "城市散步"]
task_style = "先给清楚结论，再处理关键细节。"

[appearance]
direction = "虚构成年女性，成熟、利落、有距离感。"
default_style = "自然生活摄影，真实皮肤质感。"
default_hairstyle = "根据场景变化"
default_expression = "克制自然"
default_makeup = "根据场景选择"
default_wardrobe = "简洁、有质感"

[visual_identity]
status = "unset"
facial_anchor = ""
identity_version = 1
reference_ids = []

[relationship]
starting_mode = "natural"
romance_enabled = false

[provenance]
user_fields = ["display_name", "intent_summary"]
generated_fields = ["traits", "speaking_style", "appearance.direction"]
""".strip(),
                encoding="utf-8",
            )

            profile = load_profile(path)

            self.assertEqual(profile.schema_version, 3)
            self.assertEqual(profile.template_id, "custom")
            self.assertEqual(profile.background, "有自己的审美和生活节奏，但不虚构现实身份。")
            self.assertEqual(profile.visual.identity_status, "unset")
            self.assertFalse(profile.visual.identity_anchor)
            self.assertEqual(profile.visual.reference_ids, ())
            self.assertEqual(profile.visual.default_hairstyle, "根据场景变化")
            self.assertIn("intent_summary", profile.provenance.user_fields)

    def test_locked_schema_three_identity_requires_one_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid-identity.toml"
            path.write_text(
                """
schema_version = 3
id = "companion"
display_name = "岚"
template_id = "custom"
intent_summary = "成熟可靠"

[persona]
traits = ["成熟"]
speaking_style = "自然。"
boundaries = []
background = "虚构成年人物。"
values = ["尊重"]
interests = []
task_style = "准确完成任务。"

[appearance]
direction = "虚构成年女性。"
default_style = "自然生活摄影。"
default_hairstyle = "随场景变化"
default_expression = "自然"
default_makeup = "随场景变化"
default_wardrobe = "随场景变化"

[visual_identity]
status = "locked"
facial_anchor = ""
identity_version = 1
reference_ids = []

[relationship]
starting_mode = "natural"
romance_enabled = false

[provenance]
user_fields = []
generated_fields = []
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigError, "恰好一张"):
                load_profile(path)

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
