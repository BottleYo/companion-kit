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


if __name__ == "__main__":
    unittest.main()
