from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Callable
from uuid import uuid4

from .backup import BackupError, BackupManager, CompanionDataLayout
from .config import ConfigError, load_profile
from .file_lock import InterprocessLockError, exclusive_file_lock
from .image_assets import ImageAssetError, ImageAssetStore
from .initializer import InitializationError, safe_profile_path
from .public_bundle import inspect_public_tree, inspect_release_layout
from .state_store import RelationshipStore, StoreError
from .upgrade import (
    CodexInstallationReceipt,
    CodexUpgradePlanner,
    InstallationReceiptStore,
    UpgradeError,
    version_base,
)


_PROGRAM_SNAPSHOT_SCHEMA = 1
_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class ProgramSnapshot:
    snapshot_id: str
    plugin_version: str
    marketplace_name: str
    path: Path
    item_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "plugin_version": self.plugin_version,
            "marketplace_name": self.marketplace_name,
            "item_count": self.item_count,
            "verified": True,
        }


@dataclass(frozen=True)
class CodexUpgradeResult:
    from_version: str
    to_version: str
    backup_id: str
    program_snapshot_id: str
    applied: bool
    rolled_back: bool
    upgrade_registered: bool
    requires_new_task: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "host": "codex",
            "from_version": self.from_version,
            "to_version": self.to_version,
            "backup_id": self.backup_id,
            "program_snapshot_id": self.program_snapshot_id,
            "applied": self.applied,
            "rolled_back": self.rolled_back,
            "upgrade_registered": self.upgrade_registered,
            "durable_data_replaced": False,
            "requires_new_task": self.requires_new_task,
        }


@dataclass(frozen=True)
class _ProgramFile:
    relative_path: str
    size: int
    sha256: str
    executable: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "size": self.size,
            "sha256": self.sha256,
            "executable": self.executable,
        }


