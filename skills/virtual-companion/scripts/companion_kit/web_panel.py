from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
from threading import Lock
import time
from typing import Mapping
from urllib.parse import urlsplit
import webbrowser

from . import __version__
from .backup import BackupError, BackupManager, CompanionDataLayout
from .companion_scope import CompanionScopeError, CompanionScopeStore
from .companion_status import build_runtime_readiness, companion_context_loaded
from .codex_upgrade import CodexUpgradeExecutor
from .host_install import HostInstaller
from .hook_health import HookHealthError, HookHealthStore, version_base
from .identity_pack import PRIMARY_FACE
from .identity_workflow import (
    IdentityConfirmationRetry,
    confirm_primary_candidate,
)
from .image_assets import ImageAssetError, ImageAssetStore
from .initializer import default_profile_path
from .installer import InstallError
from .profile_store import ProfileConflict, ProfileStore, ProfileStoreError
from .public_bundle import contains_absolute_path
from .upgrade import CodexUpgradePlanner, UpgradeError


_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
_PROFILE_KEYS = {
    "template_id",
    "description",
    "display_name",
    "overrides",
    "expected_version",
    "starting_mode",
    "romance_enabled",
}
_DRAFT_KEYS = {"template_id", "description", "display_name", "overrides"}
_INSTALL_KEYS = {"host", "confirm"}
_BACKUP_KEYS = {"confirm"}
_UPGRADE_APPLY_KEYS = {"confirm"}
_IDENTITY_CONFIRM_KEYS = {"candidate_id", "profile_version", "confirm"}
_HOOK_REVIEW_KEYS = {"confirm"}
_MAX_BODY_BYTES = 24 * 1024
_MAX_IMAGE_BODY_BYTES = 12 * 1024 * 1024


def _safe_error(exc: Exception, fallback: str) -> str:
    message = str(exc).strip()
    if not message or contains_absolute_path(message):
        return fallback
    return message


def _private_root_for_profile(profile_path: Path) -> Path:
    parent = profile_path.parent
    owner_root = parent.parent if parent.name == "profiles" else parent
    return owner_root / "private"


class CompanionPanelServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        stores: Mapping[str, ProfileStore],
        installer: HostInstaller,
        backup_manager: BackupManager,
        upgrade_planner: CodexUpgradePlanner,
        upgrade_executor: CodexUpgradeExecutor,
        hook_health_store: HookHealthStore,
        companion_scope_store: CompanionScopeStore,
        nonce: str,
    ) -> None:
        self.stores = dict(stores)
        self.store = self.stores["codex"]
        self.installer = installer
        self.backup_manager = backup_manager
        self.upgrade_planner = upgrade_planner
        self.upgrade_executor = upgrade_executor
        self.hook_health_store = hook_health_store
        self.companion_scope_store = companion_scope_store
        self._plugin_status_lock = Lock()
        self._plugin_status_cache: tuple[float, tuple[object, ...], dict[str, object]] | None = None
        self.panel_nonce = nonce
        super().__init__(server_address, CompanionPanelHandler)

    def cached_plugin_status(
        self,
        key: tuple[object, ...],
    ) -> dict[str, object] | None:
        with self._plugin_status_lock:
            cached = self._plugin_status_cache
            if cached is None or cached[1] != key or time.monotonic() - cached[0] > 20:
                return None
            return dict(cached[2])

    def remember_plugin_status(
        self,
        key: tuple[object, ...],
        status: dict[str, object],
    ) -> None:
        with self._plugin_status_lock:
            self._plugin_status_cache = (time.monotonic(), key, dict(status))

    def invalidate_plugin_status(self) -> None:
        with self._plugin_status_lock:
            self._plugin_status_cache = None

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"

    @property
    def panel_url(self) -> str:
        return f"{self.origin}/#token={self.panel_nonce}"


