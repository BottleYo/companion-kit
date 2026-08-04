from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .contracts import Decision, DecisionKind
from .image_assets import (
    ImageAssetError,
    ImageAssetStore,
    ReferenceAsset,
)
from .openai_image_api import ImageApiError, ImageApiResult
from .photo_authorization import (
    AuthorizationError,
    PhotoAuthorizationPlan,
    PhotoAuthorizationStore,
)
from .profile_store import (
    ProfileConflict,
    ProfileSnapshot,
    ProfileStore,
    ProfileStoreError,
)


class PhotoWorkflowError(ValueError):
    """Codex 当前任务的严格图片流程无法继续。"""


class ImageClient(Protocol):
    @property
    def auth_ready(self) -> bool: ...

    def generate(self, prompt: str) -> ImageApiResult: ...

    def edit(self, prompt: str, reference_png: bytes) -> ImageApiResult: ...


@dataclass(frozen=True)
class PhotoRunResult:
    stage: str
    purpose: str
    artifact_path: Path
    profile_version: str
    candidate_id: str | None = None
    artifact_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "purpose": self.purpose,
            "candidate_id": self.candidate_id,
            "artifact_id": self.artifact_id,
            "artifact_path": str(self.artifact_path),
            "profile_version": self.profile_version,
            "next_action": (
                "请只在当前 Codex 任务展示候选图；用户确认后再固定身份"
                if self.purpose == "prototype"
                else "请只附加到当前 Codex 任务；附加成功后立即清理临时成图"
            ),
        }


