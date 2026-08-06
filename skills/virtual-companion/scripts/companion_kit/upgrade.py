from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Callable

from .backup import BackupManager, CompanionDataLayout, DataInventory
from .file_lock import InterprocessLockError, exclusive_file_lock
from .initializer import InitializationError, safe_profile_path


class UpgradeError(ValueError):
    """无法安全检查或规划 Companion Kit 升级。"""


Runner = Callable[..., subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]
_MARKETPLACE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SOURCE_TYPE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
_VERSION_RE = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)


def version_base(value: str) -> str:
    """忽略 Codex 本地 cachebuster，但保留正式版本和预发布号。"""

    return str(value or "").strip().split("+", 1)[0]


def _version_key(value: str) -> tuple[object, ...] | None:
    match = _VERSION_RE.fullmatch(str(value or "").strip())
    if match is None:
        return None
    prerelease = match.group("prerelease")
    if prerelease is None:
        suffix: tuple[object, ...] = (1,)
    else:
        tokens: list[tuple[int, object]] = []
        for token in prerelease.split("."):
            tokens.append((0, int(token)) if token.isdigit() else (1, token.casefold()))
        suffix = (0, *tokens)
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        *suffix,
    )


@dataclass(frozen=True)
class CodexInstallationReceipt:
    plugin_version: str
    marketplace_name: str
    marketplace_source_type: str
    plugin_enabled: bool
    recorded_at: str

    def __post_init__(self) -> None:
        version = str(self.plugin_version or "").strip()
        marketplace = str(self.marketplace_name or "").strip()
        source_type = str(self.marketplace_source_type or "").strip().lower()
        if (
            not version
            or len(version) > 128
            or any(ord(character) < 32 for character in version)
        ):
            raise UpgradeError("安装收据中的 Plugin 版本无效")
        if not _MARKETPLACE_RE.fullmatch(marketplace):
            raise UpgradeError("安装收据中的 Marketplace 名称无效")
        if not _SOURCE_TYPE_RE.fullmatch(source_type):
            raise UpgradeError("安装收据中的来源类型无效")
        if not isinstance(self.plugin_enabled, bool):
            raise UpgradeError("安装收据中的启用状态无效")
        try:
            recorded = datetime.fromisoformat(str(self.recorded_at))
        except ValueError as exc:
            raise UpgradeError("安装收据中的时间无效") from exc
        if recorded.tzinfo is None:
            raise UpgradeError("安装收据中的时间必须包含时区")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "host": "codex",
            "plugin_name": "companion-kit",
            "plugin_version": self.plugin_version,
            "marketplace_name": self.marketplace_name,
            "marketplace_source_type": self.marketplace_source_type,
            "plugin_enabled": self.plugin_enabled,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, raw: object) -> CodexInstallationReceipt:
        if not isinstance(raw, dict):
            raise UpgradeError("安装收据格式无效")
        required = {
            "schema_version",
            "host",
            "plugin_name",
            "plugin_version",
            "marketplace_name",
            "marketplace_source_type",
            "plugin_enabled",
            "recorded_at",
        }
        if (
            set(raw) != required
            or raw.get("schema_version") != 1
            or raw.get("host") != "codex"
            or raw.get("plugin_name") != "companion-kit"
        ):
            raise UpgradeError("安装收据字段或版本无效")
        return cls(
            plugin_version=str(raw["plugin_version"]),
            marketplace_name=str(raw["marketplace_name"]),
            marketplace_source_type=str(raw["marketplace_source_type"]),
            plugin_enabled=raw["plugin_enabled"],  # type: ignore[arg-type]
            recorded_at=str(raw["recorded_at"]),
        )


