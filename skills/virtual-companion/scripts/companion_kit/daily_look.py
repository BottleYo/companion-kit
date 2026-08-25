from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
import re
from typing import Iterable

from .public_bundle import contains_absolute_path
from .styling import ResolvedStyleProfile


class DailyLookError(ValueError):
    """每日穿搭卡或其图片控制指令不满足最小协议。"""


DAILY_LOOK_SCHEMA_VERSION = 2
DAILY_LOOK_ACTIONS = {
    "use_daily",
    "replace_daily",
    "one_shot",
    "preserve_target",
}
DAILY_LOOK_STATUSES = {"planned", "confirmed"}
DAILY_LOOK_SOURCES = {"auto", "user", "mixed"}
_LOOK_ID_RE = re.compile(r"^look_[0-9a-f]{24}$")
_DAY_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
_LOOK_FIELDS_V1 = {
    "schema_version",
    "look_id",
    "day_key",
    "revision",
    "theme_id",
    "palette_id",
    "title",
    "mood",
    "palette",
    "silhouette",
    "hero_piece",
    "accent",
    "source",
    "status",
    "created_at",
    "confirmed_at",
}
_LOOK_FIELDS = _LOOK_FIELDS_V1 | {
    "outfit",
    "accessories",
    "hairstyle",
    "makeup",
    "performance",
    "tip",
    "persona_style",
    "avoid_rule",
}
_PROPOSAL_FIELDS = {"title", "palette", "silhouette", "hero_piece", "accent"}
_DIRECTIVE_FIELDS = {"action", "look_id", "proposal"}


