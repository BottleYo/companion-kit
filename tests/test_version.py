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
        self.assertEqual(claude["version"], project_version)

    def test_codex_plugin_uses_quiet_runtime_and_explicit_skill(self) -> None:
        manifest = json.loads(
            (PROJECT_ROOT / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        prompts = manifest["interface"]["defaultPrompt"]
        self.assertIsInstance(prompts, list)
        self.assertTrue(prompts)
        self.assertNotIn("$virtual-companion", " ".join(prompts))

        hooks = json.loads(
            (PROJECT_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(hooks["hooks"]), {"SessionStart"})
        serialized_hooks = json.dumps(hooks, ensure_ascii=False)
        self.assertNotIn("statusMessage", serialized_hooks)
        self.assertNotIn("UserPromptSubmit", serialized_hooks)

        agent_manifest = (
            PROJECT_ROOT / "skills" / "virtual-companion" / "agents" / "openai.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("allow_implicit_invocation: false", agent_manifest)


if __name__ == "__main__":
    unittest.main()
