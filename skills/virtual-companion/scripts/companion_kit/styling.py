from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Iterable

from .config import PersonaProfile
from .file_lock import InterprocessLockError, exclusive_file_lock
from .initializer import InitializationError, default_profile_path, safe_profile_path
from .public_bundle import contains_absolute_path


class StylingError(ValueError):
    """Persona 私有造型偏好无法安全读取或保存。"""


class StylingConflict(StylingError):
    """造型偏好已在其他位置发生变化。"""


STYLING_SCHEMA_VERSION = 1
STYLING_BOLDNESS = {"restrained", "balanced", "expressive"}
_PROFILE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_ROOT_KEYS = {
    "schema_version",
    "profile_digest",
    "direction",
    "boldness",
    "signature_elements",
    "avoid_elements",
}
_MAX_ELEMENTS = 8


@dataclass(frozen=True)
class StylingPreferences:
    """只保存用户对单个 Persona 明确确认的私有造型偏好。"""

    direction: str
    boldness: str
    signature_elements: tuple[str, ...] = ()
    avoid_elements: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: object, *, profile_digest: str) -> StylingPreferences:
        if (
            not isinstance(raw, dict)
            or set(raw) != _ROOT_KEYS
            or raw.get("schema_version") != STYLING_SCHEMA_VERSION
            or raw.get("profile_digest") != profile_digest
        ):
            raise StylingError("造型偏好字段或版本无效")
        preferences = cls(
            direction=_text(raw.get("direction"), "造型方向", max_length=240),
            boldness=_boldness(raw.get("boldness")),
            signature_elements=_elements(
                raw.get("signature_elements"), "标志性元素"
            ),
            avoid_elements=_elements(raw.get("avoid_elements"), "造型禁区"),
        )
        _validate_element_conflicts(preferences)
        return preferences

    def to_dict(self, *, profile_digest: str) -> dict[str, object]:
        return {
            "schema_version": STYLING_SCHEMA_VERSION,
            "profile_digest": profile_digest,
            "direction": self.direction,
            "boldness": self.boldness,
            "signature_elements": list(self.signature_elements),
            "avoid_elements": list(self.avoid_elements),
        }


@dataclass(frozen=True)
class StylingSnapshot:
    preferences: StylingPreferences
    version: str


@dataclass(frozen=True)
class ResolvedStyleProfile:
    """供每日造型规划使用的轻量 Style DNA；不包含路径或聊天正文。"""

    direction: str
    boldness: str
    signature_elements: tuple[str, ...]
    avoid_elements: tuple[str, ...]
    archetype_weights: tuple[tuple[str, int], ...]
    user_configured: bool

    @property
    def anchor(self) -> str:
        parts = [self.direction]
        if self.signature_elements:
            parts.append("标志性元素：" + "、".join(self.signature_elements))
        if self.avoid_elements:
            parts.append("不要出现：" + "、".join(self.avoid_elements))
        return "；".join(parts)

    def weight(self, family: str) -> int:
        return dict(self.archetype_weights).get(family, 0)


def default_styling_root() -> Path:
    return default_profile_path("codex").parents[1] / "private" / "styling"


