from __future__ import annotations

from dataclasses import dataclass, replace
import json
import re
from typing import Iterable


class PhotoMomentError(ValueError):
    """照片配方或工具控制信封不满足最小协议。"""


PHOTO_MOMENT_SCHEMA_VERSION = 1
_ENVELOPE_START = "[[COMPANION_KIT_PHOTO_V1]]"
_ENVELOPE_END = "[[/COMPANION_KIT_PHOTO_V1]]"
_MOMENT_FIELDS = {
    "mode",
    "scene",
    "activity",
    "framing",
    "hairstyle",
    "expression",
    "time_band",
    "intimacy_band",
    "caption_act",
    "identity_version",
}
_ENVELOPE_FIELDS = {"schema_version", "turn_token", "photo_moment"}
_TOKEN_RE = re.compile(r"^ckp_[0-9a-f]{24}$")

_VALUES = {
    "mode": {"new", "edit_previous"},
    "scene": {
        "home",
        "window",
        "street",
        "cafe",
        "outdoors",
        "transit",
        "workspace",
        "custom",
    },
    "activity": {
        "pause",
        "walking",
        "sitting",
        "drinking",
        "reading",
        "getting_ready",
        "adjusting_accessory",
        "custom",
    },
    "framing": {
        "close",
        "half",
        "three_quarter",
        "full",
        "mirror",
        "over_shoulder",
        "custom",
    },
    "hairstyle": {
        "loose",
        "tied",
        "half_up",
        "pinned_back",
        "textured",
        "custom",
    },
    "expression": {
        "soft_smile",
        "open_smile",
        "quiet_direct",
        "playful",
        "thoughtful",
        "custom",
    },
    "time_band": {"morning", "day", "dusk", "night", "custom"},
    "intimacy_band": {
        "everyday",
        "personal",
        "romantic",
        "intimate_non_explicit",
    },
    "caption_act": {
        "share_detail",
        "soft_tease",
        "unfinished_thought",
        "invite_choice",
        "gentle_check_in",
        "custom",
    },
}
_ROTATIONS = {
    "scene": ("home", "window", "street", "cafe", "outdoors", "transit", "workspace"),
    "activity": (
        "pause",
        "walking",
        "sitting",
        "drinking",
        "reading",
        "getting_ready",
        "adjusting_accessory",
    ),
    "framing": ("close", "half", "three_quarter", "full", "mirror", "over_shoulder"),
    "hairstyle": ("loose", "tied", "half_up", "pinned_back", "textured"),
    "expression": ("soft_smile", "open_smile", "quiet_direct", "playful", "thoughtful"),
}
_INTIMACY_ORDER = (
    "everyday",
    "personal",
    "romantic",
    "intimate_non_explicit",
)


