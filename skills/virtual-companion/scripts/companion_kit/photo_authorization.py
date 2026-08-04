from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Callable
import uuid

from .file_lock import InterprocessLockError, exclusive_file_lock
from .image_provider import BASELINE_QUALITY, GPT_IMAGE_2


class AuthorizationError(ValueError):
    """图片调用没有有效的当前任务单次授权。"""


_PLAN_ID_RE = re.compile(r"^plan_[a-f0-9]{24}$")
_PROFILE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_REFERENCE_ID_RE = re.compile(r"^ref_[a-f0-9]{16,32}$")
_VERSION_RE = re.compile(r"^[a-f0-9]{64}$")
_ROUTE_ID_RE = re.compile(
    r"^(?:openai-direct|(?:codex|openclaw|hermes):openai-direct)$"
)
_STORED_KEYS = {
    "schema_version",
    "plan_id",
    "task_scope_digest",
    "profile_id",
    "profile_version",
    "identity_version",
    "prompt_digest",
    "reference_id",
    "purpose",
    "operation",
    "route_id",
    "model",
    "quality",
    "created_at",
    "expires_at",
}


@dataclass(frozen=True)
class PhotoAuthorizationPlan:
    plan_id: str
    profile_id: str
    identity_version: int
    reference_id: str | None
    purpose: str
    operation: str
    created_at: datetime
    expires_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "profile_id": self.profile_id,
            "identity_version": self.identity_version,
            "has_reference": self.reference_id is not None,
            "purpose": self.purpose,
            "operation": self.operation,
            "provider": "OpenAI Image API",
            "model": GPT_IMAGE_2,
            "quality": BASELINE_QUALITY,
            "billing": "按 OpenAI API 用量单次计费",
            "external_data": "本次图片提示；已有固定形象时还会发送一张参考图",
            "expires_at": self.expires_at.astimezone(UTC).isoformat(),
        }


def _safe_path(raw: str | Path) -> Path:
    raw_path = Path(raw).expanduser()
    if ".." in raw_path.parts:
        raise AuthorizationError("图片授权路径不能包含 ..")
    path = Path(os.path.abspath(raw_path))
    if sys.platform == "darwin":
        filesystem_root = Path(path.anchor)
        for alias, target in (
            (filesystem_root / "var", filesystem_root / "private" / "var"),
            (filesystem_root / "tmp", filesystem_root / "private" / "tmp"),
        ):
            try:
                path = target / path.relative_to(alias)
                break
            except ValueError:
                continue
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            break
        except OSError as exc:
            raise AuthorizationError(f"无法检查图片授权路径：{exc}") from exc
        if stat.S_ISLNK(mode):
            raise AuthorizationError("图片授权路径不能经过符号链接")
    return path


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _safe_path(path)
    if not path.is_dir():
        raise AuthorizationError("图片授权目录必须是普通目录")
    if os.name != "nt":
        path.chmod(0o700)


def _digest(value: str, label: str, *, max_length: int) -> str:
    normalized = str(value or "")
    if (
        not normalized
        or len(normalized) > max_length
        or any(ord(character) < 32 and character not in "\n\t" for character in normalized)
    ):
        raise AuthorizationError(f"{label} 无效")
    return sha256(normalized.encode("utf-8")).hexdigest()


def _validate_request(
    *,
    profile_id: str,
    profile_version: str,
    identity_version: int,
    reference_id: str | None,
    purpose: str,
    operation: str,
) -> None:
    if not _PROFILE_ID_RE.fullmatch(str(profile_id or "")):
        raise AuthorizationError("profile_id 格式无效")
    if not _VERSION_RE.fullmatch(str(profile_version or "")):
        raise AuthorizationError("人格配置版本无效")
    if (
        not isinstance(identity_version, int)
        or isinstance(identity_version, bool)
        or identity_version < 1
    ):
        raise AuthorizationError("identity_version 必须是正整数")
    if reference_id is not None and not _REFERENCE_ID_RE.fullmatch(reference_id):
        raise AuthorizationError("reference_id 格式无效")
    valid_pair = (purpose, operation, reference_id is not None) in {
        ("prototype", "generation", False),
        ("photo", "edit", True),
    }
    if not valid_pair:
        raise AuthorizationError("图片目的、操作与参考图状态不一致")


