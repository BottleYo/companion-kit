from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile
from typing import Iterable
from uuid import uuid4

from .public_bundle import inspect_public_tree


class InstallError(ValueError):
    """安装计划或公开 Skill 包不安全。"""


@dataclass(frozen=True)
class InstallResult:
    host: str
    destination: Path
    applied: bool


_SUPPORTED_HOSTS = {"openclaw", "hermes", "codex", "claude"}


def _validate_public_bundle(
    source: Path,
    *,
    forbidden_text: Iterable[str] = (),
) -> None:
    if not (source / "SKILL.md").is_file():
        raise InstallError("Skill 源目录缺少 SKILL.md")
    failures = inspect_public_tree(source, forbidden_text=forbidden_text)
    if failures:
        raise InstallError(failures[0])


def install_skill(
    *,
    host: str,
    source: str | Path,
    target_root: str | Path,
    apply: bool,
    force: bool = False,
    forbidden_text: Iterable[str] = (),
) -> InstallResult:
    normalized_host = str(host or "").strip().lower()
    if normalized_host not in _SUPPORTED_HOSTS:
        raise InstallError(f"不支持的宿主：{host}")

    raw_source = Path(source).expanduser()
    if raw_source.is_symlink():
        raise InstallError("Skill 源目录不能是符号链接")
    source_path = raw_source.resolve()
    _validate_public_bundle(source_path, forbidden_text=forbidden_text)
    raw_target = Path(target_root).expanduser()
    if raw_target.is_symlink():
        raise InstallError("target-root 不能是符号链接")
    target_path = raw_target.resolve()
    if target_path == Path(target_path.anchor):
        raise InstallError("target-root 不能是文件系统根目录")
    skills_path = target_path / "skills"
    if skills_path.is_symlink():
        raise InstallError("目标 skills 目录不能是符号链接")
    destination = skills_path / "virtual-companion"
    if source_path == destination or source_path in destination.parents:
        raise InstallError("安装目标不能位于 Skill 源目录内部")
    if not apply:
        return InstallResult(host=normalized_host, destination=destination, applied=False)

    destination_exists = destination.exists() or destination.is_symlink()
    if destination_exists:
        if not force:
            raise InstallError(f"目标已存在：{destination}；如需覆盖请显式使用 force")
        if destination.is_symlink():
            raise InstallError("为避免覆盖未知目标，安装器不会删除符号链接")
        if not destination.is_dir():
            raise InstallError("现有安装目标必须是目录")

    staging_root: Path | None = None
    backup: Path | None = None
    try:
        skills_path.mkdir(parents=True, exist_ok=True)
        if skills_path.is_symlink():
            raise InstallError("目标 skills 目录不能是符号链接")

        staging_root = Path(
            tempfile.mkdtemp(prefix=".virtual-companion-install-", dir=skills_path)
        )
        staged = staging_root / "virtual-companion"
        shutil.copytree(source_path, staged)
        staged_failures = inspect_public_tree(staged, forbidden_text=forbidden_text)
        if staged_failures:
            raise InstallError(staged_failures[0])

        if destination_exists:
            backup = skills_path / f".virtual-companion-backup-{uuid4().hex}"
            destination.rename(backup)
        try:
            staged.rename(destination)
        except OSError:
            if backup is not None and backup.exists() and not destination.exists():
                backup.rename(destination)
            raise

        if backup is not None:
            shutil.rmtree(backup)
            backup = None
    except InstallError:
        raise
    except OSError as exc:
        raise InstallError(f"无法安装 Skill：{exc}") from exc
    finally:
        if staging_root is not None and staging_root.exists():
            shutil.rmtree(staging_root, ignore_errors=True)
    return InstallResult(host=normalized_host, destination=destination, applied=True)
