from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Callable

from .daily_look import (
    DAILY_LOOK_SCHEMA_VERSION,
    DailyLook,
    DailyLookDirective,
    DailyLookError,
    companion_day_key,
    custom_daily_look,
    plan_daily_look,
    proposal_daily_look,
)
from .file_lock import InterprocessLockError, exclusive_file_lock
from .initializer import InitializationError, default_profile_path, safe_profile_path


class DailyLookStoreError(ValueError):
    """每日穿搭状态无法安全读取或写入。"""


DAILY_LOOK_STORE_SCHEMA_VERSION = 1
DAILY_LOOK_PENDING_SCHEMA_VERSION = 1
_MAX_RECENT_DAYS = 30
_MAX_PREFERRED_THEMES = 8
_PENDING_TTL = timedelta(minutes=20)
_PROFILE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_LOOK_ID_RE = re.compile(r"^look_[0-9a-f]{24}$")
_THEME_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
_STORE_KEYS = {
    "schema_version",
    "profile_digest",
    "enabled",
    "preferred_theme_ids",
    "recent",
}
_PENDING_KEYS = {
    "schema_version",
    "profile_digest",
    "created_at",
    "action",
    "expected_look_id",
    "look",
}


@dataclass(frozen=True)
class DailyLookSnapshot:
    enabled: bool
    current: DailyLook | None
    recent: tuple[DailyLook, ...]
    preferred_theme_ids: tuple[str, ...]


@dataclass(frozen=True)
class DailyLookCommitResult:
    look: DailyLook | None
    committed: bool


@dataclass(frozen=True)
class _State:
    enabled: bool
    recent: tuple[DailyLook, ...]
    preferred_theme_ids: tuple[str, ...]


@dataclass(frozen=True)
class _Pending:
    action: str
    expected_look_id: str
    look: DailyLook


def default_daily_look_root() -> Path:
    return default_profile_path("codex").parents[1] / "private" / "daily-looks"


def _safe_path(raw: str | Path) -> Path:
    try:
        return safe_profile_path(raw)
    except InitializationError as exc:
        raise DailyLookStoreError(str(exc)) from exc


def _profile_digest(profile_id: str) -> str:
    if not isinstance(profile_id, str) or not _PROFILE_ID_RE.fullmatch(profile_id):
        raise DailyLookStoreError("Persona 标识无效")
    return sha256(profile_id.encode("utf-8")).hexdigest()


def _opaque_digest(value: str, label: str) -> str:
    normalized = str(value or "")
    if (
        not normalized
        or len(normalized) > 256
        or any(ord(character) < 32 for character in normalized)
    ):
        raise DailyLookStoreError(f"{label}无效")
    return sha256(normalized.encode("utf-8")).hexdigest()


def _call_digest(session_id: str, tool_use_id: str) -> str:
    session = _opaque_digest(session_id, "Codex session")
    tool = _opaque_digest(tool_use_id, "Codex 图片工具调用")
    return sha256(f"{session}:{tool}".encode("ascii")).hexdigest()


def _private_directory(path: Path) -> None:
    _safe_path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise DailyLookStoreError("每日穿搭目录必须是普通目录")
    if os.name != "nt":
        path.chmod(0o700)


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
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
        raise DailyLookStoreError("每日穿搭状态无法保存") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, object] | None:
    if path.is_symlink():
        raise DailyLookStoreError("每日穿搭状态不能是符号链接")
    if not path.exists():
        return None
    try:
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode) or path.stat().st_size > 256 * 1024:
            raise DailyLookStoreError("每日穿搭状态必须是小型普通文件")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except DailyLookStoreError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DailyLookStoreError("每日穿搭状态无法读取") from exc
    if not isinstance(raw, dict):
        raise DailyLookStoreError("每日穿搭状态结构无效")
    return raw