class CodexPhotoWorkflow:
    """严格模式编排；Kernel、资产、授权和 API 仍保持独立边界。"""

    def __init__(
        self,
        *,
        authorizations: PhotoAuthorizationStore,
        assets: ImageAssetStore,
        client: ImageClient,
    ) -> None:
        self.authorizations = authorizations
        self.assets = assets
        self.client = client

    def _shape(
        self,
        *,
        snapshot: ProfileSnapshot,
        decision: Decision,
        purpose: str,
    ) -> tuple[str, str | None]:
        if (
            decision.kind is not DecisionKind.PHOTO_PLAN
            or decision.delivery_mode != "local_artifact"
            or not decision.photo_prompt.strip()
        ):
            raise PhotoWorkflowError("当前输入不是可执行的 Codex 照片计划")
        if tuple(decision.reference_ids) != snapshot.profile.visual.reference_ids:
            raise PhotoWorkflowError("照片计划与当前人格配置不一致")
        references = snapshot.profile.visual.reference_ids
        if purpose == "prototype":
            if references:
                raise PhotoWorkflowError("当前身份版本已经固定；不要静默覆盖参考图")
            return "generation", None
        if purpose == "photo":
            if len(references) != 1:
                raise PhotoWorkflowError("请先生成并确认一张固定人物原型")
            reference_id = references[0]
            self.assets.resolve_reference(
                reference_id=reference_id,
                profile_id=snapshot.profile.id,
                identity_version=snapshot.profile.visual.identity_version,
            )
            return "edit", reference_id
        raise PhotoWorkflowError("图片目的只能是 prototype 或 photo")

    def prepare(
        self,
        *,
        snapshot: ProfileSnapshot,
        decision: Decision,
        purpose: str,
        task_scope: str,
    ) -> PhotoAuthorizationPlan:
        if not self.client.auth_ready:
            raise PhotoWorkflowError(
                "严格模式尚未配置 OPENAI_API_KEY；未创建付费授权"
            )
        try:
            operation, reference_id = self._shape(
                snapshot=snapshot,
                decision=decision,
                purpose=purpose,
            )
            return self.authorizations.create(
                task_scope=task_scope,
                profile_id=snapshot.profile.id,
                profile_version=snapshot.version,
                identity_version=snapshot.profile.visual.identity_version,
                prompt=decision.photo_prompt,
                reference_id=reference_id,
                purpose=purpose,
                operation=operation,
            )
        except (AuthorizationError, ImageAssetError) as exc:
            raise PhotoWorkflowError(str(exc)) from exc

    def run(
        self,
        *,
        snapshot: ProfileSnapshot,
        decision: Decision,
        purpose: str,
        task_scope: str,
        plan_id: str,
        confirmed: bool,
    ) -> PhotoRunResult:
        if confirmed is not True:
            raise PhotoWorkflowError("尚未确认本次 API 计费、数据外发和虚构成年人约束")
        if not self.client.auth_ready:
            raise PhotoWorkflowError("严格模式的 OPENAI_API_KEY 当前不可用")
        try:
            operation, reference_id = self._shape(
                snapshot=snapshot,
                decision=decision,
                purpose=purpose,
            )
            reference_png = None
            if reference_id is not None:
                reference_png = self.assets.read_reference_bytes(
                    reference_id=reference_id,
                    profile_id=snapshot.profile.id,
                    identity_version=snapshot.profile.visual.identity_version,
                )
            self.authorizations.consume(
                plan_id=plan_id,
                task_scope=task_scope,
                profile_id=snapshot.profile.id,
                profile_version=snapshot.version,
                identity_version=snapshot.profile.visual.identity_version,
                prompt=decision.photo_prompt,
                reference_id=reference_id,
                purpose=purpose,
                operation=operation,
            )
            if operation == "generation":
                api_result = self.client.generate(decision.photo_prompt)
            else:
                if reference_png is None:
                    raise PhotoWorkflowError("身份参考图不存在")
                api_result = self.client.edit(decision.photo_prompt, reference_png)

            if purpose == "prototype":
                candidate = self.assets.store_candidate(
                    profile_id=snapshot.profile.id,
                    identity_version=snapshot.profile.visual.identity_version,
                    image_bytes=api_result.image_bytes,
                    task_scope=task_scope,
                )
                return PhotoRunResult(
                    stage="generated",
                    purpose=purpose,
                    artifact_path=candidate.path,
                    profile_version=snapshot.version,
                    candidate_id=candidate.candidate_id,
                )
            artifact = self.assets.store_artifact(
                profile_id=snapshot.profile.id,
                identity_version=snapshot.profile.visual.identity_version,
                image_bytes=api_result.image_bytes,
                task_scope=task_scope,
            )
            return PhotoRunResult(
                stage="generated",
                purpose=purpose,
                artifact_path=artifact.path,
                profile_version=snapshot.version,
                artifact_id=artifact.artifact_id,
            )
        except PhotoWorkflowError:
            raise
        except (AuthorizationError, ImageAssetError, ImageApiError) as exc:
            raise PhotoWorkflowError(str(exc)) from exc

    def confirm_identity(
        self,
        *,
        profile_store: ProfileStore,
        snapshot: ProfileSnapshot,
        candidate_id: str,
        task_scope: str,
    ) -> tuple[ReferenceAsset, ProfileSnapshot]:
        try:
            current = profile_store.read()
            if current is None or current.version != snapshot.version:
                raise ProfileConflict("人格配置已变化，请重新检查候选原型")
            reference = self.assets.confirm_candidate(
                candidate_id=candidate_id,
                profile_id=snapshot.profile.id,
                identity_version=snapshot.profile.visual.identity_version,
                task_scope=task_scope,
            )
            bound = profile_store.bind_reference(
                reference_id=reference.reference_id,
                identity_version=snapshot.profile.visual.identity_version,
                expected_version=snapshot.version,
            )
            return reference, bound
        except (ImageAssetError, ProfileStoreError) as exc:
            raise PhotoWorkflowError(str(exc)) from exc

    def finish_delivery(self, *, artifact_id: str, task_scope: str) -> None:
        try:
            self.assets.finish_delivery(
                artifact_id=artifact_id,
                task_scope=task_scope,
            )
        except ImageAssetError as exc:
            raise PhotoWorkflowError(str(exc)) from exc
