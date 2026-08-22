from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import stat
import tempfile
from typing import Callable

from .codex_turn import PendingPhotoContext
from .file_lock import InterprocessLockError, exclusive_file_lock
from .initializer import InitializationError, default_profile_path, safe_profile_path


PENDING_PHOTO_CONTEXT_SCHEMA_VERSION = 1
PENDING_PHOTO_CONTEXT_TTL = timedelta(minutes=10)
_MAX_RECORD_BYTES = 4 * 1024
_RECORD_KEYS = {
    "schema_version",
    "session_digest",
    "profile_digest",
    "created_at",
    "source",
    "context_ref",
}


class PendingPhotoContextStoreError(ValueError):
    """短时续拍上下文无法安全读取或保存。"""


def default_pending_photo_context_root() -> Path:
    return (
        default_profile_path("codex").parents[1]
        / "private"
        / "pending-photo-contexts"
    )


def _safe_path(raw: str | Path) -> Path:
    try:
        return safe_profile_path(raw)
    except InitializationError as exc:
        raise PendingPhotoContextStoreError(str(exc)) from exc


def _digest(label: str, value: object) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > 256
        or any(ord(character) < 32 for character in normalized)
    ):
        raise PendingPhotoContextStoreError(f"{label}无效")
    return sha256(normalized.encode("utf-8")).hexdigest()


