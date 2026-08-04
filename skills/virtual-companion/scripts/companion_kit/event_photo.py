from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from .contracts import Decision, DecisionKind
from .event_adapter import EventAdapterError, format_current_reply_handoff
from .event_job_store import (
    EventJob,
    EventJobContext,
    EventJobError,
    EventJobShape,
    EventJobStore,
)
from .image_assets import ImageAssetError, ImageAssetStore, ReferenceAsset
from .openai_image_api import ImageApiError, ImageApiResult
from .photo_authorization import (
    AuthorizationError,
    PhotoAuthorizationStore,
)
from .profile_store import (
    ProfileConflict,
    ProfileSnapshot,
    ProfileStore,
    ProfileStoreError,
)


class EventPhotoError(ValueError):
    """OpenClaw/Hermes 当前会话的严格图片流程无法继续。"""


class ImageClient(Protocol):
    @property
    def auth_ready(self) -> bool: ...

    def generate(self, prompt: str) -> ImageApiResult: ...

    def edit(self, prompt: str, reference_png: bytes) -> ImageApiResult: ...


@dataclass(frozen=True)
class EventPreparedPhoto:
    host: str
    job_id: str
    plan_id: str
    purpose: str
    operation: str
    expires_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": "planned",
            "host": self.host,
            "job_id": self.job_id,
            "plan_id": self.plan_id,
            "purpose": self.purpose,
            "operation": self.operation,
            "expires_at": self.expires_at,
            "confirmation_required": True,
            "confirmation": (
                "确认后只调用一次 OpenAI Image API；会按 API 用量计费，"
                "并发送本次图片提示。已有固定形象时还会发送一张参考图。"
                "本阶段只处理虚构成年人，不模仿真人。"
            ),
        }


@dataclass(frozen=True)
class EventPhotoRunResult:
    host: str
    stage: str
    job_id: str
    purpose: str
    asset_id: str
    artifact_path: Path
    profile_version: str
    candidate_id: str | None = None
    artifact_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        # 绝对路径只允许在 claim_handoff 的最后宿主边界展开。
        return {
            "host": self.host,
            "stage": self.stage,
            "job_id": self.job_id,
            "purpose": self.purpose,
            "asset_id": self.asset_id,
            "candidate_id": self.candidate_id,
            "artifact_id": self.artifact_id,
            "profile_version": self.profile_version,
            "next_action": "交给当前会话附件机制；不要发送到任意其他目标",
        }


def _scope_digest(*parts: str) -> str:
    normalized: list[str] = []
    for value in parts:
        text = str(value or "")
        if (
            not text
            or len(text) > 512
            or any(ord(character) < 32 for character in text)
        ):
            raise EventPhotoError("当前宿主会话作用域无效")
        normalized.append(text)
    return "event_" + sha256("\0".join(normalized).encode("utf-8")).hexdigest()


