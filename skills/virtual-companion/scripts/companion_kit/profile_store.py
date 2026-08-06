from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import os
from pathlib import Path
import re
from threading import Lock
from typing import Mapping

from .config import ConfigError, PersonaProfile, load_profile
from .file_lock import InterprocessLockError, exclusive_file_lock
from .initializer import (
    BUILTIN_TEMPLATES,
    InitializationError,
    default_profile_path,
    load_template_profile,
    normalize_display_name,
    safe_profile_path,
    save_profile_document,
)
from .persona_draft import PersonaDraft, PersonaDraftError, compose_persona_draft
from .relationship import RelationshipError, RelationshipPolicy


class ProfileStoreError(ValueError):
    """本地人格存储不可安全读取或写入。"""


class ProfileConflict(ProfileStoreError):
    """人格配置已变化，需要刷新后再保存。"""


_REFERENCE_ID_RE = re.compile(r"^ref_[a-f0-9]{16,32}$")


@dataclass(frozen=True)
class TemplatePreview:
    id: str
    name: str
    description: str
    recommended: bool
    default_name: str
    traits: tuple[str, ...]
    speaking_style: str
    appearance: str
    default_style: str
    background: str
    task_style: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "recommended": self.recommended,
            "default_name": self.default_name,
            "traits": list(self.traits),
            "speaking_style": self.speaking_style,
            "appearance": self.appearance,
            "default_style": self.default_style,
            "background": self.background,
            "task_style": self.task_style,
        }


@dataclass(frozen=True)
class ProfileSnapshot:
    profile: PersonaProfile
    version: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.profile.schema_version,
            "id": self.profile.id,
            "display_name": self.profile.display_name,
            "template_id": self.profile.template_id,
            "intent_summary": self.profile.intent_summary,
            "traits": list(self.profile.traits),
            "speaking_style": self.profile.speaking_style,
            "boundaries": list(self.profile.boundaries),
            "background": self.profile.background,
            "values": list(self.profile.values),
            "interests": list(self.profile.interests),
            "task_style": self.profile.task_style,
            "visual": {
                "identity_anchor": self.profile.visual.identity_anchor,
                "identity_version": self.profile.visual.identity_version,
                "identity_status": self.profile.visual.identity_status,
                "appearance": self.profile.visual.appearance,
                "default_style": self.profile.visual.default_style,
                "default_hairstyle": self.profile.visual.default_hairstyle,
                "default_expression": self.profile.visual.default_expression,
                "default_makeup": self.profile.visual.default_makeup,
                "default_wardrobe": self.profile.visual.default_wardrobe,
                "reference_count": len(self.profile.visual.reference_ids),
            },
            "appearance": {
                "direction": self.profile.visual.appearance,
                "default_style": self.profile.visual.default_style,
                "default_hairstyle": self.profile.visual.default_hairstyle,
                "default_expression": self.profile.visual.default_expression,
                "default_makeup": self.profile.visual.default_makeup,
                "default_wardrobe": self.profile.visual.default_wardrobe,
            },
            "visual_identity": {
                "status": self.profile.visual.identity_status,
                "identity_version": self.profile.visual.identity_version,
                "reference_count": len(self.profile.visual.reference_ids),
            },
            "relationship": {
                "starting_mode": self.profile.relationship.starting_mode,
                "romance_enabled": self.profile.relationship.romance_enabled,
            },
            "provenance": {
                "user_fields": list(self.profile.provenance.user_fields),
                "generated_fields": list(self.profile.provenance.generated_fields),
            },
            "version": self.version,
        }


