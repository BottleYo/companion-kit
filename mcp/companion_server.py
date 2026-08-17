#!/usr/bin/env python3
from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
import re
import sys
from typing import Any


sys.dont_write_bytecode = True

WIDGET_URI = "ui://companion-kit/home-v1.html"
MCP_APP_MIME = "text/html;profile=mcp-app"
TOOL_NAME = "open_companion_home"
SAVE_PERSONA_TOOL = "save_companion_persona"
SET_PRIMARY_FACE_TOOL = "set_companion_primary_face"
CLEAR_SCOPES_TOOL = "clear_companion_task_bindings"
PRIVATE_UI_STATE_KEY = "companion-kit/ui-state"
_PROFILE_VERSION_RE = re.compile(r"^[a-f0-9]{64}$")
LATEST_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = frozenset(
    {"2024-11-05", "2025-03-26", "2025-06-18", LATEST_PROTOCOL_VERSION}
)

STATUS_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "version": {"type": "string"},
        "persona": {
            "type": "object",
            "properties": {
                "configured": {"type": "boolean"},
                "healthy": {"type": "boolean"},
            },
            "required": ["configured", "healthy"],
            "additionalProperties": False,
        },
        "identity": {
            "type": "object",
            "properties": {
                "level": {
                    "type": "string",
                    "enum": ["unset", "basic", "enhanced", "unavailable"],
                },
                "ready": {"type": "boolean"},
                "count": {"type": "integer", "minimum": 0},
            },
            "required": ["level", "ready", "count"],
            "additionalProperties": False,
        },
        "hooks": {
            "type": "object",
            "properties": {
                "session_verified": {"type": "boolean"},
                "companion_context_verified": {"type": "boolean"},
                "photo_receipt_verified": {"type": "boolean"},
            },
            "required": [
                "session_verified",
                "companion_context_verified",
                "photo_receipt_verified",
            ],
            "additionalProperties": False,
        },
        "scope": {
            "type": "object",
            "properties": {
                "active_task_count": {"type": "integer", "minimum": 0},
                "healthy": {"type": "boolean"},
            },
            "required": ["active_task_count", "healthy"],
            "additionalProperties": False,
        },
        "readiness": {
            "type": "object",
            "properties": {
                "state": {"type": "string"},
                "ready": {"type": "boolean"},
                "summary": {"type": "string"},
                "detail": {"type": "string"},
            },
            "required": ["state", "ready", "summary", "detail"],
            "additionalProperties": False,
        },
    },
    "required": ["version", "persona", "identity", "hooks", "scope", "readiness"],
    "additionalProperties": False,
}


def _plugin_root() -> Path:
    configured = os.environ.get("PLUGIN_ROOT", "").strip()
    return Path(configured).resolve() if configured else Path(__file__).resolve().parents[1]


PLUGIN_ROOT = _plugin_root()
SKILL_ROOT = PLUGIN_ROOT / "skills" / "virtual-companion"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from companion_kit import __version__  # noqa: E402
from companion_kit.companion_status import CodexCompanionStatusService  # noqa: E402


def _widget_html() -> str:
    path = Path(__file__).with_name("widget.html")
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 512 * 1024:
            raise OSError
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeError("Companion Kit Plugin UI 资源不完整") from exc


def _tool_descriptor() -> dict[str, object]:
    return {
        "name": TOOL_NAME,
        "title": "打开 Companion Kit 人物面板",
        "description": (
            "仅当用户明确想打开、查看或管理 Companion Kit 人物面板时使用。"
            "普通编程、分析、写作和一般图片任务不要调用。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "outputSchema": STATUS_OUTPUT_SCHEMA,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
            "idempotentHint": True,
        },
        "_meta": {
            "ui": {
                "resourceUri": WIDGET_URI,
                "visibility": ["model", "app"],
            },
            "openai/outputTemplate": WIDGET_URI,
            "openai/toolInvocation/invoking": "正在打开人物面板",
            "openai/toolInvocation/invoked": "人物面板已打开",
        },
    }