def parse_element_text(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise StylingError(f"{label}必须是文字")
    values = tuple(
        part.strip()
        for part in re.split(r"[\n,，、;；]+", value)
        if part.strip()
    )
    return _elements(list(values), label)


def resolve_style_profile(
    profile: PersonaProfile,
    preferences: StylingPreferences | None = None,
) -> ResolvedStyleProfile:
    fallback = "；".join(
        value.strip()
        for value in (
            profile.intent_summary,
            profile.visual.default_style,
            profile.visual.default_wardrobe,
        )
        if str(value or "").strip()
    )[:240]
    if not fallback:
        fallback = "延续 Persona 本来的气质，根据场景自然变化"
    direction = preferences.direction if preferences is not None else fallback
    boldness = (
        preferences.boldness
        if preferences is not None
        else _infer_boldness(direction)
    )
    signatures = preferences.signature_elements if preferences is not None else ()
    avoids = preferences.avoid_elements if preferences is not None else ()
    weighted_text = "；".join((direction, *signatures))
    weights = _archetype_weights(weighted_text)
    return ResolvedStyleProfile(
        direction=direction,
        boldness=boldness,
        signature_elements=signatures,
        avoid_elements=avoids,
        archetype_weights=tuple(sorted(weights.items())),
        user_configured=preferences is not None,
    )


def _infer_boldness(text: str) -> str:
    normalized = str(text or "").casefold()
    expressive = ("大胆", "抢眼", "明艳", "张扬", "性感", "强势", "攻击性")
    restrained = ("低调", "克制", "安静", "保守", "极简", "清淡")
    expressive_score = sum(word in normalized for word in expressive)
    restrained_score = sum(word in normalized for word in restrained)
    if expressive_score > restrained_score:
        return "expressive"
    if restrained_score > expressive_score:
        return "restrained"
    return "balanced"


def _archetype_weights(text: str) -> dict[str, int]:
    normalized = str(text or "").casefold()
    groups = {
        "sharp": ("高冷", "利落", "强势", "御姐", "冷感", "攻击性", "剪裁"),
        "classic": ("经典", "知性", "通勤", "复古", "成熟", "优雅"),
        "soft": ("温柔", "治愈", "柔和", "亲和", "软糯", "轻盈"),
        "relaxed": ("松弛", "随性", "休闲", "自然", "慵懒", "居家"),
        "playful": ("俏皮", "活泼", "甜酷", "灵动", "可爱", "玩心"),
        "glam": ("明艳", "华丽", "性感", "抢眼", "精致", "夜晚", "御姐"),
        "romantic": ("浪漫", "甜美", "柔美", "花朵", "梦幻"),
        "street": ("街头", "酷", "机能", "皮革", "丹宁", "叛逆"),
        "sporty": ("运动", "活力", "健身", "户外", "轻快"),
    }
    weights = {
        family: sum(2 if len(word) >= 3 else 1 for word in words if word in normalized)
        for family, words in groups.items()
    }
    if not any(weights.values()):
        weights["adaptive"] = 1
    return weights


def _safe_path(raw: str | Path) -> Path:
    try:
        return safe_profile_path(raw)
    except InitializationError as exc:
        raise StylingError(str(exc)) from exc


def _profile_digest(profile_id: str) -> str:
    if not isinstance(profile_id, str) or not _PROFILE_ID_RE.fullmatch(profile_id):
        raise StylingError("Persona 标识无效")
    return sha256(profile_id.encode("utf-8")).hexdigest()


def _text(value: object, label: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise StylingError(f"{label}必须是文字")
    normalized = " ".join(value.split()).strip()
    if (
        not normalized
        or len(normalized) > max_length
        or any(ord(character) < 32 for character in normalized)
        or contains_absolute_path(normalized)
        or "://" in normalized
        or "COMPANION_KIT" in normalized
    ):
        raise StylingError(f"{label}无效")
    return normalized


def _boldness(value: object) -> str:
    if not isinstance(value, str) or value not in STYLING_BOLDNESS:
        raise StylingError("造型表现程度无效")
    return value


def _elements(value: object, label: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or len(value) > _MAX_ELEMENTS
        or any(not isinstance(item, str) for item in value)
    ):
        raise StylingError(f"{label}必须是最多 {_MAX_ELEMENTS} 项文字")
    result = tuple(_text(item, label, max_length=60) for item in value)
    if len(set(result)) != len(result):
        raise StylingError(f"{label}不能重复")
    return result


def _validate_element_conflicts(preferences: StylingPreferences) -> None:
    signatures = {item.casefold() for item in preferences.signature_elements}
    avoids = {item.casefold() for item in preferences.avoid_elements}
    if signatures & avoids:
        raise StylingError("同一个元素不能既是标志性元素又是造型禁区")


def _private_directory(path: Path) -> None:
    _safe_path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise StylingError("造型偏好目录必须是普通目录")
    if os.name != "nt":
        path.chmod(0o700)


def _read_json(path: Path) -> dict[str, object] | None:
    if path.is_symlink():
        raise StylingError("造型偏好不能是符号链接")
    if not path.exists():
        return None
    try:
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode) or path.stat().st_size > 64 * 1024:
            raise StylingError("造型偏好必须是小型普通文件")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except StylingError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StylingError("造型偏好无法读取") from exc
    if not isinstance(raw, dict):
        raise StylingError("造型偏好结构无效")
    return raw


def _atomic_json(path: Path, payload: dict[str, object]) -> bytes:
    _private_directory(path.parent)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            if os.name != "nt":
                os.fchmod(handle.fileno(), 0o600)
            temporary = Path(handle.name)
        temporary.replace(path)
        if os.name != "nt":
            path.chmod(0o600)
        temporary = None
    except OSError as exc:
        raise StylingError("造型偏好无法保存") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return encoded


class StylingPreferenceStore:
    """每个 Persona 一份私有造型偏好；不保存聊天、图片或本地路径。"""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        lock_timeout: float = 1.0,
    ) -> None:
        if not 0 < float(lock_timeout) <= 60:
            raise StylingError("造型偏好锁等待时间无效")
        self.root = _safe_path(root or default_styling_root())
        self._lock_path = self.root / ".styling.lock"
        self._lock_timeout = float(lock_timeout)

    def _path(self, profile_id: str) -> Path:
        return self.root / f"{_profile_digest(profile_id)}.json"

    def inspect(self, *, profile_id: str) -> StylingSnapshot | None:
        digest = _profile_digest(profile_id)
        if self.root.is_symlink():
            raise StylingError("造型偏好目录不能是符号链接")
        if not self.root.exists():
            return None
        if not self.root.is_dir():
            raise StylingError("造型偏好目录必须是普通目录")
        path = self._path(profile_id)
        raw = _read_json(path)
        if raw is None:
            return None
        encoded = path.read_bytes()
        return StylingSnapshot(
            preferences=StylingPreferences.from_dict(raw, profile_digest=digest),
            version=sha256(encoded).hexdigest(),
        )

    def save(
        self,
        *,
        profile_id: str,
        direction: str,
        boldness: str,
        signature_elements: Iterable[str],
        avoid_elements: Iterable[str],
        expected_version: str | None,
    ) -> StylingSnapshot:
        digest = _profile_digest(profile_id)
        if isinstance(signature_elements, (str, bytes)) or isinstance(
            avoid_elements, (str, bytes)
        ):
            raise StylingError("造型元素必须是文字列表")
        try:
            signature_list = list(signature_elements)
            avoid_list = list(avoid_elements)
        except TypeError as exc:
            raise StylingError("造型元素必须是文字列表") from exc
        preferences = StylingPreferences(
            direction=_text(direction, "造型方向", max_length=240),
            boldness=_boldness(boldness),
            signature_elements=_elements(signature_list, "标志性元素"),
            avoid_elements=_elements(avoid_list, "造型禁区"),
        )
        _validate_element_conflicts(preferences)
        path = self._path(profile_id)
        try:
            _private_directory(self.root)
            with exclusive_file_lock(
                self._lock_path,
                timeout=self._lock_timeout,
            ):
                current_raw = _read_json(path)
                current_bytes = path.read_bytes() if current_raw is not None else None
                current_version = (
                    sha256(current_bytes).hexdigest()
                    if current_bytes is not None
                    else None
                )
                if current_version != expected_version:
                    raise StylingConflict("造型偏好已经变化，请刷新后重试")
                encoded = _atomic_json(
                    path,
                    preferences.to_dict(profile_digest=digest),
                )
        except StylingError:
            raise
        except (OSError, InterprocessLockError) as exc:
            raise StylingError("造型偏好无法安全保存") from exc
        return StylingSnapshot(
            preferences=preferences,
            version=sha256(encoded).hexdigest(),
        )