class DailyLookStore:
    """只保存结构化穿搭卡；不保存聊天、提示词、任务 ID、路径或图片。"""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        lock_timeout: float = 1.0,
    ) -> None:
        if not math.isfinite(lock_timeout) or lock_timeout <= 0:
            raise DailyLookStoreError("每日穿搭锁等待时间无效")
        self.root = _safe_path(root or default_daily_look_root())
        self.runtime_root = self.root / "runtime"
        self._lock_path = self.root / ".daily-looks.lock"
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._lock_timeout = float(lock_timeout)

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise DailyLookStoreError("每日穿搭时间必须包含时区")
        return value

    def _created_at(self) -> str:
        return self._now().astimezone(UTC).isoformat()

    def _state_path(self, profile_id: str) -> Path:
        return self.root / f"{_profile_digest(profile_id)}.json"

    def _pending_path(self, session_id: str, tool_use_id: str) -> Path:
        return self.runtime_root / f"{_call_digest(session_id, tool_use_id)}.json"

    def _load_state(self, profile_id: str) -> _State:
        digest = _profile_digest(profile_id)
        raw = _read_json(self._state_path(profile_id))
        if raw is None:
            return _State(enabled=True, recent=(), preferred_theme_ids=())
        if (
            set(raw) != _STORE_KEYS
            or raw.get("schema_version") != DAILY_LOOK_STORE_SCHEMA_VERSION
            or raw.get("profile_digest") != digest
            or not isinstance(raw.get("enabled"), bool)
        ):
            raise DailyLookStoreError("每日穿搭状态版本或字段无效")
        preferred_raw = raw.get("preferred_theme_ids")
        recent_raw = raw.get("recent")
        if (
            not isinstance(preferred_raw, list)
            or len(preferred_raw) > _MAX_PREFERRED_THEMES
            or any(
                not isinstance(item, str) or not _THEME_ID_RE.fullmatch(item)
                for item in preferred_raw
            )
            or len(set(preferred_raw)) != len(preferred_raw)
            or not isinstance(recent_raw, list)
            or len(recent_raw) > _MAX_RECENT_DAYS
        ):
            raise DailyLookStoreError("每日穿搭状态内容无效")
        try:
            recent = tuple(DailyLook.from_dict(item) for item in recent_raw)
        except DailyLookError as exc:
            raise DailyLookStoreError(str(exc)) from exc
        day_keys = [item.day_key for item in recent]
        if day_keys != sorted(day_keys) or len(day_keys) != len(set(day_keys)):
            raise DailyLookStoreError("每日穿搭历史顺序无效")
        return _State(
            enabled=raw["enabled"],  # type: ignore[arg-type]
            recent=recent,
            preferred_theme_ids=tuple(preferred_raw),
        )

    def _write_state(self, profile_id: str, state: _State) -> None:
        recent = tuple(sorted(state.recent, key=lambda item: item.day_key))[
            -_MAX_RECENT_DAYS:
        ]
        _atomic_json(
            self._state_path(profile_id),
            {
                "schema_version": DAILY_LOOK_STORE_SCHEMA_VERSION,
                "profile_digest": _profile_digest(profile_id),
                "enabled": state.enabled,
                "preferred_theme_ids": list(state.preferred_theme_ids),
                "recent": [item.to_dict() for item in recent],
            },
        )

    def _current_from_state(self, state: _State) -> DailyLook | None:
        day_key = companion_day_key(self._now())
        return next(
            (item for item in reversed(state.recent) if item.day_key == day_key),
            None,
        )

    @staticmethod
    def _replace_day(state: _State, look: DailyLook) -> _State:
        recent = tuple(item for item in state.recent if item.day_key != look.day_key)
        return _State(
            enabled=state.enabled,
            recent=(*recent, look),
            preferred_theme_ids=state.preferred_theme_ids,
        )

    def snapshot(self, *, profile_id: str) -> DailyLookSnapshot:
        try:
            _private_directory(self.root)
            with exclusive_file_lock(self._lock_path, timeout=self._lock_timeout):
                state = self._load_state(profile_id)
                return DailyLookSnapshot(
                    enabled=state.enabled,
                    current=self._current_from_state(state),
                    recent=state.recent,
                    preferred_theme_ids=state.preferred_theme_ids,
                )
        except (OSError, InterprocessLockError) as exc:
            raise DailyLookStoreError("每日穿搭状态无法读取") from exc

    def inspect(self, *, profile_id: str) -> DailyLookSnapshot:
        """只读面板快照；首次打开面板时不创建目录、锁文件或今日穿搭。"""

        if self.root.is_symlink():
            raise DailyLookStoreError("每日穿搭目录不能是符号链接")
        if not self.root.exists():
            return DailyLookSnapshot(
                enabled=True,
                current=None,
                recent=(),
                preferred_theme_ids=(),
            )
        try:
            if not self.root.is_dir():
                raise DailyLookStoreError("每日穿搭目录必须是普通目录")
            state = self._load_state(profile_id)
            return DailyLookSnapshot(
                enabled=state.enabled,
                current=self._current_from_state(state),
                recent=state.recent,
                preferred_theme_ids=state.preferred_theme_ids,
            )
        except OSError as exc:
            raise DailyLookStoreError("每日穿搭状态无法读取") from exc

    def current(self, *, profile_id: str) -> DailyLook | None:
        return self.snapshot(profile_id=profile_id).current

    def ensure_today(
        self,
        *,
        profile_id: str,
        style_anchor: str,
    ) -> DailyLook | None:
        try:
            _private_directory(self.root)
            with exclusive_file_lock(self._lock_path, timeout=self._lock_timeout):
                state = self._load_state(profile_id)
                if not state.enabled:
                    return None
                current = self._current_from_state(state)
                if current is not None:
                    return current
                look = plan_daily_look(
                    profile_id=profile_id,
                    day_key=companion_day_key(self._now()),
                    revision=1,
                    style_anchor=style_anchor,
                    recent=state.recent,
                    preferred_theme_ids=state.preferred_theme_ids,
                    created_at=self._created_at(),
                )
                self._write_state(profile_id, self._replace_day(state, look))
                return look
        except (DailyLookError, OSError, InterprocessLockError) as exc:
            if isinstance(exc, DailyLookStoreError):
                raise
            raise DailyLookStoreError("无法安排今天的穿搭") from exc

    def set_enabled(self, *, profile_id: str, enabled: bool) -> DailyLookSnapshot:
        if not isinstance(enabled, bool):
            raise DailyLookStoreError("每日穿搭开关无效")
        try:
            _private_directory(self.root)
            with exclusive_file_lock(self._lock_path, timeout=self._lock_timeout):
                state = self._load_state(profile_id)
                updated = _State(
                    enabled=enabled,
                    recent=state.recent,
                    preferred_theme_ids=state.preferred_theme_ids,
                )
                self._write_state(profile_id, updated)
                return DailyLookSnapshot(
                    enabled=updated.enabled,
                    current=self._current_from_state(updated),
                    recent=updated.recent,
                    preferred_theme_ids=updated.preferred_theme_ids,
                )
        except (OSError, InterprocessLockError) as exc:
            raise DailyLookStoreError("每日穿搭开关无法保存") from exc

    def _expected_current(
        self,
        state: _State,
        expected_look_id: str,
    ) -> DailyLook:
        if not isinstance(expected_look_id, str) or not _LOOK_ID_RE.fullmatch(
            expected_look_id
        ):
            raise DailyLookStoreError("每日穿搭版本无效，请刷新人物面板")
        current = self._current_from_state(state)
        if current is None or current.look_id != expected_look_id:
            raise DailyLookStoreError("今天的穿搭已经变化，请刷新后再试")
        return current

    def reroll_today(
        self,
        *,
        profile_id: str,
        style_anchor: str,
        expected_look_id: str,
    ) -> DailyLook:
        try:
            _private_directory(self.root)
            with exclusive_file_lock(self._lock_path, timeout=self._lock_timeout):
                state = self._load_state(profile_id)
                if not state.enabled:
                    raise DailyLookStoreError("每日穿搭当前已暂停")
                current = self._expected_current(state, expected_look_id)
                look = plan_daily_look(
                    profile_id=profile_id,
                    day_key=current.day_key,
                    revision=current.revision + 1,
                    style_anchor=style_anchor,
                    recent=state.recent,
                    preferred_theme_ids=state.preferred_theme_ids,
                    created_at=self._created_at(),
                )
                self._write_state(profile_id, self._replace_day(state, look))
                return look
        except (DailyLookError, OSError, InterprocessLockError) as exc:
            if isinstance(exc, DailyLookStoreError):
                raise
            raise DailyLookStoreError("今天的穿搭无法安全更换") from exc

    def customize_today(
        self,
        *,
        profile_id: str,
        note: str,
        expected_look_id: str,
    ) -> DailyLook:
        try:
            _private_directory(self.root)
            with exclusive_file_lock(self._lock_path, timeout=self._lock_timeout):
                state = self._load_state(profile_id)
                if not state.enabled:
                    raise DailyLookStoreError("每日穿搭当前已暂停")
                current = self._expected_current(state, expected_look_id)
                look = custom_daily_look(
                    profile_id=profile_id,
                    day_key=current.day_key,
                    revision=current.revision + 1,
                    note=note,
                    base=current,
                    created_at=self._created_at(),
                )
                self._write_state(profile_id, self._replace_day(state, look))
                return look
        except (DailyLookError, OSError, InterprocessLockError) as exc:
            if isinstance(exc, DailyLookStoreError):
                raise
            raise DailyLookStoreError("今天的穿搭微调无法安全保存") from exc

    def remember_current(
        self,
        *,
        profile_id: str,
        expected_look_id: str,
    ) -> DailyLookSnapshot:
        try:
            _private_directory(self.root)
            with exclusive_file_lock(self._lock_path, timeout=self._lock_timeout):
                state = self._load_state(profile_id)
                current = self._expected_current(state, expected_look_id)
                preferred = tuple(
                    dict.fromkeys((*state.preferred_theme_ids, current.theme_id))
                )[-_MAX_PREFERRED_THEMES:]
                updated = _State(
                    enabled=state.enabled,
                    recent=state.recent,
                    preferred_theme_ids=preferred,
                )
                self._write_state(profile_id, updated)
                return DailyLookSnapshot(
                    enabled=updated.enabled,
                    current=current,
                    recent=updated.recent,
                    preferred_theme_ids=updated.preferred_theme_ids,
                )
        except (OSError, InterprocessLockError) as exc:
            raise DailyLookStoreError("穿搭偏好无法安全保存") from exc

    def _load_pending(
        self,
        *,
        profile_id: str,
        session_id: str,
        tool_use_id: str,
    ) -> _Pending | None:
        path = self._pending_path(session_id, tool_use_id)
        raw = _read_json(path)
        if raw is None:
            return None
        if (
            set(raw) != _PENDING_KEYS
            or raw.get("schema_version") != DAILY_LOOK_PENDING_SCHEMA_VERSION
            or raw.get("profile_digest") != _profile_digest(profile_id)
            or raw.get("action") not in {"use_daily", "replace_daily"}
            or not isinstance(raw.get("expected_look_id"), str)
            or not _LOOK_ID_RE.fullmatch(raw["expected_look_id"])  # type: ignore[arg-type]
        ):
            raise DailyLookStoreError("待确认穿搭状态无效")
        created_at = raw.get("created_at")
        if not isinstance(created_at, str):
            raise DailyLookStoreError("待确认穿搭时间无效")
        try:
            timestamp = datetime.fromisoformat(created_at)
        except ValueError as exc:
            raise DailyLookStoreError("待确认穿搭时间无效") from exc
        if timestamp.tzinfo is None:
            raise DailyLookStoreError("待确认穿搭时间缺少时区")
        age = self._now().astimezone(UTC) - timestamp.astimezone(UTC)
        if age < timedelta(0) or age > _PENDING_TTL:
            path.unlink(missing_ok=True)
            return None
        try:
            look = DailyLook.from_dict(raw.get("look"))
        except DailyLookError as exc:
            raise DailyLookStoreError(str(exc)) from exc
        return _Pending(
            action=raw["action"],  # type: ignore[arg-type]
            expected_look_id=raw["expected_look_id"],  # type: ignore[arg-type]
            look=look,
        )

    def stage_for_photo(
        self,
        *,
        profile_id: str,
        session_id: str,
        tool_use_id: str,
        directive: DailyLookDirective,
    ) -> DailyLook | None:
        if directive.action in {"one_shot", "preserve_target"}:
            return None
        path = self._pending_path(session_id, tool_use_id)
        try:
            _private_directory(self.root)
            _private_directory(self.runtime_root)
            with exclusive_file_lock(self._lock_path, timeout=self._lock_timeout):
                existing = self._load_pending(
                    profile_id=profile_id,
                    session_id=session_id,
                    tool_use_id=tool_use_id,
                )
                if existing is not None:
                    return existing.look
                state = self._load_state(profile_id)
                current = self._expected_current(state, str(directive.look_id or ""))
                if directive.action == "use_daily":
                    staged = current
                else:
                    assert directive.proposal is not None
                    staged = proposal_daily_look(
                        profile_id=profile_id,
                        day_key=current.day_key,
                        revision=current.revision + 1,
                        expected=current,
                        proposal=directive.proposal,
                        created_at=self._created_at(),
                    )
                _atomic_json(
                    path,
                    {
                        "schema_version": DAILY_LOOK_PENDING_SCHEMA_VERSION,
                        "profile_digest": _profile_digest(profile_id),
                        "created_at": self._created_at(),
                        "action": directive.action,
                        "expected_look_id": current.look_id,
                        "look": staged.to_dict(),
                    },
                )
                return staged
        except (DailyLookError, OSError, InterprocessLockError) as exc:
            if isinstance(exc, DailyLookStoreError):
                raise
            raise DailyLookStoreError("无法暂存本轮每日穿搭") from exc

    def peek_for_photo(
        self,
        *,
        profile_id: str,
        session_id: str,
        tool_use_id: str,
    ) -> DailyLook | None:
        try:
            pending = self._load_pending(
                profile_id=profile_id,
                session_id=session_id,
                tool_use_id=tool_use_id,
            )
        except DailyLookStoreError:
            return None
        return pending.look if pending is not None else None

    def commit_for_photo(
        self,
        *,
        profile_id: str,
        session_id: str,
        tool_use_id: str,
    ) -> DailyLookCommitResult:
        path = self._pending_path(session_id, tool_use_id)
        if not path.exists() or path.is_symlink():
            return DailyLookCommitResult(None, False)
        try:
            _private_directory(self.root)
            with exclusive_file_lock(self._lock_path, timeout=self._lock_timeout):
                pending = self._load_pending(
                    profile_id=profile_id,
                    session_id=session_id,
                    tool_use_id=tool_use_id,
                )
                if pending is None:
                    path.unlink(missing_ok=True)
                    return DailyLookCommitResult(None, False)
                state = self._load_state(profile_id)
                current = self._current_from_state(state)
                confirmed = pending.look.with_status(
                    "confirmed",
                    confirmed_at=self._created_at(),
                )
                committed = bool(
                    current is not None
                    and current.look_id == pending.expected_look_id
                    and current.day_key == pending.look.day_key
                )
                if committed:
                    self._write_state(
                        profile_id,
                        self._replace_day(state, confirmed),
                    )
                path.unlink(missing_ok=True)
                return DailyLookCommitResult(confirmed, committed)
        except (OSError, InterprocessLockError) as exc:
            raise DailyLookStoreError("无法确认本轮每日穿搭") from exc

    def discard_for_photo(self, *, session_id: str, tool_use_id: str) -> None:
        path = self._pending_path(session_id, tool_use_id)
        try:
            if path.is_symlink():
                raise DailyLookStoreError("待确认穿搭不能是符号链接")
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise DailyLookStoreError("无法清理待确认穿搭") from exc
