from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
from typing import Callable, Iterable
from uuid import uuid4

from .config import ConfigError, load_profile
from .daily_look_store import DailyLookStore, DailyLookStoreError
from .file_lock import InterprocessLockError, exclusive_file_lock
from .image_assets import ImageAssetError, ImageAssetStore
from .initializer import InitializationError, default_profile_path, safe_profile_path


BACKUP_SCHEMA_VERSION = 1


class BackupError(ValueError):
    """无法安全检查、备份或恢复 Companion Kit 本地数据。"""


@dataclass(frozen=True)
class CompanionDataLayout:
    """Codex 用户数据的稳定边界；程序代码和 Plugin 缓存不属于这里。"""

    root: Path
    profiles_root: Path
    profile_path: Path
    private_root: Path
    images_root: Path
    photo_moments_root: Path
    daily_looks_root: Path
    relationship_database: Path
    backups_root: Path
    system_root: Path

    @classmethod
    def for_codex(
        cls,
        *,
        data_root: str | Path | None = None,
    ) -> CompanionDataLayout:
        try:
            if data_root is None:
                profile_path = default_profile_path("codex")
                root = profile_path.parents[1]
            else:
                root = _safe_path(data_root)
                profile_path = root / "profiles" / "default.toml"
            root = _safe_path(root)
        except InitializationError as exc:
            raise BackupError(str(exc)) from exc
        if root == Path(root.anchor):
            raise BackupError("数据根目录不能是文件系统根目录")
        return cls(
            root=root,
            profiles_root=root / "profiles",
            profile_path=profile_path,
            private_root=root / "private",
            images_root=root / "private" / "images",
            photo_moments_root=root / "private" / "photo-moments",
            daily_looks_root=root / "private" / "daily-looks",
            relationship_database=root / "private" / "relationships.sqlite3",
            backups_root=root / "backups",
            system_root=root / "system",
        )

    @classmethod
    def for_profile(cls, profile_path: str | Path) -> CompanionDataLayout:
        try:
            profile = _safe_path(profile_path)
            parent = profile.parent
            root = parent.parent if parent.name == "profiles" else parent
            root = _safe_path(root)
        except InitializationError as exc:
            raise BackupError(str(exc)) from exc
        if root == Path(root.anchor):
            raise BackupError("数据根目录不能是文件系统根目录")
        return cls(
            root=root,
            profiles_root=parent,
            profile_path=profile,
            private_root=root / "private",
            images_root=root / "private" / "images",
            photo_moments_root=root / "private" / "photo-moments",
            daily_looks_root=root / "private" / "daily-looks",
            relationship_database=root / "private" / "relationships.sqlite3",
            backups_root=root / "backups",
            system_root=root / "system",
        )


@dataclass(frozen=True)
class DataInventory:
    profile_schema: int | None
    relationship_schema: int | None
    identity_configured: bool
    durable_file_count: int
    durable_bytes: int
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def has_durable_data(self) -> bool:
        return self.durable_file_count > 0

    @property
    def healthy(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_schema": self.profile_schema,
            "relationship_schema": self.relationship_schema,
            "identity_configured": self.identity_configured,
            "durable_file_count": self.durable_file_count,
            "durable_bytes": self.durable_bytes,
            "has_durable_data": self.has_durable_data,
            "healthy": self.healthy,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class BackupSnapshot:
    backup_id: str
    path: Path
    manifest: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "backup_id": self.backup_id,
            "created_at": self.manifest["created_at"],
            "product_version": self.manifest["product_version"],
            "profile_schema": self.manifest["profile_schema"],
            "relationship_schema": self.manifest["relationship_schema"],
            "item_count": len(self.manifest["items"]),  # type: ignore[arg-type]
            "verified": True,
        }


@dataclass(frozen=True)
class BackupVerification:
    backup_id: str
    valid: bool
    item_count: int
    total_bytes: int

    def to_dict(self) -> dict[str, object]:
        return {
            "backup_id": self.backup_id,
            "valid": self.valid,
            "item_count": self.item_count,
            "total_bytes": self.total_bytes,
        }


@dataclass(frozen=True)
class _SourceFile:
    relative_path: str
    source: Path
    size: int
    sha256: str
    kind: str

    def signature(self) -> tuple[str, int, str, str]:
        return (self.relative_path, self.size, self.sha256, self.kind)


def _safe_path(raw: str | Path) -> Path:
    try:
        return safe_profile_path(raw)
    except InitializationError as exc:
        raise BackupError(str(exc)) from exc


