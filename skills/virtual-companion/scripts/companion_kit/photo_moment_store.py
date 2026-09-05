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

from .file_lock import InterprocessLockError, exclusive_file_lock
from .initializer import InitializationError, default_profile_path, safe_profile_path
from .photo_moment import PhotoMoment, PhotoMomentError, normalize_photo_moment


class PhotoMomentStoreError(ValueError):
    """最近照片配方无法安全读取或保存。"""


PHOTO_MOMENT_HISTORY_SCHEMA_VERSION = 4
PHOTO_MOMENT_PENDING_SCHEMA_VERSION = 4
_SUPPORTED_PHOTO_MOMENT_STORE_VERSIONS = frozenset({1, 2, 3, 4})
PHOTO_TURN_SCHEMA_VERSION = 1
PHOTO_RESULT_SCHEMA_VERSION = 3
LATEST_RESULT_MISSING = "missing"
LATEST_RESULT_GENERIC = "generic"
LATEST_RESULT_COMPANION = "companion"
_MAX_RECENT = 4
_MAX_RESULT_BYTES = 25 * 1024 * 1024
_PENDING_TTL = timedelta(minutes=20)
_RESULT_TTL = timedelta(hours=2)
_HISTORY_KEYS = {"schema_version", "profile_digest", "recent"}
_PENDING_KEYS = {
    "schema_version",
    "profile_digest",
    "created_at",
    "photo_moment",
}
_TURN_KEYS = {
    "schema_version",
    "profile_digest",
    "created_at",
    "mode",
    "turn_token",
}
_RESULT_KEYS = {
    "schema_version",
    "profile_digest",
    "created_at",
    "is_companion",
    "identity_version",
    "image_digests",
}
_RESULT_ENTRY_KEYS = {"path_sha256", "raw_sha256"}
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_TURN_TOKEN_RE = re.compile(r"^ckp_[0-9a-f]{24}$")


@dataclass(frozen=True)
class PhotoMomentCommitResult:
    photo_moment: PhotoMoment | None
    committed: bool


@dataclass(frozen=True)
class PhotoTurnTicket:
    profile_digest: str
    mode: str
    turn_token: str


def default_photo_moment_root() -> Path:
    return default_profile_path("codex").parents[1] / "private" / "photo-moments"


def _safe_path(raw: str | Path) -> Path:
    try:
        return safe_profile_path(raw)
    except InitializationError as exc:
        raise PhotoMomentStoreError(str(exc)) from exc


def _digest(label: str, value: str) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > 256
        or any(ord(character) < 32 for character in normalized)
    ):
        raise PhotoMomentStoreError(f"{label}无效")
    return sha256(normalized.encode("utf-8")).hexdigest()


def _identity_version(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise PhotoMomentStoreError("人物身份版本无效")
    return value


def _call_digest(session_id: str, tool_use_id: str) -> str:
    session = _digest("Codex session", session_id)
    tool = _digest("Codex 图片工具调用", tool_use_id)
    return sha256(f"{session}:{tool}".encode("ascii")).hexdigest()


def _private_directory(path: Path) -> None:
    _safe_path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise PhotoMomentStoreError("照片配方目录必须是普通目录")
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
        raise PhotoMomentStoreError("照片配方无法保存") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, object] | None:
    if path.is_symlink():
        raise PhotoMomentStoreError("照片配方不能是符号链接")
    if not path.exists():
        return None
    try:
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode) or path.stat().st_size > 128 * 1024:
            raise PhotoMomentStoreError("照片配方必须是小型普通文件")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except PhotoMomentStoreError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhotoMomentStoreError("照片配方无法读取") from exc
    if not isinstance(raw, dict):
        raise PhotoMomentStoreError("照片配方结构无效")
    return raw


