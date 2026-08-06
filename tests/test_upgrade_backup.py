from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from companion_kit.backup import (
    BackupError,
    BackupManager,
    CompanionDataLayout,
)
from companion_kit.image_assets import ImageAssetStore
from companion_kit.initializer import initialize_profile
from companion_kit.profile_store import ProfileStore
from companion_kit.relationship import RelationshipEvent, RelationshipEventType
from companion_kit.state_store import RelationshipStore
from tests.png_fixture import tiny_png


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "virtual-companion"
NOW = datetime(2026, 8, 6, 8, 0, tzinfo=UTC)


def _durable_hashes(root: Path) -> dict[str, str]:
    selected = (
        root / "profiles",
        root / "private" / "images",
        root / "private" / "relationships.sqlite3",
    )
    result: dict[str, str] = {}
    for candidate in selected:
        if candidate.is_file():
            result[str(candidate.relative_to(root))] = sha256(
                candidate.read_bytes()
            ).hexdigest()
        elif candidate.is_dir():
            for path in sorted(candidate.rglob("*")):
                if path.is_file() and not path.name.endswith(".lock"):
                    result[str(path.relative_to(root))] = sha256(
                        path.read_bytes()
                    ).hexdigest()
    return result


def _create_durable_data(root: Path) -> tuple[str, str]:
    profile_path = root / "profiles" / "default.toml"
    initialize_profile(
        skill_root=SKILL_ROOT,
        template_id="warm_healer",
        display_name="小禾",
        output=profile_path,
    )
    profile_store = ProfileStore(
        skill_root=SKILL_ROOT,
        profile_path=profile_path,
    )
    snapshot = profile_store.read()
    assert snapshot is not None

    assets = ImageAssetStore(root / "private" / "images")
    candidate = assets.store_candidate(
        profile_id=snapshot.profile.id,
        identity_version=snapshot.profile.visual.identity_version,
        image_bytes=tiny_png(),
        task_scope="backup-test-task",
        source="user_upload",
    )
    reference = assets.confirm_candidate(
        candidate_id=candidate.candidate_id,
        profile_id=snapshot.profile.id,
        identity_version=snapshot.profile.visual.identity_version,
        task_scope="backup-test-task",
    )
    profile_store.bind_reference(
        reference_id=reference.reference_id,
        identity_version=snapshot.profile.visual.identity_version,
        expected_version=snapshot.version,
    )

    database = root / "private" / "relationships.sqlite3"
    event = RelationshipEvent(
        event_id="upgrade-backup-event",
        event_type=RelationshipEventType.EXPLICIT_APPRECIATION,
        occurred_at=NOW,
        confidence=1.0,
        source_host="codex",
        scope_id="b" * 32,
        reason_code="explicit_user_signal",
    )
    RelationshipStore(database).apply_event("warm-healer", event)
    return reference.reference_id, snapshot.profile.id


