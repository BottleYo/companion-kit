from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Callable

from .config import ConfigError, PersonaProfile, load_profile
from .public_bundle import contains_absolute_path
from .relationship import RelationshipError, RelationshipPolicy


class InitializationError(ValueError):
    """初始化输入或目标位置不安全。"""


@dataclass(frozen=True)
class TemplateInfo:
    id: str
    name: str
    description: str
    filename: str
    recommended: bool = False


@dataclass(frozen=True)
class InitializationResult:
    template: TemplateInfo
    profile: PersonaProfile
    output: Path


BUILTIN_TEMPLATES = (
    TemplateInfo(
        id="warm_healer",
        name="温柔治愈",
        description="耐心、柔和，适合日常陪伴和情绪支持",
        filename="warm_healer.toml",
        recommended=True,
    ),
    TemplateInfo(
        id="sunny_friend",
        name="元气朋友",
        description="明快、有行动力，适合轻松聊天和鼓励推进",
        filename="sunny_friend.toml",
    ),
    TemplateInfo(
        id="calm_partner",
        name="冷静搭档",
        description="理性、可靠，适合工作讨论和问题解决",
        filename="calm_partner.toml",
    ),
    TemplateInfo(
        id="playful_pal",
        name="轻松幽默",
        description="自然、有趣，适合轻松互动但不过度冒犯",
        filename="playful_pal.toml",
    ),
)

_PROFILE_HOSTS = {"openclaw", "hermes", "codex", "claude"}


def default_profile_path(host: str | None = None) -> Path:
    normalized_host = str(host or "codex").strip().lower()
    if normalized_host not in _PROFILE_HOSTS:
        raise InitializationError(f"不支持的宿主：{host}")
    configured_home = os.environ.get("COMPANION_HOME", "").strip()
    root = (
        Path(configured_home).expanduser()
        if configured_home
        else Path.home() / ".companion-kit"
    )
    root = Path(os.path.abspath(root))
    if normalized_host == "codex":
        return root / "profiles" / "default.toml"
    return root / "hosts" / normalized_host / "profiles" / "default.toml"


def safe_profile_path(raw_output: str | Path) -> Path:
    raw_path = Path(raw_output).expanduser()
    if ".." in raw_path.parts:
        raise InitializationError("配置目标不能包含 .. 路径")

    output_path = Path(os.path.abspath(raw_path))
    if sys.platform == "darwin":
        filesystem_root = Path(output_path.anchor)
        for alias, target in (
            (
                filesystem_root / "var",
                filesystem_root / "private" / "var",
            ),
            (
                filesystem_root / "tmp",
                filesystem_root / "private" / "tmp",
            ),
        ):
            try:
                relative = output_path.relative_to(alias)
            except ValueError:
                continue
            output_path = target / relative
            break
    current = Path(output_path.anchor)
    for part in output_path.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            break
        except OSError as exc:
            raise InitializationError(f"无法检查配置目标：{exc}") from exc
        if stat.S_ISLNK(mode):
            raise InitializationError(f"配置目标不能经过符号链接：{current}")
    return output_path


def template_by_id(template_id: str) -> TemplateInfo:
    normalized = str(template_id or "").strip()
    for template in BUILTIN_TEMPLATES:
        if template.id == normalized:
            return template
    available = "、".join(template.id for template in BUILTIN_TEMPLATES)
    raise InitializationError(f"没有这个模板：{template_id}；可选：{available}")


def load_template_profile(
    skill_root: str | Path,
    template: TemplateInfo,
    *,
    host: str | None = "codex",
) -> PersonaProfile:
    skill_root = Path(skill_root)
    normalized_host = str(host or "codex").strip().lower()
    if normalized_host not in _PROFILE_HOSTS:
        raise InitializationError(f"不支持的宿主：{host}")
    template_root = (skill_root / "assets" / "templates").resolve()
    if normalized_host != "codex":
        template_root = (template_root / "legacy").resolve()
    template_path = (template_root / template.filename).resolve()
    if template_root not in template_path.parents:
        raise InitializationError("内置模板路径不安全")
    try:
        return load_profile(template_path)
    except ConfigError as exc:
        raise InitializationError(f"内置模板无效：{exc}") from exc


