import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from companion_kit.cli import _local_port, main
from companion_kit.initializer import initialize_profile
from companion_kit.profile_store import ProfileStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "virtual-companion"


class CliTests(unittest.TestCase):
    def test_local_port_accepts_auto_and_rejects_out_of_range_values(self) -> None:
        self.assertEqual(_local_port("0"), 0)
        self.assertEqual(_local_port("65535"), 65535)

        with self.assertRaises(argparse.ArgumentTypeError):
            _local_port("65536")
        with self.assertRaises(argparse.ArgumentTypeError):
            _local_port("-1")
        with self.assertRaises(argparse.ArgumentTypeError):
            _local_port("not-a-port")

    def test_photo_status_explains_native_and_strict_modes_without_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            profile_path = root / "profiles" / "default.toml"
            initialize_profile(
                skill_root=SKILL_ROOT,
                template_id="warm_healer",
                display_name="小禾",
                output=profile_path,
            )
            output = io.StringIO()
            environment = {"COMPANION_HOME": str(root)}
            with patch.dict(os.environ, environment, clear=True), redirect_stdout(output):
                code = main(["photo", "status", "--config", str(profile_path)])

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(
                payload["modes"]["codex_native"]["setup"],
                "无需单独配置",
            )
            self.assertFalse(payload["modes"]["openai_strict"]["auth_ready"])
            self.assertEqual(payload["modes"]["openai_strict"]["quality"], "high")
            self.assertFalse((root / "private").exists())

    def test_prepare_without_api_key_fails_before_creating_paid_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            profile_path = root / "profiles" / "default.toml"
            initialize_profile(
                skill_root=SKILL_ROOT,
                template_id="warm_healer",
                display_name="小禾",
                output=profile_path,
            )
            error = io.StringIO()
            with (
                patch.dict(os.environ, {"COMPANION_HOME": str(root)}, clear=True),
                redirect_stderr(error),
            ):
                code = main(
                    [
                        "photo",
                        "prepare",
                        "--config",
                        str(profile_path),
                        "--text",
                        "照片：窗边自然光身份参考",
                        "--purpose",
                        "prototype",
                        "--task-scope",
                        "current-task",
                    ]
                )

            self.assertEqual(code, 2)
            self.assertIn("OPENAI_API_KEY", error.getvalue())
            self.assertEqual(list(root.rglob("plan_*.json")), [])

    def test_photo_status_does_not_call_missing_reference_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            profile_path = root / "profiles" / "default.toml"
            initialize_profile(
                skill_root=SKILL_ROOT,
                template_id="warm_healer",
                display_name="小禾",
                output=profile_path,
            )
            store = ProfileStore(skill_root=SKILL_ROOT, profile_path=profile_path)
            current = store.read()
            store.bind_reference(
                reference_id="ref_1234567890abcdef",
                identity_version=1,
                expected_version=current.version,
            )
            output = io.StringIO()
            with (
                patch.dict(os.environ, {"COMPANION_HOME": str(root)}, clear=True),
                redirect_stdout(output),
            ):
                code = main(["photo", "status", "--config", str(profile_path)])

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertTrue(payload["reference_configured"])
            self.assertFalse(payload["reference_ready"])


if __name__ == "__main__":
    unittest.main()
