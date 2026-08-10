from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
from typing import Iterable
import uuid

from .file_lock import InterprocessLockError, exclusive_file_lock
from .image_assets import (
    ImageAssetError,
    _atomic_write,
    _codex_generated_images_root,
    _private_directory,
    _safe_absolute_path,
    sanitize_png,
)
from .initializer import default_profile_path


class CodexImageReceiptError(ValueError):
    """Codex 图片结果无法证明来自当前任务。"""


_RECEIPT_KEYS = {
    "receipt_id",
    "path_sha256",
    "raw_sha256",
    "pixel_sha256",
    "tool_use_digest",
    "created_at",
}
_ROOT_KEYS = {"schema_version", "session_digest", "receipts"}
_MAX_RECEIPTS = 12
_MAX_RECEIPT_IMAGE_BYTES = 25 * 1024 * 1024
_DEFAULT_TTL = timedelta(hours=2)


def default_codex_receipt_root() -> Path:
    return default_profile_path("codex").parents[1] / "private" / "codex-image-receipts"


def _digest(value: str) -> str:
    normalized = str(value or "")
    if (
        not normalized
        or len(normalized) > 256
        or any(ord(character) < 32 for character in normalized)
    ):
        raise CodexImageReceiptError("Codex 当前任务标识无效")
    return sha256(normalized.encode("utf-8")).hexdigest()


def _safe_generated_file(raw_path: str | Path) -> tuple[Path, bytes, bytes]:
    path = _safe_absolute_path(raw_path)
    root = _codex_generated_images_root()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise CodexImageReceiptError("图片结果不在 Codex generated_images 中") from exc
    try:
        details = path.lstat()
        mode = details.st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise CodexImageReceiptError("Codex 图片结果必须是普通文件")
        if details.st_size <= 0 or details.st_size > _MAX_RECEIPT_IMAGE_BYTES:
            raise CodexImageReceiptError("Codex 图片结果大小超出限制")
        raw = path.read_bytes()
        sanitized, _, _ = sanitize_png(raw)
    except CodexImageReceiptError:
        raise
    except (OSError, ImageAssetError) as exc:
        raise CodexImageReceiptError("Codex 图片结果不是可安全使用的 PNG") from exc
    return path, raw, sanitized


def _candidate_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        if len(value) <= 8192 and not value.startswith("data:"):
            yield value
        return
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).casefold()
            if normalized in {
                "output_hint",
                "path",
                "file",
                "file_path",
                "image_path",
                "uri",
                "resource",
            }:
                yield from _candidate_strings(child)
            elif isinstance(child, (dict, list, tuple)):
                yield from _candidate_strings(child)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            yield from _candidate_strings(child)


def extract_codex_generated_paths(tool_response: object) -> tuple[Path, ...]:
    """只从图片工具回执的路径字段提取 Codex 默认生图文件。"""

    root = _codex_generated_images_root()
    root_text = str(root)
    found: list[Path] = []
    for raw in _candidate_strings(tool_response):
        text = raw.strip().removeprefix("file://")
        candidates = [text]
        for line in text.splitlines():
            index = line.find(root_text)
            if index >= 0:
                candidates.append(line[index:])
        for candidate in candidates:
            cleaned = candidate.strip().strip("`'\"").rstrip("。；;，,")
            try:
                path = _safe_absolute_path(cleaned)
                path.relative_to(root)
            except (ImageAssetError, ValueError, OSError):
                continue
            if path not in found:
                found.append(path)
    return tuple(found)


