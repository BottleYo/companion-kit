from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Callable

from .file_lock import InterprocessLockError, exclusive_file_lock
from .initializer import InitializationError, default_profile_path, safe_profile_path


SESSION_START = "session_start"
POST_TOOL_USE = "post_tool_use"
HOOK_TYPES = frozenset({SESSION_START, POST_TOOL_USE})
HOOK_HEALTH_SCHEMA_VERSION = 1
HOOK_REVIEW_SCHEMA_VERSION = 1

_HOOK_BUNDLE_FILES = (
    "hooks/hooks.json",
    "hooks/codex_context.py",
    "hooks/codex_prompt_context.py",
    "hooks/codex_image_guard.py",
    "hooks/codex_image_receipt.py",
    "skills/virtual-companion/scripts/companion_kit/codex_runtime.py",
    "skills/virtual-companion/scripts/companion_kit/codex_turn.py",
    "skills/virtual-companion/scripts/companion_kit/codex_image_receipts.py",
    "skills/virtual-companion/scripts/companion_kit/image_assets.py",
    "skills/virtual-companion/scripts/companion_kit/state_store.py",
    "skills/virtual-companion/scripts/companion_kit/photo_moment.py",
    "skills/virtual-companion/scripts/companion_kit/photo_moment_store.py",
)
_RECEIPT_KEYS = {
    "schema_version",
    "plugin_version",
    "hook_bundle_digest",
    "hook_type",
    "last_success_at",
}
_REVIEW_KEYS = {
    "schema_version",
    "plugin_version",
    "hook_bundle_digest",
    "reviewed_at",
}
_MAX_BUNDLE_FILE_BYTES = 2 * 1024 * 1024
_LOCK_TIMEOUT_SECONDS = 0.25


class HookHealthError(ValueError):
    """Hook 健康状态无法安全读取或保存。"""


def version_base(version: str) -> str:
    """忽略 Codex 本地缓存后缀，但保留真实发布版本。"""

    return str(version or "").split("+", 1)[0].strip()


@dataclass(frozen=True)
class HookBundleIdentity:
    plugin_version: str
    hook_bundle_digest: str


@dataclass(frozen=True)
class HookHealthReceipt:
    schema_version: int
    plugin_version: str
    hook_bundle_digest: str
    hook_type: str
    last_success_at: str


@dataclass(frozen=True)
class HookHealthStatus:
    hook_type: str
    state: str
    verified: bool
    last_success_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "hook_type": self.hook_type,
            "state": self.state,
            "verified": self.verified,
            "last_success_at": self.last_success_at,
        }


@dataclass(frozen=True)
class HookReviewStatus:
    state: str
    acknowledged: bool
    reviewed_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "acknowledged": self.acknowledged,
            "reviewed_at": self.reviewed_at,
        }


def default_hook_health_root() -> Path:
    return default_profile_path("codex").parents[1] / "system" / "hook-health"


def _safe_path(raw: str | Path) -> Path:
    try:
        return safe_profile_path(raw)
    except InitializationError as exc:
        raise HookHealthError(str(exc)) from exc


def _regular_file(path: Path, *, label: str) -> bytes:
    try:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise HookHealthError(f"{label}必须是普通文件")
        size = path.stat().st_size
        if size <= 0 or size > _MAX_BUNDLE_FILE_BYTES:
            raise HookHealthError(f"{label}大小无效")
        return path.read_bytes()
    except HookHealthError:
        raise
    except OSError as exc:
        raise HookHealthError(f"{label}无法读取") from exc


