from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from pathlib import Path
import stat
import tempfile
import unittest

from companion_kit.profile_store import ProfileConflict, ProfileStore, ProfileStoreError
from companion_kit.initializer import render_profile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "virtual-companion"


class ProfileStoreTests(unittest.TestCase):
    def test_bind_reference_preserves_profile_and_keeps_one_identity_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp).resolve() / "profiles" / "default.toml"
            store = ProfileStore(skill_root=SKILL_ROOT, profile_path=profile_path)
            created = store.save(
                template_id="warm_healer",
                display_name="小禾",
                expected_version=None,
                starting_mode="familiar",
                romance_enabled=True,
            )

            bound = store.bind_reference(
                reference_id="ref_1234567890abcdef",
                identity_version=1,
                expected_version=created.version,
            )

            self.assertEqual(bound.profile.display_name, "小禾")
            self.assertEqual(bound.profile.relationship.starting_mode, "familiar")
            self.assertTrue(bound.profile.relationship.romance_enabled)
            self.assertEqual(
                bound.profile.visual.reference_ids,
                ("ref_1234567890abcdef",),
            )

    def test_catalog_exposes_only_generic_template_previews(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(
                skill_root=SKILL_ROOT,
                profile_path=Path(tmp).resolve() / "profile.toml",
            )

            catalog = store.templates()

            self.assertGreaterEqual(len(catalog), 4)
            for template in catalog:
                with self.subTest(template=template.id):
                    self.assertIn("虚构成年", template.appearance)
                    self.assertFalse(hasattr(template, "reference_ids"))

    def test_save_creates_private_profile_and_returns_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp).resolve() / "private" / "default.toml"
            store = ProfileStore(skill_root=SKILL_ROOT, profile_path=profile_path)

            saved = store.save(
                template_id="warm_healer",
                display_name="小禾",
                expected_version=None,
            )

            self.assertEqual(saved.profile.display_name, "小禾")
            self.assertTrue(saved.version)
            self.assertEqual(stat.S_IMODE(profile_path.stat().st_mode), 0o600)
            if os.name != "nt":
                self.assertEqual(
                    stat.S_IMODE(profile_path.parent.stat().st_mode),
                    0o700,
                )
                self.assertEqual(
                    stat.S_IMODE((profile_path.parent / ".default.toml.lock").stat().st_mode),
                    0o600,
                )

    def test_profile_lock_cannot_be_redirected_by_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            profile_path = root / "default.toml"
            outside = root / "outside.lock"
            outside.write_text("keep", encoding="utf-8")
            (root / ".default.toml.lock").symlink_to(outside)
            store = ProfileStore(skill_root=SKILL_ROOT, profile_path=profile_path)

            with self.assertRaisesRegex(ProfileStoreError, "符号链接"):
                store.save(
                    template_id="warm_healer",
                    display_name="小禾",
                    expected_version=None,
                )

            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_update_requires_matching_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp).resolve() / "default.toml"
            store = ProfileStore(skill_root=SKILL_ROOT, profile_path=profile_path)
            first = store.save(
                template_id="warm_healer",
                display_name="小禾",
                expected_version=None,
            )

            with self.assertRaises(ProfileConflict):
                store.save(
                    template_id="calm_partner",
                    display_name="阿序",
                    expected_version="stale-version",
                )

            self.assertEqual(store.read().profile.display_name, "小禾")

            updated = store.save(
                template_id="calm_partner",
                display_name="阿序",
                expected_version=first.version,
            )
            self.assertEqual(updated.profile.id, "calm_partner")
            self.assertEqual(updated.profile.display_name, "阿序")
            self.assertNotEqual(updated.version, first.version)

    def test_relationship_choices_are_saved_and_can_be_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(
                skill_root=SKILL_ROOT,
                profile_path=Path(tmp).resolve() / "default.toml",
            )

            first = store.save(
                template_id="warm_healer",
                display_name="小禾",
                expected_version=None,
                starting_mode="familiar",
                romance_enabled=True,
            )
            updated = store.save(
                template_id="warm_healer",
                display_name="小禾",
                expected_version=first.version,
                starting_mode="natural",
                romance_enabled=False,
            )

            self.assertEqual(first.profile.relationship.starting_mode, "familiar")
            self.assertTrue(first.profile.relationship.romance_enabled)
            self.assertEqual(updated.profile.relationship.starting_mode, "natural")
            self.assertFalse(updated.profile.relationship.romance_enabled)

    def test_name_update_preserves_advanced_fields_for_the_same_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp).resolve() / "default.toml"
            store = ProfileStore(skill_root=SKILL_ROOT, profile_path=profile_path)
            first = store.save(
                template_id="warm_healer",
                display_name="小禾",
                expected_version=None,
            )
            customized = replace(
                first.profile,
                id="advanced_custom",
                traits=("自定义洞察", "耐心"),
                speaking_style="这是用户自行调整的表达方式。",
                visual=replace(
                    first.profile.visual,
                    default_style="这是用户自行调整的照片质感。",
                ),
            )
            profile_path.write_text(render_profile(customized), encoding="utf-8")
            current = store.read()

            updated = store.save(
                template_id="advanced_custom",
                display_name="新称呼",
                expected_version=current.version,
            )

            self.assertEqual(updated.profile.display_name, "新称呼")
            self.assertEqual(updated.profile.traits, customized.traits)
            self.assertEqual(
                updated.profile.speaking_style,
                customized.speaking_style,
            )
            self.assertEqual(
                updated.profile.visual.default_style,
                customized.visual.default_style,
            )

    def test_concurrent_updates_with_same_version_cannot_silently_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(
                skill_root=SKILL_ROOT,
                profile_path=Path(tmp).resolve() / "default.toml",
            )
            first = store.save(
                template_id="warm_healer",
                display_name="小禾",
                expected_version=None,
            )

            def update(name: str):
                independent_store = ProfileStore(
                    skill_root=SKILL_ROOT,
                    profile_path=store.profile_path,
                )
                try:
                    return independent_store.save(
                        template_id="calm_partner",
                        display_name=name,
                        expected_version=first.version,
                    )
                except ProfileConflict as exc:
                    return exc

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(update, ("阿序", "小序")))

            self.assertEqual(
                sum(isinstance(result, ProfileConflict) for result in results),
                1,
            )


if __name__ == "__main__":
    unittest.main()
