from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from companion_kit.contracts import HostCapabilities, HostClass, RequestEnvelope
from companion_kit.event_job_store import EventJobContext, EventJobStore
from companion_kit.event_photo import EventPhotoError, EventPhotoWorkflow
from companion_kit.image_assets import ImageAssetStore
from companion_kit.kernel import CompanionKernel
from companion_kit.openai_image_api import ImageApiResult
from companion_kit.photo_authorization import PhotoAuthorizationStore
from companion_kit.profile_store import ProfileStore
from tests.png_fixture import tiny_png


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "virtual-companion"


class FakeImageClient:
    auth_ready = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def generate(self, prompt: str) -> ImageApiResult:
        self.calls.append(("generation", prompt))
        channel = min(len(self.calls), 255)
        return ImageApiResult(
            tiny_png(rgba=bytes((channel, 64, 96, 255))),
            "req_event_prototype",
        )

    def edit(self, prompt: str, reference_png: bytes) -> ImageApiResult:
        self.calls.append(("edit", prompt))
        self.reference_png = reference_png
        return ImageApiResult(tiny_png(metadata=b"must-disappear"), "req_event_photo")


def event_decision(snapshot, *, host: str, text: str):
    return CompanionKernel(snapshot.profile).decide(
        RequestEnvelope(text=text, session_id="opaque-event-session", source=host),
        HostCapabilities(
            host_class=HostClass.EVENT,
            can_execute_tasks=True,
            can_generate_images=True,
            can_deliver_images=True,
            has_current_reply_target=True,
            can_attach_local_artifacts=False,
        ),
    )


