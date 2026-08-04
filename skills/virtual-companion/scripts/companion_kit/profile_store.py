from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import os
from pathlib import Path
from threading import Lock

from .config import ConfigError, PersonaProfile, load_profile
from .file_lock import InterprocessLockError, exclusive_file_lock
from .initializer import (
    BUILTIN_TEMPLATES,
    InitializationError,
    default_profile_path,
    initialize_profile,
    load_template_profile,
    normalize_display_name,
    safe_profile_path,
    save_profile_document,
)
from .relationship import RelationshipError, RelationshipPolicy


class ProfileStoreError(ValueError):
    """本地人格存储不可安全读取或写入。"""


class ProfileConflict(ProfileStoreError):
    """人格配置已变化，需要刷新后再保存。"""


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
            "traits": list(self.profile.traits),
            "speaking_style": self.profile.speaking_style,
            "boundaries": list(self.profile.boundaries),
            "visual": {
                "identity_anchor": self.profile.visual.identity_anchor,
                "identity_version": self.profile.visual.identity_version,
                "appearance": self.profile.visual.appearance,
                "default_style": self.profile.visual.default_style,
                "reference_count": len(self.profile.visual.reference_ids),
            },
            "relationship": {
                "starting_mode": self.profile.relationship.starting_mode,
                "romance_enabled": self.profile.relationship.romance_enabled,
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
                )
            )
        return tuple(previews)

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
        if current is None and expected_version:
            raise ProfileConflict("人格配置已被删除，请刷新后重试")
        if current is not None and expected_version != current.version:
            raise ProfileConflict("人格配置已存在或已变化，请刷新后再保存")

        try:
            current_policy = (
                current.profile.relationship if current is not None else RelationshipPolicy()
            )
            relationship = RelationshipPolicy(
                starting_mode=starting_mode or current_policy.starting_mode,
                romance_enabled=(
                    current_policy.romance_enabled
                    if romance_enabled is None
                    else romance_enabled
                ),
            )
            self.profile_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name != "nt":
                self.profile_path.parent.chmod(0o700)
            if current is not None and current.profile.id == template_id:
                updated_profile = replace(
                    current.profile,
                    display_name=normalize_display_name(
                        display_name,
                        current.profile.display_name,
                    ),
                    relationship=relationship,
                )
                save_profile_document(
                    profile=updated_profile,
                    output=self.profile_path,
                    force=True,
                    private_parent=True,
                )
            else:
                initialize_profile(
                    skill_root=self.skill_root,
                    template_id=template_id,
                    display_name=display_name,
                    starting_mode=relationship.starting_mode,
                    romance_enabled=relationship.romance_enabled,
                    output=self.profile_path,
                    force=current is not None,
                )
        except (OSError, InitializationError, RelationshipError) as exc:
            raise ProfileStoreError(str(exc)) from exc

        saved = self.read()
        if saved is None:
            raise ProfileStoreError("人格配置保存后未找到")
        return saved
