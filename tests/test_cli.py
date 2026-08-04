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
from companion_kit.openai_image_api import ImageApiResult
from companion_kit.profile_store import ProfileStore
from tests.png_fixture import tiny_png


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

    def test_event_host_init_and_status_use_independent_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            initialized = io.StringIO()
            status_output = io.StringIO()
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(root)},
                clear=True,
            ):
                with redirect_stdout(initialized):
                    init_code = main(
                        [
                            "init",
                            "--host",
                            "hermes",
                            "--template",
                            "warm_healer",
                            "--display-name",
                            "小禾",
                            "--json",
                        ]
                    )
                with redirect_stdout(status_output):
                    status_code = main(
                        ["event-photo", "status", "--host", "hermes"]
                    )

            initialized_payload = json.loads(initialized.getvalue())
            status_payload = json.loads(status_output.getvalue())
            expected_profile = (
                root / "hosts" / "hermes" / "profiles" / "default.toml"
            )
            self.assertEqual(init_code, 0)
            self.assertEqual(initialized_payload["host"], "hermes")
            self.assertEqual(Path(initialized_payload["output"]), expected_profile)
            self.assertTrue(expected_profile.is_file())
            self.assertEqual(status_code, 0)
            self.assertEqual(status_payload["host"], "hermes")
            self.assertFalse(status_payload["strict"]["auth_ready"])
            self.assertNotIn("native_preview", status_payload)
            self.assertFalse((root / "hosts" / "hermes" / "private").exists())

    def test_beginner_init_hint_matches_selected_host(self) -> None:
        expected_hints = {
            "openclaw": "OpenClaw 可先使用宿主管理的原生快速模式",
            "hermes": "Hermes 的固定形象严格模式",
            "claude": "Claude 当前提供安全的照片计划",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(root)},
                clear=True,
            ):
                for host, expected in expected_hints.items():
                    with self.subTest(host=host):
                        output = io.StringIO()
                        with redirect_stdout(output):
                            code = main(
                                [
                                    "init",
                                    "--host",
                                    host,
                                    "--template",
                                    "warm_healer",
                                    "--display-name",
                                    "小禾",
                                ]
                            )
                        self.assertEqual(code, 0)
                        self.assertIn(expected, output.getvalue())
                        self.assertNotIn("Codex 可用原生预览", output.getvalue())

    def test_openclaw_status_exposes_host_managed_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            output = io.StringIO()
            with (
                patch.dict(
                    os.environ,
                    {"COMPANION_HOME": str(root)},
                    clear=True,
                ),
                redirect_stdout(output),
            ):
                initialize_profile(
                    skill_root=SKILL_ROOT,
                    template_id="sunny_friend",
                    display_name="小晴",
                    host="openclaw",
                )
                code = main(["event-photo", "status", "--host", "openclaw"])

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["native_preview"]["proof"], "host_managed")
            self.assertEqual(
                payload["native_preview"]["model_request"],
                "openai/gpt-image-2",
            )
            self.assertFalse(payload["strict"]["auth_ready"])

    def test_event_prepare_without_key_creates_neither_job_nor_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(root)},
                clear=True,
            ):
                initialize_profile(
                    skill_root=SKILL_ROOT,
                    template_id="calm_partner",
                    display_name="阿序",
                    host="hermes",
                )
                error = io.StringIO()
                with redirect_stderr(error):
                    code = main(
                        [
                            "event-photo",
                            "prepare",
                            "--host",
                            "hermes",
                            "--purpose",
                            "prototype",
                            "--instance-scope",
                            "local-instance",
                            "--conversation-scope",
                            "current-conversation",
                            "--request-event-id",
                            "request-event-01",
                            "--text",
                            "照片：书店里自然站立",
                        ]
                    )

            self.assertEqual(code, 2)
            self.assertIn("OPENAI_API_KEY", error.getvalue())
            self.assertEqual(list(root.rglob("plan_*.json")), [])
            self.assertEqual(list(root.rglob("job_*.json")), [])

    def test_hermes_event_cli_completes_isolated_fake_current_reply_flow(self) -> None:
        class FakeImageClient:
            auth_ready = True

            def generate(self, prompt: str) -> ImageApiResult:
                return ImageApiResult(tiny_png(), "req_cli_prototype")

            def edit(self, prompt: str, reference_png: bytes) -> ImageApiResult:
                return ImageApiResult(tiny_png(), "req_cli_photo")

        def invoke(arguments: list[str]) -> dict[str, object]:
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(arguments), 0)
            return json.loads(output.getvalue())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment = {"COMPANION_HOME": str(root)}
            common = [
                "--host",
                "hermes",
                "--instance-scope",
                "instance-cli",
                "--conversation-scope",
                "conversation-cli",
            ]
            with (
                patch.dict(os.environ, environment, clear=True),
                patch("companion_kit.cli.OpenAIImageClient", FakeImageClient),
            ):
                invoke(
                    [
                        "init",
                        "--host",
                        "hermes",
                        "--template",
                        "warm_healer",
                        "--display-name",
                        "小禾",
                        "--json",
                    ]
                )
                prototype_text = "照片：自然光下的虚构成年人身份参考"
                prototype_context = [
                    *common,
                    "--request-event-id",
                    "event-cli-prototype",
                ]
                prepared = invoke(
                    [
                        "event-photo",
                        "prepare",
                        *prototype_context,
                        "--purpose",
                        "prototype",
                        "--text",
                        prototype_text,
                    ]
                )
                generated = invoke(
                    [
                        "event-photo",
                        "run",
                        *prototype_context,
                        "--purpose",
                        "prototype",
                        "--text",
                        prototype_text,
                        "--job-id",
                        prepared["job_id"],
                        "--plan-id",
                        prepared["plan_id"],
                        "--confirm-once",
                    ]
                )
                self.assertNotIn("artifact_path", generated)
                handoff = invoke(
                    [
                        "event-photo",
                        "handoff",
                        *prototype_context,
                        "--job-id",
                        generated["job_id"],
                        "--asset-id",
                        generated["asset_id"],
                    ]
                )
                self.assertEqual(handoff["stage"], "delivery_unknown")
                self.assertTrue(handoff["response_directive"].startswith("MEDIA:/"))
                self.assertNotIn("target", handoff)

                identity = invoke(
                    [
                        "event-photo",
                        "confirm-identity",
                        *prototype_context,
                        "--candidate-id",
                        generated["candidate_id"],
                        "--profile-version",
                        generated["profile_version"],
                    ]
                )
                self.assertEqual(identity["stage"], "identity_confirmed")

                photo_text = "照片：雨后沿街散步"
                photo_context = [
                    *common,
                    "--request-event-id",
                    "event-cli-photo",
                ]
                photo_plan = invoke(
                    [
                        "event-photo",
                        "prepare",
                        *photo_context,
                        "--purpose",
                        "photo",
                        "--text",
                        photo_text,
                    ]
                )
                photo = invoke(
                    [
                        "event-photo",
                        "run",
                        *photo_context,
                        "--purpose",
                        "photo",
                        "--text",
                        photo_text,
                        "--job-id",
                        photo_plan["job_id"],
                        "--plan-id",
                        photo_plan["plan_id"],
                        "--confirm-once",
                    ]
                )
                photo_handoff = invoke(
                    [
                        "event-photo",
                        "handoff",
                        *photo_context,
                        "--job-id",
                        photo["job_id"],
                        "--asset-id",
                        photo["asset_id"],
                    ]
                )
                artifact_path = Path(
                    photo_handoff["response_directive"].removeprefix("MEDIA:")
                )
                self.assertTrue(artifact_path.is_file())
                invoke(
                    [
                        "event-photo",
                        "delivered",
                        *photo_context,
                        "--job-id",
                        photo["job_id"],
                        "--asset-id",
                        photo["asset_id"],
                        "--confirm-receipt",
                    ]
                )
                self.assertFalse(artifact_path.exists())


if __name__ == "__main__":
    unittest.main()