class InstallationReceiptStore:
    """只记录版本和来源类型；不保存仓库绝对路径或 Marketplace 私有值。"""

    def __init__(self, layout: CompanionDataLayout) -> None:
        self.layout = layout
        self.path = layout.system_root / "installation.json"
        self.lock_path = layout.system_root / ".upgrade.lock"

    @staticmethod
    def _safe(path: Path) -> Path:
        try:
            return safe_profile_path(path)
        except InitializationError as exc:
            raise UpgradeError(str(exc)) from exc

    def read(self) -> CodexInstallationReceipt | None:
        self._safe(self.path)
        if not self.path.exists():
            return None
        if self.path.is_symlink() or not self.path.is_file():
            raise UpgradeError("安装收据必须是普通文件，不能是符号链接")
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpgradeError("安装收据无法读取") from exc
        return CodexInstallationReceipt.from_dict(raw)

    def save(
        self,
        receipt: CodexInstallationReceipt,
    ) -> CodexInstallationReceipt:
        if not isinstance(receipt, CodexInstallationReceipt):
            raise UpgradeError("安装收据对象无效")
        self._safe(self.layout.system_root)
        try:
            self.layout.system_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._safe(self.layout.system_root)
            self._safe(self.path)
            if self.path.exists() and (
                self.path.is_symlink() or not self.path.is_file()
            ):
                raise UpgradeError("安装收据必须是普通文件，不能是符号链接")
            if os.name != "nt":
                self.layout.system_root.chmod(0o700)
            with exclusive_file_lock(self.lock_path, timeout=5.0):
                temporary: Path | None = None
                try:
                    with tempfile.NamedTemporaryFile(
                        mode="w",
                        encoding="utf-8",
                        dir=self.layout.system_root,
                        prefix=".installation-",
                        suffix=".json",
                        delete=False,
                    ) as handle:
                        json.dump(
                            receipt.to_dict(),
                            handle,
                            ensure_ascii=False,
                            indent=2,
                            sort_keys=True,
                        )
                        handle.write("\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                        temporary = Path(handle.name)
                    if os.name != "nt":
                        temporary.chmod(0o600)
                    temporary.replace(self.path)
                    temporary = None
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
        except UpgradeError:
            raise
        except (OSError, InterprocessLockError) as exc:
            raise UpgradeError(f"无法保存安装收据：{exc}") from exc
        return receipt


@dataclass(frozen=True)
class CodexUpgradeCheck:
    release_version: str
    installed_version: str | None
    plugin_installed: bool
    plugin_enabled: bool
    marketplace_name: str | None
    marketplace_source_type: str | None
    marketplace_matches_release_source: bool | None
    installation_receipt_present: bool
    update_candidate: bool
    inventory: DataInventory
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, object]:
        return {
            "host": "codex",
            "release_version": self.release_version,
            "installed_version": self.installed_version,
            "plugin_installed": self.plugin_installed,
            "plugin_enabled": self.plugin_enabled,
            "marketplace_name": self.marketplace_name,
            "marketplace_source_type": self.marketplace_source_type,
            "marketplace_matches_release_source": self.marketplace_matches_release_source,
            "installation_receipt_present": self.installation_receipt_present,
            "update_candidate": self.update_candidate,
            "ready": self.ready,
            "data": self.inventory.to_dict(),
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class CodexUpgradePlan:
    check: CodexUpgradeCheck
    ready: bool
    apply_available: bool
    backup_required: bool
    migrations: tuple[dict[str, object], ...]
    steps: tuple[dict[str, object], ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            **self.check.to_dict(),
            "ready": self.ready,
            "apply_available": self.apply_available,
            "backup_required": self.backup_required,
            "migrations": list(self.migrations),
            "steps": list(self.steps),
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


def _release_version(plugin_root: Path) -> str:
    manifest = plugin_root / ".codex-plugin" / "plugin.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise UpgradeError("当前项目缺少 Codex Plugin 清单")
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpgradeError("Codex Plugin 清单无法读取") from exc
    version = str(raw.get("version", "")).strip() if isinstance(raw, dict) else ""
    if not version:
        raise UpgradeError("Codex Plugin 清单缺少 version")
    return version


def _plugin_entry(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None
    installed = payload.get("installed")
    if not isinstance(installed, list):
        return None
    for raw in installed:
        if isinstance(raw, dict) and raw.get("name") == "companion-kit":
            return raw
    return None


class CodexUpgradePlanner:
    """只读检查本地版本与数据；本阶段不替换 Plugin 或迁移正式数据。"""

    def __init__(
        self,
        *,
        plugin_root: str | Path,
        layout: CompanionDataLayout,
        which: Which = shutil.which,
        runner: Runner = subprocess.run,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.plugin_root = Path(plugin_root).expanduser().resolve()
        self.layout = layout
        self.which = which
        self.runner = runner
        self._clock = clock or (lambda: datetime.now(UTC))

    def _inventory(self, release_version: str) -> DataInventory:
        return BackupManager(
            self.layout,
            product_version=release_version,
        ).inspect()

    def check(self) -> CodexUpgradeCheck:
        release_version = _release_version(self.plugin_root)
        inventory = self._inventory(release_version)
        blockers = list(inventory.blockers)
        warnings = list(inventory.warnings)
        executable = self.which("codex")
        entry: dict[str, object] | None = None
        if executable is None:
            blockers.append("未找到 Codex 命令，暂时无法核对 Plugin 安装状态")
        else:
            try:
                completed = self.runner(
                    [executable, "plugin", "list", "--json"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except (OSError, subprocess.SubprocessError):
                blockers.append("Codex Plugin 状态检查无法执行")
            else:
                if completed.returncode != 0:
                    blockers.append("Codex Plugin 状态检查失败")
                else:
                    try:
                        payload = json.loads(completed.stdout)
                    except (TypeError, json.JSONDecodeError):
                        blockers.append("Codex Plugin 状态输出无法解析")
                    else:
                        entry = _plugin_entry(payload)
                        if entry is None:
                            blockers.append("当前 Codex 尚未安装 Companion Kit Plugin")

        installed_version: str | None = None
        plugin_enabled = False
        marketplace_name: str | None = None
        source_type: str | None = None
        source_matches: bool | None = None
        if entry is not None:
            raw_version = str(entry.get("version", "")).strip()
            installed_version = raw_version or None
            plugin_enabled = bool(entry.get("enabled", False))
            raw_marketplace = str(entry.get("marketplaceName", "")).strip()
            marketplace_name = raw_marketplace or None
            if marketplace_name is None or not _MARKETPLACE_RE.fullmatch(
                marketplace_name
            ):
                blockers.append("Codex Plugin 的 Marketplace 名称无效")
            source = entry.get("marketplaceSource")
            if isinstance(source, dict):
                raw_type = str(source.get("sourceType", "")).strip().lower()
                source_type = raw_type or None
            if source_type == "local":
                raw_source = ""
                if isinstance(source, dict):
                    raw_source = str(
                        source.get("source") or source.get("value") or ""
                    ).strip()
                direct_source = entry.get("source")
                if not raw_source and isinstance(direct_source, dict):
                    raw_source = str(direct_source.get("path") or "").strip()
                if raw_source:
                    try:
                        source_matches = (
                            Path(raw_source).expanduser().resolve()
                            == self.plugin_root
                        )
                    except OSError:
                        source_matches = False
                else:
                    source_matches = False
                if not source_matches:
                    blockers.append(
                        "Codex 当前 Marketplace 没有指向这份项目，不能替换未知来源的 Plugin"
                    )
            if source_type == "local":
                warnings.append(
                    "当前属于本地 Marketplace 安装；正式升级前需要先创建恢复点，再刷新本地 Plugin 副本"
                )
            elif source_type is not None:
                warnings.append("当前安全切换只支持本地 Marketplace")
            if not plugin_enabled:
                blockers.append("Companion Kit Plugin 当前处于停用状态，请先确认是否要启用")

        if inventory.profile_schema is not None and inventory.profile_schema > 3:
            blockers.append("Persona 数据版本高于当前程序，不能降级处理")
        if (
            inventory.relationship_schema is not None
            and inventory.relationship_schema > 2
        ):
            blockers.append("关系数据版本高于当前程序，不能降级处理")
        receipt: CodexInstallationReceipt | None = None
        try:
            receipt = InstallationReceiptStore(self.layout).read()
        except UpgradeError as exc:
            blockers.append(str(exc))
        if entry is not None and receipt is None:
            warnings.append("现有安装尚未登记；首次安全升级会先写入不含私人路径的安装收据")
        elif receipt is not None and installed_version is not None:
            if receipt.plugin_version != installed_version:
                warnings.append("安装收据版本与 Codex 当前 Plugin 版本不同，需要重新登记")
        update_candidate = False
        if installed_version is not None:
            installed_base = version_base(installed_version)
            release_base = version_base(release_version)
            installed_key = _version_key(installed_base)
            release_key = _version_key(release_base)
            if installed_key is None or release_key is None:
                blockers.append("Plugin 版本格式无法安全比较")
            elif installed_key > release_key:
                blockers.append("Codex 已安装版本比当前项目更新，不能自动降级")
            else:
                update_candidate = installed_key < release_key
        return CodexUpgradeCheck(
            release_version=release_version,
            installed_version=installed_version,
            plugin_installed=entry is not None,
            plugin_enabled=plugin_enabled,
            marketplace_name=marketplace_name,
            marketplace_source_type=source_type,
            marketplace_matches_release_source=source_matches,
            installation_receipt_present=receipt is not None,
            update_candidate=update_candidate,
            inventory=inventory,
            blockers=tuple(dict.fromkeys(blockers)),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def adopt(self) -> CodexInstallationReceipt:
        """登记既有 Codex 安装；不移动、重写或迁移任何耐久用户数据。"""

        check = self.check()
        if not check.plugin_installed or check.installed_version is None:
            raise UpgradeError("没有找到可登记的 Companion Kit Plugin 安装")
        if check.marketplace_name is None:
            raise UpgradeError("当前 Plugin 缺少 Marketplace 信息，无法安全登记")
        now = self._clock()
        if now.tzinfo is None:
            raise UpgradeError("安装登记时间必须包含时区")
        receipt = CodexInstallationReceipt(
            plugin_version=check.installed_version,
            marketplace_name=check.marketplace_name,
            marketplace_source_type=check.marketplace_source_type or "unknown",
            plugin_enabled=check.plugin_enabled,
            recorded_at=now.astimezone(UTC).isoformat(),
        )
        return InstallationReceiptStore(self.layout).save(receipt)

    def plan(self) -> CodexUpgradePlan:
        check = self.check()
        migrations: list[dict[str, object]] = []
        if check.inventory.profile_schema in {1, 2}:
            migrations.append(
                {
                    "data": "persona",
                    "from": check.inventory.profile_schema,
                    "to": 3,
                    "mode": "shadow_copy_then_compatible_read",
                }
            )
        if check.inventory.relationship_schema == 1:
            migrations.append(
                {
                    "data": "relationship",
                    "from": 1,
                    "to": 2,
                    "mode": "shadow_copy_then_transactional_runtime",
                }
            )
        steps = (
            {
                "id": "preflight",
                "label": "检查版本、权限、空间和数据完整性",
                "mutates_user_data": False,
            },
            {
                "id": "verified_backup",
                "label": "创建并校验 Persona、关系和人物参考恢复点",
                "mutates_user_data": False,
            },
            {
                "id": "program_snapshot",
                "label": "复制并校验当前 Codex Plugin，给失败回滚留一条路",
                "mutates_user_data": False,
            },
            {
                "id": "stage_release",
                "label": "把新程序放入独立暂存目录并校验",
                "mutates_user_data": False,
            },
            {
                "id": "shadow_migration",
                "label": "只在恢复点副本上试跑必要的数据迁移",
                "mutates_user_data": False,
            },
            {
                "id": "plugin_switch",
                "label": "验证通过后更新 Codex Plugin 副本",
                "mutates_user_data": False,
            },
            {
                "id": "health_check",
                "label": "重启后检查 Persona、关系和 Identity Pack 是否可用",
                "mutates_user_data": False,
            },
        )
        warnings = list(check.warnings)
        if not check.update_candidate and check.ready:
            warnings.append("Codex 已经是当前项目版本，不需要重复升级")
        return CodexUpgradePlan(
            check=check,
            ready=check.ready,
            apply_available=(
                check.ready
                and check.update_candidate
                and check.marketplace_source_type == "local"
                and check.marketplace_matches_release_source is True
            ),
            backup_required=check.inventory.has_durable_data,
            migrations=tuple(migrations),
            steps=steps,
            blockers=check.blockers,
            warnings=tuple(dict.fromkeys(warnings)),
        )
