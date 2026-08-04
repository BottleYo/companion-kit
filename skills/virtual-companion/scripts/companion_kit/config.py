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


_ROOT_KEYS_V1 = {"schema_version", "id", "display_name", "persona", "visual"}
_ROOT_KEYS_V2 = _ROOT_KEYS_V1 | {"relationship"}
_PERSONA_KEYS = {"traits", "speaking_style", "boundaries"}
_VISUAL_KEYS_V1 = {"identity_anchor", "appearance", "default_style", "reference_ids"}
_VISUAL_KEYS_V2 = _VISUAL_KEYS_V1 | {"identity_version"}
_RELATIONSHIP_KEYS = {"starting_mode", "romance_enabled"}
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
    if schema_version not in {1, 2}:
        raise ConfigError("仅支持 schema_version = 1 或 2")
    _expect_keys(
        raw,
        _ROOT_KEYS_V1 if schema_version == 1 else _ROOT_KEYS_V2,
        "根配置",
    )

    profile_id = _text(raw.get("id"), "id")
    if not _ID_RE.fullmatch(profile_id):
        raise ConfigError("id 必须使用小写字母、数字、下划线或连字符")

    persona = raw.get("persona")
    visual = raw.get("visual")
    if not isinstance(persona, dict) or not isinstance(visual, dict):
        raise ConfigError("必须同时提供 [persona] 与 [visual]")
    _expect_keys(persona, _PERSONA_KEYS, "persona")
    _expect_keys(
        visual,
        _VISUAL_KEYS_V1 if schema_version == 1 else _VISUAL_KEYS_V2,
        "visual",
    )

    relationship_raw = raw.get("relationship")
    if schema_version == 2:
        if not isinstance(relationship_raw, dict):
            raise ConfigError("schema_version = 2 必须提供 [relationship]")
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

    reference_ids = _text_list(visual.get("reference_ids", []), "visual.reference_ids")
    for reference_id in reference_ids:
        if not _REFERENCE_ID_RE.fullmatch(reference_id):
            raise ConfigError("reference_ids 只能使用不透明标识，不能使用文件路径")

    return PersonaProfile(
        schema_version=schema_version,
        id=profile_id,
        display_name=_text(raw.get("display_name"), "display_name"),
        traits=_text_list(persona.get("traits", []), "persona.traits"),
        speaking_style=_text(persona.get("speaking_style"), "persona.speaking_style"),
        boundaries=_text_list(persona.get("boundaries", []), "persona.boundaries"),
        visual=VisualProfile(
            identity_anchor=_text(visual.get("identity_anchor"), "visual.identity_anchor"),
            identity_version=_positive_int(
                visual.get("identity_version"),
                "visual.identity_version",
                default=1,
            ),
            appearance=_text(visual.get("appearance"), "visual.appearance"),
            default_style=_text(visual.get("default_style"), "visual.default_style"),
            reference_ids=reference_ids,
        ),
        relationship=relationship,
    )