def _save_persona_descriptor() -> dict[str, object]:
    return {
        "name": SAVE_PERSONA_TOOL,
        "title": "保存 Companion Kit Persona",
        "description": (
            "仅当用户明确在 Companion Kit 人物面板创建或调整 Persona 时使用。"
            "不要因为普通代码里出现 persona、角色或模板等词而调用。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "template_id": {
                    "type": ["string", "null"],
                    "enum": [
                        None,
                        "warm_healer",
                        "calm_partner",
                        "sunny_friend",
                        "playful_pal",
                    ],
                },
                "description": {"type": ["string", "null"], "maxLength": 600},
                "display_name": {"type": ["string", "null"], "maxLength": 40},
                "starting_mode": {
                    "type": "string",
                    "enum": ["natural", "familiar"],
                },
                "romance_enabled": {"type": "boolean"},
                "expected_version": {
                    "type": ["string", "null"],
                    "pattern": "^[a-f0-9]{64}$",
                },
                "confirm": {"type": "boolean", "const": True},
            },
            "required": ["expected_version", "confirm"],
            "additionalProperties": False,
        },
        "outputSchema": STATUS_OUTPUT_SCHEMA,
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "openWorldHint": False,
            "idempotentHint": False,
        },
        "_meta": {
            "ui": {"visibility": ["app"]},
            "openai/visibility": "private",
            "openai/toolInvocation/invoking": "正在保存 Persona",
            "openai/toolInvocation/invoked": "Persona 已保存",
        },
    }


def _set_primary_face_descriptor() -> dict[str, object]:
    return {
        "name": SET_PRIMARY_FACE_TOOL,
        "title": "确认 Companion Kit 人物主脸",
        "description": (
            "仅当用户在 Companion Kit 人物面板预览图片、确认有权使用且明确点击固定或更换主脸时使用。"
            "普通图片生成和代码任务不要调用。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "image_base64": {"type": "string", "maxLength": 16777216},
                "adult_authorized": {"type": "boolean", "const": True},
                "confirm": {"type": "boolean", "const": True},
            },
            "required": ["image_base64", "adult_authorized", "confirm"],
            "additionalProperties": False,
        },
        "outputSchema": STATUS_OUTPUT_SCHEMA,
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "openWorldHint": False,
            "idempotentHint": False,
        },
        "_meta": {
            "ui": {"visibility": ["app"]},
            "openai/visibility": "private",
            "openai/toolInvocation/invoking": "正在确认人物主脸",
            "openai/toolInvocation/invoked": "人物主脸已确认",
        },
    }


def _clear_scopes_descriptor() -> dict[str, object]:
    return {
        "name": CLEAR_SCOPES_TOOL,
        "title": "清空 Companion Kit 任务连接",
        "description": (
            "只供人物面板在用户明确确认后清空本机陪伴任务连接。"
            "不删除 Persona、关系数据、参考照片或聊天内容。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"confirm": {"type": "boolean", "const": True}},
            "required": ["confirm"],
            "additionalProperties": False,
        },
        "outputSchema": STATUS_OUTPUT_SCHEMA,
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "openWorldHint": False,
            "idempotentHint": True,
        },
        "_meta": {
            "ui": {"visibility": ["app"]},
            "openai/visibility": "private",
            "openai/toolInvocation/invoking": "正在清空任务连接",
            "openai/toolInvocation/invoked": "任务连接已清空",
        },
    }


def _status() -> dict[str, object]:
    return CodexCompanionStatusService(
        plugin_root=PLUGIN_ROOT,
        skill_root=SKILL_ROOT,
    ).snapshot()


def _private_ui_state() -> dict[str, object]:
    """只供本地组件回填表单；不放进模型可见的 structuredContent。"""

    from companion_kit.initializer import default_profile_path
    from companion_kit.profile_store import ProfileStore, ProfileStoreError

    try:
        snapshot = ProfileStore(
            skill_root=SKILL_ROOT,
            profile_path=default_profile_path("codex"),
        ).read()
    except ProfileStoreError:
        return {"persona_form": None, "profile_version": None}
    if snapshot is None:
        return {"persona_form": None, "profile_version": None}
    profile = snapshot.profile
    template_id = (
        profile.template_id
        if profile.template_id
        in {"warm_healer", "calm_partner", "sunny_friend", "playful_pal"}
        else None
    )
    return {
        "profile_version": snapshot.version,
        "persona_form": {
            "template_id": template_id,
            "description": profile.intent_summary,
            "display_name": profile.display_name,
            "starting_mode": profile.relationship.starting_mode,
            "romance_enabled": profile.relationship.romance_enabled,
        }
    }


def _result_meta(*, render: bool = False) -> dict[str, object]:
    result: dict[str, object] = {PRIVATE_UI_STATE_KEY: _private_ui_state()}
    if render:
        result["ui"] = {"resourceUri": WIDGET_URI}
        result["openai/outputTemplate"] = WIDGET_URI
    return result


