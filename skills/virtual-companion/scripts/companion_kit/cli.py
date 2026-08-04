from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .config import ConfigError, load_profile
from .codex_photo import CodexPhotoWorkflow, PhotoWorkflowError
from .contracts import HostCapabilities, HostClass, RequestEnvelope
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

    decide = subparsers.add_parser("decide", help="输出宿主无关的处理决策，不执行生图或发送")
    decide.add_argument("--config", help="配置路径；默认使用初始化生成的配置")
    decide.add_argument("--host", choices=sorted(_HOST_CLASSES), required=True)
    decide.add_argument("--text", required=True)
    decide.add_argument("--can-generate", action="store_true")
    decide.add_argument("--can-deliver", action="store_true")
    decide.add_argument("--has-target", action="store_true")
    decide.add_argument("--can-attach", action="store_true")

    photo = subparsers.add_parser("photo", help="Codex 当前任务的图片能力与严格模式")
    photo_commands = photo.add_subparsers(dest="photo_command", required=True)

    photo_status = photo_commands.add_parser("status", help="查看两种 Codex 图片模式")
    photo_status.add_argument("--config", help="配置路径；默认使用初始化生成的配置")

    photo_prepare = photo_commands.add_parser(
        "prepare",
        help="准备一次严格模式调用，只创建授权计划，不生图",
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
        help="消费一次授权并调用严格 Image API",
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
        help="确认已附加到当前任务并清理临时成图",
    )
    photo_delivered.add_argument("--artifact-id", required=True)
    photo_delivered.add_argument("--task-scope", required=True)

    identity = subparsers.add_parser("identity", help="确认固定人物原型")
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


def _config_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    configured = os.environ.get("COMPANION_PROFILE", "").strip()
    if configured:
        return Path(configured).expanduser()
    return default_profile_path()


def _private_data_root() -> Path:
    return default_profile_path().parents[1] / "private"


def _image_assets() -> ImageAssetStore:
    skill_root = _skill_root()
    assets = ImageAssetStore(
        _private_data_root() / "images",
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


def _profile_snapshot(explicit: str | None) -> tuple[ProfileStore, ProfileSnapshot]:
    store = ProfileStore(
        skill_root=_skill_root(),
        profile_path=_config_path(explicit),
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
            )
            payload = {
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
                print("\n下一步：在新会话中启用 virtual-companion 即可。")
                print("提示：Codex 可用原生预览；严格固定形象模式需另行配置并逐次确认。")
            return 0

        if args.command == "validate":
            profile = load_profile(_config_path(args.config))
            print(json.dumps({"valid": True, "profile_id": profile.id}, ensure_ascii=False))
            return 0

        if args.command == "decide":
            profile = load_profile(_config_path(args.config))
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

        if args.command == "photo":
            if args.photo_command == "status":
                _, snapshot = _profile_snapshot(args.config)
                strict_ready = OpenAIImageClient().auth_ready
                reference_configured = len(
                    snapshot.profile.visual.reference_ids
                ) == 1
                reference_ready = False
                if reference_configured:
                    try:
                        assets = _image_assets()
                        if not assets.root.exists():
                            raise ImageAssetError("身份资产目录不存在")
                        assets.resolve_reference(
                            reference_id=snapshot.profile.visual.reference_ids[0],
                            profile_id=snapshot.profile.id,
                            identity_version=snapshot.profile.visual.identity_version,
                        )
                        reference_ready = True
                    except ImageAssetError:
                        reference_ready = False
                payload = {
                    "profile_id": snapshot.profile.id,
                    "identity_version": snapshot.profile.visual.identity_version,
                    "reference_configured": reference_configured,
                    "reference_ready": reference_ready,
                    "modes": {
                        "codex_native": {
                            "setup": "无需单独配置",
                            "availability": "由当前 Codex 任务检测",
                            "model": "gpt-image-2（宿主管理）",
                            "quality": "宿主管理，不能作为 high 严格证明",
                            "use_for": "快速预览与当前任务内迭代",
                        },
                        "openai_strict": {
                            "setup": "需要当前宿主环境的 OPENAI_API_KEY",
                            "auth_ready": strict_ready,
                            "model": "gpt-image-2",
                            "quality": "high",
                            "use_for": "固定人物原型与参考图编辑",
                        },
                    },
                }
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return 0

            if args.photo_command in {"prepare", "run"}:
                _, snapshot = _profile_snapshot(args.config)
                decision = _codex_photo_decision(snapshot, args.text)
                workflow = _photo_workflow()
                if args.photo_command == "prepare":
                    plan = workflow.prepare(
                        snapshot=snapshot,
                        decision=decision,
                        purpose=args.purpose,
                        task_scope=args.task_scope,
                    )
                    payload = {
                        "stage": "planned",
                        **plan.to_dict(),
                        "confirmation_required": True,
                        "confirmation": (
                            "确认后只调用一次 OpenAI Image API；会按 API 用量计费，"
                            "并发送本次图片提示。已有固定形象时还会发送一张参考图。"
                            "本阶段只处理虚构成年人，不模仿真人。"
                        ),
                    }
                else:
                    result = workflow.run(
                        snapshot=snapshot,
                        decision=decision,
                        purpose=args.purpose,
                        task_scope=args.task_scope,
                        plan_id=args.plan_id,
                        confirmed=args.confirm_once,
                    )
                    payload = result.to_dict()
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return 0

            workflow = _photo_workflow()
            workflow.finish_delivery(
                artifact_id=args.artifact_id,
                task_scope=args.task_scope,
            )
            print(json.dumps({"stage": "delivered", "cleaned": True}, ensure_ascii=False))
            return 0

        if args.command == "identity":
            profile_store, snapshot = _profile_snapshot(args.config)
            if snapshot.version != args.profile_version:
                raise ProfileStoreError("人格配置已变化，请重新生成并确认候选原型")
            reference, bound = _photo_workflow().confirm_identity(
                profile_store=profile_store,
                snapshot=snapshot,
                candidate_id=args.candidate_id,
                task_scope=args.task_scope,
            )
            print(
                json.dumps(
                    {
                        "stage": "identity_confirmed",
                        "reference_id": reference.reference_id,
                        "profile_version": bound.version,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

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
        PhotoWorkflowError,
        ProfileStoreError,
    ) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
