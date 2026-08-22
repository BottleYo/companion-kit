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
    r"拍一张我看看|拍张我看看|拍给我看看|拍一张看看|给我拍一张看看|"
    r"拍吧|那就拍吧|就这样拍|按这套拍|继续拍|可以[，,]?\s*拍吧|"
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
_QUICK_PENDING_BREAK_RE = re.compile(
    r"(?:分析|解释|检查|评审|修复|调试|实现|开发|设计|测试|重构|写)"
    r".{0,24}(?:代码|项目|功能|接口|模块|bug|报错)",
    re.IGNORECASE,
)
_QUICK_SCOPE_RE = re.compile(
    r"(?:陪伴任务|普通任务|companion\s+task)",
    re.IGNORECASE,
)
_QUICK_OOTD_RE = re.compile(
    r"(?:你|你的).{0,8}(?:今天|今日).{0,8}(?:穿什么|怎么穿|穿搭|ootd)|"
    r"(?:今天|今日)(?:的)?\s*(?:穿搭|ootd).{0,8}(?:是什么|怎么样|怎么搭|给我看看)?",
    re.IGNORECASE,
)
_OOTD_META_RE = re.compile(
    r"(?:分析|评估|讨论|解释|检查|评审|修复|调试|实现|开发|设计|测试|处理|"
    r"功能|方案|逻辑|模块|接口|代码|bug).{0,18}(?:穿搭|ootd)|"
    r"(?:穿搭|ootd).{0,18}(?:功能|方案|逻辑|模块|接口|代码|bug)",
    re.IGNORECASE,
)


def _likely_ootd_query(text: str) -> bool:
    normalized = str(text or "").strip()
    return bool(
        normalized
        and len(normalized) <= 240
        and _QUICK_OOTD_RE.search(normalized)
        and not _OOTD_META_RE.search(normalized)
    )