def _component(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if (
        not _COMPONENT_RE.fullmatch(normalized)
        or normalized in {".", ".."}
        or ".." in Path(normalized).parts
    ):
        raise UpgradeError(f"{label}无法安全用于本地缓存路径")
    return normalized


def _safe_path(path: str | Path) -> Path:
    try:
        return safe_profile_path(path)
    except InitializationError as exc:
        raise UpgradeError(str(exc)) from exc


def _private_directory(path: Path) -> None:
    _safe_path(path)
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise UpgradeError(f"无法创建升级私有目录：{exc}") from exc
    _safe_path(path)
    if path.is_symlink() or not path.is_dir():
        raise UpgradeError("升级私有目录必须是普通目录")
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
        raise UpgradeError(f"无法校验 Plugin 程序文件：{exc}") from exc
    return size, digest.hexdigest()


def _program_files(root: Path) -> tuple[_ProgramFile, ...]:
    _safe_path(root)
    if root.is_symlink() or not root.is_dir():
        raise UpgradeError("Codex Plugin 缓存不存在或不是普通目录")
    files: list[_ProgramFile] = []
    try:
        paths = sorted(root.rglob("*"))
    except OSError as exc:
        raise UpgradeError(f"无法枚举 Codex Plugin 缓存：{exc}") from exc
    for path in paths:
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise UpgradeError(f"无法检查 Codex Plugin 缓存：{exc}") from exc
        if stat.S_ISLNK(mode):
            raise UpgradeError("Codex Plugin 缓存不能包含符号链接")
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise UpgradeError("Codex Plugin 缓存只能包含普通文件")
        size, digest = _hash_file(path)
        files.append(
            _ProgramFile(
                relative_path=path.relative_to(root).as_posix(),
                size=size,
                sha256=digest,
                executable=bool(mode & 0o111),
            )
        )
    if not files:
        raise UpgradeError("Codex Plugin 缓存是空的")
    return tuple(files)


def _copy_program_tree(source: Path, destination: Path) -> tuple[_ProgramFile, ...]:
    if destination.exists() or destination.is_symlink():
        raise UpgradeError("Plugin 程序快照目标已经存在")
    source_files = _program_files(source)
    try:
        destination.mkdir(parents=True, mode=0o700)
        for item in source_files:
            source_path = source / item.relative_path
            target_path = destination / item.relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(target_path, flags, 0o700 if item.executable else 0o600)
            try:
                with source_path.open("rb") as source_handle, os.fdopen(
                    descriptor,
                    "wb",
                ) as target_handle:
                    descriptor = -1
                    shutil.copyfileobj(source_handle, target_handle, 1024 * 1024)
                    target_handle.flush()
                    os.fsync(target_handle.fileno())
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            if os.name != "nt":
                target_path.chmod(0o700 if item.executable else 0o600)
    except OSError as exc:
        raise UpgradeError(f"无法复制 Codex Plugin 程序：{exc}") from exc
    copied = _program_files(destination)
    if copied != source_files:
        raise UpgradeError("Codex Plugin 程序快照校验不一致")
    return copied


def _write_json(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise UpgradeError(f"无法写入程序快照清单：{exc}") from exc


class CodexProgramSnapshotStore:
    """保存升级前的 Codex 程序副本；不把 Persona 或照片混进来。"""

    def __init__(self, layout: CompanionDataLayout) -> None:
        self.layout = layout
        self.root = layout.system_root / "program-rollbacks"

    def _snapshot_path(self, snapshot_id: str) -> Path:
        normalized = _component(snapshot_id, "程序快照 ID")
        path = _safe_path(self.root / normalized)
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise UpgradeError("程序快照 ID 超出私有目录") from exc
        return path

    def create(
        self,
        *,
        snapshot_id: str,
        source: Path,
        plugin_version: str,
        marketplace_name: str,
    ) -> ProgramSnapshot:
        version = _component(plugin_version, "Plugin 版本")
        marketplace = _component(marketplace_name, "Marketplace 名称")
        destination = self._snapshot_path(snapshot_id)
        _private_directory(self.layout.system_root)
        _private_directory(self.root)
        if destination.exists() or destination.is_symlink():
            raise UpgradeError("程序快照已经存在")
        staging = Path(
            tempfile.mkdtemp(prefix=".program-staging-", dir=self.root)
        )
        try:
            files = _copy_program_tree(source, staging / "plugin")
            manifest_path = staging / "plugin" / ".codex-plugin" / "plugin.json"
            try:
                plugin_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise UpgradeError("旧 Plugin 清单无法读取") from exc
            if (
                not isinstance(plugin_manifest, dict)
                or plugin_manifest.get("name") != "companion-kit"
                or plugin_manifest.get("version") != version
            ):
                raise UpgradeError("旧 Plugin 缓存的名称或版本不匹配")
            manifest = {
                "schema_version": _PROGRAM_SNAPSHOT_SCHEMA,
                "snapshot_id": snapshot_id,
                "plugin_name": "companion-kit",
                "plugin_version": version,
                "marketplace_name": marketplace,
                "items": [item.to_dict() for item in files],
            }
            _write_json(staging / "manifest.json", manifest)
            staging.rename(destination)
            snapshot = self.verify(snapshot_id)
            return snapshot
        except UpgradeError:
            if destination.exists():
                shutil.rmtree(destination, ignore_errors=True)
            raise
        except OSError as exc:
            if destination.exists():
                shutil.rmtree(destination, ignore_errors=True)
            raise UpgradeError(f"无法发布程序快照：{exc}") from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def verify(self, snapshot_id: str) -> ProgramSnapshot:
        path = self._snapshot_path(snapshot_id)
        if path.is_symlink() or not path.is_dir():
            raise UpgradeError("程序快照不存在")
        manifest_path = path / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise UpgradeError("程序快照缺少清单")
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpgradeError("程序快照清单无法读取") from exc
        required = {
            "schema_version",
            "snapshot_id",
            "plugin_name",
            "plugin_version",
            "marketplace_name",
            "items",
        }
        if (
            not isinstance(raw, dict)
            or set(raw) != required
            or raw.get("schema_version") != _PROGRAM_SNAPSHOT_SCHEMA
            or raw.get("snapshot_id") != snapshot_id
            or raw.get("plugin_name") != "companion-kit"
            or not isinstance(raw.get("items"), list)
        ):
            raise UpgradeError("程序快照清单字段或版本无效")
        expected: list[_ProgramFile] = []
        seen: set[str] = set()
        for item in raw["items"]:
            if not isinstance(item, dict) or set(item) != {
                "relative_path",
                "size",
                "sha256",
                "executable",
            }:
                raise UpgradeError("程序快照条目格式无效")
            relative_text = str(item["relative_path"])
            relative = Path(relative_text)
            if (
                not relative_text
                or relative_text in seen
                or relative.is_absolute()
                or relative in {Path("."), Path("..")}
                or ".." in relative.parts
            ):
                raise UpgradeError("程序快照条目路径无效")
            seen.add(relative_text)
            if not isinstance(item["executable"], bool):
                raise UpgradeError("程序快照条目执行标记无效")
            try:
                size = int(item["size"])
            except (TypeError, ValueError) as exc:
                raise UpgradeError("程序快照条目大小无效") from exc
            digest = str(item["sha256"])
            if size < 0 or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise UpgradeError("程序快照条目摘要无效")
            expected.append(
                _ProgramFile(
                    relative_path=relative.as_posix(),
                    size=size,
                    sha256=digest,
                    executable=item["executable"],
                )
            )
        actual = _program_files(path / "plugin")
        if tuple(expected) != actual:
            raise UpgradeError("程序快照内容校验失败")
        return ProgramSnapshot(
            snapshot_id=snapshot_id,
            plugin_version=_component(str(raw["plugin_version"]), "Plugin 版本"),
            marketplace_name=_component(
                str(raw["marketplace_name"]),
                "Marketplace 名称",
            ),
            path=path,
            item_count=len(actual),
        )

    def restore(self, snapshot_id: str, *, codex_home: Path) -> ProgramSnapshot:
        snapshot = self.verify(snapshot_id)
        marketplace = _component(snapshot.marketplace_name, "Marketplace 名称")
        version = _component(snapshot.plugin_version, "Plugin 版本")
        cache_parent = _safe_path(
            codex_home
            / "plugins"
            / "cache"
            / marketplace
            / "companion-kit"
        )
        if cache_parent.is_symlink():
            raise UpgradeError("Codex Plugin 缓存目录不能是符号链接")
        try:
            cache_parent.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise UpgradeError(f"无法准备 Codex Plugin 回滚目录：{exc}") from exc
        held = cache_parent.parent / (
            f".companion-kit-upgrade-held-{snapshot_id}-{uuid4().hex[:8]}"
        )
        partial = cache_parent.parent / (
            f".companion-kit-rollback-partial-{snapshot_id}-{uuid4().hex[:8]}"
        )
        moved_current = False
        try:
            if cache_parent.exists():
                if not cache_parent.is_dir():
                    raise UpgradeError("Codex Plugin 缓存目标不是普通目录")
                cache_parent.rename(held)
                moved_current = True
            _copy_program_tree(snapshot.path / "plugin", cache_parent / version)
        except (OSError, UpgradeError) as exc:
            try:
                if cache_parent.exists():
                    cache_parent.rename(partial)
                if moved_current and held.exists():
                    held.rename(cache_parent)
            except OSError as restore_exc:
                raise UpgradeError(
                    "旧程序恢复失败，当前缓存已保留，请不要清理恢复点"
                ) from restore_exc
            if isinstance(exc, UpgradeError):
                raise
            raise UpgradeError(f"无法恢复旧 Codex Plugin：{exc}") from exc
        return snapshot


class CodexUpgradeExecutor:
    """执行本地 Codex Plugin 切换；耐久用户数据始终只读。"""

    def __init__(
        self,
        *,
        planner: CodexUpgradePlanner,
        backups: BackupManager,
        codex_home: str | Path | None = None,
        runner: Runner = subprocess.run,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.planner = planner
        self.backups = backups
        self.layout = backups.layout
        configured = os.environ.get("CODEX_HOME", "").strip()
        selected_home = (
            Path(codex_home).expanduser()
            if codex_home is not None
            else Path(configured).expanduser()
            if configured
            else Path.home() / ".codex"
        )
        self.codex_home = _safe_path(selected_home)
        self.runner = runner
        self._clock = clock or (lambda: datetime.now(UTC))
        self.program_snapshots = CodexProgramSnapshotStore(self.layout)
        self._apply_lock = self.layout.system_root / ".program-upgrade.lock"

    def _installed_cache_path(
        self,
        *,
        marketplace_name: str,
        installed_version: str,
    ) -> Path:
        marketplace = _component(marketplace_name, "Marketplace 名称")
        version = _component(installed_version, "Plugin 版本")
        path = _safe_path(
            self.codex_home
            / "plugins"
            / "cache"
            / marketplace
            / "companion-kit"
            / version
        )
        if path.is_symlink() or not path.is_dir():
            raise UpgradeError("找不到可校验的旧 Codex Plugin 缓存，升级已停止")
        return path

    def _validate_release(self) -> None:
        failures = inspect_release_layout(self.planner.plugin_root)
        failures.extend(inspect_public_tree(self.planner.plugin_root))
        if failures:
            raise UpgradeError(f"待安装项目未通过公开包检查：{failures[0]}")

    def _validate_shadow_copy(self, backup_id: str) -> None:
        shadow_parent = _safe_path(self.layout.root.parent)
        if shadow_parent.is_symlink() or not shadow_parent.is_dir():
            raise UpgradeError("无法准备恢复点副本验证目录")
        temporary = Path(
            tempfile.mkdtemp(
                prefix=".companion-upgrade-shadow-",
                dir=shadow_parent,
            )
        )
        if os.name != "nt":
            temporary.chmod(0o700)
        destination = temporary / "data"
        try:
            self.backups.recover_copy(backup_id, destination)
            shadow_layout = CompanionDataLayout.for_codex(data_root=destination)
            if shadow_layout.profile_path.exists():
                profile = load_profile(shadow_layout.profile_path)
                if profile.visual.is_locked:
                    ImageAssetStore(shadow_layout.images_root).resolve_identity_pack(
                        primary_reference_id=profile.visual.reference_ids[0],
                        profile_id=profile.id,
                        identity_version=profile.visual.identity_version,
                    )
            if shadow_layout.relationship_database.exists():
                RelationshipStore(
                    shadow_layout.relationship_database
                ).get_state("upgrade_probe")
            inventory = BackupManager(
                shadow_layout,
                product_version=self.backups.product_version,
            ).inspect()
            if not inventory.healthy:
                raise UpgradeError(inventory.blockers[0])
        except (BackupError, ConfigError, ImageAssetError, StoreError) as exc:
            raise UpgradeError(f"恢复点副本验证失败：{exc}") from exc
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def _save_receipt(
        self,
        *,
        plugin_version: str,
        marketplace_name: str,
        enabled: bool,
    ) -> None:
        now = self._clock()
        if now.tzinfo is None:
            raise UpgradeError("升级时间必须包含时区")
        InstallationReceiptStore(self.layout).save(
            CodexInstallationReceipt(
                plugin_version=plugin_version,
                marketplace_name=marketplace_name,
                marketplace_source_type="local",
                plugin_enabled=enabled,
                recorded_at=now.astimezone(UTC).isoformat(),
            )
        )

    def _try_save_receipt(
        self,
        *,
        plugin_version: str,
        marketplace_name: str,
        enabled: bool,
    ) -> bool:
        try:
            self._save_receipt(
                plugin_version=plugin_version,
                marketplace_name=marketplace_name,
                enabled=enabled,
            )
        except UpgradeError:
            return False
        return True

    def _restore_program(
        self,
        *,
        snapshot_id: str,
        expected_version: str,
    ) -> bool:
        snapshot = self.program_snapshots.restore(
            snapshot_id,
            codex_home=self.codex_home,
        )
        health = self.planner.check()
        if (
            health.installed_version is None
            or version_base(health.installed_version)
            != version_base(expected_version)
            or not health.plugin_enabled
        ):
            raise UpgradeError(
                "旧程序文件已经放回，但 Codex 未确认旧版本；请保留恢复点并停止继续更新"
            )
        return self._try_save_receipt(
            plugin_version=health.installed_version,
            marketplace_name=snapshot.marketplace_name,
            enabled=True,
        )

    def apply(self, *, confirm: bool) -> CodexUpgradeResult:
        if confirm is not True:
            raise UpgradeError("升级会替换 Codex Plugin，请先单独确认")
        plan = self.planner.plan()
        if not plan.apply_available:
            if not plan.check.update_candidate and plan.check.ready:
                raise UpgradeError("Codex 已经是当前项目版本")
            raise UpgradeError(plan.blockers[0] if plan.blockers else "当前升级计划不可执行")
        check = plan.check
        assert check.installed_version is not None
        assert check.marketplace_name is not None
        self._validate_release()
        old_cache = self._installed_cache_path(
            marketplace_name=check.marketplace_name,
            installed_version=check.installed_version,
        )
        backup = self.backups.create()
        self._validate_shadow_copy(backup.backup_id)
        _private_directory(self.layout.system_root)
        try:
            with exclusive_file_lock(self._apply_lock, timeout=5.0):
                current = self.planner.plan()
                if (
                    not current.apply_available
                    or current.check.installed_version != check.installed_version
                    or current.check.marketplace_name != check.marketplace_name
                ):
                    raise UpgradeError("升级期间 Codex Plugin 状态发生变化，已停止切换")
                program = self.program_snapshots.create(
                    snapshot_id=backup.backup_id,
                    source=old_cache,
                    plugin_version=check.installed_version,
                    marketplace_name=check.marketplace_name,
                )
                executable = self.planner.which("codex")
                if executable is None:
                    raise UpgradeError("未找到 Codex 命令，无法执行 Plugin 切换")
                try:
                    completed = self.runner(
                        [
                            executable,
                            "plugin",
                            "add",
                            f"companion-kit@{check.marketplace_name}",
                            "--json",
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    try:
                        self._restore_program(
                            snapshot_id=program.snapshot_id,
                            expected_version=check.installed_version,
                        )
                    except UpgradeError as rollback_exc:
                        raise UpgradeError(
                            f"Plugin 切换无法执行，自动回滚也未确认；恢复点 {backup.backup_id} 仍然有效"
                        ) from rollback_exc
                    raise UpgradeError(
                        f"Plugin 切换无法执行，已恢复旧程序；恢复点 {backup.backup_id} 已保留"
                    ) from exc
                if completed.returncode != 0:
                    try:
                        self._restore_program(
                            snapshot_id=program.snapshot_id,
                            expected_version=check.installed_version,
                        )
                    except UpgradeError as rollback_exc:
                        raise UpgradeError(
                            f"Plugin 切换失败且自动回滚未确认；恢复点 {backup.backup_id} 仍然有效"
                        ) from rollback_exc
                    raise UpgradeError(
                        f"Plugin 切换失败，已恢复旧程序；恢复点 {backup.backup_id} 已保留"
                    )
                health = self.planner.check()
                if (
                    health.blockers
                    or health.installed_version is None
                    or version_base(health.installed_version)
                    != version_base(check.release_version)
                    or not health.plugin_enabled
                ):
                    try:
                        self._restore_program(
                            snapshot_id=program.snapshot_id,
                            expected_version=check.installed_version,
                        )
                    except UpgradeError as rollback_exc:
                        raise UpgradeError(
                            f"新版本健康检查失败且自动回滚未确认；恢复点 {backup.backup_id} 仍然有效"
                        ) from rollback_exc
                    raise UpgradeError(
                        f"新版本健康检查失败，已恢复旧程序；恢复点 {backup.backup_id} 已保留"
                    )
                upgrade_registered = self._try_save_receipt(
                    plugin_version=health.installed_version,
                    marketplace_name=check.marketplace_name,
                    enabled=True,
                )
                return CodexUpgradeResult(
                    from_version=check.installed_version,
                    to_version=health.installed_version,
                    backup_id=backup.backup_id,
                    program_snapshot_id=program.snapshot_id,
                    applied=True,
                    rolled_back=False,
                    upgrade_registered=upgrade_registered,
                    requires_new_task=True,
                )
        except InterprocessLockError as exc:
            raise UpgradeError("另一个升级操作正在进行，请稍后再试") from exc

    def rollback(
        self,
        *,
        snapshot_id: str,
        confirm: bool,
    ) -> CodexUpgradeResult:
        if confirm is not True:
            raise UpgradeError("回滚会替换 Codex Plugin，请先单独确认")
        snapshot = self.program_snapshots.verify(snapshot_id)
        current = self.planner.check()
        current_version = current.installed_version or "unknown"
        _private_directory(self.layout.system_root)
        try:
            with exclusive_file_lock(self._apply_lock, timeout=5.0):
                upgrade_registered = self._restore_program(
                    snapshot_id=snapshot_id,
                    expected_version=snapshot.plugin_version,
                )
        except InterprocessLockError as exc:
            raise UpgradeError("另一个升级操作正在进行，请稍后再试") from exc
        return CodexUpgradeResult(
            from_version=current_version,
            to_version=snapshot.plugin_version,
            backup_id=snapshot_id,
            program_snapshot_id=snapshot_id,
            applied=True,
            rolled_back=True,
            upgrade_registered=upgrade_registered,
            requires_new_task=True,
        )
