from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
from typing import Iterable


_FORBIDDEN_NAMES = {"memory.md", "soul.md", "user.md"}
_FORBIDDEN_DIRS = {
    ".companion",
    ".companion-kit",
    "chats",
    "local-profiles",
    "memories",
    "memory",
    "outputs",
    "sessions",
    "transcripts",
}
_JUNK_DIRS = {".pytest_cache", ".venv", "__pycache__", "build", "dist"}
_SECRET_FILE_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "id_ed25519",
    "id_rsa",
    "secrets.toml",
}
_SECRET_FILE_PREFIXES = (".env.", "auth.", "credentials.", "oauth.", "secrets.")
_SECRET_FILE_EXTENSIONS = {".key", ".p12", ".pem", ".pfx"}
_PERSON_MEDIA_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".dng",
    ".gif",
    ".heic",
    ".jpeg",
    ".jpg",
    ".png",
    ".raw",
    ".tif",
    ".tiff",
    ".webp",
}
_PRIVATE_STATE_EXTENSIONS = {".db", ".sqlite", ".sqlite3"}
_PRIVATE_STATE_PREFIXES = ("chat_", "conversation_", "session_", "transcript_")
_COMMON_HOME_PATH_RE = re.compile(
    r"(?:/"
    r"(?:Users|home)/[^/\s'\"<>`]+(?:/[^\s'\"<>`]+)+"
    r"|/"
    r"root(?:/[^\s'\"<>`]+)+"
    r"|[A-Za-z]:\\Users\\[^\\\s'\"<>`]+(?:\\[^\s'\"<>`]+)+)"
)
_UNIX_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9:.])/"
    r"(?:Users|home|root|private|var|tmp|data|mnt|Volumes|etc|opt|srv)/"
    r"[^/\s'\"<>`]+(?:/[^/\s'\"<>`]+)*"
)
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"
    r"(?:[^\\/\s'\"<>`]+[\\/])+[^\\/\s'\"<>`]+"
)
_WINDOWS_UNC_PATH_RE = re.compile(
    r"(?<!\\)\\\\[^\\\s'\"<>`]+\\[^\\\s'\"<>`]+"
    r"(?:\\[^\\\s'\"<>`]+)+"
)
_TILDE_PATH_RE = re.compile(r"~" r"/(?:[^/\s'\"<>`]+/)*[^/\s'\"<>`]+")
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
)
_RELEASE_ROOT_ALLOWLIST = {
    ".agents",
    ".claude-plugin",
    ".codex-plugin",
    ".gitignore",
    "LICENSE",
    "PRIVACY.md",
    "README.md",
    "assets",
    "docs",
    "hooks",
    "pyproject.toml",
    "scripts",
    "skills",
    "tests",
}
_REQUIRED_RELEASE_ROOT_ENTRIES = {
    "LICENSE",
    "PRIVACY.md",
    "README.md",
    "docs",
    "pyproject.toml",
    "scripts",
    "skills",
    "tests",
}


def contains_absolute_path(text: str) -> bool:
    """识别常见 Unix、macOS 与 Windows 本机路径。"""

    return any(
        pattern.search(text)
        for pattern in (
            _COMMON_HOME_PATH_RE,
            _UNIX_ABSOLUTE_PATH_RE,
            _WINDOWS_ABSOLUTE_PATH_RE,
            _WINDOWS_UNC_PATH_RE,
            _TILDE_PATH_RE,
        )
    )


def inspect_release_layout(root: str | Path) -> list[str]:
    """用顶层允许列表约束最终发行输入，避免临时文件被顺手打包。"""

    root_path = Path(root).expanduser().absolute()
    if not root_path.is_dir():
        return [f"公开项目目录不存在：{root_path}"]
    try:
        entries = {entry.name for entry in root_path.iterdir() if entry.name != ".git"}
    except OSError as exc:
        return [f"无法检查发行允许列表：{exc}"]

    failures = [
        f"公开项目顶层条目不在发行允许列表：{name}"
        for name in sorted(entries - _RELEASE_ROOT_ALLOWLIST)
    ]
    failures.extend(
        f"公开项目缺少必需发行条目：{name}"
        for name in sorted(_REQUIRED_RELEASE_ROOT_ENTRIES - entries)
    )
    return failures


def _relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root))


def _contains_forbidden_term(value: str, terms: tuple[str, ...]) -> bool:
    folded = value.casefold()
    return any(term.casefold() in folded for term in terms)