def _save_persona(arguments: dict[str, object]) -> dict[str, object]:
    allowed = {
        "template_id",
        "description",
        "display_name",
        "starting_mode",
        "romance_enabled",
        "expected_version",
        "confirm",
    }
    if set(arguments) - allowed or arguments.get("confirm") is not True:
        raise ValueError("保存 Persona 需要在人物面板明确确认")
    template_id = arguments.get("template_id")
    description = arguments.get("description")
    display_name = arguments.get("display_name")
    starting_mode = arguments.get("starting_mode")
    romance_enabled = arguments.get("romance_enabled")
    expected_version = arguments.get("expected_version")
    if template_id is not None and template_id not in {
        "warm_healer",
        "calm_partner",
        "sunny_friend",
        "playful_pal",
    }:
        raise ValueError("Persona 模板无效")
    if description is not None and (
        not isinstance(description, str) or len(description) > 600
    ):
        raise ValueError("Persona 描述无效")
    if display_name is not None and (
        not isinstance(display_name, str) or len(display_name) > 40
    ):
        raise ValueError("Persona 称呼无效")
    if starting_mode is not None and starting_mode not in {"natural", "familiar"}:
        raise ValueError("相处起点无效")
    if romance_enabled is not None and not isinstance(romance_enabled, bool):
        raise ValueError("关系方向无效")
    if expected_version is not None and (
        not isinstance(expected_version, str)
        or not _PROFILE_VERSION_RE.fullmatch(expected_version)
    ):
        raise ValueError("Persona 版本无效，请刷新人物面板")

    from companion_kit.initializer import default_profile_path
    from companion_kit.profile_store import (
        ProfileConflict,
        ProfileStore,
        ProfileStoreError,
    )

    store = ProfileStore(skill_root=SKILL_ROOT, profile_path=default_profile_path("codex"))
    try:
        current = store.read()
        if template_id is None and not str(description or "").strip():
            if current is None:
                raise ValueError("请选一个模板，或用一句话描述想认识的人")
            store.save(
                template_id=current.profile.id,
                display_name=display_name,
                expected_version=expected_version,
                starting_mode=starting_mode,
                romance_enabled=romance_enabled,
            )
        else:
            store.save_draft(
                expected_version=expected_version,
                template_id=template_id,
                description=description,
                display_name=display_name,
                starting_mode=starting_mode,
                romance_enabled=romance_enabled,
            )
    except ProfileConflict as exc:
        raise ValueError(
            "Persona 已在其他位置更新；请刷新人物面板后再保存，现有配置没有被覆盖"
        ) from exc
    except ProfileStoreError as exc:
        raise ValueError("Persona 无法安全保存，原来的配置没有被覆盖") from exc
    return _status()


def _set_primary_face(arguments: dict[str, object]) -> dict[str, object]:
    if set(arguments) != {"image_base64", "adult_authorized", "confirm"}:
        raise ValueError("人物主脸确认参数无效")
    if arguments.get("adult_authorized") is not True or arguments.get("confirm") is not True:
        raise ValueError("请先确认图片权利与成年形象，再固定人物主脸")
    encoded = arguments.get("image_base64")
    if not isinstance(encoded, str) or not encoded or len(encoded) > 16_777_216:
        raise ValueError("人物参考图无效或过大")
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("人物参考图不是有效的 PNG 数据") from exc
    if not image_bytes or len(image_bytes) > 12 * 1024 * 1024:
        raise ValueError("人物参考图无效或过大")

    from companion_kit.backup import CompanionDataLayout
    from companion_kit.identity_pack import PRIMARY_FACE
    from companion_kit.identity_workflow import confirm_primary_candidate
    from companion_kit.image_assets import ImageAssetError, ImageAssetStore
    from companion_kit.initializer import default_profile_path
    from companion_kit.profile_store import ProfileStore, ProfileStoreError
    import secrets

    profile_path = default_profile_path("codex")
    store = ProfileStore(skill_root=SKILL_ROOT, profile_path=profile_path)
    try:
        current = store.read()
        if current is None:
            raise ValueError("请先保存 Persona，再固定人物主脸")
        layout = CompanionDataLayout.for_profile(profile_path)
        assets = ImageAssetStore(layout.images_root)
        identity_version = current.profile.visual.identity_version
        if current.profile.visual.is_locked:
            identity_version += 1
        task_scope = "plugin-ui:" + secrets.token_urlsafe(18)
        candidate = assets.store_candidate(
            profile_id=current.profile.id,
            identity_version=identity_version,
            image_bytes=image_bytes,
            task_scope=task_scope,
            source="user_upload",
            role=PRIMARY_FACE,
        )
        confirm_primary_candidate(
            assets=assets,
            profile_store=store,
            candidate_id=candidate.candidate_id,
            task_scope=task_scope,
            expected_profile_version=current.version,
        )
    except (ImageAssetError, ProfileStoreError, OSError) as exc:
        raise ValueError("人物参考图无法安全确认，原来的形象没有被覆盖") from exc
    return _status()


