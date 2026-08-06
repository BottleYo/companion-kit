from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Callable, Mapping, Sequence
from uuid import uuid4

from .installer import InstallError, install_skill
from .public_bundle import inspect_public_tree, inspect_release_layout


_HOST_NAMES = {
    "openclaw": "OpenClaw",
    "hermes": "Hermes",
    "codex": "Codex",
    "claude": "Claude",
}
_CODEX_MARKETPLACE_NAME = "companion-kit-preview"
_CODEX_PLUGIN_NAME = "companion-kit"
_LEGACY_SKILL_NAME = "virtual-companion"
_LEGACY_MARKER_LIMIT = 256 * 1024


@dataclass(frozen=True)
class HostInstallPlan:
    host: str
    name: str
    family: str
    method: str
    source: Path
    destination: Path | None
    argv: tuple[str, ...]
    existing: bool | None
    available: bool
    apply_required: bool = True
    bootstrap_argv: tuple[str, ...] = ()
    legacy_skill_count: int = 0

    def to_dict(self) -> dict[str, object]:
        def public_command(argv: tuple[str, ...]) -> list[str]:
            command = list(argv)
            if command:
                command[0] = Path(command[0]).name
                for index, value in enumerate(command):
                    if value == str(self.source):
                        command[index] = (
                            "<内置 Plugin>"
                            if self.method == "codex_plugin"
                            else "<内置 Skill>"
                        )
            return command

        command = public_command(self.argv)
        commands = tuple(
            public_command(argv)
            for argv in (self.bootstrap_argv, self.argv)
            if argv
        )
        return {
            "host": self.host,
            "name": self.name,
            "family": self.family,
            "method": self.method,
            "source": (
                "bundled_plugin"
                if self.method == "codex_plugin"
                else "bundled_skill"
            ),
            "destination": str(self.destination) if self.destination else None,
            "command": command,
            "commands": commands,
            "existing": self.existing,
            "available": self.available,
            "apply_required": self.apply_required,
            "legacy_skill_count": self.legacy_skill_count,
            "legacy_skill_action": (
                "backup_and_disable" if self.legacy_skill_count else "none"
            ),
        }


@dataclass(frozen=True)
class HostInstallResult:
    plan: HostInstallPlan
    applied: bool

    def to_dict(self) -> dict[str, object]:
        payload = self.plan.to_dict()
        payload["applied"] = self.applied
        return payload


Runner = Callable[..., subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]