@dataclass(frozen=True)
class PhotoMoment:
    mode: str
    scene: str
    activity: str
    framing: str
    hairstyle: str
    expression: str
    time_band: str
    intimacy_band: str
    caption_act: str
    identity_version: int

    @classmethod
    def from_dict(cls, raw: object) -> PhotoMoment:
        if not isinstance(raw, dict) or set(raw) != _MOMENT_FIELDS:
            raise PhotoMomentError("PhotoMoment 字段无效")
        values: dict[str, object] = {}
        for field in _MOMENT_FIELDS - {"identity_version"}:
            value = raw.get(field)
            if not isinstance(value, str) or value not in _VALUES[field]:
                raise PhotoMomentError(f"PhotoMoment {field} 无效")
            values[field] = value
        identity_version = raw.get("identity_version")
        if (
            not isinstance(identity_version, int)
            or isinstance(identity_version, bool)
            or identity_version < 1
            or identity_version > 1_000_000
        ):
            raise PhotoMomentError("PhotoMoment identity_version 无效")
        values["identity_version"] = identity_version
        return cls(**values)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "scene": self.scene,
            "activity": self.activity,
            "framing": self.framing,
            "hairstyle": self.hairstyle,
            "expression": self.expression,
            "time_band": self.time_band,
            "intimacy_band": self.intimacy_band,
            "caption_act": self.caption_act,
            "identity_version": self.identity_version,
        }

    def render_image_constraints(self) -> str:
        intimacy = _INTIMACY_LABELS[self.intimacy_band]
        if self.mode == "edit_previous":
            return (
                "这是对上一张已验证人物照片的明确编辑。主脸参考只固定人物身份；"
                "只修改用户明确提出的部分，其他发型、表情、妆容、服装、姿势、构图和环境尽量保留目标图现状。"
                f"关系表达上限：{intimacy}；即使原始描述更进一步也不得越过。"
            )
        lines = [
            "人物主脸参考只固定脸部身份与稳定面部几何，不继承参考图或上一张成图的发型、表情、妆容、服饰、姿势和背景。",
            "本次应像一张真实生活里刚拍下的照片，不是换背景的人像模板；保留自然皮肤、轻微不完美和合理环境细节。",
        ]
        for label, field, mapping in (
            ("场景", "scene", _SCENE_LABELS),
            ("动作", "activity", _ACTIVITY_LABELS),
            ("镜头", "framing", _FRAMING_LABELS),
            ("发型", "hairstyle", _HAIRSTYLE_LABELS),
            ("表情", "expression", _EXPRESSION_LABELS),
            ("时间与光线", "time_band", _TIME_LABELS),
        ):
            value = getattr(self, field)
            if value != "custom":
                lines.append(f"{label}：{mapping[value]}。")
        lines.append("用户在原始图片描述里明确提出的细节优先；不要添加文字、水印、拼贴或分镜说明。")
        lines.append(f"关系表达上限：{intimacy}；即使原始描述更进一步也不得越过。")
        return "\n".join(lines)

    def render_caption_context(self) -> str:
        scene = _SCENE_LABELS.get(self.scene, "用户指定的场景")
        activity = _ACTIVITY_LABELS.get(self.activity, "用户指定的动作")
        expression = _EXPRESSION_LABELS.get(self.expression, "用户指定的神情")
        caption = _CAPTION_LABELS.get(self.caption_act, "顺着当前对话自然接下去")
        intimacy = _INTIMACY_LABELS[self.intimacy_band]
        return (
            "只有图片工具真实成功返回后，才写人物会自然说出口的一两句话；没有真实结果就不说已经拍好。不要讲生成过程、模型、Hook、Provider、耗时或参数。"
            f"这次的共同照片时刻是：{scene}，{activity}，{expression}；表达动作是“{caption}”；关系表达上限是“{intimacy}”。"
            "让文字回应用户刚才的语境，并与实际可见画面呼应；若某个计划细节在成图中并不清楚，就不要硬说它已经出现。"
            "人物要有一点自己的态度或小心思，并留下一个让对话容易继续的口子；避免机械地问“喜欢吗”“还想看吗”，也不要复述规格清单。"
        )


_SCENE_LABELS = {
    "home": "有真实生活痕迹的室内",
    "window": "窗边",
    "street": "街角或散步途中",
    "cafe": "轻松的咖啡店一角",
    "outdoors": "自然的户外环境",
    "transit": "出门或途中短暂停留",
    "workspace": "桌边或工作区域",
}
_ACTIVITY_LABELS = {
    "pause": "动作做到一半停下来望向镜头",
    "walking": "走动中自然回看",
    "sitting": "放松坐着，身体有自然重心",
    "drinking": "手里拿着日常饮品",
    "reading": "刚从书页或屏幕抬起视线",
    "getting_ready": "出门前随手整理自己",
    "adjusting_accessory": "正在整理眼镜、袖口或一件小配饰",
}
_FRAMING_LABELS = {
    "close": "不端正摆拍的自然近景",
    "half": "有手机随手感的半身构图",
    "three_quarter": "带环境留白的三分之二身构图",
    "full": "能看见动作和穿搭关系的全身构图",
    "mirror": "自然镜面自拍，但不要遮住关键脸部特征",
    "over_shoulder": "转身或回眸的越肩角度",
}
_HAIRSTYLE_LABELS = {
    "loose": "按人物适合的发长自然放下，并改变分缝和脸侧轮廓",
    "tied": "按当前发长松散束起，保留自然碎发",
    "half_up": "按当前发长做轻松的半扎或局部收拢",
    "pinned_back": "把一侧头发别到耳后或向后整理，露出不同脸侧轮廓",
    "textured": "做出与上一张不同的自然蓬松和纹理，不改变人物身份",
}
_EXPRESSION_LABELS = {
    "soft_smile": "眼角有一点笑意，嘴角克制",
    "open_smile": "放松地笑开，不沿用固定模板微笑",
    "quiet_direct": "安静直视镜头，像在等对方接话",
    "playful": "俏皮地轻挑眉或把笑意藏住一点",
    "thoughtful": "若有所思，视线短暂离开镜头",
}
_TIME_LABELS = {
    "morning": "清晨自然光",
    "day": "白天自然光，明暗有层次",
    "dusk": "傍晚环境光与室内暖光自然混合",
    "night": "普通夜间室内或街灯光线，保持真实手机照片质感",
}
_CAPTION_LABELS = {
    "share_detail": "抓住一个可见小细节顺手分享",
    "soft_tease": "轻轻逗一下，但不油腻也不越过关系边界",
    "unfinished_thought": "把话留一半，让对方自然接住",
    "invite_choice": "给对方一个有内容的二选一，不问空泛的喜欢不喜欢",
    "gentle_check_in": "借这张照片自然关心对方此刻的状态",
}
_INTIMACY_LABELS = {
    "everyday": "普通日常分享，不使用恋人式身体距离、暧昧姿态或挑逗性服饰",
    "personal": "亲近但有分寸的私人分享，可以温柔关心，但不把关系直接说成恋人",
    "romantic": "允许恋人式暧昧和轻微撩拨，但保持非露骨、非性化",
    "intimate_non_explicit": "允许更亲密的非露骨氛围，仍不表现露骨性行为或裸露",
}


