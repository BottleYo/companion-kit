from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from companion_kit.codex_photo import CodexPhotoWorkflow, PhotoWorkflowError
from companion_kit.contracts import HostCapabilities, HostClass, RequestEnvelope
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
        return ImageApiResult(tiny_png(), "req_prototype")

    def edit(self, prompt: str, reference_png: bytes) -> ImageApiResult:
        self.calls.append(("edit", prompt))
        self.reference = reference_png
        return ImageApiResult(tiny_png(metadata=b"result-metadata"), "req_photo")


def photo_decision(snapshot, text: str):
    return CompanionKernel(snapshot.profile).decide(
        RequestEnvelope(text=text, session_id="test", source="codex"),
        HostCapabilities(
            host_class=HostClass.DESKTOP,
            can_execute_tasks=True,
            can_generate_images=True,
            can_deliver_images=False,
            has_current_reply_target=False,
            can_attach_local_artifacts=True,
        ),
    )


class CodexPhotoWorkflowTests(unittest.TestCase):
    def test_prototype_confirmation_then_reference_edit_and_delivery_cleanup(self) -> None:
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
            assets = ImageAssetStore(root / "assets")
            client = FakeImageClient()
            workflow = CodexPhotoWorkflow(
                authorizations=PhotoAuthorizationStore(root / "authorizations"),
                assets=assets,
                client=client,
            )
            task_scope = "codex-current-task"
            prototype_decision = photo_decision(
                snapshot,
                "照片：自然光下的中性正面身份参考照",
            )
            plan = workflow.prepare(
                snapshot=snapshot,
                decision=prototype_decision,
                purpose="prototype",
                task_scope=task_scope,
            )

            with self.assertRaises(PhotoWorkflowError):
                workflow.run(
                    snapshot=snapshot,
                    decision=prototype_decision,
                    purpose="prototype",
                    task_scope=task_scope,
                    plan_id=plan.plan_id,
                    confirmed=False,
                )
            self.assertEqual(client.calls, [])
            prototype = workflow.run(
                snapshot=snapshot,
                decision=prototype_decision,
                purpose="prototype",
                task_scope=task_scope,
                plan_id=plan.plan_id,
                confirmed=True,
            )
            self.assertEqual(prototype.stage, "generated")
            self.assertTrue(prototype.artifact_path.is_file())
            self.assertEqual([call[0] for call in client.calls], ["generation"])
            with self.assertRaises(PhotoWorkflowError):
                workflow.run(
                    snapshot=snapshot,
                    decision=prototype_decision,
                    purpose="prototype",
                    task_scope=task_scope,
                    plan_id=plan.plan_id,
                    confirmed=True,
                )
            self.assertEqual(len(client.calls), 1)

            selected, bound = workflow.confirm_identity(
                profile_store=profile_store,
                snapshot=snapshot,
                candidate_id=prototype.candidate_id,
                task_scope=task_scope,
            )
            self.assertEqual(bound.profile.visual.reference_ids, (selected.reference_id,))

            decision = photo_decision(bound, "照片：雨后街角轻松散步")
            photo_plan = workflow.prepare(
                snapshot=bound,
                decision=decision,
                purpose="photo",
                task_scope=task_scope,
            )
            photo = workflow.run(
                snapshot=bound,
                decision=decision,
                purpose="photo",
                task_scope=task_scope,
                plan_id=photo_plan.plan_id,
                confirmed=True,
            )

            self.assertEqual([call[0] for call in client.calls], ["generation", "edit"])
            self.assertTrue(photo.artifact_path.is_file())
            workflow.finish_delivery(
                artifact_id=photo.artifact_id,
                task_scope=task_scope,
            )
            self.assertFalse(photo.artifact_path.exists())


if __name__ == "__main__":
    unittest.main()
