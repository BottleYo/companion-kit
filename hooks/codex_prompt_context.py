#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys


sys.dont_write_bytecode = True

_QUICK_PHOTO_RE = re.compile(
    r"(?:自拍|照片|相片|拍张|拍.{0,4}张|再拍|重拍|重新拍|换个场景再来|换个发型|上一张|刚才那张|刚刚那张|这张图|"
    r"(?:(?:帮我|给我)?(?:生成|做|画|出|来)|(?:我)?想要).{0,10}(?:(?:一|两|三|四|五|几|多)?(?:张|组|些))?.{0,10}你(?:的)?(?:自拍|照片|相片|人物照|人像照|图|样子)|"
    r"(?:给我|发我|发来|来|想看|想看看|看看|让我看看).{0,10}(?:(?:一|两|三|四|五|几|多)?(?:张|组|些))?.{0,10}你(?:的)?(?:自拍|照片|相片|人物照|人像照|图|样子)|"
    r"(?:给我|想看|看看|发|来|拍).{0,10}(?:(?:一|两|三|四|五|几|多)?张)?.{0,6}你(?:在|正在)?(?:做饭|吃饭|喝咖啡|喝茶|散步|运动|健身|看书|工作|上班|旅行|逛街|做瑜伽|晒太阳|做甜点|做早餐|做晚饭|睡觉|起床|化妆|试衣服|开车|坐车|唱歌|跳舞|弹琴|打游戏).{0,8}(?:的)?(?:自拍|照片|相片|图|样子)|"
    r"(?:给我|想看|看看|发|来|拍).{0,18}(?:你现在|你今天|你本人|你在|你穿|你戴|你刚)|"
    r"(?:增强|补充|提高|加一张).{0,12}(?:侧脸|全身|体型|身材)|"
    r"(?:侧脸|全身|体型|身材).{0,12}(?:稳定|参考)|"
    r"take (?:a|another) (?:photo|selfie)|send (?:me )?(?:a|another) (?:photo|selfie)|"
    r"show me (?:a|another|your) (?:photo|selfie|picture)|"
    r"(?:generate|create|make|draw|want|would like|send|show).{0,18}(?:photo|selfie|picture).{0,12}(?:of you|of your face)|"
    r"(?:(?:generate|create|make|draw|want|would like)\s+|(?:send|show)(?:\s+me)?\s+)your\s+(?:photo|selfie|picture))",
    re.IGNORECASE,
)


def _plugin_root() -> Path:
    configured = os.environ.get("PLUGIN_ROOT", "").strip()
    return Path(configured).resolve() if configured else Path(__file__).resolve().parents[1]


def _output_context(context: str) -> None:
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": context,
                }
            },
            ensure_ascii=False,
        )
    )


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if payload.get("hook_event_name") != "UserPromptSubmit":
            return 0
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not _QUICK_PHOTO_RE.search(prompt):
            return 0

        package_root = _plugin_root() / "skills" / "virtual-companion" / "scripts"
        sys.path.insert(0, str(package_root))
        from companion_kit.codex_runtime import load_codex_runtime_context
        from companion_kit.codex_turn import (
            CodexTurnKind,
            classify_codex_turn,
            make_turn_token,
        )
        from companion_kit.hook_health import inspect_hook_bundle
        from companion_kit.photo_moment_store import PhotoMomentStore

        runtime = load_codex_runtime_context()
        if runtime is None:
            return 0
        session_id = str(payload.get("session_id") or "")
        turn_id = str(payload.get("turn_id") or "")
        store = PhotoMomentStore(lock_timeout=0.25)
        intent = classify_codex_turn(
            prompt,
            previous_image_kind=store.latest_result_state(
                profile_id=runtime.profile.id,
                session_id=session_id,
            ),
        )
        if intent.kind is CodexTurnKind.PASS_THROUGH:
            return 0
        mode = (
            "edit_previous"
            if intent.kind is CodexTurnKind.PHOTO_EDIT_PREVIOUS
            else "new"
        )
        token = make_turn_token(
            session_id=session_id,
            turn_id=turn_id,
            mode=mode,
            hook_bundle_digest=inspect_hook_bundle(
                _plugin_root()
            ).hook_bundle_digest,
        )
        store.issue_turn(
            profile_id=runtime.profile.id,
            session_id=session_id,
            turn_id=turn_id,
            mode=mode,
            turn_token=token,
        )
        recent = store.recent(
            profile_id=runtime.profile.id,
            identity_version=runtime.profile.visual.identity_version,
        )
        _output_context(
            runtime.render_photo(
                mode=mode,
                turn_token=token,
                recent_moments=recent,
                control_path=(
                    _plugin_root()
                    / "skills"
                    / "virtual-companion"
                    / "scripts"
                    / "companionctl.py"
                ),
                task_scope=session_id,
                enhancement_role=intent.identity_role,
            )
        )
    except Exception:
        # 识别为人物照片却无法建立私有控制上下文时，宁可暂停照片，也不冒险换脸。
        try:
            _output_context(
                "本轮人物照片控制上下文无法安全建立；不要调用 imagegen，也不要改用纯文字生成另一张脸。聊天和其他任务照常。"
            )
        except Exception:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