class CompanionPanelHandler(BaseHTTPRequestHandler):
    server: CompanionPanelServer
    protocol_version = "HTTP/1.1"
    server_version = "CompanionPanel"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:
        return

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
            "style-src 'self'; script-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")

    def _send_bytes(
        self,
        status: int,
        payload: bytes,
        content_type: str,
    ) -> None:
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, encoded, "application/json; charset=utf-8")

    def _expected_host(self) -> str:
        return f"127.0.0.1:{self.server.server_port}"

    def _host_allowed(self) -> bool:
        return self.headers.get("Host", "") == self._expected_host()

    def _authenticated(self) -> bool:
        received = self.headers.get("X-Companion-Token", "")
        return bool(received) and secrets.compare_digest(
            received,
            self.server.panel_nonce,
        )

    def _origin_allowed(self) -> bool:
        return self.headers.get("Origin", "") == self.server.origin

    def _read_json(self) -> dict[str, object] | None:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
        if content_type != "application/json":
            self._send_json(415, {"error": "只接受 JSON 请求"})
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "请求长度无效"})
            return None
        if length <= 0 or length > _MAX_BODY_BYTES:
            self._send_json(413, {"error": "请求内容过大或为空"})
            return None
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "JSON 格式无效"})
            return None
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "请求内容必须是对象"})
            return None
        return payload

    def _read_png(self) -> bytes | None:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
        if content_type != "image/png":
            self.close_connection = True
            self._send_json(415, {"error": "参考图需要转换为 PNG 后上传"})
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.close_connection = True
            self._send_json(400, {"error": "图片长度无效"})
            return None
        if length <= 0 or length > _MAX_IMAGE_BODY_BYTES:
            self.close_connection = True
            self._send_json(413, {"error": "参考图过大或为空"})
            return None
        return self.rfile.read(length)

    def _identity_assets(self) -> ImageAssetStore:
        image_root = _private_root_for_profile(self.server.store.profile_path) / "images"
        return ImageAssetStore(image_root)

    def _identity_scope(self) -> str:
        return f"web-panel:{self.server.panel_nonce}"

    def _plugin_status(self, plan) -> dict[str, object]:
        key = (
            getattr(plan, "method", None),
            getattr(plan, "existing", None),
            __version__,
        )
        cached = self.server.cached_plugin_status(key)
        if cached is not None:
            return cached
        if plan is None:
            status = {
                "known": False,
                "installed": False,
                "enabled": False,
                "installed_version": None,
                "current": False,
            }
            self.server.remember_plugin_status(key, status)
            return status
        if plan.method != "codex_plugin":
            installed = plan.existing is True
            status = {
                "known": True,
                "installed": installed,
                "enabled": installed,
                "installed_version": __version__ if installed else None,
                "current": installed,
            }
            self.server.remember_plugin_status(key, status)
            return status
        try:
            check = self.server.upgrade_planner.check()
        except (UpgradeError, BackupError):
            status = {
                "known": False,
                "installed": False,
                "enabled": False,
                "installed_version": None,
                "current": False,
            }
            self.server.remember_plugin_status(key, status)
            return status
        installed_version = check.installed_version
        status = {
            "known": True,
            "installed": check.plugin_installed,
            "enabled": check.plugin_enabled,
            "installed_version": installed_version,
            "current": bool(
                check.plugin_installed
                and installed_version
                and version_base(installed_version) == version_base(__version__)
            ),
        }
        self.server.remember_plugin_status(key, status)
        return status

    @staticmethod
    def _session_loaded(
        hook_health: dict[str, object],
        *,
        material_modified_at: float | None,
    ) -> bool:
        return companion_context_loaded(
            hook_health,
            material_modified_at=material_modified_at,
        )

    def _runtime_readiness(
        self,
        *,
        plugin: dict[str, object],
        profile: dict[str, object] | None,
        identity_pack: dict[str, object],
        hook_health: dict[str, object],
        material_modified_at: float | None,
        companion_task_count: int,
    ) -> dict[str, object]:
        return build_runtime_readiness(
            plugin=plugin,
            persona_configured=profile is not None,
            identity_pack=identity_pack,
            hook_health=hook_health,
            material_modified_at=material_modified_at,
            companion_task_count=companion_task_count,
        )

    def _state_payload(self) -> dict[str, object]:
        snapshot = None
        try:
            snapshot = self.server.store.read()
            profile = snapshot.to_dict() if snapshot else None
            profile_error = None
        except ProfileStoreError as exc:
            profile = None
            profile_error = _safe_error(exc, "本地 Persona 无法安全读取")

        plan = None
        try:
            plan = self.server.installer.plan("codex")
            host = plan.to_dict()
        except InstallError as exc:
            host = {
                "host": "codex",
                "name": "Codex",
                "available": False,
                "existing": None,
                "error": _safe_error(exc, "Codex 安装方案无法安全生成"),
            }
        plugin_status = self._plugin_status(plan)
        host["existing"] = (
            plugin_status["installed"] if plugin_status["known"] else None
        )
        hosts = [host]

        identity_pack: dict[str, object]
        material_modified_at: float | None = None
        if snapshot is not None:
            try:
                material_modified_at = self.server.store.profile_path.stat().st_mtime
            except OSError:
                material_modified_at = None
        if not profile or profile["visual"]["reference_count"] != 1:
            identity_pack = {
                "level": "unset",
                "ready": False,
                "roles": [],
                "count": 0,
            }
        else:
            image_root = _private_root_for_profile(self.server.store.profile_path) / "images"
            if not image_root.is_dir() or snapshot is None:
                identity_pack = {
                    "level": "unavailable",
                    "ready": False,
                    "roles": [],
                    "count": 0,
                }
            else:
                try:
                    resolved_pack = ImageAssetStore(image_root).resolve_identity_pack(
                        primary_reference_id=snapshot.profile.visual.reference_ids[0],
                        profile_id=snapshot.profile.id,
                        identity_version=snapshot.profile.visual.identity_version,
                    )
                except (ImageAssetError, OSError):
                    identity_pack = {
                        "level": "unavailable",
                        "ready": False,
                        "roles": [],
                        "count": 0,
                    }
                else:
                    member_modified_at = []
                    for member in resolved_pack.members:
                        try:
                            member_modified_at.append(member.path.stat().st_mtime)
                        except OSError:
                            member_modified_at = []
                            break
                    if member_modified_at:
                        material_modified_at = max(
                            [material_modified_at or 0.0, *member_modified_at]
                        )
                    identity_pack = {
                        "level": "enhanced" if len(resolved_pack.members) > 1 else "basic",
                        "ready": True,
                        "roles": list(resolved_pack.roles),
                        "count": len(resolved_pack.members),
                    }

        hook_health = self.server.hook_health_store.snapshot()
        try:
            companion_task_count = self.server.companion_scope_store.count()
            companion_scope_error = None
        except CompanionScopeError:
            companion_task_count = 0
            companion_scope_error = "陪伴任务绑定暂时无法安全读取"
        runtime_readiness = self._runtime_readiness(
            plugin=plugin_status,
            profile=profile,
            identity_pack=identity_pack,
            hook_health=hook_health,
            material_modified_at=material_modified_at,
            companion_task_count=companion_task_count,
        )

        if identity_pack.get("ready") is True:
            identity_status = (
                "主脸参考已就绪"
                if runtime_readiness["session_loaded"]
                else "参考图已保存，陪伴任务尚未加载当前资料"
            )
        elif bool(profile and profile["visual"]["reference_count"] == 1):
            identity_status = "参考图已保存，但当前无法安全读取"
        else:
            identity_status = "尚未确认主脸"

        photo_modes = {
            "codex_native": {
                "title": "Codex 内置生图",
                "status": "无需单独配置",
                "description": "人物候选和日常照片都直接使用 Codex 内置 gpt-image-2，计入现有方案用量。",
            },
            "identity_reuse": {
                "title": "固定形象复用",
                "status": identity_status,
                "description": "主脸只在你明确确认后固定；侧脸和体型可以按需补充，新任务会按场景选择一到两张参考，不需要 API Key。",
            },
            "identity_pack": identity_pack,
            "reference_configured": bool(
                profile and profile["visual"]["reference_count"] == 1
            ),
        }

        return {
            "version": __version__,
            "phase": "本轮只优化 Codex Persona 创建、聊天与内置生图体验；其他宿主配置保持独立。",
            "templates": [
                template.to_dict() for template in self.server.store.templates()
            ],
            "profile": profile,
            "profile_error": profile_error,
            "photo_modes": photo_modes,
            "hook_health": hook_health,
            "companion_scope": {
                "active_task_count": companion_task_count,
                "error": companion_scope_error,
            },
            "runtime_readiness": runtime_readiness,
            "hosts": hosts,
        }

    def do_GET(self) -> None:
        if not self._host_allowed():
            self._send_json(403, {"error": "请求来源无效"})
            return
        path = urlsplit(self.path).path
        if path == "/api/state":
            if not self._authenticated():
                self._send_json(401, {"error": "面板授权已失效，请重新启动"})
                return
            try:
                self._send_json(200, self._state_payload())
            except ProfileStoreError as exc:
                self._send_json(
                    500,
                    {"error": _safe_error(exc, "面板无法读取内置模板")},
                )
            return

        if path == "/api/hook-health":
            if not self._authenticated():
                self._send_json(401, {"error": "面板授权已失效，请重新启动"})
                return
            payload = self._state_payload()
            self._send_json(
                200,
                {
                    "hook_health": payload["hook_health"],
                    "runtime_readiness": payload["runtime_readiness"],
                },
            )
            return

        if path == "/api/backups":
            if not self._authenticated():
                self._send_json(401, {"error": "面板授权已失效，请重新启动"})
                return
            try:
                backups = [
                    snapshot.to_dict()
                    for snapshot in self.server.backup_manager.list()
                ]
            except BackupError as exc:
                self._send_json(
                    409,
                    {"error": _safe_error(exc, "恢复点暂时无法安全读取")},
                )
                return
            self._send_json(200, {"backups": backups})
            return

        if path == "/api/upgrade/check":
            if not self._authenticated():
                self._send_json(401, {"error": "面板授权已失效，请重新启动"})
                return
            try:
                check = self.server.upgrade_planner.check()
            except UpgradeError as exc:
                self._send_json(
                    409,
                    {"error": _safe_error(exc, "暂时无法安全检查更新")},
                )
                return
            self._send_json(200, {"upgrade": check.to_dict()})
            return

        if path == "/api/identity/primary":
            if not self._authenticated():
                self._send_json(401, {"error": "面板授权已失效，请重新启动"})
                return
            self._send_primary_identity()
            return

        if path == "/favicon.ico":
            self._send_bytes(204, b"", "image/x-icon")
            return

        asset = _ASSETS.get(path)
        if asset is None:
            self._send_json(404, {"error": "页面不存在"})
            return
        filename, content_type = asset
        asset_path = Path(__file__).with_name("web_assets") / filename
        try:
            payload = asset_path.read_bytes()
        except OSError:
            self._send_json(500, {"error": "面板资源不完整，请重新安装"})
            return
        self._send_bytes(200, payload, content_type)

    def do_POST(self) -> None:
        if not self._host_allowed():
            self._send_json(403, {"error": "请求来源无效"})
            return
        if not self._authenticated():
            self._send_json(401, {"error": "面板授权已失效，请重新启动"})
            return
        if not self._origin_allowed():
            self._send_json(403, {"error": "写入请求来源无效"})
            return

        path = urlsplit(self.path).path
        if path == "/api/identity/candidate":
            self._stage_identity_candidate()
            return
        payload = self._read_json()
        if payload is None:
            return
        if path == "/api/profile":
            self._save_profile(payload)
            return
        if path == "/api/persona/draft":
            self._preview_draft(payload)
            return
        if path == "/api/install":
            self._install_host(payload)
            return
        if path == "/api/hooks/reviewed":
            self._acknowledge_hooks_reviewed(payload)
            return
        if path == "/api/backups":
            self._create_backup(payload)
            return
        if path == "/api/upgrade/apply":
            self._apply_upgrade(payload)
            return
        if path == "/api/identity/confirm":
            self._confirm_identity(payload)
            return
        self._send_json(404, {"error": "接口不存在"})

    def _create_backup(self, payload: dict[str, object]) -> None:
        if set(payload) - _BACKUP_KEYS:
            self._send_json(400, {"error": "恢复点请求包含不允许的字段"})
            return
        if payload.get("confirm") is not True:
            self._send_json(409, {"error": "请先确认创建本地恢复点"})
            return
        try:
            snapshot = self.server.backup_manager.create()
        except BackupError as exc:
            self._send_json(
                409,
                {"error": _safe_error(exc, "恢复点创建失败，现有数据没有改动")},
            )
            return
        self._send_json(201, {"backup": snapshot.to_dict()})

    def _apply_upgrade(self, payload: dict[str, object]) -> None:
        if set(payload) - _UPGRADE_APPLY_KEYS:
            self._send_json(400, {"error": "升级请求包含不允许的字段"})
            return
        if payload.get("confirm") is not True:
            self._send_json(409, {"error": "请先单独确认替换 Codex Plugin"})
            return
        try:
            result = self.server.upgrade_executor.apply(confirm=True)
        except UpgradeError as exc:
            self._send_json(
                409,
                {"error": _safe_error(exc, "升级没有完成，现有用户数据没有被覆盖")},
            )
            return
        self.server.invalidate_plugin_status()
        self._send_json(200, {"upgrade": result.to_dict()})

    def _stage_identity_candidate(self) -> None:
        try:
            snapshot = self.server.store.read()
        except ProfileStoreError as exc:
            self._send_json(
                400,
                {"error": _safe_error(exc, "本地 Persona 无法安全读取")},
            )
            return
        if snapshot is None:
            self.close_connection = True
            self._send_json(409, {"error": "请先保存 Persona，再上传人物参考图"})
            return
        if self.headers.get("X-Companion-Image-Consent", "") != "adult-authorized":
            self.close_connection = True
            self._send_json(
                409,
                {"error": "请先确认图片是成年虚构形象，或你有权使用的成年人物参考"},
            )
            return
        image_bytes = self._read_png()
        if image_bytes is None:
            return
        identity_version = snapshot.profile.visual.identity_version
        replacing = snapshot.profile.visual.is_locked
        if replacing:
            identity_version += 1
        try:
            candidate = self._identity_assets().store_candidate(
                profile_id=snapshot.profile.id,
                identity_version=identity_version,
                image_bytes=image_bytes,
                task_scope=self._identity_scope(),
                source="user_upload",
                role=PRIMARY_FACE,
            )
        except (ImageAssetError, OSError) as exc:
            self._send_json(
                400,
                {"error": _safe_error(exc, "参考图无法安全保存")},
            )
            return
        self._send_json(
            201,
            {
                "candidate": {
                    "candidate_id": candidate.candidate_id,
                    "profile_version": snapshot.version,
                    "identity_version": identity_version,
                    "operation": "replace_primary" if replacing else "set_primary",
                    "width": candidate.width,
                    "height": candidate.height,
                    "status": "pending",
                }
            },
        )

    def _confirm_identity(self, payload: dict[str, object]) -> None:
        if set(payload) - _IDENTITY_CONFIRM_KEYS:
            self._send_json(400, {"error": "形象确认请求包含不允许的字段"})
            return
        candidate_id = payload.get("candidate_id")
        profile_version = payload.get("profile_version")
        if not isinstance(candidate_id, str) or not candidate_id:
            self._send_json(400, {"error": "候选形象编号无效"})
            return
        if not isinstance(profile_version, str) or not profile_version:
            self._send_json(400, {"error": "Persona 版本无效"})
            return
        if payload.get("confirm") is not True:
            self._send_json(409, {"error": "只有明确确认后才会固定人物主脸"})
            return
        try:
            reference, bound = confirm_primary_candidate(
                assets=self._identity_assets(),
                profile_store=self.server.store,
                candidate_id=candidate_id,
                task_scope=self._identity_scope(),
                expected_profile_version=profile_version,
            )
        except IdentityConfirmationRetry as exc:
            self._send_json(
                409,
                {
                    "error": _safe_error(exc, "Persona 已变化，请重新确认候选"),
                    "retry_profile_version": exc.retry_profile_version,
                },
            )
            return
        except (ImageAssetError, ProfileStoreError, OSError) as exc:
            self._send_json(
                409,
                {"error": _safe_error(exc, "人物主脸无法安全确认")},
            )
            return
        self._send_json(
            200,
            {
                "profile": bound.to_dict(),
                "identity": {
                    "status": "locked",
                    "role": reference.role,
                    "level": "basic",
                },
            },
        )

    def _send_primary_identity(self) -> None:
        try:
            snapshot = self.server.store.read()
            if snapshot is None or not snapshot.profile.visual.is_locked:
                self._send_json(404, {"error": "人物主脸尚未确认"})
                return
            pack = self._identity_assets().resolve_identity_pack(
                primary_reference_id=snapshot.profile.visual.reference_ids[0],
                profile_id=snapshot.profile.id,
                identity_version=snapshot.profile.visual.identity_version,
            )
            primary = pack.member(PRIMARY_FACE)
            if primary is None:
                raise ImageAssetError("身份参考包缺少主脸")
            payload = primary.path.read_bytes()
        except (ImageAssetError, ProfileStoreError, OSError) as exc:
            self._send_json(
                404,
                {"error": _safe_error(exc, "人物主脸暂时不可用")},
            )
            return
        self._send_bytes(200, payload, "image/png")

    def _save_profile(self, payload: dict[str, object]) -> None:
        if set(payload) - _PROFILE_KEYS:
            self._send_json(400, {"error": "配置请求包含不允许的字段"})
            return
        template_id = payload.get("template_id")
        description = payload.get("description")
        display_name = payload.get("display_name")
        overrides = payload.get("overrides")
        expected_version = payload.get("expected_version")
        starting_mode = payload.get("starting_mode")
        romance_enabled = payload.get("romance_enabled")
        if template_id is not None and not isinstance(template_id, str):
            self._send_json(400, {"error": "模板编号无效"})
            return
        if description is not None and not isinstance(description, str):
            self._send_json(400, {"error": "人物描述必须是文字"})
            return
        if template_id is None and not str(description or "").strip():
            self._send_json(400, {"error": "请选择一个起点，或用一句话描述 TA"})
            return
        if display_name is not None and not isinstance(display_name, str):
            self._send_json(400, {"error": "称呼必须是文字"})
            return
        if overrides is not None and not isinstance(overrides, dict):
            self._send_json(400, {"error": "调整内容格式无效"})
            return
        if expected_version is not None and not isinstance(expected_version, str):
            self._send_json(400, {"error": "配置版本无效"})
            return
        if starting_mode is not None and starting_mode not in {"natural", "familiar"}:
            self._send_json(400, {"error": "相处起点无效"})
            return
        if romance_enabled is not None and not isinstance(romance_enabled, bool):
            self._send_json(400, {"error": "关系方向设置无效"})
            return

        try:
            store = self.server.store
            existed = store.read() is not None
            snapshot = store.save_draft(
                template_id=template_id,
                description=description,
                display_name=display_name,
                overrides=overrides,
                expected_version=expected_version,
                starting_mode=starting_mode,
                romance_enabled=romance_enabled,
            )
        except ProfileConflict as exc:
            self._send_json(409, {"error": str(exc)})
            return
        except ProfileStoreError as exc:
            self._send_json(
                400,
                {"error": _safe_error(exc, "本地人格配置无法安全保存")},
            )
            return
        self._send_json(
            200 if existed else 201,
            {"profile": snapshot.to_dict()},
        )

    def _preview_draft(self, payload: dict[str, object]) -> None:
        if set(payload) - _DRAFT_KEYS:
            self._send_json(400, {"error": "预览请求包含不允许的字段"})
            return
        template_id = payload.get("template_id")
        description = payload.get("description")
        display_name = payload.get("display_name")
        overrides = payload.get("overrides")
        if template_id is not None and not isinstance(template_id, str):
            self._send_json(400, {"error": "模板编号无效"})
            return
        if description is not None and not isinstance(description, str):
            self._send_json(400, {"error": "人物描述必须是文字"})
            return
        if template_id is None and not str(description or "").strip():
            self._send_json(400, {"error": "请选择一个起点，或用一句话描述 TA"})
            return
        if display_name is not None and not isinstance(display_name, str):
            self._send_json(400, {"error": "称呼必须是文字"})
            return
        if overrides is not None and not isinstance(overrides, dict):
            self._send_json(400, {"error": "调整内容格式无效"})
            return
        try:
            draft = self.server.store.preview_draft(
                template_id=template_id,
                description=description,
                display_name=display_name,
                overrides=overrides,
            )
        except ProfileStoreError as exc:
            self._send_json(
                400,
                {"error": _safe_error(exc, "无法安全补全 Persona 草稿")},
            )
            return
        self._send_json(200, {"draft": draft.to_dict()})

    def _acknowledge_hooks_reviewed(self, payload: dict[str, object]) -> None:
        if set(payload) - _HOOK_REVIEW_KEYS:
            self._send_json(400, {"error": "Hook 审核确认包含不允许的字段"})
            return
        if payload.get("confirm") is not True:
            self._send_json(409, {"error": "请先在 Codex 的 /hooks 中完成真实审核"})
            return
        before = self._state_payload()
        readiness = before["runtime_readiness"]
        if not isinstance(readiness, dict) or not all(
            readiness.get(key) is True
            for key in ("plugin_installed", "plugin_enabled", "plugin_current")
        ):
            self._send_json(409, {"error": "请先安装或更新并启用 Companion Kit Plugin"})
            return
        try:
            self.server.hook_health_store.acknowledge_review()
        except HookHealthError as exc:
            self._send_json(
                409,
                {"error": _safe_error(exc, "Hook 审核进度无法安全保存")},
            )
            return
        after = self._state_payload()
        self._send_json(
            200,
            {
                "hook_health": after["hook_health"],
                "runtime_readiness": after["runtime_readiness"],
            },
        )

    def _install_host(self, payload: dict[str, object]) -> None:
        if set(payload) - _INSTALL_KEYS:
            self._send_json(400, {"error": "安装请求包含不允许的字段"})
            return
        host = payload.get("host")
        if host != "codex":
            self._send_json(400, {"error": "当前面板只安装 Codex"})
            return
        if payload.get("confirm") is not True:
            self._send_json(409, {"error": "请先查看安装方案并单独确认"})
            return
        try:
            result = self.server.installer.install(host)
        except InstallError as exc:
            self._send_json(
                409,
                {"error": _safe_error(exc, "宿主安装失败，请检查本机环境")},
            )
            return
        self.server.invalidate_plugin_status()
        self._send_json(200, {"install": result.to_dict()})


