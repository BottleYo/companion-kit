from contextlib import contextmanager
import http.client
import json
from pathlib import Path
from threading import Thread
import tempfile
import unittest

from companion_kit.web_panel import create_panel_server


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "virtual-companion"


@contextmanager
def running_panel(root: Path):
    profile_path = root / "profiles" / "default.toml"
    install_roots = {
        "openclaw": root / "openclaw",
        "hermes": root / "hermes",
        "codex": root / "agents",
        "claude": root / "claude",
    }
    server = create_panel_server(
        skill_root=SKILL_ROOT,
        profile_path=profile_path,
        install_roots=install_roots,
        nonce="test-panel-token",
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, profile_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(
    server,
    method: str,
    path: str,
    *,
    body: dict[str, object] | None = None,
    token: str | None = None,
    origin: str | None = None,
    host: str | None = None,
):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    headers = {"Host": host or f"127.0.0.1:{server.server_port}"}
    encoded = None
    if token is not None:
        headers["X-Companion-Token"] = token
    if origin is not None:
        headers["Origin"] = origin
    if body is not None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=encoded, headers=headers)
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    return response, payload


class WebPanelTests(unittest.TestCase):
    def test_static_page_has_local_security_headers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with running_panel(Path(tmp).resolve()) as (server, _):
                response, payload = request(server, "GET", "/")
                favicon, _ = request(server, "GET", "/favicon.ico")

                self.assertEqual(response.status, 200)
                self.assertEqual(favicon.status, 204)
                self.assertIn(b"Companion Kit", payload)
                self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy"))
                self.assertEqual(response.getheader("Cache-Control"), "no-store")

    def test_state_explains_codex_builtin_images_without_api_setup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with running_panel(Path(tmp).resolve()) as (server, _):
                response, body = request(
                    server,
                    "GET",
                    "/api/state",
                    token="test-panel-token",
                )
                payload = json.loads(body)

                self.assertEqual(response.status, 200)
                self.assertEqual(payload["version"], "0.6.0-dev.1")
                self.assertIn("codex_native", payload["photo_modes"])
                self.assertIn("identity_reuse", payload["photo_modes"])
                self.assertNotIn("openai_strict", payload["photo_modes"])
                self.assertEqual(
                    set(payload["profiles"]),
                    {"openclaw", "hermes", "codex", "claude"},
                )
                self.assertIn("openclaw", payload["photo_modes_by_host"])
                self.assertNotIn("Bearer ", json.dumps(payload))

    def test_api_requires_nonce_and_rejects_unexpected_host(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with running_panel(Path(tmp).resolve()) as (server, _):
                unauthorized, _ = request(server, "GET", "/api/state")
                rebound, _ = request(
                    server,
                    "GET",
                    "/api/state",
                    token="test-panel-token",
                    host="attacker.example",
                )

                self.assertEqual(unauthorized.status, 401)
                self.assertEqual(rebound.status, 403)

    def test_profile_create_view_and_conditional_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with running_panel(root) as (server, profile_path):
                origin = f"http://127.0.0.1:{server.server_port}"
                created, created_body = request(
                    server,
                    "POST",
                    "/api/profile",
                    token="test-panel-token",
                    origin=origin,
                    body={
                        "template_id": "warm_healer",
                        "display_name": "小禾",
                        "starting_mode": "familiar",
                        "romance_enabled": True,
                    },
                )
                created_payload = json.loads(created_body)

                conflict, _ = request(
                    server,
                    "POST",
                    "/api/profile",
                    token="test-panel-token",
                    origin=origin,
                    body={"template_id": "calm_partner", "display_name": "阿序"},
                )
                updated, updated_body = request(
                    server,
                    "POST",
                    "/api/profile",
                    token="test-panel-token",
                    origin=origin,
                    body={
                        "template_id": "calm_partner",
                        "display_name": "阿序",
                        "expected_version": created_payload["profile"]["version"],
                    },
                )
                updated_payload = json.loads(updated_body)

                self.assertEqual(created.status, 201)
                self.assertTrue(profile_path.is_file())
                self.assertEqual(conflict.status, 409)
                self.assertEqual(updated.status, 200)
                self.assertEqual(updated_payload["profile"]["display_name"], "阿序")
                self.assertEqual(
                    created_payload["profile"]["relationship"]["starting_mode"],
                    "familiar",
                )
                self.assertTrue(
                    created_payload["profile"]["relationship"]["romance_enabled"]
                )

    def test_each_host_profile_is_saved_and_viewed_independently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with running_panel(root) as (server, _):
                origin = f"http://127.0.0.1:{server.server_port}"
                for host, template, name in (
                    ("openclaw", "sunny_friend", "小晴"),
                    ("hermes", "calm_partner", "阿序"),
                ):
                    response, _ = request(
                        server,
                        "POST",
                        "/api/profile",
                        token="test-panel-token",
                        origin=origin,
                        body={
                            "host": host,
                            "template_id": template,
                            "display_name": name,
                        },
                    )
                    self.assertEqual(response.status, 201)

                response, body = request(
                    server,
                    "GET",
                    "/api/state",
                    token="test-panel-token",
                )
                payload = json.loads(body)

                self.assertEqual(response.status, 200)
                self.assertEqual(
                    payload["profiles"]["openclaw"]["display_name"],
                    "小晴",
                )
                self.assertEqual(
                    payload["profiles"]["hermes"]["display_name"],
                    "阿序",
                )
                self.assertIsNone(payload["profiles"]["codex"])
                self.assertTrue(
                    (
                        root
                        / "hosts"
                        / "openclaw"
                        / "profiles"
                        / "default.toml"
                    ).is_file()
                )
                self.assertTrue(
                    (
                        root
                        / "hosts"
                        / "hermes"
                        / "profiles"
                        / "default.toml"
                    ).is_file()
                )

    def test_write_api_rejects_arbitrary_paths_force_and_bad_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with running_panel(Path(tmp).resolve()) as (server, _):
                origin = f"http://127.0.0.1:{server.server_port}"
                extra_path, _ = request(
                    server,
                    "POST",
                    "/api/profile",
                    token="test-panel-token",
                    origin=origin,
                    body={
                        "template_id": "warm_healer",
                        "display_name": "小禾",
                        "output": "outside.toml",
                    },
                )
                force_install, _ = request(
                    server,
                    "POST",
                    "/api/install",
                    token="test-panel-token",
                    origin=origin,
                    body={"host": "codex", "confirm": True, "force": True},
                )
                bad_origin, _ = request(
                    server,
                    "POST",
                    "/api/profile",
                    token="test-panel-token",
                    origin="http://attacker.example",
                    body={"template_id": "warm_healer", "display_name": "小禾"},
                )

                self.assertEqual(extra_path.status, 400)
                self.assertEqual(force_install.status, 400)
                self.assertEqual(bad_origin.status, 403)

    def test_profile_write_returns_safe_error_when_existing_config_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            profile_path = root / "profiles" / "default.toml"
            profile_path.parent.mkdir(parents=True)
            profile_path.write_text("not valid toml = [", encoding="utf-8")

            with running_panel(root) as (server, _):
                origin = f"http://127.0.0.1:{server.server_port}"
                response, body = request(
                    server,
                    "POST",
                    "/api/profile",
                    token="test-panel-token",
                    origin=origin,
                    body={"template_id": "warm_healer", "display_name": "小禾"},
                )

                payload = json.loads(body)
                self.assertEqual(response.status, 400)
                self.assertIn("error", payload)
                self.assertNotIn(str(root), payload["error"])

    def test_install_requires_separate_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with running_panel(root) as (server, _):
                origin = f"http://127.0.0.1:{server.server_port}"
                preview_only, _ = request(
                    server,
                    "POST",
                    "/api/install",
                    token="test-panel-token",
                    origin=origin,
                    body={"host": "codex", "confirm": False},
                )
                installed, installed_body = request(
                    server,
                    "POST",
                    "/api/install",
                    token="test-panel-token",
                    origin=origin,
                    body={"host": "codex", "confirm": True},
                )
                payload = json.loads(installed_body)

                self.assertEqual(preview_only.status, 409)
                self.assertEqual(installed.status, 200)
                self.assertTrue(payload["install"]["applied"])
                self.assertTrue(
                    (root / "agents" / "skills" / "virtual-companion" / "SKILL.md").is_file()
                )


if __name__ == "__main__":
    unittest.main()