def _private_directory(path: Path) -> None:
    _safe_path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _safe_path(path)
    if not path.is_dir() or path.is_symlink():
        raise BackupError("私有数据目录必须是普通目录")
    if os.name != "nt":
        path.chmod(0o700)


def _hash_file(path: Path) -> tuple[int, str]:
    digest = sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
    except OSError as exc:
        raise BackupError(f"无法读取待备份数据：{exc}") from exc
    return size, digest.hexdigest()


def _kind_for(relative_path: str) -> str:
    if relative_path.startswith("profiles/"):
        return "persona"
    if relative_path == "private/relationships.sqlite3":
        return "relationship_database"
    if relative_path.startswith("private/photo-moments/"):
        return "photo_moment"
    if relative_path.startswith("private/daily-looks/"):
        return "daily_look"
    return "identity_asset"


def _iter_tree_files(
    root: Path,
    *,
    data_root: Path,
    excluded_parts: Iterable[str] = (),
) -> tuple[_SourceFile, ...]:
    if not root.exists():
        return ()
    _safe_path(root)
    if root.is_symlink() or not root.is_dir():
        raise BackupError("待备份数据目录必须是普通目录，不能是符号链接")
    excluded = set(excluded_parts)
    result: list[_SourceFile] = []
    try:
        paths = sorted(root.rglob("*"))
    except OSError as exc:
        raise BackupError(f"无法枚举待备份数据：{exc}") from exc
    for path in paths:
        relative_to_tree = path.relative_to(root)
        if any(part in excluded for part in relative_to_tree.parts):
            continue
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise BackupError(f"无法检查待备份数据：{exc}") from exc
        if stat.S_ISLNK(mode):
            raise BackupError("待备份数据不能包含符号链接")
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise BackupError("待备份数据只能包含普通文件")
        if path.name.endswith(".lock"):
            continue
        size, digest = _hash_file(path)
        relative = str(path.relative_to(data_root))
        result.append(
            _SourceFile(
                relative_path=relative,
                source=path,
                size=size,
                sha256=digest,
                kind=_kind_for(relative),
            )
        )
    return tuple(result)


def _database_status(path: Path) -> tuple[int | None, str | None]:
    if not path.exists():
        return None, None
    _safe_path(path)
    if path.is_symlink() or not path.is_file():
        return None, "关系数据库必须是普通文件，不能是符号链接"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or str(integrity[0]).casefold() != "ok":
            return None, "关系数据库完整性校验失败"
        row = connection.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            return None, "关系数据库缺少 schema_version"
        version = int(row[0])
        if version < 1:
            return None, "关系数据库 schema_version 无效"
        return version, None
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        return None, f"关系数据库无法只读检查：{exc}"
    finally:
        if connection is not None:
            connection.close()