def _private_directory(path: Path) -> None:
    _safe_path(path)
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise PendingPhotoContextStoreError("续拍上下文目录必须是普通目录")
        if os.name != "nt":
            path.chmod(0o700)
    except PendingPhotoContextStoreError:
        raise
    except OSError as exc:
        raise PendingPhotoContextStoreError("续拍上下文目录无法创建") from exc


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
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
        raise PendingPhotoContextStoreError("续拍上下文无法保存") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class PendingPhotoContextStore:
    """按任务与 Persona 保存一次性短时状态，不保存正文、任务 ID 或 Persona ID。"""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        lock_timeout: float = 0.5,
    ) -> None:
        if not math.isfinite(lock_timeout) or lock_timeout <= 0:
            raise PendingPhotoContextStoreError("续拍上下文锁等待时间无效")
        self.root = _safe_path(root or default_pending_photo_context_root())
        self._lock_path = self.root / ".pending-photo-contexts.lock"
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock_timeout = float(lock_timeout)

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise PendingPhotoContextStoreError("续拍上下文时间必须包含时区")
        return value.astimezone(UTC)

    def _path(self, session_id: object) -> Path:
        return self.root / f"{_digest('Codex 任务标识', session_id)}.json"

    def _load_locked(
        self,
        *,
        profile_id: object,
        session_id: object,
    ) -> PendingPhotoContext | None:
        path = self._path(session_id)
        if path.is_symlink():
            raise PendingPhotoContextStoreError("续拍上下文不能是符号链接")
        if not path.exists():
            return None
        try:
            mode = path.lstat().st_mode
            if (
                not stat.S_ISREG(mode)
                or path.stat().st_size <= 0
                or path.stat().st_size > _MAX_RECORD_BYTES
                or (os.name != "nt" and stat.S_IMODE(mode) & 0o077)
            ):
                raise PendingPhotoContextStoreError("续拍上下文必须是私有小型普通文件")
            raw = json.loads(path.read_text(encoding="utf-8"))
        except PendingPhotoContextStoreError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PendingPhotoContextStoreError("续拍上下文无法读取") from exc
        if (
            not isinstance(raw, dict)
            or set(raw) != _RECORD_KEYS
            or raw.get("schema_version") != PENDING_PHOTO_CONTEXT_SCHEMA_VERSION
            or raw.get("session_digest") != _digest("Codex 任务标识", session_id)
            or not isinstance(raw.get("created_at"), str)
            or not isinstance(raw.get("source"), str)
            or not isinstance(raw.get("context_ref"), str)
        ):
            raise PendingPhotoContextStoreError("续拍上下文结构无效")
        try:
            created_at = datetime.fromisoformat(raw["created_at"])
        except ValueError as exc:
            raise PendingPhotoContextStoreError("续拍上下文时间无效") from exc
        if created_at.tzinfo is None:
            raise PendingPhotoContextStoreError("续拍上下文时间缺少时区")
        age = self._now() - created_at.astimezone(UTC)
        profile_matches = raw.get("profile_digest") == _digest(
            "Persona 标识", profile_id
        )
        if age < timedelta(0) or age > PENDING_PHOTO_CONTEXT_TTL or not profile_matches:
            path.unlink(missing_ok=True)
            return None
        try:
            return PendingPhotoContext(
                source=raw["source"],
                context_ref=raw["context_ref"],
            )
        except ValueError as exc:
            raise PendingPhotoContextStoreError(str(exc)) from exc

    def _cleanup_expired_locked(self) -> None:
        now = self._now()
        for path in tuple(sorted(self.root.glob("*.json")))[:512]:
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                raw = json.loads(path.read_text(encoding="utf-8"))
                if (
                    not isinstance(raw, dict)
                    or set(raw) != _RECORD_KEYS
                    or raw.get("schema_version")
                    != PENDING_PHOTO_CONTEXT_SCHEMA_VERSION
                    or not isinstance(raw.get("created_at"), str)
                ):
                    continue
                created_at = datetime.fromisoformat(raw["created_at"])
                if created_at.tzinfo is None:
                    continue
                age = now - created_at.astimezone(UTC)
                if age < timedelta(0) or age > PENDING_PHOTO_CONTEXT_TTL:
                    path.unlink(missing_ok=True)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                # 损坏或未来版本记录留给诊断，不在清理中覆盖。
                continue

    def remember(
        self,
        *,
        profile_id: object,
        session_id: object,
        source: str,
        context_ref: str = "",
    ) -> PendingPhotoContext:
        try:
            context = PendingPhotoContext(source=source, context_ref=context_ref)
        except ValueError as exc:
            raise PendingPhotoContextStoreError(str(exc)) from exc
        profile_digest = _digest("Persona 标识", profile_id)
        session_digest = _digest("Codex 任务标识", session_id)
        _private_directory(self.root)
        try:
            with exclusive_file_lock(self._lock_path, timeout=self._lock_timeout):
                self._cleanup_expired_locked()
                _atomic_json(
                    self._path(session_id),
                    {
                        "schema_version": PENDING_PHOTO_CONTEXT_SCHEMA_VERSION,
                        "session_digest": session_digest,
                        "profile_digest": profile_digest,
                        "created_at": self._now().isoformat(),
                        "source": context.source,
                        "context_ref": context.context_ref,
                    },
                )
        except PendingPhotoContextStoreError:
            raise
        except (OSError, InterprocessLockError) as exc:
            raise PendingPhotoContextStoreError("续拍上下文无法保存") from exc
        return context

    def peek(
        self,
        *,
        profile_id: object,
        session_id: object,
    ) -> PendingPhotoContext | None:
        if self.root.is_symlink():
            raise PendingPhotoContextStoreError("续拍上下文目录不能是符号链接")
        if not self.root.exists():
            return None
        try:
            with exclusive_file_lock(self._lock_path, timeout=self._lock_timeout):
                return self._load_locked(
                    profile_id=profile_id,
                    session_id=session_id,
                )
        except PendingPhotoContextStoreError:
            raise
        except (OSError, InterprocessLockError) as exc:
            raise PendingPhotoContextStoreError("续拍上下文无法读取") from exc

    def consume(
        self,
        *,
        profile_id: object,
        session_id: object,
    ) -> PendingPhotoContext | None:
        if self.root.is_symlink():
            raise PendingPhotoContextStoreError("续拍上下文目录不能是符号链接")
        if not self.root.exists():
            return None
        path = self._path(session_id)
        try:
            with exclusive_file_lock(self._lock_path, timeout=self._lock_timeout):
                context = self._load_locked(
                    profile_id=profile_id,
                    session_id=session_id,
                )
                if context is not None:
                    path.unlink(missing_ok=True)
                return context
        except PendingPhotoContextStoreError:
            raise
        except (OSError, InterprocessLockError) as exc:
            raise PendingPhotoContextStoreError("续拍上下文无法消费") from exc

    def clear(self, *, session_id: object) -> None:
        if self.root.is_symlink():
            raise PendingPhotoContextStoreError("续拍上下文目录不能是符号链接")
        if not self.root.exists():
            return
        path = self._path(session_id)
        try:
            with exclusive_file_lock(self._lock_path, timeout=self._lock_timeout):
                if path.is_symlink():
                    raise PendingPhotoContextStoreError("续拍上下文不能是符号链接")
                path.unlink(missing_ok=True)
        except PendingPhotoContextStoreError:
            raise
        except (OSError, InterprocessLockError) as exc:
            raise PendingPhotoContextStoreError("续拍上下文无法清除") from exc