class EventPhotoWorkflow:
    """事件型宿主严格模式；只把安全成图交回当前会话。"""

    def __init__(
        self,
        *,
        host: str,
        authorizations: PhotoAuthorizationStore,
        assets: ImageAssetStore,
        jobs: EventJobStore,
        client: ImageClient,
    ) -> None:
        if host not in {"openclaw", "hermes"}:
            raise EventPhotoError("事件宿主只能是 openclaw 或 hermes")
        self.host = host
        self.authorizations = authorizations
        self.assets = assets
        self.jobs = jobs
        self.client = client

    def _assert_context(self, context: EventJobContext) -> None:
        if context.host != self.host:
            raise EventPhotoError("图片作业不属于当前宿主")

    def _authorization_scope(self, context: EventJobContext) -> str:
        self._assert_context(context)
        return _scope_digest(
            self.host,
            context.instance_scope,
            context.conversation_scope,
        )

    def _asset_scope(self, context: EventJobContext) -> str:
        self._assert_context(context)
        return _scope_digest(
            self.host,
            context.instance_scope,
            context.conversation_scope,
        )

    def _shape(
        self,
        *,
        snapshot: ProfileSnapshot,
        decision: Decision,
        purpose: str,
    ) -> tuple[str, str | None, EventJobShape]:
        if (
            decision.kind is not DecisionKind.PHOTO_PLAN
            or decision.delivery_mode != "current_reply"
            or not decision.photo_prompt.strip()
        ):
            raise EventPhotoError("当前输入不是可执行的事件宿主照片计划")
        if tuple(decision.reference_ids) != snapshot.profile.visual.reference_ids:
            raise EventPhotoError("照片计划与当前宿主人格配置不一致")
        references = snapshot.profile.visual.reference_ids
        if purpose == "prototype":
            if references:
                raise EventPhotoError("当前宿主身份已经固定；不要静默覆盖参考图")
            operation = "generation"
            reference_id = None
        elif purpose == "photo":
            if len(references) != 1:
                raise EventPhotoError("请先为当前宿主生成并确认一张人物原型")
            operation = "edit"
            reference_id = references[0]
            self.assets.resolve_reference(
                reference_id=reference_id,
                profile_id=snapshot.profile.id,
                identity_version=snapshot.profile.visual.identity_version,
            )
        else:
            raise EventPhotoError("图片目的只能是 prototype 或 photo")
        return (
            operation,
            reference_id,
            EventJobShape(
                profile_id=snapshot.profile.id,
                profile_version=snapshot.version,
                identity_version=snapshot.profile.visual.identity_version,
                prompt=decision.photo_prompt,
                reference_id=reference_id,
                purpose=purpose,
                operation=operation,
            ),
        )

    def prepare(
        self,
        *,
        snapshot: ProfileSnapshot,
        decision: Decision,
        purpose: str,
        context: EventJobContext,
    ) -> EventPreparedPhoto:
        self._assert_context(context)
        if not self.client.auth_ready:
            raise EventPhotoError(
                f"{self.host} 严格模式尚未配置 OPENAI_API_KEY；未创建付费授权"
            )
        try:
            operation, reference_id, shape = self._shape(
                snapshot=snapshot,
                decision=decision,
                purpose=purpose,
            )
            plan = self.authorizations.create(
                task_scope=self._authorization_scope(context),
                profile_id=snapshot.profile.id,
                profile_version=snapshot.version,
                identity_version=snapshot.profile.visual.identity_version,
                prompt=decision.photo_prompt,
                reference_id=reference_id,
                purpose=purpose,
                operation=operation,
            )
            try:
                job = self.jobs.create(
                    context=context,
                    shape=shape,
                    authorization_plan_id=plan.plan_id,
                )
            except EventJobError:
                self.authorizations.discard(plan.plan_id)
                raise
            return EventPreparedPhoto(
                host=self.host,
                job_id=job.job_id,
                plan_id=plan.plan_id,
                purpose=purpose,
                operation=operation,
                expires_at=plan.expires_at.astimezone(UTC).isoformat(),
            )
        except (AuthorizationError, EventJobError, ImageAssetError) as exc:
            raise EventPhotoError(str(exc)) from exc

    def run(
        self,
        *,
        snapshot: ProfileSnapshot,
        decision: Decision,
        purpose: str,
        context: EventJobContext,
        job_id: str,
        plan_id: str,
        confirmed: bool,
    ) -> EventPhotoRunResult:
        self._assert_context(context)
        if confirmed is not True:
            raise EventPhotoError("尚未确认本次 API 计费、数据外发和虚构成年人约束")
        if not self.client.auth_ready:
            raise EventPhotoError("严格模式的 OPENAI_API_KEY 当前不可用")
        claimed = False
        try:
            operation, reference_id, shape = self._shape(
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
            self.jobs.claim_generation(
                job_id=job_id,
                context=context,
                shape=shape,
                authorization_plan_id=plan_id,
            )
            claimed = True
            self.authorizations.consume(
                plan_id=plan_id,
                task_scope=self._authorization_scope(context),
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
                    raise EventPhotoError("当前宿主身份参考图不存在")
                api_result = self.client.edit(decision.photo_prompt, reference_png)

            asset_scope = self._asset_scope(context)
            if purpose == "prototype":
                candidate = self.assets.store_candidate(
                    profile_id=snapshot.profile.id,
                    identity_version=snapshot.profile.visual.identity_version,
                    image_bytes=api_result.image_bytes,
                    task_scope=asset_scope,
                )
                self.jobs.mark_generated(
                    job_id=job_id,
                    context=context,
                    asset_id=candidate.candidate_id,
                )
                return EventPhotoRunResult(
                    host=self.host,
                    stage="generated",
                    job_id=job_id,
                    purpose=purpose,
                    asset_id=candidate.candidate_id,
                    candidate_id=candidate.candidate_id,
                    artifact_id=None,
                    artifact_path=candidate.path,
                    profile_version=snapshot.version,
                )
            artifact = self.assets.store_artifact(
                profile_id=snapshot.profile.id,
                identity_version=snapshot.profile.visual.identity_version,
                image_bytes=api_result.image_bytes,
                task_scope=asset_scope,
            )
            self.jobs.mark_generated(
                job_id=job_id,
                context=context,
                asset_id=artifact.artifact_id,
            )
            return EventPhotoRunResult(
                host=self.host,
                stage="generated",
                job_id=job_id,
                purpose=purpose,
                asset_id=artifact.artifact_id,
                candidate_id=None,
                artifact_id=artifact.artifact_id,
                artifact_path=artifact.path,
                profile_version=snapshot.version,
            )
        except EventPhotoError:
            if claimed:
                self._mark_failed_quietly(job_id=job_id, context=context)
            raise
        except (
            AuthorizationError,
            EventJobError,
            ImageAssetError,
            ImageApiError,
        ) as exc:
            if claimed:
                self._mark_failed_quietly(job_id=job_id, context=context)
            raise EventPhotoError(str(exc)) from exc

    def _mark_failed_quietly(
        self,
        *,
        job_id: str,
        context: EventJobContext,
    ) -> None:
        try:
            self.jobs.mark_failed(job_id=job_id, context=context)
        except EventJobError:
            pass

    def _job_for_asset(
        self,
        *,
        job_id: str,
        asset_id: str,
    ) -> EventJob:
        job = self.jobs.read(job_id)
        if job.host != self.host or job.asset_id != asset_id:
            raise EventPhotoError("事件作业与当前图片资产不一致")
        return job

    def claim_handoff(
        self,
        *,
        job_id: str,
        asset_id: str,
        context: EventJobContext,
    ) -> dict[str, object]:
        self._assert_context(context)
        snapshot_artifact_id: str | None = None
        handoff_claimed = False
        try:
            job = self._job_for_asset(
                job_id=job_id,
                asset_id=asset_id,
            )
            asset_scope = self._asset_scope(context)
            if job.purpose == "prototype":
                # 候选槽会被下一张原型覆盖。先在资产锁内复制已校验内容，
                # 再保存到唯一的短期目录，宿主拿到的路径便不会随候选变化。
                candidate_png = self.assets.read_candidate_bytes(
                    candidate_id=asset_id,
                    profile_id=job.profile_id,
                    identity_version=job.identity_version,
                    task_scope=asset_scope,
                )
                snapshot = self.assets.store_artifact(
                    profile_id=job.profile_id,
                    identity_version=job.identity_version,
                    image_bytes=candidate_png,
                    task_scope=asset_scope,
                )
                snapshot_artifact_id = snapshot.artifact_id
                handoff_artifact_id = snapshot.artifact_id
                path = snapshot.path
            else:
                handoff_artifact_id = asset_id
                path = self.assets.resolve_artifact(
                    artifact_id=asset_id,
                    profile_id=job.profile_id,
                    identity_version=job.identity_version,
                    task_scope=asset_scope,
                )

            # 先确认适配器能接管该唯一文件，再占用只能执行一次的 handoff。
            format_current_reply_handoff(host=self.host, artifact_path=path)
            self.jobs.claim_handoff(
                job_id=job_id,
                context=context,
                asset_id=asset_id,
                handoff_artifact_id=handoff_artifact_id,
            )
            handoff_claimed = True

            # 取得一次性 claim 后重新校验实际交付快照。该路径此后不会被候选槽覆盖。
            path = self.assets.resolve_artifact(
                artifact_id=handoff_artifact_id,
                profile_id=job.profile_id,
                identity_version=job.identity_version,
                task_scope=asset_scope,
            )
            handoff = format_current_reply_handoff(
                host=self.host,
                artifact_path=path,
            )
            return {
                "stage": "delivery_unknown",
                "job_id": job_id,
                "asset_id": asset_id,
                **handoff,
                "next_action": (
                    "只交给当前会话；没有明确宿主回执时不要声称已经发送"
                ),
            }
        except (EventAdapterError, EventJobError, ImageAssetError) as exc:
            if snapshot_artifact_id is not None and not handoff_claimed:
                try:
                    self.assets.finish_delivery(
                        artifact_id=snapshot_artifact_id,
                        task_scope=self._asset_scope(context),
                    )
                except ImageAssetError:
                    pass
            raise EventPhotoError(str(exc)) from exc

    def mark_delivered(
        self,
        *,
        job_id: str,
        asset_id: str,
        context: EventJobContext,
        receipt_confirmed: bool,
    ) -> None:
        self._assert_context(context)
        if receipt_confirmed is not True:
            raise EventPhotoError("宿主没有返回明确的当前会话媒体接管回执")
        try:
            job = self.jobs.mark_delivered(
                job_id=job_id,
                context=context,
                asset_id=asset_id,
            )
            if job.handoff_artifact_id is None:
                raise EventPhotoError("事件作业缺少交付快照")
            self.assets.finish_delivery(
                artifact_id=job.handoff_artifact_id,
                task_scope=self._asset_scope(context),
            )
        except (EventJobError, ImageAssetError) as exc:
            raise EventPhotoError(str(exc)) from exc

    def confirm_identity(
        self,
        *,
        profile_store: ProfileStore,
        snapshot: ProfileSnapshot,
        candidate_id: str,
        context: EventJobContext,
    ) -> tuple[ReferenceAsset, ProfileSnapshot]:
        self._assert_context(context)
        try:
            current = profile_store.read()
            if current is None or current.version != snapshot.version:
                raise ProfileConflict("当前宿主人格配置已变化，请重新检查候选原型")
            reference = self.assets.confirm_candidate(
                candidate_id=candidate_id,
                profile_id=snapshot.profile.id,
                identity_version=snapshot.profile.visual.identity_version,
                task_scope=self._asset_scope(context),
            )
            bound = profile_store.bind_reference(
                reference_id=reference.reference_id,
                identity_version=snapshot.profile.visual.identity_version,
                expected_version=snapshot.version,
            )
            return reference, bound
        except (ImageAssetError, ProfileStoreError) as exc:
            raise EventPhotoError(str(exc)) from exc