def _next_value(field: str, value: str, recent_values: Iterable[str]) -> str:
    if value == "custom":
        return value
    rotation = _ROTATIONS[field]
    try:
        start = rotation.index(value)
    except ValueError:
        start = -1
    blocked = set(recent_values)
    for offset in range(1, len(rotation) + 1):
        candidate = rotation[(start + offset) % len(rotation)]
        if candidate not in blocked:
            return candidate
    return rotation[(start + 1) % len(rotation)]


def normalize_photo_moment(
    candidate: PhotoMoment,
    *,
    recent: Iterable[PhotoMoment],
    allowed_intimacy_bands: Iterable[str],
) -> PhotoMoment:
    allowed_values = set(allowed_intimacy_bands)
    allowed = tuple(
        value for value in _INTIMACY_ORDER if value in allowed_values
    )
    if not allowed:
        allowed = ("everyday",)
    intimacy = (
        candidate.intimacy_band
        if candidate.intimacy_band in allowed
        else allowed[-1]
    )
    result = replace(candidate, intimacy_band=intimacy)
    history = tuple(item for item in recent if item.mode == "new")
    if result.mode != "new" or not history:
        return result
    previous = history[-1]
    changes: dict[str, str] = {}
    for field in ("hairstyle", "expression"):
        value = getattr(result, field)
        if value != "custom" and value == getattr(previous, field):
            changes[field] = _next_value(
                field,
                value,
                (getattr(item, field) for item in history[-4:]),
            )
    result = replace(result, **changes)
    compared = ("scene", "activity", "framing", "hairstyle", "expression")
    difference_count = sum(
        getattr(result, field) != getattr(previous, field) for field in compared
    )
    for field in ("scene", "activity", "framing"):
        if difference_count >= 2:
            break
        value = getattr(result, field)
        if value == "custom":
            continue
        changed = _next_value(
            field,
            value,
            (getattr(item, field) for item in history[-4:]),
        )
        if changed != value:
            result = replace(result, **{field: changed})
            difference_count += 1
    return result


def encode_photo_envelope(turn_token: str, photo_moment: PhotoMoment) -> str:
    if not _TOKEN_RE.fullmatch(str(turn_token or "")):
        raise PhotoMomentError("照片回合标记无效")
    payload = json.dumps(
        {
            "schema_version": PHOTO_MOMENT_SCHEMA_VERSION,
            "turn_token": turn_token,
            "photo_moment": photo_moment.to_dict(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"\n\n{_ENVELOPE_START}\n{payload}\n{_ENVELOPE_END}"


def parse_photo_envelope(
    prompt: object,
    *,
    expected_token: str,
) -> tuple[str, PhotoMoment]:
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20_000:
        raise PhotoMomentError("图片提示无效")
    if not _TOKEN_RE.fullmatch(str(expected_token or "")):
        raise PhotoMomentError("预期照片回合标记无效")
    start = prompt.rfind(_ENVELOPE_START)
    end = prompt.rfind(_ENVELOPE_END)
    if (
        start < 0
        or end < start
        or prompt[end + len(_ENVELOPE_END) :].strip()
        or prompt[:start].find(_ENVELOPE_START) >= 0
    ):
        raise PhotoMomentError("缺少有效 Companion 照片控制信封")
    raw_json = prompt[start + len(_ENVELOPE_START) : end].strip()
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise PhotoMomentError("Companion 照片控制信封无效") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != _ENVELOPE_FIELDS
        or payload.get("schema_version") != PHOTO_MOMENT_SCHEMA_VERSION
        or payload.get("turn_token") != expected_token
    ):
        raise PhotoMomentError("Companion 照片控制信封与当前回合不匹配")
    cleaned = prompt[:start].rstrip()
    if not cleaned:
        raise PhotoMomentError("图片提示缺少真实画面描述")
    return cleaned, PhotoMoment.from_dict(payload.get("photo_moment"))
