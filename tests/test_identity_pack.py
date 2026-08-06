from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from companion_kit.identity_pack import (
    BODY_SHAPE,
    PRIMARY_FACE,
    PROFILE_FACE,
    select_identity_roles,
)
from companion_kit.image_assets import ImageAssetError, ImageAssetStore
from tests.png_fixture import tiny_png


class IdentityPackTests(unittest.TestCase):
    def _confirm_primary(
        self,
        store: ImageAssetStore,
        *,
        profile_id: str = "companion",
        task_scope: str = "primary-task",
    ):
        candidate = store.store_candidate(
            profile_id=profile_id,
            identity_version=1,
            image_bytes=tiny_png(rgba=b"\x20\x40\x60\xff"),
            task_scope=task_scope,
            role=PRIMARY_FACE,
        )
        return store.confirm_candidate(
            candidate_id=candidate.candidate_id,
            profile_id=profile_id,
            identity_version=1,
            task_scope=task_scope,
        )

    def _confirm_supplement(
        self,
        store: ImageAssetStore,
        *,
        primary_reference_id: str,
        role: str,
        rgba: bytes,
        task_scope: str,
    ):
        candidate = store.store_candidate(
            profile_id="companion",
            identity_version=1,
            image_bytes=tiny_png(rgba=rgba),
            task_scope=task_scope,
            role=role,
            primary_reference_id=primary_reference_id,
        )
        return store.confirm_candidate(
            candidate_id=candidate.candidate_id,
            profile_id="companion",
            identity_version=1,
            task_scope=task_scope,
        )

    def test_identity_pack_has_one_primary_and_two_optional_roles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ImageAssetStore(Path(tmp).resolve() / "assets")
            primary = self._confirm_primary(store)
            profile = self._confirm_supplement(
                store,
                primary_reference_id=primary.reference_id,
                role=PROFILE_FACE,
                rgba=b"\x60\x40\x20\xff",
                task_scope="profile-task",
            )
            body = self._confirm_supplement(
                store,
                primary_reference_id=primary.reference_id,
                role=BODY_SHAPE,
                rgba=b"\x10\x80\x30\xff",
                task_scope="body-task",
            )

            pack = store.resolve_identity_pack(
                primary_reference_id=primary.reference_id,
                profile_id="companion",
                identity_version=1,
            )

            self.assertEqual(pack.revision, 3)
            self.assertEqual(
                tuple(member.role for member in pack.members),
                (PRIMARY_FACE, PROFILE_FACE, BODY_SHAPE),
            )
            self.assertEqual(
                tuple(member.reference_id for member in pack.members),
                (primary.reference_id, profile.reference_id, body.reference_id),
            )
            self.assertTrue(all(member.path.is_file() for member in pack.members))
            manifest = json.loads(
                (store.root / "identities" / "companion" / "v1" / "pack.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertNotIn("path", json.dumps(manifest))
            self.assertNotIn("prompt", json.dumps(manifest))

    def test_legacy_selected_reference_is_adapted_without_writing_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "assets"
            store = ImageAssetStore(root)
            primary = self._confirm_primary(store)
            profile_root = root / "identities" / "companion" / "v1"
            (profile_root / "pack.json").unlink()
            for member in (profile_root / "members").iterdir():
                member.unlink()
            (profile_root / "members").rmdir()

            pack = store.resolve_identity_pack(
                primary_reference_id=primary.reference_id,
                profile_id="companion",
                identity_version=1,
            )

            self.assertEqual(pack.revision, 0)
            self.assertEqual(tuple(member.role for member in pack.members), (PRIMARY_FACE,))
            self.assertEqual(pack.members[0].path, profile_root / "selected.png")
            self.assertFalse((profile_root / "pack.json").exists())
            self.assertFalse((profile_root / "members").exists())

    def test_first_supplement_materializes_legacy_primary_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "assets"
            store = ImageAssetStore(root)
            primary = self._confirm_primary(store)
            profile_root = root / "identities" / "companion" / "v1"
            (profile_root / "pack.json").unlink()
            for member in (profile_root / "members").iterdir():
                member.unlink()
            (profile_root / "members").rmdir()

            profile = self._confirm_supplement(
                store,
                primary_reference_id=primary.reference_id,
                role=PROFILE_FACE,
                rgba=b"\x60\x40\x20\xff",
                task_scope="profile-task",
            )
            pack = store.resolve_identity_pack(
                primary_reference_id=primary.reference_id,
                profile_id="companion",
                identity_version=1,
            )

            self.assertEqual(pack.revision, 1)
            self.assertEqual(pack.roles, (PRIMARY_FACE, PROFILE_FACE))
            self.assertEqual(pack.member(PROFILE_FACE).reference_id, profile.reference_id)
            self.assertTrue(all(member.path.parent.name == "members" for member in pack.members))
            self.assertTrue((profile_root / "selected.png").is_file())

    def test_replacing_optional_role_keeps_primary_and_removes_old_member(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ImageAssetStore(Path(tmp).resolve() / "assets")
            primary = self._confirm_primary(store)
            first = self._confirm_supplement(
                store,
                primary_reference_id=primary.reference_id,
                role=PROFILE_FACE,
                rgba=b"\x60\x40\x20\xff",
                task_scope="first-profile-task",
            )
            replacement = self._confirm_supplement(
                store,
                primary_reference_id=primary.reference_id,
                role=PROFILE_FACE,
                rgba=b"\x80\x30\x50\xff",
                task_scope="replacement-profile-task",
            )

            pack = store.resolve_identity_pack(
                primary_reference_id=primary.reference_id,
                profile_id="companion",
                identity_version=1,
            )

            self.assertEqual(pack.revision, 3)
            self.assertEqual(pack.roles, (PRIMARY_FACE, PROFILE_FACE))
            self.assertEqual(pack.member(PRIMARY_FACE).reference_id, primary.reference_id)
            self.assertEqual(pack.member(PROFILE_FACE).reference_id, replacement.reference_id)
            self.assertFalse(first.path.exists())

    def test_primary_rollback_refuses_to_delete_changed_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ImageAssetStore(Path(tmp).resolve() / "assets")
            candidate = store.store_candidate(
                profile_id="companion",
                identity_version=1,
                image_bytes=tiny_png(rgba=b"\x20\x40\x60\xff"),
                task_scope="primary-task",
                role=PRIMARY_FACE,
            )
            primary = store.confirm_candidate(
                candidate_id=candidate.candidate_id,
                profile_id="companion",
                identity_version=1,
                task_scope="primary-task",
            )
            self._confirm_supplement(
                store,
                primary_reference_id=primary.reference_id,
                role=PROFILE_FACE,
                rgba=b"\x60\x40\x20\xff",
                task_scope="profile-task",
            )

            with self.assertRaisesRegex(ImageAssetError, "已变化"):
                store.rollback_primary_confirmation(
                    candidate_id=candidate.candidate_id,
                    reference_id=primary.reference_id,
                    profile_id="companion",
                    identity_version=1,
                    task_scope="primary-task",
                )

            pack = store.resolve_identity_pack(
                primary_reference_id=primary.reference_id,
                profile_id="companion",
                identity_version=1,
            )
            self.assertEqual(pack.roles, (PRIMARY_FACE, PROFILE_FACE))

    def test_new_layout_trace_without_manifest_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "assets"
            store = ImageAssetStore(root)
            primary = self._confirm_primary(store)
            profile_root = root / "identities" / "companion" / "v1"
            (profile_root / "pack.json").unlink()

            with self.assertRaisesRegex(ImageAssetError, "身份参考包"):
                store.resolve_identity_pack(
                    primary_reference_id=primary.reference_id,
                    profile_id="companion",
                    identity_version=1,
                )

    def test_dangling_new_layout_symlink_never_falls_back_to_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "assets"
            store = ImageAssetStore(root)
            primary = self._confirm_primary(store)
            profile_root = root / "identities" / "companion" / "v1"
            (profile_root / "pack.json").unlink()
            for member in (profile_root / "members").iterdir():
                member.unlink()
            (profile_root / "members").rmdir()

            for trace_name in ("pack.json", "members"):
                with self.subTest(trace_name=trace_name):
                    trace = profile_root / trace_name
                    trace.symlink_to(profile_root / f"missing-{trace_name}")
                    with self.assertRaisesRegex(ImageAssetError, "身份参考包|符号链接"):
                        store.resolve_identity_pack(
                            primary_reference_id=primary.reference_id,
                            profile_id="companion",
                            identity_version=1,
                        )
                    trace.unlink()

    def test_member_directory_symlink_is_rejected_before_reading_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "assets"
            store = ImageAssetStore(root)
            primary = self._confirm_primary(store)
            profile_root = root / "identities" / "companion" / "v1"
            members = profile_root / "members"
            external = root.parent / "external-members"
            members.rename(external)
            members.symlink_to(external, target_is_directory=True)

            with self.assertRaisesRegex(ImageAssetError, "符号链接"):
                store.resolve_identity_pack(
                    primary_reference_id=primary.reference_id,
                    profile_id="companion",
                    identity_version=1,
                )

    def test_declared_missing_supplement_makes_whole_pack_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ImageAssetStore(Path(tmp).resolve() / "assets")
            primary = self._confirm_primary(store)
            supplement = self._confirm_supplement(
                store,
                primary_reference_id=primary.reference_id,
                role=PROFILE_FACE,
                rgba=b"\x60\x40\x20\xff",
                task_scope="profile-task",
            )
            supplement.path.unlink()

            with self.assertRaisesRegex(ImageAssetError, "身份参考包"):
                store.resolve_identity_pack(
                    primary_reference_id=primary.reference_id,
                    profile_id="companion",
                    identity_version=1,
                )

    def test_supplement_candidate_is_bound_to_pack_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ImageAssetStore(Path(tmp).resolve() / "assets")
            primary = self._confirm_primary(store)
            stale = store.store_candidate(
                profile_id="companion",
                identity_version=1,
                image_bytes=tiny_png(rgba=b"\x60\x40\x20\xff"),
                task_scope="stale-profile-task",
                role=PROFILE_FACE,
                primary_reference_id=primary.reference_id,
            )
            pack_path = store.root / "identities" / "companion" / "v1" / "pack.json"
            manifest = json.loads(pack_path.read_text(encoding="utf-8"))
            manifest["revision"] += 1
            pack_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ImageAssetError, "版本"):
                store.confirm_candidate(
                    candidate_id=stale.candidate_id,
                    profile_id="companion",
                    identity_version=1,
                    task_scope="stale-profile-task",
                )

    def test_same_supplement_can_finish_cleanup_after_commit_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ImageAssetStore(Path(tmp).resolve() / "assets")
            primary = self._confirm_primary(store)
            candidate = store.store_candidate(
                profile_id="companion",
                identity_version=1,
                image_bytes=tiny_png(rgba=b"\x60\x40\x20\xff"),
                task_scope="profile-task",
                role=PROFILE_FACE,
                primary_reference_id=primary.reference_id,
            )
            first = store.confirm_candidate(
                candidate_id=candidate.candidate_id,
                profile_id="companion",
                identity_version=1,
                task_scope="profile-task",
                retain_candidate=True,
            )

            recovered = store.confirm_candidate(
                candidate_id=candidate.candidate_id,
                profile_id="companion",
                identity_version=1,
                task_scope="profile-task",
                retain_candidate=True,
            )
            store.discard_candidate(
                candidate_id=candidate.candidate_id,
                profile_id="companion",
                identity_version=1,
                task_scope="profile-task",
            )

            self.assertEqual(first.reference_id, recovered.reference_id)
            self.assertEqual(recovered.role, PROFILE_FACE)
            self.assertEqual(recovered.pack_revision, 2)
            self.assertFalse((store.root / "identities" / "companion" / "v1" / "pending.png").exists())

    def test_supplement_cannot_exist_before_primary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ImageAssetStore(Path(tmp).resolve() / "assets")

            with self.assertRaisesRegex(ImageAssetError, "主脸"):
                store.store_candidate(
                    profile_id="companion",
                    identity_version=1,
                    image_bytes=tiny_png(),
                    task_scope="profile-task",
                    role=PROFILE_FACE,
                    primary_reference_id="ref_1234567890abcdef",
                )

    def test_scene_role_selection_never_uses_more_than_two_references(self) -> None:
        available = (PRIMARY_FACE, PROFILE_FACE, BODY_SHAPE)

        self.assertEqual(select_identity_roles("窗边自然自拍", available), (PRIMARY_FACE,))
        self.assertEqual(
            select_identity_roles("回眸侧脸近照", available),
            (PRIMARY_FACE, PROFILE_FACE),
        )
        self.assertEqual(
            select_identity_roles("全身穿搭照", available),
            (PRIMARY_FACE, BODY_SHAPE),
        )
        self.assertEqual(
            select_identity_roles("侧身回头的全身照", available),
            (PRIMARY_FACE, BODY_SHAPE),
        )
        self.assertEqual(
            select_identity_roles("侧脸近照", (PRIMARY_FACE, BODY_SHAPE)),
            (PRIMARY_FACE,),
        )


if __name__ == "__main__":
    unittest.main()
