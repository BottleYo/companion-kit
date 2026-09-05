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
from companion_kit.hook_health import COMPANION_CONTEXT
from companion_kit.identity_pack import PROFILE_FACE
from companion_kit.image_assets import ImageAssetStore
from companion_kit.profile_store import ProfileConflict
from tests.png_fixture import tiny_png


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "virtual-companion"


@contextmanager
def running_panel(root: Path, *, profile_path: Path | None = None):
    profile_path = profile_path or root / "profiles" / "default.toml"
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
    raw: bytes | None = None,
    content_type: str | None = None,
    extra_headers: dict[str, str] | None = None,
    token: str | None = None,
    origin: str | None = None,
    host: str | None = None,
):
    if body is not None and raw is not None:
        raise ValueError("body 与 raw 不能同时提供")
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
    elif raw is not None:
        encoded = raw
        headers["Content-Type"] = content_type or "application/octet-stream"
    if extra_headers:
        headers.update(extra_headers)
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
                self.assertIn(b'id="identityFile"', payload)
                self.assertIn(b'id="createBackup"', payload)
                self.assertIn(b'id="applyUpgrade"', payload)
                self.assertIn(b'id="copyHooksCommand"', payload)
                self.assertIn(b'id="confirmHooksReviewed"', payload)
                self.assertIn(b'id="replaceIdentity"', payload)
                self.assertIn("设为固定主脸".encode("utf-8"), payload)
                self.assertIn("更换主脸".encode("utf-8"), payload)
                self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy"))
                self.assertEqual(response.getheader("Cache-Control"), "no-store")

    def test_install_copy_does_not_claim_codex_is_runtime_ready(self) -> None:
        app_js = (
            SKILL_ROOT / "scripts" / "companion_kit" / "web_assets" / "app.js"
        ).read_text(encoding="utf-8")

        self.assertNotIn("安装完成，新开一个 Codex 任务就可以开始", app_js)
        self.assertIn(
            "Plugin 已安装；请先审核 Companion Kit Hooks，再新建任务完成验证。",
            app_js,
        )

    def test_panel_creates_verified_restore_point_only_after_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with running_panel(root) as (server, profile_path):
                origin = f"http://127.0.0.1:{server.server_port}"
                server.store.save(
                    template_id="warm_healer",
                    display_name="小禾",
                    expected_version=None,
                )
                before = profile_path.read_bytes()
                refused, _ = request(
                    server,
                    "POST",
                    "/api/backups",
                    token="test-panel-token",
                    origin=origin,
                    body={"confirm": False},
                )
                created, created_body = request(
                    server,
                    "POST",
                    "/api/backups",
                    token="test-panel-token",
                    origin=origin,
                    body={"confirm": True},
                )
                listed, listed_body = request(
                    server,
                    "GET",
                    "/api/backups",
                    token="test-panel-token",
                )

            created_payload = json.loads(created_body)
            listed_payload = json.loads(listed_body)
            self.assertEqual(refused.status, 409)
            self.assertEqual(created.status, 201)
            self.assertTrue(created_payload["backup"]["verified"])
            self.assertEqual(listed.status, 200)
            self.assertEqual(
                listed_payload["backups"][0]["backup_id"],
                created_payload["backup"]["backup_id"],
            )
            self.assertEqual(profile_path.read_bytes(), before)

    def test_upgrade_check_endpoint_is_explicit_and_hides_private_source(self) -> None:
        class FakeCheck:
            def to_dict(self) -> dict[str, object]:
                return {
                    "ready": True,
                    "release_version": "0.9.0-dev.5",
                    "marketplace_source_type": "local",
                    "update_candidate": True,
                }

        class FakePlanner:
            def check(self) -> FakeCheck:
                return FakeCheck()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with running_panel(root) as (server, _):
                server.upgrade_planner = FakePlanner()
                unauthorized, _ = request(server, "GET", "/api/upgrade/check")
                response, body = request(
                    server,
                    "GET",
                    "/api/upgrade/check",
                    token="test-panel-token",
                )

            payload = json.loads(body)
            self.assertEqual(unauthorized.status, 401)
            self.assertEqual(response.status, 200)
            self.assertTrue(payload["upgrade"]["update_candidate"])
            self.assertNotIn(str(root), json.dumps(payload))

    def test_upgrade_apply_requires_confirmation_and_returns_no_private_path(self) -> None:
        class FakeResult:
            def to_dict(self) -> dict[str, object]:
                return {
                    "from_version": "0.7.0-dev.4",
                    "to_version": "0.9.0-dev.5",
                    "backup_id": "20260806T080000Z-deadbeef",
                    "applied": True,
                    "durable_data_replaced": False,
                }

        class FakeExecutor:
            def apply(self, *, confirm: bool) -> FakeResult:
                if confirm is not True:
                    raise AssertionError("confirmation was not forwarded")
                return FakeResult()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with running_panel(root) as (server, profile_path):
                server.store.save(
                    template_id="warm_healer",
                    display_name="小禾",
                    expected_version=None,
                )
                before = profile_path.read_bytes()
                server.upgrade_executor = FakeExecutor()
                origin = f"http://127.0.0.1:{server.server_port}"
                refused, _ = request(
                    server,
                    "POST",
                    "/api/upgrade/apply",
                    token="test-panel-token",
                    origin=origin,
                    body={"confirm": False},
                )
                response, body = request(
                    server,
                    "POST",
                    "/api/upgrade/apply",
                    token="test-panel-token",
                    origin=origin,
                    body={"confirm": True},
                )

            payload = json.loads(body)
            self.assertEqual(refused.status, 409)
            self.assertEqual(response.status, 200)
            self.assertTrue(payload["upgrade"]["applied"])
            self.assertFalse(payload["upgrade"]["durable_data_replaced"])
            self.assertEqual(profile_path.read_bytes(), before)
            self.assertNotIn(str(root), json.dumps(payload))

    def test_identity_upload_requires_saved_persona_and_explicit_consent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with running_panel(root) as (server, _):
                origin = f"http://127.0.0.1:{server.server_port}"
                no_profile, _ = request(
                    server,
                    "POST",
                    "/api/identity/candidate",
                    token="test-panel-token",
                    origin=origin,
                    raw=tiny_png(),
                    content_type="image/png",
                    extra_headers={"X-Companion-Image-Consent": "adult-authorized"},
                )
                server.store.save(
                    template_id="warm_healer",
                    display_name="小禾",
                    expected_version=None,
                )
                no_consent, _ = request(
                    server,
                    "POST",
                    "/api/identity/candidate",
                    token="test-panel-token",
                    origin=origin,
                    raw=tiny_png(),
                    content_type="image/png",
                )
                wrong_type, _ = request(
                    server,
                    "POST",
                    "/api/identity/candidate",
                    token="test-panel-token",
                    origin=origin,
                    raw=tiny_png(),
                    content_type="image/jpeg",
                    extra_headers={"X-Companion-Image-Consent": "adult-authorized"},
                )

            self.assertEqual(no_profile.status, 409)
            self.assertEqual(no_consent.status, 409)
            self.assertEqual(wrong_type.status, 415)
            self.assertFalse((root / "private" / "images").exists())

    def test_identity_upload_preview_and_confirmation_lock_primary_face(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with running_panel(root) as (server, _):
                origin = f"http://127.0.0.1:{server.server_port}"
                snapshot = server.store.save(
                    template_id="warm_healer",
                    display_name="小禾",
                    expected_version=None,
                )
                staged, staged_body = request(
                    server,
                    "POST",
                    "/api/identity/candidate",
                    token="test-panel-token",
                    origin=origin,
                    raw=tiny_png(metadata=b"private-metadata"),
                    content_type="image/png",
                    extra_headers={"X-Companion-Image-Consent": "adult-authorized"},
                )
                staged_payload = json.loads(staged_body)
                pending = next((root / "private" / "images").rglob("pending.png"))
                pending_bytes = pending.read_bytes()

                confirmed, confirmed_body = request(
                    server,
                    "POST",
                    "/api/identity/confirm",
                    token="test-panel-token",
                    origin=origin,
                    body={
                        "candidate_id": staged_payload["candidate"]["candidate_id"],
                        "profile_version": snapshot.version,
                        "confirm": True,
                    },
                )
                confirmed_payload = json.loads(confirmed_body)
                state_response, state_body = request(
                    server,
                    "GET",
                    "/api/state",
                    token="test-panel-token",
                )
                state = json.loads(state_body)
                private_preview, private_preview_body = request(
                    server,
                    "GET",
                    "/api/identity/primary",
                    token="test-panel-token",
                )
                unauthorized_preview, _ = request(
                    server,
                    "GET",
                    "/api/identity/primary",
                )

            self.assertEqual(staged.status, 201)
            self.assertNotIn(b"private-metadata", pending_bytes)
            self.assertEqual(confirmed.status, 200)
            self.assertEqual(
                confirmed_payload["profile"]["visual_identity"]["status"],
                "locked",
            )
            self.assertNotIn("reference_id", confirmed_payload)
            self.assertEqual(state_response.status, 200)
            self.assertTrue(state["photo_modes"]["reference_configured"])
            self.assertEqual(state["photo_modes"]["identity_pack"]["level"], "basic")
            self.assertEqual(private_preview.status, 200)
            self.assertEqual(private_preview.getheader("Content-Type"), "image/png")
            self.assertEqual(private_preview_body, pending_bytes)
            self.assertEqual(unauthorized_preview.status, 401)
            self.assertFalse(any((root / "private" / "images").rglob("pending.png")))

    def test_identity_upload_replaces_locked_face_as_a_new_identity_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with running_panel(root) as (server, _):
                origin = f"http://127.0.0.1:{server.server_port}"
                snapshot = server.store.save(
                    template_id="warm_healer",
                    display_name="小禾",
                    expected_version=None,
                )
                assets = ImageAssetStore(root / "private" / "images")
                candidate = assets.store_candidate(
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    image_bytes=tiny_png(),
                    task_scope="existing-face",
                )
                reference = assets.confirm_candidate(
                    candidate_id=candidate.candidate_id,
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    task_scope="existing-face",
                )
                bound = server.store.bind_reference(
                    reference_id=reference.reference_id,
                    identity_version=1,
                    expected_version=snapshot.version,
                )
                supplemental = assets.store_candidate(
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    image_bytes=tiny_png(rgba=b"\x60\x40\x20\xff"),
                    task_scope="existing-profile-face",
                    role=PROFILE_FACE,
                    primary_reference_id=reference.reference_id,
                )
                assets.confirm_candidate(
                    candidate_id=supplemental.candidate_id,
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    task_scope="existing-profile-face",
                )
                server.hook_health_store.record_success("session_start")
                server.companion_scope_store.bind("replacement-task")
                server.hook_health_store.record_success(COMPANION_CONTEXT)
                before_state_response, before_state_body = request(
                    server,
                    "GET",
                    "/api/state",
                    token="test-panel-token",
                )
                before_state = json.loads(before_state_body)
                relationship_path = root / "private" / "relationships.sqlite3"
                relationship_path.parent.mkdir(parents=True, exist_ok=True)
                relationship_path.write_bytes(b"important-relationship-history")
                replacement_bytes = tiny_png(rgba=b"\x80\x40\x20\xff")

                staged, staged_body = request(
                    server,
                    "POST",
                    "/api/identity/candidate",
                    token="test-panel-token",
                    origin=origin,
                    raw=replacement_bytes,
                    content_type="image/png",
                    extra_headers={"X-Companion-Image-Consent": "adult-authorized"},
                )
                staged_payload = json.loads(staged_body)
                confirmed, confirmed_body = request(
                    server,
                    "POST",
                    "/api/identity/confirm",
                    token="test-panel-token",
                    origin=origin,
                    body={
                        "candidate_id": staged_payload["candidate"]["candidate_id"],
                        "profile_version": staged_payload["candidate"]["profile_version"],
                        "confirm": True,
                    },
                )
                confirmed_payload = json.loads(confirmed_body)
                current = server.store.read()
                assert current is not None
                old_pack = assets.resolve_identity_pack(
                    primary_reference_id=reference.reference_id,
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                )
                new_pack = assets.resolve_identity_pack(
                    primary_reference_id=current.profile.visual.reference_ids[0],
                    profile_id=snapshot.profile.id,
                    identity_version=2,
                )
                primary_response, primary_body = request(
                    server,
                    "GET",
                    "/api/identity/primary",
                    token="test-panel-token",
                )
                after_state_response, after_state_body = request(
                    server,
                    "GET",
                    "/api/state",
                    token="test-panel-token",
                )
                after_state = json.loads(after_state_body)

            self.assertEqual(staged.status, 201)
            self.assertEqual(before_state_response.status, 200)
            self.assertTrue(before_state["runtime_readiness"]["session_loaded"])
            self.assertEqual(staged_payload["candidate"]["identity_version"], 2)
            self.assertEqual(staged_payload["candidate"]["operation"], "replace_primary")
            self.assertEqual(confirmed.status, 200)
            self.assertEqual(
                confirmed_payload["profile"]["visual_identity"]["identity_version"],
                2,
            )
            self.assertEqual(current.profile.display_name, bound.profile.display_name)
            self.assertEqual(old_pack.roles, ("primary_face", "profile_face"))
            self.assertEqual(new_pack.roles, ("primary_face",))
            self.assertNotEqual(
                current.profile.visual.reference_ids,
                bound.profile.visual.reference_ids,
            )
            self.assertEqual(
                relationship_path.read_bytes(),
                b"important-relationship-history",
            )
            self.assertEqual(primary_response.status, 200)
            self.assertEqual(primary_body, replacement_bytes)
            self.assertEqual(after_state_response.status, 200)
            self.assertFalse(after_state["runtime_readiness"]["session_loaded"])

    def test_staged_replacement_does_not_change_active_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with running_panel(root) as (server, _):
                origin = f"http://127.0.0.1:{server.server_port}"
                snapshot = server.store.save(
                    template_id="warm_healer",
                    display_name="小禾",
                    expected_version=None,
                )
                assets = ImageAssetStore(root / "private" / "images")
                candidate = assets.store_candidate(
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    image_bytes=tiny_png(),
                    task_scope="active-face",
                )
                primary = assets.confirm_candidate(
                    candidate_id=candidate.candidate_id,
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    task_scope="active-face",
                )
                bound = server.store.bind_reference(
                    reference_id=primary.reference_id,
                    identity_version=1,
                    expected_version=snapshot.version,
                )

                staged, staged_body = request(
                    server,
                    "POST",
                    "/api/identity/candidate",
                    token="test-panel-token",
                    origin=origin,
                    raw=tiny_png(rgba=b"\x10\x90\x40\xff"),
                    content_type="image/png",
                    extra_headers={"X-Companion-Image-Consent": "adult-authorized"},
                )
                current = server.store.read()

            self.assertEqual(staged.status, 201, staged_body)
            self.assertIsNotNone(current)
            assert current is not None
            self.assertEqual(current.version, bound.version)
            self.assertEqual(current.profile.visual.identity_version, 1)
            self.assertEqual(
                current.profile.visual.reference_ids,
                bound.profile.visual.reference_ids,
            )

    def test_failed_replacement_keeps_current_face_and_restores_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with running_panel(root) as (server, _):
                origin = f"http://127.0.0.1:{server.server_port}"
                snapshot = server.store.save(
                    template_id="warm_healer",
                    display_name="小禾",
                    expected_version=None,
                )
                assets = ImageAssetStore(root / "private" / "images")
                initial = assets.store_candidate(
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    image_bytes=tiny_png(),
                    task_scope="rollback-current",
                )
                primary = assets.confirm_candidate(
                    candidate_id=initial.candidate_id,
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    task_scope="rollback-current",
                )
                bound = server.store.bind_reference(
                    reference_id=primary.reference_id,
                    identity_version=1,
                    expected_version=snapshot.version,
                )
                replacement_bytes = tiny_png(rgba=b"\x70\x30\x50\xff")
                staged, staged_body = request(
                    server,
                    "POST",
                    "/api/identity/candidate",
                    token="test-panel-token",
                    origin=origin,
                    raw=replacement_bytes,
                    content_type="image/png",
                    extra_headers={"X-Companion-Image-Consent": "adult-authorized"},
                )
                staged_payload = json.loads(staged_body)
                with patch.object(
                    server.store,
                    "rotate_reference",
                    side_effect=ProfileConflict("Persona 同时发生了变化"),
                ):
                    failed, failed_body = request(
                        server,
                        "POST",
                        "/api/identity/confirm",
                        token="test-panel-token",
                        origin=origin,
                        body={
                            "candidate_id": staged_payload["candidate"]["candidate_id"],
                            "profile_version": staged_payload["candidate"]["profile_version"],
                            "confirm": True,
                        },
                    )
                failed_payload = json.loads(failed_body)
                current = server.store.read()
                assert current is not None
                restored_candidate = assets.read_candidate_bytes(
                    candidate_id=staged_payload["candidate"]["candidate_id"],
                    profile_id=snapshot.profile.id,
                    identity_version=2,
                    task_scope="web-panel:test-panel-token",
                )
                current_pack = assets.resolve_identity_pack(
                    primary_reference_id=primary.reference_id,
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                )

            self.assertEqual(staged.status, 201)
            self.assertEqual(failed.status, 409)
            self.assertEqual(failed_payload["retry_profile_version"], bound.version)
            self.assertEqual(current.version, bound.version)
            self.assertEqual(current.profile.visual.identity_version, 1)
            self.assertEqual(
                current.profile.visual.reference_ids,
                (primary.reference_id,),
            )
            self.assertEqual(restored_candidate, replacement_bytes)
            self.assertEqual(current_pack.primary_reference_id, primary.reference_id)
            self.assertFalse(
                (root / "private" / "images" / "identities" / "companion" / "v2" / "pack.json").exists()
            )

    def test_flat_custom_profile_keeps_images_beside_its_own_private_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            profile_path = root / "custom" / "persona.toml"
            with running_panel(root, profile_path=profile_path) as (server, _):
                origin = f"http://127.0.0.1:{server.server_port}"
                server.store.save(
                    template_id="warm_healer",
                    display_name="小禾",
                    expected_version=None,
                )
                response, _ = request(
                    server,
                    "POST",
                    "/api/identity/candidate",
                    token="test-panel-token",
                    origin=origin,
                    raw=tiny_png(),
                    content_type="image/png",
                    extra_headers={"X-Companion-Image-Consent": "adult-authorized"},
                )

            self.assertEqual(response.status, 201)
            self.assertTrue(any((root / "custom" / "private" / "images").rglob("pending.png")))
            self.assertFalse((root / "private").exists())

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
                self.assertEqual(payload["version"], "0.9.0-dev.5")
                self.assertIn("codex_native", payload["photo_modes"])
                self.assertIn("identity_reuse", payload["photo_modes"])
                self.assertNotIn("openai_strict", payload["photo_modes"])
                self.assertEqual(
                    payload["photo_modes"]["identity_reuse"]["status"],
                    "尚未确认主脸",
                )
                self.assertNotIn("profiles", payload)
                self.assertNotIn("photo_modes_by_host", payload)
                self.assertEqual([host["host"] for host in payload["hosts"]], ["codex"])
                self.assertNotIn("must-not-be-read", json.dumps(payload))
                self.assertNotIn("Bearer ", json.dumps(payload))

    def test_installed_plugin_without_session_hook_receipt_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with running_panel(root) as (server, _):
                server.installer.install("codex")
                response, body = request(
                    server,
                    "GET",
                    "/api/state",
                    token="test-panel-token",
                )
                payload = json.loads(body)

            self.assertEqual(response.status, 200)
            self.assertEqual(payload["runtime_readiness"]["state"], "review_required")
            self.assertFalse(payload["runtime_readiness"]["ready"])
            self.assertTrue(payload["runtime_readiness"]["plugin_installed"])
            self.assertEqual(payload["hook_health"]["session_start"]["state"], "missing")
            self.assertNotIn("trusted_hash", json.dumps(payload))

    def test_healthy_identity_pack_is_saved_but_not_loaded_without_session_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with running_panel(root) as (server, _):
                server.installer.install("codex")
                snapshot = server.store.save(
                    template_id="warm_healer",
                    display_name="小禾",
                    expected_version=None,
                )
                assets = ImageAssetStore(root / "private" / "images")
                candidate = assets.store_candidate(
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    image_bytes=tiny_png(),
                    task_scope="saved-primary",
                )
                primary = assets.confirm_candidate(
                    candidate_id=candidate.candidate_id,
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    task_scope="saved-primary",
                )
                server.store.bind_reference(
                    reference_id=primary.reference_id,
                    identity_version=1,
                    expected_version=snapshot.version,
                )

                response, body = request(
                    server,
                    "GET",
                    "/api/hook-health",
                    token="test-panel-token",
                )
                payload = json.loads(body)

            self.assertEqual(response.status, 200)
            self.assertTrue(payload["runtime_readiness"]["reference_saved"])
            self.assertFalse(payload["runtime_readiness"]["session_loaded"])
            self.assertEqual(
                payload["runtime_readiness"]["summary"],
                "参考图已保存，但 Hooks 尚未审核",
            )
            self.assertEqual(payload["runtime_readiness"]["state"], "review_required")

    def test_review_session_and_explicit_companion_binding_move_panel_to_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with running_panel(root) as (server, _):
                server.installer.install("codex")
                snapshot = server.store.save(
                    template_id="warm_healer",
                    display_name="小禾",
                    expected_version=None,
                )
                assets = ImageAssetStore(root / "private" / "images")
                candidate = assets.store_candidate(
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    image_bytes=tiny_png(),
                    task_scope="ready-primary",
                )
                primary = assets.confirm_candidate(
                    candidate_id=candidate.candidate_id,
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    task_scope="ready-primary",
                )
                server.store.bind_reference(
                    reference_id=primary.reference_id,
                    identity_version=1,
                    expected_version=snapshot.version,
                )
                origin = f"http://127.0.0.1:{server.server_port}"
                acknowledged, acknowledged_body = request(
                    server,
                    "POST",
                    "/api/hooks/reviewed",
                    token="test-panel-token",
                    origin=origin,
                    body={"confirm": True},
                )
                pending = json.loads(acknowledged_body)
                server.hook_health_store.record_success("session_start")
                verified, verified_body = request(
                    server,
                    "GET",
                    "/api/hook-health",
                    token="test-panel-token",
                )
                verified_payload = json.loads(verified_body)
                server.companion_scope_store.bind("ready-task")
                server.hook_health_store.record_success(COMPANION_CONTEXT)
                ready_response, ready_body = request(
                    server,
                    "GET",
                    "/api/hook-health",
                    token="test-panel-token",
                )
                ready = json.loads(ready_body)

            self.assertEqual(acknowledged.status, 200)
            self.assertEqual(
                pending["runtime_readiness"]["state"],
                "verification_pending",
            )
            self.assertEqual(verified.status, 200)
            self.assertEqual(
                verified_payload["runtime_readiness"]["state"],
                "companion_task_required",
            )
            self.assertEqual(ready_response.status, 200)
            self.assertEqual(ready["runtime_readiness"]["state"], "ready")
            self.assertTrue(ready["runtime_readiness"]["ready"])
            self.assertEqual(
                ready["runtime_readiness"]["summary"],
                "Persona 已加载，主脸参考已就绪",
            )
            self.assertFalse(ready["hook_health"]["post_tool_use"]["verified"])

    def test_stale_session_receipt_returns_panel_to_review_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with running_panel(root) as (server, _):
                server.installer.install("codex")
                server.hook_health_store.acknowledge_review()
                server.hook_health_store.record_success("session_start")
                health_path = root / "system" / "hook-health" / "session_start.json"
                review_path = root / "system" / "hook-health" / "review.json"
                for path in (health_path, review_path):
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    payload["plugin_version"] = "0.7.0-dev.4"
                    path.write_text(json.dumps(payload), encoding="utf-8")

                response, body = request(
                    server,
                    "GET",
                    "/api/hook-health",
                    token="test-panel-token",
                )
                payload = json.loads(body)

            self.assertEqual(response.status, 200)
            self.assertEqual(payload["hook_health"]["session_start"]["state"], "stale")
            self.assertEqual(payload["hook_health"]["review"]["state"], "stale")
            self.assertEqual(payload["runtime_readiness"]["state"], "review_required")

    def test_face_confirmed_after_session_receipt_requires_a_new_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with running_panel(root) as (server, _):
                server.installer.install("codex")
                snapshot = server.store.save(
                    template_id="warm_healer",
                    display_name="小禾",
                    expected_version=None,
                )
                server.hook_health_store.record_success("session_start")
                server.companion_scope_store.bind("newer-face-task")
                server.hook_health_store.record_success(COMPANION_CONTEXT)
                assets = ImageAssetStore(root / "private" / "images")
                candidate = assets.store_candidate(
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    image_bytes=tiny_png(),
                    task_scope="newer-face",
                )
                primary = assets.confirm_candidate(
                    candidate_id=candidate.candidate_id,
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    task_scope="newer-face",
                )
                server.store.bind_reference(
                    reference_id=primary.reference_id,
                    identity_version=1,
                    expected_version=snapshot.version,
                )

                response, body = request(
                    server,
                    "GET",
                    "/api/hook-health",
                    token="test-panel-token",
                )
                payload = json.loads(body)

            self.assertEqual(response.status, 200)
            self.assertTrue(payload["hook_health"]["session_start"]["verified"])
            self.assertFalse(payload["runtime_readiness"]["session_loaded"])
            self.assertEqual(
                payload["runtime_readiness"]["state"],
                "verification_pending",
            )

    def test_hook_review_and_install_do_not_overwrite_existing_persona_or_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with running_panel(root) as (server, profile_path):
                snapshot = server.store.save(
                    template_id="warm_healer",
                    display_name="小禾",
                    expected_version=None,
                )
                assets = ImageAssetStore(root / "private" / "images")
                candidate = assets.store_candidate(
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    image_bytes=tiny_png(),
                    task_scope="preserved-primary",
                )
                primary = assets.confirm_candidate(
                    candidate_id=candidate.candidate_id,
                    profile_id=snapshot.profile.id,
                    identity_version=1,
                    task_scope="preserved-primary",
                )
                server.store.bind_reference(
                    reference_id=primary.reference_id,
                    identity_version=1,
                    expected_version=snapshot.version,
                )
                relationship_path = root / "private" / "relationships.sqlite3"
                relationship_path.write_bytes(b"important-relationship-history")
                before_profile = profile_path.read_bytes()
                before_image = primary.path.read_bytes()
                before_relationship = relationship_path.read_bytes()

                server.installer.install("codex")
                origin = f"http://127.0.0.1:{server.server_port}"
                request(
                    server,
                    "POST",
                    "/api/hooks/reviewed",
                    token="test-panel-token",
                    origin=origin,
                    body={"confirm": True},
                )

            self.assertEqual(profile_path.read_bytes(), before_profile)
            self.assertEqual(primary.path.read_bytes(), before_image)
            self.assertEqual(relationship_path.read_bytes(), before_relationship)

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
