from datetime import UTC, datetime, timedelta
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier
import unittest

from companion_kit.relationship import (
    RelationshipEvent,
    RelationshipEventType,
    RelationshipPolicy,
)
from companion_kit.state_store import (
    STORE_SCHEMA_VERSION,
    RelationshipStore,
    StoreError,
    StoreVersionError,
)


NOW = datetime(2026, 8, 4, 0, 0, tzinfo=UTC)


def make_event(index: int, event_type: RelationshipEventType) -> RelationshipEvent:
    return RelationshipEvent(
        event_id=f"event-{index:04d}",
        event_type=event_type,
        occurred_at=NOW + timedelta(hours=(index - 1) * 6),
        confidence=1.0,
        source_host="codex",
        scope_id="b" * 32,
        reason_code="explicit_user_signal",
    )


class StateStoreTests(unittest.TestCase):
    def test_concurrent_first_open_of_empty_database_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "state.sqlite3"
            barrier = Barrier(2)

            class CoordinatedStore(RelationshipStore):
                def _create_schema_v2(self, connection: sqlite3.Connection) -> None:
                    barrier.wait(timeout=5)
                    RelationshipStore._create_schema_v2(connection)

            def first_open(_: int):
                return CoordinatedStore(database).get_state("relationship-demo")

            with ThreadPoolExecutor(max_workers=2) as executor:
                states = list(executor.map(first_open, range(2)))

            self.assertEqual(
                {
                    (state.familiarity, state.trust, state.closeness, state.revision)
                    for state in states
                },
                {(5, 10, 5, 0)},
            )
            connection = sqlite3.connect(database)
            version_rows = connection.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchall()
            connection.close()
            self.assertEqual(version_rows, [(str(STORE_SCHEMA_VERSION),)])

    def test_starting_policy_controls_uninitialized_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RelationshipStore(
                Path(tmp) / "state.sqlite3",
                policy=RelationshipPolicy(starting_mode="familiar"),
            )

            state = store.get_state("relationship-demo")

            self.assertEqual(
                (state.familiarity, state.trust, state.closeness),
                (20, 25, 15),
            )

    def test_same_event_id_is_scoped_to_one_relationship(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RelationshipStore(Path(tmp) / "state.sqlite3")
            candidate = make_event(1, RelationshipEventType.WARM_EXCHANGE)

            first = store.apply_event("relationship-one", candidate)
            second = store.apply_event("relationship-two", candidate)

            self.assertTrue(first.applied)
            self.assertTrue(second.applied)
            self.assertFalse(second.duplicate)

    def test_event_is_idempotent_and_daily_growth_is_capped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RelationshipStore(Path(tmp) / "state.sqlite3")
            first = make_event(1, RelationshipEventType.WARM_EXCHANGE)

            applied = store.apply_event("relationship-demo", first)
            duplicate = store.apply_event("relationship-demo", first)
            for index in range(2, 5):
                store.apply_event(
                    "relationship-demo",
                    make_event(index, RelationshipEventType.WARM_EXCHANGE),
                )
            state = store.get_state("relationship-demo")

            self.assertTrue(applied.applied)
            self.assertTrue(duplicate.duplicate)
            self.assertEqual(duplicate.state.revision, applied.state.revision)
            self.assertEqual(state.familiarity, 8)
            self.assertEqual(state.closeness, 9)

    def test_export_contains_state_but_no_event_or_source_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RelationshipStore(Path(tmp) / "state.sqlite3")
            store.apply_event(
                "relationship-demo",
                make_event(1, RelationshipEventType.EXPLICIT_APPRECIATION),
            )

            payload = store.export_relationship("relationship-demo")
            encoded = json.dumps(payload, ensure_ascii=False)

            self.assertEqual(payload["schema_version"], STORE_SCHEMA_VERSION)
            self.assertIn("state", payload)
            self.assertNotIn("events", payload)
            self.assertNotIn("source_host", encoded)
            self.assertNotIn("scope_id", encoded)
            self.assertNotIn(str(Path(tmp)), encoded)

    def test_atmosphere_is_scoped_and_expires(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RelationshipStore(Path(tmp) / "state.sqlite3")
            store.apply_event(
                "relationship-demo",
                make_event(1, RelationshipEventType.EXPLICIT_DISCOMFORT),
            )

            active = store.get_atmosphere(
                "relationship-demo",
                "codex",
                "b" * 32,
                now=NOW + timedelta(hours=1),
            )
            expired = store.get_atmosphere(
                "relationship-demo",
                "codex",
                "b" * 32,
                now=NOW + timedelta(days=2),
            )

            self.assertEqual(active.value, "strained")
            self.assertEqual(expired.value, "neutral")

    def test_event_and_atmosphere_namespaces_include_source_host(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RelationshipStore(Path(tmp) / "state.sqlite3")
            first = make_event(1, RelationshipEventType.EXPLICIT_DISCOMFORT)
            second = replace(
                first,
                source_host="hermes",
                occurred_at=first.occurred_at + timedelta(hours=1),
            )

            codex = store.apply_event("relationship-demo", first)
            hermes = store.apply_event("relationship-demo", second)

            self.assertTrue(codex.applied)
            self.assertTrue(hermes.applied)
            self.assertFalse(hermes.duplicate)
            self.assertEqual(
                store.get_atmosphere(
                    "relationship-demo",
                    "codex",
                    "b" * 32,
                    now=NOW + timedelta(hours=2),
                ),
                "strained",
            )
            self.assertEqual(
                store.get_atmosphere(
                    "relationship-demo",
                    "openclaw",
                    "b" * 32,
                    now=NOW + timedelta(hours=2),
                ),
                "neutral",
            )

    def test_same_id_with_changed_payload_is_idempotency_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RelationshipStore(Path(tmp) / "state.sqlite3")
            first = make_event(1, RelationshipEventType.WARM_EXCHANGE)
            store.apply_event("relationship-demo", first)

            conflict = store.apply_event(
                "relationship-demo",
                replace(first, reason_code="different_reason"),
            )

            self.assertFalse(conflict.applied)
            self.assertFalse(conflict.duplicate)
            self.assertTrue(conflict.conflict)
            self.assertEqual(conflict.reason, "idempotency_conflict")

    def test_database_and_parent_permissions_are_private(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX permissions are not available on Windows")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "private-state"
            database = root / "state.sqlite3"
            RelationshipStore(database).get_state("relationship-demo")

            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertEqual(database.stat().st_mode & 0o777, 0o600)

    def test_delete_removes_product_controlled_sqlite_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "state.sqlite3"
            store = RelationshipStore(database)
            store.apply_event(
                "relationship-demo",
                make_event(1, RelationshipEventType.WARM_EXCHANGE),
            )
            Path(f"{database}-wal").touch(exist_ok=True)
            Path(f"{database}-shm").touch(exist_ok=True)
            Path(f"{database}-journal").touch(exist_ok=True)

            removed = store.delete_product_data()

            self.assertFalse(database.exists())
            self.assertFalse(Path(f"{database}-wal").exists())
            self.assertFalse(Path(f"{database}-shm").exists())
            self.assertFalse(Path(f"{database}-journal").exists())
            self.assertIn(database.name, removed)

    def test_version_one_database_migrates_atomically_and_preserves_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "state.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO meta VALUES ('schema_version', '1');
                CREATE TABLE relationship_state (
                    relationship_id TEXT PRIMARY KEY,
                    familiarity INTEGER NOT NULL,
                    trust INTEGER NOT NULL,
                    closeness INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE relationship_event (
                    relationship_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_host TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    applied INTEGER NOT NULL,
                    result_reason TEXT NOT NULL,
                    delta_familiarity INTEGER NOT NULL,
                    delta_trust INTEGER NOT NULL,
                    delta_closeness INTEGER NOT NULL,
                    PRIMARY KEY (relationship_id, event_id)
                );
                CREATE INDEX relationship_event_daily_idx
                    ON relationship_event(relationship_id, occurred_at);
                CREATE INDEX relationship_event_type_idx
                    ON relationship_event(relationship_id, event_type, occurred_at);
                CREATE TABLE relationship_atmosphere (
                    relationship_id TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    value TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    PRIMARY KEY (relationship_id, scope_id)
                );
                """
            )
            connection.execute(
                "INSERT INTO relationship_state VALUES (?, ?, ?, ?, ?, ?)",
                ("relationship-demo", 7, 8, 9, 1, NOW.isoformat()),
            )
            old_event = make_event(1, RelationshipEventType.EXPLICIT_DISCOMFORT)
            connection.execute(
                """
                INSERT INTO relationship_event VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    "relationship-demo",
                    old_event.event_id,
                    old_event.event_type.value,
                    old_event.occurred_at.isoformat(),
                    old_event.confidence,
                    "hermes",
                    old_event.scope_id,
                    old_event.reason_code,
                    1,
                    "event_applied",
                    0,
                    -2,
                    -1,
                ),
            )
            rejected_event = replace(
                old_event,
                event_id="event-0002",
                occurred_at=NOW + timedelta(hours=1),
                source_host="codex",
                confidence=0.25,
            )
            connection.execute(
                """
                INSERT INTO relationship_event VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    "relationship-demo",
                    rejected_event.event_id,
                    rejected_event.event_type.value,
                    rejected_event.occurred_at.isoformat(),
                    rejected_event.confidence,
                    rejected_event.source_host,
                    rejected_event.scope_id,
                    rejected_event.reason_code,
                    0,
                    "confidence_below_threshold",
                    0,
                    0,
                    0,
                ),
            )
            connection.execute(
                "INSERT INTO relationship_atmosphere VALUES (?, ?, ?, ?)",
                (
                    "relationship-demo",
                    old_event.scope_id,
                    "strained",
                    (NOW + timedelta(hours=24)).isoformat(),
                ),
            )
            connection.commit()
            connection.close()

            store = RelationshipStore(database)
            state = store.get_state("relationship-demo")
            atmosphere = store.get_atmosphere(
                "relationship-demo",
                "hermes",
                old_event.scope_id,
                now=NOW + timedelta(hours=1),
            )
            rejected_host_atmosphere = store.get_atmosphere(
                "relationship-demo",
                "codex",
                old_event.scope_id,
                now=NOW + timedelta(hours=1),
            )
            cross_host = store.apply_event(
                "relationship-demo",
                replace(
                    old_event,
                    source_host="codex",
                    occurred_at=NOW + timedelta(hours=6),
                ),
            )

            self.assertEqual(
                (state.familiarity, state.trust, state.closeness, state.revision),
                (7, 8, 9, 1),
            )
            self.assertEqual(atmosphere, "strained")
            self.assertEqual(rejected_host_atmosphere, "neutral")
            self.assertFalse(cross_host.duplicate)
            connection = sqlite3.connect(database)
            version = connection.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            atmosphere_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(relationship_atmosphere)"
                ).fetchall()
            }
            connection.close()
            self.assertEqual(int(version), STORE_SCHEMA_VERSION)
            self.assertIn("source_host", atmosphere_columns)

    def test_failed_version_one_migration_rolls_back_every_schema_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "state.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO meta VALUES ('schema_version', '1');
                CREATE TABLE relationship_state (
                    relationship_id TEXT PRIMARY KEY,
                    familiarity INTEGER NOT NULL,
                    trust INTEGER NOT NULL,
                    closeness INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE relationship_event (
                    relationship_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_host TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    applied INTEGER NOT NULL,
                    result_reason TEXT NOT NULL,
                    delta_familiarity INTEGER NOT NULL,
                    delta_trust INTEGER NOT NULL,
                    delta_closeness INTEGER NOT NULL,
                    PRIMARY KEY (relationship_id, event_id)
                );
                CREATE INDEX relationship_event_daily_idx
                    ON relationship_event(relationship_id, occurred_at);
                CREATE INDEX relationship_event_type_idx
                    ON relationship_event(relationship_id, event_type, occurred_at);
                CREATE TABLE relationship_atmosphere (
                    relationship_id TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    value TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    PRIMARY KEY (relationship_id, scope_id)
                );
                INSERT INTO relationship_atmosphere VALUES (
                    'relationship-demo',
                    'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                    'strained',
                    '2026-08-05T00:00:00+00:00'
                );
                """
            )
            connection.close()

            with self.assertRaisesRegex(StoreVersionError, "缺少可验证"):
                RelationshipStore(database).get_state("relationship-demo")

            connection = sqlite3.connect(database)
            version = connection.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            atmosphere_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(relationship_atmosphere)"
                ).fetchall()
            }
            migration_tables = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name LIKE '%_v2_migration'
                """
            ).fetchall()
            atmosphere_count = connection.execute(
                "SELECT COUNT(*) FROM relationship_atmosphere"
            ).fetchone()[0]
            connection.close()

            self.assertEqual(version, "1")
            self.assertNotIn("source_host", atmosphere_columns)
            self.assertEqual(migration_tables, [])
            self.assertEqual(atmosphere_count, 1)

    def test_rejects_symlinked_database_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside.sqlite3"
            outside.touch()
            link = root / "state.sqlite3"
            link.symlink_to(outside)

            with self.assertRaisesRegex(StoreError, "符号链接"):
                RelationshipStore(link)

    def test_newer_schema_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "state.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT INTO meta VALUES ('schema_version', '999')")
            connection.commit()
            connection.close()

            with self.assertRaises(StoreVersionError):
                RelationshipStore(database).get_state("relationship-demo")


if __name__ == "__main__":
    unittest.main()
