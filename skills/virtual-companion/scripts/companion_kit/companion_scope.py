from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import stat
import tempfile

from .file_lock import InterprocessLockError, exclusive_file_lock
from .initializer import InitializationError, default_profile_path, safe_profile_path


COMPANION_SCOPE_SCHEMA_VERSION = 1
DEFAULT_MAX_BINDINGS = 512
_KEY_BYTES = 32
_MAX_BINDING_BYTES = 4 * 1024
_BINDING_KEYS = {"schema_version", "scope_digest"}


class CompanionScopeError(ValueError):
    """Codex 陪伴任务绑定无法安全读取或保存。"""


def default_companion_scope_root() -> Path:
    return default_profile_path("codex").parents[1] / "system" / "codex-scopes"


def _safe_path(raw: str | Path) -> Path:
    try:
        return safe_profile_path(raw)
    except InitializationError as exc:
        raise CompanionScopeError(str(exc)) from exc


def _session_id(value: object) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > 256
        or any(ord(character) < 32 for character in normalized)
    ):
        raise CompanionScopeError("Codex 任务标识无效")
    return normalized


def _private_directory(path: Path) -> None:
    _safe_path(path)
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise CompanionScopeError("陪伴任务目录必须是普通目录")
        if os.name != "nt":
            path.chmod(0o700)
    except CompanionScopeError:
        raise
    except OSError as exc:
        raise CompanionScopeError("陪伴任务目录无法创建") from exc


def _secure_regular_file(path: Path, *, label: str, max_bytes: int) -> bytes:
    if path.is_symlink():
        raise CompanionScopeError(f"{label}不能是符号链接")
    try:
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            raise CompanionScopeError(f"{label}必须是普通文件")
        size = path.stat().st_size
        if size <= 0 or size > max_bytes:
            raise CompanionScopeError(f"{label}大小无效")
        if os.name != "nt" and stat.S_IMODE(mode) & 0o077:
            raise CompanionScopeError(f"{label}权限过宽")
        return path.read_bytes()
    except CompanionScopeError:
        raise
    except OSError as exc:
        raise CompanionScopeError(f"{label}无法读取") from exc


