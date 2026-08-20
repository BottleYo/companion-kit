from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
import re
from typing import Iterable

from .public_bundle import contains_absolute_path


class DailyLookError(ValueError):
    """每日穿搭卡或其图片控制指令不满足最小协议。"""


DAILY_LOOK_SCHEMA_VERSION = 1
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
_LOOK_FIELDS = {
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
    source: str
    status: str
    created_at: str
    confirmed_at: str | None = None

    @classmethod
    def from_dict(cls, raw: object) -> DailyLook:
        if (
            not isinstance(raw, dict)
            or set(raw) != _LOOK_FIELDS
            or raw.get("schema_version") != DAILY_LOOK_SCHEMA_VERSION
        ):
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
            hero_piece=_safe_text(
                raw.get("hero_piece"), "穿搭核心单品", max_length=120
            ),
            accent=_safe_text(raw.get("accent"), "穿搭点睛细节", max_length=80),
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
            self.hero_piece,
            self.accent,
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
            f"今天的穿搭卡是“{self.title}”：气质偏{self.mood}；"
            f"配色是{self.palette}；轮廓是{self.silhouette}；"
            f"核心单品是{self.hero_piece}；点睛细节是{self.accent}。"
            "这是今天的服饰连续性，不代表人物在后台真实生活。自然用第一人称聊，"
            "不要展示穿搭卡、标识、修订号或生成规则。"
        )

    def render_image_constraints(self) -> str:
        return (
            "这张每日穿搭卡只固定今天的穿搭锚点，不固定发型、表情和妆容，也不替代人物主脸参考。"
            f"今日主题：{self.title}；气质：{self.mood}；配色：{self.palette}；"
            f"轮廓：{self.silhouette}；核心单品：{self.hero_piece}；点睛细节：{self.accent}。"
            "同一天可以自然增减外套、挽袖或调整配饰，但核心单品、主色关系和整体气质要连续。"
            "不要继承身份参考图里的衣服；用户在本次画面描述中明确提出的服饰要求优先。"
        )

    def render_caption_context(self) -> str:
        return (
            f"这次沿用的今日穿搭主题是“{self.title}”，可用细节包括{self.hero_piece}、"
            f"{self.accent}和{self.palette}。回复只能挑画面里确实可见的一处自然提起，"
            "不要复述穿搭清单，也不要声称看不清的细节已经出现。"
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
)

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
    digest.update(b"companion-kit-daily-look-v1\0")
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


def plan_daily_look(
    *,
    profile_id: str,
    day_key: str,
    revision: int,
    style_anchor: str,
    recent: Iterable[DailyLook],
    preferred_theme_ids: Iterable[str] = (),
    created_at: str,
) -> DailyLook:
    family = _style_family(style_anchor)
    themes = tuple(theme for theme in _THEMES if family in theme.families)
    palettes = tuple(palette for palette in _PALETTES if family in palette.families)
    if not themes:
        themes = _THEMES
    if not palettes:
        palettes = _PALETTES
    history = tuple(recent)[-30:]
    blocked_signatures = {item.signature for item in history[-14:]}
    recent_palette_ids = {item.palette_id for item in history[-2:]}
    recent_theme_ids = {item.theme_id for item in history[-3:]}
    preferred = set(preferred_theme_ids)
    candidates: list[tuple[_ThemeRecipe, _PaletteRecipe, str]] = []
    for theme in themes:
        for palette in palettes:
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
                source="auto",
                status="planned",
                created_at=created_at,
            )
            candidates.append((theme, palette, probe.signature))
    candidates.sort(
        key=lambda item: (
            0 if item[0].id in preferred else 1,
            sha256(
                f"{profile_id}\0{day_key}\0{revision}\0{item[0].id}\0{item[1].id}".encode(
                    "utf-8"
                )
            ).hexdigest(),
        )
    )

    selected: tuple[_ThemeRecipe, _PaletteRecipe, str] | None = None
    filters = (
        lambda item: item[2] not in blocked_signatures
        and item[0].id not in recent_theme_ids
        and item[1].id not in recent_palette_ids,
        lambda item: item[2] not in blocked_signatures
        and item[0].id not in recent_theme_ids,
        lambda item: item[2] not in blocked_signatures,
        lambda item: True,
    )
    for predicate in filters:
        selected = next((item for item in candidates if predicate(item)), None)
        if selected is not None:
            break
    assert selected is not None
    theme, palette, _ = selected
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
        source="mixed",
        status="planned",
        created_at=created_at,
    )
