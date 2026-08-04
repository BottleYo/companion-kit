from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from companion_kit.installer import InstallError, install_skill


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "virtual-companion"


class InstallerTests(unittest.TestCase):
    def test_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target_root = Path(tmp) / "codex-home"

            result = install_skill(
                host="codex",
                source=SKILL_ROOT,
                target_root=target_root,
                apply=False,
            )

            self.assertFalse(result.applied)
            self.assertFalse(result.destination.exists())
            self.assertEqual(
                result.destination,
                target_root.resolve() / "skills" / "virtual-companion",
            )

    def test_apply_copies_only_the_public_skill_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target_root = Path(tmp) / "hermes-home"

            result = install_skill(
                host="hermes",
                source=SKILL_ROOT,
                target_root=target_root,
                apply=True,
            )

            self.assertTrue(result.applied)
            self.assertTrue((result.destination / "SKILL.md").is_file())
            self.assertTrue(
                (
                    result.destination
                    / "scripts"
                    / "companion_kit"
                    / "web_assets"
                    / "index.html"
                ).is_file()
            )
            self.assertFalse((result.destination / "SOUL.md").exists())
            self.assertFalse((result.destination / "USER.md").exists())

    def test_apply_works_for_all_supported_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for host in ("openclaw", "hermes", "codex", "claude"):
                with self.subTest(host=host):
                    result = install_skill(
                        host=host,
                        source=SKILL_ROOT,
                        target_root=Path(tmp) / host,
                        apply=True,
                    )
                    self.assertTrue((result.destination / "SKILL.md").is_file())

    def test_installed_bundle_cli_is_runnable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = install_skill(
                host="codex",
                source=SKILL_ROOT,
                target_root=Path(tmp) / "codex-home",
                apply=True,
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(result.destination / "scripts" / "companionctl.py"),
                    "validate",
                    "--config",
                    str(result.destination / "assets" / "demo_companion.toml"),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertTrue(json.loads(completed.stdout)["valid"])

            installed_companionctl = result.destination / "scripts" / "companionctl.py"
            environment = dict(os.environ)
            environment.pop("OPENAI_API_KEY", None)
            environment["COMPANION_HOME"] = str(Path(tmp).resolve() / "companion-home")
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            initialized = subprocess.run(
                [
                    sys.executable,
                    str(result.destination / "scripts" / "companionctl.py"),
                    "init",
                    "--template",
                    "sunny_friend",
                    "--json",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            initialized_payload = json.loads(initialized.stdout)
            self.assertEqual(initialized_payload["template_id"], "sunny_friend")
            self.assertTrue(Path(initialized_payload["output"]).is_file())

            validated = subprocess.run(
                [
                    sys.executable,
                    str(installed_companionctl),
                    "validate",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(validated.returncode, 0, validated.stderr)
            self.assertTrue(json.loads(validated.stdout)["valid"])

            photo_status = subprocess.run(
                [
                    sys.executable,
                    str(installed_companionctl),
                    "photo",
                    "status",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(photo_status.returncode, 0, photo_status.stderr)
            status_payload = json.loads(photo_status.stdout)
            self.assertIn("codex_native", status_payload["modes"])
            self.assertFalse(status_payload["modes"]["openai_strict"]["auth_ready"])

    def test_rejects_media_in_public_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "skill"
            source.mkdir()
            (source / "SKILL.md").write_text("demo", encoding="utf-8")
            (source / "portrait.png").write_bytes(b"not-a-real-image")

            with self.assertRaisesRegex(InstallError, "媒体"):
                install_skill(
                    host="codex",
                    source=source,
                    target_root=Path(tmp) / "target",
                    apply=False,
                )

    def test_rejects_symlinks_in_public_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "skill"
            source.mkdir()
            (source / "SKILL.md").write_text("demo", encoding="utf-8")
            private_file = Path(tmp) / "private.txt"
            private_file.write_text("private", encoding="utf-8")
            (source / "linked.txt").symlink_to(private_file)

            with self.assertRaisesRegex(InstallError, "符号链接"):
                install_skill(
                    host="codex",
                    source=source,
                    target_root=Path(tmp) / "target",
                    apply=False,
                )

    def test_rejects_chat_state_in_public_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "skill"
            source.mkdir()
            (source / "SKILL.md").write_text("demo", encoding="utf-8")
            (source / "conversation_export.json").write_text("[]", encoding="utf-8")

            with self.assertRaisesRegex(InstallError, "会话状态"):
                install_skill(
                    host="codex",
                    source=source,
                    target_root=Path(tmp) / "target",
                    apply=False,
                )

    def test_rejects_cache_and_secret_files_in_public_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for relative, message in (
                ("__pycache__/module.pyc", "缓存"),
                (".env", "密钥"),
                (".env.production", "密钥"),
            ):
                with self.subTest(relative=relative):
                    source = Path(tmp) / relative.replace("/", "-") / "skill"
                    source.mkdir(parents=True)
                    (source / "SKILL.md").write_text("demo", encoding="utf-8")
                    unsafe = source / relative
                    unsafe.parent.mkdir(parents=True, exist_ok=True)
                    unsafe.write_bytes(b"private")

                    with self.assertRaisesRegex(InstallError, message):
                        install_skill(
                            host="codex",
                            source=source,
                            target_root=Path(tmp) / "target",
                            apply=False,
                        )

    def test_rejects_common_private_home_path_in_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "skill"
            source.mkdir()
            private_path = Path("/").joinpath(
                "Users", "example", "private", "portrait.jpg"
            )
            (source / "SKILL.md").write_text(
                f"reference = '{private_path}'",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(InstallError, "绝对路径"):
                install_skill(
                    host="codex",
                    source=source,
                    target_root=Path(tmp) / "target",
                    apply=False,
                )

    def test_rejects_filesystem_root_as_target(self) -> None:
        with self.assertRaisesRegex(InstallError, "根目录"):
            install_skill(
                host="codex",
                source=SKILL_ROOT,
                target_root=Path("/"),
                apply=False,
            )

    def test_force_rejects_symlinked_skills_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "target"
            outside_skills = Path(tmp) / "outside-skills"
            existing = outside_skills / "virtual-companion"
            existing.mkdir(parents=True)
            marker = existing / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            root.mkdir()
            (root / "skills").symlink_to(outside_skills, target_is_directory=True)

            with self.assertRaisesRegex(InstallError, "符号链接"):
                install_skill(
                    host="codex",
                    source=SKILL_ROOT,
                    target_root=root,
                    apply=True,
                    force=True,
                )

            self.assertTrue(marker.is_file())

    def test_rejects_symlinked_target_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "outside"
            outside.mkdir()
            linked_root = Path(tmp) / "linked-root"
            linked_root.symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(InstallError, "符号链接"):
                install_skill(
                    host="codex",
                    source=SKILL_ROOT,
                    target_root=linked_root,
                    apply=True,
                )

            self.assertFalse((outside / "skills").exists())

    def test_force_copy_failure_preserves_existing_installation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target_root = Path(tmp) / "target"
            existing = target_root / "skills" / "virtual-companion"
            existing.mkdir(parents=True)
            marker = existing / "keep.txt"
            marker.write_text("keep", encoding="utf-8")

            with patch(
                "companion_kit.installer.shutil.copytree",
                side_effect=OSError("simulated copy failure"),
            ):
                with self.assertRaisesRegex(InstallError, "无法安装"):
                    install_skill(
                        host="codex",
                        source=SKILL_ROOT,
                        target_root=target_root,
                        apply=True,
                        force=True,
                    )

            self.assertTrue(marker.is_file())

    def test_refuses_unknown_host(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(InstallError):
                install_skill(
                    host="unknown",
                    source=SKILL_ROOT,
                    target_root=Path(tmp),
                    apply=False,
                )


if __name__ == "__main__":
    unittest.main()
