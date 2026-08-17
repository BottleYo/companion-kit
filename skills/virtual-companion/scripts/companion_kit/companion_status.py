from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Mapping

from . import __version__
from .backup import CompanionDataLayout
from .companion_scope import CompanionScopeError, CompanionScopeStore
from .hook_health import COMPANION_CONTEXT, HookHealthStore
from .image_assets import ImageAssetError, ImageAssetStore
from .initializer import default_profile_path
from .profile_store import ProfileStore, ProfileStoreError


def companion_context_loaded(
    hook_health: Mapping[str, object],
    *,
    material_modified_at: float | None,
) -> bool:
    companion = hook_health.get(COMPANION_CONTEXT)
    if not isinstance(companion, dict) or companion.get("verified") is not True:
        return False
    if material_modified_at is None:
        return True
    last_success_at = companion.get("last_success_at")
    if not isinstance(last_success_at, str):
        return False
    try:
        loaded_at = datetime.fromisoformat(last_success_at)
    except ValueError:
        return False
    if loaded_at.tzinfo is None:
        return False
    return loaded_at.timestamp() >= material_modified_at


def build_runtime_readiness(
    *,
    plugin: Mapping[str, object],
    persona_configured: bool,
    identity_pack: Mapping[str, object],
    hook_health: Mapping[str, object],
    material_modified_at: float | None,
    companion_task_count: int,
) -> dict[str, object]:
    session = hook_health.get("session_start")
    review = hook_health.get("review")
    post_tool = hook_health.get("post_tool_use")
    companion_context = hook_health.get(COMPANION_CONTEXT)
    session_verified = bool(
        isinstance(session, dict) and session.get("verified") is True
    )
    review_acknowledged = bool(
        isinstance(review, dict) and review.get("acknowledged") is True
    )
    post_tool_verified = bool(
        isinstance(post_tool, dict) and post_tool.get("verified") is True
    )
    companion_context_verified = bool(
        isinstance(companion_context, dict)
        and companion_context.get("verified") is True
    )
    session_loaded = companion_context_loaded(
        hook_health,
        material_modified_at=material_modified_at,
    )
    reference_saved = identity_pack.get("ready") is True

    ready = False
    if plugin.get("known") is not True:
        state = "installation_unknown"
        summary = "暂时无法核对 Companion Kit Plugin 安装状态"
        detail = "请确认 Codex 可以正常运行，再刷新人物面板。"
    elif plugin.get("installed") is not True:
        state = "uninstalled"
        summary = "Companion Kit Plugin 尚未安装"
        detail = "先安装 Plugin；安装成功不等于 Hooks 已经可以运行。"
    elif plugin.get("enabled") is not True:
        state = "plugin_disabled"
        summary = "Companion Kit Plugin 已安装，但目前处于停用状态"
        detail = "请先在 Codex 的 Plugin 管理界面启用它。"
    elif plugin.get("current") is not True:
        state = "update_required"
        summary = "Plugin 版本已经变化，需要先更新"
        detail = "更新不会覆盖 Persona、关系数据或参考图；更新后需要重新审核 Hooks。"
    elif not session_verified:
        if review_acknowledged:
            state = "verification_pending"
            summary = (
                "参考图已保存，但新任务尚未验证 Hooks"
                if reference_saved
                else "Hooks 已审核，等待新任务运行验证"
            )
            detail = "新建一个 Codex 任务；SessionStart 真正运行后，状态会自动更新。"
        else:
            state = "review_required"
            summary = (
                "参考图已保存，但 Hooks 尚未审核"
                if reference_saved
                else "Plugin 已安装，等待你审核 Hooks"
            )
            detail = "在 Codex 输入 /hooks，逐项审核 Companion Kit 的四个 Hooks。"
    elif not persona_configured:
        state = "persona_required"
        summary = "Hooks 已验证，尚未保存 Persona"
        detail = "先在人物面板创建 Persona；普通 Codex 任务不会受到影响。"
    elif companion_task_count <= 0:
        state = "companion_task_required"
        summary = "Plugin 与 Hooks 已就绪，尚未连接陪伴任务"
        detail = "只在想陪伴聊天的任务里说“把这个任务设为陪伴任务”。"
    elif not companion_context_verified or not session_loaded:
        state = "verification_pending"
        summary = (
            "参考图已保存，但陪伴任务尚未加载"
            if reference_saved
            else "陪伴任务尚未加载当前 Persona"
        )
        detail = "回到已连接的陪伴任务继续一轮，或新建任务后重新连接。"
    elif identity_pack.get("level") == "unset":
        state = "primary_face_required"
        summary = "Persona 已成功加载，尚未固定主脸"
        detail = "可以先聊天和解决问题；要固定人物照片时再上传或生成候选。"
    elif not reference_saved:
        state = "identity_unavailable"
        summary = "参考图已保存，但当前无法安全加载"
        detail = "无需重新上传；先检查现有 Identity Pack 的健康状态。"
    else:
        state = "ready"
        ready = True
        summary = "Persona 已加载，主脸参考已就绪"
        detail = "人物照片回合会按需使用已确认的身份参考；普通任务仍保持原来的 Codex 体验。"

    return {
        "state": state,
        "ready": ready,
        "summary": summary,
        "detail": detail,
        "plugin_installed": plugin.get("installed") is True,
        "plugin_enabled": plugin.get("enabled") is True,
        "plugin_current": plugin.get("current") is True,
        "persona_configured": persona_configured,
        "reference_saved": reference_saved,
        "session_hook_verified": session_verified,
        "companion_context_verified": companion_context_verified,
        "companion_task_count": companion_task_count,
        "session_loaded": session_loaded,
        "post_tool_verified": post_tool_verified,
    }


