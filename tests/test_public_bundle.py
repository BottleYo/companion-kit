from pathlib import Path, PureWindowsPath
import subprocess
import tempfile
import unittest

from companion_kit.public_bundle import (
    contains_absolute_path,
    inspect_git_objects,
    inspect_public_tree,
    inspect_release_layout,
)


def initialize_repository(root: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Companion Test"],
        cwd=root,
        check=True,
    )


class PublicBundleTests(unittest.TestCase):
    def test_worktree_path_name_is_scanned_for_private_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "public"
            root.mkdir()
            (root / "private-sentinel-notes.txt").write_text(
                "generic",
                encoding="utf-8",
            )

            failures = inspect_public_tree(
                root,
                forbidden_text=("private-sentinel",),
            )

            self.assertTrue(any("公开路径" in failure for failure in failures))

    def test_nested_git_metadata_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "public"
            nested = root / "vendor" / ".git"
            nested.mkdir(parents=True)

            failures = inspect_public_tree(root)

            self.assertTrue(any("嵌套 Git" in failure for failure in failures))

    def test_release_layout_rejects_unlisted_top_level_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "public"
            root.mkdir()
            for filename in ("README.md", "PRIVACY.md", "LICENSE", "pyproject.toml"):
                (root / filename).write_text("demo", encoding="utf-8")
            for dirname in ("docs", "scripts", "skills", "tests"):
                (root / dirname).mkdir()
            (root / "unexpected-notes.txt").write_text("demo", encoding="utf-8")

            failures = inspect_release_layout(root)

            self.assertTrue(any("允许列表" in failure for failure in failures))

    def test_detects_common_cross_platform_private_paths(self) -> None:
        paths = (
            str(Path("/").joinpath("Users", "example", "private", "portrait.jpg")),
            str(Path("/").joinpath("home", "example", "private", "portrait.jpg")),
            str(Path("/").joinpath("Volumes", "Private", "portrait.jpg")),
            str(PureWindowsPath("C:/", "Users", "example", "portrait.jpg")),
            str(PureWindowsPath("//server/share", "private", "portrait.jpg")),
            "~" + "/" + "private" + "/" + "portrait.jpg",
        )

        for value in paths:
            with self.subTest(value=value):
                self.assertTrue(contains_absolute_path(value))

        self.assertFalse(contains_absolute_path("assets/demo_companion.toml"))
        self.assertFalse(contains_absolute_path("/photo 窗边半身照"))

    def test_symlink_target_is_never_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "public"
            root.mkdir()
            (root / "SKILL.md").write_text("demo", encoding="utf-8")
            outside = Path(tmp) / "outside.txt"
            outside.write_text("private-sentinel", encoding="utf-8")
            (root / "linked.txt").symlink_to(outside)

            failures = inspect_public_tree(
                root,
                forbidden_text=("private-sentinel",),
            )

            self.assertTrue(any("符号链接" in failure for failure in failures))
            self.assertFalse(any("私人标识" in failure for failure in failures))

    def test_large_text_is_scanned_completely(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "public"
            root.mkdir()
            (root / "SKILL.md").write_text(
                "x" * 1_100_000 + "private-sentinel",
                encoding="utf-8",
            )

            failures = inspect_public_tree(
                root,
                forbidden_text=("private-sentinel",),
            )

            self.assertTrue(any("私人标识" in failure for failure in failures))

    def test_deleted_private_text_is_still_detected_in_git_objects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "public"
            root.mkdir()
            initialize_repository(root)
            private = root / "removed.txt"
            private.write_text("private-sentinel", encoding="utf-8")
            subprocess.run(["git", "add", "removed.txt"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-m", "temporary"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            private.unlink()
            subprocess.run(["git", "add", "-u"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-m", "remove"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )

            failures = inspect_git_objects(
                root,
                forbidden_text=("private-sentinel",),
            )

            self.assertTrue(any("Git blob" in failure for failure in failures))

    def test_git_tree_path_is_scanned_even_when_blob_is_generic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "public"
            root.mkdir()
            initialize_repository(root)
            private_path = root / "private-sentinel-notes.txt"
            private_path.write_text("generic", encoding="utf-8")
            subprocess.run(
                ["git", "add", private_path.name],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "baseline"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )

            failures = inspect_git_objects(
                root,
                forbidden_text=("private-sentinel",),
            )

            self.assertTrue(any("Git tree 路径" in failure for failure in failures))

    def test_commit_author_and_annotated_tag_message_are_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "public"
            root.mkdir()
            initialize_repository(root)
            subprocess.run(
                ["git", "config", "user.name", "author-sentinel"],
                cwd=root,
                check=True,
            )
            (root / "README.md").write_text("generic", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-m", "baseline"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "tag", "-a", "safe-tag", "-m", "tag-sentinel"],
                cwd=root,
                check=True,
            )

            failures = inspect_git_objects(
                root,
                forbidden_text=("author-sentinel", "tag-sentinel"),
            )

            self.assertTrue(any("Git commit 元数据" in failure for failure in failures))
            self.assertTrue(any("Git tag 元数据" in failure for failure in failures))

    def test_linked_worktree_gitfile_uses_shared_object_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "main"
            linked = Path(tmp) / "linked"
            root.mkdir()
            initialize_repository(root)
            (root / "README.md").write_text(
                "private-sentinel",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-m", "baseline"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "worktree", "add", "-b", "linked-check", str(linked)],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue((linked / ".git").is_file())

            failures = inspect_git_objects(
                linked,
                forbidden_text=("private-sentinel",),
            )

            self.assertTrue(any("Git blob" in failure for failure in failures))

    def test_historical_gitlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "public"
            root.mkdir()
            initialize_repository(root)
            (root / "README.md").write_text("generic", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-m", "baseline"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            commit_id = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(
                [
                    "git",
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"160000,{commit_id},vendor/dependency",
                ],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "add dependency"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )

            failures = inspect_git_objects(root)

            self.assertTrue(any("子模块" in failure for failure in failures))

    def test_broken_git_symlink_is_not_treated_as_no_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "public"
            root.mkdir()
            (root / ".git").symlink_to(root / "missing-git-dir")

            failures = inspect_git_objects(root)

            self.assertTrue(any("Git 元数据入口" in failure for failure in failures))

    def test_git_replace_cannot_hide_original_private_blob(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "public"
            root.mkdir()
            initialize_repository(root)
            (root / "README.md").write_text(
                "private-sentinel",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-m", "baseline"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            original = subprocess.run(
                ["git", "rev-parse", "HEAD:README.md"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            replacement = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                input="generic",
            ).stdout.strip()
            subprocess.run(
                ["git", "replace", original, replacement],
                cwd=root,
                check=True,
            )

            failures = inspect_git_objects(
                root,
                forbidden_text=("private-sentinel",),
            )

            self.assertTrue(any("Git blob" in failure for failure in failures))
