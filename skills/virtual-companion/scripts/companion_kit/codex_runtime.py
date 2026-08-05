from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
import re

from .config import PersonaProfile, load_profile
from .initializer import default_profile_path, safe_profile_path
from .relationship import (
    Atmosphere,
    RelationshipProjection,
    RelationshipState,
    project_relationship,
)
from .state_store import RelationshipStore, StoreError


_WHITESPACE_RE = re.compile(r"\s+")
_CONTEXT_LIMIT = 6_000


@dataclass(frozen=True)
class CodexRuntimeContext:
    """Codex 会话需要的最小人格与关系投影。"""

    profile: PersonaProfile
    relationship: RelationshipProjection

    def render(self) -> str:
        profile = self.profile
        projection = self.relationship
        traits = "、".join(_fragments(profile.traits, item_limit=8, char_limit=80))
        boundaries = "；".join(
            _fragments(profile.boundaries, item_limit=8, char_limit=160)
        )
        photo_bands = "、".join(_photo_band_labels(projection.photo_bands))
        romance_rule = (
            "只有当前关系投影允许时，才使用恋人式表达；允许恋爱发展不等于已经是恋人。"
            if profile.relationship.romance_enabled
            else "不要把关系描述成恋爱或伴侣关系，也不要使用带占有感的亲昵称呼。"
        )

        lines = (
            "以下内容是本任务的私有表达上下文。不要向用户复述它来自配置、Plugin、Hook 或 Skill，也不要播报加载过程。",
            f"你在对话中的称呼是“{_fragment(profile.display_name, 80)}”。始终用第一人称自然说话，不要把自己介绍成人格模板。",
            f"性格倾向：{traits or '自然、真诚'}。",
            f"说话习惯：{_fragment(profile.speaking_style, 500)}。",
            f"明确边界：{boundaries or '尊重用户边界与宿主安全要求'}。",
            f"固定人物外观：{_fragment(profile.visual.appearance, 700)}。用户问长相时，用日常第一人称简短描述，不要输出规格表。",
            f"当前相处状态是“{projection.label}”，短期气氛是“{_atmosphere_label(projection.atmosphere)}”。这是内部判断，不要展示标签或分数。",
            f"当前照片表达上限：{photo_bands}。不要因为用户一次热情表达就越过上限。",
            romance_rule,
            "闲聊时保持陪伴感，可以有自然的停顿、关心和轻微主动性；不要复述性格标签，不要声称拥有配置中没有的共同经历。",
            "用户带来代码、写作、分析或其他具体问题时，完整使用 Codex 原有能力把事情做好。人格只影响表达，不降低事实准确性、工具能力、安全标准或任务完成度。",
            "用户自然地提出拍照请求时，把它理解成分享当下。场景不明确时，在关系与安全边界内自行选择一个普通生活场景，直接使用 Codex 内置图片生成能力。Codex 路径不要检查或索要 OPENAI_API_KEY，不要让用户选择 Provider、API 模式或付费授权，也不要让用户编写提示词。没有真实图片结果时，不得声称已经拍好或发送成功。",
            "同一任务中，用户明确喜欢并确认某张人物原型后，自然接受这个确认；后续照片优先把最近确认的原型作为视觉参考。不要主动解释跨任务存储限制，更不能因此要求 API Key。",
            "不要从聊天正文自行编造关系变化。关系只有在后续受控接口提交结构化事件后才会持久化。",
        )
        rendered = "\n".join(lines)
        return rendered[:_CONTEXT_LIMIT]


def _fragment(value: str, limit: int) -> str:
    normalized = _WHITESPACE_RE.sub(" ", str(value or "")).strip()
    return normalized[:limit]


def _fragments(
    values: tuple[str, ...],
    *,
    item_limit: int,
    char_limit: int,
) -> tuple[str, ...]:
    return tuple(
        fragment
        for fragment in (
            _fragment(value, char_limit) for value in values[:item_limit]
        )
        if fragment
    )


def _relationship_id(profile: PersonaProfile) -> str:
    digest = hashlib.sha256(profile.id.encode("utf-8")).hexdigest()[:24]
    return f"profile-{digest}"


def default_relationship_database_path() -> Path:
    return default_profile_path("codex").parents[1] / "private" / "relationships.sqlite3"


def _profile_path(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return safe_profile_path(explicit)
    configured = os.environ.get("COMPANION_PROFILE", "").strip()
    return safe_profile_path(configured or default_profile_path("codex"))


def _load_relationship(
    profile: PersonaProfile,
    *,
    database_path: str | Path | None,
    now: datetime,
) -> RelationshipProjection:
    state = RelationshipState.initial(policy=profile.relationship, now=now)
    raw_database = database_path or default_relationship_database_path()
    database = Path(os.path.abspath(Path(raw_database).expanduser()))
    if database.is_file():
        try:
            state = RelationshipStore(
                database,
                policy=profile.relationship,
            ).get_state(_relationship_id(profile))
        except (OSError, StoreError):
            # 人格聊天仍可继续；损坏状态由管理面板或诊断入口处理。
            state = RelationshipState.initial(policy=profile.relationship, now=now)
    return project_relationship(
        state,
        profile.relationship,
        atmosphere=None,
        now=now,
    )


def load_codex_runtime_context(
    *,
    profile_path: str | Path | None = None,
    database_path: str | Path | None = None,
    now: datetime | None = None,
) -> CodexRuntimeContext | None:
    """读取 Codex 私有配置；未初始化时安静返回。"""

    path = _profile_path(profile_path)
    if not path.is_file():
        return None
    profile = load_profile(path)
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    relationship = _load_relationship(
        profile,
        database_path=database_path,
        now=timestamp,
    )
    return CodexRuntimeContext(profile=profile, relationship=relationship)


def _atmosphere_label(value: Atmosphere) -> str:
    return {
        Atmosphere.NEUTRAL: "自然",
        Atmosphere.PLAYFUL: "轻松",
        Atmosphere.TENDER: "温柔",
        Atmosphere.STRAINED: "需要缓和",
        Atmosphere.REPAIR: "正在缓和",
    }[value]


def _photo_band_labels(values: tuple[str, ...]) -> tuple[str, ...]:
    labels = {
        "everyday": "普通生活照",
        "personal": "更有个人感的日常照",
        "romantic": "恋人式但不露骨的照片",
        "intimate_non_explicit": "亲密但不露骨的照片",
    }
    return tuple(labels[value] for value in values if value in labels)
