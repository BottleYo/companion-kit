import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from companion_kit.backup import BackupManager, CompanionDataLayout
from companion_kit.codex_upgrade import (
    CodexProgramSnapshotStore,
    CodexUpgradeExecutor,
)
from companion_kit.profile_store import ProfileStore
from companion_kit.photo_moment import PhotoMoment
from companion_kit.photo_moment_store import PhotoMomentStore
from companion_kit.upgrade import (
    CodexUpgradePlanner,
    InstallationReceiptStore,
    UpgradeError,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "virtual-companion"


class FakeCodex:
    def __init__(self, codex_home: Path, *, fail_new_health: bool = False) -> None:
        self.codex_home = codex_home
        self.fail_new_health = fail_new_health
        self.marketplace = "companion-kit-preview"
        self.cache_parent = (
            codex_home
            / "plugins"
            / "cache"
            / self.marketplace
            / "companion-kit"
        )
        self._write_cache("0.7.0-dev.4", marker="old-program")

    def _write_cache(self, version: str, *, marker: str) -> None:
        if self.cache_parent.exists():
            shutil.rmtree(self.cache_parent)
        root = self.cache_parent / version
        (root / ".codex-plugin").mkdir(parents=True)
        (root / ".codex-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "companion-kit",
                    "version": version,
                    "description": "test",
                }
            ),
            encoding="utf-8",
        )
        (root / "marker.txt").write_text(marker, encoding="utf-8")

    def _installed_version(self) -> str | None:
        if not self.cache_parent.is_dir():
            return None
        versions = sorted(
            path.name
            for path in self.cache_parent.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        )
        return versions[-1] if len(versions) == 1 else None

    def __call__(
        self,
        argv: list[str],
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        if argv[1:4] == ["plugin", "list", "--json"]:
            version = self._installed_version()
            installed = []
            if version is not None:
                installed.append(
                    {
                        "pluginId": f"companion-kit@{self.marketplace}",
                        "name": "companion-kit",
                        "marketplaceName": self.marketplace,
                        "version": version,
                        "installed": True,
                        "enabled": not (
                            self.fail_new_health and version == "0.7.0-dev.8"
                        ),
                        "source": {
                            "source": "local",
                            "path": str(PROJECT_ROOT),
                        },
                        "marketplaceSource": {
                            "sourceType": "local",
                            "source": str(PROJECT_ROOT),
                        },
                    }
                )
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=json.dumps({"installed": installed, "available": []}),
                stderr="",
            )
        if argv[1:3] == ["plugin", "add"]:
            self._write_cache("0.7.0-dev.8", marker="new-program")
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=json.dumps({"version": "0.7.0-dev.8"}),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {argv}")