def _copy_regular_file(source: Path, destination: Path) -> tuple[int, str]:
    _safe_path(source)
    try:
        mode = source.lstat().st_mode
    except OSError as exc:
        raise BackupError(f"无法检查待备份文件：{exc}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise BackupError("待备份文件必须是普通文件，不能是符号链接")
    _private_directory(destination.parent)
    digest = sha256()
    size = 0
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(destination, flags, 0o600)
        with source.open("rb") as input_handle, os.fdopen(
            descriptor,
            "wb",
        ) as output_handle:
            while True:
                chunk = input_handle.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
                output_handle.write(chunk)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        if os.name != "nt":
            destination.chmod(0o600)
    except OSError as exc:
        raise BackupError(f"无法写入备份文件：{exc}") from exc
    return size, digest.hexdigest()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    _private_directory(path.parent)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise BackupError(f"无法写入备份清单：{exc}") from exc


def _load_manifest(path: Path) -> dict[str, object]:
    _safe_path(path)
    if path.is_symlink() or not path.is_file():
        raise BackupError("备份缺少 manifest.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("备份清单无法读取") from exc
    if not isinstance(raw, dict):
        raise BackupError("备份清单格式无效")
    required = {
        "schema_version",
        "backup_id",
        "created_at",
        "product_version",
        "host",
        "profile_schema",
        "relationship_schema",
        "source_health",
        "excluded_transient",
        "items",
    }
    if set(raw) != required or raw.get("schema_version") != BACKUP_SCHEMA_VERSION:
        raise BackupError("备份清单版本或字段无效")
    if not isinstance(raw.get("items"), list):
        raise BackupError("备份清单 items 无效")
    return raw


class BackupManager:
    """只处理耐久用户数据；不更新程序，也不覆盖正在使用的数据。"""

    def __init__(
        self,
        layout: CompanionDataLayout,
        *,
        product_version: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.layout = layout
        self.product_version = str(product_version or "").strip()
        if not self.product_version:
            raise BackupError("product_version 不能为空")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock_path = self.layout.system_root / ".upgrade.lock"

    def _source_files(self) -> tuple[_SourceFile, ...]:
        if self.layout.profiles_root == self.layout.root:
            profile_files: tuple[_SourceFile, ...] = ()
            profile_path = self.layout.profile_path
            if profile_path.exists():
                _safe_path(profile_path)
                if profile_path.is_symlink() or not profile_path.is_file():
                    raise BackupError(
                        "待备份 Persona 必须是普通文件，不能是符号链接"
                    )
                size, digest = _hash_file(profile_path)
                profile_files = (
                    _SourceFile(
                        relative_path=str(profile_path.relative_to(self.layout.root)),
                        source=profile_path,
                        size=size,
                        sha256=digest,
                        kind="persona",
                    ),
                )
        else:
            profile_files = _iter_tree_files(
                self.layout.profiles_root,
                data_root=self.layout.root,
            )
        image_files = _iter_tree_files(
            self.layout.images_root,
            data_root=self.layout.root,
            excluded_parts=("runtime",),
        )
        photo_moment_files = _iter_tree_files(
            self.layout.photo_moments_root,
            data_root=self.layout.root,
            excluded_parts=("runtime",),
        )
        daily_look_files = _iter_tree_files(
            self.layout.daily_looks_root,
            data_root=self.layout.root,
            excluded_parts=("runtime",),
        )
        return tuple(
            sorted(
                (
                    *profile_files,
                    *image_files,
                    *photo_moment_files,
                    *daily_look_files,
                ),
                key=lambda item: item.relative_path,
            )
        )

    def inspect(self) -> DataInventory:
        blockers: list[str] = []
        warnings: list[str] = []
        profile_schema: int | None = None
        identity_configured = False
        profile_id: str | None = None

        if self.layout.profile_path.exists():
            try:
                _safe_path(self.layout.profile_path)
                profile = load_profile(self.layout.profile_path)
                profile_schema = profile.schema_version
                profile_id = profile.id
                identity_configured = profile.visual.is_locked
                if identity_configured:
                    if not self.layout.images_root.is_dir():
                        blockers.append("Persona 已固定人物身份，但参考图目录不存在")
                    else:
                        ImageAssetStore(self.layout.images_root).resolve_identity_pack(
                            primary_reference_id=profile.visual.reference_ids[0],
                            profile_id=profile.id,
                            identity_version=profile.visual.identity_version,
                        )
            except (OSError, ConfigError, InitializationError) as exc:
                blockers.append(f"Persona 无法安全读取：{exc}")
            except ImageAssetError as exc:
                blockers.append(f"人物身份包无法安全读取：{exc}")

        if profile_id is not None and self.layout.daily_looks_root.exists():
            try:
                DailyLookStore(self.layout.daily_looks_root).inspect(
                    profile_id=profile_id
                )
            except DailyLookStoreError as exc:
                blockers.append(f"每日穿搭记录无法安全读取：{exc}")

        relationship_schema, database_error = _database_status(
            self.layout.relationship_database
        )
        if database_error:
            blockers.append(database_error)

        try:
            source_files = self._source_files()
        except BackupError as exc:
            source_files = ()
            blockers.append(str(exc))

        durable_bytes = sum(item.size for item in source_files)
        durable_count = len(source_files)
        if self.layout.relationship_database.exists():
            try:
                durable_bytes += self.layout.relationship_database.stat().st_size
                durable_count += 1
            except OSError as exc:
                blockers.append(f"无法检查关系数据库大小：{exc}")
        if (self.layout.images_root / "runtime").exists():
            warnings.append("短期成图目录不会进入长期恢复点，也不会在升级时被删除")
        return DataInventory(
            profile_schema=profile_schema,
            relationship_schema=relationship_schema,
            identity_configured=identity_configured,
            durable_file_count=durable_count,
            durable_bytes=durable_bytes,
            blockers=tuple(blockers),
            warnings=tuple(warnings),
        )

    def _backup_database(self, destination: Path) -> tuple[int, str, int | None]:
        source = self.layout.relationship_database
        if not source.exists():
            return 0, "", None
        _safe_path(source)
        if source.is_symlink() or not source.is_file():
            raise BackupError("关系数据库必须是普通文件，不能是符号链接")
        _private_directory(destination.parent)
        source_connection: sqlite3.Connection | None = None
        target_connection: sqlite3.Connection | None = None
        try:
            source_connection = sqlite3.connect(
                f"{source.as_uri()}?mode=ro",
                uri=True,
            )
            target_connection = sqlite3.connect(destination)
            source_connection.backup(target_connection)
            target_connection.commit()
            integrity = target_connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or str(integrity[0]).casefold() != "ok":
                raise BackupError("关系数据库备份完整性校验失败")
            row = target_connection.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
            schema = int(row[0]) if row is not None else None
        except BackupError:
            raise
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            raise BackupError(f"无法创建关系数据库一致性备份：{exc}") from exc
        finally:
            if target_connection is not None:
                target_connection.close()
            if source_connection is not None:
                source_connection.close()
        if os.name != "nt":
            destination.chmod(0o600)
        size, digest = _hash_file(destination)
        return size, digest, schema

    def create(self, *, max_attempts: int = 3) -> BackupSnapshot:
        if max_attempts < 1 or max_attempts > 5:
            raise BackupError("max_attempts 必须在 1 到 5 之间")
        _private_directory(self.layout.system_root)
        _private_directory(self.layout.backups_root)
        try:
            with exclusive_file_lock(self._lock_path, timeout=5.0):
                return self._create_locked(max_attempts=max_attempts)
        except (OSError, InterprocessLockError) as exc:
            if isinstance(exc, BackupError):
                raise
            raise BackupError(f"无法取得升级备份锁：{exc}") from exc

    def _create_locked(self, *, max_attempts: int) -> BackupSnapshot:
        now = self._clock()
        if now.tzinfo is None:
            raise BackupError("备份时间必须包含时区")
        created_at = now.astimezone(UTC)
        backup_id = (
            created_at.strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid4().hex[:8]
        )
        destination = self.layout.backups_root / backup_id
        if destination.exists() or destination.is_symlink():
            raise BackupError("备份目标已经存在")

        last_change_error: BackupError | None = None
        for _ in range(max_attempts):
            staging = Path(
                tempfile.mkdtemp(
                    prefix=".backup-staging-",
                    dir=self.layout.backups_root,
                )
            )
            try:
                before = self._source_files()
                inventory = self.inspect()
                items: list[dict[str, object]] = []
                for source_item in before:
                    target = staging / source_item.relative_path
                    size, digest = _copy_regular_file(source_item.source, target)
                    if size != source_item.size or digest != source_item.sha256:
                        raise BackupError("备份复制校验失败")
                    items.append(
                        {
                            "relative_path": source_item.relative_path,
                            "kind": source_item.kind,
                            "size": size,
                            "sha256": digest,
                        }
                    )

                relationship_schema = inventory.relationship_schema
                if self.layout.relationship_database.exists():
                    relative = "private/relationships.sqlite3"
                    size, digest, relationship_schema = self._backup_database(
                        staging / relative
                    )
                    items.append(
                        {
                            "relative_path": relative,
                            "kind": "relationship_database",
                            "size": size,
                            "sha256": digest,
                        }
                    )

                after = self._source_files()
                if tuple(item.signature() for item in before) != tuple(
                    item.signature() for item in after
                ):
                    raise BackupError("备份期间用户资料发生变化，请重试")

                manifest: dict[str, object] = {
                    "schema_version": BACKUP_SCHEMA_VERSION,
                    "backup_id": backup_id,
                    "created_at": created_at.isoformat(),
                    "product_version": self.product_version,
                    "host": "codex",
                    "profile_schema": inventory.profile_schema,
                    "relationship_schema": relationship_schema,
                    "source_health": {
                        "healthy": inventory.healthy,
                        "blockers": list(inventory.blockers),
                        "warnings": list(inventory.warnings),
                    },
                    "excluded_transient": [
                        "private/images/runtime",
                        "private/codex-image-receipts",
                        "private/photo-moments/runtime",
                        "private/daily-looks/runtime",
                    ],
                    "items": sorted(items, key=lambda item: str(item["relative_path"])),
                }
                _write_json(staging / "manifest.json", manifest)
                staging.rename(destination)
                snapshot = BackupSnapshot(
                    backup_id=backup_id,
                    path=destination,
                    manifest=manifest,
                )
                try:
                    self.verify(backup_id)
                except BackupError:
                    shutil.rmtree(destination, ignore_errors=True)
                    raise
                return snapshot
            except BackupError as exc:
                last_change_error = exc
            except OSError as exc:
                last_change_error = BackupError(f"无法完成恢复点切换：{exc}")
            finally:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
            if last_change_error is not None and "发生变化" not in str(
                last_change_error
            ):
                raise last_change_error
        raise last_change_error or BackupError("无法创建稳定恢复点")

    def _snapshot(self, backup_id: str) -> BackupSnapshot:
        normalized = str(backup_id or "").strip()
        if (
            not normalized
            or "/" in normalized
            or "\\" in normalized
            or normalized in {".", ".."}
        ):
            raise BackupError("backup_id 无效")
        path = _safe_path(self.layout.backups_root / normalized)
        try:
            path.relative_to(self.layout.backups_root)
        except ValueError as exc:
            raise BackupError("backup_id 超出备份目录") from exc
        if path.is_symlink() or not path.is_dir():
            raise BackupError("恢复点不存在")
        manifest = _load_manifest(path / "manifest.json")
        if manifest.get("backup_id") != normalized:
            raise BackupError("恢复点名称与清单不一致")
        return BackupSnapshot(normalized, path, manifest)

    def verify(self, backup_id: str) -> BackupVerification:
        snapshot = self._snapshot(backup_id)
        total_bytes = 0
        seen: set[str] = set()
        items = snapshot.manifest["items"]
        assert isinstance(items, list)
        for raw_item in items:
            if not isinstance(raw_item, dict) or set(raw_item) != {
                "relative_path",
                "kind",
                "size",
                "sha256",
            }:
                raise BackupError("备份条目格式无效")
            relative_text = str(raw_item["relative_path"])
            relative = Path(relative_text)
            if (
                not relative_text
                or relative.is_absolute()
                or ".." in relative.parts
                or relative_text in seen
            ):
                raise BackupError("备份条目路径校验失败")
            seen.add(relative_text)
            path = _safe_path(snapshot.path / relative)
            try:
                path.relative_to(snapshot.path)
            except ValueError as exc:
                raise BackupError("备份条目超出恢复点目录") from exc
            if path.is_symlink() or not path.is_file():
                raise BackupError("备份条目缺失或不是普通文件")
            size, digest = _hash_file(path)
            try:
                expected_size = int(raw_item["size"])
            except (TypeError, ValueError) as exc:
                raise BackupError("备份条目大小无效") from exc
            if size != expected_size or digest != raw_item["sha256"]:
                raise BackupError("备份内容校验失败")
            total_bytes += size
            if raw_item["kind"] == "relationship_database":
                schema, error = _database_status(path)
                if error or schema != snapshot.manifest["relationship_schema"]:
                    raise BackupError("关系数据库备份校验失败")
        return BackupVerification(
            backup_id=backup_id,
            valid=True,
            item_count=len(items),
            total_bytes=total_bytes,
        )

    def list(self) -> tuple[BackupSnapshot, ...]:
        if not self.layout.backups_root.exists():
            return ()
        _safe_path(self.layout.backups_root)
        snapshots: list[BackupSnapshot] = []
        try:
            entries = sorted(self.layout.backups_root.iterdir(), reverse=True)
        except OSError as exc:
            raise BackupError(f"无法列出恢复点：{exc}") from exc
        for path in entries:
            if path.name.startswith("."):
                continue
            if path.is_symlink() or not path.is_dir():
                raise BackupError("备份目录包含未知条目")
            snapshot = self._snapshot(path.name)
            self.verify(snapshot.backup_id)
            snapshots.append(snapshot)
        return tuple(snapshots)

    def recover_copy(self, backup_id: str, destination: str | Path) -> Path:
        snapshot = self._snapshot(backup_id)
        self.verify(backup_id)
        raw_destination = Path(destination).expanduser()
        if ".." in raw_destination.parts:
            raise BackupError("恢复目标不能包含 ..")
        target = _safe_path(raw_destination)
        if target == Path(target.anchor):
            raise BackupError("恢复目标不能是文件系统根目录")
        if target == self.layout.root or self.layout.root in target.parents:
            raise BackupError("恢复副本不能写入正在使用的数据目录")
        if target.exists() or target.is_symlink():
            raise BackupError("恢复目标已经存在；不会覆盖现有文件")
        parent = _safe_path(target.parent)
        if parent.exists():
            if parent.is_symlink() or not parent.is_dir():
                raise BackupError("恢复目标的父目录必须是普通目录")
        else:
            _private_directory(parent)
        staging = Path(
            tempfile.mkdtemp(prefix=".recovery-staging-", dir=parent)
        )
        try:
            items = snapshot.manifest["items"]
            assert isinstance(items, list)
            for item in items:
                assert isinstance(item, dict)
                relative = Path(str(item["relative_path"]))
                size, digest = _copy_regular_file(
                    snapshot.path / relative,
                    staging / relative,
                )
                if size != item["size"] or digest != item["sha256"]:
                    raise BackupError("恢复副本复制校验失败")
            staging.rename(target)
            return target
        except BackupError:
            raise
        except OSError as exc:
            raise BackupError(f"无法创建恢复副本：{exc}") from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