class CodexCompanionStatusService:
    """为 Plugin UI 提供不含路径、任务标识和 Persona 正文的状态快照。"""

    def __init__(
        self,
        *,
        plugin_root: str | Path,
        skill_root: str | Path,
        profile_path: str | Path | None = None,
    ) -> None:
        self.plugin_root = Path(plugin_root).resolve()
        self.skill_root = Path(skill_root).resolve()
        self.profile_path = Path(profile_path or default_profile_path("codex")).resolve()
        self.layout = CompanionDataLayout.for_profile(self.profile_path)

    def snapshot(self) -> dict[str, object]:
        store = ProfileStore(skill_root=self.skill_root, profile_path=self.profile_path)
        try:
            profile_snapshot = store.read()
            profile_error = False
        except ProfileStoreError:
            profile_snapshot = None
            profile_error = True

        material_modified_at: float | None = None
        if profile_snapshot is not None:
            try:
                material_modified_at = self.profile_path.stat().st_mtime
            except OSError:
                material_modified_at = None

        identity_pack: dict[str, object] = {
            "level": "unset",
            "ready": False,
            "count": 0,
        }
        if profile_snapshot is not None and profile_snapshot.profile.visual.is_locked:
            try:
                pack = ImageAssetStore(self.layout.images_root).resolve_identity_pack(
                    primary_reference_id=profile_snapshot.profile.visual.reference_ids[0],
                    profile_id=profile_snapshot.profile.id,
                    identity_version=profile_snapshot.profile.visual.identity_version,
                )
                modified = [member.path.stat().st_mtime for member in pack.members]
                if modified:
                    material_modified_at = max([material_modified_at or 0.0, *modified])
                identity_pack = {
                    "level": "enhanced" if len(pack.members) > 1 else "basic",
                    "ready": True,
                    "count": len(pack.members),
                }
            except (ImageAssetError, OSError):
                identity_pack = {
                    "level": "unavailable",
                    "ready": False,
                    "count": 0,
                }

        hook_health = HookHealthStore(
            root=self.layout.system_root / "hook-health",
            plugin_root=self.plugin_root,
        ).snapshot()
        try:
            companion_task_count = CompanionScopeStore(
                root=self.layout.system_root / "codex-scopes"
            ).count()
            scope_error = False
        except CompanionScopeError:
            companion_task_count = 0
            scope_error = True

        readiness = build_runtime_readiness(
            plugin={
                "known": True,
                "installed": True,
                "enabled": True,
                "current": True,
            },
            persona_configured=profile_snapshot is not None,
            identity_pack=identity_pack,
            hook_health=hook_health,
            material_modified_at=material_modified_at,
            companion_task_count=companion_task_count,
        )
        return {
            "version": __version__,
            "persona": {
                "configured": profile_snapshot is not None,
                "healthy": not profile_error,
            },
            "identity": identity_pack,
            "hooks": {
                "session_verified": readiness["session_hook_verified"],
                "companion_context_verified": readiness[
                    "companion_context_verified"
                ],
                "photo_receipt_verified": readiness["post_tool_verified"],
            },
            "scope": {
                "active_task_count": companion_task_count,
                "healthy": not scope_error,
            },
            "readiness": {
                "state": readiness["state"],
                "ready": readiness["ready"],
                "summary": readiness["summary"],
                "detail": readiness["detail"],
            },
        }
