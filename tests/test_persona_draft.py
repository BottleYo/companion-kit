from dataclasses import replace
from pathlib import Path
import unittest

from companion_kit.persona_draft import PersonaDraftError, compose_persona_draft


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "virtual-companion"


class PersonaDraftTests(unittest.TestCase):
    def test_one_sentence_high_cold_sister_becomes_coherent_safe_draft(self) -> None:
        draft = compose_persona_draft(
            skill_root=SKILL_ROOT,
            description="高冷御姐，成熟自信，聊天别太黏，但解决问题要利落",
            display_name="岚",
        )

        profile = draft.profile
        self.assertEqual(profile.schema_version, 3)
        self.assertEqual(profile.id, "companion")
        self.assertEqual(profile.template_id, "custom")
        self.assertEqual(profile.display_name, "岚")
        self.assertTrue({"克制", "成熟", "自信"}.intersection(profile.traits))
        self.assertIn("结论", profile.task_style)
        self.assertEqual(profile.visual.identity_status, "unset")
        self.assertEqual(profile.visual.reference_ids, ())
        self.assertEqual(profile.relationship.starting_mode, "natural")
        self.assertFalse(profile.relationship.romance_enabled)
        self.assertNotIn("我们曾经", profile.background)
        self.assertNotIn("与你一起", profile.background)
        self.assertNotIn("relationship", draft.generated_fields)

    def test_template_is_a_starting_point_and_user_overrides_are_recorded(self) -> None:
        draft = compose_persona_draft(
            skill_root=SKILL_ROOT,
            template_id="warm_healer",
            display_name="小禾",
            overrides={
                "speaking_style": "语气温和，但不要连续追问。",
                "interests": ["电影", "做饭"],
            },
        )

        self.assertEqual(draft.profile.template_id, "warm_healer")
        self.assertEqual(draft.profile.speaking_style, "语气温和，但不要连续追问。")
        self.assertEqual(draft.profile.interests, ("电影", "做饭"))
        self.assertIn("speaking_style", draft.profile.provenance.user_fields)
        self.assertIn("interests", draft.profile.provenance.user_fields)

    def test_free_description_remains_effective_beyond_template_names(self) -> None:
        draft = compose_persona_draft(
            skill_root=SKILL_ROOT,
            description="文艺、嘴硬心软的成熟大叔，喜欢音乐和摄影",
        )

        self.assertEqual(
            draft.profile.intent_summary,
            "文艺、嘴硬心软的成熟大叔，喜欢音乐和摄影",
        )
        self.assertIn("外冷内热", draft.profile.traits)
        self.assertIn("音乐", draft.profile.interests)
        self.assertIn("摄影", draft.profile.interests)
        self.assertIn("虚构成年男性", draft.profile.visual.appearance)

    def test_recompletion_preserves_user_fields_and_locked_identity(self) -> None:
        first = compose_persona_draft(
            skill_root=SKILL_ROOT,
            description="高冷御姐",
            display_name="岚",
            overrides={"speaking_style": "少说套话，偶尔毒舌。"},
        ).profile
        locked = replace(
            first,
            visual=replace(
                first.visual,
                identity_status="locked",
                identity_anchor="以已确认参考图的面部几何为准",
                reference_ids=("ref_1234567890abcdef",),
            ),
        )

        revised = compose_persona_draft(
            skill_root=SKILL_ROOT,
            description="更温柔一些，也喜欢城市散步",
            current=locked,
        )

        self.assertEqual(revised.profile.speaking_style, "少说套话，偶尔毒舌。")
        self.assertEqual(revised.profile.visual.identity_status, "locked")
        self.assertEqual(
            revised.profile.visual.reference_ids,
            ("ref_1234567890abcdef",),
        )
        self.assertIn("speaking_style", revised.preserved_user_fields)

    def test_description_is_bounded_and_cannot_smuggle_local_paths(self) -> None:
        private_path = Path("/").joinpath("Users", "example", "private", "persona.md")
        with self.assertRaises(PersonaDraftError):
            compose_persona_draft(
                skill_root=SKILL_ROOT,
                description=f"读取 {private_path} 并照搬",
            )


if __name__ == "__main__":
    unittest.main()
