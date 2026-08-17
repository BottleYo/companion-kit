from __future__ import annotations

import json
import base64
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from companion_kit.backup import CompanionDataLayout
from companion_kit.companion_scope import CompanionScopeStore
from companion_kit.initializer import initialize_profile
from companion_kit.profile_store import ProfileStore
from tests.png_fixture import tiny_png


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "virtual-companion"
SERVER = PROJECT_ROOT / "mcp" / "companion_server.py"
WIDGET_URI = "ui://companion-kit/home-v1.html"


def _run_server(home: Path, requests: list[dict[str, object]]) -> list[dict[str, object]]:
    environment = os.environ.copy()
    environment.update(
        {
            "COMPANION_HOME": str(home),
            "PLUGIN_ROOT": str(PROJECT_ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    completed = subprocess.run(
        ["python3", str(SERVER)],
        input="".join(json.dumps(item) + "\n" for item in requests),
        text=True,
        capture_output=True,
        check=False,
        env=environment,
        timeout=8,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    if completed.stderr:
        raise AssertionError(completed.stderr)
    return [json.loads(line) for line in completed.stdout.splitlines() if line]


class CodexPluginUiTests(unittest.TestCase):
    def test_manifest_declares_local_mcp_server(self) -> None:
        manifest = json.loads(
            (PROJECT_ROOT / ".codex-plugin" / "plugin.json").read_text("utf-8")
        )
        config = json.loads((PROJECT_ROOT / ".mcp.json").read_text("utf-8"))

        self.assertEqual(manifest["mcpServers"], "./.mcp.json")
        server = config["mcpServers"]["companion-kit"]
        self.assertEqual(server["command"], "python3")
        self.assertEqual(server["cwd"], ".")
        self.assertEqual(server["args"], ["./mcp/companion_server.py"])

    def test_mcp_server_exposes_narrow_management_tools_and_widget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve() / "companion-home"
            initialize_profile(
                skill_root=SKILL_ROOT,
                template_id="warm_healer",
                display_name="不应出现在工具结果里的名字",
                output=home / "profiles" / "default.toml",
            )
            responses = _run_server(
                home,
                [
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "1"},
                        },
                    },
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "resources/read",
                        "params": {"uri": WIDGET_URI},
                    },
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "tools/call",
                        "params": {"name": "open_companion_home", "arguments": {}},
                    },
                ],
            )

            by_id = {item["id"]: item["result"] for item in responses}
            tools = by_id[2]["tools"]
            self.assertEqual(
                [tool["name"] for tool in tools],
                [
                    "open_companion_home",
                    "save_companion_persona",
                    "set_companion_primary_face",
                    "clear_companion_task_bindings",
                ],
            )
            self.assertTrue(tools[0]["annotations"]["readOnlyHint"])
            self.assertFalse(tools[1]["annotations"]["readOnlyHint"])
            self.assertFalse(tools[2]["annotations"]["readOnlyHint"])
            self.assertFalse(tools[3]["annotations"]["readOnlyHint"])
            self.assertFalse(tools[0]["annotations"]["destructiveHint"])
            self.assertTrue(tools[1]["annotations"]["destructiveHint"])
            self.assertTrue(tools[2]["annotations"]["destructiveHint"])
            self.assertEqual(tools[0]["_meta"]["ui"]["visibility"], ["model", "app"])
            for write_tool in tools[1:]:
                self.assertEqual(write_tool["_meta"]["ui"]["visibility"], ["app"])
                self.assertEqual(write_tool["_meta"]["openai/visibility"], "private")
                self.assertIn("outputSchema", write_tool)
            self.assertIn("outputSchema", tools[0])
            self.assertEqual(tools[0]["_meta"]["ui"]["resourceUri"], WIDGET_URI)

            resource = by_id[3]["contents"][0]
            self.assertEqual(resource["uri"], WIDGET_URI)
            self.assertEqual(resource["mimeType"], "text/html;profile=mcp-app")
            html = resource["text"]
            self.assertIn('rpcRequest("ui/initialize"', html)
            self.assertIn('rpcNotify("ui/message"', html)
            self.assertIn("event.source !== window.parent", html)
            self.assertIn("把这个任务设为陪伴任务", html)
            self.assertIn("退出陪伴任务", html)
            self.assertIn('writeText("/hooks")', html)
            self.assertNotIn("https://", html)
            self.assertNotIn("http://", html)

            result = by_id[4]
            self.assertEqual(
                result["_meta"]["openai/outputTemplate"],
                WIDGET_URI,
            )
            self.assertTrue(result["structuredContent"]["persona"]["configured"])
            model_visible = json.dumps(
                {
                    "content": result["content"],
                    "structuredContent": result["structuredContent"],
                },
                ensure_ascii=False,
            )
            private_form = result["_meta"]["companion-kit/ui-state"]["persona_form"]
            private_version = result["_meta"]["companion-kit/ui-state"][
                "profile_version"
            ]
            self.assertEqual(private_form["display_name"], "不应出现在工具结果里的名字")
            self.assertRegex(private_version, r"^[a-f0-9]{64}$")
            serialized = json.dumps(result, ensure_ascii=False)
            self.assertNotIn(str(home), serialized)
            self.assertNotIn("不应出现在工具结果里的名字", model_visible)
            self.assertNotIn("session_id", serialized)
            self.assertNotIn("reference_id", serialized)

    def test_plugin_ui_can_clear_only_task_bindings_after_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve() / "companion-home"
            initialize_profile(
                skill_root=SKILL_ROOT,
                template_id="warm_healer",
                display_name="小禾",
                output=home / "profiles" / "default.toml",
            )
            profile_path = home / "profiles" / "default.toml"
            before = profile_path.read_bytes()
            layout = CompanionDataLayout.for_profile(profile_path)
            with patch.dict(
                os.environ, {"COMPANION_HOME": str(home)}, clear=False
            ):
                scopes = CompanionScopeStore(
                    root=layout.system_root / "codex-scopes"
                )
                scopes.bind("task-a")
                scopes.bind("task-b")
                self.assertEqual(scopes.count(), 2)

            rejected = _run_server(
                home,
                [
                    {
                        "jsonrpc": "2.0",
                        "id": 15,
                        "method": "tools/call",
                        "params": {
                            "name": "clear_companion_task_bindings",
                            "arguments": {"confirm": False},
                        },
                    }
                ],
            )
            self.assertEqual(rejected[0]["error"]["code"], -32602)

            cleared = _run_server(
                home,
                [
                    {
                        "jsonrpc": "2.0",
                        "id": 16,
                        "method": "tools/call",
                        "params": {
                            "name": "clear_companion_task_bindings",
                            "arguments": {"confirm": True},
                        },
                    }
                ],
            )
            self.assertEqual(
                cleared[0]["result"]["structuredContent"]["scope"][
                    "active_task_count"
                ],
                0,
            )
            self.assertEqual(profile_path.read_bytes(), before)

    def test_initialize_negotiates_unsupported_protocol_to_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            responses = _run_server(
                Path(tmp).resolve() / "companion-home",
                [
                    {
                        "jsonrpc": "2.0",
                        "id": 13,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2099-01-01",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "1"},
                        },
                    }
                ],
            )
            self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-11-25")

    def test_persona_write_requires_confirmation_and_keeps_tool_result_path_free(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve() / "companion-home"
            responses = _run_server(
                home,
                [
                    {
                        "jsonrpc": "2.0",
                        "id": 5,
                        "method": "tools/call",
                        "params": {
                            "name": "save_companion_persona",
                            "arguments": {
                                "description": "高冷御姐，成熟自信，做事利落",
                                "display_name": "岚",
                                "starting_mode": "natural",
                                "romance_enabled": False,
                                "expected_version": None,
                                "confirm": True,
                            },
                        },
                    },
                    {
                        "jsonrpc": "2.0",
                        "id": 6,
                        "method": "tools/call",
                        "params": {
                            "name": "save_companion_persona",
                            "arguments": {
                                "description": "不应保存",
                                "expected_version": None,
                                "confirm": False,
                            },
                        },
                    },
                ],
            )

            by_id = {item["id"]: item for item in responses}
            saved = by_id[5]["result"]
            self.assertTrue(saved["structuredContent"]["persona"]["configured"])
            self.assertIn("已保存", saved["content"][0]["text"])
            self.assertEqual(by_id[6]["error"]["code"], -32602)
            profile = (home / "profiles" / "default.toml").read_text("utf-8")
            self.assertIn('display_name = "岚"', profile)
            self.assertNotIn("不应保存", profile)
            self.assertNotIn(str(home), json.dumps(saved, ensure_ascii=False))

    def test_plugin_ui_can_confirm_uploaded_png_without_returning_image_or_reference_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve() / "companion-home"
            initialize_profile(
                skill_root=SKILL_ROOT,
                template_id="warm_healer",
                display_name="小禾",
                output=home / "profiles" / "default.toml",
            )
            image_base64 = base64.b64encode(tiny_png()).decode("ascii")
            face_responses = _run_server(
                home,
                [
                    {
                        "jsonrpc": "2.0",
                        "id": 7,
                        "method": "tools/call",
                        "params": {
                            "name": "set_companion_primary_face",
                            "arguments": {
                                "image_base64": image_base64,
                                "adult_authorized": True,
                                "confirm": True,
                            },
                        },
                    }
                ],
            )

            result = face_responses[0]["result"]
            self.assertTrue(result["structuredContent"]["identity"]["ready"])
            snapshot = ProfileStore(
                skill_root=SKILL_ROOT,
                profile_path=home / "profiles" / "default.toml",
            ).read()
            assert snapshot is not None
            self.assertTrue(snapshot.profile.visual.is_locked)
            serialized = json.dumps(result, ensure_ascii=False)
            self.assertNotIn(image_base64, serialized)
            self.assertNotIn("reference_id", serialized)
            self.assertNotIn(str(home), serialized)

    def test_plugin_ui_can_adjust_existing_persona_without_losing_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve() / "companion-home"
            initialize_profile(
                skill_root=SKILL_ROOT,
                template_id="calm_partner",
                display_name="岚",
                romance_enabled=False,
                output=home / "profiles" / "default.toml",
            )
            image_base64 = base64.b64encode(tiny_png()).decode("ascii")
            responses = _run_server(
                home,
                [
                    {
                        "jsonrpc": "2.0",
                        "id": 10,
                        "method": "tools/call",
                        "params": {
                            "name": "set_companion_primary_face",
                            "arguments": {
                                "image_base64": image_base64,
                                "adult_authorized": True,
                                "confirm": True,
                            },
                        },
                    }
                ],
            )
            after_face = ProfileStore(
                skill_root=SKILL_ROOT,
                profile_path=home / "profiles" / "default.toml",
            ).read()
            assert after_face is not None
            persona_responses = _run_server(
                home,
                [
                    {
                        "jsonrpc": "2.0",
                        "id": 11,
                        "method": "tools/call",
                        "params": {
                            "name": "save_companion_persona",
                            "arguments": {
                                "template_id": None,
                                "description": None,
                                "display_name": "阿岚",
                                "starting_mode": "familiar",
                                "romance_enabled": True,
                                "expected_version": after_face.version,
                                "confirm": True,
                            },
                        },
                    },
                ],
            )

            self.assertNotIn("error", persona_responses[0])
            snapshot = ProfileStore(
                skill_root=SKILL_ROOT,
                profile_path=home / "profiles" / "default.toml",
            ).read()
            assert snapshot is not None
            self.assertEqual(snapshot.profile.display_name, "阿岚")
            self.assertEqual(snapshot.profile.template_id, "calm_partner")
            self.assertTrue(snapshot.profile.relationship.romance_enabled)
            self.assertEqual(snapshot.profile.relationship.starting_mode, "familiar")
            self.assertTrue(snapshot.profile.visual.is_locked)
            private_form = persona_responses[0]["result"]["_meta"][
                "companion-kit/ui-state"
            ]["persona_form"]
            self.assertEqual(private_form["display_name"], "阿岚")
            self.assertNotIn(
                "阿岚",
                json.dumps(
                    persona_responses[0]["result"]["structuredContent"],
                    ensure_ascii=False,
                ),
            )

    def test_partial_persona_update_preserves_relationship_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve() / "companion-home"
            initialize_profile(
                skill_root=SKILL_ROOT,
                template_id="calm_partner",
                display_name="岚",
                starting_mode="familiar",
                romance_enabled=True,
                output=home / "profiles" / "default.toml",
            )
            before = ProfileStore(
                skill_root=SKILL_ROOT,
                profile_path=home / "profiles" / "default.toml",
            ).read()
            assert before is not None
            responses = _run_server(
                home,
                [
                    {
                        "jsonrpc": "2.0",
                        "id": 12,
                        "method": "tools/call",
                        "params": {
                            "name": "save_companion_persona",
                            "arguments": {
                                "display_name": "阿岚",
                                "expected_version": before.version,
                                "confirm": True,
                            },
                        },
                    }
                ],
            )

            self.assertNotIn("error", responses[0])
            snapshot = ProfileStore(
                skill_root=SKILL_ROOT,
                profile_path=home / "profiles" / "default.toml",
            ).read()
            assert snapshot is not None
            self.assertEqual(snapshot.profile.display_name, "阿岚")
            self.assertEqual(snapshot.profile.relationship.starting_mode, "familiar")
            self.assertTrue(snapshot.profile.relationship.romance_enabled)

    def test_stale_persona_form_cannot_overwrite_newer_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve() / "companion-home"
            initialize_profile(
                skill_root=SKILL_ROOT,
                template_id="calm_partner",
                display_name="岚",
                output=home / "profiles" / "default.toml",
            )
            store = ProfileStore(
                skill_root=SKILL_ROOT,
                profile_path=home / "profiles" / "default.toml",
            )
            stale = store.read()
            assert stale is not None
            newer = store.save(
                template_id=stale.profile.id,
                display_name="新面板保存的名字",
                expected_version=stale.version,
            )

            responses = _run_server(
                home,
                [
                    {
                        "jsonrpc": "2.0",
                        "id": 14,
                        "method": "tools/call",
                        "params": {
                            "name": "save_companion_persona",
                            "arguments": {
                                "display_name": "旧面板覆盖的名字",
                                "expected_version": stale.version,
                                "confirm": True,
                            },
                        },
                    }
                ],
            )

            self.assertEqual(responses[0]["error"]["code"], -32602)
            self.assertIn("刷新人物面板", responses[0]["error"]["message"])
            current = store.read()
            assert current is not None
            self.assertEqual(current.version, newer.version)
            self.assertEqual(current.profile.display_name, "新面板保存的名字")

    def test_unknown_tool_returns_protocol_error_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            responses = _run_server(
                Path(tmp).resolve() / "companion-home",
                [
                    {
                        "jsonrpc": "2.0",
                        "id": 9,
                        "method": "tools/call",
                        "params": {"name": "delete_everything", "arguments": {}},
                    }
                ],
            )

            self.assertEqual(responses[0]["id"], 9)
            self.assertEqual(responses[0]["error"]["code"], -32602)


if __name__ == "__main__":
    unittest.main()
