from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
from typing import Mapping
from urllib.parse import urlsplit
import webbrowser

from .host_install import HostInstaller
from .initializer import default_profile_path
from .installer import InstallError
from .profile_store import ProfileConflict, ProfileStore, ProfileStoreError
from .public_bundle import contains_absolute_path


_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
_PROFILE_KEYS = {
    "host",
    "template_id",
    "display_name",
    "expected_version",
    "starting_mode",
    "romance_enabled",
}
_INSTALL_KEYS = {"host", "confirm"}
_HOSTS = ("openclaw", "hermes", "codex", "claude")
_MAX_BODY_BYTES = 8 * 1024


def _safe_error(exc: Exception, fallback: str) -> str:
    message = str(exc).strip()
    if not message or contains_absolute_path(message):
        return fallback
    return message


class CompanionPanelServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        stores: Mapping[str, ProfileStore],
        installer: HostInstaller,
        nonce: str,
    ) -> None:
        self.stores = dict(stores)
        self.store = self.stores["codex"]
        self.installer = installer
        self.panel_nonce = nonce
        super().__init__(server_address, CompanionPanelHandler)

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

    def _state_payload(self) -> dict[str, object]:
        profiles: dict[str, object] = {}
        profile_errors: dict[str, str | None] = {}
        for host, store in self.server.stores.items():
            try:
                snapshot = store.read()
                profiles[host] = snapshot.to_dict() if snapshot else None
                profile_errors[host] = None
            except ProfileStoreError as exc:
                profiles[host] = None
                profile_errors[host] = _safe_error(
                    exc,
                    "本地人格配置无法安全读取",
                )

        profile = profiles["codex"]
        profile_error = profile_errors["codex"]

        hosts: list[dict[str, object]] = []
        for host in _HOSTS:
            try:
                hosts.append(self.server.installer.plan(host).to_dict())
            except InstallError as exc:
                hosts.append(
                    {
                        "host": host,
                        "name": host.title(),
                        "available": False,
                        "existing": None,
                        "error": _safe_error(exc, "安装方案无法安全生成"),
                    }
                )
        strict_ready = bool(os.environ.get("OPENAI_API_KEY", "").strip())

        def strict_mode(*, supported: bool = True) -> dict[str, object]:
            if not supported:
                return {
                    "title": "固定形象严格模式",
                    "status": "尚未接入",
                    "auth_ready": False,
                    "description": "当前版本只保留照片计划，不会冒充宿主执行图片调用。",
                }
            return {
                "title": "固定形象严格模式",
                "status": (
                    "当前启动环境已检测到 API Key"
                    if strict_ready
                    else "当前启动环境未检测到 API Key"
                ),
                "auth_ready": strict_ready,
                "description": (
                    "固定使用 gpt-image-2 / high；对应工具需继承 Key，每次付费调用前都会单独确认。"
                ),
            }

        photo_modes_by_host = {
            "codex": {
                "codex_native": {
                    "title": "Codex 原生模式",
                    "status": "无需单独配置",
                    "description": "适合当前任务快速预览；画质由 Codex 管理，不作为 high 严格证明。",
                },
                "openai_strict": strict_mode(),
            },
            "openclaw": {
                "codex_native": {
                    "title": "OpenClaw 原生快速模式",
                    "status": "宿主管理",
                    "description": "请求 gpt-image-2 / high 并由 OpenClaw 返回当前会话；不能作为官方 API 直连证明。",
                },
                "openai_strict": strict_mode(),
            },
            "hermes": {
                "codex_native": {
                    "title": "Hermes 当前会话模式",
                    "status": "使用严格模式",
                    "description": "当前没有单独的通用快速模式；成图只交给本次入站会话。",
                },
                "openai_strict": strict_mode(),
            },
            "claude": {
                "codex_native": {
                    "title": "Claude 图片执行",
                    "status": "仍为规划",
                    "description": "等待明确的当前任务图片工具与附件契约，不复用其他宿主冒充执行。",
                },
                "openai_strict": strict_mode(supported=False),
            },
        }
        for host, modes in photo_modes_by_host.items():
            host_profile = profiles[host]
            modes["reference_configured"] = bool(
                host_profile
                and host_profile["visual"]["reference_count"] == 1
            )

        return {
            "version": "0.5.0",
            "phase": "Codex、OpenClaw 与 Hermes 已有各自图片路径；Claude 暂保留安全规划。",
            "templates": [
                template.to_dict() for template in self.server.store.templates()
            ],
            "profile": profile,
            "profile_error": profile_error,
            "profiles": profiles,
            "profile_errors": profile_errors,
            "photo_modes": photo_modes_by_host["codex"],
            "photo_modes_by_host": photo_modes_by_host,
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
        payload = self._read_json()
        if payload is None:
            return
        if path == "/api/profile":
            self._save_profile(payload)
            return
        if path == "/api/install":
            self._install_host(payload)
            return
        self._send_json(404, {"error": "接口不存在"})

    def _save_profile(self, payload: dict[str, object]) -> None:
        if set(payload) - _PROFILE_KEYS:
            self._send_json(400, {"error": "配置请求包含不允许的字段"})
            return
        template_id = payload.get("template_id")
        display_name = payload.get("display_name")
        host = payload.get("host", "codex")
        expected_version = payload.get("expected_version")
        starting_mode = payload.get("starting_mode")
        romance_enabled = payload.get("romance_enabled")
        if not isinstance(template_id, str) or not isinstance(display_name, str):
            self._send_json(400, {"error": "请选择模板并填写称呼"})
            return
        if not isinstance(host, str) or host not in _HOSTS:
            self._send_json(400, {"error": "请选择受支持的宿主"})
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
            store = self.server.stores[host]
            existed = store.read() is not None
            snapshot = store.save(
                template_id=template_id,
                display_name=display_name,
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
            {"host": host, "profile": snapshot.to_dict()},
        )

    def _install_host(self, payload: dict[str, object]) -> None:
        if set(payload) - _INSTALL_KEYS:
            self._send_json(400, {"error": "安装请求包含不允许的字段"})
            return
        host = payload.get("host")
        if not isinstance(host, str) or host not in _HOSTS:
            self._send_json(400, {"error": "请选择受支持的宿主"})
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
    if profile_path is None:
        profile_paths = {
            host: default_profile_path(host) for host in _HOSTS
        }
    else:
        codex_path = Path(os.path.abspath(Path(profile_path).expanduser()))
        companion_root = codex_path.parents[1]
        profile_paths = {
            "codex": codex_path,
            "openclaw": companion_root / "hosts" / "openclaw" / "profiles" / "default.toml",
            "hermes": companion_root / "hosts" / "hermes" / "profiles" / "default.toml",
            "claude": companion_root / "hosts" / "claude" / "profiles" / "default.toml",
        }
    return CompanionPanelServer(
        ("127.0.0.1", port),
        stores={
            host: ProfileStore(skill_root=root, profile_path=profile_paths[host])
            for host in _HOSTS
        },
        installer=HostInstaller(skill_root=root, target_roots=install_roots),
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