def _safe_text(value: object, label: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise DailyLookError(f"{label} 必须是文字")
    normalized = " ".join(value.split()).strip()
    if (
        not normalized
        or len(normalized) > max_length
        or any(ord(character) < 32 for character in normalized)
        or "COMPANION_KIT" in normalized
        or "://" in normalized
        or contains_absolute_path(normalized)
    ):
        raise DailyLookError(f"{label} 无效")
    return normalized


def _token(value: object, label: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise DailyLookError(f"{label} 无效")
    return value


def _timestamp(value: object, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise DailyLookError(f"{label} 无效")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DailyLookError(f"{label} 无效") from exc
    if parsed.tzinfo is None:
        raise DailyLookError(f"{label} 缺少时区")
    return parsed.isoformat()


def companion_day_key(now: datetime) -> str:
    """把凌晨四点以前的连续聊天归入前一个 Persona 日。"""

    if not isinstance(now, datetime) or now.tzinfo is None:
        raise DailyLookError("每日穿搭时间必须包含时区")
    return (now - timedelta(hours=4)).date().isoformat()


@dataclass(frozen=True)
class DailyLookProposal:
    title: str
    palette: str
    silhouette: str
    hero_piece: str
    accent: str

    @classmethod
    def from_dict(cls, raw: object) -> DailyLookProposal:
        if not isinstance(raw, dict) or set(raw) != _PROPOSAL_FIELDS:
            raise DailyLookError("每日穿搭提案字段无效")
        return cls(
            title=_safe_text(raw.get("title"), "穿搭主题", max_length=40),
            palette=_safe_text(raw.get("palette"), "穿搭配色", max_length=80),
            silhouette=_safe_text(
                raw.get("silhouette"), "穿搭轮廓", max_length=80
            ),
            hero_piece=_safe_text(
                raw.get("hero_piece"), "穿搭核心单品", max_length=120
            ),
            accent=_safe_text(raw.get("accent"), "穿搭点睛细节", max_length=80),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "palette": self.palette,
            "silhouette": self.silhouette,
            "hero_piece": self.hero_piece,
            "accent": self.accent,
        }


@dataclass(frozen=True)
class DailyLookDirective:
    action: str
    look_id: str | None
    proposal: DailyLookProposal | None

    @classmethod
    def from_dict(cls, raw: object) -> DailyLookDirective:
        if not isinstance(raw, dict) or set(raw) != _DIRECTIVE_FIELDS:
            raise DailyLookError("每日穿搭控制字段无效")
        action = raw.get("action")
        if not isinstance(action, str) or action not in DAILY_LOOK_ACTIONS:
            raise DailyLookError("每日穿搭控制动作无效")
        look_id = raw.get("look_id")
        proposal_raw = raw.get("proposal")
        proposal = (
            DailyLookProposal.from_dict(proposal_raw)
            if proposal_raw is not None
            else None
        )
        if action in {"use_daily", "replace_daily"}:
            if not isinstance(look_id, str) or not _LOOK_ID_RE.fullmatch(look_id):
                raise DailyLookError("每日穿搭控制缺少当前主题")
        elif look_id is not None:
            raise DailyLookError("临时穿搭或编辑目标不能绑定每日主题")
        if action == "replace_daily":
            if proposal is None:
                raise DailyLookError("更换每日穿搭需要完整提案")
        elif proposal is not None:
            raise DailyLookError("当前穿搭动作不能携带新提案")
        return cls(action=action, look_id=look_id, proposal=proposal)

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "look_id": self.look_id,
            "proposal": self.proposal.to_dict() if self.proposal is not None else None,
        }


@dataclass(frozen=True)
class DailyLook:
    look_id: str
    day_key: str
    revision: int
    theme_id: str
    palette_id: str
    title: str
    mood: str
    palette: str
    silhouette: str
    hero_piece: str
    accent: str
    outfit: str
    accessories: str
    hairstyle: str
    makeup: str
    performance: str
    tip: str
    persona_style: str
    avoid_rule: str
    source: str
    status: str
    created_at: str
    confirmed_at: str | None = None

    @classmethod
    def from_dict(cls, raw: object) -> DailyLook:
        if not isinstance(raw, dict):
            raise DailyLookError("每日穿搭卡字段或版本无效")
        schema_version = raw.get("schema_version")
        if schema_version == 1:
            if set(raw) != _LOOK_FIELDS_V1:
                raise DailyLookError("每日穿搭卡字段或版本无效")
        elif schema_version == DAILY_LOOK_SCHEMA_VERSION:
            if set(raw) != _LOOK_FIELDS:
                raise DailyLookError("每日穿搭卡字段或版本无效")
        else:
            raise DailyLookError("每日穿搭卡字段或版本无效")
        look_id = raw.get("look_id")
        day_key = raw.get("day_key")
        revision = raw.get("revision")
        source = raw.get("source")
        status = raw.get("status")
        if not isinstance(look_id, str) or not _LOOK_ID_RE.fullmatch(look_id):
            raise DailyLookError("每日穿搭卡 ID 无效")
        if not isinstance(day_key, str) or not _DAY_KEY_RE.fullmatch(day_key):
            raise DailyLookError("每日穿搭日期无效")
        try:
            datetime.fromisoformat(day_key)
        except ValueError as exc:
            raise DailyLookError("每日穿搭日期无效") from exc
        if (
            not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 1
            or revision > 10_000
        ):
            raise DailyLookError("每日穿搭修订号无效")
        if source not in DAILY_LOOK_SOURCES:
            raise DailyLookError("每日穿搭来源无效")
        if status not in DAILY_LOOK_STATUSES:
            raise DailyLookError("每日穿搭状态无效")
        created_at = _timestamp(raw.get("created_at"), "每日穿搭创建时间")
        confirmed_at = _timestamp(
            raw.get("confirmed_at"), "每日穿搭确认时间", optional=True
        )
        if status == "planned" and confirmed_at is not None:
            raise DailyLookError("未出片的每日穿搭不能有确认时间")
        if status == "confirmed" and confirmed_at is None:
            raise DailyLookError("已确认的每日穿搭缺少确认时间")
        hero_piece = _safe_text(
            raw.get("hero_piece"), "穿搭核心单品", max_length=120
        )
        accent = _safe_text(raw.get("accent"), "穿搭点睛细节", max_length=80)
        legacy = schema_version == 1
        return cls(
            look_id=look_id,
            day_key=day_key,
            revision=revision,
            theme_id=_token(raw.get("theme_id"), "穿搭主题标识"),
            palette_id=_token(raw.get("palette_id"), "穿搭配色标识"),
            title=_safe_text(raw.get("title"), "穿搭主题", max_length=40),
            mood=_safe_text(raw.get("mood"), "穿搭气质", max_length=80),
            palette=_safe_text(raw.get("palette"), "穿搭配色", max_length=80),
            silhouette=_safe_text(
                raw.get("silhouette"), "穿搭轮廓", max_length=80
            ),
            hero_piece=hero_piece,
            accent=accent,
            outfit=(
                hero_piece
                if legacy
                else _safe_text(raw.get("outfit"), "完整穿搭", max_length=220)
            ),
            accessories=(
                accent
                if legacy
                else _safe_text(raw.get("accessories"), "配饰方案", max_length=120)
            ),
            hairstyle=(
                "按当前场景自然整理，不固定身份参考图里的发型"
                if legacy
                else _safe_text(raw.get("hairstyle"), "发型方案", max_length=120)
            ),
            makeup=(
                "按当前场景自然调整，不改变脸型或五官比例"
                if legacy
                else _safe_text(raw.get("makeup"), "妆容方案", max_length=120)
            ),
            performance=(
                "保持 Persona 本来的气质，避免固定表情和标准微笑"
                if legacy
                else _safe_text(raw.get("performance"), "神态方向", max_length=140)
            ),
            tip=(
                f"今天让{accent}成为一个不经意的小重点。"
                if legacy
                else _safe_text(raw.get("tip"), "今天的小心思", max_length=120)
            ),
            persona_style=(
                "延续 Persona 原有审美"
                if legacy
                else _safe_text(raw.get("persona_style"), "Persona 造型方向", max_length=240)
            ),
            avoid_rule=(
                "不添加与 Persona 冲突的造型元素"
                if legacy
                else _safe_text(raw.get("avoid_rule"), "造型禁区", max_length=180)
            ),
            source=source,
            status=status,
            created_at=created_at or "",
            confirmed_at=confirmed_at,
        )

    @property
    def signature(self) -> str:
        digest = sha256()
        for value in (
            self.theme_id,
            self.palette_id,
            self.title,
            self.outfit,
            self.accessories,
            self.hairstyle,
            self.makeup,
        ):
            digest.update(value.encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()[:24]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": DAILY_LOOK_SCHEMA_VERSION,
            "look_id": self.look_id,
            "day_key": self.day_key,
            "revision": self.revision,
            "theme_id": self.theme_id,
            "palette_id": self.palette_id,
            "title": self.title,
            "mood": self.mood,
            "palette": self.palette,
            "silhouette": self.silhouette,
            "hero_piece": self.hero_piece,
            "accent": self.accent,
            "outfit": self.outfit,
            "accessories": self.accessories,
            "hairstyle": self.hairstyle,
            "makeup": self.makeup,
            "performance": self.performance,
            "tip": self.tip,
            "persona_style": self.persona_style,
            "avoid_rule": self.avoid_rule,
            "source": self.source,
            "status": self.status,
            "created_at": self.created_at,
            "confirmed_at": self.confirmed_at,
        }

    def to_public_dict(self) -> dict[str, object]:
        return {
            "look_id": self.look_id,
            "day_key": self.day_key,
            "revision": self.revision,
            "title": self.title,
            "mood": self.mood,
            "palette": self.palette,
            "silhouette": self.silhouette,
            "hero_piece": self.hero_piece,
            "accent": self.accent,
            "outfit": self.outfit,
            "accessories": self.accessories,
            "hairstyle": self.hairstyle,
            "makeup": self.makeup,
            "performance": self.performance,
            "tip": self.tip,
            "source": self.source,
            "status": self.status,
        }

    def with_status(
        self,
        status: str,
        *,
        confirmed_at: str | None = None,
    ) -> DailyLook:
        if status not in DAILY_LOOK_STATUSES:
            raise DailyLookError("每日穿搭状态无效")
        timestamp = (
            _timestamp(confirmed_at or self.created_at, "每日穿搭确认时间")
            if status == "confirmed"
            else None
        )
        return replace(
            self,
            status=status,
            confirmed_at=timestamp,
        )

    def render_private_context(self) -> str:
        return (
            f"今天的主题是“{self.title}”，气质偏{self.mood}。"
            f"核心单品：{self.hero_piece}；完整造型：{self.outfit}；配饰：{self.accessories}；"
            f"发型：{self.hairstyle}；妆容：{self.makeup}。"
            f"今天的小心思：{self.tip}"
            "今天的穿搭卡只用于造型连续性，不代表人物在后台真实生活。自然用第一人称聊；"
            "如果用户询问今日主题，可以说出主题和小心思，但不要展示穿搭卡、标识、修订号或生成规则。"
        )

    def render_image_constraints(self) -> str:
        return (
            "这张每日造型卡只固定今天的穿搭锚点和表演方向，不替代人物主脸参考；不固定发型、表情和妆容到人物身份。"
            f"Persona 造型方向：{self.persona_style}；造型禁区：{self.avoid_rule}。"
            f"今日主题：{self.title}；气质：{self.mood}；配色：{self.palette}；"
            f"轮廓：{self.silhouette}；核心单品：{self.hero_piece}；完整服装：{self.outfit}；配饰：{self.accessories}；"
            f"发型方向：{self.hairstyle}；妆容方向：{self.makeup}；神态种子：{self.performance}。"
            "同一天可以自然增减外套、挽袖、调整一件配饰或改变头发局部状态，但主要服装、主色关系和整体气质要连续。"
            "PhotoMoment 中本次明确选择的发型、妆容、表情、视线和头部角度是最终状态；它应在今日方向内自然变化，不要逐字复制神态种子。"
            "不要继承身份参考图里的衣服、发型、妆容和表情；用户在本次画面描述中明确提出的要求优先。"
        )

    def render_caption_context(self) -> str:
        return (
            f"这次沿用的今日主题是“{self.title}”；今天的小心思是“{self.tip}”。"
            f"计划中的可用细节包括{self.outfit}、{self.accessories}、{self.hairstyle}和{self.makeup}。"
            "图片成功后，结合 Persona 说话方式、当前关系分寸和用户上一句话，只挑画面里确实可见的一处自然提起。"
            "可以分享、克制地等待评价、轻轻逗弄、符合人设地撒娇或留一句没说完的话；"
            "撒娇不等于幼态化，必须先经过 Persona 性格翻译并受当前亲密度上限约束。"
            "不要复述穿搭清单，不要声称看不清的细节已经出现，也不要机械地问喜欢吗或还想看吗。"
        )


@dataclass(frozen=True)
class _ThemeRecipe:
    id: str
    title: str
    mood: str
    silhouette: str
    hero_piece: str
    accent: str
    families: frozenset[str]


@dataclass(frozen=True)
class _StylingRecipe:
    outfit: str
    accessories: str
    hairstyle: str
    makeup: str
    performance: str
    tip: str
    tags: frozenset[str]
    boldness: str = "balanced"


@dataclass(frozen=True)
class _PaletteRecipe:
    id: str
    label: str
    families: frozenset[str]


_THEMES = (
    _ThemeRecipe(
        "clean_structure",
        "干净结构",
        "克制、利落，但保留生活感",
        "清楚的直线与松紧对比",
        "一件有结构感的短外套或上装",
        "细金属配饰或轮廓干净的眼镜",
        frozenset({"dark", "classic", "adaptive"}),
    ),
    _ThemeRecipe(
        "quiet_layers",
        "安静层次",
        "低调、成熟，有轻微材质变化",
        "薄层叠穿，外松内简",
        "质地轻薄的针织或衬衫层",
        "小面积腕表、耳饰或领口细节",
        frozenset({"dark", "soft", "classic", "adaptive"}),
    ),
    _ThemeRecipe(
        "soft_geometry",
        "柔和几何",
        "柔和但不甜腻，线条清楚",
        "圆润上装与直线下装形成平衡",
        "一件有触感的简洁上装",
        "细框眼镜或小体积包袋",
        frozenset({"soft", "classic", "adaptive"}),
    ),
    _ThemeRecipe(
        "easy_tailoring",
        "松弛剪裁",
        "像认真搭过，但没有正式摆拍感",
        "略宽松的剪裁配自然重心",
        "一件不紧绷的剪裁单品",
        "挽袖、腕表或低存在感金属配饰",
        frozenset({"dark", "relaxed", "classic", "adaptive"}),
    ),
    _ThemeRecipe(
        "texture_focus",
        "材质焦点",
        "颜色克制，让真实面料成为重点",
        "简洁轮廓配一处明显纹理",
        "针织、棉麻或轻皮革中的一种主材质",
        "同色小配饰，不堆砌装饰",
        frozenset({"dark", "soft", "relaxed", "adaptive"}),
    ),
    _ThemeRecipe(
        "shirt_moment",
        "衬衫时刻",
        "清爽、清醒，有一点不经意的正式感",
        "上装线条清楚，下装保留活动空间",
        "一件领口与袖口有细节的衬衫",
        "眼镜、腕表或简洁耳饰任选其一",
        frozenset({"dark", "classic", "bright", "adaptive"}),
    ),
    _ThemeRecipe(
        "knit_pause",
        "针织停顿",
        "松弛、亲和，像普通生活中的一段空闲",
        "柔软上装配利落下装",
        "一件轻薄针织或简洁开衫",
        "小耳饰、发夹或一只日常杯子作为生活细节",
        frozenset({"soft", "relaxed", "adaptive"}),
    ),
    _ThemeRecipe(
        "denim_contrast",
        "丹宁对比",
        "轻松但不随便，用硬朗与柔软做对比",
        "短层次或上紧下松的自然比例",
        "一件克制使用的丹宁单品",
        "皮质腰带、简洁包袋或小面积金属",
        frozenset({"relaxed", "bright", "adaptive"}),
    ),
    _ThemeRecipe(
        "single_accent",
        "一笔亮色",
        "整体安静，只留一个有记忆点的颜色",
        "主体轮廓简洁，重点集中在一处",
        "Persona 风格里的基础款主件",
        "一件小面积亮色配饰或内搭",
        frozenset({"dark", "bright", "classic", "adaptive"}),
    ),
    _ThemeRecipe(
        "weekend_air",
        "周末空气",
        "轻快、松弛，不假装拥有固定现实日程",
        "便于走动的自然层次",
        "一件舒适但有版型的日常单品",
        "轻便包袋、帽子或运动感小配饰",
        frozenset({"soft", "relaxed", "bright", "adaptive"}),
    ),
    _ThemeRecipe(
        "retro_detail",
        "复古小细节",
        "不过度做旧，只借一个年代感细节",
        "经典比例配现代的简洁处理",
        "一件轮廓经典的上装或连身单品",
        "复古镜框、皮质腕表或小丝巾",
        frozenset({"classic", "soft", "dark", "adaptive"}),
    ),
    _ThemeRecipe(
        "tonal_motion",
        "同色流动",
        "同一色系里有深浅和材质变化",
        "纵向线条清楚，动作时不僵硬",
        "一件与下装形成同色层次的主件",
        "同色包袋或低对比配饰",
        frozenset({"dark", "soft", "relaxed", "adaptive"}),
    ),
    _ThemeRecipe(
        "quiet_glam",
        "安静的明艳",
        "有存在感但不过度用力，适合夜晚或想被认真看见的时刻",
        "收腰或修身主线配一处流动层次",
        "一件颜色克制但轮廓鲜明的主件",
        "光泽耳饰、戒指或小体积手包",
        frozenset({"dark", "classic", "bright", "adaptive"}),
    ),
    _ThemeRecipe(
        "soft_power",
        "柔软力量",
        "成熟自信，但材质和线条不显得生硬",
        "柔软贴合与清楚肩线形成平衡",
        "一件能强调腰线或肩颈的柔软主件",
        "细链、耳饰或有弧度的金属细节",
        frozenset({"dark", "soft", "classic", "adaptive"}),
    ),
    _ThemeRecipe(
        "playful_edge",
        "一点坏心思",
        "俏皮、清醒，有轻微反差但不幼态",
        "短长比例或不对称细节制造轻快感",
        "一件带反差色、斜裁或不对称设计的单品",
        "只留一件有趣的眼镜、耳饰或发饰",
        frozenset({"bright", "relaxed", "dark", "adaptive"}),
    ),
    _ThemeRecipe(
        "romantic_edge",
        "柔美带锋",
        "浪漫但不甜腻，用柔软材质配利落细节",
        "流动裙摆或柔软主件配清楚腰线",
        "一件有垂坠、斜裁或细腻纹理的主件",
        "尖细金属、窄腰带或低调光泽",
        frozenset({"soft", "classic", "bright", "adaptive"}),
    ),
    _ThemeRecipe(
        "street_polish",
        "街头精修",
        "有一点酷和行动感，但整体仍然干净成熟",
        "短外层与纵向内搭形成有速度感的比例",
        "一件皮革、丹宁或挺括棉质的街头主件",
        "小体积金属、窄框眼镜或结构感包",
        frozenset({"dark", "relaxed", "bright", "adaptive"}),
    ),
    _ThemeRecipe(
        "sleek_motion",
        "利落在路上",
        "修长、轻快，适合走动和带环境的照片",
        "上身收束、下摆或外层保留动态",
        "一套能在走动时保持清楚轮廓的完整搭配",
        "腕表、耳饰或肩背小包",
        frozenset({"dark", "classic", "relaxed", "adaptive"}),
    ),
    _ThemeRecipe(
        "after_dark",
        "入夜以后",
        "比白天更明艳和松弛，仍然保持真实出门感",
        "修身主线配一处露肤或光泽，但不堆叠性感符号",
        "一件适合夜间光线的深色或宝石色主件",
        "眼唇择一强调，再配一件有光泽的配饰",
        frozenset({"dark", "classic", "bright", "adaptive"}),
    ),
    _ThemeRecipe(
        "unexpected_color",
        "意外的一笔",
        "保持人物原有气质，只让一个颜色或材质跳出来",
        "基础轮廓稳定，视觉重点集中在一处",
        "Persona 常用轮廓中的完整主搭配",
        "一件与平时不同的颜色、鞋、包或耳饰",
        frozenset({"bright", "soft", "dark", "classic", "adaptive"}),
    ),
)


_STYLING_RECIPES: dict[str, _StylingRecipe] = {
    "clean_structure": _StylingRecipe(
        "短款结构上装叠一件简洁内搭，配高腰、纵向线条清楚的下装或裙装，鞋型保持利落",
        "细金属耳饰或轮廓干净的眼镜，只选一到两件",
        "偏分直发、低束发或把一侧别到耳后，露出清楚脸侧轮廓",
        "轻薄柔雾底妆，眉眼线条清楚，唇色保持克制",
        "清醒地看向镜头，头部微转，嘴角中性或只有很浅的弧度",
        "今天把线条收干净，让一件小配饰替整套造型说话。",
        frozenset({"短外套", "衬衫", "高腰", "裙装", "裤装", "眼镜"}),
    ),
    "quiet_layers": _StylingRecipe(
        "轻薄内搭、柔软中层和可自然脱下的外层形成三段层次，下装保持单色和简洁",
        "小耳饰配腕表，避免项链、围巾和包袋同时出现",
        "自然散发或松散半扎，发丝保留真实层次和少量碎发",
        "自然肤质、低饱和眼影和接近本身的唇色",
        "像忙到一半停下来，视线从手边慢慢回到镜头",
        "今天不靠亮色，想让你看见的是材质和靠近时才发现的小细节。",
        frozenset({"针织", "衬衫", "层叠", "腕表"}),
        "restrained",
    ),
    "soft_geometry": _StylingRecipe(
        "有弧度的柔软上装配线条清楚的裙装或下装，通过圆与直的反差保持成熟",
        "细框眼镜或小体积包袋二选一，再加一件极小耳饰",
        "柔顺散发配轻微侧分，或做低位置半扎",
        "柔光底妆、轻腮红和低饱和唇色，不做统一网红妆面",
        "神态放松但有主见，视线短暂离开镜头后再回看",
        "今天想把柔软和利落放在一起，不必每一处都很乖。",
        frozenset({"针织", "裙装", "裤装", "眼镜", "包"}),
    ),
    "easy_tailoring": _StylingRecipe(
        "一件略松弛的剪裁外层配贴合内搭，下装或裙装保持高腰和自然活动空间",
        "挽袖露出腕表，或用单只耳饰制造不经意感",
        "低马尾、低盘发或一侧别耳，保留少量自然碎发",
        "薄底妆配清楚眉形，眼妆和唇妆只强调一个",
        "身体重心放松，头部四分之三转开，再用视线回到镜头",
        "今天故意把剪裁穿得没那么正式，挽一下袖口就够了。",
        frozenset({"西装", "短外套", "高腰", "裙装", "裤装", "腕表"}),
    ),
    "texture_focus": _StylingRecipe(
        "用针织、棉麻、丝缎或轻皮革中的一种做主材质，其余单品保持单色和低对比",
        "同色耳饰、腰带或小包只保留一件，避免遮挡主材质",
        "发型保持自然纹理，可从直顺、松卷或低束中选择",
        "妆面保持真实肤质，根据主材质选择柔雾或轻微光泽",
        "像在触碰衣料或整理袖口时被拍到，表情不是标准摆拍",
        "今天颜色很安静，真正的重点要靠近一点才看得清。",
        frozenset({"针织", "丝缎", "皮革", "棉麻", "腰带"}),
    ),
    "shirt_moment": _StylingRecipe(
        "有领口和袖口细节的衬衫配高腰裙装或下装，可微微解开最上方一颗扣子并自然挽袖",
        "眼镜、腕表或简洁耳饰任选一到两件",
        "低束发、偏分直发或把两侧头发收向后方",
        "干净底妆、清楚眉眼和自然唇色，保留真实肤质",
        "眼神清醒，正在整理袖口或领口时轻轻抬眼",
        "今天的小心思藏在领口和袖口，不用把整套都说得太明白。",
        frozenset({"衬衫", "裙装", "裤装", "眼镜", "腕表"}),
    ),
    "knit_pause": _StylingRecipe(
        "一件贴肤但不紧绷的细针织或开衫，配轮廓清楚的裙装或下装，保留舒服的生活感",
        "小耳饰、发夹或一枚戒指，最多保留一个视觉重点",
        "自然散发、松散挽发或低位置半扎",
        "接近素颜的轻薄妆面，眉毛和唇色稍微提气",
        "眼睑和面部肌肉放松，像休息时才注意到镜头",
        "今天穿得软一点，但不打算把整个人也变得没脾气。",
        frozenset({"针织", "开衫", "裙装", "裤装", "发夹"}),
    ),
    "denim_contrast": _StylingRecipe(
        "只使用一件丹宁主件，和修身针织、简洁衬衫或柔软裙装形成软硬对比",
        "皮质腰带、小包或小面积金属三选一",
        "高束发、自然散发或带蓬松纹理的半扎，避免过度精修",
        "自然妆面配稍清楚的睫毛或唇色",
        "走动中回看，神态轻快但不刻意卖萌",
        "今天只借一点丹宁的硬朗，其他地方还是按自己的节奏来。",
        frozenset({"丹宁", "牛仔裤", "牛仔裙", "针织", "腰带", "包"}),
    ),
    "single_accent": _StylingRecipe(
        "Persona 常用的基础轮廓保持稳定，只在内搭、鞋、包或裙摆中放入一处明确亮色",
        "配饰颜色与亮色重点呼应，但体积保持克制",
        "发型从 Persona 常用方式中选一个与上一张不同的整理方式",
        "妆面保持自然，可让唇色或眼影轻轻呼应点色",
        "先保持克制，再通过眼神或不对称浅笑透露一点得意",
        "今天只留一笔亮色，看看你会不会先注意到它。",
        frozenset({"亮色", "包", "鞋", "裙装", "裤装"}),
    ),
    "weekend_air": _StylingRecipe(
        "舒适但有版型的日常主件配便于走动的裙装或下装，外层可以随手披着或拿在手上",
        "轻便包、帽子、墨镜或运动感小配饰只选一件",
        "松散高束、自然散发或略有风感的纹理",
        "轻薄防晒感底妆、自然眉眼和有气色的唇色",
        "像走动或晒太阳时随手回看，笑意和动作都不完全停住",
        "今天不想把每一处都整理得太规矩，留一点风吹过的样子。",
        frozenset({"休闲", "裙装", "裤装", "帽子", "墨镜", "包"}),
    ),
    "retro_detail": _StylingRecipe(
        "现代简洁主搭配加入一件复古轮廓的上装、连身装或裙装，不做整套年代复刻",
        "复古镜框、皮质腕表、小丝巾或珍珠感耳饰只选一件",
        "柔顺偏分、低盘发或带自然弧度的卷发",
        "柔雾底妆配经典但低饱和的唇色",
        "神态克制，头部微转，像旧照片里的瞬间但保留现代生活感",
        "今天只借一个旧时代的小细节，不打算把自己也变成复古道具。",
        frozenset({"复古", "连衣裙", "裙装", "镜框", "腕表", "丝巾"}),
    ),
    "tonal_motion": _StylingRecipe(
        "同一色系的上装、下装或裙装通过深浅和材质区分，保持清楚纵向线条",
        "同色包袋、鞋或金属配饰只用一件拉开层次",
        "偏分散发、低束发或一侧别耳，发丝方向跟随动作",
        "使用与主色温度一致的轻妆，但不把肤色整体染成同一色",
        "动作中回看或侧身停顿，表情保持自然流动",
        "今天不靠撞色，想让同一种颜色在不同光线里慢慢变化。",
        frozenset({"同色系", "裙装", "裤装", "包", "鞋"}),
    ),
    "quiet_glam": _StylingRecipe(
        "颜色克制但轮廓鲜明的修身主件，搭配一处流动下摆、斜裁或适度露肤，整体保持成年真实感",
        "有光泽的耳饰、戒指或小手包只选择一件主角",
        "大弧度侧分、利落低盘发或有光泽的自然长发",
        "轻薄夜间妆，眼妆或唇妆择一增强，避免厚重磨皮",
        "视线直接而自信，嘴角只留轻微不对称弧度",
        "今天可以亮一点，但真正抢眼的那部分不打算写在清单里。",
        frozenset({"修身", "裙装", "连衣裙", "光泽", "耳饰", "手包"}),
        "expressive",
    ),
    "soft_power": _StylingRecipe(
        "贴合而柔软的主件配清楚肩线或腰线，可选择修身针织、收腰连身装或柔软衬衫",
        "弧形金属耳饰、细链或窄腰带任选一件",
        "低束发、偏分大波浪或把一侧头发收起",
        "柔雾底妆配清楚眼神，唇色保持成熟低饱和",
        "表情温和但不讨好镜头，下巴和视线保持稳定",
        "今天想把锋利藏进柔软里，靠近才看得出来。",
        frozenset({"修身", "针织", "衬衫", "连衣裙", "腰带", "耳饰"}),
    ),
    "playful_edge": _StylingRecipe(
        "保留 Persona 常用主线，加入一件反差色、不对称剪裁或短长比例明显的单品",
        "有趣眼镜、单边耳饰或小发饰只选一个",
        "高束发、半扎或一侧别耳，让脸侧轮廓和上一张明显不同",
        "自然妆面中加强睫毛、眼线或一处跳色唇妆",
        "侧面回看，一侧眉峰轻抬，嘴角形成不对称半笑",
        "今天留了一点不太乖的细节，先不告诉你藏在哪里。",
        frozenset({"不对称", "亮色", "裙装", "裤装", "眼镜", "耳饰"}),
        "expressive",
    ),
    "romantic_edge": _StylingRecipe(
        "有垂坠或斜裁感的裙装、连身装或柔软主件，用清楚腰线和利落外层压住甜度",
        "窄腰带、尖细金属耳饰或低调光泽鞋款",
        "柔软长发、低盘发或松散半扎，保留少量脸侧碎发",
        "清透底妆、细腻眼妆和有气色但不甜腻的唇色",
        "短暂移开视线再回看，笑意轻而有分寸",
        "今天的柔软不是为了变乖，只是想让线条没那么锋利。",
        frozenset({"裙装", "连衣裙", "斜裁", "腰带", "耳饰", "高跟鞋"}),
    ),
    "street_polish": _StylingRecipe(
        "短款皮革、丹宁或挺括棉质外层配修身内搭，下装或裙装保持清楚比例和行动感",
        "窄框眼镜、结构感小包或小面积金属任选一件",
        "高束发、带纹理的散发或把前发向后整理",
        "轻薄哑光底妆配更清楚的眼线，唇色保持低饱和",
        "走动中回看或微抬下巴，眼神有速度感和主见",
        "今天想穿得像随时会出门，不像专门坐好等一张照片。",
        frozenset({"皮革", "丹宁", "短外套", "裙装", "裤装", "眼镜", "包"}),
        "expressive",
    ),
    "sleek_motion": _StylingRecipe(
        "上身收束、下摆或外层保留动作空间，选择纵向线条清楚且适合走动的完整搭配",
        "腕表、肩背小包或小耳饰不超过两件",
        "低束发、顺直散发或带风感的自然纹理",
        "自然妆面配清楚眉眼，避免过度精修",
        "身体正在移动，头部回转，视线刚好追上镜头",
        "今天这套要在走起来的时候才完整，站得太规矩反而没意思。",
        frozenset({"纵向线条", "裙装", "裤装", "腕表", "包"}),
    ),
    "after_dark": _StylingRecipe(
        "深色或宝石色修身主件配一处斜裁、开衩、肩颈线条或光泽材质，其余部分保持简洁",
        "高跟鞋、耳饰、戒指或小手包中选一到两件形成夜间重点",
        "侧分大波浪、利落盘发或有光泽的顺直长发",
        "比日常稍浓的夜间妆，眼唇择一强调，肤质仍然真实",
        "目光停在镜头上更久一点，神态自信，可以有轻微逗弄感",
        "入夜以后可以比白天多一点存在感，但不会把所有答案都穿在身上。",
        frozenset({"修身", "裙装", "连衣裙", "开衩", "高跟鞋", "耳饰", "手包"}),
        "expressive",
    ),
    "unexpected_color": _StylingRecipe(
        "Persona 常用轮廓保持不变，只让一件上装、裙装、鞋或包使用意外但协调的颜色",
        "其他配饰回到中性色，只留一处颜色呼应",
        "使用与最近不同的束散方式，但不改变人物稳定发色",
        "妆面保持自然，可在眼影或唇色中轻轻呼应重点色",
        "像知道这处变化会被发现，眼神里有一点藏不住的得意",
        "今天换的不是整个人，只是故意留了一处让你多看一眼。",
        frozenset({"亮色", "裙装", "裤装", "鞋", "包"}),
        "expressive",
    ),
}

_PALETTES = (
    _PaletteRecipe(
        "charcoal_ivory", "炭灰、象牙白和少量银色", frozenset({"dark", "classic", "adaptive"})
    ),
    _PaletteRecipe(
        "ink_stone", "墨蓝、石灰和克制的金属色", frozenset({"dark", "classic", "adaptive"})
    ),
    _PaletteRecipe(
        "espresso_cream", "浓咖、奶油白和深棕", frozenset({"dark", "soft", "classic", "adaptive"})
    ),
    _PaletteRecipe(
        "burgundy_charcoal", "炭灰为主，酒红只做小面积点色", frozenset({"dark", "classic", "adaptive"})
    ),
    _PaletteRecipe(
        "oat_camel", "燕麦色、浅驼和暖白", frozenset({"soft", "relaxed", "classic", "adaptive"})
    ),
    _PaletteRecipe(
        "olive_sand", "低饱和橄榄绿、沙色和米白", frozenset({"soft", "relaxed", "adaptive"})
    ),
    _PaletteRecipe(
        "slate_blue", "灰蓝、冷白和深海军蓝", frozenset({"dark", "soft", "classic", "adaptive"})
    ),
    _PaletteRecipe(
        "black_white", "黑白为主，用材质而不是花纹拉开层次", frozenset({"dark", "classic", "adaptive"})
    ),
    _PaletteRecipe(
        "denim_red", "丹宁蓝、暖白和一小笔砖红", frozenset({"relaxed", "bright", "adaptive"})
    ),
    _PaletteRecipe(
        "sage_cream", "鼠尾草绿、奶油白和浅灰", frozenset({"soft", "bright", "adaptive"})
    ),
    _PaletteRecipe(
        "cobalt_neutral", "中性色为主，钴蓝只作为一个清楚重点", frozenset({"bright", "classic", "adaptive"})
    ),
    _PaletteRecipe(
        "plum_stone", "低饱和梅子色、石色和深灰", frozenset({"dark", "soft", "adaptive"})
    ),
)


def _style_family(style_anchor: str) -> str:
    normalized = str(style_anchor or "").casefold()
    groups = (
        ("dark", ("高冷", "深色", "冷色", "利落", "御姐", "酷", "克制", "成熟")),
        ("soft", ("温柔", "治愈", "柔和", "软", "优雅", "亲和")),
        ("bright", ("阳光", "俏皮", "明亮", "活泼", "彩色", "甜酷")),
        ("relaxed", ("松弛", "随性", "休闲", "运动", "街头", "自然")),
        ("classic", ("经典", "知性", "复古", "简约", "通勤", "剪裁")),
    )
    scores = tuple(
        (sum(1 for word in words if word in normalized), family)
        for family, words in groups
    )
    score, family = max(scores)
    return family if score > 0 else "adaptive"


def _look_id(
    *,
    profile_id: str,
    day_key: str,
    revision: int,
    theme_id: str,
    palette_id: str,
    hero_piece: str,
) -> str:
    digest = sha256()
    digest.update(b"companion-kit-daily-look-v2\0")
    for value in (
        profile_id,
        day_key,
        str(revision),
        theme_id,
        palette_id,
        hero_piece,
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return f"look_{digest.hexdigest()[:24]}"


_STYLE_FAMILY_ALIASES: dict[str, tuple[str, ...]] = {
    "sharp": ("dark", "classic"),
    "classic": ("classic",),
    "soft": ("soft",),
    "relaxed": ("relaxed",),
    "playful": ("bright", "relaxed"),
    "glam": ("bright", "dark", "classic"),
    "romantic": ("soft", "classic", "bright"),
    "street": ("dark", "relaxed", "bright"),
    "sporty": ("relaxed", "bright"),
    "adaptive": ("adaptive",),
}
_BOLDNESS_RANK = {"restrained": 0, "balanced": 1, "expressive": 2}
_THEME_ARCHETYPES: dict[str, tuple[str, ...]] = {
    "clean_structure": ("sharp", "classic"),
    "quiet_layers": ("classic", "soft"),
    "soft_geometry": ("soft", "classic"),
    "easy_tailoring": ("sharp", "relaxed", "classic"),
    "texture_focus": ("relaxed", "soft"),
    "shirt_moment": ("sharp", "classic"),
    "knit_pause": ("soft", "relaxed"),
    "denim_contrast": ("street", "relaxed"),
    "single_accent": ("playful", "sharp"),
    "weekend_air": ("relaxed", "sporty"),
    "retro_detail": ("classic", "romantic"),
    "tonal_motion": ("sharp", "classic"),
    "quiet_glam": ("glam", "sharp"),
    "soft_power": ("sharp", "glam", "soft"),
    "playful_edge": ("playful", "street"),
    "romantic_edge": ("romantic", "glam"),
    "street_polish": ("street", "sharp"),
    "sleek_motion": ("sharp", "sporty"),
    "after_dark": ("glam", "sharp"),
    "unexpected_color": ("playful", "glam"),
}


def _style_family_weights(
    *,
    style_anchor: str,
    style_profile: ResolvedStyleProfile | None,
) -> dict[str, int]:
    if style_profile is None:
        return {_style_family(style_anchor): 3, "adaptive": 1}
    result: dict[str, int] = {"adaptive": 1}
    for archetype, weight in style_profile.archetype_weights:
        for family in _STYLE_FAMILY_ALIASES.get(archetype, (archetype,)):
            result[family] = result.get(family, 0) + max(0, int(weight))
    return result


def _recipe_conflicts(recipe: _StylingRecipe, avoids: Iterable[str]) -> bool:
    haystack = "；".join(
        (
            recipe.outfit,
            recipe.accessories,
            recipe.hairstyle,
            recipe.makeup,
            *sorted(recipe.tags),
        )
    ).casefold()
    return any(str(item).strip().casefold() in haystack for item in avoids)


def _recipe_style_score(
    *,
    theme: _ThemeRecipe,
    palette: _PaletteRecipe,
    recipe: _StylingRecipe,
    family_weights: dict[str, int],
    style_profile: ResolvedStyleProfile | None,
) -> int:
    score = sum(family_weights.get(family, 0) for family in theme.families)
    score += sum(family_weights.get(family, 0) for family in palette.families) // 2
    if style_profile is None:
        return score
    score += 4 * sum(
        style_profile.weight(archetype)
        for archetype in _THEME_ARCHETYPES.get(theme.id, ())
    )
    normalized_tags = "；".join(sorted(recipe.tags)).casefold()
    score += 3 * sum(
        str(item).casefold() in normalized_tags
        for item in style_profile.signature_elements
    )
    score += 2 - abs(
        _BOLDNESS_RANK[recipe.boldness]
        - _BOLDNESS_RANK[style_profile.boldness]
    )
    return score


def _style_details(
    *,
    recipe: _StylingRecipe,
    style_anchor: str,
    style_profile: ResolvedStyleProfile | None,
) -> tuple[str, str, str]:
    direction = (
        style_profile.direction
        if style_profile is not None
        else _safe_text(style_anchor, "Persona 造型方向", max_length=240)
    )
    signatures = style_profile.signature_elements if style_profile is not None else ()
    avoids = style_profile.avoid_elements if style_profile is not None else ()
    outfit = recipe.outfit
    if signatures:
        outfit += "；并按场景自然融入这些个人标志：" + "、".join(signatures)
    avoid_rule = (
        "不要出现：" + "、".join(avoids)
        if avoids
        else "不添加与 Persona 气质或本次场景冲突的造型元素"
    )
    return outfit, direction, avoid_rule


def plan_daily_look(
    *,
    profile_id: str,
    day_key: str,
    revision: int,
    style_anchor: str,
    recent: Iterable[DailyLook],
    preferred_theme_ids: Iterable[str] = (),
    created_at: str,
    style_profile: ResolvedStyleProfile | None = None,
) -> DailyLook:
    family_weights = _style_family_weights(
        style_anchor=style_anchor,
        style_profile=style_profile,
    )
    themes = _THEMES
    palettes = _PALETTES
    history = tuple(recent)[-30:]
    blocked_signatures = {item.signature for item in history[-14:]}
    recent_palette_ids = {item.palette_id for item in history[-2:]}
    recent_theme_ids = {item.theme_id for item in history[-3:]}
    preferred = set(preferred_theme_ids)
    candidates: list[tuple[_ThemeRecipe, _PaletteRecipe, _StylingRecipe, str, int]] = []
    for theme in themes:
        styling = _STYLING_RECIPES[theme.id]
        if style_profile is not None and _recipe_conflicts(
            styling, style_profile.avoid_elements
        ):
            continue
        for palette in palettes:
            outfit, persona_style, avoid_rule = _style_details(
                recipe=styling,
                style_anchor=style_anchor,
                style_profile=style_profile,
            )
            probe = DailyLook(
                look_id="look_" + "0" * 24,
                day_key=day_key,
                revision=revision,
                theme_id=theme.id,
                palette_id=palette.id,
                title=theme.title,
                mood=theme.mood,
                palette=palette.label,
                silhouette=theme.silhouette,
                hero_piece=theme.hero_piece,
                accent=theme.accent,
                outfit=outfit,
                accessories=styling.accessories,
                hairstyle=styling.hairstyle,
                makeup=styling.makeup,
                performance=styling.performance,
                tip=styling.tip,
                persona_style=persona_style,
                avoid_rule=avoid_rule,
                source="auto",
                status="planned",
                created_at=created_at,
            )
            candidates.append(
                (
                    theme,
                    palette,
                    styling,
                    probe.signature,
                    _recipe_style_score(
                        theme=theme,
                        palette=palette,
                        recipe=styling,
                        family_weights=family_weights,
                        style_profile=style_profile,
                    ),
                )
            )
    if not candidates:
        raise DailyLookError("当前造型禁区过多，暂时无法安排今日主题")
    candidates.sort(
        key=lambda item: (
            -(item[4] + (4 if item[0].id in preferred else 0)),
            sha256(
                f"{profile_id}\0{day_key}\0{revision}\0{item[0].id}\0{item[1].id}".encode(
                    "utf-8"
                )
            ).hexdigest(),
        )
    )

    selected: tuple[
        _ThemeRecipe, _PaletteRecipe, _StylingRecipe, str, int
    ] | None = None
    filters = (
        lambda item: item[3] not in blocked_signatures
        and item[0].id not in recent_theme_ids
        and item[1].id not in recent_palette_ids,
        lambda item: item[3] not in blocked_signatures
        and item[0].id not in recent_theme_ids,
        lambda item: item[3] not in blocked_signatures,
        lambda item: True,
    )
    for predicate in filters:
        selected = next((item for item in candidates if predicate(item)), None)
        if selected is not None:
            break
    assert selected is not None
    theme, palette, styling, _, _ = selected
    outfit, persona_style, avoid_rule = _style_details(
        recipe=styling,
        style_anchor=style_anchor,
        style_profile=style_profile,
    )
    return DailyLook(
        look_id=_look_id(
            profile_id=profile_id,
            day_key=day_key,
            revision=revision,
            theme_id=theme.id,
            palette_id=palette.id,
            hero_piece=theme.hero_piece,
        ),
        day_key=day_key,
        revision=revision,
        theme_id=theme.id,
        palette_id=palette.id,
        title=theme.title,
        mood=theme.mood,
        palette=palette.label,
        silhouette=theme.silhouette,
        hero_piece=theme.hero_piece,
        accent=theme.accent,
        outfit=outfit,
        accessories=styling.accessories,
        hairstyle=styling.hairstyle,
        makeup=styling.makeup,
        performance=styling.performance,
        tip=styling.tip,
        persona_style=persona_style,
        avoid_rule=avoid_rule,
        source="auto",
        status="planned",
        created_at=created_at,
    )


def custom_daily_look(
    *,
    profile_id: str,
    day_key: str,
    revision: int,
    note: str,
    base: DailyLook,
    created_at: str,
) -> DailyLook:
    requirement = _safe_text(note, "今日穿搭微调", max_length=160)
    # 微调保留底层主题族。以后点“记住这种感觉”时能偏向同类轮廓，
    # 但不会把用户今天写的整句话每天照抄。
    theme_id = base.theme_id
    normalized = requirement.casefold()
    outfit = base.outfit
    accessories = base.accessories
    hairstyle = base.hairstyle
    makeup = base.makeup
    performance = base.performance
    if any(word in normalized for word in ("发型", "头发", "马尾", "卷发", "直发", "刘海")):
        hairstyle = requirement
    elif any(word in normalized for word in ("妆", "口红", "眼影", "唇色")):
        makeup = requirement
    elif any(word in normalized for word in ("表情", "神态", "眼神", "微笑", "笑", "视线")):
        performance = requirement
    elif any(word in normalized for word in ("配饰", "耳饰", "项链", "眼镜", "包", "鞋", "帽")):
        accessories = requirement
    else:
        outfit = requirement
    return DailyLook(
        look_id=_look_id(
            profile_id=profile_id,
            day_key=day_key,
            revision=revision,
            theme_id=theme_id,
            palette_id=base.palette_id,
            hero_piece=requirement,
        ),
        day_key=day_key,
        revision=revision,
        theme_id=theme_id,
        palette_id=base.palette_id,
        title="按你说的来",
        mood="延续 Persona 的气质，优先满足这次明确调整",
        palette=base.palette,
        silhouette=base.silhouette,
        hero_piece=requirement,
        accent=base.accent,
        outfit=outfit,
        accessories=accessories,
        hairstyle=hairstyle,
        makeup=makeup,
        performance=performance,
        tip="今天的小心思就按你说的来，只改这次点名的部分。",
        persona_style=base.persona_style,
        avoid_rule=base.avoid_rule,
        source="user",
        status="planned",
        created_at=created_at,
    )


def proposal_daily_look(
    *,
    profile_id: str,
    day_key: str,
    revision: int,
    expected: DailyLook,
    proposal: DailyLookProposal,
    created_at: str,
) -> DailyLook:
    theme_id = expected.theme_id
    return DailyLook(
        look_id=_look_id(
            profile_id=profile_id,
            day_key=day_key,
            revision=revision,
            theme_id=theme_id,
            palette_id=expected.palette_id,
            hero_piece=proposal.hero_piece,
        ),
        day_key=day_key,
        revision=revision,
        theme_id=theme_id,
        palette_id=expected.palette_id,
        title=proposal.title,
        mood="延续 Persona 气质，并采用本轮明确提出的新穿搭",
        palette=proposal.palette,
        silhouette=proposal.silhouette,
        hero_piece=proposal.hero_piece,
        accent=proposal.accent,
        outfit=proposal.hero_piece,
        accessories=proposal.accent,
        hairstyle=expected.hairstyle,
        makeup=expected.makeup,
        performance=expected.performance,
        tip="今天换了一套完整造型，想看看你会先注意到哪一处。",
        persona_style=expected.persona_style,
        avoid_rule=expected.avoid_rule,
        source="mixed",
        status="planned",
        created_at=created_at,
    )