class CompanionScopeStore:
    """只保存显式绑定的 Codex 任务 HMAC，不保存原始任务标识或正文。"""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        lock_timeout: float = 0.5,
        max_bindings: int = DEFAULT_MAX_BINDINGS,
    ) -> None:
        if not math.isfinite(lock_timeout) or lock_timeout <= 0:
            raise CompanionScopeError("陪伴任务锁等待时间无效")
        if (
            not isinstance(max_bindings, int)
            or isinstance(max_bindings, bool)
            or not 1 <= max_bindings <= 4096
        ):
            raise CompanionScopeError("陪伴任务数量上限无效")
        self.root = _safe_path(root or default_companion_scope_root())
        self.bindings_root = self.root / "bindings"
        self.key_path = self.root / "scope-key"
        self._lock_path = self.root / ".codex-scopes.lock"
        self._lock_timeout = float(lock_timeout)
        self._max_bindings = max_bindings

    def _ensure_roots(self) -> None:
        _private_directory(self.root.parent)
        _private_directory(self.root)
        _private_directory(self.bindings_root)

    def _read_key(self, *, create: bool) -> bytes | None:
        if self.key_path.is_symlink():
            raise CompanionScopeError("陪伴任务本机密钥不能是符号链接")
        if not self.key_path.exists():
            if not create:
                return None
            key = os.urandom(_KEY_BYTES)
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor: int | None = None
            try:
                descriptor = os.open(self.key_path, flags, 0o600)
                os.write(descriptor, key)
                os.fsync(descriptor)
                if os.name != "nt":
                    os.fchmod(descriptor, 0o600)
            except FileExistsError:
                key = _secure_regular_file(
                    self.key_path,
                    label="陪伴任务本机密钥",
                    max_bytes=_KEY_BYTES,
                )
            except OSError as exc:
                raise CompanionScopeError("陪伴任务本机密钥无法创建") from exc
            finally:
                if descriptor is not None:
                    os.close(descriptor)
            if len(key) != _KEY_BYTES:
                raise CompanionScopeError("陪伴任务本机密钥无效")
            return key
        key = _secure_regular_file(
            self.key_path,
            label="陪伴任务本机密钥",
            max_bytes=_KEY_BYTES,
        )
        if len(key) != _KEY_BYTES:
            raise CompanionScopeError("陪伴任务本机密钥无效")
        return key

    @staticmethod
    def _digest(session_id: str, key: bytes) -> str:
        return hmac.new(
            key,
            b"companion-kit-codex-scope-v1\0" + session_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _binding_path(self, digest: str) -> Path:
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise CompanionScopeError("陪伴任务摘要无效")
        return self.bindings_root / f"{digest}.json"

    def _read_binding(self, digest: str) -> dict[str, object] | None:
        path = self._binding_path(digest)
        if path.is_symlink():
            raise CompanionScopeError("陪伴任务绑定不能是符号链接")
        if not path.exists():
            return None
        try:
            payload = json.loads(
                _secure_regular_file(
                    path,
                    label="陪伴任务绑定",
                    max_bytes=_MAX_BINDING_BYTES,
                ).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CompanionScopeError("陪伴任务绑定无法读取") from exc
        if (
            not isinstance(payload, dict)
            or set(payload) != _BINDING_KEYS
            or payload.get("schema_version") != COMPANION_SCOPE_SCHEMA_VERSION
            or payload.get("scope_digest") != digest
        ):
            raise CompanionScopeError("陪伴任务绑定结构无效")
        return payload

    def _binding_digests(self) -> tuple[str, ...]:
        if self.bindings_root.is_symlink():
            raise CompanionScopeError("陪伴任务绑定目录不能是符号链接")
        if not self.bindings_root.exists():
            return ()
        try:
            entries = tuple(self.bindings_root.iterdir())
        except OSError as exc:
            raise CompanionScopeError("陪伴任务绑定目录无法读取") from exc
        digests: list[str] = []
        for path in entries:
            if path.name.startswith("."):
                raise CompanionScopeError("陪伴任务绑定目录包含未知文件")
            if path.suffix != ".json":
                raise CompanionScopeError("陪伴任务绑定目录包含未知文件")
            digest = path.stem
            self._read_binding(digest)
            digests.append(digest)
        return tuple(sorted(digests))

    def _write_binding(self, digest: str) -> None:
        path = self._binding_path(digest)
        if path.is_symlink():
            raise CompanionScopeError("陪伴任务绑定不能是符号链接")
        encoded = json.dumps(
            {
                "schema_version": COMPANION_SCOPE_SCHEMA_VERSION,
                "scope_digest": digest,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.bindings_root,
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
            raise CompanionScopeError("陪伴任务绑定无法保存") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def is_bound(self, session_id: object) -> bool:
        normalized = _session_id(session_id)
        if self.root.is_symlink() or self.bindings_root.is_symlink():
            raise CompanionScopeError("陪伴任务目录不能是符号链接")
        key = self._read_key(create=False)
        if key is None:
            return False
        digest = self._digest(normalized, key)
        return self._read_binding(digest) is not None

    def bind(self, session_id: object) -> bool:
        normalized = _session_id(session_id)
        self._ensure_roots()
        try:
            with exclusive_file_lock(self._lock_path, timeout=self._lock_timeout):
                key = self._read_key(create=True)
                assert key is not None
                digest = self._digest(normalized, key)
                if self._read_binding(digest) is not None:
                    return False
                if len(self._binding_digests()) >= self._max_bindings:
                    raise CompanionScopeError(
                        "陪伴任务绑定数量已达安全上限；请在人物面板清空旧任务连接"
                    )
                self._write_binding(digest)
        except CompanionScopeError:
            raise
        except InterprocessLockError as exc:
            raise CompanionScopeError("陪伴任务绑定正被其他进程更新") from exc
        return True

    def unbind(self, session_id: object) -> bool:
        normalized = _session_id(session_id)
        if self.root.is_symlink() or self.bindings_root.is_symlink():
            raise CompanionScopeError("陪伴任务目录不能是符号链接")
        if not self.root.exists():
            return False
        try:
            with exclusive_file_lock(self._lock_path, timeout=self._lock_timeout):
                key = self._read_key(create=False)
                if key is None:
                    return False
                digest = self._digest(normalized, key)
                path = self._binding_path(digest)
                if self._read_binding(digest) is None:
                    return False
                path.unlink()
        except CompanionScopeError:
            raise
        except (OSError, InterprocessLockError) as exc:
            raise CompanionScopeError("陪伴任务绑定无法解除") from exc
        return True

    def count(self) -> int:
        if self.root.is_symlink() or self.bindings_root.is_symlink():
            raise CompanionScopeError("陪伴任务目录不能是符号链接")
        if not self.root.exists():
            return 0
        key = self._read_key(create=False)
        digests = self._binding_digests()
        if key is None and digests:
            raise CompanionScopeError("陪伴任务绑定缺少本机密钥")
        return len(digests)

    def clear_all(self, *, confirm: bool) -> int:
        """明确确认后重置任务连接；允许从损坏记录或丢失密钥中自助恢复。"""

        if confirm is not True:
            raise CompanionScopeError("清除陪伴任务绑定需要明确确认")
        if self.root.is_symlink() or self.bindings_root.is_symlink():
            raise CompanionScopeError("陪伴任务目录不能是符号链接")
        if not self.root.exists():
            return 0
        try:
            with exclusive_file_lock(self._lock_path, timeout=self._lock_timeout):
                try:
                    entries = tuple(self.bindings_root.iterdir())
                except FileNotFoundError:
                    entries = ()
                removed = 0
                for path in entries:
                    mode = path.lstat().st_mode
                    if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
                        raise CompanionScopeError(
                            "陪伴任务绑定目录包含无法安全清除的内容"
                        )
                    path.unlink()
                    removed += 1
                if self.key_path.exists() or self.key_path.is_symlink():
                    key_mode = self.key_path.lstat().st_mode
                    if not (stat.S_ISREG(key_mode) or stat.S_ISLNK(key_mode)):
                        raise CompanionScopeError(
                            "陪伴任务本机密钥无法安全重置"
                        )
                    self.key_path.unlink()
        except CompanionScopeError:
            raise
        except (OSError, InterprocessLockError) as exc:
            raise CompanionScopeError("陪伴任务绑定无法清除") from exc
        return removed
