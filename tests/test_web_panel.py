from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
from threading import Thread
import tempfile
import unittest
from unittest.mock import patch

from companion_kit.web_panel import create_panel_server
from companion_kit.identity_pack import PROFILE_FACE
from companion_kit.image_assets import ImageAssetStore
from tests.png_fixture import tiny_png


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
                with patch.dict(
                    os.environ,
                    {"OPENAI_API_KEY": "must-not-be-read-by-codex-panel"},
                ):
                    response, body = request(
                        server,
                        "GET",
                        "/api/state",
                        token="test-panel-token",
                    )
                payload = json.loads(body)

                self.assertEqual(response.status, 200)
                self.assertEqual(payload["version"], "0.7.0-dev.3")
                self.assertIn("codex_native", payload["photo_modes"])
                self.assertIn("identity_reuse", payload["photo_modes"])
                self.assertNotIn("openai_strict", payload["photo_modes"])
                self.assertEqual(
                    payload["photo_modes"]["identity_reuse"]["status"],
                    "本地链路已就绪，等待真实 Codex 验收",
                )
                self.assertNotIn("profiles", payload)
                self.assertNotIn("photo_modes_by_host", payload)
                self.assertEqual([host["host"] for host in payload["hosts"]], ["codex"])
                self.assertNotIn("must-not-be-read", json.dumps(payload))
                self.assertNotIn("Bearer ", json.dumps(payload))

    def test_state_shows_identity_pack_summary_without_private_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with running_panel(root) as (server, _):
                snapshot = server.store.save(
                    template_id="warm_healer",
                    display_name="小禾",
                    expected_version=None,
                )
                assets = ImageAssetStore(root / "private" / "images")
                primary_candidate = assets.store_candidate(
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    image_bytes=tiny_png(rgba=b"\x20\x40\x60\xff"),
                    task_scope="primary-task",
                )
                primary = assets.confirm_candidate(
                    candidate_id=primary_candidate.candidate_id,
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    task_scope="primary-task",
                )
                server.store.bind_reference(
                    reference_id=primary.reference_id,
                    identity_version=1,
                    expected_version=snapshot.version,
                )
                side_candidate = assets.store_candidate(
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    image_bytes=tiny_png(rgba=b"\x60\x40\x20\xff"),
                    task_scope="side-task",
                    role=PROFILE_FACE,
                    primary_reference_id=primary.reference_id,
                )
                assets.confirm_candidate(
                    candidate_id=side_candidate.candidate_id,
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    task_scope="side-task",
                )

                response, body = request(
                    server,
                    "GET",
                    "/api/state",
                    token="test-panel-token",
                )
                payload = json.loads(body)

            self.assertEqual(response.status, 200)
            self.assertEqual(payload["photo_modes"]["identity_pack"]["level"], "enhanced")
            self.assertEqual(
                payload["photo_modes"]["identity_pack"]["roles"],
                ["primary_face", "profile_face"],
            )
            serialized = json.dumps(payload["photo_modes"]["identity_pack"], ensure_ascii=False)
            self.assertNotIn("ref_", serialized)
            self.assertNotIn("sha256", serialized)
            self.assertNotIn(str(root), serialized)

    def test_reading_missing_identity_pack_does_not_create_private_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with running_panel(root) as (server, _):
                snapshot = server.store.save(
                    template_id="warm_healer",
                    display_name="小禾",
                    expected_version=None,
                )
                server.store.bind_reference(
                    reference_id="ref_1234567890abcdef",
                    identity_version=1,
                    expected_version=snapshot.version,
                )

                response, body = request(
                    server,
                    "GET",
                    "/api/state",
                    token="test-panel-token",
                )
                payload = json.loads(body)

            self.assertEqual(response.status, 200)
            self.assertEqual(payload["photo_modes"]["identity_pack"]["level"], "unavailable")
            self.assertFalse((root / "private").exists())

    def test_one_sentence_persona_draft_is_preview_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with running_panel(root) as (server, profile_path):
                origin = f"http://127.0.0.1:{server.server_port}"
                response, body = request(
                    server,
                    "POST",
                    "/api/persona/draft",
                    token="test-panel-token",
                    origin=origin,
                    body={
                        "description": "高冷御姐，成熟自信，解决问题利落",
                        "display_name": "岚",
                    },
                )
                payload = json.loads(body)

                self.assertEqual(response.status, 200)
                self.assertEqual(payload["draft"]["display_name"], "岚")
                self.assertIn("成熟", payload["draft"]["traits"])
                self.assertEqual(
                    payload["draft"]["visual_identity"]["status"],
                    "unset",
                )
                self.assertFalse(profile_path.exists())

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

    def test_codex_panel_does_not_write_other_host_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with running_panel(root) as (server, _):
                origin = f"http://127.0.0.1:{server.server_port}"
                response, _ = request(
                    server,
                    "POST",
                    "/api/profile",
                    token="test-panel-token",
                    origin=origin,
                    body={
                        "host": "openclaw",
                        "template_id": "sunny_friend",
                        "display_name": "小晴",
                    },
                )

                self.assertEqual(response.status, 400)
                self.assertFalse(
                    (root / "hosts" / "openclaw" / "profiles" / "default.toml").exists()
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
