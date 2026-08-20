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
_ENVELOPE_MARKERS = (
    "[[COMPANION_KIT_PHOTO_V1]]",
    "[[COMPANION_KIT_PHOTO_V2]]",
    "[[COMPANION_KIT_PHOTO_V3]]",
)


def _plugin_root() -> Path:
    configured = os.environ.get("PLUGIN_ROOT", "").strip()
    return Path(configured).resolve() if configured else Path(__file__).resolve().parents[1]


def _decision(
    decision: str,
    *,
    reason: str | None = None,
    updated_input: dict[str, object] | None = None,
    additional_context: str | None = None,
) -> None:
    specific: dict[str, object] = {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
    }
    if reason:
        specific["permissionDecisionReason"] = reason
    if updated_input is not None:
        specific["updatedInput"] = updated_input
    if additional_context:
        specific["additionalContext"] = additional_context
    sys.stdout.write(
        json.dumps({"hookSpecificOutput": specific}, ensure_ascii=False)
    )


def _deny(reason: str) -> int:
    _decision("deny", reason=reason)
    return 0


def main() -> int:
    recognized = False
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if payload.get("hook_event_name") != "PreToolUse":
            return 0
        if str(payload.get("tool_name") or "") not in _IMAGE_TOOL_NAMES:
            return 0
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            return 0
        prompt = tool_input.get("prompt")

        package_root = _plugin_root() / "skills" / "virtual-companion" / "scripts"
        sys.path.insert(0, str(package_root))
        from companion_kit.codex_image_receipts import (
            CodexImageReceiptError,
            CodexImageReceiptStore,
        )
        from companion_kit.codex_runtime import load_codex_runtime_context
        from companion_kit.codex_turn import make_turn_token
        from companion_kit.daily_look import DailyLookDirective
        from companion_kit.daily_look_store import (
            DailyLookStore,
            DailyLookStoreError,
        )
        from companion_kit.hook_health import inspect_hook_bundle
        from companion_kit.photo_moment import (
            PhotoMomentError,
            normalize_photo_moment,
            parse_photo_envelope_details,
        )
        from companion_kit.photo_moment_store import (
            PhotoMomentStore,
            PhotoMomentStoreError,
        )

        session_id = str(payload.get("session_id") or "")
        turn_id = str(payload.get("turn_id") or "")
        tool_use_id = str(payload.get("tool_use_id") or "")
        store = PhotoMomentStore(lock_timeout=0.25)
        has_marker = isinstance(prompt, str) and any(
            marker in prompt for marker in _ENVELOPE_MARKERS
        )
        ticket_present = store.turn_ticket_present(
            session_id=session_id,
            turn_id=turn_id,
        )
        recognized = has_marker or ticket_present
        ticket = store.turn_ticket(session_id=session_id, turn_id=turn_id)
        if ticket is None:
            if has_marker or ticket_present:
                return _deny("人物照片回合票据无效或已过期；请重新发起这次拍照。")
            return 0
        recognized = True
        if not has_marker or not isinstance(prompt, str):
            return _deny("人物照片回合缺少控制信封；本次停止生图，请重新建立照片计划。")

        bundle_digest = inspect_hook_bundle(_plugin_root()).hook_bundle_digest
        token = make_turn_token(
            session_id=session_id,
            turn_id=turn_id,
            mode=ticket.mode,
            hook_bundle_digest=bundle_digest,
        )
        if token != ticket.turn_token:
            return _deny("人物照片控制信封与当前回合不匹配；请重新建立本轮照片计划。")
        try:
            cleaned_prompt, candidate_moment, daily_directive = (
                parse_photo_envelope_details(
                    prompt,
                    expected_token=ticket.turn_token,
                )
            )
        except PhotoMomentError:
            return _deny("人物照片控制信封与当前回合不匹配；请重新建立本轮照片计划。")
        mode = ticket.mode
        if candidate_moment.mode != mode:
            return _deny("人物照片模式与当前回合不匹配；请重新建立本轮照片计划。")
        if daily_directive is None:
            # V1/V2 信封仍可安全使用，但不会凭旧协议改写每日穿搭。
            daily_directive = DailyLookDirective.from_dict(
                {
                    "action": (
                        "preserve_target" if mode == "edit_previous" else "one_shot"
                    ),
                    "look_id": None,
                    "proposal": None,
                }
            )
        if mode == "edit_previous" and daily_directive.action != "preserve_target":
            return _deny("编辑上一张只能保留目标照片中的穿搭，不能同时改写今日穿搭。")
        if mode == "new" and daily_directive.action == "preserve_target":
            return _deny("新照片不能使用编辑目标的穿搭控制；请重新建立照片计划。")

        runtime = load_codex_runtime_context()
        if runtime is None:
            return _deny("人物照片配置当前不可用；不要生成替代人物。")
        if not store.ticket_matches_profile(ticket, runtime.profile.id):
            return _deny("人物照片回合不属于当前 Persona；请重新发起拍照。")
        if candidate_moment.identity_version != runtime.profile.visual.identity_version:
            return _deny("人物身份版本已经变化；请按当前固定形象重新建立照片计划。")
        if runtime.profile.visual.is_locked and (
            runtime.identity_pack is None or runtime.identity_reference is None
        ):
            return _deny("固定人物身份参考当前不可用；不要生成另一张脸。")

        updated = dict(tool_input)
        updated.pop("num_last_images_to_include", None)
        caption_context: str | None = None
        recent = store.recent(
            profile_id=runtime.profile.id,
            identity_version=runtime.profile.visual.identity_version,
        )
        normalized = normalize_photo_moment(
            candidate_moment,
            recent=recent,
            allowed_intimacy_bands=runtime.relationship.photo_bands,
        )
        pack = runtime.identity_pack
        primary_path = runtime.identity_reference

        if mode == "new":
            if "generated_images" in cleaned_prompt:
                return _deny("新照片不能把上一张生成图路径写进图片提示；请按新场景重新描述。")
            if runtime.profile.visual.is_locked:
                assert pack is not None
                selection_brief = cleaned_prompt
                if normalized.framing == "full":
                    selection_brief += " 全身 穿搭"
                elif normalized.framing == "over_shoulder":
                    selection_brief += " 回眸 侧脸"
                selected = pack.select_for_brief(selection_brief)
                updated["referenced_image_paths"] = [
                    str(member.path) for member in selected
                ]
            else:
                # 未锁脸时只能生成待确认候选，不能偷偷沿用任意历史图。
                updated.pop("referenced_image_paths", None)
        else:
            raw_references = tool_input.get("referenced_image_paths")
            if not isinstance(raw_references, list) or not raw_references:
                return _deny(
                    "编辑上一张需要把当前任务图片回执中的目标图作为本地参考，并与主脸一起重试。"
                )
            identity_paths = (
                {str(member.path) for member in pack.members}
                if pack is not None
                else set()
            )
            non_identity_references: list[str] = []
            for raw in raw_references:
                if not isinstance(raw, str) or not raw.strip():
                    return _deny("编辑参考必须是当前任务中可验证的本地图片。")
                if raw in identity_paths:
                    continue
                if raw not in non_identity_references:
                    non_identity_references.append(raw)

            if len(non_identity_references) != 1:
                return _deny("明确编辑一次只能使用一张当前任务目标图，并且始终同时带主脸。")
            raw_target = non_identity_references[0]
            try:
                CodexImageReceiptStore(lock_timeout=0.25).verify_latest(
                    session_id=session_id,
                    source_path=raw_target,
                )
            except CodexImageReceiptError:
                return _deny("上一张照片无法由当前任务图片回执验证；不会退化成新拍。")
            try:
                store.verify_latest_companion_path(
                    profile_id=runtime.profile.id,
                    session_id=session_id,
                    source_path=raw_target,
                    identity_version=runtime.profile.visual.identity_version,
                )
            except PhotoMomentStoreError:
                return _deny("编辑目标不是当前任务最近一次人物照片；不会套用固定人物。")
            target = Path(raw_target).resolve()
            for prompt_path in (raw_target, str(target)):
                cleaned_prompt = cleaned_prompt.replace(
                    prompt_path,
                    "上一张已验证的目标照片",
                )
            if "generated_images" in cleaned_prompt:
                return _deny("编辑提示中不能向图片服务发送本机生成路径。")
            updated["referenced_image_paths"] = (
                [str(primary_path), str(target)]
                if primary_path is not None
                else [str(target)]
            )

        daily_look = None
        try:
            normalized = store.stage_varied(
                profile_id=runtime.profile.id,
                session_id=session_id,
                tool_use_id=tool_use_id,
                photo_moment=normalized,
                allowed_intimacy_bands=runtime.relationship.photo_bands,
            )
            if daily_directive.action in {"use_daily", "replace_daily"}:
                try:
                    daily_look = DailyLookStore(lock_timeout=0.25).stage_for_photo(
                        profile_id=runtime.profile.id,
                        session_id=session_id,
                        tool_use_id=tool_use_id,
                        directive=daily_directive,
                    )
                except DailyLookStoreError:
                    store.discard(session_id=session_id, tool_use_id=tool_use_id)
                    return _deny("今天的穿搭状态已经变化；请重新发起这次拍照。")
        except PhotoMomentStoreError:
            # 配方历史不是身份安全边界；当前图片仍使用权威参考，文案约束直接随工具反馈传递。
            if daily_directive.action in {"use_daily", "replace_daily"}:
                return _deny("今天的照片配方暂时无法安全保存；请重新发起这次拍照。")
            caption_context = normalized.render_caption_context()
        constraints = [normalized.render_image_constraints()]
        if daily_look is not None:
            constraints.append(daily_look.render_image_constraints())
        elif daily_directive.action == "one_shot":
            constraints.append(
                "本轮服饰只按当前画面要求一次性生成，不把它当作人物身份或跨照片固定造型。"
            )
        updated["prompt"] = cleaned_prompt.rstrip() + "\n\n" + "\n".join(constraints)
        _decision(
            "allow",
            updated_input=updated,
            additional_context=caption_context,
        )
    except Exception:
        if recognized:
            return _deny("人物照片调用保护没有安全完成；本次停止生图，不生成替代脸。")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
