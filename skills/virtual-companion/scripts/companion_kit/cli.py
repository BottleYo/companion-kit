from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .config import ConfigError, load_profile
from .codex_photo import CodexPhotoWorkflow, PhotoWorkflowError
from .contracts import HostCapabilities, HostClass, RequestEnvelope
from .event_adapter import openclaw_native_preview_request
from .event_job_store import EventJobContext, EventJobError, EventJobStore
from .event_photo import EventPhotoError, EventPhotoWorkflow
from .initializer import (
    BUILTIN_TEMPLATES,
    InitializationError,
    default_profile_path,
    initialize_profile,
    prompt_for_profile,
)
from .host_install import HostInstallResult, HostInstaller
from .image_assets import ImageAssetError, ImageAssetStore
from .installer import InstallError, install_skill
from .kernel import CompanionKernel
from .openai_image_api import ImageApiError, OpenAIImageClient
from .photo_authorization import AuthorizationError, PhotoAuthorizationStore
from .profile_store import ProfileSnapshot, ProfileStore, ProfileStoreError
from .web_panel import run_panel


_HOST_CLASSES = {
    "openclaw": HostClass.EVENT,
    "hermes": HostClass.EVENT,
    "codex": HostClass.DESKTOP,
    "claude": HostClass.DESKTOP,
}


