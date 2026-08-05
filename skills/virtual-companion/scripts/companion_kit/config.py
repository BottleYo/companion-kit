from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib
from typing import Any

from .public_bundle import contains_absolute_path
from .relationship import RelationshipError, RelationshipPolicy


class ConfigError(ValueError):
    """人格配置不满足公开、安全的 schema。"""


@dataclass(frozen=True)
class VisualProfile:
    identity_anchor: str
    identity_version: int
    appearance: str
    default_style: str
    reference_ids: tuple[str, ...]
    identity_status: str = "unset"
    default_hairstyle: str = "根据场景和用户要求自然变化"
    default_expression: str = "根据当下情绪自然变化"
    default_makeup: str = "根据场景和用户要求自然变化"
    default_wardrobe: str = "根据场景和用户要求自然变化"

    @property
    def is_locked(self) -> bool:
        return self.identity_status == "locked" and len(self.reference_ids) == 1


@dataclass(frozen=True)
class PersonaProvenance:
    user_fields: tuple[str, ...] = ()
    generated_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class PersonaProfile:
    schema_version: int
    id: str
    display_name: str
    traits: tuple[str, ...]
    speaking_style: str
    boundaries: tuple[str, ...]
    visual: VisualProfile
    relationship: RelationshipPolicy
    template_id: str = "custom"
    intent_summary: str = "沿用现有人格设定"
    background: str = "虚构成年人物，不声称拥有未记录的现实身份或共同经历。"
    values: tuple[str, ...] = ()
    interests: tuple[str, ...] = ()
    task_style: str = "具体任务优先准确、完整地完成。"
    provenance: PersonaProvenance = PersonaProvenance()


_ROOT_KEYS_V1 = {"schema_version", "id", "display_name", "persona", "visual"}
_ROOT_KEYS_V2 = _ROOT_KEYS_V1 | {"relationship"}
_ROOT_KEYS_V3 = {
    "schema_version",
    "id",
    "display_name",
    "template_id",
    "intent_summary",
    "persona",
    "appearance",
    "visual_identity",
    "relationship",
    "provenance",
}
_PERSONA_KEYS = {"traits", "speaking_style", "boundaries"}
_PERSONA_KEYS_V3 = _PERSONA_KEYS | {
    "background",
    "values",
    "interests",
    "task_style",
}
_VISUAL_KEYS_V1 = {"identity_anchor", "appearance", "default_style", "reference_ids"}
_VISUAL_KEYS_V2 = _VISUAL_KEYS_V1 | {"identity_version"}
_APPEARANCE_KEYS_V3 = {
    "direction",
    "default_style",
    "default_hairstyle",
    "default_expression",
    "default_makeup",
    "default_wardrobe",
}
_VISUAL_IDENTITY_KEYS_V3 = {
    "status",
    "facial_anchor",
    "identity_version",
    "reference_ids",
}
_RELATIONSHIP_KEYS = {"starting_mode", "romance_enabled"}
_PROVENANCE_KEYS = {"user_fields", "generated_fields"}
_SENSITIVE_KEY_PARTS = {
    "api_key",
    "apikey",
    "token",
    "password",
    "secret",
    "credential",
    "credentials",
    "chat_history",
    "conversation_history",
    "messages",
    "memory",
}
_PATH_KEY_PARTS = {
    "reference_path",
    "reference_paths",
    "photo_path",
    "image_path",
    "file_path",
}
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_REFERENCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
_IDENTITY_STATUSES = {"unset", "locked"}


def _scan_keys(value: Any, prefix: str = "") -> None:
    if not isinstance(value, dict):
        return
    for raw_key, child in value.items():
        key = str(raw_key).strip().lower()
        location = f"{prefix}.{key}" if prefix else key
        if key in _SENSITIVE_KEY_PARTS:
            raise ConfigError(f"人格配置不得包含敏感字段：{location}")
        if key in _PATH_KEY_PARTS or key.endswith("_path") or key.endswith("_paths"):
            raise ConfigError(f"人格配置不得包含本地路径：{location}")
        _scan_keys(child, location)


def _expect_keys(section: dict[str, Any], allowed: set[str], label: str) -> None:
    unexpected = sorted(set(section) - allowed)
    if unexpected:
        raise ConfigError(f"{label} 包含未知字段：{', '.join(unexpected)}")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{label} 必须是字符串")
    result = value.strip()
    if not result:
        raise ConfigError(f"{label} 不能为空")
    if contains_absolute_path(result):
        raise ConfigError(f"{label} 不得包含本机路径")
    return result


