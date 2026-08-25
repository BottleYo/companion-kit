from pathlib import Path
import subprocess
import tempfile
import unittest

from companion_kit.backup import CompanionDataLayout
from companion_kit.host_install import HostInstaller
from companion_kit.upgrade import InstallationReceiptStore


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
            home = Path(tmp).resolve()
            installer = HostInstaller(
                skill_root=SKILL_ROOT,
                home=home,
                environment={},
                which=lambda name: "/usr/bin/codex" if name == "codex" else None,
                runner=runner,
            )

            result = installer.install("codex")

            receipt = InstallationReceiptStore(
                CompanionDataLayout.for_codex(data_root=home / ".companion-kit")
            ).read()

        self.assertTrue(result.applied)
        self.assertTrue(result.upgrade_registered)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.plugin_version, "0.9.0-dev.4")
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

    def test_codex_apply_leaves_user_skill_directories_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            marker = home / ".codex" / "skills" / "virtual-companion" / "marker.txt"
            marker.parent.mkdir(parents=True)
            marker.write_text("keep me", encoding="utf-8")

            installer = HostInstaller(
                skill_root=SKILL_ROOT,
                home=home,
                environment={},
                which=lambda name: "/usr/bin/codex" if name == "codex" else None,
                runner=lambda argv, **_: subprocess.CompletedProcess(
                    argv,
                    0,
                    stdout="{}",
                    stderr="",
                ),
            )

            result = installer.install("codex")

            self.assertTrue(result.applied)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep me")
            self.assertNotIn("legacy_skill_count", result.plan.to_dict())

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
