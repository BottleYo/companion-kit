from __future__ import annotations

from .identity_pack import PRIMARY_FACE
from .image_assets import ImageAssetStore, ReferenceAsset
from .profile_store import (
    ProfileSnapshot,
    ProfileStore,
    ProfileStoreError,
)


class IdentityConfirmationRetry(ProfileStoreError):
    def __init__(self, message: str, *, retry_profile_version: str) -> None:
        super().__init__(message)
        self.retry_profile_version = retry_profile_version


def confirm_identity_candidate(
    *,
    assets: ImageAssetStore,
    profile_store: ProfileStore,
    candidate_id: str,
    task_scope: str,
    expected_profile_version: str,
) -> tuple[ReferenceAsset, ProfileSnapshot]:
    """确认候选并把主脸绑定到 Persona，失败时尽量恢复候选。"""

    snapshot = profile_store.read()
    if snapshot is None:
        raise ProfileStoreError("人格配置不存在，请先完成初始化")
    if snapshot.version != expected_profile_version:
        raise IdentityConfirmationRetry(
            "Codex Persona 已变化，请重新确认候选原型",
            retry_profile_version=snapshot.version,
        )

    reference = assets.confirm_candidate(
        candidate_id=candidate_id,
        profile_id=snapshot.profile.id,
        identity_version=snapshot.profile.visual.identity_version,
        task_scope=task_scope,
        retain_candidate=True,
    )
    if reference.role == PRIMARY_FACE:
        try:
            bound = profile_store.bind_reference(
                reference_id=reference.reference_id,
                identity_version=snapshot.profile.visual.identity_version,
                expected_version=snapshot.version,
            )
        except ProfileStoreError as bind_error:
            latest = None
            latest_read_succeeded = False
            try:
                latest = profile_store.read()
                latest_read_succeeded = True
            except ProfileStoreError:
                pass
            if (
                latest is not None
                and latest.profile.visual.identity_version
                == snapshot.profile.visual.identity_version
                and latest.profile.visual.reference_ids
                == (reference.reference_id,)
            ):
                bound = latest
            elif latest_read_succeeded:
                assets.rollback_primary_confirmation(
                    candidate_id=candidate_id,
                    reference_id=reference.reference_id,
                    profile_id=snapshot.profile.id,
                    identity_version=snapshot.profile.visual.identity_version,
                    task_scope=task_scope,
                )
                if latest is not None:
                    raise IdentityConfirmationRetry(
                        str(bind_error),
                        retry_profile_version=latest.version,
                    ) from bind_error
                raise
            else:
                raise
    else:
        if not snapshot.profile.visual.is_locked:
            raise ProfileStoreError("增强参考不能在主脸确认前启用")
        bound = snapshot

    assets.discard_candidate(
        candidate_id=candidate_id,
        profile_id=snapshot.profile.id,
        identity_version=snapshot.profile.visual.identity_version,
        task_scope=task_scope,
    )
    return reference, bound


def confirm_primary_candidate(
    *,
    assets: ImageAssetStore,
    profile_store: ProfileStore,
    candidate_id: str,
    task_scope: str,
    expected_profile_version: str,
) -> tuple[ReferenceAsset, ProfileSnapshot]:
    """确认面板主脸候选；已有主脸时创建下一身份版本而非覆盖。"""

    snapshot = profile_store.read()
    if snapshot is None:
        raise ProfileStoreError("人格配置不存在，请先完成初始化")
    if snapshot.version != expected_profile_version:
        raise IdentityConfirmationRetry(
            "Codex Persona 已变化，请重新确认候选原型",
            retry_profile_version=snapshot.version,
        )
    if not snapshot.profile.visual.is_locked:
        return confirm_identity_candidate(
            assets=assets,
            profile_store=profile_store,
            candidate_id=candidate_id,
            task_scope=task_scope,
            expected_profile_version=expected_profile_version,
        )

    current_identity_version = snapshot.profile.visual.identity_version
    next_identity_version = current_identity_version + 1
    current_reference_id = snapshot.profile.visual.reference_ids[0]
    reference = assets.confirm_candidate(
        candidate_id=candidate_id,
        profile_id=snapshot.profile.id,
        identity_version=next_identity_version,
        task_scope=task_scope,
        retain_candidate=True,
    )
    if reference.role != PRIMARY_FACE:
        raise ProfileStoreError("更换主脸只能确认主脸候选")
    if reference.reference_id == current_reference_id:
        assets.rollback_primary_confirmation(
            candidate_id=candidate_id,
            reference_id=reference.reference_id,
            profile_id=snapshot.profile.id,
            identity_version=next_identity_version,
            task_scope=task_scope,
        )
        raise ProfileStoreError("这张图片与当前主脸相同，无需更换")

    try:
        bound = profile_store.rotate_reference(
            reference_id=reference.reference_id,
            current_reference_id=current_reference_id,
            current_identity_version=current_identity_version,
            next_identity_version=next_identity_version,
            expected_version=snapshot.version,
        )
    except ProfileStoreError as bind_error:
        latest = None
        latest_read_succeeded = False
        try:
            latest = profile_store.read()
            latest_read_succeeded = True
        except ProfileStoreError:
            pass
        if (
            latest is not None
            and latest.profile.visual.identity_version == next_identity_version
            and latest.profile.visual.reference_ids == (reference.reference_id,)
        ):
            bound = latest
        elif latest_read_succeeded and (
            latest is None
            or (
                latest.profile.id == snapshot.profile.id
                and latest.profile.visual.identity_version == current_identity_version
                and latest.profile.visual.reference_ids == (current_reference_id,)
            )
        ):
            assets.rollback_primary_confirmation(
                candidate_id=candidate_id,
                reference_id=reference.reference_id,
                profile_id=snapshot.profile.id,
                identity_version=next_identity_version,
                task_scope=task_scope,
            )
            if latest is not None:
                raise IdentityConfirmationRetry(
                    str(bind_error),
                    retry_profile_version=latest.version,
                ) from bind_error
            raise
        else:
            # 无法证明新版本未绑定时保留 Pack 与候选，避免破坏性回滚。
            raise

    assets.discard_candidate(
        candidate_id=candidate_id,
        profile_id=snapshot.profile.id,
        identity_version=next_identity_version,
        task_scope=task_scope,
    )
    return reference, bound
