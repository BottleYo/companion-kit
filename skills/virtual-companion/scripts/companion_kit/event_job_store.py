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

from .file_lock import InterprocessLockError, exclusive_file_lock


class EventJobError(ValueError):
    """事件型宿主图片作业无法安全继续。"""


_EVENT_HOSTS = {"openclaw", "hermes"}
_JOB_ID_RE = re.compile(r"^job_[a-f0-9]{24}$")
_PLAN_ID_RE = re.compile(r"^plan_[a-f0-9]{24}$")
_PROFILE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_REFERENCE_ID_RE = re.compile(r"^ref_[a-f0-9]{16,32}$")
_ASSET_ID_RE = re.compile(r"^(?:cand|art)_[a-f0-9]{16,32}$")
_VERSION_RE = re.compile(r"^[a-f0-9]{64}$")
_DIGEST_RE = _VERSION_RE
_STAGES = {
    "planned",
    "generating",
    "generated",
    "delivery_unknown",
    "delivered",
    "failed",
}
_STORED_KEYS = {
    "schema_version",
    "job_id",
    "host",
    "instance_digest",
    "conversation_digest",
    "request_event_digest",
    "profile_id",
    "profile_version",
    "identity_version",
    "prompt_digest",
    "reference_id",
    "purpose",
    "operation",
    "authorization_plan_id",
    "asset_id",
    "handoff_artifact_id",
    "stage",
    "created_at",
    "updated_at",
    "expires_at",
}


@dataclass(frozen=True)
class EventJobContext:
    host: str
    instance_scope: str
    conversation_scope: str
    request_event_id: str


@dataclass(frozen=True)
class EventJobShape:
    profile_id: str
    profile_version: str
    identity_version: int
    prompt: str
    reference_id: str | None
    purpose: str
    operation: str


@dataclass(frozen=True)
class EventJob:
    job_id: str
    host: str
    profile_id: str
    identity_version: int
    reference_id: str | None
    purpose: str
    operation: str
    authorization_plan_id: str
    asset_id: str | None
    handoff_artifact_id: str | None
    stage: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "host": self.host,
            "profile_id": self.profile_id,
            "identity_version": self.identity_version,
            "has_reference": self.reference_id is not None,
            "purpose": self.purpose,
            "operation": self.operation,
            "asset_id": self.asset_id,
            "stage": self.stage,
            "expires_at": self.expires_at.astimezone(UTC).isoformat(),
        }


def _safe_path(raw: str | Path) -> Path:
    raw_path = Path(raw).expanduser()
    if ".." in raw_path.parts:
        raise EventJobError("事件作业路径不能包含 ..")
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
            raise EventJobError(f"无法检查事件作业路径：{exc}") from exc
        if stat.S_ISLNK(mode):
            raise EventJobError("事件作业路径不能经过符号链接")
    return path


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _safe_path(path)
    if not path.is_dir():
        raise EventJobError("事件作业目录必须是普通目录")
    if os.name != "nt":
        path.chmod(0o700)


def _digest(
    value: str,
    label: str,
    *,
    max_length: int,
    allow_formatting: bool = False,
) -> str:
    normalized = str(value or "")
    allowed_controls = "\n\t" if allow_formatting else ""
    if (
        not normalized
        or len(normalized) > max_length
        or any(
            ord(character) < 32 and character not in allowed_controls
            for character in normalized
        )
    ):
        raise EventJobError(f"{label} 无效")
    return sha256(normalized.encode("utf-8")).hexdigest()