class CodexUpgradeExecutorTests(unittest.TestCase):
    @staticmethod
    def _photo_history(layout: CompanionDataLayout, profile_id: str) -> Path:
        moments = PhotoMomentStore(layout.photo_moments_root)
        photo_moment = PhotoMoment.from_dict(
            {
                "mode": "new",
                "scene": "window",
                "activity": "getting_ready",
                "framing": "half",
                "hairstyle": "loose",
                "expression": "soft_smile",
                "time_band": "day",
                "intimacy_band": "everyday",
                "caption_act": "share_detail",
                "identity_version": 1,
            }
        )
        moments.stage(
            profile_id=profile_id,
            session_id="upgrade-test-session",
            tool_use_id="upgrade-test-image",
            photo_moment=photo_moment,
        )
        result = moments.commit(
            profile_id=profile_id,
            session_id="upgrade-test-session",
            tool_use_id="upgrade-test-image",
        )
        if not result.committed:
            raise AssertionError("failed to prepare photo history")
        return moments.history_path(profile_id)

    def _system(self, root: Path, *, fail_new_health: bool = False):
        data_root = root / "companion-home"
        codex_home = root / "codex-home"
        layout = CompanionDataLayout.for_codex(data_root=data_root)
        fake = FakeCodex(codex_home, fail_new_health=fail_new_health)
        planner = CodexUpgradePlanner(
            plugin_root=PROJECT_ROOT,
            layout=layout,
            which=lambda name: "/usr/bin/codex" if name == "codex" else None,
            runner=fake,
        )
        backups = BackupManager(layout, product_version="0.7.0-dev.8")
        executor = CodexUpgradeExecutor(
            planner=planner,
            backups=backups,
            codex_home=codex_home,
            runner=fake,
        )
        return layout, fake, planner, backups, executor

    def test_apply_keeps_durable_data_and_retains_two_verified_safety_nets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            layout, fake, _, backups, executor = self._system(root)
            store = ProfileStore(
                skill_root=SKILL_ROOT,
                profile_path=layout.profile_path,
            )
            snapshot = store.save(
                template_id="warm_healer",
                display_name="小禾",
                expected_version=None,
            )
            history = self._photo_history(layout, snapshot.profile.id)
            before = layout.profile_path.read_bytes()
            history_before = history.read_bytes()

            with patch.object(executor, "_validate_release", return_value=None):
                result = executor.apply(confirm=True)

            self.assertTrue(result.applied)
            self.assertFalse(result.rolled_back)
            self.assertEqual(result.from_version, "0.7.0-dev.4")
            self.assertEqual(result.to_version, "0.7.0-dev.8")
            self.assertEqual(layout.profile_path.read_bytes(), before)
            self.assertEqual(history.read_bytes(), history_before)
            self.assertTrue(backups.verify(result.backup_id).valid)
            program = CodexProgramSnapshotStore(layout).verify(
                result.program_snapshot_id
            )
            self.assertEqual(program.plugin_version, "0.7.0-dev.4")
            self.assertEqual(fake._installed_version(), "0.7.0-dev.8")
            receipt = InstallationReceiptStore(layout).read()
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt.plugin_version, "0.7.0-dev.8")

    def test_failed_new_health_check_restores_old_program_not_old_persona(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            layout, fake, _, backups, executor = self._system(
                root,
                fail_new_health=True,
            )
            store = ProfileStore(
                skill_root=SKILL_ROOT,
                profile_path=layout.profile_path,
            )
            snapshot = store.save(
                template_id="calm_partner",
                display_name="阿岚",
                expected_version=None,
            )
            history = self._photo_history(layout, snapshot.profile.id)
            before = layout.profile_path.read_bytes()
            history_before = history.read_bytes()

            with patch.object(executor, "_validate_release", return_value=None):
                with self.assertRaisesRegex(UpgradeError, "已恢复旧程序"):
                    executor.apply(confirm=True)

            self.assertEqual(layout.profile_path.read_bytes(), before)
            self.assertEqual(history.read_bytes(), history_before)
            self.assertEqual(fake._installed_version(), "0.7.0-dev.4")
            self.assertEqual(
                (
                    fake.cache_parent
                    / "0.7.0-dev.4"
                    / "marker.txt"
                ).read_text(encoding="utf-8"),
                "old-program",
            )
            snapshots = backups.list()
            self.assertEqual(len(snapshots), 1)
            self.assertTrue(backups.verify(snapshots[0].backup_id).valid)
            receipt = InstallationReceiptStore(layout).read()
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt.plugin_version, "0.7.0-dev.4")

    def test_old_relationship_migration_is_rehearsed_on_copy_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            layout, _, _, _, executor = self._system(root)
            database = layout.relationship_database
            database.parent.mkdir(parents=True)
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO meta VALUES ('schema_version', '1');
                CREATE TABLE relationship_state (
                    relationship_id TEXT PRIMARY KEY,
                    familiarity INTEGER NOT NULL,
                    trust INTEGER NOT NULL,
                    closeness INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE relationship_event (
                    relationship_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_host TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    applied INTEGER NOT NULL,
                    result_reason TEXT NOT NULL,
                    delta_familiarity INTEGER NOT NULL,
                    delta_trust INTEGER NOT NULL,
                    delta_closeness INTEGER NOT NULL,
                    PRIMARY KEY (relationship_id, event_id)
                );
                CREATE INDEX relationship_event_daily_idx
                    ON relationship_event(relationship_id, occurred_at);
                CREATE INDEX relationship_event_type_idx
                    ON relationship_event(relationship_id, event_type, occurred_at);
                CREATE TABLE relationship_atmosphere (
                    relationship_id TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    value TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    PRIMARY KEY (relationship_id, scope_id)
                );
                """
            )
            connection.commit()
            connection.close()
            before = database.read_bytes()

            with patch.object(executor, "_validate_release", return_value=None):
                result = executor.apply(confirm=True)

            self.assertTrue(result.applied)
            self.assertEqual(database.read_bytes(), before)
            connection = sqlite3.connect(database)
            version = connection.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            connection.close()
            self.assertEqual(version, "1")

    def test_apply_requires_explicit_confirmation_before_creating_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            layout, _, _, _, executor = self._system(root)

            with self.assertRaisesRegex(UpgradeError, "单独确认"):
                executor.apply(confirm=False)

            self.assertFalse(layout.root.exists())

    def test_receipt_failure_does_not_turn_a_healthy_upgrade_into_data_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            _, fake, _, _, executor = self._system(root)

            with (
                patch.object(executor, "_validate_release", return_value=None),
                patch.object(executor, "_try_save_receipt", return_value=False),
            ):
                result = executor.apply(confirm=True)

            self.assertTrue(result.applied)
            self.assertFalse(result.upgrade_registered)
            self.assertEqual(fake._installed_version(), "0.7.0-dev.8")

    def test_program_snapshot_tampering_is_rejected_before_manual_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            layout, _, _, _, executor = self._system(root)
            source = executor._installed_cache_path(
                marketplace_name="companion-kit-preview",
                installed_version="0.7.0-dev.4",
            )
            snapshot = executor.program_snapshots.create(
                snapshot_id="20260806T080000Z-deadbeef",
                source=source,
                plugin_version="0.7.0-dev.4",
                marketplace_name="companion-kit-preview",
            )
            (snapshot.path / "plugin" / "marker.txt").write_text(
                "tampered",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(UpgradeError, "校验失败"):
                executor.rollback(
                    snapshot_id=snapshot.snapshot_id,
                    confirm=True,
                )


if __name__ == "__main__":
    unittest.main()
