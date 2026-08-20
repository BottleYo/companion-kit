import json
from datetime import UTC, datetime
from pathlib import Path
import subprocess
import tempfile
import unittest

from companion_kit.backup import CompanionDataLayout
from companion_kit.upgrade import (
    CodexInstallationReceipt,
    CodexUpgradePlanner,
    InstallationReceiptStore,
    UpgradeError,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CodexUpgradePlannerTests(unittest.TestCase):
    def test_installation_receipt_records_version_without_private_source_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "companion-home"
            layout = CompanionDataLayout.for_codex(data_root=root)
            store = InstallationReceiptStore(layout)
            receipt = CodexInstallationReceipt(
                plugin_version="0.7.0-dev.4",
                marketplace_name="companion-kit-preview",
                marketplace_source_type="local",
                plugin_enabled=True,
                recorded_at="2026-08-06T08:00:00+00:00",
            )

            saved = store.save(receipt)
            loaded = store.read()

            self.assertEqual(saved, receipt)
            self.assertEqual(loaded, receipt)
            encoded = (layout.system_root / "installation.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("source_value", encoded)
            self.assertNotIn(str(PROJECT_ROOT), encoded)

    def test_installation_receipt_refuses_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "companion-home"
            layout = CompanionDataLayout.for_codex(data_root=root)
            layout.system_root.mkdir(parents=True)
            outside = root.parent / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            try:
                (layout.system_root / "installation.json").symlink_to(outside)
            except (NotImplementedError, OSError):
                self.skipTest("当前平台不支持符号链接")

            with self.assertRaisesRegex(UpgradeError, "符号链接"):
                InstallationReceiptStore(layout).save(
                    CodexInstallationReceipt(
                        plugin_version="0.7.0-dev.4",
                        marketplace_name="companion-kit-preview",
                        marketplace_source_type="local",
                        plugin_enabled=True,
                        recorded_at="2026-08-06T08:00:00+00:00",
                    )
                )

    def test_check_detects_existing_local_plugin_without_writing_data(self) -> None:
        payload = {
            "installed": [
                {
                    "pluginId": "companion-kit@companion-kit-preview",
                    "name": "companion-kit",
                    "marketplaceName": "companion-kit-preview",
                    "version": "0.7.0-dev.3",
                    "installed": True,
                    "enabled": True,
                    "source": "local",
                    "marketplaceSource": {
                        "sourceType": "local",
                        "value": str(PROJECT_ROOT),
                    },
                }
            ],
            "available": [],
        }

        def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=json.dumps(payload),
                stderr="",
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "companion-home"
            planner = CodexUpgradePlanner(
                plugin_root=PROJECT_ROOT,
                layout=CompanionDataLayout.for_codex(data_root=root),
                which=lambda name: "/usr/bin/codex" if name == "codex" else None,
                runner=runner,
            )

            result = planner.check()

            self.assertTrue(result.plugin_installed)
            self.assertTrue(result.plugin_enabled)
            self.assertEqual(result.installed_version, "0.7.0-dev.3")
            self.assertEqual(result.release_version, "0.9.0-dev.2")
            self.assertEqual(result.marketplace_source_type, "local")
            self.assertTrue(result.marketplace_matches_release_source)
            self.assertTrue(result.update_candidate)
            self.assertTrue(result.ready)
            self.assertFalse(root.exists())
            self.assertNotIn(str(PROJECT_ROOT), json.dumps(result.to_dict()))

    def test_adopt_records_existing_install_without_touching_durable_data(self) -> None:
        payload = {
            "installed": [
                {
                    "pluginId": "companion-kit@companion-kit-preview",
                    "name": "companion-kit",
                    "marketplaceName": "companion-kit-preview",
                    "version": "0.7.0-dev.4",
                    "installed": True,
                    "enabled": True,
                    "source": "local",
                    "marketplaceSource": {
                        "sourceType": "local",
                        "value": str(PROJECT_ROOT),
                    },
                }
            ],
            "available": [],
        }

        def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "companion-home"
            profile = root / "profiles" / "default.toml"
            profile.parent.mkdir(parents=True)
            profile.write_text("keep-this-byte-for-byte", encoding="utf-8")
            before = profile.read_bytes()
            layout = CompanionDataLayout.for_codex(data_root=root)
            planner = CodexUpgradePlanner(
                plugin_root=PROJECT_ROOT,
                layout=layout,
                which=lambda _: "/usr/bin/codex",
                runner=runner,
                clock=lambda: datetime(2026, 8, 6, 8, 0, tzinfo=UTC),
            )

            receipt = planner.adopt()

            self.assertEqual(profile.read_bytes(), before)
            self.assertEqual(receipt.plugin_version, "0.7.0-dev.4")
            encoded = (layout.system_root / "installation.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn(str(PROJECT_ROOT), encoded)
            self.assertTrue(planner.check().installation_receipt_present)

    def test_plan_requires_backup_before_any_program_replacement(self) -> None:
        payload = {
            "installed": [
                {
                    "pluginId": "companion-kit@companion-kit-preview",
                    "name": "companion-kit",
                    "marketplaceName": "companion-kit-preview",
                    "version": "0.7.0-dev.4",
                    "installed": True,
                    "enabled": True,
                    "source": "local",
                    "marketplaceSource": {
                        "sourceType": "local",
                        "value": str(PROJECT_ROOT),
                    },
                }
            ],
            "available": [],
        }

        def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "companion-home"
            (root / "profiles").mkdir(parents=True)
            (root / "profiles" / "default.toml").write_text(
                "schema_version = 999\n",
                encoding="utf-8",
            )
            planner = CodexUpgradePlanner(
                plugin_root=PROJECT_ROOT,
                layout=CompanionDataLayout.for_codex(data_root=root),
                which=lambda _: "/usr/bin/codex",
                runner=runner,
            )

            plan = planner.plan()

            self.assertFalse(plan.ready)
            self.assertFalse(plan.apply_available)
            self.assertEqual(plan.steps[0]["id"], "preflight")
            self.assertEqual(plan.steps[1]["id"], "verified_backup")
            self.assertEqual(plan.steps[-1]["id"], "health_check")
            self.assertTrue(plan.blockers)

    def test_plan_allows_verified_local_upgrade_without_mutating_data(self) -> None:
        payload = {
            "installed": [
                {
                    "name": "companion-kit",
                    "marketplaceName": "companion-kit-preview",
                    "version": "0.7.0-dev.4",
                    "enabled": True,
                    "marketplaceSource": {
                        "sourceType": "local",
                        "source": str(PROJECT_ROOT),
                    },
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "companion-home"
            planner = CodexUpgradePlanner(
                plugin_root=PROJECT_ROOT,
                layout=CompanionDataLayout.for_codex(data_root=root),
                which=lambda _: "/usr/bin/codex",
                runner=lambda argv, **_: subprocess.CompletedProcess(
                    argv,
                    0,
                    json.dumps(payload),
                    "",
                ),
            )

            plan = planner.plan()

            self.assertTrue(plan.ready)
            self.assertTrue(plan.apply_available)
            self.assertTrue(plan.check.update_candidate)
            self.assertFalse(root.exists())

    def test_cachebuster_does_not_look_like_a_product_update(self) -> None:
        payload = {
            "installed": [
                {
                    "name": "companion-kit",
                    "marketplaceName": "companion-kit-preview",
                    "version": "0.9.0-dev.2+codex.local-20260806-080000",
                    "enabled": True,
                    "marketplaceSource": {
                        "sourceType": "local",
                        "source": str(PROJECT_ROOT),
                    },
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            planner = CodexUpgradePlanner(
                plugin_root=PROJECT_ROOT,
                layout=CompanionDataLayout.for_codex(
                    data_root=Path(tmp).resolve() / "companion-home"
                ),
                which=lambda _: "/usr/bin/codex",
                runner=lambda argv, **_: subprocess.CompletedProcess(
                    argv,
                    0,
                    json.dumps(payload),
                    "",
                ),
            )

            check = planner.check()

            self.assertTrue(check.ready)
            self.assertFalse(check.update_candidate)

    def test_mismatched_marketplace_source_and_downgrade_fail_closed(self) -> None:
        payload = {
            "installed": [
                {
                    "name": "companion-kit",
                    "marketplaceName": "companion-kit-preview",
                    "version": "0.9.0",
                    "enabled": True,
                    "marketplaceSource": {
                        "sourceType": "local",
                        "source": "/different/project",
                    },
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            planner = CodexUpgradePlanner(
                plugin_root=PROJECT_ROOT,
                layout=CompanionDataLayout.for_codex(
                    data_root=Path(tmp).resolve() / "companion-home"
                ),
                which=lambda _: "/usr/bin/codex",
                runner=lambda argv, **_: subprocess.CompletedProcess(
                    argv,
                    0,
                    json.dumps(payload),
                    "",
                ),
            )

            check = planner.check()

            self.assertFalse(check.ready)
            self.assertFalse(check.marketplace_matches_release_source)
            self.assertIn("不能自动降级", " ".join(check.blockers))
            self.assertNotIn("/different/project", json.dumps(check.to_dict()))

    def test_codex_cli_failure_is_reported_as_a_blocker(self) -> None:
        def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 2, "", "failed")

        with tempfile.TemporaryDirectory() as tmp:
            planner = CodexUpgradePlanner(
                plugin_root=PROJECT_ROOT,
                layout=CompanionDataLayout.for_codex(
                    data_root=Path(tmp).resolve() / "companion-home"
                ),
                which=lambda _: "/usr/bin/codex",
                runner=runner,
            )

            result = planner.check()

            self.assertFalse(result.plugin_installed)
            self.assertTrue(result.blockers)
            self.assertNotIn("failed", json.dumps(result.to_dict()))


if __name__ == "__main__":
    unittest.main()
