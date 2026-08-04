from pathlib import Path
from dataclasses import replace
import unittest

from companion_kit.config import load_profile
from companion_kit.contracts import (
    DecisionKind,
    HostCapabilities,
    HostClass,
    PhotoJobStage,
    RequestEnvelope,
)
from companion_kit.kernel import CompanionKernel, InvalidTransition, transition_photo_job


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE = load_profile(
    PROJECT_ROOT / "skills" / "virtual-companion" / "assets" / "demo_companion.toml"
)


def capabilities(
    host_class: HostClass,
    *,
    image: bool = False,
    delivery: bool = False,
    target: bool = False,
    attachment: bool = False,
) -> HostCapabilities:
    return HostCapabilities(
        host_class=host_class,
        can_execute_tasks=True,
        can_generate_images=image,
        can_deliver_images=delivery,
        has_current_reply_target=target,
        can_attach_local_artifacts=attachment,
    )


class KernelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.kernel = CompanionKernel(PROFILE)

    def decide(self, text: str, caps: HostCapabilities):
        return self.kernel.decide(
            RequestEnvelope(text=text, session_id="test-session", source="test"),
            caps,
        )

    def test_ordinary_tasks_stay_host_owned(self) -> None:
        caps = capabilities(HostClass.DESKTOP, image=True, attachment=True)
        ordinary = (
            "你可以搞一个示例，以进行一个更好的示范和说明",
            "请实现一个自拍检测插件",
            "帮我检查这个项目的问题",
        )

        for text in ordinary:
            with self.subTest(text=text):
                decision = self.decide(text, caps)
                self.assertEqual(decision.kind, DecisionKind.PASS_THROUGH)
                self.assertEqual(decision.reason, "host_owns_request")

    def test_structured_photo_request_is_unambiguous(self) -> None:
        caps = capabilities(HostClass.DESKTOP, image=True, attachment=True)

        decision = self.decide("照片：窗边自然光半身照", caps)

        self.assertEqual(decision.kind, DecisionKind.PHOTO_PLAN)
        self.assertEqual(decision.delivery_mode, "local_artifact")
        self.assertIn("窗边自然光半身照", decision.photo_prompt)
        self.assertIn(PROFILE.visual.identity_anchor, decision.photo_prompt)

    def test_photo_command_without_brief_asks_for_detail(self) -> None:
        caps = capabilities(HostClass.DESKTOP, image=True, attachment=True)

        decision = self.decide("/photo", caps)

        self.assertEqual(decision.kind, DecisionKind.CLARIFY)
        self.assertEqual(decision.reason, "missing_photo_brief")

    def test_negated_photo_text_never_executes(self) -> None:
        caps = capabilities(HostClass.EVENT, image=True, delivery=True, target=True)

        decision = self.decide("不要生成照片，只讨论实现方案", caps)

        self.assertEqual(decision.kind, DecisionKind.PASS_THROUGH)

    def test_negative_visual_constraints_remain_photo_requests(self) -> None:
        caps = capabilities(HostClass.DESKTOP, image=True, attachment=True)
        requests = (
            "照片：窗边半身照，不要水印",
            "照片：别拍糊，保留自然噪点",
            "/photo 不要出现文字和品牌标志",
            "照片：不要拍到路人，只保留主角",
        )

        for text in requests:
            with self.subTest(text=text):
                decision = self.decide(text, caps)
                self.assertEqual(decision.kind, DecisionKind.PHOTO_PLAN)

    def test_explicit_photo_cancellation_stays_host_owned(self) -> None:
        caps = capabilities(HostClass.DESKTOP, image=True, attachment=True)
        cancellations = (
            "照片：不要生成照片，只讨论构图",
            "照片：不要拍她，只讨论构图",
            "照片：别生成这张图，只分析提示词",
            "/photo 不需要生成图片，只做方案评审",
            "照片：禁止生成，只讨论",
            "照片：只分析构图，不执行",
        )

        for text in cancellations:
            with self.subTest(text=text):
                decision = self.decide(text, caps)
                self.assertEqual(decision.kind, DecisionKind.PASS_THROUGH)

    def test_structured_non_execution_requests_stay_host_owned(self) -> None:
        caps = capabilities(HostClass.DESKTOP, image=True, attachment=True)
        requests = (
            "照片：请讨论这张照片的构图",
            "照片：请写一个自拍检测插件",
            "/photo 分析生成图片的测试方案",
            "照片：转述上面的图片需求",
        )

        for text in requests:
            with self.subTest(text=text):
                decision = self.decide(text, caps)
                self.assertEqual(decision.kind, DecisionKind.PASS_THROUGH)

    def test_event_host_requires_current_reply_target_before_generation(self) -> None:
        caps = capabilities(HostClass.EVENT, image=True, delivery=True, target=False)

        decision = self.decide("/photo 玄关全身照", caps)

        self.assertEqual(decision.kind, DecisionKind.BLOCKED)
        self.assertEqual(decision.reason, "current_reply_delivery_unavailable")
        self.assertEqual(decision.photo_prompt, "")

    def test_desktop_host_can_return_local_artifact(self) -> None:
        caps = capabilities(HostClass.DESKTOP, image=True, attachment=True)

        decision = self.decide("/companion-photo 公园抓拍", caps)

        self.assertEqual(decision.kind, DecisionKind.PHOTO_PLAN)
        self.assertEqual(decision.delivery_mode, "local_artifact")

    def test_reference_ids_are_separate_from_provider_prompt(self) -> None:
        profile = replace(
            PROFILE,
            visual=replace(PROFILE.visual, reference_ids=("demo-front-v1",)),
        )
        kernel = CompanionKernel(profile)
        caps = capabilities(HostClass.DESKTOP, image=True, attachment=True)

        decision = kernel.decide(
            RequestEnvelope(text="照片：公园抓拍", session_id="test", source="test"),
            caps,
        )

        self.assertEqual(decision.reference_ids, ("demo-front-v1",))
        self.assertNotIn("demo-front-v1", decision.photo_prompt)

    def test_missing_generator_fails_closed(self) -> None:
        caps = capabilities(HostClass.DESKTOP, image=False, attachment=True)

        decision = self.decide("照片：咖啡馆随手拍", caps)

        self.assertEqual(decision.kind, DecisionKind.BLOCKED)
        self.assertEqual(decision.reason, "image_generation_unavailable")

    def test_photo_job_transition_order(self) -> None:
        stage = transition_photo_job(PhotoJobStage.PLANNED, PhotoJobStage.AUTHORIZED)
        stage = transition_photo_job(stage, PhotoJobStage.GENERATING)
        stage = transition_photo_job(stage, PhotoJobStage.GENERATED)
        stage = transition_photo_job(stage, PhotoJobStage.DELIVERED)
        self.assertEqual(stage, PhotoJobStage.DELIVERED)

        with self.assertRaises(InvalidTransition):
            transition_photo_job(PhotoJobStage.PLANNED, PhotoJobStage.DELIVERED)

        cancelled = transition_photo_job(
            PhotoJobStage.AUTHORIZED,
            PhotoJobStage.CANCELLED,
        )
        self.assertEqual(cancelled, PhotoJobStage.CANCELLED)
        with self.assertRaises(InvalidTransition):
            transition_photo_job(cancelled, PhotoJobStage.GENERATED)

        generated_cancelled = transition_photo_job(
            PhotoJobStage.GENERATED,
            PhotoJobStage.CANCELLED,
        )
        self.assertEqual(generated_cancelled, PhotoJobStage.CANCELLED)


if __name__ == "__main__":
    unittest.main()
