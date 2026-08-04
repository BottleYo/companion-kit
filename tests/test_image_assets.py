from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import stat
import tempfile
import unittest

from companion_kit.image_assets import ImageAssetError, ImageAssetStore
from tests.png_fixture import tiny_png


class ImageAssetStoreTests(unittest.TestCase):
    def test_candidate_confirmation_keeps_one_private_sanitized_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "private-assets"
            store = ImageAssetStore(root)
            secret_metadata = b"private-metadata-must-disappear"

            candidate = store.store_candidate(
                profile_id="warm_healer",
                identity_version=1,
                image_bytes=tiny_png(metadata=secret_metadata),
                task_scope="codex-task-one",
            )
            selected = store.confirm_candidate(
                candidate_id=candidate.candidate_id,
                profile_id="warm_healer",
                identity_version=1,
                task_scope="codex-task-one",
            )
            resolved = store.resolve_reference(
                reference_id=selected.reference_id,
                profile_id="warm_healer",
                identity_version=1,
            )

            self.assertEqual(resolved, selected.path)
            self.assertNotIn(secret_metadata, resolved.read_bytes())
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(resolved.stat().st_mode), 0o600)
            self.assertEqual(len(list(root.rglob("*.png"))), 1)

            manifests = list(root.rglob("*.json"))
            self.assertEqual(len(manifests), 1)
            manifest_text = manifests[0].read_text(encoding="utf-8")
            manifest = json.loads(manifest_text)
            self.assertNotIn("codex-task-one", manifest_text)
            self.assertNotIn("path", manifest)
            self.assertEqual(manifest["reference_id"], selected.reference_id)

    def test_new_candidate_replaces_pending_slot_without_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ImageAssetStore(Path(tmp).resolve() / "assets")
            first = store.store_candidate(
                profile_id="warm_healer",
                identity_version=1,
                image_bytes=tiny_png(),
                task_scope="task-one",
            )
            second = store.store_candidate(
                profile_id="warm_healer",
                identity_version=1,
                image_bytes=tiny_png(metadata=b"replacement"),
                task_scope="task-two",
            )

            self.assertNotEqual(first.candidate_id, second.candidate_id)
            with self.assertRaises(ImageAssetError):
                store.confirm_candidate(
                    candidate_id=first.candidate_id,
                    profile_id="warm_healer",
                    identity_version=1,
                    task_scope="task-one",
                )
            with self.assertRaises(ImageAssetError):
                store.confirm_candidate(
                    candidate_id=second.candidate_id,
                    profile_id="warm_healer",
                    identity_version=1,
                    task_scope="wrong-task",
                )
            self.assertEqual(len(list(store.root.rglob("pending.png"))), 1)

    def test_transient_artifact_is_task_bound_and_deleted_after_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ImageAssetStore(Path(tmp).resolve() / "assets")
            artifact = store.store_artifact(
                profile_id="warm_healer",
                identity_version=1,
                image_bytes=tiny_png(),
                task_scope="current-codex-task",
            )

            with self.assertRaises(ImageAssetError):
                store.finish_delivery(
                    artifact_id=artifact.artifact_id,
                    task_scope="another-task",
                )
            self.assertTrue(artifact.path.is_file())
            store.finish_delivery(
                artifact_id=artifact.artifact_id,
                task_scope="current-codex-task",
            )
            self.assertFalse(artifact.path.exists())

    def test_stale_transient_artifact_is_pruned_without_touching_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            current = [datetime(2026, 8, 4, 12, 0, tzinfo=UTC)]
            store = ImageAssetStore(
                Path(tmp).resolve() / "assets",
                clock=lambda: current[0],
            )
            artifact = store.store_artifact(
                profile_id="warm_healer",
                identity_version=1,
                image_bytes=tiny_png(),
                task_scope="current-task",
            )
            current[0] += timedelta(hours=2)

            removed = store.prune_runtime(max_age=timedelta(hours=1))

            self.assertEqual(removed, 1)
            self.assertFalse(artifact.path.exists())

    def test_crash_between_artifact_and_manifest_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ImageAssetStore(Path(tmp).resolve() / "assets")
            incomplete = store.root / "runtime" / "art_0123456789abcdef"
            incomplete.mkdir(parents=True)
            (incomplete / "result.png").write_bytes(tiny_png())

            removed = store.prune_runtime()
            replacement = store.store_artifact(
                profile_id="warm_healer",
                identity_version=1,
                image_bytes=tiny_png(),
                task_scope="current-task",
            )

            self.assertEqual(removed, 1)
            self.assertFalse(incomplete.exists())
            self.assertTrue(replacement.path.is_file())

    @unittest.skipIf(os.name == "nt", "Windows 符号链接权限不稳定")
    def test_asset_root_rejects_symbolic_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            real = base / "real"
            real.mkdir()
            link = base / "linked"
            link.symlink_to(real, target_is_directory=True)

            with self.assertRaises(ImageAssetError):
                ImageAssetStore(link)


if __name__ == "__main__":
    unittest.main()
