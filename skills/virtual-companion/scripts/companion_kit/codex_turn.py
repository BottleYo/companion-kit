from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import re


class CodexTurnKind(str, Enum):
    PASS_THROUGH = "pass_through"
    PHOTO_NEW = "photo_new"
    PHOTO_EDIT_PREVIOUS = "photo_edit_previous"


@dataclass(frozen=True)
class CodexTurnIntent:
    kind: CodexTurnKind
    identity_role: str | None = None


_NON_EXECUTION_RE = re.compile(
    r"(?:不要|不用|无需|别|禁止|停止|取消)(?:真的)?(?:生图|生成图片|出图|拍照)|"
    r"(?:只|先)(?:分析|讨论|解释|检查|评审|设计|写|修复)(?:一下)?(?:照片|生图|图片|imagegen|hook)|"
    r"(?:分析|讨论|解释|检查|评审|修复|调试|实现|开发|设计|测试|处理|识别|上传|下载|压缩|转换)"
    r".{0,12}(?:照片|生图|图片|imagegen|hook)|"
    r"(?:照片|生图|图片)(?:生成)?(?:逻辑|方案|代码|测试|参数|提示词|上传|下载|处理|功能|组件|模块|接口|bug|故障)"
    r"(?:怎么|如何|为什么|有无|是否)?|"
    r"(?:帮我|给我)?(?:看看|看下|看一眼)(?:一下)?(?:这|那|上一|刚才)?张(?:照片|图片)",
    re.IGNORECASE,
)
_EDIT_OBJECT_RE = re.compile(
    r"(?:上一张|上张|刚才(?:发的|拍的)?那张|刚刚(?:发的|拍的)?那张|这张)"
    r"(?:照片|相片|自拍|图片|图)?",
    re.IGNORECASE,
)
_EDIT_ACTION_RE = re.compile(
    r"(?:只改|修改|改成|改为|改掉|调整|编辑|换成|换掉|换|替换|保留.+只|"
    r"edit|modify|change|replace)",
    re.IGNORECASE,
)
_PERSONA_EDIT_CUE_RE = re.compile(
    r"(?:自拍|人物|人像|脸|五官|表情|发型|头发|"
    r"短发|长发|卷发|直发|马尾|妆容|化妆|口红|眼镜|衣服|外套|裙子|穿搭|姿势|动作)",
    re.IGNORECASE,
)
_GENERIC_EDIT_SUBJECT_RE = re.compile(
    r"(?:产品(?:图|模特)?|商品(?:图|模特)?|电商|广告|海报|服装模特|商品模特)",
    re.IGNORECASE,
)
_PERSONA_OWNED_OBJECT_RE = re.compile(
    r"你(?:的|发的|拍的)?"
    r"(?:上一张|上张|刚才(?:发的|拍的)?那张|刚刚(?:发的|拍的)?那张|这张)",
    re.IGNORECASE,
)
_STRONG_PERSONA_SUBJECT_RE = re.compile(
    r"(?:自拍|人物(?:照片|相片|图片|图)?|人像|脸|脸部|五官|表情|发型|头发|"
    r"短发|长发|卷发|直发|马尾|发色|妆容)",
    re.IGNORECASE,
)
_EXPLICIT_PERSONA_MEDIA_RE = re.compile(
    r"(?:上一张|上张|刚才(?:发的|拍的)?那张|刚刚(?:发的|拍的)?那张|这张)"
    r".{0,10}(?:自拍|人物(?:照片|相片|图片|图)?|人像)",
    re.IGNORECASE,
)
_PROFILE_ENHANCEMENT_RE = re.compile(
    r"(?:增强|补充|提高|加一张).{0,12}(?:侧脸|回眸).{0,12}(?:稳定|参考|形象|照片)?|"
    r"(?:侧脸|回眸).{0,12}(?:稳定|参考).{0,12}(?:增强|补充|提高)?",
    re.IGNORECASE,
)
_BODY_ENHANCEMENT_RE = re.compile(
    r"(?:增强|补充|提高|加一张).{0,12}(?:全身|体型|身材).{0,12}(?:稳定|参考|形象|照片)?|"
    r"(?:全身|体型|身材).{0,12}(?:稳定|参考).{0,12}(?:增强|补充|提高)?",
    re.IGNORECASE,
)
_EXPLICIT_PERSONA_CREATION_RE = re.compile(
    r"(?:(?:帮我|给我)?(?:生成|做|画|出|来)|(?:我)?想要)"
    r".{0,10}(?:(?:一|两|三|四|五|几|多)?(?:张|组|些))?"
    r".{0,10}你(?:的)?(?:自拍|照片|相片|人物照|人像照|图|样子)|"
    r"(?:generate|create|make|draw|want|would like|send|show)"
    r".{0,18}(?:(?:a|another|some)\s+)?(?:photo|selfie|picture)"
    r".{0,12}(?:of you|of your face)|"
    r"(?:(?:generate|create|make|draw|want|would like)\s+|"
    r"(?:send|show)(?:\s+me)?\s+)your\s+(?:photo|selfie|picture)",
    re.IGNORECASE,
)
_DIRECT_PERSONA_MEDIA_RE = re.compile(
    r"(?:给我|发我|发来|来|想看|想看看|看看|让我看看)"
    r".{0,10}(?:(?:一|两|三|四|五|几|多)?(?:张|组|些))?"
    r".{0,10}你(?:的)?(?:自拍|照片|相片|人物照|人像照|图|样子)",
    re.IGNORECASE,
)
_PERSONA_ACTIVITY_PHOTO_RE = re.compile(
    r"(?:给我|想看|看看|发|来|拍).{0,10}"
    r"(?:(?:一|两|三|四|五|几|多)?张)?.{0,6}你(?:在|正在)?"
    r"(?:做饭|吃饭|喝咖啡|喝茶|散步|运动|健身|看书|工作|上班|旅行|逛街|"
    r"做瑜伽|晒太阳|做甜点|做早餐|做晚饭|睡觉|起床|化妆|试衣服|开车|坐车|"
    r"唱歌|跳舞|弹琴|打游戏).{0,8}(?:的)?(?:自拍|照片|相片|图|样子)",
    re.IGNORECASE,
)
_DIRECT_PHOTO_RES = (
    _EXPLICIT_PERSONA_CREATION_RE,
    _DIRECT_PERSONA_MEDIA_RE,
    _PERSONA_ACTIVITY_PHOTO_RE,
    re.compile(
        r"(?:给我|可以|能不能|能否|想看|来|发|拍)"
        r".{0,18}(?:拍|发|来|看)?"
        r"(?:一|两|三|四|五|几|多)?(?:张|组|些)?"
        r".{0,12}(?:自拍|照片|相片)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:自拍|照片|相片).{0,18}(?:给我|看看|看下|发来|拍来|来一张)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:再拍|重拍|重新拍|多拍|继续拍|换个场景再来|换个发型重新拍|"
        r"take (?:another|a) (?:photo|selfie)|send (?:me )?(?:a|another) (?:photo|selfie)|"
        r"show me (?:a|another|your) (?:photo|selfie|picture))",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:想看|想看看|想瞧瞧).{0,18}(?:你|你的|你现在|你今天).{0,18}(?:自拍|照片|样子)|"
        r"(?:你|你的|你现在|你今天|你的样子).{0,20}(?:拍|发|来).{0,10}(?:一|两|三|四|五|几|多)?张",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:给我|想看|看看|发|来|拍).{0,12}(?:一|两|三|四|五|几|多)?张?"
        r".{0,10}(?:你现在(?:的样子)?|你今天(?:的样子)?|你本人|"
        r"你在.{1,10}(?:的样子|的照片|的自拍|的图)|"
        r"你(?:穿|戴|刚).{1,10}(?:的样子|的照片|的自拍|的图))",
        re.IGNORECASE,
    ),
    re.compile(r"^(?:照片|拍照|人物照片|photo)\s*[:：]", re.IGNORECASE),
)
_PREVIOUS_IMAGE_KINDS = {"missing", "generic", "companion"}


