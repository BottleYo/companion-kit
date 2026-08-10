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
        from companion_kit.config import load_profile
        from companion_kit.initializer import default_profile_path
        from companion_kit.photo_moment_store import PhotoMomentStore

        profile_path = default_profile_path("codex")
        if not profile_path.is_file():
            return 0
        profile = load_profile(profile_path)
        session_id = str(payload.get("session_id") or "")
        tool_use_id = str(payload.get("tool_use_id") or "")
        moments = PhotoMomentStore(lock_timeout=0.25)
        pending = moments.peek(
            profile_id=profile.id,
            session_id=session_id,
            tool_use_id=tool_use_id,
        )
        paths = extract_codex_generated_paths(payload.get("tool_response"))
        if not paths:
            if pending is not None:
                moments.discard(
                    session_id=session_id,
                    tool_use_id=tool_use_id,
                )
            return 0
        recorded = CodexImageReceiptStore(lock_timeout=0.25).record(
            session_id=session_id,
            tool_use_id=tool_use_id,
            paths=paths,
        )
        if recorded:
            try:
                # 每个新的图片结果先按普通图片落位；只有存在同一工具调用的
                # PhotoMoment，才会在下面升级成人物照片。重复 Post 不改写状态。
                moments.record_image_result(
                    profile_id=profile.id,
                    session_id=session_id,
                    paths=paths,
                    is_companion=False,
                )
            except Exception:
                # latest receipt 与内容哈希仍会阻止旧摘要授权新的普通图片。
                pass
            try:
                from companion_kit.hook_health import HookHealthStore, POST_TOOL_USE

                HookHealthStore(plugin_root=_plugin_root()).record_success(POST_TOOL_USE)
            except Exception:
                # 图片回执已保存时，面板健康记录失败不能影响原图片工具结果。
                pass
            if pending is not None:
                try:
                    moments.record_image_result(
                        profile_id=profile.id,
                        session_id=session_id,
                        paths=paths,
                        is_companion=True,
                    )
                except Exception:
                    # 照片仍已真实生成，但不会把它错误开放成“编辑上一张”。
                    pass
                result = moments.commit(
                    profile_id=profile.id,
                    session_id=session_id,
                    tool_use_id=tool_use_id,
                )
                photo_moment = result.photo_moment or pending
                sys.stdout.write(
                    json.dumps(
                        {
                            "hookSpecificOutput": {
                                "hookEventName": "PostToolUse",
                                "additionalContext": photo_moment.render_caption_context(),
                            }
                        },
                        ensure_ascii=False,
                    )
                )
    except Exception:
        # 图片回执缺失时，后续身份暂存会失败关闭；不能影响 Codex 原任务。
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
