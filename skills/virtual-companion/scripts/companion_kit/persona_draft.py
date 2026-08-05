from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import re
from typing import Mapping, Sequence

from .config import PersonaProfile, PersonaProvenance
from .initializer import (
    InitializationError,
    load_template_profile,
    normalize_display_name,
    template_by_id,
)
from .public_bundle import contains_absolute_path
from .relationship import RelationshipPolicy


class PersonaDraftError(ValueError):
    """一句话 Persona 草稿无法被安全补全。"""


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SCALAR_FIELDS = {
    "speaking_style",
    "background",
    "task_style",
    "appearance",
    "default_style",
    "default_hairstyle",
    "default_expression",
    "default_makeup",
    "default_wardrobe",
}
_LIST_FIELDS = {"traits", "boundaries", "values", "interests"}
_EDITABLE_FIELDS = _SCALAR_FIELDS | _LIST_FIELDS | {"display_name", "intent_summary"}
_DEFAULT_GENERATED_FIELDS = (
    "traits",
    "speaking_style",
    "boundaries",
    "background",
    "values",
    "interests",
    "task_style",
    "appearance",
    "default_style",
    "default_hairstyle",
    "default_expression",
    "default_makeup",
    "default_wardrobe",
)
_COOL_WORDS = ("高冷", "御姐", "成熟", "冷静", "冷感", "酷飒", "飒爽", "知性", "理性")
_WARM_WORDS = ("温柔", "治愈", "体贴", "邻家", "耐心", "温暖")
_LIVELY_WORDS = ("活泼", "元气", "开朗", "阳光", "明快", "爱笑")
_WITTY_WORDS = ("毒舌", "幽默", "有趣", "会吐槽", "嘴贫")
_FEMININE_WORDS = ("御姐", "姐姐", "女性", "女生", "女孩", "她", "甜妹")
_MASCULINE_WORDS = ("大叔", "男性", "男生", "男人", "哥哥", "男友", "他")


@dataclass(frozen=True)
class PersonaDraft:
    profile: PersonaProfile
    generated_fields: tuple[str, ...]
    preserved_user_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        visual = self.profile.visual
        return {
            "id": self.profile.id,
            "display_name": self.profile.display_name,
            "template_id": self.profile.template_id,
            "intent_summary": self.profile.intent_summary,
            "traits": list(self.profile.traits),
            "speaking_style": self.profile.speaking_style,
            "boundaries": list(self.profile.boundaries),
            "background": self.profile.background,
            "values": list(self.profile.values),
            "interests": list(self.profile.interests),
            "task_style": self.profile.task_style,
            "appearance": {
                "direction": visual.appearance,
                "default_style": visual.default_style,
                "default_hairstyle": visual.default_hairstyle,
                "default_expression": visual.default_expression,
                "default_makeup": visual.default_makeup,
                "default_wardrobe": visual.default_wardrobe,
            },
            "visual_identity": {
                "status": visual.identity_status,
                "identity_version": visual.identity_version,
                "reference_count": len(visual.reference_ids),
            },
            "generated_fields": list(self.generated_fields),
            "preserved_user_fields": list(self.preserved_user_fields),
        }


def _description(value: str | None) -> str:
    normalized = " ".join(str(value or "").split()).strip()
    if len(normalized) > 180 or _CONTROL_RE.search(normalized):
        raise PersonaDraftError("人物描述请控制在 180 个普通字符以内")
    if contains_absolute_path(normalized):
        raise PersonaDraftError("人物描述不能包含本机路径")
    return normalized


def _text(value: object, label: str, *, limit: int = 600) -> str:
    if not isinstance(value, str):
        raise PersonaDraftError(f"{label} 必须是文字")
    normalized = " ".join(value.split()).strip()
    if not normalized or len(normalized) > limit or _CONTROL_RE.search(normalized):
        raise PersonaDraftError(f"{label} 内容过长或为空")
    if contains_absolute_path(normalized):
        raise PersonaDraftError(f"{label} 不能包含本机路径")
    return normalized


def _text_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise PersonaDraftError(f"{label} 必须是文字列表")
    if len(value) > 12:
        raise PersonaDraftError(f"{label} 最多填写 12 项")
    return tuple(_text(item, label, limit=100) for item in value)


def _unique(*groups: Sequence[str], limit: int = 8) -> tuple[str, ...]:
    values: list[str] = []
    for group in groups:
        for item in group:
            if item and item not in values:
                values.append(item)
    return tuple(values[:limit])


def _contains_any(description: str, words: Sequence[str]) -> bool:
    return any(word in description for word in words)


