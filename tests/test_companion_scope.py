from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from companion_kit.codex_turn import CodexScopeCommand, classify_scope_command
from companion_kit.companion_scope import CompanionScopeError, CompanionScopeStore


class CompanionScopeStoreTests(unittest.TestCase):
    def test_missing_store_read_is_side_effect_free(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "system" / "codex-scopes"
            store = CompanionScopeStore(root=root)

            self.assertFalse(store.is_bound("ordinary-task"))
            self.assertEqual(store.count(), 0)
            self.assertFalse(root.exists())

    def test_bind_persists_only_hmac_and_unbinds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "system" / "codex-scopes"
            store = CompanionScopeStore(root=root)
            session_id = "private-codex-task-id"

            self.assertTrue(store.bind(session_id))
            self.assertFalse(store.bind(session_id))
            self.assertTrue(store.is_bound(session_id))
            self.assertEqual(store.count(), 1)

            key = root / "scope-key"
            bindings = tuple((root / "bindings").glob("*.json"))
            self.assertEqual(len(key.read_bytes()), 32)
            self.assertEqual(len(bindings), 1)
            payload = json.loads(bindings[0].read_text(encoding="utf-8"))
            self.assertEqual(set(payload), {"schema_version", "scope_digest"})
            self.assertRegex(payload["scope_digest"], r"^[0-9a-f]{64}$")
            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn(session_id, serialized)
            self.assertNotIn(session_id, bindings[0].name)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(bindings[0].stat().st_mode), 0o600)

            self.assertTrue(store.unbind(session_id))
            self.assertFalse(store.unbind(session_id))
            self.assertFalse(store.is_bound(session_id))
            self.assertEqual(store.count(), 0)

    def test_corrupt_binding_is_rejected_instead_of_treated_as_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "system" / "codex-scopes"
            store = CompanionScopeStore(root=root)
            store.bind("task-one")
            binding = next((root / "bindings").glob("*.json"))
            binding.write_text("{broken", encoding="utf-8")

            with self.assertRaises(CompanionScopeError):
                store.is_bound("task-one")
            with self.assertRaises(CompanionScopeError):
                store.count()

    def test_capacity_refuses_new_binding_without_silent_eviction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "system" / "codex-scopes"
            store = CompanionScopeStore(root=root, max_bindings=2)
            store.bind("task-one")
            store.bind("task-two")

            with self.assertRaises(CompanionScopeError):
                store.bind("task-three")

            self.assertTrue(store.is_bound("task-one"))
            self.assertTrue(store.is_bound("task-two"))
            self.assertFalse(store.is_bound("task-three"))

    @unittest.skipIf(os.name == "nt", "Windows 测试环境可能没有创建符号链接的权限")
    def test_dangling_scope_symlink_is_rejected_instead_of_treated_as_healthy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            system = Path(tmp).resolve() / "system"
            system.mkdir()
            root = system / "codex-scopes"
            store = CompanionScopeStore(root=root)
            root.symlink_to(system / "missing-scope", target_is_directory=True)

            with self.assertRaises(CompanionScopeError):
                store.is_bound("ordinary-task")
            with self.assertRaises(CompanionScopeError):
                store.count()

    def test_clear_all_requires_explicit_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "system" / "codex-scopes"
            store = CompanionScopeStore(root=root)
            store.bind("task-one")

            with self.assertRaises(CompanionScopeError):
                store.clear_all(confirm=False)

            self.assertEqual(store.clear_all(confirm=True), 1)
            self.assertFalse(store.is_bound("task-one"))

    def test_confirmed_clear_recovers_corrupt_binding_and_missing_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "system" / "codex-scopes"
            store = CompanionScopeStore(root=root)
            store.bind("task-one")
            binding = next((root / "bindings").glob("*.json"))
            binding.write_text("{broken", encoding="utf-8")
            (root / "scope-key").unlink()

            with self.assertRaises(CompanionScopeError):
                store.count()
            self.assertEqual(store.clear_all(confirm=True), 1)
            self.assertEqual(store.count(), 0)
            self.assertTrue(store.bind("new-task"))
            self.assertTrue(store.is_bound("new-task"))


class CodexScopeIntentTests(unittest.TestCase):
    def test_scope_commands_are_explicit_and_narrow(self) -> None:
        bind_phrases = (
            "把这个任务设为陪伴任务",
            "进入陪伴任务",
            "Enable companion task",
        )
        unbind_phrases = (
            "退出陪伴任务",
            "把当前任务恢复为普通任务",
            "Disable companion task",
        )
        pass_through = (
            "陪我聊一会儿",
            "给产品做一个陪伴模式的设置页",
            "分析一下陪伴任务怎么实现",
            "生成一张人物照片",
        )

        for phrase in bind_phrases:
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    classify_scope_command(phrase),
                    CodexScopeCommand.BIND,
                )
        for phrase in unbind_phrases:
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    classify_scope_command(phrase),
                    CodexScopeCommand.UNBIND,
                )
        for phrase in pass_through:
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    classify_scope_command(phrase),
                    CodexScopeCommand.PASS_THROUGH,
                )

    def test_bind_plus_photo_is_classified_as_bind_first(self) -> None:
        self.assertEqual(
            classify_scope_command("把这个任务设为陪伴任务，然后拍张自拍"),
            CodexScopeCommand.BIND,
        )


if __name__ == "__main__":
    unittest.main()
