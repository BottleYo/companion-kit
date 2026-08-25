from __future__ import annotations

from dataclasses import dataclass, replace
import json
import re
from typing import Iterable

from .daily_look import DailyLookDirective, DailyLookError


class PhotoMomentError(ValueError):
    """照片配方或工具控制信封不满足最小协议。"""


PHOTO_MOMENT_SCHEMA_VERSION = 3
PHOTO_ENVELOPE_SCHEMA_VERSION = 4
_ENVELOPES = {
    1: (
        "[[COMPANION_KIT_PHOTO_V1]]",
        "[[/COMPANION_KIT_PHOTO_V1]]",
    ),
    2: (
        "[[COMPANION_KIT_PHOTO_V2]]",
        "[[/COMPANION_KIT_PHOTO_V2]]",
    ),
    3: (
        "[[COMPANION_KIT_PHOTO_V3]]",
        "[[/COMPANION_KIT_PHOTO_V3]]",
    ),
    4: (
        "[[COMPANION_KIT_PHOTO_V4]]",
        "[[/COMPANION_KIT_PHOTO_V4]]",
    ),
}
_MOMENT_FIELDS_V1 = {
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
_MOMENT_FIELDS_V2 = _MOMENT_FIELDS_V1 | {"makeup"}
_MOMENT_FIELDS = _MOMENT_FIELDS_V2 | {"portrait_dynamics"}
_ENVELOPE_FIELDS = {"schema_version", "turn_token", "photo_moment"}
_ENVELOPE_FIELDS_WITH_DAILY_LOOK = _ENVELOPE_FIELDS | {"daily_look"}
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
        "calm_serious",
        "sleepy_relaxed",
        "amused",
        "fond",
        "self_assured",
        "guarded_soft",
        "custom",
    },
    "makeup": {
        "bare",
        "minimal",
        "natural",
        "soft_matte",
        "warm_tone",
        "cool_tone",
        "defined_eyes",
        "evening",
        "custom",
        "unspecified",
    },
    "portrait_dynamics": {
        "direct_soft",
        "three_quarter_soft",
        "downward_private_smile",
        "direct_open_smile",
        "caught_mid_laugh",
        "side_glance_half_smile",
        "curious_tilt",
        "direct_neutral",
        "quiet_off_camera",
        "downward_thoughtful",
        "calm_three_quarter",
        "sleepy_tilt",
        "amused_side_eye",
        "soft_challenge",
        "brief_lookaway",
        "confident_chin_lift",
        "warm_eye_smile",
        "mid_sentence_glance",
        "custom",
        "unspecified",
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
        "persona_coax",
        "confident_tease",
        "restrained_affection",
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
    "expression": (
        "soft_smile",
        "open_smile",
        "quiet_direct",
        "playful",
        "thoughtful",
        "calm_serious",
        "sleepy_relaxed",
        "amused",
        "fond",
        "self_assured",
        "guarded_soft",
    ),
    "makeup": (
        "bare",
        "minimal",
        "natural",
        "soft_matte",
        "warm_tone",
        "cool_tone",
        "defined_eyes",
        "evening",
    ),
    "portrait_dynamics": (
        "direct_soft",
        "three_quarter_soft",
        "downward_private_smile",
        "direct_open_smile",
        "caught_mid_laugh",
        "side_glance_half_smile",
        "curious_tilt",
        "direct_neutral",
        "quiet_off_camera",
        "downward_thoughtful",
        "calm_three_quarter",
        "sleepy_tilt",
        "amused_side_eye",
        "soft_challenge",
        "brief_lookaway",
        "confident_chin_lift",
        "warm_eye_smile",
        "mid_sentence_glance",
    ),
}
_PORTRAIT_DYNAMICS_BY_EXPRESSION = {
    "soft_smile": (
        "direct_soft",
        "three_quarter_soft",
        "downward_private_smile",
    ),
    "open_smile": ("direct_open_smile", "caught_mid_laugh"),
    "quiet_direct": ("direct_neutral", "calm_three_quarter"),
    "playful": (
        "side_glance_half_smile",
        "curious_tilt",
        "caught_mid_laugh",
    ),
    "thoughtful": (
        "quiet_off_camera",
        "downward_thoughtful",
        "calm_three_quarter",
    ),
    "calm_serious": (
        "direct_neutral",
        "calm_three_quarter",
        "quiet_off_camera",
    ),
    "sleepy_relaxed": (
        "sleepy_tilt",
        "downward_private_smile",
        "quiet_off_camera",
    ),
    "amused": (
        "amused_side_eye",
        "mid_sentence_glance",
        "side_glance_half_smile",
    ),
    "fond": (
        "warm_eye_smile",
        "brief_lookaway",
        "downward_private_smile",
    ),
    "self_assured": (
        "confident_chin_lift",
        "soft_challenge",
        "calm_three_quarter",
    ),
    "guarded_soft": (
        "brief_lookaway",
        "quiet_off_camera",
        "three_quarter_soft",
    ),
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
    makeup: str = "unspecified"
    portrait_dynamics: str = "unspecified"

    @classmethod
    def from_dict(cls, raw: object) -> PhotoMoment:
        if not isinstance(raw, dict):
            raise PhotoMomentError("PhotoMoment 字段无效")
        fields = set(raw)
        if fields == _MOMENT_FIELDS_V1:
            normalized = {
                **raw,
                "makeup": "unspecified",
                "portrait_dynamics": "unspecified",
            }
        elif fields == _MOMENT_FIELDS_V2:
            normalized = {**raw, "portrait_dynamics": "unspecified"}
        elif fields == _MOMENT_FIELDS:
            normalized = dict(raw)
        else:
            raise PhotoMomentError("PhotoMoment 字段无效")
        values: dict[str, object] = {}
        for field in _MOMENT_FIELDS - {"identity_version"}:
            value = normalized.get(field)
            if not isinstance(value, str) or value not in _VALUES[field]:
                raise PhotoMomentError(f"PhotoMoment {field} 无效")
            values[field] = value
        identity_version = normalized.get("identity_version")
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
            "makeup": self.makeup,
            "portrait_dynamics": self.portrait_dynamics,
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
            "人物主脸参考只固定脸部身份与稳定面部几何，不继承参考图或上一张成图的发型、表情、头部角度、视线、嘴角弧度、妆容、服饰、姿势和背景。",
            "本次应像一张真实生活里刚拍下的照片，不是换背景的人像模板；保留自然皮肤、轻微不完美和合理环境细节。",
        ]
        if self.framing in {
            "half",
            "three_quarter",
            "full",
            "mirror",
            "over_shoulder",
            "custom",
        }:
            lines.extend(
                (
                    "身体可见时采用偏高挑、修长但解剖自然的成年人物比例：头身比 1:7 到 1:8，肩宽约为头宽的 1.6 到 2 倍，颈部、躯干、髋部和腿部衔接完整；肩颈舒展、重心自然。若用户、Persona 或已确认体型参考有明确特征，以其为准。",
                    "使用正常人像拍摄距离和透视，避免 0.5x 超广角、近距离俯拍或让头部异常靠近镜头；严禁大头娃娃、窄肩短颈、压缩躯干和短腿。高挑感来自自然成人骨架、站姿、穿搭和构图，不是缩小头部或暴力拉长四肢。脸部身份锁只锁五官和脸型，不能接管身体几何。",
                )
            )
        else:
            lines.append(
                "使用自然成年人人像透视；头部与可见肩颈比例协调，不因脸部身份参考生成大头、窄肩或短颈。"
            )
        for label, field, mapping in (
            ("场景", "scene", _SCENE_LABELS),
            ("动作", "activity", _ACTIVITY_LABELS),
            ("镜头", "framing", _FRAMING_LABELS),
            ("发型", "hairstyle", _HAIRSTYLE_LABELS),
            ("时间与光线", "time_band", _TIME_LABELS),
        ):
            value = getattr(self, field)
            if value != "custom":
                lines.append(f"{label}：{mapping[value]}。")
        if self.expression != "custom":
            lines.append(
                f"本次最终神态：{_EXPRESSION_LABELS[self.expression]}。"
                "具体头部角度、视线和嘴部状态以下面的本次面部动态为准。"
            )
        if self.portrait_dynamics not in {"custom", "unspecified"}:
            lines.append(
                f"本次面部动态：{_PORTRAIT_DYNAMICS_LABELS[self.portrait_dynamics]}。"
                "眼神、眉部、脸颊和嘴部肌肉要共同响应同一种当下情绪，保留左右轻微不对称和自然瞬间感。"
            )
        if self.makeup not in {"custom", "unspecified"}:
            lines.append(
                f"本次最终妆容：{_MAKEUP_LABELS[self.makeup]}。"
                "妆容随当下场景自然成立，不改变脸型、五官比例或人物辨识特征，也不套用统一瘦脸、大眼或网红脸。"
            )
        lines.append(
            "主脸参考里可见的表情、头部姿态、视线、嘴角和妆容都只是拍摄当时状态；上面的本次神态、面部动态与妆容才是当前照片的最终决定。"
        )
        lines.append(
            "除非用户明确要求保持，当前照片不得照抄主脸参考或上一张照片的头部角度、视线和嘴角弧度；不要每张都正头直视、同一种标准微笑。"
        )
        lines.append(
            "用户在原始图片描述里明确点名的细节始终优先；custom 表示直接遵照原始描述。不要添加文字、水印、拼贴或分镜说明。"
        )
        lines.append(f"关系表达上限：{intimacy}；即使原始描述更进一步也不得越过。")
        return "\n".join(lines)

    def render_caption_context(self) -> str:
        scene = _SCENE_LABELS.get(self.scene, "用户指定的场景")
        activity = _ACTIVITY_LABELS.get(self.activity, "用户指定的动作")
        expression = _EXPRESSION_LABELS.get(self.expression, "用户指定的神情")
        portrait_dynamics = _PORTRAIT_DYNAMICS_LABELS.get(
            self.portrait_dynamics,
            "用户指定或自然发生的面部动态",
        )
        makeup = _MAKEUP_LABELS.get(self.makeup, "用户指定或自然延续的妆容")
        caption = _CAPTION_LABELS.get(self.caption_act, "顺着当前对话自然接下去")
        intimacy = _INTIMACY_LABELS[self.intimacy_band]
        return (
            "只有图片工具真实成功返回后，才写人物会自然说出口的一两句话；没有真实结果就不说已经拍好。不要讲生成过程、模型、Hook、Provider、耗时或参数。"
            f"这次的共同照片时刻是：{scene}，{activity}，{expression}；面部动态是{portrait_dynamics}；妆容方向是{makeup}；表达动作是“{caption}”；关系表达上限是“{intimacy}”。"
            "让文字回应用户刚才的语境，并与实际可见画面呼应；若某个计划细节在成图中并不清楚，就不要硬说它已经出现。"
            "人物要有一点自己的态度或小心思，并留下一个让对话容易继续的口子；避免机械地问“喜欢吗”“还想看吗”，也不要复述规格清单。"
            "先把表达动作翻译成这个 Persona 自己会说的话，再受关系上限约束；亲近不等于换一种通用甜妹口吻，撒娇也不等于幼态化。"
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
    "soft_smile": "温柔放松，笑意克制而真实，不是每张相同的标准微笑",
    "open_smile": "明显开心，面部肌肉随情绪自然参与，但不过度咧嘴或表演",
    "quiet_direct": "安静清醒，情绪克制，不靠固定微笑讨好镜头",
    "playful": "有一点灵动和逗弄感，但不做夸张卖萌或模板化表演",
    "thoughtful": "像刚想到一件事，注意力短暂落在镜头之外，面部保持松弛",
    "calm_serious": "清醒坚定，情绪稳定，不笑但也不僵硬或凶狠",
    "sleepy_relaxed": "眼睑和面部肌肉自然放松，带一点刚醒或夜深的慵懒",
    "amused": "像刚听见一句有意思的话，笑意先从眼睛出现，嘴角不完全对称",
    "fond": "带着熟悉和在意，眼神温暖但不过度表演亲密",
    "self_assured": "自信从容，目光和下巴角度有主见，不靠僵硬冷脸制造气场",
    "guarded_soft": "表面仍有分寸，但眉眼和嘴角泄露一点柔软，不是标准微笑",
}
_PORTRAIT_DYNAMICS_LABELS = {
    "direct_soft": "头部基本平正但保留轻微自然不对称，视线柔和地落在镜头上；嘴唇自然闭合，嘴角只有很浅的弧度，眼角同步带笑",
    "three_quarter_soft": "头部向一侧转约 25–35°形成自然四分之三侧面，眼睛回看镜头；闭唇笑意轻，左右嘴角弧度略有差别",
    "downward_private_smile": "下巴自然低约 5–10°，视线短暂向下或从下方抬回；嘴角像想起一件小事般轻轻上扬，不露齿",
    "direct_open_smile": "头部不完全摆正，目光直接而明亮；嘴唇自然张开并露出少量牙齿，脸颊和眼角随笑意真实抬起",
    "caught_mid_laugh": "头部轻微转动或后仰，像刚好被抓到笑起来的瞬间；眼睛自然眯起，嘴部张开但不夸张咧开",
    "side_glance_half_smile": "头部向一侧自然转动约 15–20°，视线从侧面回到镜头附近；一侧眉峰轻抬，嘴角形成不对称的半笑",
    "curious_tilt": "头部横向轻歪约 5–10°，视线带一点好奇地看向镜头；嘴唇微启，一侧眉毛自然稍高",
    "direct_neutral": "头部保留几度自然偏转而非证件照式摆正，视线清楚直达镜头；嘴唇放松闭合，嘴角不带笑",
    "quiet_off_camera": "头部轻转，视线落在镜头旁边或更远处；眉间和嘴部完全放松，像短暂停顿而不是刻意摆拍",
    "downward_thoughtful": "下巴略低，眼睛看向手边或画面下方；嘴唇自然闭合或微启，眉部只有很轻的专注感",
    "calm_three_quarter": "头部以克制的四分之三角度转开，视线平稳地回到镜头附近；嘴部中性，眉形放松而清醒",
    "sleepy_tilt": "头部轻靠或微微侧倾，眼睑自然放松，视线柔软；嘴唇松弛，嘴角只有若有若无的弧度",
    "amused_side_eye": "头部保持轻微侧转，眼睛从侧面带笑地看回来；一侧嘴角先抬起，像刚被逗到但不急着承认",
    "soft_challenge": "下巴微抬但肩颈放松，视线稳定地迎向镜头；眉峰只有一点变化，嘴角像在等对方接招",
    "brief_lookaway": "目光刚从镜头移开，头部只跟着转动几度；嘴唇轻抿或微启，像有句话暂时没说出口",
    "confident_chin_lift": "下巴自然抬高约 3–6°，头部略偏而非正对；目光清楚，嘴部放松，呈现从容而不是傲慢僵硬",
    "warm_eye_smile": "头部轻偏，视线落回镜头；眼角和脸颊先有温度，嘴角弧度很浅且左右略不对称",
    "mid_sentence_glance": "像说话说到一半忽然看向镜头，嘴唇自然微启，眉眼仍保留上一秒的情绪和动作惯性",
}
_MAKEUP_LABELS = {
    "bare": "接近素颜，只保留真实肤色、眉毛和唇色",
    "minimal": "很轻的日常整理，底妆薄，眉眼与唇色接近本身",
    "natural": "自然日常妆，肤质可见，眉眼和唇色有克制的提气",
    "soft_matte": "低光泽的柔雾妆面，轮廓轻，不做厚重磨皮",
    "warm_tone": "克制的暖色眼妆与唇色，整体像自然光下的生活妆",
    "cool_tone": "克制的冷调眼妆与唇色，清爽但不过度锐化",
    "defined_eyes": "眼线或睫毛比日常略清楚，其他部分保持轻薄",
    "evening": "比日常稍浓的夜间妆，眼唇有重点但仍像真人出门前完成的妆容",
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
    "persona_coax": "用这个 Persona 自己的方式轻轻讨一句回应；关系允许时可以撒娇，但不能幼态化",
    "confident_tease": "带着自信留一个小挑战，让对方有内容可接，不使用油腻套话",
    "restrained_affection": "不直说满，用一个可见细节或半句话表达在意",
}
_INTIMACY_LABELS = {
    "everyday": "普通日常分享，不使用恋人式身体距离、暧昧姿态或恋人称谓；服装仍只由 Persona、场景和用户要求决定",
    "personal": "亲近但有分寸的私人分享，可以温柔关心，但不把关系直接说成恋人",
    "romantic": "允许恋人式暧昧、身体距离和轻微撩拨，但保持非露骨；服装不由亲密度单独决定",
    "intimate_non_explicit": "允许更亲密的非露骨氛围，仍不表现露骨性行为；服装不由亲密度单独决定",
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


def _normalize_portrait_dynamics(
    moment: PhotoMoment,
    recent: tuple[PhotoMoment, ...],
) -> PhotoMoment:
    value = moment.portrait_dynamics
    if value == "custom":
        return moment
    allowed = _PORTRAIT_DYNAMICS_BY_EXPRESSION.get(
        moment.expression,
        _ROTATIONS["portrait_dynamics"],
    )
    blocked = {
        item.portrait_dynamics
        for item in recent[-4:]
        if item.portrait_dynamics != "unspecified"
    }
    if value in allowed and value not in blocked:
        return moment
    try:
        start = allowed.index(value)
    except ValueError:
        start = -1
    for offset in range(1, len(allowed) + 1):
        candidate = allowed[(start + offset) % len(allowed)]
        if candidate not in blocked:
            return replace(moment, portrait_dynamics=candidate)
    return replace(
        moment,
        portrait_dynamics=allowed[(start + 1) % len(allowed)],
    )


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
    if result.mode != "new":
        return result
    if not history:
        return _normalize_portrait_dynamics(result, history)
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
    result = _normalize_portrait_dynamics(result, history)
    if (
        result.makeup not in {"custom", "unspecified"}
        and len(history) >= 2
        and all(item.makeup == result.makeup for item in history[-2:])
        and (
            result.scene != previous.scene
            or result.time_band != previous.time_band
            or (
                result.activity == "getting_ready"
                and previous.activity != "getting_ready"
            )
        )
    ):
        result = replace(
            result,
            makeup=_next_value(
                "makeup",
                result.makeup,
                (
                    item.makeup
                    for item in history[-4:]
                    if item.makeup != "unspecified"
                ),
            ),
        )
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


def encode_photo_envelope(
    turn_token: str,
    photo_moment: PhotoMoment,
    *,
    daily_look: DailyLookDirective | None = None,
) -> str:
    if not _TOKEN_RE.fullmatch(str(turn_token or "")):
        raise PhotoMomentError("照片回合标记无效")
    version = PHOTO_ENVELOPE_SCHEMA_VERSION
    payload_object: dict[str, object] = {
        "schema_version": version,
        "turn_token": turn_token,
        "photo_moment": photo_moment.to_dict(),
    }
    if daily_look is not None:
        payload_object["daily_look"] = daily_look.to_dict()
    payload = json.dumps(
        payload_object,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    start_marker, end_marker = _ENVELOPES[version]
    return f"\n\n{start_marker}\n{payload}\n{end_marker}"


def parse_photo_envelope(
    prompt: object,
    *,
    expected_token: str,
) -> tuple[str, PhotoMoment]:
    cleaned, photo_moment, _ = parse_photo_envelope_details(
        prompt,
        expected_token=expected_token,
    )
    return cleaned, photo_moment


def parse_photo_envelope_details(
    prompt: object,
    *,
    expected_token: str,
) -> tuple[str, PhotoMoment, DailyLookDirective | None]:
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20_000:
        raise PhotoMomentError("图片提示无效")
    if not _TOKEN_RE.fullmatch(str(expected_token or "")):
        raise PhotoMomentError("预期照片回合标记无效")
    matches: list[tuple[int, str, str, int, int]] = []
    for version, (start_marker, end_marker) in _ENVELOPES.items():
        start = prompt.rfind(start_marker)
        end = prompt.rfind(end_marker)
        if start < 0 and end < 0:
            continue
        if start < 0 or end < start:
            raise PhotoMomentError("缺少有效 Companion 照片控制信封")
        matches.append((version, start_marker, end_marker, start, end))
    if len(matches) != 1:
        raise PhotoMomentError("缺少有效 Companion 照片控制信封")
    version, start_marker, end_marker, start, end = matches[0]
    if (
        prompt[end + len(end_marker) :].strip()
        or prompt[:start].find(start_marker) >= 0
        or prompt[:start].find(end_marker) >= 0
    ):
        raise PhotoMomentError("缺少有效 Companion 照片控制信封")
    raw_json = prompt[start + len(start_marker) : end].strip()
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise PhotoMomentError("Companion 照片控制信封无效") from exc
    if version == 3:
        expected_envelope_fields = (_ENVELOPE_FIELDS_WITH_DAILY_LOOK,)
    elif version == PHOTO_ENVELOPE_SCHEMA_VERSION:
        expected_envelope_fields = (
            _ENVELOPE_FIELDS,
            _ENVELOPE_FIELDS_WITH_DAILY_LOOK,
        )
    else:
        expected_envelope_fields = (_ENVELOPE_FIELDS,)
    if (
        not isinstance(payload, dict)
        or set(payload) not in expected_envelope_fields
        or payload.get("schema_version") != version
        or payload.get("turn_token") != expected_token
    ):
        raise PhotoMomentError("Companion 照片控制信封与当前回合不匹配")
    moment_raw = payload.get("photo_moment")
    expected_fields = (
        _MOMENT_FIELDS_V1
        if version == 1
        else _MOMENT_FIELDS_V2
        if version in {2, 3}
        else _MOMENT_FIELDS
    )
    if not isinstance(moment_raw, dict) or set(moment_raw) != expected_fields:
        raise PhotoMomentError("Companion 照片控制信封与当前回合不匹配")
    cleaned = prompt[:start].rstrip()
    if not cleaned:
        raise PhotoMomentError("图片提示缺少真实画面描述")
    daily_look: DailyLookDirective | None = None
    if "daily_look" in payload:
        try:
            daily_look = DailyLookDirective.from_dict(payload.get("daily_look"))
        except DailyLookError as exc:
            raise PhotoMomentError("Companion 每日穿搭控制无效") from exc
    return cleaned, PhotoMoment.from_dict(moment_raw), daily_look
