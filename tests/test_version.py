import json
from pathlib import Path
import re
import unittest

import companion_kit


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class VersionTests(unittest.TestCase):
    def test_package_and_plugin_versions_match(self) -> None:
        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        project_version = re.search(
            r'^version = "([^"]+)"$',
            pyproject,
            flags=re.MULTILINE,
        ).group(1)
        codex = json.loads(
            (PROJECT_ROOT / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        claude = json.loads(
            (PROJECT_ROOT / ".claude-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(companion_kit.__version__, project_version)
        self.assertEqual(codex["version"], project_version)
        self.assertEqual(claude["version"], "0.6.0-dev.1")

    def test_codex_plugin_uses_quiet_runtime_without_installing_a_skill(self) -> None:
        manifest = json.loads(
            (PROJECT_ROOT / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("skills", manifest)
        prompts = manifest["interface"]["defaultPrompt"]
        self.assertIsInstance(prompts, list)
        self.assertTrue(prompts)
        self.assertNotIn("$virtual-companion", " ".join(prompts))

        hooks = json.loads(
            (PROJECT_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(hooks["hooks"]), {"SessionStart", "PostToolUse"})
        serialized_hooks = json.dumps(hooks, ensure_ascii=False)
        self.assertNotIn("statusMessage", serialized_hooks)
        self.assertNotIn("UserPromptSubmit", serialized_hooks)
        self.assertIn("image_gen__imagegen", serialized_hooks)

        agent_manifest = (
            PROJECT_ROOT / "skills" / "virtual-companion" / "agents" / "openai.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("allow_implicit_invocation: false", agent_manifest)

    def test_readme_gives_codex_an_unambiguous_plugin_install_prompt(self) -> None:
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("把这段原样发给它", readme)
        self.assertIn("不要调用 skill-installer", readme)
        self.assertIn("install --host codex --apply", readme)
        self.assertIn("不要删除 Companion Kit 的本地数据目录", readme)


if __name__ == "__main__":
    unittest.main()