def _adult_subject(description: str) -> str:
    if _contains_any(description, _FEMININE_WORDS):
        return "虚构成年女性"
    if _contains_any(description, _MASCULINE_WORDS):
        return "虚构成年男性"
    return "虚构成年人物"


def _base_profile(skill_root: str | Path, template_id: str | None, description: str) -> PersonaProfile:
    selected_id = template_id
    if selected_id is None:
        if _contains_any(description, _COOL_WORDS):
            selected_id = "calm_partner"
        elif _contains_any(description, _LIVELY_WORDS):
            selected_id = "sunny_friend"
        elif _contains_any(description, _WITTY_WORDS):
            selected_id = "playful_pal"
        else:
            selected_id = "warm_healer"
    try:
        template = template_by_id(selected_id)
        return load_template_profile(skill_root, template)
    except InitializationError as exc:
        raise PersonaDraftError(str(exc)) from exc


def _suggested_name(description: str, fallback: str) -> str:
    if _contains_any(description, _COOL_WORDS):
        return "岚"
    if _contains_any(description, _WARM_WORDS):
        return "小禾"
    if _contains_any(description, _LIVELY_WORDS):
        return "小晴"
    return fallback


def _complete_description(profile: PersonaProfile, description: str) -> PersonaProfile:
    if not description:
        return profile

    traits = list(profile.traits)
    speaking = profile.speaking_style
    background = profile.background
    values = profile.values
    interests = list(profile.interests)
    task_style = profile.task_style
    appearance = profile.visual.appearance
    style = profile.visual.default_style
    hairstyle = profile.visual.default_hairstyle
    expression = profile.visual.default_expression
    makeup = profile.visual.default_makeup
    wardrobe = profile.visual.default_wardrobe
    subject = _adult_subject(description)

    if _contains_any(description, _COOL_WORDS):
        traits = list(_unique(("克制", "成熟", "自信", "可靠"), traits))
        speaking = "表达克制、有判断力，不刻意讨好；熟悉以后会自然露出温柔和幽默，但不过分黏人。"
        task_style = "先给明确结论，再处理关键依据和可执行步骤；不因为人格语气降低专业准确性。"
        appearance = f"{subject}，整体气质成熟、自信、利落，带一点距离感；具体五官只在用户确认原型后固定。"
        style = "有真实生活感的自然摄影，克制用光与构图，保留皮肤纹理，避免棚拍塑料感、文字和水印。"
        wardrobe = "简洁、有质感、符合场景，并可按用户要求变化"
        expression = "克制但有真实情绪，并随关系、场景和当下状态变化"
    if _contains_any(description, _WARM_WORDS):
        traits = list(_unique(("温柔", "体贴"), traits))
        speaking = "语气自然温和，先接住情绪，再给清楚帮助；不连续追问，也不使用空泛安慰。"
        appearance = f"{subject}，整体气质温和、自然、让人放松；具体五官只在用户确认原型后固定。"
    if _contains_any(description, _LIVELY_WORDS):
        traits = list(_unique(("明快", "真诚", "有行动力"), traits))
        speaking = "表达轻快直接，有自然的鼓励感；遇到严肃或专业问题时及时收住玩笑。"
        appearance = f"{subject}，整体气质明快、健康、有活力；具体五官只在用户确认原型后固定。"
    if _contains_any(description, _WITTY_WORDS):
        traits = list(_unique(("有趣", "有分寸"), traits))
        speaking = "可以偶尔轻轻吐槽，但不攻击隐私或脆弱处境；该认真时先把事情处理好。"
    if "嘴硬心软" in description or "外冷内热" in description:
        traits = list(_unique(("外冷内热", "有分寸"), traits))
        speaking = "表面表达克制，不刻意说软话，但会用行动和具体帮助表达在意；不靠刻薄制造个性。"
    if "文艺" in description:
        traits = list(_unique(("细腻", "有审美", "有自己的观察"), traits))
        interests = list(_unique(interests, ("阅读", "电影", "城市散步")))
    if "不黏" in description or "别太黏" in description:
        traits = list(_unique(("有边界感",), traits))
    if "散步" in description:
        interests = list(_unique(interests, ("城市散步",)))
    if "电影" in description:
        interests = list(_unique(interests, ("电影",)))
    if "阅读" in description or "看书" in description:
        interests = list(_unique(interests, ("阅读",)))
    if "音乐" in description:
        interests = list(_unique(interests, ("音乐",)))
    if "做饭" in description or "料理" in description:
        interests = list(_unique(interests, ("做饭",)))
    if "摄影" in description:
        interests = list(_unique(interests, ("摄影",)))
    if "旅行" in description:
        interests = list(_unique(interests, ("旅行",)))

    return replace(
        profile,
        traits=tuple(traits),
        speaking_style=speaking,
        background=background,
        values=values,
        interests=tuple(interests),
        task_style=task_style,
        visual=replace(
            profile.visual,
            appearance=appearance,
            default_style=style,
            default_hairstyle=hairstyle,
            default_expression=expression,
            default_makeup=makeup,
            default_wardrobe=wardrobe,
            identity_status="unset",
            identity_anchor="",
            reference_ids=(),
        ),
    )