def create_panel_server(
    *,
    skill_root: str | Path,
    profile_path: str | Path | None = None,
    install_roots: Mapping[str, str | Path] | None = None,
    nonce: str | None = None,
    port: int = 0,
) -> CompanionPanelServer:
    if not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("端口必须在 0–65535 之间")
    token = nonce or secrets.token_urlsafe(32)
    if len(token) < 16:
        raise ValueError("面板授权令牌长度不足")
    root = Path(skill_root).resolve()
    codex_path = (
        default_profile_path("codex")
        if profile_path is None
        else Path(os.path.abspath(Path(profile_path).expanduser()))
    )
    layout = CompanionDataLayout.for_profile(codex_path)
    backup_manager = BackupManager(layout, product_version=__version__)
    upgrade_planner = CodexUpgradePlanner(
        plugin_root=root.parents[1],
        layout=layout,
    )
    return CompanionPanelServer(
        ("127.0.0.1", port),
        stores={"codex": ProfileStore(skill_root=root, profile_path=codex_path)},
        installer=HostInstaller(skill_root=root, target_roots=install_roots),
        backup_manager=backup_manager,
        upgrade_planner=upgrade_planner,
        upgrade_executor=CodexUpgradeExecutor(
            planner=upgrade_planner,
            backups=backup_manager,
        ),
        hook_health_store=HookHealthStore(
            root=layout.system_root / "hook-health",
            plugin_root=root.parents[1],
        ),
        companion_scope_store=CompanionScopeStore(
            root=layout.system_root / "codex-scopes",
        ),
        nonce=token,
    )


def run_panel(
    *,
    skill_root: str | Path,
    port: int = 0,
    open_browser: bool = True,
) -> int:
    server = create_panel_server(skill_root=skill_root, port=port)
    print("Companion Kit 本地面板已启动：")
    print(server.panel_url)
    print("仅本机可访问；按 Ctrl+C 关闭。")
    if open_browser:
        webbrowser.open(server.panel_url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n面板已关闭。")
    finally:
        server.server_close()
    return 0