def inspect_hook_bundle(plugin_root: str | Path) -> HookBundleIdentity:
    root = Path(plugin_root).expanduser().resolve()
    manifest_path = root / ".codex-plugin" / "plugin.json"
    try:
        manifest = json.loads(
            _regular_file(manifest_path, label="Codex Plugin 清单").decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HookHealthError("Codex Plugin 清单无效") from exc
    version = str(manifest.get("version", "")).strip() if isinstance(manifest, dict) else ""
    if (
        not version
        or len(version) > 120
        or any(ord(character) < 32 for character in version)
    ):
        raise HookHealthError("Codex Plugin 清单缺少有效版本")

    digest = sha256()
    digest.update(b"companion-kit-hook-bundle-v2\0")
    for relative in _HOOK_BUNDLE_FILES:
        path = root.joinpath(*relative.split("/"))
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise HookHealthError("Hook bundle 路径无效") from exc
        payload = _regular_file(path, label=f"Hook bundle 文件 {relative}")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return HookBundleIdentity(
        plugin_version=version,
        hook_bundle_digest=digest.hexdigest(),
    )


def _timestamp(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise HookHealthError(f"{label}无效")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HookHealthError(f"{label}无效") from exc
    if parsed.tzinfo is None:
        raise HookHealthError(f"{label}必须包含时区")
    return parsed.astimezone(UTC).isoformat()


class HookHealthStore:
    """只记录 Hook 是否真实运行；不读取 Codex 信任配置。"""

    def __init__(
        self,
        *,
        plugin_root: str | Path,
        root: str | Path | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = _safe_path(root or default_hook_health_root())
        self.bundle = inspect_hook_bundle(plugin_root)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock_path = self.root / ".hook-health.lock"

    def _now(self) -> str:
        value = self._clock()
        if value.tzinfo is None:
            raise HookHealthError("Hook 健康时间必须包含时区")
        return value.astimezone(UTC).isoformat()

    def _receipt_path(self, hook_type: str) -> Path:
        if hook_type not in HOOK_TYPES:
            raise HookHealthError("不支持的 Hook 类型")
        return self.root / f"{hook_type}.json"

    def _ensure_private_root(self) -> None:
        _safe_path(self.root)
        for directory in (self.root.parent, self.root):
            try:
                directory.mkdir(parents=True, exist_ok=True)
                mode = directory.lstat().st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                    raise HookHealthError("Hook 健康目录必须是普通目录")
                if os.name != "nt":
                    directory.chmod(0o700)
            except HookHealthError:
                raise
            except OSError as exc:
                raise HookHealthError("Hook 健康目录无法创建") from exc

    def _read_json(self, path: Path) -> dict[str, object] | None:
        if path.is_symlink():
            raise HookHealthError("Hook 健康回执不能是符号链接")
        if not path.exists():
            return None
        try:
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise HookHealthError("Hook 健康回执必须是普通文件")
            payload = json.loads(path.read_text(encoding="utf-8"))
        except HookHealthError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HookHealthError("Hook 健康回执无法读取") from exc
        if not isinstance(payload, dict):
            raise HookHealthError("Hook 健康回执结构无效")
        return payload

    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        self._ensure_private_root()
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        temporary: Path | None = None
        try:
            with exclusive_file_lock(
                self._lock_path,
                timeout=_LOCK_TIMEOUT_SECONDS,
            ):
                if path.is_symlink():
                    raise HookHealthError("Hook 健康回执不能是符号链接")
                with tempfile.NamedTemporaryFile(
                    dir=self.root,
                    prefix=f".{path.name}.",
                    delete=False,
                ) as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                    temporary = Path(handle.name)
                if os.name != "nt":
                    temporary.chmod(0o600)
                temporary.replace(path)
                temporary = None
        except HookHealthError:
            raise
        except (OSError, InterprocessLockError) as exc:
            raise HookHealthError("Hook 健康回执无法保存") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _matches_current_bundle(self, payload: dict[str, object]) -> bool:
        return (
            version_base(str(payload.get("plugin_version") or ""))
            == version_base(self.bundle.plugin_version)
            and payload.get("hook_bundle_digest") == self.bundle.hook_bundle_digest
        )

    def record_success(self, hook_type: str) -> HookHealthReceipt:
        path = self._receipt_path(hook_type)
        receipt = HookHealthReceipt(
            schema_version=HOOK_HEALTH_SCHEMA_VERSION,
            plugin_version=self.bundle.plugin_version,
            hook_bundle_digest=self.bundle.hook_bundle_digest,
            hook_type=hook_type,
            last_success_at=self._now(),
        )
        self._write_json(
            path,
            {
                "schema_version": receipt.schema_version,
                "plugin_version": receipt.plugin_version,
                "hook_bundle_digest": receipt.hook_bundle_digest,
                "hook_type": receipt.hook_type,
                "last_success_at": receipt.last_success_at,
            },
        )
        return receipt

    def status(self, hook_type: str) -> HookHealthStatus:
        path = self._receipt_path(hook_type)
        try:
            payload = self._read_json(path)
            if payload is None:
                return HookHealthStatus(hook_type, "missing", False)
            if (
                set(payload) != _RECEIPT_KEYS
                or payload.get("schema_version") != HOOK_HEALTH_SCHEMA_VERSION
                or payload.get("hook_type") != hook_type
                or not isinstance(payload.get("plugin_version"), str)
                or not isinstance(payload.get("hook_bundle_digest"), str)
            ):
                raise HookHealthError("Hook 健康回执结构无效")
            last_success_at = _timestamp(
                payload.get("last_success_at"),
                label="Hook 最近成功时间",
            )
        except HookHealthError:
            return HookHealthStatus(hook_type, "invalid", False)
        if not self._matches_current_bundle(payload):
            return HookHealthStatus(hook_type, "stale", False, last_success_at)
        return HookHealthStatus(hook_type, "verified", True, last_success_at)

    def acknowledge_review(self) -> HookReviewStatus:
        reviewed_at = self._now()
        self._write_json(
            self.root / "review.json",
            {
                "schema_version": HOOK_REVIEW_SCHEMA_VERSION,
                "plugin_version": self.bundle.plugin_version,
                "hook_bundle_digest": self.bundle.hook_bundle_digest,
                "reviewed_at": reviewed_at,
            },
        )
        return HookReviewStatus("acknowledged", True, reviewed_at)

    def review_status(self) -> HookReviewStatus:
        try:
            payload = self._read_json(self.root / "review.json")
            if payload is None:
                return HookReviewStatus("missing", False)
            if (
                set(payload) != _REVIEW_KEYS
                or payload.get("schema_version") != HOOK_REVIEW_SCHEMA_VERSION
                or not isinstance(payload.get("plugin_version"), str)
                or not isinstance(payload.get("hook_bundle_digest"), str)
            ):
                raise HookHealthError("Hook 审核确认结构无效")
            reviewed_at = _timestamp(
                payload.get("reviewed_at"),
                label="Hook 审核确认时间",
            )
        except HookHealthError:
            return HookReviewStatus("invalid", False)
        if not self._matches_current_bundle(payload):
            return HookReviewStatus("stale", False, reviewed_at)
        return HookReviewStatus("acknowledged", True, reviewed_at)

    def snapshot(self) -> dict[str, object]:
        return {
            "review": self.review_status().to_dict(),
            SESSION_START: self.status(SESSION_START).to_dict(),
            POST_TOOL_USE: self.status(POST_TOOL_USE).to_dict(),
        }