def _local_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("端口必须是 0 到 65535 之间的整数") from exc
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("端口必须是 0 到 65535 之间的整数")
    return port


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Companion Kit 本地、安全的决策工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("templates", help="查看可直接使用的内置模板")

    panel = subparsers.add_parser("ui", help="打开本地初始化与管理面板")
    panel.add_argument(
        "--port",
        type=_local_port,
        default=0,
        help="本地端口；默认自动选择",
    )
    panel.add_argument("--no-open", action="store_true", help="不自动打开浏览器")

    initialize = subparsers.add_parser("init", help="用中文向导创建陪伴对象配置")
    initialize.add_argument(
        "--host",
        choices=sorted(_HOST_CLASSES),
        default="codex",
        help="为哪个工具创建独立配置；默认 codex",
    )
    initialize.add_argument(
        "--template",
        choices=[template.id for template in BUILTIN_TEMPLATES],
        help="直接选择模板；不填写时进入交互向导",
    )
    initialize.add_argument("--display-name", help="自定义陪伴对象的称呼")
    initialize.add_argument(
        "--starting-mode",
        choices=("natural", "familiar"),
        help="相处起点；默认从自然认识开始",
    )
    initialize.add_argument(
        "--enable-romance",
        action="store_true",
        help="允许关系自然发展为恋爱式陪伴；默认关闭",
    )
    initialize.add_argument("--output", help="配置保存位置；默认使用独立用户目录")
    initialize.add_argument("--force", action="store_true", help="替换已有配置")
    initialize.add_argument("--json", action="store_true", help="输出机器可读结果")

    validate = subparsers.add_parser("validate", help="校验人格配置")
    validate.add_argument("--config", help="配置路径；默认使用初始化生成的配置")
    validate.add_argument(
        "--host",
        choices=sorted(_HOST_CLASSES),
        default="codex",
    )

    decide = subparsers.add_parser("decide", help="输出宿主无关的处理决策，不执行生图或发送")
    decide.add_argument("--config", help="配置路径；默认使用初始化生成的配置")
    decide.add_argument("--host", choices=sorted(_HOST_CLASSES), required=True)
    decide.add_argument("--text", required=True)
    decide.add_argument("--can-generate", action="store_true")
    decide.add_argument("--can-deliver", action="store_true")
    decide.add_argument("--has-target", action="store_true")
    decide.add_argument("--can-attach", action="store_true")

    event_photo = subparsers.add_parser(
        "event-photo",
        help="OpenClaw / Hermes 当前会话的图片能力与严格模式",
    )
    event_commands = event_photo.add_subparsers(
        dest="event_photo_command",
        required=True,
    )

    event_status = event_commands.add_parser("status", help="查看当前宿主图片能力")
    event_status.add_argument("--host", choices=("openclaw", "hermes"), required=True)
    event_status.add_argument("--config", help="配置路径；默认使用宿主独立配置")

    def add_event_context(command: argparse.ArgumentParser) -> None:
        command.add_argument("--host", choices=("openclaw", "hermes"), required=True)
        command.add_argument("--instance-scope", required=True)
        command.add_argument("--conversation-scope", required=True)
        command.add_argument("--request-event-id", required=True)

    for name, help_text in (
        ("prepare", "准备一次严格模式调用，只创建单次授权"),
        ("run", "消费单次授权并调用 OpenAI Image API"),
    ):
        command = event_commands.add_parser(name, help=help_text)
        add_event_context(command)
        command.add_argument("--config", help="配置路径；默认使用宿主独立配置")
        command.add_argument("--text", required=True, help="本次明确照片命令")
        command.add_argument(
            "--purpose",
            choices=("prototype", "photo"),
            required=True,
        )
        if name == "run":
            command.add_argument("--job-id", required=True)
            command.add_argument("--plan-id", required=True)
            command.add_argument("--confirm-once", action="store_true")

    event_handoff = event_commands.add_parser(
        "handoff",
        help="把已生成图片一次性交给当前会话附件机制",
    )
    add_event_context(event_handoff)
    event_handoff.add_argument("--job-id", required=True)
    event_handoff.add_argument("--asset-id", required=True)

    event_delivered = event_commands.add_parser(
        "delivered",
        help="仅在宿主明确确认媒体接管后完成作业",
    )
    add_event_context(event_delivered)
    event_delivered.add_argument("--job-id", required=True)
    event_delivered.add_argument("--asset-id", required=True)
    event_delivered.add_argument("--confirm-receipt", action="store_true")

    event_identity = event_commands.add_parser(
        "confirm-identity",
        help="把当前会话候选图固定为该宿主的唯一身份参考",
    )
    add_event_context(event_identity)
    event_identity.add_argument("--config", help="配置路径；默认使用宿主独立配置")
    event_identity.add_argument("--candidate-id", required=True)
    event_identity.add_argument("--profile-version", required=True)

    photo = subparsers.add_parser("photo", help="查看 Codex 内置图片能力")
    photo_commands = photo.add_subparsers(dest="photo_command", required=True)

    photo_status = photo_commands.add_parser("status", help="查看 Codex 内置图片能力")
    photo_status.add_argument("--config", help="配置路径；默认使用初始化生成的配置")

    photo_prepare = photo_commands.add_parser(
        "prepare",
        help="已停用的旧 Codex API 入口",
    )
    photo_prepare.add_argument("--config", help="配置路径；默认使用初始化生成的配置")
    photo_prepare.add_argument("--text", required=True, help="本次明确照片命令")
    photo_prepare.add_argument(
        "--purpose",
        choices=("prototype", "photo"),
        required=True,
        help="首次固定形象用 prototype，日常照片用 photo",
    )
    photo_prepare.add_argument(
        "--task-scope",
        required=True,
        help="当前 Codex 任务内稳定的不透明作用域",
    )

    photo_run = photo_commands.add_parser(
        "run",
        help="已停用的旧 Codex API 入口",
    )
    photo_run.add_argument("--config", help="配置路径；默认使用初始化生成的配置")
    photo_run.add_argument("--text", required=True, help="必须与 prepare 完全相同")
    photo_run.add_argument("--purpose", choices=("prototype", "photo"), required=True)
    photo_run.add_argument("--task-scope", required=True)
    photo_run.add_argument("--plan-id", required=True)
    photo_run.add_argument(
        "--confirm-once",
        action="store_true",
        help="确认一次 API 计费、数据外发及虚构成年人约束",
    )

    photo_delivered = photo_commands.add_parser(
        "delivered",
        help="已停用的旧 Codex API 入口",
    )
    photo_delivered.add_argument("--artifact-id", required=True)
    photo_delivered.add_argument("--task-scope", required=True)

    identity = subparsers.add_parser("identity", help="已停用的旧 Codex API 身份入口")
    identity_commands = identity.add_subparsers(dest="identity_command", required=True)
    identity_confirm = identity_commands.add_parser(
        "confirm",
        help="把当前任务候选图固定为唯一身份参考",
    )
    identity_confirm.add_argument("--config", help="配置路径；默认使用初始化生成的配置")
    identity_confirm.add_argument("--candidate-id", required=True)
    identity_confirm.add_argument("--task-scope", required=True)
    identity_confirm.add_argument(
        "--profile-version",
        required=True,
        help="photo run 返回的人格配置版本",
    )

    install = subparsers.add_parser("install", help="安装通用 Skill；默认只预览")
    install.add_argument("--host", choices=sorted(_HOST_CLASSES), required=True)
    install.add_argument(
        "--target-root",
        help="高级用法：覆盖宿主默认安装根目录",
    )
    install.add_argument("--apply", action="store_true", help="确认写入目标目录")
    install.add_argument("--force", action="store_true")
    install.add_argument(
        "--forbid-text",
        action="append",
        default=[],
        help="安装前额外拒绝的私人标识；可重复使用",
    )
    return parser