def likely_codex_photo_turn(text: str) -> bool:
    """Hook 的无磁盘快速门；宁可少命中，也不劫持普通任务。"""

    normalized = str(text or "").strip()
    if not normalized or len(normalized) > 20_000:
        return False
    if _NON_EXECUTION_RE.search(normalized):
        return False
    if _PROFILE_ENHANCEMENT_RE.search(normalized) or _BODY_ENHANCEMENT_RE.search(
        normalized
    ):
        return True
    return bool(
        (_EDIT_OBJECT_RE.search(normalized) and _EDIT_ACTION_RE.search(normalized))
        or any(pattern.search(normalized) for pattern in _DIRECT_PHOTO_RES)
    )


def classify_codex_turn(
    text: str,
    *,
    previous_image_kind: str = "missing",
) -> CodexTurnIntent:
    if previous_image_kind not in _PREVIOUS_IMAGE_KINDS:
        raise ValueError("上一张图片类型无效")
    normalized = str(text or "").strip()
    if not likely_codex_photo_turn(normalized):
        return CodexTurnIntent(CodexTurnKind.PASS_THROUGH)
    if _PROFILE_ENHANCEMENT_RE.search(normalized):
        return CodexTurnIntent(CodexTurnKind.PHOTO_NEW, "profile_face")
    if _BODY_ENHANCEMENT_RE.search(normalized):
        return CodexTurnIntent(CodexTurnKind.PHOTO_NEW, "body_shape")
    is_edit = bool(
        _EDIT_OBJECT_RE.search(normalized) and _EDIT_ACTION_RE.search(normalized)
    )
    if is_edit and _GENERIC_EDIT_SUBJECT_RE.search(normalized):
        return CodexTurnIntent(CodexTurnKind.PASS_THROUGH)
    if is_edit and previous_image_kind == "generic":
        return CodexTurnIntent(CodexTurnKind.PASS_THROUGH)
    strong_persona_edit = bool(
        (
            _PERSONA_OWNED_OBJECT_RE.search(normalized)
            and _STRONG_PERSONA_SUBJECT_RE.search(normalized)
        )
        or _EXPLICIT_PERSONA_MEDIA_RE.search(normalized)
    )
    if is_edit and strong_persona_edit:
        return CodexTurnIntent(CodexTurnKind.PHOTO_EDIT_PREVIOUS)
    if (
        is_edit
        and previous_image_kind == "companion"
        and _PERSONA_EDIT_CUE_RE.search(normalized)
    ):
        return CodexTurnIntent(CodexTurnKind.PHOTO_EDIT_PREVIOUS)
    if is_edit:
        return CodexTurnIntent(CodexTurnKind.PASS_THROUGH)
    return CodexTurnIntent(CodexTurnKind.PHOTO_NEW)


def make_turn_token(
    *,
    session_id: str,
    turn_id: str,
    mode: str,
    hook_bundle_digest: str,
) -> str:
    values = tuple(str(value or "").strip() for value in (session_id, turn_id, mode))
    bundle = str(hook_bundle_digest or "").strip().lower()
    if (
        any(not value or len(value) > 256 or any(ord(char) < 32 for char in value) for value in values)
        or not re.fullmatch(r"[0-9a-f]{64}", bundle)
        or mode not in {"new", "edit_previous"}
    ):
        raise ValueError("照片回合标记输入无效")
    digest = sha256()
    digest.update(b"companion-kit-photo-turn-v1\0")
    for value in (*values, bundle):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return f"ckp_{digest.hexdigest()[:24]}"
