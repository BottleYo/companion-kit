from pathlib import Path
import subprocess
import tempfile
import unittest

from companion_kit.host_install import HostInstaller


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "virtual-companion"


class HostInstallTests(unittest.TestCase):
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
                which=lambda name: "/usr/bin/openclaw" if name == "openclaw" else None,
            )

            openclaw = installer.plan("openclaw")
            hermes = installer.plan("hermes")
            codex = installer.plan("codex")
            claude = installer.plan("claude")

            self.assertEqual(openclaw.method, "native_cli")
            self.assertIsNone(openclaw.destination)
            self.assertEqual(hermes.destination, home / ".hermes" / "skills" / "virtual-companion")
            self.assertEqual(codex.destination, home / ".agents" / "skills" / "virtual-companion")
            self.assertEqual(claude.destination, home / ".claude" / "skills" / "virtual-companion")

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