def _skill_root() -> Path:
    candidate = Path(__file__).resolve().parents[2]
    if not (candidate / "SKILL.md").is_file():
        raise InstallError("安装命令必须从完整 Skill 包中的 companionctl.py 运行")
    return candidate


def _config_path(explicit: str | None, host: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    configured = os.environ.get("COMPANION_PROFILE", "").strip()
    if configured:
        return Path(configured).expanduser()
    return default_profile_path(host)


def _private_data_root(host: str | None = None) -> Path:
    return default_profile_path(host).parents[1] / "private"


def _image_assets(host: str | None = None) -> ImageAssetStore:
    skill_root = _skill_root()
    assets = ImageAssetStore(
        _private_data_root(host) / "images",
        forbidden_roots=(skill_root.parents[1],),
    )
    if assets.root.exists():
        assets.prune_runtime()
    return assets


def _photo_workflow() -> CodexPhotoWorkflow:
    return CodexPhotoWorkflow(
        authorizations=PhotoAuthorizationStore(
            _private_data_root() / "authorizations"
        ),
        assets=_image_assets(),
        client=OpenAIImageClient(),
    )


def _event_photo_workflow(host: str) -> EventPhotoWorkflow:
    private_root = _private_data_root(host)
    return EventPhotoWorkflow(
        host=host,
        authorizations=PhotoAuthorizationStore(
            private_root / "authorizations",
            route_id=f"{host}:openai-direct",
        ),
        assets=_image_assets(host),
        jobs=EventJobStore(private_root / "event-jobs"),
        client=OpenAIImageClient(),
    )


def _profile_snapshot(
    explicit: str | None,
    host: str | None = None,
) -> tuple[ProfileStore, ProfileSnapshot]:
    store = ProfileStore(
        skill_root=_skill_root(),
        profile_path=_config_path(explicit, host),
    )
    snapshot = store.read()
    if snapshot is None:
        raise ProfileStoreError("尚未初始化陪伴对象，请先运行 init 或打开 ui")
    return store, snapshot


def _codex_photo_decision(snapshot: ProfileSnapshot, text: str):
    return CompanionKernel(snapshot.profile).decide(
        RequestEnvelope(text=text, session_id="codex-current-task", source="codex"),
        HostCapabilities(
            host_class=HostClass.DESKTOP,
            can_execute_tasks=True,
            can_generate_images=True,
            can_deliver_images=False,
            has_current_reply_target=False,
            can_attach_local_artifacts=True,
        ),
    )


def _event_photo_decision(snapshot: ProfileSnapshot, host: str, text: str):
    return CompanionKernel(snapshot.profile).decide(
        RequestEnvelope(text=text, session_id="current-event", source=host),
        HostCapabilities(
            host_class=HostClass.EVENT,
            can_execute_tasks=True,
            can_generate_images=True,
            can_deliver_images=True,
            has_current_reply_target=True,
            can_attach_local_artifacts=False,
        ),
    )


def _event_context(args: argparse.Namespace) -> EventJobContext:
    return EventJobContext(
        host=args.host,
        instance_scope=args.instance_scope,
        conversation_scope=args.conversation_scope,
        request_event_id=args.request_event_id,
    )


def _reference_status(
    snapshot: ProfileSnapshot,
    host: str | None = None,
) -> tuple[bool, bool]:
    reference_configured = len(snapshot.profile.visual.reference_ids) == 1
    reference_ready = False
    image_root = _private_data_root(host) / "images"
    if reference_configured and image_root.exists():
        try:
            assets = _image_assets(host)
            assets.resolve_reference(
                reference_id=snapshot.profile.visual.reference_ids[0],
                profile_id=snapshot.profile.id,
                identity_version=snapshot.profile.visual.identity_version,
            )
            reference_ready = True
        except ImageAssetError:
            reference_ready = False
    return reference_configured, reference_ready


def _print_templates() -> None:
    print("可选的内置模板：")
    for index, template in enumerate(BUILTIN_TEMPLATES, start=1):
        marker = "（推荐）" if template.recommended else ""
        print(f"  {index}. {template.name}{marker} — {template.description}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "templates":
            _print_templates()
            return 0

        if args.command == "ui":
            return run_panel(
                skill_root=_skill_root(),
                port=args.port,
                open_browser=not args.no_open,
            )

        if args.command == "init":
            template_id = args.template
            display_name = args.display_name
            if template_id is None and sys.stdin.isatty():
                template_id, display_name = prompt_for_profile(
                    display_name=display_name
                )
            if template_id is None:
                template_id = next(
                    template.id for template in BUILTIN_TEMPLATES if template.recommended
                )

            result = initialize_profile(
                skill_root=_skill_root(),
                template_id=template_id,
                display_name=display_name,
                starting_mode=args.starting_mode,
                romance_enabled=args.enable_romance,
                output=args.output,
                force=args.force,
                host=args.host,
            )
            payload = {
                "host": args.host,
                "template_id": result.template.id,
                "template_name": result.template.name,
                "display_name": result.profile.display_name,
                "starting_mode": result.profile.relationship.starting_mode,
                "romance_enabled": result.profile.relationship.romance_enabled,
                "output": str(result.output),
            }
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print("\n✅ 配置完成")
                print(f"风格：{result.template.name}")
                print(f"称呼：{result.profile.display_name}")
                print(f"保存位置：{result.output}")
                print(
                    "\n下一步："
                    + (
                        "安装完整 Codex Plugin，然后开一个新任务直接聊天。"
                        if args.host == "codex"
                        else "在新会话中显式启用 virtual-companion。"
                    )
                )
                host_hint = {
                    "codex": (
                        "提示：Codex 生图始终使用内置能力，不需要 API Key 或其他 Provider。"
                    ),
                    "openclaw": (
                        "提示：OpenClaw 可先使用宿主管理的原生快速模式；"
                        "固定形象严格模式需另行配置并逐次确认。"
                    ),
                    "hermes": (
                        "提示：Hermes 的固定形象严格模式需在宿主进程配置 "
                        "OPENAI_API_KEY，并逐次确认。"
                    ),
                    "claude": (
                        "提示：Claude 当前提供安全的照片计划；人格聊天和原有任务能力可直接使用。"
                    ),
                }[args.host]
                print(host_hint)
            return 0

        if args.command == "validate":
            profile = load_profile(_config_path(args.config, args.host))
            print(json.dumps({"valid": True, "profile_id": profile.id}, ensure_ascii=False))
            return 0

        if args.command == "decide":
            profile = load_profile(_config_path(args.config, args.host))
            capabilities = HostCapabilities(
                host_class=_HOST_CLASSES[args.host],
                can_execute_tasks=True,
                can_generate_images=args.can_generate,
                can_deliver_images=args.can_deliver,
                has_current_reply_target=args.has_target,
                can_attach_local_artifacts=args.can_attach,
            )
            decision = CompanionKernel(profile).decide(
                RequestEnvelope(text=args.text, session_id="local-cli", source=args.host),
                capabilities,
            )
            print(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "event-photo":
            if args.event_photo_command == "status":
                _, snapshot = _profile_snapshot(args.config, args.host)
                reference_configured, reference_ready = _reference_status(
                    snapshot,
                    args.host,
                )
                payload: dict[str, object] = {
                    "host": args.host,
                    "profile_id": snapshot.profile.id,
                    "identity_version": snapshot.profile.visual.identity_version,
                    "reference_configured": reference_configured,
                    "reference_ready": reference_ready,
                    "strict": {
                        "setup": "需要当前宿主进程环境中的 OPENAI_API_KEY",
                        "auth_ready": OpenAIImageClient().auth_ready,
                        "model": "gpt-image-2",
                        "quality": "high",
                        "delivery": "current_reply",
                    },
                }
                if args.host == "openclaw":
                    preview = openclaw_native_preview_request("通用安全预览")
                    payload["native_preview"] = {
                        "setup": "使用宿主 image_generate，无需 Companion Kit API Key",
                        "proof": preview["proof"],
                        "model_request": preview["arguments"]["model"],
                        "quality_request": preview["arguments"]["quality"],
                        "delivery": preview["delivery"],
                    }
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return 0

            context = _event_context(args)
            workflow = _event_photo_workflow(args.host)
            if args.event_photo_command in {"prepare", "run"}:
                _, snapshot = _profile_snapshot(args.config, args.host)
                decision = _event_photo_decision(snapshot, args.host, args.text)
                if args.event_photo_command == "prepare":
                    result = workflow.prepare(
                        snapshot=snapshot,
                        decision=decision,
                        purpose=args.purpose,
                        context=context,
                    )
                else:
                    result = workflow.run(
                        snapshot=snapshot,
                        decision=decision,
                        purpose=args.purpose,
                        context=context,
                        job_id=args.job_id,
                        plan_id=args.plan_id,
                        confirmed=args.confirm_once,
                    )
                print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
                return 0

            if args.event_photo_command == "handoff":
                handoff = workflow.claim_handoff(
                    job_id=args.job_id,
                    asset_id=args.asset_id,
                    context=context,
                )
                print(json.dumps(handoff, ensure_ascii=False, indent=2))
                return 0

            if args.event_photo_command == "delivered":
                workflow.mark_delivered(
                    job_id=args.job_id,
                    asset_id=args.asset_id,
                    context=context,
                    receipt_confirmed=args.confirm_receipt,
                )
                print(
                    json.dumps(
                        {"stage": "delivered", "cleaned": True},
                        ensure_ascii=False,
                    )
                )
                return 0

            profile_store, snapshot = _profile_snapshot(args.config, args.host)
            if snapshot.version != args.profile_version:
                raise ProfileStoreError("当前宿主人格配置已变化，请重新生成并确认候选原型")
            reference, bound = workflow.confirm_identity(
                profile_store=profile_store,
                snapshot=snapshot,
                candidate_id=args.candidate_id,
                context=context,
            )
            print(
                json.dumps(
                    {
                        "stage": "identity_confirmed",
                        "host": args.host,
                        "reference_id": reference.reference_id,
                        "profile_version": bound.version,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if args.command == "photo":
            if args.photo_command == "status":
                _, snapshot = _profile_snapshot(args.config)
                reference_configured, reference_ready = _reference_status(snapshot)
                payload = {
                    "profile_id": snapshot.profile.id,
                    "identity_version": snapshot.profile.visual.identity_version,
                    "reference_configured": reference_configured,
                    "reference_ready": reference_ready,
                    "modes": {
                        "codex_native": {
                            "setup": "无需单独配置",
                            "availability": "由当前 Codex 任务检测",
                            "model": "gpt-image-2（Codex 内置）",
                            "billing": "计入 Codex 方案用量或额度",
                            "use_for": "人物原型、日常照片与参考图编辑",
                        },
                    },
                    "api_key_required": False,
                    "provider_choice_required": False,
                    "cross_task_reference_bridge": (
                        "ready" if reference_ready else "in_development"
                    ),
                }
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return 0

            if args.photo_command in {"prepare", "run"}:
                raise PhotoWorkflowError(
                    "Codex 已停用独立 API 生图入口；请直接使用 Codex 内置生图能力"
                )

            raise PhotoWorkflowError(
                "Codex 已停用独立 API 投递入口；图片由 Codex 当前任务直接接管"
            )

        if args.command == "identity":
            raise PhotoWorkflowError(
                "Codex 已停用独立 API 身份流程；请直接使用 Codex 内置生图能力"
            )

        if args.target_root:
            installed = install_skill(
                host=args.host,
                source=_skill_root(),
                target_root=args.target_root,
                apply=args.apply,
                force=args.force,
                forbidden_text=args.forbid_text,
            )
            payload = {
                "host": installed.host,
                "method": "skill_copy",
                "destination": str(installed.destination),
                "applied": installed.applied,
            }
        else:
            if args.force:
                raise InstallError("默认安装不会直接覆盖；请先移除旧安装后重试")
            host_installer = HostInstaller(
                skill_root=_skill_root(),
                forbidden_text=args.forbid_text,
            )
            if args.apply:
                host_result = host_installer.install(args.host)
            else:
                host_result = HostInstallResult(
                    plan=host_installer.plan(args.host),
                    applied=False,
                )
            payload = host_result.to_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except (
        AuthorizationError,
        ConfigError,
        ImageApiError,
        ImageAssetError,
        InitializationError,
        InstallError,
        EventJobError,
        EventPhotoError,
        PhotoWorkflowError,
        ProfileStoreError,
    ) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