def normalize_display_name(value: str | None, default: str) -> str:
    result = str(value or "").strip() or default
    if len(result) > 40 or any(character in result for character in "\r\n\t"):
        raise InitializationError("称呼应为 1–40 个普通字符")
    if contains_absolute_path(result):
        raise InitializationError("称呼不能包含本机路径")
    return result


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_list(values: tuple[str, ...]) -> str:
    if not values:
        return "[]"
    lines = ["["]
    lines.extend(f"  {_toml_string(value)}," for value in values)
    lines.append("]")
    return "\n".join(lines)


def _render_legacy_profile(profile: PersonaProfile) -> str:
    return "\n".join(
        (
            "# 由 Companion Kit 初始化向导生成。",
            "# 请勿写入密钥、聊天记录、真实照片路径或私人素材。",
            "schema_version = 2",
            f"id = {_toml_string(profile.id)}",
            f"display_name = {_toml_string(profile.display_name)}",
            "",
            "[persona]",
            f"traits = {_toml_list(profile.traits)}",
            f"speaking_style = {_toml_string(profile.speaking_style)}",
            f"boundaries = {_toml_list(profile.boundaries)}",
            "",
            "[visual]",
            f"identity_anchor = {_toml_string(profile.visual.identity_anchor)}",
            f"identity_version = {profile.visual.identity_version}",
            f"appearance = {_toml_string(profile.visual.appearance)}",
            f"default_style = {_toml_string(profile.visual.default_style)}",
            f"reference_ids = {_toml_list(profile.visual.reference_ids)}",
            "",
            "[relationship]",
            f"starting_mode = {_toml_string(profile.relationship.starting_mode)}",
            f"romance_enabled = {str(profile.relationship.romance_enabled).lower()}",
            "",
        )
    )


def render_profile(profile: PersonaProfile) -> str:
    if profile.schema_version < 3:
        return _render_legacy_profile(profile)
    return "\n".join(
        (
            "# 由 Companion Kit Persona 面板生成。",
            "# 请勿写入密钥、聊天记录、真实照片路径或私人素材。",
            "schema_version = 3",
            f"id = {_toml_string(profile.id)}",
            f"display_name = {_toml_string(profile.display_name)}",
            f"template_id = {_toml_string(profile.template_id)}",
            f"intent_summary = {_toml_string(profile.intent_summary)}",
            "",
            "[persona]",
            f"traits = {_toml_list(profile.traits)}",
            f"speaking_style = {_toml_string(profile.speaking_style)}",
            f"boundaries = {_toml_list(profile.boundaries)}",
            f"background = {_toml_string(profile.background)}",
            f"values = {_toml_list(profile.values)}",
            f"interests = {_toml_list(profile.interests)}",
            f"task_style = {_toml_string(profile.task_style)}",
            "",
            "[appearance]",
            f"direction = {_toml_string(profile.visual.appearance)}",
            f"default_style = {_toml_string(profile.visual.default_style)}",
            f"default_hairstyle = {_toml_string(profile.visual.default_hairstyle)}",
            f"default_expression = {_toml_string(profile.visual.default_expression)}",
            f"default_makeup = {_toml_string(profile.visual.default_makeup)}",
            f"default_wardrobe = {_toml_string(profile.visual.default_wardrobe)}",
            "",
            "[visual_identity]",
            f"status = {_toml_string(profile.visual.identity_status)}",
            f"facial_anchor = {_toml_string(profile.visual.identity_anchor)}",
            f"identity_version = {profile.visual.identity_version}",
            f"reference_ids = {_toml_list(profile.visual.reference_ids)}",
            "",
            "[relationship]",
            f"starting_mode = {_toml_string(profile.relationship.starting_mode)}",
            f"romance_enabled = {str(profile.relationship.romance_enabled).lower()}",
            "",
            "[provenance]",
            f"user_fields = {_toml_list(profile.provenance.user_fields)}",
            f"generated_fields = {_toml_list(profile.provenance.generated_fields)}",
            "",
        )
    )