class EventPhotoWorkflowTests(unittest.TestCase):
    def _workflow(self, root: Path, host: str, client: FakeImageClient):
        return EventPhotoWorkflow(
            host=host,
            authorizations=PhotoAuthorizationStore(
                root / "authorizations",
                route_id=f"{host}:openai-direct",
            ),
            assets=ImageAssetStore(root / "assets"),
            jobs=EventJobStore(root / "jobs"),
            client=client,
        )

    def test_strict_event_flow_is_single_use_and_current_reply_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            profile_store = ProfileStore(
                skill_root=SKILL_ROOT,
                profile_path=root / "profiles" / "default.toml",
            )
            snapshot = profile_store.save(
                template_id="warm_healer",
                display_name="小禾",
                expected_version=None,
            )
            client = FakeImageClient()
            workflow = self._workflow(root, "hermes", client)
            context = EventJobContext(
                host="hermes",
                instance_scope="hermes-local-instance",
                conversation_scope="current-chat-scope",
                request_event_id="request-event-001",
            )
            decision = event_decision(
                snapshot,
                host="hermes",
                text="照片：自然光下的中性身份参考图",
            )

            prepared = workflow.prepare(
                snapshot=snapshot,
                decision=decision,
                purpose="prototype",
                context=context,
            )
            with self.assertRaisesRegex(EventPhotoError, "已经处理"):
                workflow.prepare(
                    snapshot=snapshot,
                    decision=decision,
                    purpose="prototype",
                    context=context,
                )
            self.assertEqual(
                len(list((root / "authorizations").glob("plan_*.json"))),
                1,
            )

            with self.assertRaises(EventPhotoError):
                workflow.run(
                    snapshot=snapshot,
                    decision=decision,
                    purpose="prototype",
                    context=context,
                    job_id=prepared.job_id,
                    plan_id=prepared.plan_id,
                    confirmed=False,
                )
            self.assertEqual(client.calls, [])

            confirmation_context = EventJobContext(
                host=context.host,
                instance_scope=context.instance_scope,
                conversation_scope=context.conversation_scope,
                request_event_id="request-event-confirmation-001",
            )
            generated = workflow.run(
                snapshot=snapshot,
                decision=decision,
                purpose="prototype",
                context=confirmation_context,
                job_id=prepared.job_id,
                plan_id=prepared.plan_id,
                confirmed=True,
            )
            self.assertEqual(generated.stage, "generated")
            self.assertTrue(generated.artifact_path.is_file())
            self.assertEqual([call[0] for call in client.calls], ["generation"])

            handoff = workflow.claim_handoff(
                job_id=generated.job_id,
                asset_id=generated.asset_id,
                context=confirmation_context,
            )
            self.assertEqual(handoff["stage"], "delivery_unknown")
            self.assertEqual(handoff["transport"], "current_response_media")
            self.assertTrue(handoff["response_directive"].startswith("MEDIA:"))
            self.assertNotIn("target", handoff)
            prototype_handoff_path = Path(
                handoff["response_directive"].removeprefix("MEDIA:")
            )
            self.assertNotEqual(prototype_handoff_path, generated.artifact_path)
            self.assertTrue(prototype_handoff_path.is_file())
            with self.assertRaisesRegex(EventPhotoError, "不能重复"):
                workflow.claim_handoff(
                    job_id=generated.job_id,
                    asset_id=generated.asset_id,
                    context=confirmation_context,
                )
            workflow.mark_delivered(
                job_id=generated.job_id,
                asset_id=generated.asset_id,
                context=confirmation_context,
                receipt_confirmed=True,
            )
            self.assertFalse(prototype_handoff_path.exists())
            self.assertTrue(generated.artifact_path.is_file())

            selected, bound = workflow.confirm_identity(
                profile_store=profile_store,
                snapshot=snapshot,
                candidate_id=generated.candidate_id,
                context=confirmation_context,
            )
            self.assertEqual(bound.profile.visual.reference_ids, (selected.reference_id,))

            photo_context = EventJobContext(
                host="hermes",
                instance_scope=context.instance_scope,
                conversation_scope=context.conversation_scope,
                request_event_id="request-event-002",
            )
            photo_decision = event_decision(
                bound,
                host="hermes",
                text="照片：雨后沿街散步",
            )
            photo_plan = workflow.prepare(
                snapshot=bound,
                decision=photo_decision,
                purpose="photo",
                context=photo_context,
            )
            photo = workflow.run(
                snapshot=bound,
                decision=photo_decision,
                purpose="photo",
                context=photo_context,
                job_id=photo_plan.job_id,
                plan_id=photo_plan.plan_id,
                confirmed=True,
            )
            self.assertEqual([call[0] for call in client.calls], ["generation", "edit"])
            self.assertNotIn(b"must-disappear", photo.artifact_path.read_bytes())

            workflow.claim_handoff(
                job_id=photo.job_id,
                asset_id=photo.asset_id,
                context=photo_context,
            )
            workflow.mark_delivered(
                job_id=photo.job_id,
                asset_id=photo.asset_id,
                context=photo_context,
                receipt_confirmed=True,
            )
            self.assertFalse(photo.artifact_path.exists())

    def test_hosts_cannot_reuse_each_others_job_or_asset_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            profile_store = ProfileStore(
                skill_root=SKILL_ROOT,
                profile_path=root / "hermes" / "profiles" / "default.toml",
            )
            snapshot = profile_store.save(
                template_id="calm_partner",
                display_name="阿序",
                expected_version=None,
            )
            hermes = self._workflow(root / "hermes", "hermes", FakeImageClient())
            context = EventJobContext(
                host="hermes",
                instance_scope="instance-one",
                conversation_scope="conversation-one",
                request_event_id="event-one",
            )
            decision = event_decision(
                snapshot,
                host="hermes",
                text="照片：书店里随手翻书",
            )
            prepared = hermes.prepare(
                snapshot=snapshot,
                decision=decision,
                purpose="prototype",
                context=context,
            )
            generated = hermes.run(
                snapshot=snapshot,
                decision=decision,
                purpose="prototype",
                context=context,
                job_id=prepared.job_id,
                plan_id=prepared.plan_id,
                confirmed=True,
            )

            openclaw = self._workflow(
                root / "openclaw",
                "openclaw",
                FakeImageClient(),
            )
            wrong_context = EventJobContext(
                host="openclaw",
                instance_scope=context.instance_scope,
                conversation_scope=context.conversation_scope,
                request_event_id=context.request_event_id,
            )
            with self.assertRaises(EventPhotoError):
                openclaw.claim_handoff(
                    job_id=generated.job_id,
                    asset_id=generated.asset_id,
                    context=wrong_context,
                )

            self.assertTrue(generated.artifact_path.is_file())
            self.assertFalse((root / "openclaw" / "assets").exists())

    def test_prototype_handoff_snapshot_cannot_be_overwritten_by_another_chat(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            profile_store = ProfileStore(
                skill_root=SKILL_ROOT,
                profile_path=root / "profiles" / "default.toml",
            )
            snapshot = profile_store.save(
                template_id="calm_partner",
                display_name="阿序",
                expected_version=None,
            )
            workflow = self._workflow(root, "hermes", FakeImageClient())

            first_context = EventJobContext(
                host="hermes",
                instance_scope="same-hermes-instance",
                conversation_scope="conversation-a",
                request_event_id="event-a",
            )
            first_decision = event_decision(
                snapshot,
                host="hermes",
                text="照片：窗边的中性身份参考图",
            )
            first_plan = workflow.prepare(
                snapshot=snapshot,
                decision=first_decision,
                purpose="prototype",
                context=first_context,
            )
            first = workflow.run(
                snapshot=snapshot,
                decision=first_decision,
                purpose="prototype",
                context=first_context,
                job_id=first_plan.job_id,
                plan_id=first_plan.plan_id,
                confirmed=True,
            )
            first_handoff = workflow.claim_handoff(
                job_id=first.job_id,
                asset_id=first.asset_id,
                context=first_context,
            )
            first_handoff_path = Path(
                first_handoff["response_directive"].removeprefix("MEDIA:")
            )
            first_handoff_bytes = first_handoff_path.read_bytes()

            second_context = EventJobContext(
                host="hermes",
                instance_scope="same-hermes-instance",
                conversation_scope="conversation-b",
                request_event_id="event-b",
            )
            second_decision = event_decision(
                snapshot,
                host="hermes",
                text="照片：户外的中性身份参考图",
            )
            second_plan = workflow.prepare(
                snapshot=snapshot,
                decision=second_decision,
                purpose="prototype",
                context=second_context,
            )
            second = workflow.run(
                snapshot=snapshot,
                decision=second_decision,
                purpose="prototype",
                context=second_context,
                job_id=second_plan.job_id,
                plan_id=second_plan.plan_id,
                confirmed=True,
            )

            self.assertNotEqual(first_handoff_path, second.artifact_path)
            self.assertEqual(first_handoff_path.read_bytes(), first_handoff_bytes)
            self.assertNotEqual(
                first_handoff_path.read_bytes(),
                second.artifact_path.read_bytes(),
            )

            workflow.mark_delivered(
                job_id=first.job_id,
                asset_id=first.asset_id,
                context=first_context,
                receipt_confirmed=True,
            )
            self.assertFalse(first_handoff_path.exists())
            self.assertTrue(second.artifact_path.is_file())


if __name__ == "__main__":
    unittest.main()