def _optional_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{label} 必须是字符串")
    result = value.strip()
    if contains_absolute_path(result):
        raise ConfigError(f"{label} 不得包含本机路径")
    return result


def _text_list(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"{label} 必须是字符串数组")
    result = tuple(item.strip() for item in value if item.strip())
    if len(result) != len(value):
        raise ConfigError(f"{label} 不能包含空值")
    if any(contains_absolute_path(item) for item in result):
        raise ConfigError(f"{label} 不得包含本机路径")
    return result


def _positive_int(value: Any, label: str, *, default: int | None = None) -> int:
    if value is None and default is not None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigError(f"{label} 必须是正整数")
    return value


def _boolean(value: Any, label: str, *, default: bool | None = None) -> bool:
    if value is None and default is not None:
        return default
    if not isinstance(value, bool):
        raise ConfigError(f"{label} 必须是布尔值")
    return value


def load_profile(path: str | Path) -> PersonaProfile:
    config_path = Path(path)
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"无法读取人格配置：{exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("人格配置根节点必须是对象")
    _scan_keys(raw)
    schema_version = raw.get("schema_version")
    if schema_version not in {1, 2, 3}:
        raise ConfigError("仅支持 schema_version = 1、2 或 3")
    _expect_keys(
        raw,
        (
            _ROOT_KEYS_V1
            if schema_version == 1
            else _ROOT_KEYS_V2
            if schema_version == 2
            else _ROOT_KEYS_V3
        ),
        "根配置",
    )

    profile_id = _text(raw.get("id"), "id")
    if not _ID_RE.fullmatch(profile_id):
        raise ConfigError("id 必须使用小写字母、数字、下划线或连字符")

    persona = raw.get("persona")
    if not isinstance(persona, dict):
        raise ConfigError("必须提供 [persona]")
    _expect_keys(
        persona,
        _PERSONA_KEYS_V3 if schema_version == 3 else _PERSONA_KEYS,
        "persona",
    )

    if schema_version == 3:
        appearance_raw = raw.get("appearance")
        identity_raw = raw.get("visual_identity")
        provenance_raw = raw.get("provenance")
        if not isinstance(appearance_raw, dict) or not isinstance(identity_raw, dict):
            raise ConfigError("schema_version = 3 必须提供 [appearance] 与 [visual_identity]")
        if not isinstance(provenance_raw, dict):
            raise ConfigError("schema_version = 3 必须提供 [provenance]")
        _expect_keys(appearance_raw, _APPEARANCE_KEYS_V3, "appearance")
        _expect_keys(identity_raw, _VISUAL_IDENTITY_KEYS_V3, "visual_identity")
        _expect_keys(provenance_raw, _PROVENANCE_KEYS, "provenance")
        visual = identity_raw
    else:
        visual = raw.get("visual")
        if not isinstance(visual, dict):
            raise ConfigError("必须提供 [visual]")
        _expect_keys(
            visual,
            _VISUAL_KEYS_V1 if schema_version == 1 else _VISUAL_KEYS_V2,
            "visual",
        )
        appearance_raw = visual
        provenance_raw = {}

    relationship_raw = raw.get("relationship")
    if schema_version in {2, 3}:
        if not isinstance(relationship_raw, dict):
            raise ConfigError(f"schema_version = {schema_version} 必须提供 [relationship]")
        _expect_keys(relationship_raw, _RELATIONSHIP_KEYS, "relationship")
    else:
        relationship_raw = {}
    try:
        relationship = RelationshipPolicy(
            starting_mode=_text(
                relationship_raw.get("starting_mode", "natural"),
                "relationship.starting_mode",
            ),
            romance_enabled=_boolean(
                relationship_raw.get("romance_enabled"),
                "relationship.romance_enabled",
                default=False,
            ),
        )
    except RelationshipError as exc:
        raise ConfigError(str(exc)) from exc

    reference_label = (
        "visual_identity.reference_ids" if schema_version == 3 else "visual.reference_ids"
    )
    reference_ids = _text_list(visual.get("reference_ids", []), reference_label)
    for reference_id in reference_ids:
        if not _REFERENCE_ID_RE.fullmatch(reference_id):
            raise ConfigError("reference_ids 只能使用不透明标识，不能使用文件路径")

    if schema_version == 3:
        identity_status = _text(visual.get("status"), "visual_identity.status")
        if identity_status not in _IDENTITY_STATUSES:
            raise ConfigError("visual_identity.status 只能是 unset 或 locked")
        identity_anchor = _optional_text(
            visual.get("facial_anchor", ""),
            "visual_identity.facial_anchor",
        )
        if identity_status == "unset" and reference_ids:
            raise ConfigError("未固定身份时不能绑定参考图")
        if identity_status == "locked" and len(reference_ids) != 1:
            raise ConfigError("固定身份必须绑定恰好一张已确认参考图")
        template_id = _text(raw.get("template_id"), "template_id")
        if not _ID_RE.fullmatch(template_id):
            raise ConfigError("template_id 必须使用小写字母、数字、下划线或连字符")
        user_fields = _text_list(
            provenance_raw.get("user_fields", []),
            "provenance.user_fields",
        )
        generated_fields = _text_list(
            provenance_raw.get("generated_fields", []),
            "provenance.generated_fields",
        )
        if any(
            not _FIELD_NAME_RE.fullmatch(field)
            for field in (*user_fields, *generated_fields)
        ):
            raise ConfigError("provenance 字段名格式无效")
        overlap = set(user_fields).intersection(generated_fields)
        if overlap:
            raise ConfigError("provenance 的用户字段与自动补全字段不能重复")
    else:
        identity_status = "locked" if len(reference_ids) == 1 else "unset"
        identity_anchor = _text(visual.get("identity_anchor"), "visual.identity_anchor")
        template_id = profile_id
        user_fields = ()
        generated_fields = ()

    return PersonaProfile(
        schema_version=schema_version,
        id=profile_id,
        display_name=_text(raw.get("display_name"), "display_name"),
        traits=_text_list(persona.get("traits", []), "persona.traits"),
        speaking_style=_text(persona.get("speaking_style"), "persona.speaking_style"),
        boundaries=_text_list(persona.get("boundaries", []), "persona.boundaries"),
        visual=VisualProfile(
            identity_anchor=identity_anchor,
            identity_version=_positive_int(
                visual.get("identity_version"),
                (
                    "visual_identity.identity_version"
                    if schema_version == 3
                    else "visual.identity_version"
                ),
                default=1,
            ),
            appearance=_text(
                appearance_raw.get("direction" if schema_version == 3 else "appearance"),
                "appearance.direction" if schema_version == 3 else "visual.appearance",
            ),
            default_style=_text(
                appearance_raw.get("default_style"),
                "appearance.default_style" if schema_version == 3 else "visual.default_style",
            ),
            reference_ids=reference_ids,
            identity_status=identity_status,
            default_hairstyle=(
                _text(appearance_raw.get("default_hairstyle"), "appearance.default_hairstyle")
                if schema_version == 3
                else "根据场景和用户要求自然变化"
            ),
            default_expression=(
                _text(appearance_raw.get("default_expression"), "appearance.default_expression")
                if schema_version == 3
                else "根据当下情绪自然变化"
            ),
            default_makeup=(
                _text(appearance_raw.get("default_makeup"), "appearance.default_makeup")
                if schema_version == 3
                else "根据场景和用户要求自然变化"
            ),
            default_wardrobe=(
                _text(appearance_raw.get("default_wardrobe"), "appearance.default_wardrobe")
                if schema_version == 3
                else "根据场景和用户要求自然变化"
            ),
        ),
        relationship=relationship,
        template_id=template_id,
        intent_summary=(
            _text(raw.get("intent_summary"), "intent_summary")
            if schema_version == 3
            else "沿用旧版人格设定"
        ),
        background=(
            _text(persona.get("background"), "persona.background")
            if schema_version == 3
            else "虚构成年人物，不声称拥有未记录的现实身份或共同经历。"
        ),
        values=(
            _text_list(persona.get("values", []), "persona.values")
            if schema_version == 3
            else ()
        ),
        interests=(
            _text_list(persona.get("interests", []), "persona.interests")
            if schema_version == 3
            else ()
        ),
        task_style=(
            _text(persona.get("task_style"), "persona.task_style")
            if schema_version == 3
            else "具体任务优先准确、完整地完成。"
        ),
        provenance=PersonaProvenance(
            user_fields=user_fields,
            generated_fields=generated_fields,
        ),
    )
