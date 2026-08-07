from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile
import unittest

from companion_kit.hook_health import (
    HookHealthStore,
    POST_TOOL_USE,
    SESSION_START,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _copy_hook_bundle(root: Path, *, version: str = "0.7.0-dev.6") -> Path:
    plugin_root = root / "plugin"
    (plugin_root / ".codex-plugin").mkdir(parents=True)
    (plugin_root / "hooks").mkdir()
    (plugin_root / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "companion-kit", "version": version}),
        encoding="utf-8",
    )
    for name in ("hooks.json", "codex_context.py", "codex_image_receipt.py"):
        source = PROJECT_ROOT / "hooks" / name
        (plugin_root / "hooks" / name).write_bytes(source.read_bytes())
    return plugin_root


class HookHealthStoreTests(unittest.TestCase):
    def test_success_receipt_contains_only_minimal_non_private_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            plugin_root = _copy_hook_bundle(root)
            health_root = root / "companion-home" / "system" / "hook-health"
            store = HookHealthStore(
                root=health_root,
                plugin_root=plugin_root,
                clock=lambda: datetime(2026, 8, 6, 9, 30, tzinfo=UTC),
            )

            receipt = store.record_success(SESSION_START)
            payload = json.loads((health_root / "session_start.json").read_text("utf-8"))

            self.assertEqual(
                set(payload),
                {
                    "schema_version",
                    "plugin_version",
                    "hook_bundle_digest",
                    "hook_type",
                    "last_success_at",
                },
            )
            self.assertEqual(payload["hook_type"], SESSION_START)
            self.assertEqual(payload["plugin_version"], "0.7.0-dev.6")
            self.assertEqual(payload["last_success_at"], receipt.last_success_at)
            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn(str(root), serialized)
            self.assertNotIn("session_id", serialized)
            self.assertNotIn("prompt", serialized)
            self.assertTrue(store.status(SESSION_START).verified)

    def test_missing_health_read_is_side_effect_free(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            plugin_root = _copy_hook_bundle(root)
            health_root = root / "companion-home" / "system" / "hook-health"
            store = HookHealthStore(root=health_root, plugin_root=plugin_root)

            status = store.status(SESSION_START)

            self.assertEqual(status.state, "missing")
            self.assertFalse(status.verified)
            self.assertFalse(health_root.exists())

    def test_version_or_bundle_change_invalidates_health_and_review_ack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            plugin_root = _copy_hook_bundle(root, version="0.7.0-dev.5")
            health_root = root / "companion-home" / "system" / "hook-health"
            old_store = HookHealthStore(root=health_root, plugin_root=plugin_root)
            old_store.acknowledge_review()
            old_store.record_success(SESSION_START)
            old_store.record_success(POST_TOOL_USE)

            manifest = plugin_root / ".codex-plugin" / "plugin.json"
            manifest.write_text(
                json.dumps({"name": "companion-kit", "version": "0.7.0-dev.6"}),
                encoding="utf-8",
            )
            current_store = HookHealthStore(root=health_root, plugin_root=plugin_root)

            self.assertEqual(current_store.review_status().state, "stale")
            self.assertEqual(current_store.status(SESSION_START).state, "stale")
            self.assertEqual(current_store.status(POST_TOOL_USE).state, "stale")

            # 即使版本没变，只要 Hook bundle 内容变化也必须重新审核。
            current_store.acknowledge_review()
            current_store.record_success(SESSION_START)
            context_hook = plugin_root / "hooks" / "codex_context.py"
            context_hook.write_text(
                context_hook.read_text(encoding="utf-8") + "\n# changed hook bundle\n",
                encoding="utf-8",
            )
            changed_store = HookHealthStore(root=health_root, plugin_root=plugin_root)
            self.assertEqual(changed_store.review_status().state, "stale")
            self.assertEqual(changed_store.status(SESSION_START).state, "stale")

    def test_cachebuster_suffix_does_not_invalidate_same_release_and_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            plugin_root = _copy_hook_bundle(root, version="0.7.0-dev.6+codex.local-a")
            health_root = root / "health"
            installed_store = HookHealthStore(root=health_root, plugin_root=plugin_root)
            installed_store.record_success(SESSION_START)

            manifest = plugin_root / ".codex-plugin" / "plugin.json"
            manifest.write_text(
                json.dumps({"name": "companion-kit", "version": "0.7.0-dev.6"}),
                encoding="utf-8",
            )
            source_store = HookHealthStore(root=health_root, plugin_root=plugin_root)

            self.assertTrue(source_store.status(SESSION_START).verified)


if __name__ == "__main__":
    unittest.main()
