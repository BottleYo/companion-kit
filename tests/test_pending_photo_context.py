from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest

from companion_kit.pending_photo_context import PendingPhotoContextStore


class PendingPhotoContextStoreTests(unittest.TestCase):
    def test_context_is_session_and_persona_scoped_and_consumed_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "pending-photo-contexts"
            store = PendingPhotoContextStore(root=root)
            created = store.remember(
                profile_id="companion",
                session_id="task-one",
                source="daily_look",
                context_ref="look_" + "a" * 24,
            )

            self.assertEqual(created.source, "daily_look")
            self.assertIsNone(
                store.peek(profile_id="companion", session_id="task-two")
            )
            self.assertEqual(
                store.consume(profile_id="companion", session_id="task-one"),
                created,
            )
            self.assertIsNone(
                store.consume(profile_id="companion", session_id="task-one")
            )

            store.remember(
                profile_id="companion",
                session_id="task-one",
                source="photo_setup",
            )
            self.assertIsNone(
                store.peek(profile_id="another-persona", session_id="task-one")
            )
            self.assertIsNone(
                store.peek(profile_id="companion", session_id="task-one")
            )

    def test_expired_context_is_not_returned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "pending-photo-contexts"
            start = datetime(2026, 8, 22, 10, 0, tzinfo=UTC)
            writer = PendingPhotoContextStore(root=root, clock=lambda: start)
            writer.remember(
                profile_id="companion",
                session_id="task-one",
                source="photo_setup",
            )

            expired = PendingPhotoContextStore(
                root=root,
                clock=lambda: start + timedelta(minutes=11),
            )
            self.assertIsNone(
                expired.peek(profile_id="companion", session_id="task-one")
            )

    def test_store_does_not_persist_raw_session_or_persona_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "pending-photo-contexts"
            store = PendingPhotoContextStore(root=root)
            store.remember(
                profile_id="private-persona-name",
                session_id="private-task-id",
                source="daily_look",
                context_ref="look_" + "b" * 24,
            )

            record = next(root.glob("*.json"))
            serialized = record.read_text(encoding="utf-8")
            payload = json.loads(serialized)
            self.assertEqual(
                set(payload),
                {
                    "schema_version",
                    "session_digest",
                    "profile_digest",
                    "created_at",
                    "source",
                    "context_ref",
                },
            )
            self.assertNotIn("private-task-id", serialized)
            self.assertNotIn("private-persona-name", serialized)
            self.assertNotIn("private-task-id", record.name)
            if os.name != "nt":
                self.assertEqual(record.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
