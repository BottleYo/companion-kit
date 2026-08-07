#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import sys


sys.dont_write_bytecode = True


_IMAGE_TOOL_NAMES = {
    "image_gen__imagegen",
    "mcp__image_gen__imagegen",
    "imagegen",
}


def _plugin_root() -> Path:
    configured = os.environ.get("PLUGIN_ROOT", "").strip()
    return Path(configured).resolve() if configured else Path(__file__).resolve().parents[1]


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if payload.get("hook_event_name") != "PostToolUse":
            return 0
        if str(payload.get("tool_name") or "") not in _IMAGE_TOOL_NAMES:
            return 0
        package_root = _plugin_root() / "skills" / "virtual-companion" / "scripts"
        sys.path.insert(0, str(package_root))
        from companion_kit.codex_image_receipts import (
            CodexImageReceiptStore,
            extract_codex_generated_paths,
        )
        from companion_kit.initializer import default_profile_path

        if not default_profile_path("codex").is_file():
            return 0
        paths = extract_codex_generated_paths(payload.get("tool_response"))
        if not paths:
            return 0
        recorded = CodexImageReceiptStore().record(
            session_id=str(payload.get("session_id") or ""),
            tool_use_id=str(payload.get("tool_use_id") or ""),
            paths=paths,
        )
        if recorded:
            try:
                from companion_kit.hook_health import HookHealthStore, POST_TOOL_USE

                HookHealthStore(plugin_root=_plugin_root()).record_success(POST_TOOL_USE)
            except Exception:
                # 图片回执已保存时，面板健康记录失败不能影响原图片工具结果。
                pass
    except Exception:
        # 图片回执缺失时，后续身份暂存会失败关闭；不能影响 Codex 原任务。
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
