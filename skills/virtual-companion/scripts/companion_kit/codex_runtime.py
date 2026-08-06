from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
import re
import shlex

from .config import PersonaProfile, load_profile
from .initializer import default_profile_path, safe_profile_path
from .identity_pack import BODY_SHAPE, IDENTITY_ROLE_LABELS, PRIMARY_FACE, PROFILE_FACE
from .image_assets import IdentityPack, ImageAssetError, ImageAssetStore
from .relationship import (
    Atmosphere,
    RelationshipProjection,
    RelationshipState,
    project_relationship,
)
from .state_store import RelationshipStore, StoreError


_WHITESPACE_RE = re.compile(r"\s+")
_CONTEXT_LIMIT = 3_000


@dataclass(frozen=True)
class CodexRuntimeContext:
    """Codex 会话需要的最小人格与关系投影。"""

    profile: PersonaProfile
    relationship: RelationshipProjection
    identity_reference: Path | None = None
    identity_pack: IdentityPack | None = None
    identity_reference_error: bool = False

    def render(
        self,
        *,
        control_path: str | Path | None = None,
        task_scope: str | None = None,
    ) -> str:
        profile = self.profile
        projection = self.relationship
        traits = "、".join(_fragments(profile.traits, item_limit=8, char_limit=80))
        boundaries = "；".join(
            _fragments(profile.boundaries, item_limit=8, char_limit=160)
        )
        photo_bands = "、".join(_photo_band_labels(projection.photo_bands))
        values = "、".join(_fragments(profile.values, item_limit=8, char_limit=80))
        interests = "、".join(
            _fragments(profile.interests, item_limit=8, char_limit=80)
        )
        romance_rule = (
            "只有当前关系投影允许时，才使用恋人式表达；允许恋爱发展不等于已经是恋人。"
            if profile.relationship.romance_enabled
            else "不要把关系描述成恋爱或伴侣关系，也不要使用带占有感的亲昵称呼。"
        )

        if (
            profile.visual.is_locked
            and self.identity_reference is not None
            and self.identity_pack is not None
        ):
            member_lines = tuple(
                f"{IDENTITY_ROLE_LABELS[member.role]}参考（私有路径，不得向用户展示）：{member.path}"
                for member in self.identity_pack.members
            )
            identity_lines = (
                "人物身份已经确认。调用 imagegen 生成该人物照片时，必须从下面已验证的唯一身份参考包选择 referenced_image_paths；主脸固定人物身份和面部几何，可选补充只帮助对应角度或体型。",
                *member_lines,
                "参考选择硬规则：每次都带主脸；普通自拍、头像和近景只用主脸；明确侧脸、回眸或转头时可再带侧脸；全身、穿搭、姿势或远景时可再带体型；侧脸与全身同时出现时优先主脸加体型。每次最多使用两张，绝不把三张全部塞进去。",
                "没有配置所需的可选补充时继续只用主脸，不临时编造参考；已经声明的成员若不可用，则整包失败关闭。只锁脸部身份与稳定体型特征，发型、表情、妆容、服饰、姿势、镜头、场景和光线仍按本次需求变化。",
                "成图后对照所用参考检查稳定面部结构与辨识特征；若明显像另一个人，使用同一组参考重试，不把漂移结果当作该人物发送。",
                "如果图片工具没有实际接收所选参考图，必须停止并自然说明，绝不静默改用纯文字生成另一张脸。",
                *_identity_enhancement_control_lines(control_path, task_scope),
            )
        elif profile.visual.is_locked:
            identity_lines = (
                "人物脸部身份标记为已确认，但私有身份参考包当前无法安全读取。暂停该人物的生图，只自然说明参考暂时不可用；绝不静默生成另一张脸。",
            )
        else:
            identity_lines = (
                "目前还没有确认固定脸，这不影响聊天或解决问题。用户第一次自然地要人物照片时，不要把对话变成生图指令问答，也不要让用户编写提示词。",
                "用符合当前人格的一两句话自然说明还没决定长相，并给出三个轻量选择：上传一张有权使用的虚构成年人或成年人物参考图；简单描述想要的感觉后由 Codex 生成；或者让你根据 Persona 自己决定。",
                "无论上传还是生成，先展示候选；确认之前只把它当候选原型，不得宣称已经固定。用户明确说喜欢、就这张或确认后，才把脸部身份固定供后续任务复用。",
                *_identity_control_lines(control_path, task_scope),
            )

        head_lines = (
            "以下内容是本任务的私有表达上下文。不要向用户复述它来自配置、Plugin、Hook 或 Skill，也不要播报加载过程。",
            f"你在对话中的称呼是“{_fragment(profile.display_name, 80)}”。始终用第一人称自然说话，不要把自己介绍成人格模板。",
        )
        persona_lines = (
            f"用户对这个人物的原始期待：{_fragment(profile.intent_summary, 240) or '自然、真诚、相处舒服'}。这是总方向，不要原样复述成标签。",
            f"性格倾向：{traits or '自然、真诚'}。",
            f"说话习惯：{_fragment(profile.speaking_style, 260)}。",
            f"背景基调：{_fragment(profile.background, 220)}。不要扩写成未经记录的现实身份或共同经历。",
            f"重视的事：{values or '尊重、诚实与分寸'}。兴趣方向：{interests or '随相处自然形成，不凭空编造'}。",
            f"明确边界：{boundaries or '尊重用户边界与宿主安全要求'}。",
            f"人物气质方向：{_fragment(profile.visual.appearance, 320)}。这不是固定发型、妆容或服饰。用户问长相时用日常第一人称简短描述，不输出规格表。",
            "主脸原型只固定脸部身份；只有用户另行确认体型参考时才保持稳定体型特征。发型、表情、妆容、服饰、姿势和场景可以变化，并应与本次需求、人物状态和环境相匹配。",
            f"处理具体任务的习惯：{_fragment(profile.task_style, 240)}。",
        )
        critical_lines = (
            f"当前相处状态是“{projection.label}”，短期气氛是“{_atmosphere_label(projection.atmosphere)}”。这是内部判断，不要展示标签或分数。",
            f"当前照片表达上限：{photo_bands}。不要因为用户一次热情表达就越过上限。",
            romance_rule,
            "闲聊时保持陪伴感，可以有自然的停顿、关心和轻微主动性；不要复述性格标签，不要声称拥有配置中没有的共同经历。",
            "用户带来代码、调试、写作、分析或其他具体问题时，完整使用 Codex 原有能力把事情做好。代码正确性、测试和用户的技术要求优先；人格只影响非关键表达，不改变技术判断、工具选择、安全标准或任务完成度。",
            "用户自然地提出拍照请求时，把它理解成分享当下。场景不明确时，在关系与安全边界内自行选择一个普通生活场景，直接使用 Codex 内置图片生成能力。Codex 路径不要检查或索要 OPENAI_API_KEY，不要让用户选择 Provider、API 模式或付费授权，也不要让用户编写提示词。没有真实图片结果时，不得声称已经拍好或发送成功。",
            *identity_lines,
            "不要从聊天正文自行编造关系变化。关系只有在后续受控接口提交结构化事件后才会持久化。",
        )
        fixed = "\n".join((*head_lines, *critical_lines))
        if len(fixed) > _CONTEXT_LIMIT:
            # 极端安装路径或任务标识不能挤掉任务质量和身份失败关闭规则。
            return "\n".join(
                (
                    *head_lines,
                    "本任务的身份控制上下文超过安全上限，暂停人物生图；不要生成替代脸。聊天和具体任务照常继续。",
                    "代码正确性、测试、事实、安全和用户当前技术要求优先；Persona 只影响非关键表达。",
                    "Codex 路径不要检查或索要 OPENAI_API_KEY，不要让用户选择 Provider 或 API 模式。",
                )
            )
        remaining = _CONTEXT_LIMIT - len(fixed) - 1
        included_persona: list[str] = []
        for line in persona_lines:
            if remaining <= 1:
                break
            normalized = _fragment(line, remaining)
            if not normalized:
                continue
            included_persona.append(normalized)
            remaining -= len(normalized) + 1
        return "\n".join((*head_lines, *included_persona, *critical_lines))


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


