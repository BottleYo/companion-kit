from __future__ import annotations

import unittest

from companion_kit.codex_turn import (
    CodexTurnKind,
    classify_codex_turn,
    make_turn_token,
)


class CodexTurnClassifierTests(unittest.TestCase):
    def test_natural_photo_requests_are_new_photos(self) -> None:
        for text in (
            "拍张照片给我",
            "给我发一张你现在的自拍",
            "再拍一张，这次换个场景",
            "换个发型重新拍一张",
            "我想看你现在的样子，拍一张吧",
            "发一张你现在的样子",
            "给我来张你在海边的图",
            "帮我生成一张你的自拍",
            "生成一张你的照片",
            "做一张你的自拍",
            "画一张你的照片",
            "出一张你的自拍",
            "我想要一张你的照片",
            "生成你的照片",
            "我想要你的自拍",
            "画你的照片",
            "来一张你的图",
            "给我一张你的图",
            "给我看看你的样子",
            "让我看看你的照片",
            "给我来张你做饭的图",
            "generate a photo of you",
            "create a selfie of you",
            "create your picture",
            "send me your picture",
            "show your selfie",
            "show me a selfie",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    classify_codex_turn(text).kind,
                    CodexTurnKind.PHOTO_NEW,
                )

    def test_only_explicit_previous_image_changes_are_edits(self) -> None:
        for text in (
            "把上一张的眼镜换成黑框",
            "只改刚才那张照片的表情",
            "在这张图上换一件外套",
            "把上一张改成短发",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    classify_codex_turn(
                        text,
                        previous_image_kind="companion",
                    ).kind,
                    CodexTurnKind.PHOTO_EDIT_PREVIOUS,
                )
                self.assertEqual(
                    classify_codex_turn(text).kind,
                    CodexTurnKind.PASS_THROUGH,
                )

        for text in (
            "把你刚才那张猫图的表情改萌一点",
            "把你上一张自拍改成短发",
        ):
            with self.subTest(text=text, previous_image_kind="generic"):
                self.assertEqual(
                    classify_codex_turn(
                        text,
                        previous_image_kind="generic",
                    ).kind,
                    CodexTurnKind.PASS_THROUGH,
                )

        for text in (
            "把你上一张自拍改成短发",
            "把你刚才那张照片的表情改掉",
            "把你刚才发的那张照片改成短发",
            "把上一张自拍的眼镜换成黑框",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    classify_codex_turn(text).kind,
                    CodexTurnKind.PHOTO_EDIT_PREVIOUS,
                )

        self.assertEqual(
            classify_codex_turn("换个场景再来一张").kind,
            CodexTurnKind.PHOTO_NEW,
        )
        for text in (
            "把上一张图换成蓝色背景",
            "把上一张产品图换成蓝色背景",
            "把上一张产品模特的衣服换成蓝色",
            "把你刚才那张猫图换成夜景",
            "把你这张风景图调亮",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    classify_codex_turn(
                        text,
                        previous_image_kind="companion",
                    ).kind,
                    CodexTurnKind.PASS_THROUGH,
                )

    def test_identity_pack_enhancement_keeps_its_exact_role(self) -> None:
        profile = classify_codex_turn("帮我增强一下侧脸稳定性")
        body = classify_codex_turn("补一张全身体型参考")

        self.assertEqual(profile.kind, CodexTurnKind.PHOTO_NEW)
        self.assertEqual(profile.identity_role, "profile_face")
        self.assertEqual(body.kind, CodexTurnKind.PHOTO_NEW)
        self.assertEqual(body.identity_role, "body_shape")

    def test_coding_meta_discussion_and_generic_image_work_pass_through(self) -> None:
        for text in (
            "请分析照片生成为什么总是沿用上次发型，不要生图",
            "给产品落地页生成一张抽象背景图",
            "帮我给这个产品拍张照片",
            "给这个商品拍张照片",
            "拍张办公室白板照片给我",
            "来一张咖啡杯的照片",
            "生成一张宠物狗照片",
            "修复 imagegen 参数校验测试",
            "帮我检查这张照片里有什么",
            "帮我看看这张照片",
            "看看这张照片里是谁",
            "给我看看这张照片",
            "修复照片上传功能的 bug",
            "分析一下图片生成接口为什么变慢了",
            "解释一下 PreToolUse 的运行逻辑",
            "今天把这个 Python 报错解决掉",
            "帮我生成一张你设计的流程图",
            "画一张你的系统架构图",
            "generate a photo for your product",
            "create a picture for your website",
            "draw on your picture",
            "create art for your photo",
            "make edits to your picture",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    classify_codex_turn(text).kind,
                    CodexTurnKind.PASS_THROUGH,
                )

        for text in (
            "拍一张你拿着咖啡杯的照片给我",
            "给我一张你和宠物狗一起的照片",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    classify_codex_turn(text).kind,
                    CodexTurnKind.PHOTO_NEW,
                )

    def test_turn_token_is_bound_without_embedding_raw_identifiers(self) -> None:
        token = make_turn_token(
            session_id="private-session-id",
            turn_id="private-turn-id",
            mode="new",
            hook_bundle_digest="a" * 64,
        )
        changed = make_turn_token(
            session_id="private-session-id",
            turn_id="another-turn-id",
            mode="new",
            hook_bundle_digest="a" * 64,
        )

        self.assertRegex(token, r"^ckp_[0-9a-f]{24}$")
        self.assertNotEqual(token, changed)
        self.assertNotIn("private-session-id", token)
        self.assertNotIn("private-turn-id", token)


if __name__ == "__main__":
    unittest.main()