def _style_anchor(runtime: object) -> str:
    profile = runtime.profile
    return "；".join(
        value
        for value in (
            profile.visual.appearance,
            profile.visual.default_wardrobe,
            profile.intent_summary,
        )
        if str(value or "").strip()
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


def _record_companion_context_health() -> None:
    try:
        from companion_kit.hook_health import COMPANION_CONTEXT, HookHealthStore

        HookHealthStore(plugin_root=_plugin_root()).record_success(COMPANION_CONTEXT)
    except Exception:
        return


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if payload.get("hook_event_name") != "UserPromptSubmit":
            return 0
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not (
            _QUICK_PHOTO_RE.search(prompt)
            or _QUICK_SCOPE_RE.search(prompt)
            or _QUICK_OOTD_RE.search(prompt)
            or _QUICK_PENDING_BREAK_RE.search(prompt)
        ):
            return 0

        package_root = _plugin_root() / "skills" / "virtual-companion" / "scripts"
        sys.path.insert(0, str(package_root))
        from companion_kit.companion_scope import (
            CompanionScopeError,
            CompanionScopeStore,
        )
        from companion_kit.codex_runtime import load_codex_runtime_context
        from companion_kit.codex_turn import (
            CodexScopeCommand,
            CodexTurnKind,
            breaks_pending_photo_context,
            classify_codex_turn,
            classify_scope_command,
            likely_bound_short_photo_request,
            likely_codex_photo_turn,
            likely_pending_photo_followup,
            make_turn_token,
        )
        from companion_kit.hook_health import inspect_hook_bundle
        from companion_kit.daily_look_store import DailyLookStore, DailyLookStoreError
        from companion_kit.photo_moment_store import PhotoMomentStore
        from companion_kit.pending_photo_context import PendingPhotoContextStore

        session_id = str(payload.get("session_id") or "")
        turn_id = str(payload.get("turn_id") or "")
        scope_command = classify_scope_command(prompt)
        if scope_command is CodexScopeCommand.BIND:
            runtime = load_codex_runtime_context(include_identity=False)
            if runtime is None:
                _output_context(
                    "Companion Kit 尚未配置 Persona；本轮不要调用 imagegen，也不要假装已经绑定。"
                    "请自然提示用户先打开人物面板完成初始化。"
                )
                return 0
            try:
                CompanionScopeStore(lock_timeout=0.25).bind(session_id)
            except CompanionScopeError:
                _output_context(
                    "当前任务无法安全保存陪伴绑定；保持普通 Codex 任务，不加载 Persona，"
                    "本轮不要调用 imagegen。请自然提示用户打开人物面板检查状态。"
                )
                return 0
            try:
                PendingPhotoContextStore(lock_timeout=0.25).clear(
                    session_id=session_id
                )
            except Exception:
                pass
            context = runtime.render(
                control_path=(
                    _plugin_root()
                    / "skills"
                    / "virtual-companion"
                    / "scripts"
                    / "companionctl.py"
                ),
                task_scope=session_id,
            )
            if _QUICK_PHOTO_RE.search(prompt) and likely_codex_photo_turn(prompt):
                token = make_turn_token(
                    session_id=session_id,
                    turn_id=turn_id,
                    mode="new",
                    hook_bundle_digest=inspect_hook_bundle(
                        _plugin_root()
                    ).hook_bundle_digest,
                )
                PhotoMomentStore(lock_timeout=0.25).issue_turn(
                    profile_id=runtime.profile.id,
                    session_id=session_id,
                    turn_id=turn_id,
                    mode="new",
                    turn_token=token,
                )
                context += (
                    "\n当前消息同时要求绑定和拍照；本轮只完成绑定，本轮不要调用 imagegen。"
                    "请自然确认已经连接，并请用户下一条直接说想拍什么。"
                )
            _record_companion_context_health()
            _output_context(context)
            return 0
        if scope_command is CodexScopeCommand.UNBIND:
            try:
                CompanionScopeStore(lock_timeout=0.25).unbind(session_id)
            except CompanionScopeError:
                _output_context(
                    "当前任务的陪伴绑定无法安全更新；不要继续扩展 Persona 表达，"
                    "请自然提示用户打开人物面板检查状态。"
                )
                return 0
            try:
                PendingPhotoContextStore(lock_timeout=0.25).clear(
                    session_id=session_id
                )
            except Exception:
                pass
            _output_context(
                "当前任务已退出陪伴任务；从本轮起不再加载 Persona、关系或人物照片规则。"
                "自然简短确认即可，不删除 Persona、关系、记忆或参考照片。"
            )
            return 0

        is_photo_turn = likely_codex_photo_turn(prompt)
        is_bound_short_request = likely_bound_short_photo_request(prompt)
        is_pending_followup = likely_pending_photo_followup(prompt)
        clears_pending = breaks_pending_photo_context(prompt)
        companion_bound = False
        pending_context = None
        runtime = None
        # “陪伴任务怎么实现”或“设计 OOTD 功能”之类的普通讨论可能命中快速门，
        # 但不应因此读取 Persona，更不能向未绑定任务注入人物状态。
        if not is_photo_turn:
            if not (
                _likely_ootd_query(prompt)
                or is_bound_short_request
                or is_pending_followup
                or clears_pending
            ):
                return 0
            try:
                companion_bound = CompanionScopeStore(
                    lock_timeout=0.25
                ).is_bound(session_id)
                if not companion_bound:
                    return 0
            except CompanionScopeError:
                return 0
            if clears_pending and not _likely_ootd_query(prompt):
                try:
                    PendingPhotoContextStore(lock_timeout=0.25).clear(
                        session_id=session_id
                    )
                except Exception:
                    pass
                return 0
            if _likely_ootd_query(prompt):
                runtime = load_codex_runtime_context(include_identity=False)
                if runtime is None:
                    return 0
                try:
                    daily_look = DailyLookStore(lock_timeout=0.25).ensure_today(
                        profile_id=runtime.profile.id,
                        style_anchor=_style_anchor(runtime),
                    )
                except DailyLookStoreError:
                    daily_look = None
                try:
                    continuation_store = PendingPhotoContextStore(lock_timeout=0.25)
                    if daily_look is None:
                        continuation_store.clear(session_id=session_id)
                    else:
                        continuation_store.remember(
                            profile_id=runtime.profile.id,
                            session_id=session_id,
                            source="daily_look",
                            context_ref=daily_look.look_id,
                        )
                except Exception:
                    pass
                _record_companion_context_health()
                _output_context(runtime.render_daily_look(daily_look))
                return 0

            runtime = load_codex_runtime_context()
            if runtime is None:
                return 0
            if is_pending_followup:
                try:
                    pending_context = PendingPhotoContextStore(
                        lock_timeout=0.25
                    ).peek(
                        profile_id=runtime.profile.id,
                        session_id=session_id,
                    )
                except Exception:
                    return 0
            is_photo_turn = likely_codex_photo_turn(
                prompt,
                companion_bound=companion_bound,
                pending_photo_context=pending_context,
            )
            if not is_photo_turn:
                return 0

        if runtime is None:
            runtime = load_codex_runtime_context()
        if runtime is None:
            _output_context(
                "Companion Kit 尚未配置 Persona；本轮不要调用 imagegen，也不要生成替代人物。"
                "请自然提示用户先完成人物初始化。"
            )
            return 0
        store = PhotoMomentStore(lock_timeout=0.25)
        intent = classify_codex_turn(
            prompt,
            previous_image_kind=store.latest_result_state(
                profile_id=runtime.profile.id,
                session_id=session_id,
                identity_version=runtime.profile.visual.identity_version,
            ),
            companion_bound=companion_bound,
            pending_photo_context=pending_context,
        )
        if intent.kind is CodexTurnKind.PASS_THROUGH:
            return 0
        if is_pending_followup:
            try:
                consumed = PendingPhotoContextStore(lock_timeout=0.25).consume(
                    profile_id=runtime.profile.id,
                    session_id=session_id,
                )
            except Exception:
                return 0
            if consumed != pending_context:
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
        if not is_pending_followup:
            try:
                PendingPhotoContextStore(lock_timeout=0.25).clear(
                    session_id=session_id
                )
            except Exception:
                pass
        recent = store.recent(
            profile_id=runtime.profile.id,
            identity_version=runtime.profile.visual.identity_version,
        )
        daily_look = None
        if (
            mode == "new"
            and intent.identity_role is None
            and runtime.profile.visual.is_locked
        ):
            try:
                daily_look = DailyLookStore(lock_timeout=0.25).ensure_today(
                    profile_id=runtime.profile.id,
                    style_anchor=_style_anchor(runtime),
                )
            except DailyLookStoreError:
                daily_look = None
        _output_context(
            runtime.render_photo(
                mode=mode,
                turn_token=token,
                recent_moments=recent,
                daily_look=daily_look,
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
