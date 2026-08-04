from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


class PhotoIntentKind(str, Enum):
    PASS_THROUGH = "pass_through"
    CLARIFY = "clarify"
    PHOTO = "photo"


@dataclass(frozen=True)
class PhotoIntent:
    kind: PhotoIntentKind
    brief: str = ""


_COMMAND_RE = re.compile(r"^/(?:companion-)?photo(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)
_LABEL_RE = re.compile(r"^(?:照片|拍照|人物照片|photo)\s*[:：]\s*(.*)$", re.IGNORECASE | re.DOTALL)
_HARD_CANCEL_RE = re.compile(
    r"^(?:"
    r"(?:不要|不用|不需要|不想|无需|别|禁止|请勿)(?:再)?"
    r"(?:生成|生图|出图|制作|创建)"
    r"|(?:取消|停止)(?:生成|生图|出图|拍照|照片|图片)?"
    r")",
    re.IGNORECASE,
)
_AMBIGUOUS_CANCEL_RE = re.compile(
    r"^(?:不要|不用|不需要|不想|无需|别|禁止|请勿)(?:再)?(?:拍|发)",
    re.IGNORECASE,
)
_NON_EXECUTION_CUE_RE = re.compile(
    r"(?:只(?:讨论|分析|做|写|评审|检查|优化|转述)|"
    r"(?:方案|插件|代码|测试|提示词)(?:讨论|分析|评审|检查|优化)?|"
    r"不(?:执行|生成|生图|出图))",
    re.IGNORECASE,
)
_META_REQUEST_RE = re.compile(
    r"^(?:请)?(?:"
    r"(?:讨论|分析|解释|评审|检查|测试|优化)(?:一下|这|该|上面|以下|生成|照片|图片|构图|提示词|方案|\s)"
    r"|(?:写|实现|开发|设计|修复)(?:一个|一段|这|该|插件|代码|测试|方案|\s)"
    r"|转述"
    r")",
    re.IGNORECASE,
)
_ONLY_META_RE = re.compile(
    r"^只(?:讨论|分析|做|写|评审|检查|优化|转述)",
    re.IGNORECASE,
)


def _is_non_execution_brief(brief: str) -> bool:
    if _HARD_CANCEL_RE.match(brief) or _ONLY_META_RE.match(brief):
        return True
    if _AMBIGUOUS_CANCEL_RE.match(brief) and _NON_EXECUTION_CUE_RE.search(brief):
        return True
    return bool(_META_REQUEST_RE.match(brief))


def classify_photo_intent(text: str) -> PhotoIntent:
    normalized = str(text or "").strip()
    if not normalized:
        return PhotoIntent(PhotoIntentKind.PASS_THROUGH)

    match = _COMMAND_RE.match(normalized) or _LABEL_RE.match(normalized)
    if not match:
        return PhotoIntent(PhotoIntentKind.PASS_THROUGH)

    brief = str(match.group(1) or "").strip()
    if not brief:
        return PhotoIntent(PhotoIntentKind.CLARIFY)
    if _is_non_execution_brief(brief):
        return PhotoIntent(PhotoIntentKind.PASS_THROUGH)
    return PhotoIntent(PhotoIntentKind.PHOTO, brief=brief)
