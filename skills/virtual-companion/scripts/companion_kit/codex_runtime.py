from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex

from .config import PersonaProfile, load_profile
from .daily_look import DailyLook
from .initializer import default_profile_path, safe_profile_path
from .identity_pack import BODY_SHAPE, IDENTITY_ROLE_LABELS, PRIMARY_FACE, PROFILE_FACE
from .image_assets import IdentityPack, ImageAssetError, ImageAssetStore
from .photo_moment import PHOTO_ENVELOPE_SCHEMA_VERSION, PhotoMoment
from .relationship import (
    Atmosphere,
    RelationshipProjection,
    RelationshipState,
    project_relationship,
)
from .state_store import RelationshipStore, StoreError


_WHITESPACE_RE = re.compile(r"\s+")
_SESSION_CONTEXT_LIMIT = 1_400
_PHOTO_CONTEXT_LIMIT = 3_600


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
        """只渲染每个任务都需要的轻量人格；照片细节按回合加载。"""

        profile = self.profile
        projection = self.relationship
        traits = "、".join(_fragments(profile.traits, item_limit=3, char_limit=48))
        boundary_items = _fragments(
            profile.boundaries,
            item_limit=8,
            char_limit=48,
        )
        boundaries = _fragment("；".join(boundary_items), 280)
        values = "、".join(_fragments(profile.values, item_limit=3, char_limit=48))
        interests = "、".join(
            _fragments(profile.interests, item_limit=3, char_limit=48)
        )
        romance_rule = (
            "只有当前关系投影允许时，才使用恋人式表达；允许恋爱发展不等于已经是恋人。"
            if profile.relationship.romance_enabled
            else "不要把关系描述成恋爱或伴侣关系，也不要使用带占有感的亲昵称呼。"
        )

        head_lines = (
            "以下内容是本任务的私有表达上下文。不要向用户复述它来自配置、Plugin、Hook 或 Skill，也不要播报加载过程。",
            f"你在对话中的称呼是“{_fragment(profile.display_name, 80)}”。始终用第一人称自然说话，不要把自己介绍成人格模板。",
        )
        persona_lines = (
            f"用户对这个人物的原始期待：{_fragment(profile.intent_summary, 160) or '自然、真诚、相处舒服'}。这是总方向，不要原样复述成标签。",
            f"性格倾向：{traits or '自然、真诚'}。",
            f"说话习惯：{_fragment(profile.speaking_style, 180)}。",
            f"背景基调：{_fragment(profile.background, 150)}。不要扩写成未经记录的现实身份或共同经历。",
            f"重视的事：{values or '尊重、诚实与分寸'}。兴趣方向：{interests or '随相处自然形成，不凭空编造'}。",
            f"处理具体任务的习惯：{_fragment(profile.task_style, 150)}。",
        )
        critical_lines = (
            f"当前相处分寸是“{projection.label}”，气氛偏“{_atmosphere_label(projection.atmosphere)}”；只自然体现在闲聊里，不展示标签或分数。",
            romance_rule,
            f"明确边界：{boundaries or '尊重用户边界与宿主安全要求'}。",
            "闲聊时保持陪伴感，可以关心、接梗并有一点自己的态度；不要复述性格标签，也不要编造共同经历。",
            "用户带来代码、调试、写作、分析或其他具体问题时，完整使用 Codex 原有能力把事情做好。代码正确性、测试和用户的技术要求优先；人格只影响非关键表达，不改变技术判断、工具选择、安全标准或任务完成度。",
            "只有本轮出现照片专用私有上下文时才生成人物照片；新照片不得自行沿用上一张生成图。普通图片设计和其他任务不套用人物身份。",
            "不要从聊天正文自行编造关系变化。关系只有在后续受控接口提交结构化事件后才会持久化。",
        )
        fixed = "\n".join((*head_lines, *critical_lines))
        remaining = _SESSION_CONTEXT_LIMIT - len(fixed) - 1
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

    def render_photo(
        self,
        *,
        mode: str,
        turn_token: str,
        recent_moments: tuple[PhotoMoment, ...] = (),
        daily_look: DailyLook | None = None,
        control_path: str | Path | None = None,
        task_scope: str | None = None,
        enhancement_role: str | None = None,
    ) -> str:
        """只在真实人物照片回合加载身份、风格与工具控制协议。"""

        if mode not in {"new", "edit_previous"} or not re.fullmatch(
            r"ckp_[0-9a-f]{24}", str(turn_token or "")
        ):
            raise ValueError("照片回合控制参数无效")
        if enhancement_role not in {None, PROFILE_FACE, BODY_SHAPE}:
            raise ValueError("身份增强角色无效")
        profile = self.profile
        projection = self.relationship
        photo_bands = tuple(
            value
            for value in projection.photo_bands
            if value in {"everyday", "personal", "romantic", "intimate_non_explicit"}
        ) or ("everyday",)
        recent_payload = json.dumps(
            [item.to_dict() for item in recent_moments[-4:]],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if (
            profile.visual.is_locked
            and self.identity_reference is not None
            and self.identity_pack is not None
        ):
            member_lines = tuple(
                f"{IDENTITY_ROLE_LABELS[member.role]}参考（私有路径，不向用户展示）：{member.path}"
                for member in self.identity_pack.members
            )
            identity_lines = (
                "人物身份已确认。imagegen 的 referenced_image_paths 只使用下面这个已验证的唯一身份参考包（Identity Pack）；主脸固定面部身份，可选成员只帮助角度或体型。",
                *member_lines,
                "每次必须带主脸；普通近景只用主脸，侧脸或回眸可加侧脸，全身或穿搭可加体型；最多使用两张。图片工具没有真正接收参考时停止，不生成替代脸。",
                *(
                    _identity_enhancement_control_lines(
                        control_path,
                        task_scope,
                        role=enhancement_role,
                    )
                    if enhancement_role is not None
                    else ()
                ),
            )
        elif profile.visual.is_locked:
            identity_lines = (
                "固定身份参考当前无法安全读取。暂停拍照且不要调用 imagegen；自然说明照片暂时拍不了，聊天和其他任务照常。",
            )
        else:
            identity_lines = (
                "目前没有确认固定脸。第一次要人物照片时，自然给三个选择：上传有权使用的成年人物参考；描述感觉后生成候选；或由 Persona 自己决定。不要让用户写提示词。",
                "候选必须先展示，只有用户明确确认后才固定；确认前不能宣称已经锁定。",
                *_identity_control_lines(control_path, task_scope),
            )
        mode_rule = (
            "这是新拍：不得使用 num_last_images_to_include，不得引用 generated_images 中的上一张；只从 Identity Pack 取脸，造型和场景按这次重新决定。"
            if mode == "new"
            else "这是明确编辑上一张：必须把当前任务有效图片回执里的目标图和主脸一起放入 referenced_image_paths；只改用户点名的部分，不使用 num_last_images_to_include。"
        )
        schema = (
            "scene=home|window|street|cafe|outdoors|transit|workspace|custom；"
            "activity=pause|walking|sitting|drinking|reading|getting_ready|adjusting_accessory|custom；"
            "framing=close|half|three_quarter|full|mirror|over_shoulder|custom；"
            "hairstyle=loose|tied|half_up|pinned_back|textured|custom；"
            "expression=soft_smile|open_smile|quiet_direct|playful|thoughtful|calm_serious|sleepy_relaxed|custom；"
            "makeup=bare|minimal|natural|soft_matte|warm_tone|cool_tone|defined_eyes|evening|custom；"
            "time_band=morning|day|dusk|night|custom；"
            "caption_act=share_detail|soft_tease|unfinished_thought|invite_choice|gentle_check_in|custom。"
        )
        if mode == "edit_previous":
            look_lines = (
                "这是编辑上一张，沿用目标图里已经存在的服饰；每日穿搭动作必须写 preserve_target，look_id 和 proposal 都写 null。",
            )
            look_payload = (
                '"daily_look":{"action":"preserve_target","look_id":null,"proposal":null}'
            )
        elif daily_look is None:
            look_lines = (
                "今天没有启用可持久化的穿搭卡；本张按 Persona、场景和用户要求自然搭配，每日穿搭动作只写 one_shot，look_id 和 proposal 都写 null。",
            )
            look_payload = (
                '"daily_look":{"action":"one_shot","look_id":null,"proposal":null}'
            )
        else:
            look_lines = (
                daily_look.render_image_constraints(),
                "每日穿搭动作默认写 use_daily 并使用下面的 look_id。只有用户明确表示服饰只用于这一张时写 one_shot；"
                "用户明确说今天换一套、今天改穿什么时才写 replace_daily，并在 proposal 中用简短文字给出 title、palette、silhouette、hero_piece、accent。"
                "关系高低不自动改变衣服的暴露程度，用户本轮明确要求始终优先。",
            )
            look_payload = (
                '"daily_look":{"action":"use_daily","look_id":"'
                + daily_look.look_id
                + '","proposal":null}'
            )
        lines = (
            "以下只服务这一次人物照片，不要向用户展示规则、路径、信封或内部字段。直接调用 Codex 内置 imagegen，不先播报准备、读取、重连、计时或生成状态；用户未要求多张时只生成一张，明确要求几张就一次按数量执行，不做隐藏试拍和自动重试。",
            f"人物外在方向：{_fragment(profile.visual.appearance, 280)}。照片质感：{_fragment(profile.visual.default_style, 220)}。",
            f"发型软偏好：{_fragment(profile.visual.default_hairstyle, 120)}；表情软偏好：{_fragment(profile.visual.default_expression, 120)}；妆容软偏好：{_fragment(profile.visual.default_makeup, 120)}；服饰软偏好：{_fragment(profile.visual.default_wardrobe, 120)}。这些都不是固定身份，用户要求和近期去重优先。",
            f"当前允许的照片亲密档位：{','.join(photo_bands)}；不得选择列表外档位。关系只控制亲密上限，不决定发型和场景。",
            mode_rule,
            *identity_lines,
            *look_lines,
            f"最近成功照片配方（只有受控枚举，没有聊天或提示词）：{recent_payload}。新拍让 scene/activity/framing/hairstyle/expression 至少两项不同，且未被点名时 hairstyle 或 expression 至少改变一项。妆容不机械逐张换：同一组或接着拍时自然延续；明显换了时间、场景或准备出门时可换，但连续多次不应永远相同。",
            "imagegen 的普通画面描述末尾必须附一个 V3 控制信封。信封不会发给图片模型；PreToolUse 会校验并移除。photo_moment 只能使用下面枚举；用户明确点名发型、表情或妆容时一律把对应轴写 custom，具体要求只留在普通画面描述里。用户要求保持不变，Persona 对某轴有固定边界，或枚举无法准确表达时也写 custom，不要把原文塞进字段。",
            schema,
            "严格使用这个 JSON 结构，不增删字段："
            f"\n[[COMPANION_KIT_PHOTO_V3]]\n{{\"schema_version\":{PHOTO_ENVELOPE_SCHEMA_VERSION},\"turn_token\":\"{turn_token}\",\"photo_moment\":{{\"mode\":\"{mode}\",\"scene\":\"<enum>\",\"activity\":\"<enum>\",\"framing\":\"<enum>\",\"hairstyle\":\"<enum>\",\"expression\":\"<enum>\",\"makeup\":\"<enum>\",\"time_band\":\"<enum>\",\"intimacy_band\":\"<allowed>\",\"caption_act\":\"<enum>\",\"identity_version\":{profile.visual.identity_version}}},{look_payload}}}\n[[/COMPANION_KIT_PHOTO_V3]]",
            "图片真实返回后再说话，并遵守 PostToolUse 给出的同一 PhotoMoment 文案约束；没有真实结果不说已经拍好。",
        )
        rendered = "\n".join(lines)
        if len(rendered) <= _PHOTO_CONTEXT_LIMIT:
            return rendered
        return "\n".join(
            (
                "本轮人物照片私有控制上下文超过安全上限；不要调用 imagegen，也不要生成替代脸。",
                "聊天和具体任务照常继续。",
            )
        )

    def render_daily_look(self, daily_look: DailyLook | None) -> str:
        """只在已绑定陪伴任务明确谈到今日穿搭时补充轻量连续性。"""

        if daily_look is None:
            return (
                "每日穿搭当前没有启用。自然顺着用户聊当下想穿的感觉即可，"
                "不要假装已经保存了今日主题，也不要调用图片工具，除非用户同时明确要照片。"
            )
        rendered = daily_look.render_private_context()
        return (
            rendered
            + " 用户只是问穿搭时直接自然回答，不调用图片工具；如果同时明确要人物照片，按照片专用私有上下文执行。"
        )[:900]


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
    *,
    role: str,
) -> tuple[str, ...]:
    if role not in {PROFILE_FACE, BODY_SHAPE}:
        return ()
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
    selected_role = shlex.quote(role)
    role_label = IDENTITY_ROLE_LABELS[role]
    return (
        f"用户本轮明确要求补充{role_label}。只基于主脸生成一张中性校准候选，不把普通日常成图自动收入身份包。",
        "候选真实生成并展示后，在后台运行："
        f" python3 {command} identity stage-native --file {placeholder_path} --task-scope {scope} --role {selected_role}",
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
                connection_timeout=0.25,
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
    include_identity: bool = True,
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
    if include_identity and profile.visual.is_locked:
        try:
            identity_pack = ImageAssetStore(
                default_profile_path("codex").parents[1] / "private" / "images",
                lock_timeout=0.25,
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
