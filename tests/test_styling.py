from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from companion_kit.config import load_profile
from companion_kit.initializer import initialize_profile
from companion_kit.styling import (
    StylingConflict,
    StylingPreferenceStore,
    resolve_style_profile,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "virtual-companion"


class StylingPreferenceTests(unittest.TestCase):
    def _profile(self, root: Path, *, template_id: str = "calm_partner"):
        profile_path = root / "profiles" / "default.toml"
        initialize_profile(
            skill_root=SKILL_ROOT,
            template_id=template_id,
            display_name=None,
            output=profile_path,
        )
        return load_profile(profile_path)

    def test_persona_defaults_become_style_dna_without_public_user_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = self._profile(Path(tmp).resolve())

            resolved = resolve_style_profile(profile)

            self.assertFalse(resolved.user_configured)
            self.assertTrue(resolved.direction)
            self.assertEqual(resolved.signature_elements, ())
            self.assertEqual(resolved.avoid_elements, ())
            self.assertGreater(sum(weight for _, weight in resolved.archetype_weights), 0)

    def test_private_preferences_are_isolated_and_contain_no_conversation_or_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            store = StylingPreferenceStore(root / "private" / "styling")

            first = store.save(
                profile_id="private-persona-a",
                direction="冷静利落，带一点复古感",
                boldness="balanced",
                signature_elements=("复古镜框", "细金属耳饰"),
                avoid_elements=("荧光色",),
                expected_version=None,
            )
            store.save(
                profile_id="private-persona-b",
                direction="自然松弛，材质柔软",
                boldness="restrained",
                signature_elements=(),
                avoid_elements=("大 Logo",),
                expected_version=None,
            )

            self.assertEqual(
                store.inspect(profile_id="private-persona-a").preferences,
                first.preferences,
            )
            self.assertNotEqual(
                store.inspect(profile_id="private-persona-a").preferences,
                store.inspect(profile_id="private-persona-b").preferences,
            )
            serialized = "".join(
                path.read_text(encoding="utf-8")
                for path in (root / "private" / "styling").glob("*.json")
            )
            self.assertNotIn("private-persona-a", serialized)
            self.assertNotIn("session_id", serialized)
            self.assertNotIn("prompt", serialized)
            self.assertNotIn(str(root), serialized)
            for path in (root / "private" / "styling").glob("*.json"):
                self.assertEqual(json.loads(path.read_text("utf-8"))["schema_version"], 1)

    def test_optimistic_save_never_overwrites_a_newer_preference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StylingPreferenceStore(Path(tmp).resolve() / "styling")
            current = store.save(
                profile_id="private-persona",
                direction="简洁知性",
                boldness="balanced",
                signature_elements=(),
                avoid_elements=(),
                expected_version=None,
            )
            updated = store.save(
                profile_id="private-persona",
                direction="简洁知性，增加一点颜色",
                boldness="expressive",
                signature_elements=("颜色重点",),
                avoid_elements=(),
                expected_version=current.version,
            )

            with self.assertRaises(StylingConflict):
                store.save(
                    profile_id="private-persona",
                    direction="这次不应该覆盖",
                    boldness="restrained",
                    signature_elements=(),
                    avoid_elements=(),
                    expected_version=current.version,
                )
            self.assertEqual(
                store.inspect(profile_id="private-persona").version,
                updated.version,
            )

    def test_same_element_cannot_be_required_and_forbidden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StylingPreferenceStore(Path(tmp).resolve() / "styling")
            with self.assertRaisesRegex(ValueError, "不能既是"):
                store.save(
                    profile_id="private-persona",
                    direction="简洁自然",
                    boldness="balanced",
                    signature_elements=("复古镜框",),
                    avoid_elements=("复古镜框",),
                    expected_version=None,
                )


if __name__ == "__main__":
    unittest.main()