def _git_path_policy_issue(path: str, terms: tuple[str, ...]) -> str | None:
    """检查历史 tree 中的路径；不读取路径指向的对象。"""

    normalized = path.replace("\\", "/").strip("/")
    if not normalized:
        return "Git tree 包含空路径"
    if _contains_forbidden_term(normalized, terms):
        return "Git tree 路径包含私人标识"
    if any(pattern.search(normalized) for pattern in _SECRET_VALUE_PATTERNS):
        return "Git tree 路径包含疑似密钥值"

    parts = tuple(part.casefold() for part in normalized.split("/") if part)
    filename = parts[-1]
    suffix = Path(filename).suffix.casefold()
    if any(part in _FORBIDDEN_DIRS for part in parts[:-1]):
        return "Git tree 路径包含私人状态目录"
    if any(part in _JUNK_DIRS for part in parts[:-1]):
        return "Git tree 路径包含构建或缓存残留"
    if filename in _FORBIDDEN_NAMES:
        return "Git tree 路径包含禁止的人格状态文件"
    if (
        filename in _SECRET_FILE_NAMES
        or filename.startswith(_SECRET_FILE_PREFIXES)
        or suffix in _SECRET_FILE_EXTENSIONS
    ):
        return "Git tree 路径包含疑似密钥文件"
    if suffix in _PERSON_MEDIA_EXTENSIONS:
        return "Git tree 路径包含人物媒体"
    if suffix in _PRIVATE_STATE_EXTENSIONS or filename.startswith(
        _PRIVATE_STATE_PREFIXES
    ):
        return "Git tree 路径包含会话状态"
    return None


def _text_issue(
    path: Path,
    *,
    root: Path,
    forbidden_text: tuple[str, ...],
) -> str | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                folded = line.casefold()
                if any(term.casefold() in folded for term in forbidden_text):
                    return f"公开文本包含私人标识：{_relative(path, root)}"
                if any(pattern.search(line) for pattern in _SECRET_VALUE_PATTERNS):
                    return f"公开文本包含疑似密钥值：{_relative(path, root)}"
                if contains_absolute_path(line):
                    return f"公开文本包含本机绝对路径：{_relative(path, root)}"
    except (OSError, UnicodeDecodeError):
        return f"公开项目包含无法安全检查的二进制或非 UTF-8 文件：{_relative(path, root)}"
    return None


def inspect_public_tree(
    root: str | Path,
    *,
    forbidden_text: Iterable[str] = (),
) -> list[str]:
    """只检查公开树本身；遇到符号链接或私人目录时绝不跟随读取。"""

    root_path = Path(root).expanduser().absolute()
    if root_path.is_symlink():
        return ["公开项目根目录不能是符号链接"]
    if not root_path.is_dir():
        return [f"公开项目目录不存在：{root_path}"]

    terms = tuple(term for term in forbidden_text if term)
    failures: list[str] = []
    for current, dirnames, filenames in os.walk(root_path, followlinks=False):
        current_path = Path(current)

        for dirname in list(dirnames):
            path = current_path / dirname
            relative = _relative(path, root_path)
            lowered = dirname.casefold()
            if path.is_symlink():
                failures.append(f"公开项目不得包含符号链接：{relative}")
                dirnames.remove(dirname)
            elif lowered == ".git":
                if current_path != root_path:
                    failures.append(f"公开项目包含嵌套 Git 元数据：{relative}")
                dirnames.remove(dirname)
            elif _contains_forbidden_term(relative, terms):
                failures.append(f"公开路径包含私人标识：{relative}")
                dirnames.remove(dirname)
            elif lowered in _FORBIDDEN_DIRS:
                failures.append(f"公开项目不得包含私人状态目录：{relative}")
                dirnames.remove(dirname)
            elif lowered in _JUNK_DIRS:
                failures.append(f"公开项目包含构建或缓存残留：{relative}")
                dirnames.remove(dirname)

        for filename in filenames:
            path = current_path / filename
            relative = _relative(path, root_path)
            lowered = filename.casefold()
            suffix = path.suffix.casefold()

            if lowered == ".git":
                if current_path == root_path:
                    # linked worktree 使用 gitfile；它是仓库元数据，不属于发行输入。
                    continue
                failures.append(f"公开项目包含嵌套 Git 元数据：{relative}")
                continue
            if path.is_symlink():
                failures.append(f"公开项目不得包含符号链接：{relative}")
                continue
            if not path.is_file():
                failures.append(f"公开项目包含非常规文件：{relative}")
                continue
            if _contains_forbidden_term(relative, terms):
                failures.append(f"公开路径包含私人标识：{relative}")
                continue
            if lowered in _FORBIDDEN_NAMES:
                failures.append(f"公开项目包含禁止的人格状态文件：{relative}")
                continue
            if (
                lowered in _SECRET_FILE_NAMES
                or lowered.startswith(_SECRET_FILE_PREFIXES)
                or suffix in _SECRET_FILE_EXTENSIONS
            ):
                failures.append(f"公开项目包含疑似密钥文件：{relative}")
                continue
            if suffix in _PERSON_MEDIA_EXTENSIONS:
                failures.append(f"公开项目不得包含人物媒体：{relative}")
                continue
            if suffix in _PRIVATE_STATE_EXTENSIONS or lowered.startswith(
                _PRIVATE_STATE_PREFIXES
            ):
                failures.append(f"公开项目不得包含会话状态：{relative}")
                continue

            issue = _text_issue(
                path,
                root=root_path,
                forbidden_text=terms,
            )
            if issue:
                failures.append(issue)

    return failures


