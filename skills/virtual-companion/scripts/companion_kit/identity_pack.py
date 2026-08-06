from __future__ import annotations


class IdentityPackRoleError(ValueError):
    """身份参考角色不属于轻量身份包。"""


PRIMARY_FACE = "primary_face"
PROFILE_FACE = "profile_face"
BODY_SHAPE = "body_shape"

IDENTITY_PACK_ROLES = (PRIMARY_FACE, PROFILE_FACE, BODY_SHAPE)
OPTIONAL_IDENTITY_ROLES = (PROFILE_FACE, BODY_SHAPE)
IDENTITY_ROLE_LABELS = {
    PRIMARY_FACE: "主脸",
    PROFILE_FACE: "侧脸",
    BODY_SHAPE: "体型",
}

_BODY_CUES = (
    "全身",
    "穿搭",
    "体型",
    "身材",
    "远景",
    "走路",
    "站姿",
    "坐姿",
    "full body",
    "full-body",
    "outfit",
)
_PROFILE_CUES = (
    "侧脸",
    "侧面",
    "回眸",
    "回头",
    "转头",
    "明显侧身",
    "profile",
    "side view",
    "three-quarter",
    "3/4",
)


def normalize_identity_role(value: str) -> str:
    role = str(value or "").strip().lower()
    if role not in IDENTITY_PACK_ROLES:
        raise IdentityPackRoleError("身份参考角色无效")
    return role


def ordered_identity_roles(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    normalized = tuple(normalize_identity_role(value) for value in values)
    if len(set(normalized)) != len(normalized):
        raise IdentityPackRoleError("身份参考角色不能重复")
    return tuple(role for role in IDENTITY_PACK_ROLES if role in normalized)


def select_identity_roles(
    brief: str,
    available_roles: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    """按场景选择一到两张参考；主脸始终存在且永远优先。"""

    available = ordered_identity_roles(available_roles)
    if PRIMARY_FACE not in available:
        raise IdentityPackRoleError("身份参考包缺少主脸")
    text = str(brief or "").casefold()
    if BODY_SHAPE in available and any(cue in text for cue in _BODY_CUES):
        return (PRIMARY_FACE, BODY_SHAPE)
    if PROFILE_FACE in available and any(cue in text for cue in _PROFILE_CUES):
        return (PRIMARY_FACE, PROFILE_FACE)
    return (PRIMARY_FACE,)