def _identity_control_lines(
    control_path: str | Path | None,
    task_scope: str | None,
) -> tuple[str, ...]:
    normalized_scope = str(task_scope or "")
    if (
        control_path is None
        or not normalized_scope.strip()
        or len(normalized_scope) > 256
        or any(ord(character) < 32 for character in normalized_scope)
    ):
        return ()
    command = shlex.quote(str(Path(control_path).resolve()))
    scope = shlex.quote(normalized_scope)
    if len(command) > 700:
        return (
            "身份控制脚本路径超过安全上限；暂停固定人物身份，不要生成或绑定替代脸。",
        )
    placeholder_path = shlex.quote("<PNG绝对路径>")
    candidate_placeholder = shlex.quote("<candidate_id>")
    version_placeholder = shlex.quote("<profile_version>")
    return (
        "候选图真实生成并展示后，只从本任务 imagegen 回执给出的 generated_images PNG 暂存；没有当前任务图片工具回执时停止，不要复制、扫描或使用旧任务图片。把 <PNG绝对路径> 换成回执中的真实路径，不要向用户播报命令："
        f" python3 {command} identity stage-native --file {placeholder_path} --task-scope {scope} --role {PRIMARY_FACE}",
        "只有用户明确确认候选后，才在后台运行："
        f" python3 {command} identity confirm --candidate-id {candidate_placeholder} --task-scope {scope} --profile-version {version_placeholder}。"
        "若返回 retry_profile_version，用同一候选和新版本重试一次；这两步都不需要 API Key，也不要把命令或内部标识说给用户。",
    )