def inspect_git_objects(
    root: str | Path,
    *,
    forbidden_text: Iterable[str] = (),
) -> list[str]:
    """扫描历史 blob、tree、commit、tag 与 ref，包括不可达对象。"""

    root_path = Path(root).expanduser().absolute()
    git_marker = root_path / ".git"
    if git_marker.is_symlink():
        return ["Git 元数据入口无效或经过符号链接"]
    if not git_marker.exists():
        return []
    if not (git_marker.is_dir() or git_marker.is_file()):
        return ["Git 元数据入口无效或经过符号链接"]
    terms = tuple(term for term in forbidden_text if term)
    try:
        repository = subprocess.run(
            ["git", "--no-replace-objects", "rev-parse", "--is-inside-work-tree"],
            cwd=root_path,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if repository.stdout.strip() != "true":
            return ["Git 元数据入口不属于有效工作树"]
        inventory = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "cat-file",
                "--batch-all-objects",
                "--batch-check=%(objectname) %(objecttype) %(objectsize)",
            ],
            cwd=root_path,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"无法检查 Git 对象：{exc}"]

    failures: list[str] = []
    for raw_line in inventory.stdout.splitlines():
        parts = raw_line.split()
        if len(parts) != 3:
            continue
        object_id, object_type, raw_size = parts
        try:
            size = int(raw_size)
        except ValueError:
            failures.append(f"Git 对象大小无效：{object_id[:12]}")
            continue
        if object_type in {"blob", "commit", "tag"} and size > 20 * 1024 * 1024:
            failures.append(f"Git 对象过大，无法安全发布：{object_id[:12]}")
            continue
        if object_type == "tree":
            try:
                tree = subprocess.run(
                    [
                        "git",
                        "--no-replace-objects",
                        "ls-tree",
                        "-rz",
                        "--full-tree",
                        object_id,
                    ],
                    cwd=root_path,
                    check=True,
                    capture_output=True,
                    timeout=120,
                ).stdout
            except (OSError, subprocess.SubprocessError) as exc:
                failures.append(f"无法读取 Git tree {object_id[:12]}：{exc}")
                continue
            for record in tree.split(b"\0"):
                if not record:
                    continue
                try:
                    metadata, raw_path = record.split(b"\t", 1)
                    mode = metadata.split(b" ", 1)[0].decode("ascii")
                    historical_path = raw_path.decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    failures.append(
                        f"Git tree 包含无法安全检查的路径：{object_id[:12]}"
                    )
                    continue
                if mode == "120000":
                    failures.append(
                        f"Git tree 包含符号链接：{object_id[:12]}:{historical_path}"
                    )
                    continue
                if mode == "160000":
                    failures.append(
                        f"Git tree 包含无法内联检查的子模块：{object_id[:12]}:{historical_path}"
                    )
                    continue
                issue = _git_path_policy_issue(historical_path, terms)
                if issue:
                    failures.append(f"{issue}：{object_id[:12]}:{historical_path}")
            continue
        if object_type not in {"blob", "commit", "tag"}:
            continue
        try:
            blob = subprocess.run(
                [
                    "git",
                    "--no-replace-objects",
                    "cat-file",
                    object_type,
                    object_id,
                ],
                cwd=root_path,
                check=True,
                capture_output=True,
                timeout=120,
            ).stdout
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            failures.append(f"Git 对象包含无法检查的二进制内容：{object_id[:12]}")
            continue
        except (OSError, subprocess.SubprocessError) as exc:
            failures.append(f"无法读取 Git 对象 {object_id[:12]}：{exc}")
            continue

        label = {
            "blob": "Git blob",
            "commit": "Git commit 元数据",
            "tag": "Git tag 元数据",
        }[object_type]
        if _contains_forbidden_term(text, terms):
            failures.append(f"{label}包含私人标识：{object_id[:12]}")
        elif any(pattern.search(text) for pattern in _SECRET_VALUE_PATTERNS):
            failures.append(f"{label}包含疑似密钥值：{object_id[:12]}")
        elif contains_absolute_path(text):
            failures.append(f"{label}包含本机绝对路径：{object_id[:12]}")

    try:
        refs = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "for-each-ref",
                "--format=%(refname)",
            ],
            cwd=root_path,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError) as exc:
        failures.append(f"无法检查 Git 引用名称：{exc}")
    else:
        for ref in refs:
            if _contains_forbidden_term(ref, terms):
                failures.append(f"Git 引用名称包含私人标识：{ref}")
            elif any(pattern.search(ref) for pattern in _SECRET_VALUE_PATTERNS):
                failures.append(f"Git 引用名称包含疑似密钥值：{ref}")

    return list(dict.fromkeys(failures))