def _clear_scopes(arguments: dict[str, object]) -> dict[str, object]:
    if set(arguments) != {"confirm"} or arguments.get("confirm") is not True:
        raise ValueError("清空陪伴任务连接需要在人物面板明确确认")
    from companion_kit.backup import CompanionDataLayout
    from companion_kit.companion_scope import CompanionScopeError, CompanionScopeStore
    from companion_kit.initializer import default_profile_path

    layout = CompanionDataLayout.for_profile(default_profile_path("codex"))
    try:
        CompanionScopeStore(
            root=layout.system_root / "codex-scopes",
            lock_timeout=0.25,
        ).clear_all(confirm=True)
    except CompanionScopeError as exc:
        raise ValueError("任务连接无法安全清空；Persona 和参考照片没有变化") from exc
    return _status()


def _result_for(method: str, params: object) -> dict[str, object]:
    arguments = params if isinstance(params, dict) else {}
    if method == "initialize":
        requested = arguments.get("protocolVersion")
        protocol = (
            requested
            if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS
            else LATEST_PROTOCOL_VERSION
        )
        return {
            "protocolVersion": protocol,
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {"name": "companion-kit", "version": __version__},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {
            "tools": [
                _tool_descriptor(),
                _save_persona_descriptor(),
                _set_primary_face_descriptor(),
                _clear_scopes_descriptor(),
            ]
        }
    if method == "resources/list":
        return {
            "resources": [
                {
                    "uri": WIDGET_URI,
                    "name": "Companion Kit 人物面板",
                    "description": "在 Codex 内查看 Persona、固定形象和陪伴任务状态。",
                    "mimeType": MCP_APP_MIME,
                }
            ]
        }
    if method == "resources/templates/list":
        return {"resourceTemplates": []}
    if method == "resources/read":
        if arguments.get("uri") != WIDGET_URI:
            raise ValueError("未知的 Plugin UI 资源")
        return {
            "contents": [
                {
                    "uri": WIDGET_URI,
                    "mimeType": MCP_APP_MIME,
                    "text": _widget_html(),
                    "_meta": {
                        "ui": {
                            "prefersBorder": True,
                            "csp": {"connectDomains": [], "resourceDomains": []},
                        },
                        "openai/widgetDescription": (
                            "Companion Kit 的 Codex 内人物面板，只展示最小运行状态和明确操作。"
                        ),
                    },
                }
            ]
        }
    if method == "tools/call":
        name = arguments.get("name")
        tool_arguments = arguments.get("arguments", {})
        if not isinstance(tool_arguments, dict):
            raise ValueError("Companion Kit 工具参数无效")
        if name == SAVE_PERSONA_TOOL:
            status = _save_persona(tool_arguments)
            return {
                "content": [{"type": "text", "text": "Persona 已保存。"}],
                "structuredContent": status,
                "_meta": _result_meta(),
            }
        if name == SET_PRIMARY_FACE_TOOL:
            status = _set_primary_face(tool_arguments)
            return {
                "content": [{"type": "text", "text": "人物主脸已确认。"}],
                "structuredContent": status,
                "_meta": _result_meta(),
            }
        if name == CLEAR_SCOPES_TOOL:
            status = _clear_scopes(tool_arguments)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "陪伴任务连接已清空；Persona、关系和参考照片仍在。",
                    }
                ],
                "structuredContent": status,
                "_meta": _result_meta(),
            }
        if name != TOOL_NAME or tool_arguments:
            raise ValueError("不支持的 Companion Kit 工具调用")
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Companion Kit 人物面板已打开。",
                }
            ],
            "structuredContent": _status(),
            "_meta": _result_meta(render=True),
        }
    raise LookupError("未知的 MCP 方法")


def _response(request: object) -> dict[str, object] | None:
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return {
            "jsonrpc": "2.0",
            "id": request.get("id") if isinstance(request, dict) else None,
            "error": {"code": -32600, "message": "Invalid Request"},
        }
    request_id = request.get("id")
    method = request.get("method")
    if not isinstance(method, str):
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32600, "message": "Invalid Request"},
        }
    if request_id is None:
        return None
    try:
        result = _result_for(method, request.get("params"))
    except ValueError as exc:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32602, "message": str(exc)},
        }
    except LookupError:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "Method not found"},
        }
    except Exception:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32603, "message": "Companion Kit 状态暂时不可用"},
        }
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request: Any = json.loads(line)
        except json.JSONDecodeError:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
        else:
            response = _response(request)
        if response is not None:
            sys.stdout.write(
                json.dumps(
                    response,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