def _parse_time(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise AuthorizationError(f"图片授权的 {label} 无效")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AuthorizationError(f"图片授权的 {label} 无效") from exc
    if parsed.tzinfo is None:
        raise AuthorizationError(f"图片授权的 {label} 缺少时区")
    return parsed.astimezone(UTC)


class PhotoAuthorizationStore:
    """只保存摘要，授权消费后先删除，再允许一次真实调用。"""

    def __init__(
        self,
        root: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
        ttl: timedelta = timedelta(minutes=10),
        route_id: str = "openai-direct",
    ) -> None:
        if ttl <= timedelta(0) or ttl > timedelta(hours=1):
            raise AuthorizationError("图片授权有效期必须在 1 小时以内")
        if not _ROUTE_ID_RE.fullmatch(str(route_id or "")):
            raise AuthorizationError("图片授权路由无效")
        self.root = _safe_path(root)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ttl = ttl
        self._route_id = route_id
        self._lock_path = self.root / ".authorizations.lock"

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise AuthorizationError("图片授权时间必须包含时区")
        return value.astimezone(UTC)

    def _locked(self):
        _private_directory(self.root)
        _safe_path(self._lock_path)
        return exclusive_file_lock(self._lock_path)

    def _path(self, plan_id: str) -> Path:
        if not _PLAN_ID_RE.fullmatch(str(plan_id or "")):
            raise AuthorizationError("plan_id 格式无效")
        return self.root / f"{plan_id}.json"

    def _write(self, path: Path, payload: dict[str, object]) -> None:
        temporary: Path | None = None
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.root,
                prefix=".companion-auth-",
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
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _prune_expired_locked(self, now: datetime) -> None:
        for path in self.root.iterdir():
            if not path.name.startswith("plan_") or path.suffix != ".json":
                continue
            plan_id = path.stem
            if not _PLAN_ID_RE.fullmatch(plan_id) or path.is_symlink() or not path.is_file():
                raise AuthorizationError("图片授权目录包含无效记录")
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AuthorizationError("图片授权记录无效") from exc
            if not isinstance(stored, dict) or set(stored) != _STORED_KEYS:
                raise AuthorizationError("图片授权记录结构无效")
            expires_at = _parse_time(stored.get("expires_at"), "过期时间")
            if now >= expires_at:
                path.unlink()

    def create(
        self,
        *,
        task_scope: str,
        profile_id: str,
        profile_version: str,
        identity_version: int,
        prompt: str,
        reference_id: str | None,
        purpose: str,
        operation: str,
    ) -> PhotoAuthorizationPlan:
        _validate_request(
            profile_id=profile_id,
            profile_version=profile_version,
            identity_version=identity_version,
            reference_id=reference_id,
            purpose=purpose,
            operation=operation,
        )
        task_scope_digest = _digest(task_scope, "当前任务作用域", max_length=256)
        prompt_digest = _digest(prompt, "图片提示", max_length=8_000)
        created_at = self._now()
        expires_at = created_at + self._ttl
        plan_id = f"plan_{uuid.uuid4().hex[:24]}"
        stored = {
            "schema_version": 1,
            "plan_id": plan_id,
            "task_scope_digest": task_scope_digest,
            "profile_id": profile_id,
            "profile_version": profile_version,
            "identity_version": identity_version,
            "prompt_digest": prompt_digest,
            "reference_id": reference_id,
            "purpose": purpose,
            "operation": operation,
            "route_id": self._route_id,
            "model": GPT_IMAGE_2,
            "quality": BASELINE_QUALITY,
            "created_at": created_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        try:
            with self._locked():
                self._prune_expired_locked(created_at)
                self._write(self._path(plan_id), stored)
        except (OSError, InterprocessLockError) as exc:
            raise AuthorizationError(f"无法保存单次图片授权：{exc}") from exc
        return PhotoAuthorizationPlan(
            plan_id=plan_id,
            profile_id=profile_id,
            identity_version=identity_version,
            reference_id=reference_id,
            purpose=purpose,
            operation=operation,
            created_at=created_at,
            expires_at=expires_at,
        )

    def consume(
        self,
        *,
        plan_id: str,
        task_scope: str,
        profile_id: str,
        profile_version: str,
        identity_version: int,
        prompt: str,
        reference_id: str | None,
        purpose: str,
        operation: str,
    ) -> PhotoAuthorizationPlan:
        _validate_request(
            profile_id=profile_id,
            profile_version=profile_version,
            identity_version=identity_version,
            reference_id=reference_id,
            purpose=purpose,
            operation=operation,
        )
        expected = {
            "task_scope_digest": _digest(
                task_scope,
                "当前任务作用域",
                max_length=256,
            ),
            "profile_id": profile_id,
            "profile_version": profile_version,
            "identity_version": identity_version,
            "prompt_digest": _digest(prompt, "图片提示", max_length=8_000),
            "reference_id": reference_id,
            "purpose": purpose,
            "operation": operation,
            "route_id": self._route_id,
            "model": GPT_IMAGE_2,
            "quality": BASELINE_QUALITY,
        }
        path = self._path(plan_id)
        try:
            with self._locked():
                if path.is_symlink() or not path.is_file():
                    raise AuthorizationError("图片授权不存在或已经使用")
                try:
                    stored = json.loads(path.read_text(encoding="utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise AuthorizationError("图片授权记录无效") from exc
                if not isinstance(stored, dict) or set(stored) != _STORED_KEYS:
                    raise AuthorizationError("图片授权记录结构无效")
                if stored.get("schema_version") != 1 or stored.get("plan_id") != plan_id:
                    raise AuthorizationError("图片授权记录版本无效")
                created_at = _parse_time(stored.get("created_at"), "创建时间")
                expires_at = _parse_time(stored.get("expires_at"), "过期时间")
                if self._now() >= expires_at:
                    path.unlink(missing_ok=True)
                    raise AuthorizationError("图片授权已过期，请重新确认")
                if any(stored.get(key) != value for key, value in expected.items()):
                    path.unlink(missing_ok=True)
                    raise AuthorizationError("图片请求已变化，请重新确认")
                # 先消费授权，再允许调用网络。进程中断时宁可让用户重新确认，
                # 也不自动重放一次可能已经计费的请求。
                path.unlink()
        except (OSError, InterprocessLockError) as exc:
            raise AuthorizationError(f"无法消费单次图片授权：{exc}") from exc
        return PhotoAuthorizationPlan(
            plan_id=plan_id,
            profile_id=profile_id,
            identity_version=identity_version,
            reference_id=reference_id,
            purpose=purpose,
            operation=operation,
            created_at=created_at,
            expires_at=expires_at,
        )

    def discard(self, plan_id: str) -> bool:
        """在后续本地建档失败时撤销尚未消费的授权。"""

        path = self._path(plan_id)
        try:
            with self._locked():
                if path.is_symlink():
                    raise AuthorizationError("图片授权路径无效")
                if not path.exists():
                    return False
                if not path.is_file():
                    raise AuthorizationError("图片授权记录无效")
                path.unlink()
                return True
        except AuthorizationError:
            raise
        except (OSError, InterprocessLockError) as exc:
            raise AuthorizationError(f"无法撤销单次图片授权：{exc}") from exc