class BackupManagerTests(unittest.TestCase):
    def test_flat_custom_profile_is_included_without_scanning_sibling_private_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "custom-owner"
            profile = root / "persona.toml"
            initialize_profile(
                skill_root=SKILL_ROOT,
                template_id="calm_partner",
                display_name="阿序",
                output=profile,
            )
            transient = root / "private" / "codex-image-receipts" / "receipt.json"
            transient.parent.mkdir(parents=True)
            transient.write_text("temporary", encoding="utf-8")
            manager = BackupManager(
                CompanionDataLayout.for_profile(profile),
                product_version="0.7.0-dev.5",
                clock=lambda: NOW,
            )

            snapshot = manager.create()
            paths = {
                item["relative_path"] for item in snapshot.manifest["items"]
            }

            self.assertIn("persona.toml", paths)
            self.assertNotIn("private/codex-image-receipts/receipt.json", paths)

    def test_backup_preserves_persona_relationship_and_identity_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "companion-home"
            reference_id, profile_id = _create_durable_data(root)
            before = _durable_hashes(root)
            manager = BackupManager(
                CompanionDataLayout.for_codex(data_root=root),
                product_version="0.7.0-dev.4",
                clock=lambda: NOW,
            )

            snapshot = manager.create()
            verification = manager.verify(snapshot.backup_id)
            after = _durable_hashes(root)

            self.assertTrue(verification.valid)
            self.assertEqual(before, after)
            self.assertEqual(snapshot.manifest["profile_schema"], 3)
            self.assertEqual(snapshot.manifest["relationship_schema"], 2)
            relative_paths = {
                item["relative_path"] for item in snapshot.manifest["items"]
            }
            self.assertIn("profiles/default.toml", relative_paths)
            self.assertIn("private/relationships.sqlite3", relative_paths)
            self.assertTrue(
                any(path.startswith("private/images/") for path in relative_paths)
            )
            self.assertFalse(
                any(path.endswith(".lock") for path in relative_paths)
            )

            recovery = root.parent / "recovery-copy"
            restored = manager.recover_copy(snapshot.backup_id, recovery)
            restored_profile = ProfileStore(
                skill_root=SKILL_ROOT,
                profile_path=restored / "profiles" / "default.toml",
            ).read()
            self.assertIsNotNone(restored_profile)
            self.assertEqual(
                restored_profile.profile.visual.reference_ids,
                (reference_id,),
            )
            pack = ImageAssetStore(restored / "private" / "images").resolve_identity_pack(
                primary_reference_id=reference_id,
                profile_id=profile_id,
                identity_version=1,
            )
            self.assertEqual(pack.primary_reference_id, reference_id)
            connection = sqlite3.connect(
                restored / "private" / "relationships.sqlite3"
            )
            event_count = connection.execute(
                "SELECT COUNT(*) FROM relationship_event"
            ).fetchone()[0]
            connection.close()
            self.assertEqual(event_count, 1)

    def test_inventory_blocks_upgrade_when_confirmed_identity_is_damaged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "companion-home"
            reference_id, profile_id = _create_durable_data(root)
            assets = ImageAssetStore(root / "private" / "images")
            pack = assets.resolve_identity_pack(
                primary_reference_id=reference_id,
                profile_id=profile_id,
                identity_version=1,
            )
            pack.members[0].path.write_bytes(b"damaged")
            manager = BackupManager(
                CompanionDataLayout.for_codex(data_root=root),
                product_version="0.7.0-dev.5",
                clock=lambda: NOW,
            )

            inventory = manager.inspect()

            self.assertFalse(inventory.healthy)
            self.assertTrue(inventory.identity_configured)
            self.assertIn("人物身份包", " ".join(inventory.blockers))

    def test_corrupted_backup_is_rejected_before_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "companion-home"
            _create_durable_data(root)
            manager = BackupManager(
                CompanionDataLayout.for_codex(data_root=root),
                product_version="0.7.0-dev.4",
                clock=lambda: NOW,
            )
            snapshot = manager.create()
            manifest_path = snapshot.path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            image_item = next(
                item
                for item in manifest["items"]
                if item["relative_path"].endswith(".png")
            )
            (snapshot.path / image_item["relative_path"]).write_bytes(b"damaged")

            with self.assertRaisesRegex(BackupError, "校验"):
                manager.verify(snapshot.backup_id)
            with self.assertRaisesRegex(BackupError, "校验"):
                manager.list()
            with self.assertRaises(BackupError):
                manager.recover_copy(
                    snapshot.backup_id,
                    root.parent / "should-not-exist",
                )
            self.assertFalse((root.parent / "should-not-exist").exists())

    def test_inventory_reads_old_relationship_schema_without_migrating_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "companion-home"
            database = root / "private" / "relationships.sqlite3"
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
                """
            )
            connection.close()
            manager = BackupManager(
                CompanionDataLayout.for_codex(data_root=root),
                product_version="0.7.0-dev.4",
                clock=lambda: NOW,
            )

            inventory = manager.inspect()

            connection = sqlite3.connect(database)
            version = connection.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            connection.close()
            self.assertEqual(inventory.relationship_schema, 1)
            self.assertEqual(version, "1")
            self.assertNotIn("relationship_event", tables)

    def test_backup_rejects_symlinked_durable_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "companion-home"
            profile_root = root / "profiles"
            profile_root.mkdir(parents=True)
            outside = root.parent / "outside.toml"
            outside.write_text("schema_version = 3", encoding="utf-8")
            try:
                (profile_root / "default.toml").symlink_to(outside)
            except (NotImplementedError, OSError):
                self.skipTest("当前平台不支持符号链接")
            manager = BackupManager(
                CompanionDataLayout.for_codex(data_root=root),
                product_version="0.7.0-dev.4",
                clock=lambda: NOW,
            )

            with self.assertRaisesRegex(BackupError, "符号链接"):
                manager.create()

    def test_recovery_never_overwrites_an_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "companion-home"
            _create_durable_data(root)
            manager = BackupManager(
                CompanionDataLayout.for_codex(data_root=root),
                product_version="0.7.0-dev.4",
                clock=lambda: NOW,
            )
            snapshot = manager.create()
            destination = root.parent / "existing"
            destination.mkdir()
            marker = destination / "keep.txt"
            marker.write_text("keep", encoding="utf-8")

            with self.assertRaisesRegex(BackupError, "已经存在"):
                manager.recover_copy(snapshot.backup_id, destination)

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_backup_retries_when_persona_changes_during_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "companion-home"
            _create_durable_data(root)
            profile = root / "profiles" / "default.toml"

            class MutatingManager(BackupManager):
                calls = 0

                def _source_files(self):
                    self.calls += 1
                    if self.calls == 3:
                        profile.write_text(
                            profile.read_text(encoding="utf-8")
                            + "\n# concurrent save completed\n",
                            encoding="utf-8",
                        )
                    return super()._source_files()

            manager = MutatingManager(
                CompanionDataLayout.for_codex(data_root=root),
                product_version="0.7.0-dev.4",
                clock=lambda: NOW,
            )

            snapshot = manager.create()

            self.assertGreaterEqual(manager.calls, 6)
            self.assertTrue(manager.verify(snapshot.backup_id).valid)
            published = [
                path
                for path in (root / "backups").iterdir()
                if path.is_dir() and not path.name.startswith(".")
            ]
            self.assertEqual(published, [snapshot.path])

    def test_failed_final_verification_leaves_no_published_restore_point(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "companion-home"
            _create_durable_data(root)

            class FailingVerificationManager(BackupManager):
                def verify(self, backup_id: str):
                    raise BackupError("模拟最终校验失败")

            manager = FailingVerificationManager(
                CompanionDataLayout.for_codex(data_root=root),
                product_version="0.7.0-dev.4",
                clock=lambda: NOW,
            )

            with self.assertRaisesRegex(BackupError, "最终校验失败"):
                manager.create()

            published = [
                path
                for path in (root / "backups").iterdir()
                if path.is_dir() and not path.name.startswith(".")
            ]
            self.assertEqual(published, [])

    def test_recovery_does_not_change_existing_parent_permissions(self) -> None:
        if not hasattr(Path, "chmod"):
            self.skipTest("当前平台不支持权限检查")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "companion-home"
            _create_durable_data(root)
            manager = BackupManager(
                CompanionDataLayout.for_codex(data_root=root),
                product_version="0.7.0-dev.4",
                clock=lambda: NOW,
            )
            snapshot = manager.create()
            parent = root.parent / "user-selected-parent"
            parent.mkdir()
            parent.chmod(0o755)

            manager.recover_copy(snapshot.backup_id, parent / "recovered")

            self.assertEqual(parent.stat().st_mode & 0o777, 0o755)


if __name__ == "__main__":
    unittest.main()