class CodexImageReceiptStore:
    """保存当前 Codex 任务中图片工具真实返回的短期、不透明回执。"""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        clock=None,
        ttl: timedelta = _DEFAULT_TTL,
        lock_timeout: float = 5.0,
    ) -> None:
        try:
            normalized_timeout = float(lock_timeout)
        except (TypeError, ValueError) as exc:
            raise CodexImageReceiptError("图片回执锁等待时间无效") from exc
        if not 0 < normalized_timeout <= 60:
            raise CodexImageReceiptError("图片回执锁等待时间无效")
        self.root = _safe_absolute_path(root or default_codex_receipt_root())
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ttl = ttl
        self._lock_path = self.root / ".receipts.lock"
        self._lock_timeout = normalized_timeout

    def _path(self, session_digest: str) -> Path:
        return self.root / f"{session_digest}.json"

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise CodexImageReceiptError("图片回执时间必须包含时区")
        return value.astimezone(UTC)

    def _load(self, session_digest: str) -> list[dict[str, str]]:
        path = self._path(session_digest)
        if not path.exists():
            return []
        try:
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise CodexImageReceiptError("Codex 图片回执必须是普通文件")
            raw = json.loads(path.read_text(encoding="utf-8"))
        except CodexImageReceiptError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CodexImageReceiptError("Codex 图片回执无效") from exc
        if (
            not isinstance(raw, dict)
            or set(raw) != _ROOT_KEYS
            or raw.get("schema_version") != 1
            or raw.get("session_digest") != session_digest
            or not isinstance(raw.get("receipts"), list)
        ):
            raise CodexImageReceiptError("Codex 图片回执结构无效")
        receipts: list[dict[str, str]] = []
        for item in raw["receipts"]:
            if (
                not isinstance(item, dict)
                or set(item) != _RECEIPT_KEYS
                or any(not isinstance(value, str) for value in item.values())
            ):
                raise CodexImageReceiptError("Codex 图片回执条目无效")
            receipts.append(item)
        return receipts

    def _active(self, receipts: Iterable[dict[str, str]], now: datetime) -> list[dict[str, str]]:
        active: list[dict[str, str]] = []
        for receipt in receipts:
            try:
                created = datetime.fromisoformat(receipt["created_at"])
            except ValueError:
                continue
            if created.tzinfo is None:
                continue
            created = created.astimezone(UTC)
            if timedelta(0) <= now - created <= self._ttl:
                active.append(receipt)
        return active[-_MAX_RECEIPTS:]

    def _save(self, session_digest: str, receipts: list[dict[str, str]]) -> None:
        path = self._path(session_digest)
        if not receipts:
            path.unlink(missing_ok=True)
            return
        payload = json.dumps(
            {
                "schema_version": 1,
                "session_digest": session_digest,
                "receipts": receipts[-_MAX_RECEIPTS:],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        _atomic_write(path, payload)

    def record(
        self,
        *,
        session_id: str,
        tool_use_id: str,
        paths: Iterable[str | Path],
    ) -> int:
        session_digest = _digest(session_id)
        tool_digest = _digest(tool_use_id)
        now = self._now()
        pending: list[dict[str, str]] = []
        for raw_path in paths:
            try:
                path, raw, sanitized = _safe_generated_file(raw_path)
            except CodexImageReceiptError:
                continue
            pending.append(
                {
                    "receipt_id": f"imgrec_{uuid.uuid4().hex[:24]}",
                    "path_sha256": sha256(str(path).encode("utf-8")).hexdigest(),
                    "raw_sha256": sha256(raw).hexdigest(),
                    "pixel_sha256": sha256(sanitized).hexdigest(),
                    "tool_use_digest": tool_digest,
                    "created_at": now.isoformat(),
                }
            )
        if not pending:
            return 0
        recorded = 0
        try:
            _private_directory(self.root)
            with exclusive_file_lock(
                self._lock_path,
                timeout=self._lock_timeout,
            ):
                receipts = self._active(self._load(session_digest), now)
                known = {(item["path_sha256"], item["raw_sha256"]) for item in receipts}
                for item in pending:
                    key = (item["path_sha256"], item["raw_sha256"])
                    if key in known:
                        continue
                    known.add(key)
                    receipts.append(item)
                    recorded += 1
                self._save(session_digest, receipts)
        except (OSError, InterprocessLockError) as exc:
            raise CodexImageReceiptError("无法保存 Codex 图片回执") from exc
        return recorded

    def consume(self, *, session_id: str, source_path: str | Path) -> bytes:
        session_digest = _digest(session_id)
        path, raw, sanitized = _safe_generated_file(source_path)
        path_digest = sha256(str(path).encode("utf-8")).hexdigest()
        raw_digest = sha256(raw).hexdigest()
        pixel_digest = sha256(sanitized).hexdigest()
        now = self._now()
        try:
            _private_directory(self.root)
            with exclusive_file_lock(
                self._lock_path,
                timeout=self._lock_timeout,
            ):
                receipts = self._active(self._load(session_digest), now)
                matched = next(
                    (
                        item
                        for item in receipts
                        if item["path_sha256"] == path_digest
                        and item["raw_sha256"] == raw_digest
                        and item["pixel_sha256"] == pixel_digest
                    ),
                    None,
                )
                if matched is None:
                    self._save(session_digest, receipts)
                    raise CodexImageReceiptError(
                        "候选原型没有当前 Codex 任务的图片工具回执"
                    )
                receipts.remove(matched)
                self._save(session_digest, receipts)
        except CodexImageReceiptError:
            raise
        except (OSError, InterprocessLockError) as exc:
            raise CodexImageReceiptError("无法核验 Codex 图片回执") from exc
        return raw

    def verify(self, *, session_id: str, source_path: str | Path) -> bytes:
        """只读证明图片来自当前任务；明确编辑可以重复引用，不消费候选回执。"""

        session_digest = _digest(session_id)
        path, raw, sanitized = _safe_generated_file(source_path)
        path_digest = sha256(str(path).encode("utf-8")).hexdigest()
        raw_digest = sha256(raw).hexdigest()
        pixel_digest = sha256(sanitized).hexdigest()
        receipts = self._active(self._load(session_digest), self._now())
        matched = any(
            item["path_sha256"] == path_digest
            and item["raw_sha256"] == raw_digest
            and item["pixel_sha256"] == pixel_digest
            for item in receipts
        )
        if not matched:
            raise CodexImageReceiptError(
                "上一张照片没有当前 Codex 任务的有效图片工具回执"
            )
        return raw

    def verify_latest(self, *, session_id: str, source_path: str | Path) -> bytes:
        """证明目标属于当前任务最近一次成功记录的图片工具调用。"""

        session_digest = _digest(session_id)
        path, raw, sanitized = _safe_generated_file(source_path)
        path_digest = sha256(str(path).encode("utf-8")).hexdigest()
        raw_digest = sha256(raw).hexdigest()
        pixel_digest = sha256(sanitized).hexdigest()
        receipts = self._active(self._load(session_digest), self._now())
        if not receipts:
            raise CodexImageReceiptError("当前 Codex 任务没有有效图片工具回执")
        latest_tool_digest = receipts[-1]["tool_use_digest"]
        matched = any(
            item["tool_use_digest"] == latest_tool_digest
            and item["path_sha256"] == path_digest
            and item["raw_sha256"] == raw_digest
            and item["pixel_sha256"] == pixel_digest
            for item in receipts
        )
        if not matched:
            raise CodexImageReceiptError(
                "编辑目标不是当前 Codex 任务最近一次成功图片结果"
            )
        return raw
