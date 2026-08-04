from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Callable, Mapping, Sequence

from .installer import InstallError, install_skill
from .public_bundle import inspect_public_tree


_HOST_NAMES = {
    "openclaw": "OpenClaw",
    "hermes": "Hermes",
    "codex": "Codex",
    "claude": "Claude",
}


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

    def to_dict(self) -> dict[str, object]:
        command = list(self.argv)
        if command:
            command[0] = Path(command[0]).name
            for index, value in enumerate(command):
                if value == str(self.source):
                    command[index] = "<内置 Skill>"
        return {
            "host": self.host,
            "name": self.name,
            "family": self.family,
            "method": self.method,
            "source": "bundled_skill",
            "destination": str(self.destination) if self.destination else None,
            "command": command,
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
