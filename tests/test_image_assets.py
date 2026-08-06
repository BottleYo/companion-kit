from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from companion_kit.image_assets import ImageAssetError, ImageAssetStore
from tests.png_fixture import tiny_png


class ImageAssetStoreTests(unittest.TestCase):
    def test_candidate_confirmation_keeps_sanitized_primary_and_legacy_mirror(self) -> None:
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
            # Identity Pack 使用不可变成员文件，selected.png 作为旧版本主脸兼容镜像。
            self.assertEqual(len(list(root.rglob("*.png"))), 2)

            manifests = list(root.rglob("*.json"))
            self.assertEqual(len(manifests), 2)
            payloads = [json.loads(path.read_text(encoding="utf-8")) for path in manifests]
            serialized = json.dumps(payloads, ensure_ascii=False)
            self.assertNotIn("codex-task-one", serialized)
            self.assertNotIn("path", serialized)
            self.assertTrue(
                any(payload.get("reference_id") == selected.reference_id for payload in payloads)
            )

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

    def test_codex_native_png_can_be_staged_from_a_safe_local_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            codex_home = base / "codex-home"
            source = codex_home / "generated_images" / "codex-result.png"
            source.parent.mkdir(parents=True)
            secret_metadata = b"native-output-metadata"
            source.write_bytes(tiny_png(metadata=secret_metadata))
            store = ImageAssetStore(base / "assets")

            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                candidate = store.store_candidate_file(
                    profile_id="companion",
                    identity_version=1,
                    source_path=source,
                    task_scope="codex-current-task",
                )

            self.assertTrue(candidate.path.is_file())
            self.assertNotIn(secret_metadata, candidate.path.read_bytes())

    @unittest.skipIf(os.name == "nt", "Windows 符号链接权限不稳定")
    def test_candidate_file_rejects_symlink_and_non_png(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            codex_home = base / "codex-home"
            generated = codex_home / "generated_images"
            generated.mkdir(parents=True)
            source = generated / "result.png"
            source.write_bytes(tiny_png())
            linked = generated / "linked.png"
            linked.symlink_to(source)
            jpeg = generated / "photo.jpg"
            jpeg.write_bytes(b"not-a-png")
            store = ImageAssetStore(base / "assets")

            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                for unsafe in (linked, jpeg):
                    with self.subTest(path=unsafe.name), self.assertRaises(ImageAssetError):
                        store.store_candidate_file(
                            profile_id="companion",
                            identity_version=1,
                            source_path=unsafe,
                            task_scope="codex-current-task",
                        )

    def test_codex_candidate_rejects_png_outside_generated_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            codex_home = base / "codex-home"
            (codex_home / "generated_images").mkdir(parents=True)
            unrelated = base / "unrelated-private-photo.png"
            unrelated.write_bytes(tiny_png())
            store = ImageAssetStore(base / "assets")

            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                with self.assertRaisesRegex(ImageAssetError, "generated_images"):
                    store.store_candidate_file(
                        profile_id="companion",
                        identity_version=1,
                        source_path=unrelated,
                        task_scope="codex-current-task",
                    )

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