def save_profile_document(
    *,
    profile: PersonaProfile,
    output: str | Path,
    force: bool = False,
    private_parent: bool = False,
) -> tuple[PersonaProfile, Path]:
    output_path = safe_profile_path(output)
    if output_path.suffix.casefold() != ".toml":
        raise InitializationError("配置文件名需要以 .toml 结尾")
    if output_path.exists() and not force:
        raise InitializationError(f"配置已存在：{output_path}；如需替换请使用 --force")
    if output_path.exists() and not output_path.is_file():
        raise InitializationError("配置目标必须是普通文件")

    temporary_path: Path | None = None
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if private_parent and os.name != "nt":
            output_path.parent.chmod(0o700)
        safe_profile_path(output_path)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=".companion-profile-",
            suffix=".toml",
            delete=False,
        ) as handle:
            handle.write(render_profile(profile))
            temporary_path = Path(handle.name)
        validated = load_profile(temporary_path)
        temporary_path.replace(output_path)
        output_path.chmod(0o600)
        temporary_path = None
    except (OSError, ConfigError) as exc:
        raise InitializationError(f"无法创建配置：{exc}") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return validated, output_path


def initialize_profile(
    *,
    skill_root: str | Path,
    template_id: str,
    display_name: str | None,
    starting_mode: str | None = None,
    romance_enabled: bool | None = None,
    output: str | Path | None = None,
    force: bool = False,
    host: str | None = None,
) -> InitializationResult:
    root = Path(skill_root).resolve()
    template = template_by_id(template_id)
    normalized_host = str(host or "codex").strip().lower()
    source_profile = load_template_profile(root, template, host=normalized_host)
    try:
        relationship = RelationshipPolicy(
            starting_mode=starting_mode or source_profile.relationship.starting_mode,
            romance_enabled=(
                source_profile.relationship.romance_enabled
                if romance_enabled is None
                else romance_enabled
            ),
        )
    except RelationshipError as exc:
        raise InitializationError(str(exc)) from exc
    profile = replace(
        source_profile,
        display_name=normalize_display_name(display_name, source_profile.display_name),
        relationship=relationship,
    )

    uses_default_output = output is None
    raw_output = (
        Path(output).expanduser()
        if output is not None
        else default_profile_path(host)
    )
    validated, output_path = save_profile_document(
        profile=profile,
        output=raw_output,
        force=force,
        private_parent=uses_default_output,
    )

    return InitializationResult(
        template=template,
        profile=validated,
        output=output_path,
    )


def prompt_for_profile(
    *,
    display_name: str | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> tuple[str, str]:
    output_fn("欢迎使用 Companion Kit，我会用两个问题帮你完成配置。")
    output_fn("不确定怎么选时直接按回车，会使用推荐项。")
    output_fn("")
    output_fn("请选择陪伴风格：")
    for index, template in enumerate(BUILTIN_TEMPLATES, start=1):
        marker = "（推荐）" if template.recommended else ""
        output_fn(f"  {index}. {template.name}{marker} — {template.description}")

    while True:
        try:
            raw_choice = input_fn("输入序号 [1]：").strip()
        except EOFError:
            raw_choice = ""
        if not raw_choice:
            selected = BUILTIN_TEMPLATES[0]
            break
        if raw_choice.isdigit() and 1 <= int(raw_choice) <= len(BUILTIN_TEMPLATES):
            selected = BUILTIN_TEMPLATES[int(raw_choice) - 1]
            break
        output_fn(f"请输入 1–{len(BUILTIN_TEMPLATES)}，或直接按回车。")

    default_name = load_template_profile(
        Path(__file__).resolve().parents[2], selected
    ).display_name
    if display_name is not None:
        return selected.id, normalize_display_name(display_name, default_name)
    try:
        raw_name = input_fn(f"你想怎么称呼 TA？ [{default_name}]：").strip()
    except EOFError:
        raw_name = ""
    return selected.id, normalize_display_name(raw_name, default_name)