class PhotoMomentStore:
    """只保存最近四条受控配方；提示词、文案、路径和原始任务标识不落盘。"""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        lock_timeout: float = 1.0,
    ) -> None:
        if not math.isfinite(lock_timeout) or lock_timeout <= 0:
            raise PhotoMomentStoreError("照片配方锁等待时间无效")
        self.root = _safe_path(root or default_photo_moment_root())
        self.runtime_root = self.root / "runtime"
        self.turn_root = self.runtime_root / "turns"
        self.result_root = self.runtime_root / "results"
        self._lock_path = self.root / ".photo-moments.lock"
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock_timeout = float(lock_timeout)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise PhotoMomentStoreError("照片配方时间必须包含时区")
        return value.astimezone(UTC)

    def history_path(self, profile_id: str) -> Path:
        return self.root / f"{_digest('Persona', profile_id)}.json"

    def _pending_path(self, session_id: str, tool_use_id: str) -> Path:
        return self.runtime_root / f"{_call_digest(session_id, tool_use_id)}.json"

    def _turn_path(self, session_id: str, turn_id: str) -> Path:
        return self.turn_root / f"{_call_digest(session_id, turn_id)}.json"

    def _result_path(self, session_id: str) -> Path:
        return self.result_root / f"{_digest('Codex session', session_id)}.json"

    def _cleanup_expired_runtime_locked(self) -> None:
        now = self._now()
        for directory, schema_version, keys, ttl in (
            (
                self.turn_root,
                PHOTO_TURN_SCHEMA_VERSION,
                _TURN_KEYS,
                _PENDING_TTL,
            ),
            (
                self.result_root,
                PHOTO_RESULT_SCHEMA_VERSION,
                _RESULT_KEYS,
                _RESULT_TTL,
            ),
        ):
            if not directory.is_dir() or directory.is_symlink():
                continue
            for path in tuple(sorted(directory.glob("*.json")))[:512]:
                try:
                    raw = _read_json(path)
                    if (
                        raw is None
                        or set(raw) != keys
                        or raw.get("schema_version") != schema_version
                        or not isinstance(raw.get("created_at"), str)
                    ):
                        continue
                    created = datetime.fromisoformat(str(raw["created_at"]))
                    if created.tzinfo is None:
                        continue
                    age = now - created.astimezone(UTC)
                    if age < timedelta(0) or age > ttl:
                        path.unlink(missing_ok=True)
                except (OSError, ValueError, PhotoMomentStoreError):
                    # 未知/损坏/未来版本文件留给诊断，不在清理中覆盖。
                    continue

    def _load_history(self, profile_id: str) -> tuple[PhotoMoment, ...]:
        profile_digest = _digest("Persona", profile_id)
        raw = _read_json(self.history_path(profile_id))
        if raw is None:
            return ()
        if (
            set(raw) != _HISTORY_KEYS
            or raw.get("schema_version")
            not in _SUPPORTED_PHOTO_MOMENT_STORE_VERSIONS
            or raw.get("profile_digest") != profile_digest
            or not isinstance(raw.get("recent"), list)
        ):
            raise PhotoMomentStoreError("照片配方历史版本或结构无效")
        try:
            moments = tuple(PhotoMoment.from_dict(item) for item in raw["recent"])
        except PhotoMomentError as exc:
            raise PhotoMomentStoreError(str(exc)) from exc
        if len(moments) > _MAX_RECENT:
            raise PhotoMomentStoreError("照片配方历史超过上限")
        return moments

    def recent(
        self,
        *,
        profile_id: str,
        identity_version: int | None = None,
    ) -> tuple[PhotoMoment, ...]:
        try:
            moments = self._load_history(profile_id)
        except PhotoMomentStoreError:
            return ()
        if identity_version is None:
            return moments
        return tuple(
            item for item in moments if item.identity_version == identity_version
        )

    def issue_turn(
        self,
        *,
        profile_id: str,
        session_id: str,
        turn_id: str,
        mode: str,
        turn_token: str,
    ) -> PhotoTurnTicket:
        if mode not in {"new", "edit_previous"} or not _TURN_TOKEN_RE.fullmatch(
            str(turn_token or "")
        ):
            raise PhotoMomentStoreError("照片回合票据参数无效")
        path = self._turn_path(session_id, turn_id)
        profile_digest = _digest("Persona", profile_id)
        _private_directory(self.root)
        _private_directory(self.runtime_root)
        _private_directory(self.turn_root)
        try:
            with exclusive_file_lock(self._lock_path, timeout=self._lock_timeout):
                self._cleanup_expired_runtime_locked()
                existing = self._load_turn(session_id=session_id, turn_id=turn_id)
                if existing is not None:
                    if existing != PhotoTurnTicket(profile_digest, mode, turn_token):
                        raise PhotoMomentStoreError("同一 Codex 回合不能更换人物照片票据")
                    return existing
                _atomic_json(
                    path,
                    {
                        "schema_version": PHOTO_TURN_SCHEMA_VERSION,
                        "profile_digest": profile_digest,
                        "created_at": self._now().isoformat(),
                        "mode": mode,
                        "turn_token": turn_token,
                    },
                )
        except PhotoMomentStoreError:
            raise
        except (OSError, InterprocessLockError) as exc:
            raise PhotoMomentStoreError("无法保存人物照片回合票据") from exc
        return PhotoTurnTicket(profile_digest, mode, turn_token)

    def _load_turn(self, *, session_id: str, turn_id: str) -> PhotoTurnTicket | None:
        raw = _read_json(self._turn_path(session_id, turn_id))
        if raw is None:
            return None
        if (
            set(raw) != _TURN_KEYS
            or raw.get("schema_version") != PHOTO_TURN_SCHEMA_VERSION
            or not isinstance(raw.get("profile_digest"), str)
            or not _DIGEST_RE.fullmatch(str(raw.get("profile_digest")))
            or raw.get("mode") not in {"new", "edit_previous"}
            or not isinstance(raw.get("turn_token"), str)
            or not _TURN_TOKEN_RE.fullmatch(str(raw.get("turn_token")))
        ):
            raise PhotoMomentStoreError("人物照片回合票据结构无效")
        created = raw.get("created_at")
        if not isinstance(created, str):
            raise PhotoMomentStoreError("人物照片回合票据时间无效")
        try:
            timestamp = datetime.fromisoformat(created)
        except ValueError as exc:
            raise PhotoMomentStoreError("人物照片回合票据时间无效") from exc
        if timestamp.tzinfo is None:
            raise PhotoMomentStoreError("人物照片回合票据时间缺少时区")
        age = self._now() - timestamp.astimezone(UTC)
        if age < timedelta(0) or age > _PENDING_TTL:
            try:
                self._turn_path(session_id, turn_id).unlink(missing_ok=True)
            except OSError:
                pass
            return None
        return PhotoTurnTicket(
            profile_digest=str(raw["profile_digest"]),
            mode=str(raw["mode"]),
            turn_token=str(raw["turn_token"]),
        )

    def turn_ticket(
        self,
        *,
        session_id: str,
        turn_id: str,
    ) -> PhotoTurnTicket | None:
        return self._load_turn(session_id=session_id, turn_id=turn_id)

    def turn_ticket_present(self, *, session_id: str, turn_id: str) -> bool:
        path = self._turn_path(session_id, turn_id)
        if path.is_symlink():
            raise PhotoMomentStoreError("人物照片回合票据不能是符号链接")
        return path.is_file()

    @staticmethod
    def ticket_matches_profile(ticket: PhotoTurnTicket, profile_id: str) -> bool:
        return ticket.profile_digest == _digest("Persona", profile_id)

    def record_image_result(
        self,
        *,
        profile_id: str,
        session_id: str,
        paths: tuple[str | Path, ...],
        is_companion: bool,
        identity_version: int,
    ) -> None:
        version = _identity_version(identity_version)
        image_digests: list[dict[str, str]] = []
        for raw_path in paths[:8]:
            candidate = Path(raw_path).expanduser()
            if not candidate.is_absolute():
                raise PhotoMomentStoreError("图片结果路径必须是绝对路径")
            try:
                details = candidate.lstat()
                mode = details.st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                    raise PhotoMomentStoreError("图片结果必须是普通文件")
                if details.st_size <= 0 or details.st_size > _MAX_RESULT_BYTES:
                    raise PhotoMomentStoreError("图片结果大小超出限制")
                raw_digest = sha256(candidate.read_bytes()).hexdigest()
            except PhotoMomentStoreError:
                raise
            except OSError as exc:
                raise PhotoMomentStoreError("图片结果无法读取") from exc
            entry = {
                "path_sha256": sha256(
                    str(candidate.resolve()).encode("utf-8")
                ).hexdigest(),
                "raw_sha256": raw_digest,
            }
            if entry not in image_digests:
                image_digests.append(entry)
        if not image_digests:
            raise PhotoMomentStoreError("图片结果不能为空")
        _private_directory(self.root)
        _private_directory(self.runtime_root)
        _private_directory(self.result_root)
        try:
            with exclusive_file_lock(self._lock_path, timeout=self._lock_timeout):
                self._cleanup_expired_runtime_locked()
                _atomic_json(
                    self._result_path(session_id),
                    {
                        "schema_version": PHOTO_RESULT_SCHEMA_VERSION,
                        "profile_digest": _digest("Persona", profile_id),
                        "created_at": self._now().isoformat(),
                        "is_companion": bool(is_companion),
                        "identity_version": version,
                        "image_digests": image_digests,
                    },
                )
        except (OSError, InterprocessLockError) as exc:
            raise PhotoMomentStoreError("无法保存当前任务图片类型回执") from exc

    def _load_result(
        self,
        *,
        profile_id: str,
        session_id: str,
        identity_version: int,
    ) -> dict[str, object] | None:
        version = _identity_version(identity_version)
        raw = _read_json(self._result_path(session_id))
        if raw is None:
            return None
        image_digests = raw.get("image_digests")
        if (
            set(raw) != _RESULT_KEYS
            or raw.get("schema_version") != PHOTO_RESULT_SCHEMA_VERSION
            or raw.get("profile_digest") != _digest("Persona", profile_id)
            or not isinstance(raw.get("is_companion"), bool)
            or not isinstance(raw.get("identity_version"), int)
            or isinstance(raw.get("identity_version"), bool)
            or int(raw.get("identity_version", 0)) < 1
            or not isinstance(image_digests, list)
            or not image_digests
            or len(image_digests) > 8
            or any(
                not isinstance(item, dict)
                or set(item) != _RESULT_ENTRY_KEYS
                or any(
                    not isinstance(value, str) or not _DIGEST_RE.fullmatch(value)
                    for value in item.values()
                )
                for item in image_digests
            )
        ):
            raise PhotoMomentStoreError("当前任务图片类型回执结构无效")
        if raw["identity_version"] != version:
            return None
        created = raw.get("created_at")
        if not isinstance(created, str):
            raise PhotoMomentStoreError("当前任务图片类型回执时间无效")
        try:
            timestamp = datetime.fromisoformat(created)
        except ValueError as exc:
            raise PhotoMomentStoreError("当前任务图片类型回执时间无效") from exc
        if timestamp.tzinfo is None:
            raise PhotoMomentStoreError("当前任务图片类型回执时间缺少时区")
        age = self._now() - timestamp.astimezone(UTC)
        if age < timedelta(0) or age > _RESULT_TTL:
            try:
                self._result_path(session_id).unlink(missing_ok=True)
            except OSError:
                pass
            return None
        return raw

    def latest_result_is_companion(
        self,
        *,
        profile_id: str,
        session_id: str,
        identity_version: int,
    ) -> bool:
        return self.latest_result_state(
            profile_id=profile_id,
            session_id=session_id,
            identity_version=identity_version,
        ) == LATEST_RESULT_COMPANION

    def latest_result_state(
        self,
        *,
        profile_id: str,
        session_id: str,
        identity_version: int,
    ) -> str:
        try:
            raw = self._load_result(
                profile_id=profile_id,
                session_id=session_id,
                identity_version=identity_version,
            )
        except PhotoMomentStoreError:
            return LATEST_RESULT_MISSING
        if raw is None:
            return LATEST_RESULT_MISSING
        return (
            LATEST_RESULT_COMPANION
            if raw["is_companion"] is True
            else LATEST_RESULT_GENERIC
        )

    def verify_latest_companion_path(
        self,
        *,
        profile_id: str,
        session_id: str,
        source_path: str | Path,
        identity_version: int,
    ) -> None:
        raw = self._load_result(
            profile_id=profile_id,
            session_id=session_id,
            identity_version=identity_version,
        )
        candidate = Path(source_path).expanduser()
        if not candidate.is_absolute():
            raise PhotoMomentStoreError("编辑目标必须是绝对路径")
        try:
            details = candidate.lstat()
            mode = details.st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise PhotoMomentStoreError("编辑目标必须是普通文件")
            if details.st_size <= 0 or details.st_size > _MAX_RESULT_BYTES:
                raise PhotoMomentStoreError("编辑目标大小超出限制")
            entry = {
                "path_sha256": sha256(
                    str(candidate.resolve()).encode("utf-8")
                ).hexdigest(),
                "raw_sha256": sha256(candidate.read_bytes()).hexdigest(),
            }
        except PhotoMomentStoreError:
            raise
        except OSError as exc:
            raise PhotoMomentStoreError("编辑目标无法读取") from exc
        if (
            raw is None
            or raw["is_companion"] is not True
            or entry not in raw["image_digests"]
        ):
            raise PhotoMomentStoreError("编辑目标不是当前任务最近一次人物照片")

    def _load_pending(
        self,
        *,
        profile_id: str,
        session_id: str,
        tool_use_id: str,
    ) -> PhotoMoment | None:
        raw = _read_json(self._pending_path(session_id, tool_use_id))
        if raw is None:
            return None
        profile_digest = _digest("Persona", profile_id)
        if (
            set(raw) != _PENDING_KEYS
            or raw.get("schema_version")
            not in _SUPPORTED_PHOTO_MOMENT_STORE_VERSIONS
            or raw.get("profile_digest") != profile_digest
        ):
            raise PhotoMomentStoreError("待提交照片配方结构无效")
        created = raw.get("created_at")
        if not isinstance(created, str):
            raise PhotoMomentStoreError("待提交照片配方时间无效")
        try:
            timestamp = datetime.fromisoformat(created)
        except ValueError as exc:
            raise PhotoMomentStoreError("待提交照片配方时间无效") from exc
        if timestamp.tzinfo is None:
            raise PhotoMomentStoreError("待提交照片配方时间缺少时区")
        age = self._now() - timestamp.astimezone(UTC)
        if age < timedelta(0) or age > _PENDING_TTL:
            return None
        try:
            return PhotoMoment.from_dict(raw.get("photo_moment"))
        except PhotoMomentError as exc:
            raise PhotoMomentStoreError(str(exc)) from exc

    def stage(
        self,
        *,
        profile_id: str,
        session_id: str,
        tool_use_id: str,
        photo_moment: PhotoMoment,
    ) -> PhotoMoment:
        path = self._pending_path(session_id, tool_use_id)
        _private_directory(self.root)
        _private_directory(self.runtime_root)
        try:
            with exclusive_file_lock(self._lock_path, timeout=self._lock_timeout):
                existing = self._load_pending(
                    profile_id=profile_id,
                    session_id=session_id,
                    tool_use_id=tool_use_id,
                )
                if existing is not None:
                    if existing != photo_moment:
                        raise PhotoMomentStoreError("同一图片调用不能更换照片配方")
                    return existing
                _atomic_json(
                    path,
                    {
                        "schema_version": PHOTO_MOMENT_PENDING_SCHEMA_VERSION,
                        "profile_digest": _digest("Persona", profile_id),
                        "created_at": self._now().isoformat(),
                        "photo_moment": photo_moment.to_dict(),
                    },
                )
        except (OSError, InterprocessLockError) as exc:
            raise PhotoMomentStoreError("无法暂存照片配方") from exc
        return photo_moment

    def stage_varied(
        self,
        *,
        profile_id: str,
        session_id: str,
        tool_use_id: str,
        photo_moment: PhotoMoment,
        allowed_intimacy_bands: tuple[str, ...],
    ) -> PhotoMoment:
        """在同一把锁内把成功历史与并发待生成配方一起纳入去重。"""

        path = self._pending_path(session_id, tool_use_id)
        _private_directory(self.root)
        _private_directory(self.runtime_root)
        try:
            with exclusive_file_lock(self._lock_path, timeout=self._lock_timeout):
                existing = self._load_pending(
                    profile_id=profile_id,
                    session_id=session_id,
                    tool_use_id=tool_use_id,
                )
                if existing is not None:
                    return existing
                try:
                    planning_history = [
                        item
                        for item in self._load_history(profile_id)
                        if item.identity_version == photo_moment.identity_version
                    ]
                except PhotoMomentStoreError:
                    planning_history = []
                profile_digest = _digest("Persona", profile_id)
                now = self._now()
                for pending_path in sorted(self.runtime_root.glob("*.json")):
                    if pending_path == path or not _DIGEST_RE.fullmatch(pending_path.stem):
                        continue
                    try:
                        raw = _read_json(pending_path)
                        if (
                            raw is None
                            or set(raw) != _PENDING_KEYS
                            or raw.get("schema_version")
                            not in _SUPPORTED_PHOTO_MOMENT_STORE_VERSIONS
                        ):
                            continue
                        created = datetime.fromisoformat(str(raw.get("created_at") or ""))
                        if created.tzinfo is None:
                            continue
                        age = now - created.astimezone(UTC)
                        if age > _PENDING_TTL:
                            pending_path.unlink(missing_ok=True)
                            continue
                        if age < timedelta(0) or raw.get("profile_digest") != profile_digest:
                            continue
                        pending = PhotoMoment.from_dict(raw.get("photo_moment"))
                        if pending.identity_version == photo_moment.identity_version:
                            planning_history.append(pending)
                    except (PhotoMomentError, PhotoMomentStoreError, ValueError):
                        continue
                normalized = normalize_photo_moment(
                    photo_moment,
                    recent=planning_history,
                    allowed_intimacy_bands=allowed_intimacy_bands,
                )
                _atomic_json(
                    path,
                    {
                        "schema_version": PHOTO_MOMENT_PENDING_SCHEMA_VERSION,
                        "profile_digest": profile_digest,
                        "created_at": self._now().isoformat(),
                        "photo_moment": normalized.to_dict(),
                    },
                )
                return normalized
        except (OSError, InterprocessLockError) as exc:
            raise PhotoMomentStoreError("无法暂存并去重照片配方") from exc

    def peek(
        self,
        *,
        profile_id: str,
        session_id: str,
        tool_use_id: str,
    ) -> PhotoMoment | None:
        try:
            return self._load_pending(
                profile_id=profile_id,
                session_id=session_id,
                tool_use_id=tool_use_id,
            )
        except PhotoMomentStoreError:
            return None

    def commit(
        self,
        *,
        profile_id: str,
        session_id: str,
        tool_use_id: str,
    ) -> PhotoMomentCommitResult:
        path = self._pending_path(session_id, tool_use_id)
        if not path.exists() or path.is_symlink():
            return PhotoMomentCommitResult(None, False)
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
                    return PhotoMomentCommitResult(None, False)
                committed = False
                try:
                    recent = list(self._load_history(profile_id))
                    if not recent or recent[-1] != pending:
                        recent.append(pending)
                    recent = recent[-_MAX_RECENT:]
                    _atomic_json(
                        self.history_path(profile_id),
                        {
                            "schema_version": PHOTO_MOMENT_HISTORY_SCHEMA_VERSION,
                            "profile_digest": _digest("Persona", profile_id),
                            "recent": [item.to_dict() for item in recent],
                        },
                    )
                    committed = True
                except PhotoMomentStoreError:
                    # 新版或损坏的历史不能覆盖；当前 Moment 仍可约束本轮文案。
                    committed = False
                path.unlink(missing_ok=True)
                return PhotoMomentCommitResult(pending, committed)
        except (OSError, InterprocessLockError) as exc:
            raise PhotoMomentStoreError("无法提交照片配方") from exc

    def discard(
        self,
        *,
        session_id: str,
        tool_use_id: str,
    ) -> None:
        path = self._pending_path(session_id, tool_use_id)
        try:
            if path.is_symlink():
                raise PhotoMomentStoreError("待提交照片配方不能是符号链接")
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise PhotoMomentStoreError("无法清理待提交照片配方") from exc
