from pathlib import Path
import subprocess
import tempfile
import unittest

from companion_kit.host_install import HostInstaller, InstallError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "virtual-companion"


class HostInstallTests(unittest.TestCase):
    def _write_legacy_codex_skill(self, codex_root: Path) -> Path:
        legacy = codex_root / "skills" / "virtual-companion"
        (legacy / "scripts").mkdir(parents=True)
        (legacy / "SKILL.md").write_text(
            "---\nname: virtual-companion\n---\n\n# Companion Kit\n",
            encoding="utf-8",
        )
        (legacy / "scripts" / "companionctl.py").write_text(
            "# legacy Companion Kit entry\n",
            encoding="utf-8",
        )
        return legacy

    def test_default_hermes_root_may_be_a_user_managed_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            actual_root = home / "Hermes"
            actual_root.mkdir()
            try:
                (home / ".hermes").symlink_to(actual_root, target_is_directory=True)
            except (NotImplementedError, OSError):
                self.skipTest("当前平台不支持目录符号链接")

            installer = HostInstaller(
                skill_root=SKILL_ROOT,
                home=home,
                environment={},
            )

            plan = installer.plan("hermes")

            self.assertEqual(
                plan.destination,
                actual_root / "skills" / "virtual-companion",
            )

    def test_default_install_matrix_uses_host_specific_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            installer = HostInstaller(
                skill_root=SKILL_ROOT,
                home=home,
                environment={},
                which=lambda name: f"/usr/bin/{name}" if name in {"openclaw", "codex"} else None,
            )

            openclaw = installer.plan("openclaw")
            hermes = installer.plan("hermes")
            codex = installer.plan("codex")
            claude = installer.plan("claude")

            self.assertEqual(openclaw.method, "native_cli")
            self.assertIsNone(openclaw.destination)
            self.assertEqual(hermes.destination, home / ".hermes" / "skills" / "virtual-companion")
            self.assertEqual(codex.method, "codex_plugin")
            self.assertIsNone(codex.destination)
            self.assertEqual(codex.source, PROJECT_ROOT)
            self.assertEqual(codex.bootstrap_argv[:4], ("/usr/bin/codex", "plugin", "marketplace", "add"))
            self.assertIn("companion-kit@companion-kit-preview", codex.argv)
            self.assertEqual(claude.destination, home / ".claude" / "skills" / "virtual-companion")

    def test_codex_apply_registers_marketplace_then_installs_plugin(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            installer = HostInstaller(
                skill_root=SKILL_ROOT,
                home=Path(tmp).resolve(),
                environment={},
                which=lambda name: "/usr/bin/codex" if name == "codex" else None,
                runner=runner,
            )

            result = installer.install("codex")

        self.assertTrue(result.applied)
        self.assertEqual(result.plan.method, "codex_plugin")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0][:4], ["/usr/bin/codex", "plugin", "marketplace", "add"])
        self.assertEqual(
            calls[1][0],
            [
                "/usr/bin/codex",
                "plugin",
                "add",
                "companion-kit@companion-kit-preview",
                "--json",
            ],
        )
        self.assertTrue(all("shell" not in kwargs for _, kwargs in calls))

    def test_codex_apply_backs_up_legacy_skill_without_touching_persona(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            legacy = self._write_legacy_codex_skill(home / ".codex")
            persona = home / ".companion-kit" / "profiles" / "default.toml"
            persona.parent.mkdir(parents=True)
            persona.write_text("display_name = \"小禾\"\n", encoding="utf-8")
            calls: list[list[str]] = []

            def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

            installer = HostInstaller(
                skill_root=SKILL_ROOT,
                home=home,
                environment={},
                which=lambda name: "/usr/bin/codex" if name == "codex" else None,
                runner=runner,
            )

            result = installer.install("codex")

            self.assertTrue(result.applied)
            self.assertEqual(result.plan.legacy_skill_count, 1)
            self.assertEqual(len(calls), 2)
            self.assertFalse(legacy.exists())
            backups = list((home / ".codex" / "legacy-skills").glob("virtual-companion-*"))
            self.assertEqual(len(backups), 1)
            self.assertTrue((backups[0] / "SKILL.md").is_file())
            self.assertEqual(persona.read_text(encoding="utf-8"), "display_name = \"小禾\"\n")

    def test_codex_plugin_failure_restores_legacy_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            legacy = self._write_legacy_codex_skill(home / ".agents")
            calls = 0

            def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                nonlocal calls
                calls += 1
                return subprocess.CompletedProcess(
                    argv,
                    0 if calls == 1 else 1,
                    stdout="{}" if calls == 1 else "",
                    stderr="failed",
                )

            installer = HostInstaller(
                skill_root=SKILL_ROOT,
                home=home,
                environment={},
                which=lambda name: "/usr/bin/codex" if name == "codex" else None,
                runner=runner,
            )

            with self.assertRaisesRegex(InstallError, "安装 Plugin"):
                installer.install("codex")

            self.assertTrue((legacy / "SKILL.md").is_file())
            backup_root = home / ".agents" / "legacy-skills"
            self.assertFalse(backup_root.exists() and any(backup_root.iterdir()))

    def test_codex_plugin_failure_restores_with_existing_backup_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            legacy = self._write_legacy_codex_skill(home / ".codex")
            backup_root = home / ".codex" / "legacy-skills"
            previous = backup_root / "virtual-companion-previous"
            previous.mkdir(parents=True)
            (previous / "note.txt").write_text("older backup", encoding="utf-8")
            calls = 0

            def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                nonlocal calls
                calls += 1
                return subprocess.CompletedProcess(
                    argv,
                    0 if calls == 1 else 1,
                    stdout="{}" if calls == 1 else "",
                    stderr="failed",
                )

            installer = HostInstaller(
                skill_root=SKILL_ROOT,
                home=home,
                environment={},
                which=lambda name: "/usr/bin/codex" if name == "codex" else None,
                runner=runner,
            )

            with self.assertRaisesRegex(InstallError, "安装 Plugin"):
                installer.install("codex")

            self.assertTrue((legacy / "SKILL.md").is_file())
            self.assertEqual((previous / "note.txt").read_text(encoding="utf-8"), "older backup")
            self.assertEqual(list(backup_root.iterdir()), [previous])

    def test_codex_install_interruption_restores_legacy_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            legacy = self._write_legacy_codex_skill(home / ".codex")

            def interrupted(_: list[str], **__: object) -> subprocess.CompletedProcess[str]:
                raise KeyboardInterrupt

            installer = HostInstaller(
                skill_root=SKILL_ROOT,
                home=home,
                environment={},
                which=lambda name: "/usr/bin/codex" if name == "codex" else None,
                runner=interrupted,
            )

            with self.assertRaises(KeyboardInterrupt):
                installer.install("codex")

            self.assertTrue((legacy / "SKILL.md").is_file())
            backup_root = home / ".codex" / "legacy-skills"
            self.assertFalse(backup_root.exists() and any(backup_root.iterdir()))

    def test_codex_refuses_legacy_skill_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            outside = home / "outside"
            self._write_legacy_codex_skill(outside)
            skills_root = home / ".codex" / "skills"
            skills_root.mkdir(parents=True)
            try:
                (skills_root / "virtual-companion").symlink_to(
                    outside / "skills" / "virtual-companion",
                    target_is_directory=True,
                )
            except (NotImplementedError, OSError):
                self.skipTest("当前平台不支持目录符号链接")

            installer = HostInstaller(
                skill_root=SKILL_ROOT,
                home=home,
                environment={},
                which=lambda name: "/usr/bin/codex" if name == "codex" else None,
            )

            with self.assertRaisesRegex(InstallError, "符号链接"):
                installer.plan("codex")

            self.assertTrue((outside / "skills" / "virtual-companion" / "SKILL.md").is_file())

    def test_codex_refuses_to_move_unknown_same_named_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            unknown = home / ".codex" / "skills" / "virtual-companion"
            unknown.mkdir(parents=True)
            (unknown / "SKILL.md").write_text(
                "---\nname: virtual-companion\n---\n\n# unrelated\n",
                encoding="utf-8",
            )
            (unknown / "scripts").mkdir()
            (unknown / "scripts" / "companionctl.py").write_text(
                "# unrelated entry\n",
                encoding="utf-8",
            )
            calls: list[list[str]] = []

            installer = HostInstaller(
                skill_root=SKILL_ROOT,
                home=home,
                environment={},
                which=lambda name: "/usr/bin/codex" if name == "codex" else None,
                runner=lambda argv, **_: calls.append(argv),
            )

            with self.assertRaisesRegex(InstallError, "无法确认"):
                installer.install("codex")

            self.assertTrue(unknown.is_dir())
            self.assertEqual(calls, [])

    def test_openclaw_apply_uses_fixed_argv_without_shell(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout="installed", stderr="")

        installer = HostInstaller(
            skill_root=SKILL_ROOT,
            which=lambda name: "/usr/bin/openclaw" if name == "openclaw" else None,
            runner=runner,
        )

        result = installer.install("openclaw")

        self.assertTrue(result.applied)
        self.assertEqual(len(calls), 1)
        argv, kwargs = calls[0]
        self.assertEqual(argv[:3], ["/usr/bin/openclaw", "skills", "install"])
        self.assertIn("--global", argv)
        self.assertNotIn("shell", kwargs)

    def test_copy_hosts_install_into_isolated_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            roots = {
                "hermes": root / "hermes",
                "codex": root / "codex-agents",
                "claude": root / "claude",
            }
            installer = HostInstaller(skill_root=SKILL_ROOT, target_roots=roots)

            for host in ("hermes", "codex", "claude"):
                with self.subTest(host=host):
                    result = installer.install(host)
                    self.assertTrue(result.applied)
                    self.assertTrue((result.plan.destination / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
