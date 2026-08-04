#!/usr/bin/env python3
"""检查整个公开项目是否混入私人状态、人物媒体或本机路径。"""

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SKILL_SCRIPTS = ROOT / "skills" / "virtual-companion" / "scripts"
sys.dont_write_bytecode = True
sys.path.insert(0, str(SKILL_SCRIPTS))

from companion_kit.public_bundle import (  # noqa: E402
    inspect_git_objects,
    inspect_public_tree,
    inspect_release_layout,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="检查 Companion Kit 公开发行边界")
    parser.add_argument(
        "--forbid-text",
        action="append",
        default=[],
        help="额外禁止出现在公开文本中的私人标识；可重复使用",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    forbidden_text = [*args.forbid_text, str(Path.home())]
    failures = inspect_release_layout(ROOT)
    failures.extend(inspect_public_tree(ROOT, forbidden_text=forbidden_text))
    failures.extend(inspect_git_objects(ROOT, forbidden_text=forbidden_text))

    if failures:
        print("公开包检查失败")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(
        "公开包检查通过：工作树与 Git 对象未包含私人标识、常见本机路径、"
        "人格状态、聊天记录、人物媒体或疑似密钥"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
