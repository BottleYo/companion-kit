from __future__ import annotations

from pathlib import Path
import os
import stat

from .image_assets import ImageAssetError, sanitize_png


class EventAdapterError(ValueError):
    """事件宿主无法安全接管当前图片。"""


_EVENT_HOSTS = {"openclaw", "hermes"}


def _prompt(value: str) -> str:
    prompt = str(value or "").strip()
    if (
        not prompt
        or len(prompt) > 8_000
        or any(ord(character) < 32 and character not in "\n\t" for character in prompt)
    ):
        raise EventAdapterError("图片提示无效")
    return prompt


def openclaw_native_preview_request(prompt: str) -> dict[str, object]:
    """构造 OpenClaw 原生快速试拍参数；证据仍属于宿主管理。"""

    return {
        "mode": "native_preview",
        "proof": "host_managed",
        "tool": "image_generate",
        "arguments": {
            "action": "generate",
            "prompt": _prompt(prompt),
            "model": "openai/gpt-image-2",
            "quality": "high",
            "size": "1024x1536",
            "outputFormat": "png",
            "count": 1,
        },
        "delivery": "host_managed_current_session",
    }


def _safe_existing_png(raw: str | Path) -> Path:
    path = Path(raw)
    if not path.is_absolute() or ".." in path.parts:
        raise EventAdapterError("当前会话图片路径无效")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except OSError as exc:
            raise EventAdapterError("当前会话图片不可用") from exc
        if stat.S_ISLNK(mode):
            raise EventAdapterError("当前会话图片路径不能经过符号链接")
    normalized = Path(os.path.abspath(path))
    if normalized.suffix.casefold() != ".png" or not normalized.is_file():
        raise EventAdapterError("当前会话图片必须是已生成的 PNG")
    try:
        sanitize_png(normalized.read_bytes())
    except (OSError, ImageAssetError) as exc:
        raise EventAdapterError("当前会话图片必须是有效的安全 PNG") from exc
    return normalized


def format_current_reply_handoff(
    *,
    host: str,
    artifact_path: str | Path,
) -> dict[str, object]:
    """只生成当前回复的宿主指令，不接受联系人或任意发送目标。"""

    if host not in _EVENT_HOSTS:
        raise EventAdapterError("当前宿主不支持事件型图片接管")
    path = _safe_existing_png(artifact_path)
    if host == "openclaw":
        return {
            "host": host,
            "transport": "current_reply_message_tool",
            "action": "send",
            "media": str(path),
            "target_policy": "current_reply_only",
        }
    return {
        "host": host,
        "transport": "current_response_media",
        "response_directive": f"MEDIA:{path}",
        "target_policy": "current_reply_only",
    }