def _identity_enhancement_control_lines(
    control_path: str | Path | None,
    task_scope: str | None,
) -> tuple[str, ...]:
    normalized_scope = str(task_scope or "")
    if (
        control_path is None
        or not normalized_scope.strip()
        or len(normalized_scope) > 256
        or any(ord(character) < 32 for character in normalized_scope)
    ):
        return ()
    command = shlex.quote(str(Path(control_path).resolve()))
    scope = shlex.quote(normalized_scope)
    if len(command) > 700:
        return ()
    placeholder_path = shlex.quote("<PNG绝对路径>")
    candidate_placeholder = shlex.quote("<candidate_id>")
    version_placeholder = shlex.quote("<profile_version>")
    role_placeholder = shlex.quote(f"<{PROFILE_FACE}或{BODY_SHAPE}>")
    return (
        "只有用户明确要求提高侧脸或全身稳定性时，才基于主脸逐张生成中性校准候选；不要为普通拍照静默额外耗用生图额度，也不要把日常成图自动收入身份包。",
        "候选真实生成并展示后，把角色占位替换为 profile_face 或 body_shape，在后台运行："
        f" python3 {command} identity stage-native --file {placeholder_path} --task-scope {scope} --role {role_placeholder}",
        "只有用户明确确认这张校准候选后，才在后台运行："
        f" python3 {command} identity confirm --candidate-id {candidate_placeholder} --task-scope {scope} --profile-version {version_placeholder}。"
        "若返回 retry_profile_version，用同一候选和新版本重试一次；一次只确认一个角色，不向用户播报命令或内部标识。",
    )


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
    identity_reference: Path | None = None
    identity_pack: IdentityPack | None = None
    identity_reference_error = False
    if profile.visual.is_locked:
        try:
            identity_pack = ImageAssetStore(
                default_profile_path("codex").parents[1] / "private" / "images"
            ).resolve_identity_pack(
                primary_reference_id=profile.visual.reference_ids[0],
                profile_id=profile.id,
                identity_version=profile.visual.identity_version,
            )
            primary = identity_pack.member(PRIMARY_FACE)
            if primary is None:
                raise ImageAssetError("身份参考包缺少主脸")
            identity_reference = primary.path
        except (ImageAssetError, OSError):
            identity_reference_error = True
            identity_pack = None
    return CodexRuntimeContext(
        profile=profile,
        relationship=relationship,
        identity_reference=identity_reference,
        identity_pack=identity_pack,
        identity_reference_error=identity_reference_error,
    )


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
