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
        self.assertEqual(
            set(hooks["hooks"]),
            {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse"},
        )
        serialized_hooks = json.dumps(hooks, ensure_ascii=False)
        self.assertNotIn("statusMessage", serialized_hooks)
        self.assertIn("codex_prompt_context.py", serialized_hooks)
        self.assertIn("codex_image_guard.py", serialized_hooks)
        self.assertIn("image_gen__imagegen", serialized_hooks)
        self.assertGreaterEqual(
            hooks["hooks"]["UserPromptSubmit"][0]["hooks"][0][
                "additionalContextLimit"
            ],
            3_200,
        )

        agent_manifest = (
            PROJECT_ROOT / "skills" / "virtual-companion" / "agents" / "openai.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("allow_implicit_invocation: false", agent_manifest)

    def test_readme_gives_codex_an_unambiguous_plugin_install_prompt(self) -> None:
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        install_prompt_start = readme.index("```text")
        install_prompt_end = readme.index("```", install_prompt_start + 7)
        install_prompt = readme[install_prompt_start:install_prompt_end]

        self.assertIn("把下面整段原样发给 Codex", readme)
        self.assertLessEqual(len(readme.splitlines()), 180)
        self.assertIn("不要调用 skill-installer", install_prompt)
        self.assertIn("install --host codex --apply", install_prompt)
        self.assertIn(
            "不要检查、移动或清理任何用户级 Skill 目录",
            install_prompt,
        )
        self.assertIn("Hook 审核引导和运行验证一起做完", install_prompt)
        self.assertIn("Plugin 文件安装成功不等于已经可以使用", install_prompt)
        self.assertIn("在 Codex 输入 `/hooks`", install_prompt)
        self.assertIn("不要写入 trusted_hash", install_prompt)
        self.assertIn("不要使用 --dangerously-bypass-hook-trust", install_prompt)
        self.assertIn("不要修改用户全局 AGENTS.md", install_prompt)
        self.assertIn("完全退出并重新打开 Codex", install_prompt)
        self.assertIn("不要让我重新上传已有的健康参考图", install_prompt)
        self.assertIn("Persona 已加载，主脸参考已就绪", install_prompt)
        self.assertNotIn("读取 README 中“安装后还差 1 分钟”", readme)
        self.assertNotIn("### 安装后还差 1 分钟", readme)
        self.assertNotIn("### 3. 完成 Codex Hook 审核", readme)
        self.assertEqual(readme.count("在 Codex 输入 `/hooks`"), 1)
        self.assertEqual(readme.count("SessionStart → codex_context.py"), 1)
        self.assertEqual(readme.count("UserPromptSubmit → codex_prompt_context.py"), 1)
        self.assertEqual(readme.count("PreToolUse → codex_image_guard.py"), 1)
        self.assertEqual(readme.count("PostToolUse → codex_image_receipt.py"), 1)
        for hook_name in (
            "SessionStart → codex_context.py",
            "UserPromptSubmit → codex_prompt_context.py",
            "PreToolUse → codex_image_guard.py",
            "PostToolUse → codex_image_receipt.py",
        ):
            self.assertIn(hook_name, install_prompt)
        usage_overview = readme.index("## 装好以后怎么用")
        self.assertLess(install_prompt_end, usage_overview)


if __name__ == "__main__":
    unittest.main()
