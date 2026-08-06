from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Callable, Mapping, Sequence

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