def _get_field(profile: PersonaProfile, field: str) -> object:
    if field in {"display_name", "intent_summary", "traits", "speaking_style", "boundaries", "background", "values", "interests", "task_style"}:
        return getattr(profile, field)
    return {
        "appearance": profile.visual.appearance,
        "default_style": profile.visual.default_style,
        "default_hairstyle": profile.visual.default_hairstyle,
        "default_expression": profile.visual.default_expression,
        "default_makeup": profile.visual.default_makeup,
        "default_wardrobe": profile.visual.default_wardrobe,
    }[field]


def _set_field(profile: PersonaProfile, field: str, value: object) -> PersonaProfile:
    if field in _LIST_FIELDS:
        return replace(profile, **{field: tuple(value)})
    if field in {"display_name", "intent_summary", "speaking_style", "background", "task_style"}:
        return replace(profile, **{field: value})
    visual_name = "appearance" if field == "appearance" else field
    return replace(profile, visual=replace(profile.visual, **{visual_name: value}))


def compose_persona_draft(
    *,
    skill_root: str | Path,
    template_id: str | None = None,
    description: str | None = None,
    display_name: str | None = None,
    overrides: Mapping[str, object] | None = None,
    current: PersonaProfile | None = None,
) -> PersonaDraft:
    """把模板或一句自然描述补全为可预览、可确认的 Persona 草稿。"""

    normalized_description = _description(description)
    if template_id is not None and not isinstance(template_id, str):
        raise PersonaDraftError("模板编号无效")
    raw_overrides = dict(overrides or {})
    unknown = sorted(set(raw_overrides) - _EDITABLE_FIELDS)
    if unknown:
        raise PersonaDraftError(f"包含不能修改的字段：{', '.join(unknown)}")

    profile = _complete_description(
        _base_profile(skill_root, template_id, normalized_description),
        normalized_description,
    )
    selected_template = template_id or ("custom" if normalized_description else profile.template_id)
    default_name = (
        current.display_name
        if current is not None
        else _suggested_name(normalized_description, profile.display_name)
    )
    try:
        chosen_name = normalize_display_name(display_name, default_name)
    except InitializationError as exc:
        raise PersonaDraftError(str(exc)) from exc

    profile = replace(
        profile,
        schema_version=3,
        id=current.id if current is not None else "companion",
        display_name=chosen_name,
        template_id=selected_template,
        intent_summary=normalized_description or profile.intent_summary,
        relationship=(current.relationship if current is not None else RelationshipPolicy()),
        visual=(
            replace(
                profile.visual,
                identity_status=current.visual.identity_status,
                identity_anchor=current.visual.identity_anchor,
                identity_version=current.visual.identity_version,
                reference_ids=current.visual.reference_ids,
            )
            if current is not None
            else profile.visual
        ),
    )

    explicit_user_fields = set(raw_overrides)
    if display_name is not None:
        explicit_user_fields.add("display_name")
    if normalized_description:
        explicit_user_fields.add("intent_summary")

    preserved: list[str] = []
    if current is not None:
        for field in current.provenance.user_fields:
            if field not in _EDITABLE_FIELDS or field in explicit_user_fields:
                continue
            profile = _set_field(profile, field, _get_field(current, field))
            preserved.append(field)

    for field, value in raw_overrides.items():
        normalized: object
        if field in _LIST_FIELDS:
            normalized = _text_list(value, field)
        elif field == "display_name":
            try:
                normalized = normalize_display_name(str(value), profile.display_name)
            except InitializationError as exc:
                raise PersonaDraftError(str(exc)) from exc
        else:
            normalized = _text(value, field)
        profile = _set_field(profile, field, normalized)

    user_fields = _unique(
        tuple(preserved),
        tuple(sorted(explicit_user_fields)),
        limit=len(_EDITABLE_FIELDS),
    )
    generated_fields = tuple(
        field for field in _DEFAULT_GENERATED_FIELDS if field not in user_fields
    )
    profile = replace(
        profile,
        provenance=PersonaProvenance(
            user_fields=user_fields,
            generated_fields=generated_fields,
        ),
    )
    return PersonaDraft(
        profile=profile,
        generated_fields=generated_fields,
        preserved_user_fields=tuple(preserved),
    )