class ProfileStore:
    def __init__(
        self,
        *,
        skill_root: str | Path,
        profile_path: str | Path | None = None,
    ) -> None:
        self.skill_root = Path(skill_root).resolve()
        raw_path = profile_path if profile_path is not None else default_profile_path()
        try:
            self.profile_path = safe_profile_path(raw_path)
        except InitializationError as exc:
            raise ProfileStoreError(str(exc)) from exc
        self._write_lock = Lock()
        self._lock_path = self.profile_path.with_name(
            f".{self.profile_path.name}.lock"
        )

    def templates(self) -> tuple[TemplatePreview, ...]:
        previews: list[TemplatePreview] = []
        for template in BUILTIN_TEMPLATES:
            try:
                profile = load_template_profile(self.skill_root, template)
            except InitializationError as exc:
                raise ProfileStoreError(str(exc)) from exc
            previews.append(
                TemplatePreview(
                    id=template.id,
                    name=template.name,
                    description=template.description,
                    recommended=template.recommended,
                    default_name=profile.display_name,
                    traits=profile.traits,
                    speaking_style=profile.speaking_style,
                    appearance=profile.visual.appearance,
                    default_style=profile.visual.default_style,
                    background=profile.background,
                    task_style=profile.task_style,
                )
            )
        return tuple(previews)

    def preview_draft(
        self,
        *,
        template_id: str | None = None,
        description: str | None = None,
        display_name: str | None = None,
        overrides: Mapping[str, object] | None = None,
    ) -> PersonaDraft:
        current = self.read()
        try:
            return compose_persona_draft(
                skill_root=self.skill_root,
                template_id=template_id,
                description=description,
                display_name=display_name,
                overrides=overrides,
                current=current.profile if current is not None else None,
            )
        except PersonaDraftError as exc:
            raise ProfileStoreError(str(exc)) from exc

    def read(self) -> ProfileSnapshot | None:
        try:
            path = safe_profile_path(self.profile_path)
        except InitializationError as exc:
            raise ProfileStoreError(str(exc)) from exc
        if not path.exists():
            return None
        if not path.is_file():
            raise ProfileStoreError("人格配置目标必须是普通文件")

        try:
            before = path.read_bytes()
            profile = load_profile(path)
            after = path.read_bytes()
        except (OSError, ConfigError) as exc:
            raise ProfileStoreError(str(exc)) from exc
        if before != after:
            raise ProfileConflict("人格配置刚刚发生变化，请刷新后重试")
        return ProfileSnapshot(profile=profile, version=sha256(after).hexdigest())

    def save(
        self,
        *,
        template_id: str,
        display_name: str | None,
        expected_version: str | None,
        starting_mode: str | None = None,
        romance_enabled: bool | None = None,
    ) -> ProfileSnapshot:
        with self._write_lock:
            try:
                safe_profile_path(self.profile_path)
                self.profile_path.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                    mode=0o700,
                )
                if os.name != "nt":
                    self.profile_path.parent.chmod(0o700)
                safe_profile_path(self.profile_path)
                safe_profile_path(self._lock_path)
                with exclusive_file_lock(self._lock_path):
                    return self._save_locked(
                        template_id=template_id,
                        display_name=display_name,
                        expected_version=expected_version,
                        starting_mode=starting_mode,
                        romance_enabled=romance_enabled,
                    )
            except (OSError, InitializationError, InterprocessLockError) as exc:
                raise ProfileStoreError(str(exc)) from exc

    def save_draft(
        self,
        *,
        expected_version: str | None,
        template_id: str | None = None,
        description: str | None = None,
        display_name: str | None = None,
        overrides: Mapping[str, object] | None = None,
        starting_mode: str | None = None,
        romance_enabled: bool | None = None,
    ) -> ProfileSnapshot:
        with self._write_lock:
            try:
                safe_profile_path(self.profile_path)
                self.profile_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if os.name != "nt":
                    self.profile_path.parent.chmod(0o700)
                safe_profile_path(self.profile_path)
                safe_profile_path(self._lock_path)
                with exclusive_file_lock(self._lock_path):
                    return self._save_draft_locked(
                        expected_version=expected_version,
                        template_id=template_id,
                        description=description,
                        display_name=display_name,
                        overrides=overrides,
                        starting_mode=starting_mode,
                        romance_enabled=romance_enabled,
                    )
            except (OSError, InitializationError, InterprocessLockError) as exc:
                raise ProfileStoreError(str(exc)) from exc

    def bind_reference(
        self,
        *,
        reference_id: str,
        identity_version: int,
        expected_version: str,
    ) -> ProfileSnapshot:
        """把已确认的私有资产绑定为当前身份版本的唯一主脸参考。"""

        if not _REFERENCE_ID_RE.fullmatch(str(reference_id or "")):
            raise ProfileStoreError("reference_id 格式无效")
        if (
            not isinstance(identity_version, int)
            or isinstance(identity_version, bool)
            or identity_version < 1
        ):
            raise ProfileStoreError("identity_version 必须是正整数")
        with self._write_lock:
            try:
                self.profile_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if os.name != "nt":
                    self.profile_path.parent.chmod(0o700)
                safe_profile_path(self.profile_path)
                safe_profile_path(self._lock_path)
                with exclusive_file_lock(self._lock_path):
                    current = self.read()
                    if current is None:
                        raise ProfileConflict("人格配置不存在，请先完成初始化")
                    if current.version != expected_version:
                        raise ProfileConflict("人格配置已变化，请重新确认候选原型")
                    if current.profile.visual.identity_version != identity_version:
                        raise ProfileConflict("身份版本已变化，请重新生成候选原型")
                    updated = replace(
                        current.profile,
                        visual=replace(
                            current.profile.visual,
                            identity_status="locked",
                            identity_anchor=(
                                current.profile.visual.identity_anchor
                                or "脸部身份以用户已确认的唯一主脸参考为准"
                            ),
                            reference_ids=(reference_id,),
                        ),
                    )
                    save_profile_document(
                        profile=updated,
                        output=self.profile_path,
                        force=True,
                        private_parent=True,
                    )
                    saved = self.read()
                    if saved is None:
                        raise ProfileStoreError("人格配置保存后未找到")
                    return saved
            except (OSError, InitializationError, InterprocessLockError) as exc:
                raise ProfileStoreError(str(exc)) from exc

    def _save_locked(
        self,
        *,
        template_id: str,
        display_name: str | None,
        expected_version: str | None,
        starting_mode: str | None,
        romance_enabled: bool | None,
    ) -> ProfileSnapshot:
        current = self.read()
        if (
            current is not None
            and template_id in {current.profile.id, current.profile.template_id}
        ):
            return self._save_existing_name_locked(
                current=current,
                display_name=display_name,
                expected_version=expected_version,
                starting_mode=starting_mode,
                romance_enabled=romance_enabled,
            )
        return self._save_draft_locked(
            expected_version=expected_version,
            template_id=template_id,
            display_name=display_name,
            starting_mode=starting_mode,
            romance_enabled=romance_enabled,
        )

    def _check_version(
        self,
        current: ProfileSnapshot | None,
        expected_version: str | None,
    ) -> None:
        if current is None and expected_version:
            raise ProfileConflict("人格配置已被删除，请刷新后重试")
        if current is not None and expected_version != current.version:
            raise ProfileConflict("人格配置已存在或已变化，请刷新后再保存")

    def _relationship(
        self,
        current: ProfileSnapshot | None,
        starting_mode: str | None,
        romance_enabled: bool | None,
    ) -> RelationshipPolicy:
        current_policy = (
            current.profile.relationship if current is not None else RelationshipPolicy()
        )
        try:
            return RelationshipPolicy(
                starting_mode=starting_mode or current_policy.starting_mode,
                romance_enabled=(
                    current_policy.romance_enabled
                    if romance_enabled is None
                    else romance_enabled
                ),
            )
        except RelationshipError as exc:
            raise ProfileStoreError(str(exc)) from exc

    def _save_existing_name_locked(
        self,
        *,
        current: ProfileSnapshot,
        display_name: str | None,
        expected_version: str | None,
        starting_mode: str | None,
        romance_enabled: bool | None,
    ) -> ProfileSnapshot:
        self._check_version(current, expected_version)
        relationship = self._relationship(current, starting_mode, romance_enabled)
        try:
            updated = replace(
                current.profile,
                schema_version=3,
                display_name=normalize_display_name(
                    display_name,
                    current.profile.display_name,
                ),
                relationship=relationship,
            )
            save_profile_document(
                profile=updated,
                output=self.profile_path,
                force=True,
                private_parent=True,
            )
        except (OSError, InitializationError) as exc:
            raise ProfileStoreError(str(exc)) from exc
        saved = self.read()
        if saved is None:
            raise ProfileStoreError("人格配置保存后未找到")
        return saved

    def _save_draft_locked(
        self,
        *,
        expected_version: str | None,
        template_id: str | None = None,
        description: str | None = None,
        display_name: str | None = None,
        overrides: Mapping[str, object] | None = None,
        starting_mode: str | None = None,
        romance_enabled: bool | None = None,
    ) -> ProfileSnapshot:
        current = self.read()
        self._check_version(current, expected_version)
        relationship = self._relationship(current, starting_mode, romance_enabled)
        try:
            draft = compose_persona_draft(
                skill_root=self.skill_root,
                template_id=template_id,
                description=description,
                display_name=display_name,
                overrides=overrides,
                current=current.profile if current is not None else None,
            )
            profile = replace(draft.profile, relationship=relationship)
            save_profile_document(
                profile=profile,
                output=self.profile_path,
                force=current is not None,
                private_parent=True,
            )
        except (OSError, InitializationError, PersonaDraftError) as exc:
            raise ProfileStoreError(str(exc)) from exc
        saved = self.read()
        if saved is None:
            raise ProfileStoreError("人格配置保存后未找到")
        return saved
