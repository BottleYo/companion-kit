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
from companion_kit.codex_image_receipts import CodexImageReceiptStore
from companion_kit.initializer import initialize_profile
from companion_kit.identity_pack import PROFILE_FACE
from companion_kit.image_assets import ImageAssetStore
from companion_kit.openai_image_api import ImageApiResult
from companion_kit.profile_store import ProfileStore, ProfileStoreError
from tests.png_fixture import tiny_png


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "virtual-companion"


class CliTests(unittest.TestCase):
    def test_codex_init_does_not_claim_runtime_ready_before_hooks_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with (
                patch.dict(
                    os.environ,
                    {"COMPANION_HOME": str(Path(tmp).resolve() / "home")},
                    clear=True,
                ),
                redirect_stdout(output),
            ):
                code = main(
                    [
                        "init",
                        "--host",
                        "codex",
                        "--template",
                        "warm_healer",
                        "--display-name",
                        "小禾",
                    ]
                )

            message = output.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("Persona 已保存", message)
            self.assertIn("/hooks", message)
            self.assertIn("SessionStart", message)
            self.assertIn("以面板的运行验证结果为准", message)
            self.assertNotIn("配置完成", message)
            self.assertNotIn("开一个新任务直接聊天", message)

    def test_backup_cli_creates_lists_verifies_and_recovers_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "companion-home"
            recovery = root.parent / "recovery-copy"
            with patch.dict(os.environ, {"COMPANION_HOME": str(root)}, clear=True):
                initialize_profile(
                    skill_root=SKILL_ROOT,
                    template_id="warm_healer",
                    display_name="小禾",
                )
                created_output = io.StringIO()
                with redirect_stdout(created_output):
                    created_code = main(["backup", "create"])
                created = json.loads(created_output.getvalue())

                listed_output = io.StringIO()
                with redirect_stdout(listed_output):
                    listed_code = main(["backup", "list"])
                listed = json.loads(listed_output.getvalue())

                verified_output = io.StringIO()
                with redirect_stdout(verified_output):
                    verified_code = main(
                        ["backup", "verify", "--backup-id", created["backup_id"]]
                    )
                verified = json.loads(verified_output.getvalue())

                recovered_output = io.StringIO()
                with redirect_stdout(recovered_output):
                    recovered_code = main(
                        [
                            "backup",
                            "recover-copy",
                            "--backup-id",
                            created["backup_id"],
                            "--destination",
                            str(recovery),
                        ]
                    )
                recovered = json.loads(recovered_output.getvalue())

            self.assertEqual(created_code, 0)
            self.assertTrue(created["verified"])
            self.assertEqual(listed_code, 0)
            self.assertEqual(listed["backups"][0]["backup_id"], created["backup_id"])
            self.assertEqual(verified_code, 0)
            self.assertTrue(verified["valid"])
            self.assertEqual(recovered_code, 0)
            self.assertTrue(recovered["created"])
            self.assertTrue((recovery / "profiles" / "default.toml").is_file())

    def test_upgrade_cli_exposes_read_only_check_and_plan(self) -> None:
        class FakeCheck:
            def to_dict(self) -> dict[str, object]:
                return {"ready": True, "mode": "check"}

        class FakePlan:
            def to_dict(self) -> dict[str, object]:
                return {
                    "ready": True,
                    "mode": "plan",
                    "apply_available": False,
                }

        class FakePlanner:
            def __init__(self, **_: object) -> None:
                pass

            def check(self) -> FakeCheck:
                return FakeCheck()

            def plan(self) -> FakePlan:
                return FakePlan()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "companion-home"
            with (
                patch.dict(os.environ, {"COMPANION_HOME": str(root)}, clear=True),
                patch("companion_kit.cli.CodexUpgradePlanner", FakePlanner),
            ):
                check_output = io.StringIO()
                with redirect_stdout(check_output):
                    check_code = main(["upgrade", "check"])
                plan_output = io.StringIO()
                with redirect_stdout(plan_output):
                    plan_code = main(["upgrade", "plan"])

            self.assertEqual(check_code, 0)
            self.assertEqual(json.loads(check_output.getvalue())["mode"], "check")
            self.assertEqual(plan_code, 0)
            self.assertFalse(json.loads(plan_output.getvalue())["apply_available"])

    def test_upgrade_cli_forwards_explicit_apply_and_rollback_confirmation(self) -> None:
        calls: list[tuple[str, object]] = []

        class FakePlanner:
            def __init__(self, **_: object) -> None:
                pass

        class FakeResult:
            def __init__(self, mode: str) -> None:
                self.mode = mode

            def to_dict(self) -> dict[str, object]:
                return {"mode": self.mode, "durable_data_replaced": False}

        class FakeExecutor:
            def __init__(self, **_: object) -> None:
                pass

            def apply(self, *, confirm: bool) -> FakeResult:
                calls.append(("apply", confirm))
                return FakeResult("apply")

            def rollback(
                self,
                *,
                snapshot_id: str,
                confirm: bool,
            ) -> FakeResult:
                calls.append(("rollback", (snapshot_id, confirm)))
                return FakeResult("rollback")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "companion-home"
            with (
                patch.dict(os.environ, {"COMPANION_HOME": str(root)}, clear=True),
                patch("companion_kit.cli.CodexUpgradePlanner", FakePlanner),
                patch("companion_kit.cli.CodexUpgradeExecutor", FakeExecutor),
            ):
                apply_output = io.StringIO()
                with redirect_stdout(apply_output):
                    apply_code = main(["upgrade", "apply", "--confirm"])
                rollback_output = io.StringIO()
                with redirect_stdout(rollback_output):
                    rollback_code = main(
                        [
                            "upgrade",
                            "rollback",
                            "--snapshot-id",
                            "20260806T080000Z-deadbeef",
                            "--confirm",
                        ]
                    )

            self.assertEqual(apply_code, 0)
            self.assertEqual(json.loads(apply_output.getvalue())["mode"], "apply")
            self.assertEqual(rollback_code, 0)
            self.assertEqual(
                json.loads(rollback_output.getvalue())["mode"],
                "rollback",
            )
            self.assertEqual(
                calls,
                [
                    ("apply", True),
                    ("rollback", ("20260806T080000Z-deadbeef", True)),
                ],
            )

    def test_codex_native_identity_can_be_staged_and_confirmed_without_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            codex_home = root / "codex-home"
            candidate_path = codex_home / "generated_images" / "candidate.png"
            candidate_path.parent.mkdir(parents=True)
            candidate_path.write_bytes(tiny_png())
            with patch.dict(
                os.environ,
                {
                    "COMPANION_HOME": str(root / "home"),
                    "CODEX_HOME": str(codex_home),
                },
                clear=True,
            ):
                CodexImageReceiptStore().record(
                    session_id="codex-task-one",
                    tool_use_id="image-tool-one",
                    paths=(candidate_path,),
                )
                self.assertEqual(
                    main(
                        [
                            "init",
                            "--host",
                            "codex",
                            "--template",
                            "warm_healer",
                            "--display-name",
                            "小禾",
                            "--json",
                        ]
                    ),
                    0,
                )
                staged_output = io.StringIO()
                with redirect_stdout(staged_output):
                    staged_code = main(
                        [
                            "identity",
                            "stage-native",
                            "--file",
                            str(candidate_path),
                            "--task-scope",
                            "codex-task-one",
                        ]
                    )
                staged = json.loads(staged_output.getvalue())

                confirmed_output = io.StringIO()
                with redirect_stdout(confirmed_output):
                    confirmed_code = main(
                        [
                            "identity",
                            "confirm",
                            "--candidate-id",
                            staged["candidate_id"],
                            "--task-scope",
                            "codex-task-one",
                            "--profile-version",
                            staged["profile_version"],
                        ]
                    )
                confirmed = json.loads(confirmed_output.getvalue())

                self.assertEqual(staged_code, 0)
                self.assertEqual(confirmed_code, 0)
                self.assertEqual(confirmed["stage"], "identity_confirmed")
                self.assertFalse(confirmed["api_key_required"])

                profile_path = codex_home / "generated_images" / "profile.png"
                profile_path.write_bytes(tiny_png(rgba=b"\x60\x40\x20\xff"))
                CodexImageReceiptStore().record(
                    session_id="codex-task-two",
                    tool_use_id="image-tool-two",
                    paths=(profile_path,),
                )
                profile_stage_output = io.StringIO()
                with redirect_stdout(profile_stage_output):
                    profile_stage_code = main(
                        [
                            "identity",
                            "stage-native",
                            "--file",
                            str(profile_path),
                            "--task-scope",
                            "codex-task-two",
                            "--role",
                            PROFILE_FACE,
                        ]
                    )
                profile_stage = json.loads(profile_stage_output.getvalue())
                profile_confirm_output = io.StringIO()
                with redirect_stdout(profile_confirm_output):
                    profile_confirm_code = main(
                        [
                            "identity",
                            "confirm",
                            "--candidate-id",
                            profile_stage["candidate_id"],
                            "--task-scope",
                            "codex-task-two",
                            "--profile-version",
                            profile_stage["profile_version"],
                        ]
                    )
                profile_confirm = json.loads(profile_confirm_output.getvalue())

                self.assertEqual(profile_stage_code, 0)
                self.assertEqual(profile_confirm_code, 0)
                self.assertEqual(profile_stage["role"], PROFILE_FACE)
                self.assertEqual(profile_confirm["role"], PROFILE_FACE)

                status_output = io.StringIO()
                with redirect_stdout(status_output):
                    status_code = main(["photo", "status"])
                status = json.loads(status_output.getvalue())
                self.assertEqual(status_code, 0)
                self.assertTrue(status["reference_configured"])
                self.assertTrue(status["reference_ready"])
                self.assertEqual(status["cross_task_reference_bridge"], "ready")
                self.assertEqual(
                    status["identity_pack"]["roles"],
                    ["primary_face", "profile_face"],
                )
                self.assertEqual(status["identity_pack"]["level"], "enhanced")
                snapshot = ProfileStore(skill_root=SKILL_ROOT).read()
                self.assertEqual(
                    snapshot.profile.visual.reference_ids,
                    (confirmed["reference_id"],),
                )
                self.assertEqual(
                    list((root / "home" / "private" / "images").rglob("pending.png")),
                    [],
                )

    def test_codex_identity_enhancement_requires_confirmed_primary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            codex_home = root / "codex-home"
            source = codex_home / "generated_images" / "profile.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(tiny_png())
            error = io.StringIO()
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(root / "home"), "CODEX_HOME": str(codex_home)},
                clear=True,
            ):
                initialize_profile(
                    skill_root=SKILL_ROOT,
                    template_id="warm_healer",
                    display_name="小禾",
                )
                CodexImageReceiptStore().record(
                    session_id="codex-task",
                    tool_use_id="image-tool",
                    paths=(source,),
                )
                with redirect_stderr(error):
                    code = main(
                        [
                            "identity",
                            "stage-native",
                            "--file",
                            str(source),
                            "--task-scope",
                            "codex-task",
                            "--role",
                            PROFILE_FACE,
                        ]
                    )

            self.assertEqual(code, 2)
            self.assertIn("主脸", error.getvalue())

    def test_failed_primary_binding_restores_same_confirmable_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            codex_home = root / "codex-home"
            candidate_path = codex_home / "generated_images" / "candidate.png"
            candidate_path.parent.mkdir(parents=True)
            candidate_path.write_bytes(tiny_png())
            with patch.dict(
                os.environ,
                {
                    "COMPANION_HOME": str(root / "home"),
                    "CODEX_HOME": str(codex_home),
                },
                clear=True,
            ):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(
                        main(
                            [
                                "init",
                                "--host",
                                "codex",
                                "--template",
                                "warm_healer",
                                "--display-name",
                                "小禾",
                                "--json",
                            ]
                        ),
                        0,
                    )
                CodexImageReceiptStore().record(
                    session_id="codex-recovery-task",
                    tool_use_id="image-tool",
                    paths=(candidate_path,),
                )
                staged_output = io.StringIO()
                with redirect_stdout(staged_output):
                    self.assertEqual(
                        main(
                            [
                                "identity",
                                "stage-native",
                                "--file",
                                str(candidate_path),
                                "--task-scope",
                                "codex-recovery-task",
                            ]
                        ),
                        0,
                    )
                staged = json.loads(staged_output.getvalue())
                confirm_args = [
                    "identity",
                    "confirm",
                    "--candidate-id",
                    staged["candidate_id"],
                    "--task-scope",
                    "codex-recovery-task",
                    "--profile-version",
                    staged["profile_version"],
                ]

                def change_profile_then_fail(
                    store: ProfileStore,
                    **_: object,
                ) -> None:
                    current = store.read()
                    store.save_draft(
                        expected_version=current.version,
                        display_name="小禾（刚调整）",
                    )
                    raise ProfileStoreError("模拟 Persona 版本冲突")

                first_error = io.StringIO()
                with (
                    patch.object(
                        ProfileStore,
                        "bind_reference",
                        new=change_profile_then_fail,
                    ),
                    redirect_stderr(first_error),
                ):
                    first_code = main(confirm_args)

                self.assertEqual(first_code, 2)
                first_payload = json.loads(first_error.getvalue())
                self.assertIn("版本冲突", first_payload["error"])
                self.assertNotEqual(
                    first_payload["retry_profile_version"],
                    staged["profile_version"],
                )
                image_root = root / "home" / "private" / "images"
                self.assertEqual(len(list(image_root.rglob("pending.png"))), 1)
                self.assertEqual(list(image_root.rglob("selected.png")), [])
                self.assertEqual(list(image_root.rglob("pack.json")), [])

                retry_args = [*confirm_args]
                retry_args[-1] = first_payload["retry_profile_version"]
                recovered_output = io.StringIO()
                with redirect_stdout(recovered_output):
                    recovered_code = main(retry_args)
                recovered = json.loads(recovered_output.getvalue())

                self.assertEqual(recovered_code, 0)
                self.assertEqual(recovered["role"], "primary_face")
                snapshot = ProfileStore(skill_root=SKILL_ROOT).read()
                self.assertTrue(snapshot.profile.visual.is_locked)

    def test_unknown_post_commit_result_never_rolls_back_bound_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            codex_home = root / "codex-home"
            candidate_path = codex_home / "generated_images" / "candidate.png"
            candidate_path.parent.mkdir(parents=True)
            candidate_path.write_bytes(tiny_png())
            with patch.dict(
                os.environ,
                {
                    "COMPANION_HOME": str(root / "home"),
                    "CODEX_HOME": str(codex_home),
                },
                clear=True,
            ):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(
                        main(
                            [
                                "init",
                                "--host",
                                "codex",
                                "--template",
                                "warm_healer",
                                "--display-name",
                                "小禾",
                                "--json",
                            ]
                        ),
                        0,
                    )
                CodexImageReceiptStore().record(
                    session_id="codex-unknown-task",
                    tool_use_id="image-tool",
                    paths=(candidate_path,),
                )
                staged_output = io.StringIO()
                with redirect_stdout(staged_output):
                    self.assertEqual(
                        main(
                            [
                                "identity",
                                "stage-native",
                                "--file",
                                str(candidate_path),
                                "--task-scope",
                                "codex-unknown-task",
                            ]
                        ),
                        0,
                    )
                staged = json.loads(staged_output.getvalue())
                original_bind = ProfileStore.bind_reference
                original_read = ProfileStore.read
                state = {"committed": False}

                def read_with_unknown_result(store: ProfileStore):
                    if state["committed"]:
                        raise ProfileStoreError("模拟提交后无法复读 Persona")
                    return original_read(store)

                def bind_then_lose_result(store: ProfileStore, **kwargs: object):
                    original_bind(store, **kwargs)
                    state["committed"] = True
                    raise ProfileStoreError("模拟 Persona 已提交但结果未知")

                error = io.StringIO()
                with (
                    patch.object(
                        ProfileStore,
                        "read",
                        new=read_with_unknown_result,
                    ),
                    patch.object(
                        ProfileStore,
                        "bind_reference",
                        new=bind_then_lose_result,
                    ),
                    redirect_stderr(error),
                ):
                    code = main(
                        [
                            "identity",
                            "confirm",
                            "--candidate-id",
                            staged["candidate_id"],
                            "--task-scope",
                            "codex-unknown-task",
                            "--profile-version",
                            staged["profile_version"],
                        ]
                    )

                self.assertEqual(code, 2)
                self.assertIn("结果未知", error.getvalue())
                snapshot = ProfileStore(skill_root=SKILL_ROOT).read()
                self.assertTrue(snapshot.profile.visual.is_locked)
                image_root = root / "home" / "private" / "images"
                self.assertEqual(len(list(image_root.rglob("selected.png"))), 1)
                self.assertEqual(len(list(image_root.rglob("pack.json"))), 1)
                self.assertEqual(len(list(image_root.rglob("pending.png"))), 1)

    def test_local_port_accepts_auto_and_rejects_out_of_range_values(self) -> None:
        self.assertEqual(_local_port("0"), 0)
        self.assertEqual(_local_port("65535"), 65535)

        with self.assertRaises(argparse.ArgumentTypeError):
            _local_port("65536")
        with self.assertRaises(argparse.ArgumentTypeError):
            _local_port("-1")
        with self.assertRaises(argparse.ArgumentTypeError):
            _local_port("not-a-port")

    def test_photo_status_uses_only_codex_builtin_images_without_key(self) -> None:
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
            self.assertEqual(set(payload["modes"]), {"codex_native"})
            self.assertEqual(
                payload["modes"]["codex_native"]["moderation_control"],
                "host_managed",
            )
            self.assertFalse(payload["api_key_required"])
            self.assertFalse(payload["provider_choice_required"])
            self.assertEqual(
                payload["cross_task_reference_bridge"],
                "waiting_for_identity_confirmation",
            )
            self.assertNotIn("OPENAI_API_KEY", output.getvalue())
            self.assertFalse((root / "private").exists())

    def test_legacy_codex_api_prepare_is_disabled(self) -> None:
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
            self.assertIn("Codex 已停用独立 API 生图入口", error.getvalue())
            self.assertNotIn("OPENAI_API_KEY", error.getvalue())
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
            self.assertEqual(
                payload["cross_task_reference_bridge"],
                "reference_unavailable",
            )

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
            self.assertEqual(status_payload["strict"]["moderation"], "low")
            self.assertNotIn("native_preview", status_payload)
            self.assertFalse((root / "hosts" / "hermes" / "private").exists())

    def test_event_host_status_keeps_legacy_reference_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with patch.dict(os.environ, {"COMPANION_HOME": str(root)}, clear=True):
                initialize_profile(
                    skill_root=SKILL_ROOT,
                    template_id="warm_healer",
                    display_name="小禾",
                    host="hermes",
                )
                profile_path = (
                    root / "hosts" / "hermes" / "profiles" / "default.toml"
                )
                profile_store = ProfileStore(
                    skill_root=SKILL_ROOT,
                    profile_path=profile_path,
                )
                snapshot = profile_store.read()
                assets = ImageAssetStore(
                    root / "hosts" / "hermes" / "private" / "images"
                )
                candidate = assets.store_candidate(
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    image_bytes=tiny_png(),
                    task_scope="hermes-task",
                )
                reference = assets.confirm_candidate(
                    candidate_id=candidate.candidate_id,
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    task_scope="hermes-task",
                )
                profile_store.bind_reference(
                    reference_id=reference.reference_id,
                    identity_version=1,
                    expected_version=snapshot.version,
                )
                next(assets.root.rglob("pack.json")).unlink()

                output = io.StringIO()
                with redirect_stdout(output):
                    code = main(["event-photo", "status", "--host", "hermes"])

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertTrue(payload["reference_configured"])
            self.assertTrue(payload["reference_ready"])

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
            self.assertEqual(payload["native_preview"]["moderation_request"], "low")
            self.assertEqual(payload["strict"]["moderation"], "low")
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
