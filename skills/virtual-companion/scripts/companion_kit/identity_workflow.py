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