class HostInstaller:
    def __init__(
        self,
        *,
        skill_root: str | Path,
        target_roots: Mapping[str, str | Path] | None = None,
        home: str | Path | None = None,
        environment: Mapping[str, str] | None = None,
        which: Which = shutil.which,
        runner: Runner = subprocess.run,
        forbidden_text: Sequence[str] = (),
    ) -> None:
        self.skill_root = Path(skill_root).resolve()
        self.target_roots = {
            key: Path(value).expanduser()
            for key, value in (target_roots or {}).items()
        }
        self.home = Path(home).expanduser() if home is not None else Path.home()
        self.environment = dict(os.environ if environment is None else environment)
        self.which = which
        self.runner = runner
        self.forbidden_text = tuple(forbidden_text)

    @property
    def plugin_root(self) -> Path:
        return self.skill_root.parents[1]

    def _codex_user_roots(self) -> tuple[Path, ...]:
        configured = self.environment.get("CODEX_HOME", "").strip()
        candidates = [
            Path(configured).expanduser() if configured else self.home / ".codex",
            self.home / ".agents",
        ]
        roots: list[Path] = []
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError as exc:
                raise InstallError(f"无法检查旧版 Codex Skill：{exc}") from exc
            if resolved not in roots:
                roots.append(resolved)
        return tuple(roots)

    @staticmethod
    def _bounded_text(path: Path) -> str:
        if path.is_symlink() or not path.is_file():
            raise InstallError("旧版 Skill 文件结构异常，已停止迁移")
        try:
            size = path.stat().st_size
            if size > _LEGACY_MARKER_LIMIT:
                raise InstallError("旧版 Skill 文件异常过大，已停止迁移")
            return path.read_text(encoding="utf-8")
        except UnicodeError as exc:
            raise InstallError("旧版 Skill 文件编码异常，已停止迁移") from exc
        except OSError as exc:
            raise InstallError(f"无法读取旧版 Codex Skill：{exc}") from exc

    def _legacy_codex_skills(self) -> tuple[tuple[Path, Path], ...]:
        found: list[tuple[Path, Path]] = []
        for codex_root in self._codex_user_roots():
            skills_root = codex_root / "skills"
            candidate = skills_root / _LEGACY_SKILL_NAME
            if not (candidate.exists() or candidate.is_symlink()):
                continue
            if skills_root.is_symlink() or candidate.is_symlink():
                raise InstallError("旧版 Skill 路径经过符号链接，已停止迁移")
            if not candidate.is_dir():
                raise InstallError("旧版 Skill 目标不是目录，已停止迁移")
            if (candidate / "scripts").is_symlink():
                raise InstallError("旧版 Skill 路径经过符号链接，已停止迁移")
            skill_text = self._bounded_text(candidate / "SKILL.md")
            entry_text = self._bounded_text(candidate / "scripts" / "companionctl.py")
            normalized_lines = {
                line.strip().lower().replace('"', "").replace("'", "")
                for line in skill_text.splitlines()
            }
            recognized = (
                "name: virtual-companion" in normalized_lines
                and (
                    "companion kit" in skill_text.lower()
                    or "companion_kit" in entry_text
                )
            )
            if not recognized:
                raise InstallError(
                    "发现同名 Skill，但无法确认它属于 Companion Kit；未做任何移动"
                )
            backup_root = codex_root / "legacy-skills"
            if backup_root.is_symlink() or (
                backup_root.exists() and not backup_root.is_dir()
            ):
                raise InstallError("旧版 Skill 备份目录不安全，已停止迁移")
            found.append((candidate, backup_root))
        return tuple(found)

    @staticmethod
    def _restore_legacy_skills(moved: Sequence[tuple[Path, Path]]) -> None:
        for source, backup in reversed(tuple(moved)):
            if source.exists() or source.is_symlink():
                raise InstallError("Plugin 安装失败，且旧版 Skill 目标已被占用，无法自动恢复")
            try:
                backup.rename(source)
            except OSError as exc:
                raise InstallError("Plugin 安装失败，旧版 Skill 无法自动恢复") from exc
            try:
                backup.parent.rmdir()
            except OSError:
                pass

    def _deactivate_legacy_skills(
        self,
        legacy_skills: Sequence[tuple[Path, Path]],
    ) -> tuple[tuple[Path, Path], ...]:
        moved: list[tuple[Path, Path]] = []
        try:
            for source, backup_root in legacy_skills:
                backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
                if backup_root.is_symlink() or not backup_root.is_dir():
                    raise InstallError("旧版 Skill 备份目录不安全，已停止迁移")
                backup = backup_root / f"{_LEGACY_SKILL_NAME}-{uuid4().hex}"
                source.rename(backup)
                moved.append((source, backup))
        except (InstallError, OSError) as exc:
            if moved:
                self._restore_legacy_skills(moved)
            if isinstance(exc, InstallError):
                raise
            raise InstallError(f"无法备份旧版 Codex Skill：{exc}") from exc
        return tuple(moved)

    def _target_root(self, host: str) -> Path:
        if host in self.target_roots:
            return self.target_roots[host].resolve()
        if host == "hermes":
            configured = self.environment.get("HERMES_HOME", "").strip()
            candidate = Path(configured).expanduser() if configured else self.home / ".hermes"
            return candidate.resolve()
        if host == "codex":
            return (self.home / ".agents").resolve()
        if host == "claude":
            configured = self.environment.get("CLAUDE_CONFIG_DIR", "").strip()
            candidate = Path(configured).expanduser() if configured else self.home / ".claude"
            return candidate.resolve()
        raise InstallError(f"{_HOST_NAMES[host]} 必须使用原生安装命令")

    def plan(self, host: str) -> HostInstallPlan:
        normalized = str(host or "").strip().lower()
        if normalized not in _HOST_NAMES:
            raise InstallError(f"不支持的宿主：{host}")

        if normalized == "openclaw" and normalized not in self.target_roots:
            executable = self.which("openclaw")
            argv = (
                executable or "openclaw",
                "skills",
                "install",
                str(self.skill_root),
                "--as",
                "virtual-companion",
                "--global",
            )
            return HostInstallPlan(
                host=normalized,
                name=_HOST_NAMES[normalized],
                family="event",
                method="native_cli",
                source=self.skill_root,
                destination=None,
                argv=argv,
                existing=None,
                available=executable is not None,
            )

        if normalized == "codex" and normalized not in self.target_roots:
            executable = self.which("codex")
            plugin_root = self.plugin_root
            marketplace = plugin_root / ".agents" / "plugins" / "marketplace.json"
            legacy_skills = self._legacy_codex_skills()
            available = (
                executable is not None
                and (plugin_root / ".codex-plugin" / "plugin.json").is_file()
                and marketplace.is_file()
            )
            return HostInstallPlan(
                host=normalized,
                name=_HOST_NAMES[normalized],
                family="desktop",
                method="codex_plugin",
                source=plugin_root,
                destination=None,
                bootstrap_argv=(
                    executable or "codex",
                    "plugin",
                    "marketplace",
                    "add",
                    str(plugin_root),
                    "--json",
                ),
                argv=(
                    executable or "codex",
                    "plugin",
                    "add",
                    f"{_CODEX_PLUGIN_NAME}@{_CODEX_MARKETPLACE_NAME}",
                    "--json",
                ),
                existing=None,
                available=available,
                legacy_skill_count=len(legacy_skills),
            )

        target_root = self._target_root(normalized)
        preview = install_skill(
            host=normalized,
            source=self.skill_root,
            target_root=target_root,
            apply=False,
            forbidden_text=self.forbidden_text,
        )
        return HostInstallPlan(
            host=normalized,
            name=_HOST_NAMES[normalized],
            family="event" if normalized in {"openclaw", "hermes"} else "desktop",
            method="skill_copy",
            source=self.skill_root,
            destination=preview.destination,
            argv=(),
            existing=preview.destination.exists(),
            available=True,
        )

    def install(self, host: str) -> HostInstallResult:
        plan = self.plan(host)
        if plan.method == "native_cli":
            if not plan.available:
                raise InstallError("未找到 OpenClaw 命令，请先安装或修复 OpenClaw")
            failures = inspect_public_tree(
                self.skill_root,
                forbidden_text=self.forbidden_text,
            )
            if failures:
                raise InstallError(failures[0])
            try:
                completed = self.runner(
                    list(plan.argv),
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise InstallError(f"OpenClaw 安装命令无法执行：{exc}") from exc
            if completed.returncode != 0:
                raise InstallError(
                    f"OpenClaw 安装失败（退出码 {completed.returncode}）"
                )
            return HostInstallResult(plan=plan, applied=True)

        if plan.method == "codex_plugin":
            if not plan.available:
                raise InstallError("未找到可用的 Codex Plugin 环境，请先更新或修复 Codex")
            failures = inspect_release_layout(plan.source)
            failures.extend(
                inspect_public_tree(
                    plan.source,
                    forbidden_text=self.forbidden_text,
                )
            )
            if failures:
                raise InstallError(failures[0])
            legacy_skills = self._legacy_codex_skills()
            moved = self._deactivate_legacy_skills(legacy_skills)
            try:
                for label, argv in (
                    ("注册本地来源", plan.bootstrap_argv),
                    ("安装 Plugin", plan.argv),
                ):
                    try:
                        completed = self.runner(
                            list(argv),
                            check=False,
                            capture_output=True,
                            text=True,
                            timeout=120,
                        )
                    except (OSError, subprocess.SubprocessError) as exc:
                        raise InstallError(f"Codex {label}无法执行：{exc}") from exc
                    if completed.returncode != 0:
                        raise InstallError(
                            f"Codex {label}失败（退出码 {completed.returncode}）"
                        )
            except BaseException:
                self._restore_legacy_skills(moved)
                raise
            applied_plan = HostInstallPlan(
                host=plan.host,
                name=plan.name,
                family=plan.family,
                method=plan.method,
                source=plan.source,
                destination=None,
                argv=plan.argv,
                existing=True,
                available=True,
                bootstrap_argv=plan.bootstrap_argv,
                legacy_skill_count=len(moved),
            )
            return HostInstallResult(plan=applied_plan, applied=True)

        result = install_skill(
            host=plan.host,
            source=self.skill_root,
            target_root=self._target_root(plan.host),
            apply=True,
            forbidden_text=self.forbidden_text,
        )
        applied_plan = HostInstallPlan(
            host=plan.host,
            name=plan.name,
            family=plan.family,
            method=plan.method,
            source=plan.source,
            destination=result.destination,
            argv=plan.argv,
            existing=True,
            available=plan.available,
        )
        return HostInstallResult(plan=applied_plan, applied=result.applied)