def _parse_time(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise EventJobError(f"事件作业的{label}无效")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise EventJobError(f"事件作业的{label}无效") from exc
    if parsed.tzinfo is None:
        raise EventJobError(f"事件作业的{label}缺少时区")
    return parsed.astimezone(UTC)


def _context_values(context: EventJobContext) -> dict[str, str]:
    if context.host not in _EVENT_HOSTS:
        raise EventJobError("事件宿主只能是 openclaw 或 hermes")
    return {
        "host": context.host,
        "instance_digest": _digest(
            context.instance_scope,
            "宿主实例作用域",
            max_length=256,
        ),
        "conversation_digest": _digest(
            context.conversation_scope,
            "当前会话作用域",
            max_length=512,
        ),
        "request_event_digest": _digest(
            context.request_event_id,
            "入站事件标识",
            max_length=512,
        ),
    }


def _shape_values(shape: EventJobShape) -> dict[str, object]:
    if not _PROFILE_ID_RE.fullmatch(str(shape.profile_id or "")):
        raise EventJobError("profile_id 格式无效")
    if not _VERSION_RE.fullmatch(str(shape.profile_version or "")):
        raise EventJobError("人格配置版本无效")
    if (
        not isinstance(shape.identity_version, int)
        or isinstance(shape.identity_version, bool)
        or shape.identity_version < 1
    ):
        raise EventJobError("identity_version 必须是正整数")
    if shape.reference_id is not None and not _REFERENCE_ID_RE.fullmatch(
        shape.reference_id
    ):
        raise EventJobError("reference_id 格式无效")
    valid_pair = (shape.purpose, shape.operation, shape.reference_id is not None) in {
        ("prototype", "generation", False),
        ("photo", "edit", True),
    }
    if not valid_pair:
        raise EventJobError("图片目的、操作与参考图状态不一致")
    return {
        "profile_id": shape.profile_id,
        "profile_version": shape.profile_version,
        "identity_version": shape.identity_version,
        "prompt_digest": _digest(
            shape.prompt,
            "图片提示",
            max_length=8_000,
            allow_formatting=True,
        ),
        "reference_id": shape.reference_id,
        "purpose": shape.purpose,
        "operation": shape.operation,
    }


class EventJobStore:
    """按宿主事件去重，只保存摘要与不透明 ID。"""

    def __init__(
        self,
        root: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
        ttl: timedelta = timedelta(hours=24),
    ) -> None:
        if ttl < timedelta(minutes=10) or ttl > timedelta(days=30):
            raise EventJobError("事件作业保留时间必须在 10 分钟到 30 天之间")
        self.root = _safe_path(root)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ttl = ttl
        self._lock_path = self.root / ".event-jobs.lock"

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise EventJobError("事件作业时间必须包含时区")
        return value.astimezone(UTC)

    def _locked(self):
        _private_directory(self.root)
        _safe_path(self._lock_path)
        return exclusive_file_lock(self._lock_path)

    def _path(self, job_id: str) -> Path:
        if not _JOB_ID_RE.fullmatch(str(job_id or "")):
            raise EventJobError("job_id 格式无效")
        return self.root / f"{job_id}.json"

    def _write_locked(self, path: Path, stored: dict[str, object]) -> None:
        temporary: Path | None = None
        encoded = json.dumps(
            stored,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.root,
                prefix=".companion-event-",
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

    def _load_locked(self, path: Path) -> dict[str, object]:
        _safe_path(path)
        if path.is_symlink() or not path.is_file():
            raise EventJobError("事件作业不存在或已经过期")
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EventJobError("事件作业记录无效") from exc
        if not isinstance(stored, dict) or set(stored) != _STORED_KEYS:
            raise EventJobError("事件作业记录结构无效")
        if stored.get("schema_version") != 1 or stored.get("stage") not in _STAGES:
            raise EventJobError("事件作业记录版本无效")
        job_id = stored.get("job_id")
        if (
            not isinstance(job_id, str)
            or not _JOB_ID_RE.fullmatch(job_id)
            or job_id != path.stem
        ):
            raise EventJobError("事件作业标识不一致")
        host = stored.get("host")
        digests = (
            stored.get("instance_digest"),
            stored.get("conversation_digest"),
            stored.get("request_event_digest"),
        )
        if host not in _EVENT_HOSTS or any(
            not isinstance(value, str) or not _DIGEST_RE.fullmatch(value)
            for value in digests
        ):
            raise EventJobError("事件作业宿主摘要无效")
        expected_key = "\0".join((str(host), *[str(value) for value in digests]))
        expected_job_id = f"job_{sha256(expected_key.encode('utf-8')).hexdigest()[:24]}"
        if job_id != expected_job_id:
            raise EventJobError("事件作业标识与宿主事件不一致")
        if (
            not isinstance(stored.get("profile_id"), str)
            or not _PROFILE_ID_RE.fullmatch(stored["profile_id"])
            or not isinstance(stored.get("profile_version"), str)
            or not _VERSION_RE.fullmatch(stored["profile_version"])
            or not isinstance(stored.get("prompt_digest"), str)
            or not _DIGEST_RE.fullmatch(stored["prompt_digest"])
        ):
            raise EventJobError("事件作业人格或提示摘要无效")
        identity_version = stored.get("identity_version")
        if (
            not isinstance(identity_version, int)
            or isinstance(identity_version, bool)
            or identity_version < 1
        ):
            raise EventJobError("事件作业身份版本无效")
        reference_id = stored.get("reference_id")
        if reference_id is not None and (
            not isinstance(reference_id, str)
            or not _REFERENCE_ID_RE.fullmatch(reference_id)
        ):
            raise EventJobError("事件作业参考标识无效")
        purpose = stored.get("purpose")
        operation = stored.get("operation")
        if (purpose, operation, reference_id is not None) not in {
            ("prototype", "generation", False),
            ("photo", "edit", True),
        }:
            raise EventJobError("事件作业图片目的与操作无效")
        plan_id = stored.get("authorization_plan_id")
        if not isinstance(plan_id, str) or not _PLAN_ID_RE.fullmatch(plan_id):
            raise EventJobError("事件作业授权标识无效")
        asset_id = stored.get("asset_id")
        if asset_id is not None and (
            not isinstance(asset_id, str)
            or not _ASSET_ID_RE.fullmatch(asset_id)
            or (purpose == "prototype" and not asset_id.startswith("cand_"))
            or (purpose == "photo" and not asset_id.startswith("art_"))
        ):
            raise EventJobError("事件作业图片资产标识无效")
        handoff_artifact_id = stored.get("handoff_artifact_id")
        if handoff_artifact_id is not None and (
            not isinstance(handoff_artifact_id, str)
            or not _ASSET_ID_RE.fullmatch(handoff_artifact_id)
            or not handoff_artifact_id.startswith("art_")
        ):
            raise EventJobError("事件作业交付快照标识无效")
        stage = stored["stage"]
        if (
            stage in {"planned", "generating", "failed"}
            and asset_id is not None
        ) or (
            stage in {"generated", "delivery_unknown", "delivered"}
            and asset_id is None
        ):
            raise EventJobError("事件作业状态与图片资产不一致")
        if (
            stage in {"planned", "generating", "generated", "failed"}
            and handoff_artifact_id is not None
        ) or (
            stage in {"delivery_unknown", "delivered"}
            and handoff_artifact_id is None
        ):
            raise EventJobError("事件作业状态与交付快照不一致")
        if handoff_artifact_id is not None and (
            (purpose == "photo" and handoff_artifact_id != asset_id)
            or (purpose == "prototype" and handoff_artifact_id == asset_id)
        ):
            raise EventJobError("事件作业图片与交付快照不一致")
        created_at = _parse_time(stored.get("created_at"), "创建时间")
        updated_at = _parse_time(stored.get("updated_at"), "更新时间")
        expires_at = _parse_time(stored.get("expires_at"), "过期时间")
        if not created_at <= updated_at <= expires_at:
            raise EventJobError("事件作业时间顺序无效")
        return stored

    def _to_job(self, stored: dict[str, object]) -> EventJob:
        return EventJob(
            job_id=str(stored["job_id"]),
            host=str(stored["host"]),
            profile_id=str(stored["profile_id"]),
            identity_version=int(stored["identity_version"]),
            reference_id=(
                str(stored["reference_id"])
                if stored["reference_id"] is not None
                else None
            ),
            purpose=str(stored["purpose"]),
            operation=str(stored["operation"]),
            authorization_plan_id=str(stored["authorization_plan_id"]),
            asset_id=str(stored["asset_id"]) if stored["asset_id"] else None,
            handoff_artifact_id=(
                str(stored["handoff_artifact_id"])
                if stored["handoff_artifact_id"]
                else None
            ),
            stage=str(stored["stage"]),
            created_at=_parse_time(stored["created_at"], "创建时间"),
            updated_at=_parse_time(stored["updated_at"], "更新时间"),
            expires_at=_parse_time(stored["expires_at"], "过期时间"),
        )

    def _prune_locked(self, now: datetime) -> int:
        removed = 0
        for path in self.root.iterdir():
            if path.name == self._lock_path.name or path.name.startswith(
                ".companion-event-"
            ):
                continue
            if not path.name.startswith("job_") or path.suffix != ".json":
                raise EventJobError("事件作业目录包含未知内容")
            if path.is_symlink() or not path.is_file():
                raise EventJobError("事件作业目录包含无效记录")
            stored = self._load_locked(path)
            if now >= _parse_time(stored["expires_at"], "过期时间"):
                path.unlink()
                removed += 1
        return removed

    def prune_expired(self) -> int:
        try:
            with self._locked():
                return self._prune_locked(self._now())
        except (OSError, InterprocessLockError) as exc:
            raise EventJobError(f"无法清理事件作业：{exc}") from exc

    def create(
        self,
        *,
        context: EventJobContext,
        shape: EventJobShape,
        authorization_plan_id: str,
    ) -> EventJob:
        context_values = _context_values(context)
        shape_values = _shape_values(shape)
        if not _PLAN_ID_RE.fullmatch(str(authorization_plan_id or "")):
            raise EventJobError("授权计划标识无效")
        event_key = "\0".join(
            (
                str(context_values["host"]),
                str(context_values["instance_digest"]),
                str(context_values["conversation_digest"]),
                str(context_values["request_event_digest"]),
            )
        )
        job_id = f"job_{sha256(event_key.encode('utf-8')).hexdigest()[:24]}"
        now = self._now()
        stored: dict[str, object] = {
            "schema_version": 1,
            "job_id": job_id,
            **context_values,
            **shape_values,
            "authorization_plan_id": authorization_plan_id,
            "asset_id": None,
            "handoff_artifact_id": None,
            "stage": "planned",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "expires_at": (now + self._ttl).isoformat(),
        }
        path = self._path(job_id)
        try:
            with self._locked():
                self._prune_locked(now)
                if path.exists() or path.is_symlink():
                    raise EventJobError("同一入站事件已经处理，不能重复创建图片作业")
                self._write_locked(path, stored)
        except EventJobError:
            raise
        except (OSError, InterprocessLockError) as exc:
            raise EventJobError(f"无法创建事件作业：{exc}") from exc
        return self._to_job(stored)

    def read(self, job_id: str) -> EventJob:
        try:
            with self._locked():
                stored = self._load_locked(self._path(job_id))
                if self._now() >= _parse_time(stored["expires_at"], "过期时间"):
                    self._path(job_id).unlink(missing_ok=True)
                    raise EventJobError("事件作业已经过期")
                return self._to_job(stored)
        except EventJobError:
            raise
        except (OSError, InterprocessLockError) as exc:
            raise EventJobError(f"无法读取事件作业：{exc}") from exc

    @staticmethod
    def _assert_context(
        stored: dict[str, object],
        context: EventJobContext,
    ) -> None:
        expected = _context_values(context)
        continuation_keys = ("host", "instance_digest", "conversation_digest")
        if any(stored.get(key) != expected[key] for key in continuation_keys):
            raise EventJobError("事件作业不属于当前宿主会话")

    @staticmethod
    def _assert_shape(stored: dict[str, object], shape: EventJobShape) -> None:
        expected = _shape_values(shape)
        if any(stored.get(key) != value for key, value in expected.items()):
            raise EventJobError("事件作业与当前照片请求不一致")

    def _update(
        self,
        *,
        job_id: str,
        context: EventJobContext,
        expected_stage: str,
        target_stage: str,
        shape: EventJobShape | None = None,
        authorization_plan_id: str | None = None,
        asset_id: str | None = None,
        handoff_artifact_id: str | None = None,
    ) -> EventJob:
        path = self._path(job_id)
        try:
            with self._locked():
                stored = self._load_locked(path)
                now = self._now()
                if now >= _parse_time(stored["expires_at"], "过期时间"):
                    path.unlink(missing_ok=True)
                    raise EventJobError("事件作业已经过期")
                self._assert_context(stored, context)
                if shape is not None:
                    self._assert_shape(stored, shape)
                if (
                    authorization_plan_id is not None
                    and stored.get("authorization_plan_id") != authorization_plan_id
                ):
                    raise EventJobError("事件作业与单次授权不一致")
                if stored.get("stage") != expected_stage:
                    raise EventJobError(
                        f"事件作业当前状态不能重复执行 {target_stage}"
                    )
                if asset_id is not None:
                    if not _ASSET_ID_RE.fullmatch(str(asset_id or "")):
                        raise EventJobError("图片资产标识无效")
                    expected_prefix = (
                        "cand_" if stored.get("purpose") == "prototype" else "art_"
                    )
                    if not asset_id.startswith(expected_prefix):
                        raise EventJobError("图片资产与作业目的不一致")
                    existing = stored.get("asset_id")
                    if existing is not None and existing != asset_id:
                        raise EventJobError("事件作业已经绑定其他图片资产")
                    stored["asset_id"] = asset_id
                if handoff_artifact_id is not None:
                    if (
                        not _ASSET_ID_RE.fullmatch(str(handoff_artifact_id or ""))
                        or not handoff_artifact_id.startswith("art_")
                    ):
                        raise EventJobError("交付快照标识无效")
                    existing_handoff = stored.get("handoff_artifact_id")
                    if (
                        existing_handoff is not None
                        and existing_handoff != handoff_artifact_id
                    ):
                        raise EventJobError("事件作业已经绑定其他交付快照")
                    original_asset_id = stored.get("asset_id")
                    if (
                        stored.get("purpose") == "photo"
                        and handoff_artifact_id != original_asset_id
                    ) or (
                        stored.get("purpose") == "prototype"
                        and handoff_artifact_id == original_asset_id
                    ):
                        raise EventJobError("交付快照与图片资产不一致")
                    stored["handoff_artifact_id"] = handoff_artifact_id
                stored["stage"] = target_stage
                stored["updated_at"] = now.isoformat()
                self._write_locked(path, stored)
                return self._to_job(stored)
        except EventJobError:
            raise
        except (OSError, InterprocessLockError) as exc:
            raise EventJobError(f"无法更新事件作业：{exc}") from exc

    def claim_generation(
        self,
        *,
        job_id: str,
        context: EventJobContext,
        shape: EventJobShape,
        authorization_plan_id: str,
    ) -> EventJob:
        return self._update(
            job_id=job_id,
            context=context,
            shape=shape,
            authorization_plan_id=authorization_plan_id,
            expected_stage="planned",
            target_stage="generating",
        )

    def mark_generated(
        self,
        *,
        job_id: str,
        context: EventJobContext,
        asset_id: str,
    ) -> EventJob:
        return self._update(
            job_id=job_id,
            context=context,
            expected_stage="generating",
            target_stage="generated",
            asset_id=asset_id,
        )

    def claim_handoff(
        self,
        *,
        job_id: str,
        context: EventJobContext,
        asset_id: str,
        handoff_artifact_id: str,
    ) -> EventJob:
        return self._update(
            job_id=job_id,
            context=context,
            expected_stage="generated",
            target_stage="delivery_unknown",
            asset_id=asset_id,
            handoff_artifact_id=handoff_artifact_id,
        )

    def mark_delivered(
        self,
        *,
        job_id: str,
        context: EventJobContext,
        asset_id: str,
    ) -> EventJob:
        return self._update(
            job_id=job_id,
            context=context,
            expected_stage="delivery_unknown",
            target_stage="delivered",
            asset_id=asset_id,
        )

    def mark_failed(
        self,
        *,
        job_id: str,
        context: EventJobContext,
    ) -> EventJob:
        job = self.read(job_id)
        if job.stage not in {"planned", "generating"}:
            raise EventJobError("事件作业当前状态不能标记失败")
        return self._update(
            job_id=job_id,
            context=context,
            expected_stage=job.stage,
            target_stage="failed",
        )
