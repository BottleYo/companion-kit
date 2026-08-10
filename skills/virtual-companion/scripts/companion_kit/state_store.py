from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys

from .relationship import (
    Atmosphere,
    AtmosphereState,
    RelationshipDelta,
    RelationshipEvent,
    RelationshipError,
    RelationshipPolicy,
    RelationshipReducer,
    RelationshipState,
    ReductionContext,
    active_atmosphere,
    validate_scope_id,
    validate_source_host,
)


STORE_SCHEMA_VERSION = 2
_RELATIONSHIP_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{2,63}$")


class StoreError(ValueError):
    """本地关系状态无法安全读取或写入。"""


class StoreVersionError(StoreError):
    """本地数据库版本与当前核心不兼容。"""


@dataclass(frozen=True)
class StoreApplyResult:
    state: RelationshipState
    applied: bool
    duplicate: bool
    reason: str
    conflict: bool = False


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise StoreError("关系时间必须包含时区")
    return value.astimezone(UTC).isoformat()


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise StoreError("关系数据库包含无效时间") from exc
    if parsed.tzinfo is None:
        raise StoreError("关系数据库时间缺少时区")
    return parsed.astimezone(UTC)


def _validate_relationship_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not _RELATIONSHIP_ID_RE.fullmatch(normalized):
        raise StoreError("relationship_id 格式无效")
    return normalized


def _safe_database_path(raw: str | Path) -> Path:
    raw_path = Path(raw).expanduser()
    if ".." in raw_path.parts:
        raise StoreError("状态库路径不能包含 ..")
    path = Path(os.path.abspath(raw_path))
    # macOS 的临时目录可能经过系统固定别名。只规范化系统管理的临时
    # 前缀；其余符号链接仍逐段拒绝，避免用户可控链接重定向数据库。
    if sys.platform == "darwin":
        filesystem_root = Path(path.anchor)
        for alias, target in (
            (
                filesystem_root / "var",
                filesystem_root / "private" / "var",
            ),
            (
                filesystem_root / "tmp",
                filesystem_root / "private" / "tmp",
            ),
        ):
            try:
                relative = path.relative_to(alias)
            except ValueError:
                continue
            path = target / relative
            break
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            break
        except OSError as exc:
            raise StoreError(f"无法检查状态库路径：{exc}") from exc
        if stat.S_ISLNK(mode):
            raise StoreError("状态库路径不能经过符号链接")
    return path


class RelationshipStore:
    """SQLite 单一写入真相源；不保存聊天正文或完整提示词。"""

    def __init__(
        self,
        database_path: str | Path,
        *,
        policy: RelationshipPolicy | None = None,
        connection_timeout: float = 5.0,
    ) -> None:
        try:
            normalized_timeout = float(connection_timeout)
        except (TypeError, ValueError) as exc:
            raise StoreError("关系数据库等待时间无效") from exc
        if not 0 < normalized_timeout <= 60:
            raise StoreError("关系数据库等待时间无效")
        self.database_path = _safe_database_path(database_path)
        self.policy = policy or RelationshipPolicy()
        self._connection_timeout = normalized_timeout
        if self.database_path.exists() and not self.database_path.is_file():
            raise StoreError("状态库目标必须是普通文件")

    def _connect(self) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = None
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name != "nt":
                self.database_path.parent.chmod(0o700)
            _safe_database_path(self.database_path)
            connection = sqlite3.connect(
                self.database_path,
                timeout=self._connection_timeout,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(
                f"PRAGMA busy_timeout = {max(1, int(self._connection_timeout * 1000))}"
            )
            self._ensure_schema(connection)
            journal = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            if journal is None or str(journal[0]).casefold() != "wal":
                raise StoreError("当前文件系统无法启用安全的 WAL 事务模式")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA foreign_keys = ON")
            if os.name != "nt":
                self.database_path.chmod(0o600)
            return connection
        except (OSError, sqlite3.Error, ValueError) as exc:
            if connection is not None:
                connection.close()
            if isinstance(exc, StoreError):
                raise
            raise StoreError(f"无法打开本地关系状态：{exc}") from exc

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        has_meta = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
        ).fetchone()
        if not has_meta:
            self._create_schema_v2(connection)

        row = connection.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            raise StoreVersionError("关系数据库缺少 schema_version")
        try:
            version = int(row["value"])
        except (TypeError, ValueError) as exc:
            raise StoreVersionError("关系数据库版本无效") from exc
        if version > STORE_SCHEMA_VERSION:
            raise StoreVersionError("关系数据库版本较新，请使用兼容版本后再继续")
        if version == 1:
            self._migrate_v1_to_v2(connection)
        elif version != STORE_SCHEMA_VERSION:
            raise StoreVersionError("关系数据库版本较旧且没有安全迁移路径")
        self._verify_schema_v2(connection)

    @staticmethod
    def _create_schema_v2(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            # 多个进程可能同时在事务外观察到空库。取得写锁后
            # 必须重查，让后到的进程复用已创建的完整 schema。
            has_meta = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
            ).fetchone()
            if has_meta:
                connection.execute("COMMIT")
                return

            connection.execute(
                """
                CREATE TABLE meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', '2')"
            )
            connection.execute(
                """
                CREATE TABLE relationship_state (
                    relationship_id TEXT PRIMARY KEY,
                    familiarity INTEGER NOT NULL,
                    trust INTEGER NOT NULL,
                    closeness INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
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
                    PRIMARY KEY (relationship_id, source_host, event_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX relationship_event_daily_idx
                ON relationship_event(relationship_id, occurred_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX relationship_event_type_idx
                ON relationship_event(relationship_id, event_type, occurred_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE relationship_atmosphere (
                    relationship_id TEXT NOT NULL,
                    source_host TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    value TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    PRIMARY KEY (relationship_id, source_host, scope_id)
                )
                """
            )
            connection.execute("COMMIT")
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    @staticmethod
    def _table_info(
        connection: sqlite3.Connection,
        table: str,
    ) -> tuple[sqlite3.Row, ...]:
        return tuple(connection.execute(f'PRAGMA table_info("{table}")').fetchall())

    def _verify_schema_v2(self, connection: sqlite3.Connection) -> None:
        required = {
            "relationship_state": {
                "relationship_id",
                "familiarity",
                "trust",
                "closeness",
                "revision",
                "updated_at",
            },
            "relationship_event": {
                "relationship_id",
                "source_host",
                "event_id",
                "event_type",
                "occurred_at",
                "confidence",
                "scope_id",
                "reason_code",
                "applied",
                "result_reason",
                "delta_familiarity",
                "delta_trust",
                "delta_closeness",
            },
            "relationship_atmosphere": {
                "relationship_id",
                "source_host",
                "scope_id",
                "value",
                "expires_at",
            },
        }
        expected_primary_keys = {
            "relationship_state": ("relationship_id",),
            "relationship_event": ("relationship_id", "source_host", "event_id"),
            "relationship_atmosphere": (
                "relationship_id",
                "source_host",
                "scope_id",
            ),
        }
        for table, columns in required.items():
            info = self._table_info(connection, table)
            names = {str(row["name"]) for row in info}
            if not info or not columns.issubset(names):
                raise StoreVersionError(f"关系数据库表结构不完整：{table}")
            primary_key = tuple(
                str(row["name"])
                for row in sorted(info, key=lambda item: int(item["pk"]))
                if int(row["pk"]) > 0
            )
            if primary_key != expected_primary_keys[table]:
                raise StoreVersionError(f"关系数据库主键结构不兼容：{table}")

    def _migrate_v1_to_v2(self, connection: sqlite3.Connection) -> None:
        required_v1 = {
            "relationship_state": {
                "relationship_id",
                "familiarity",
                "trust",
                "closeness",
                "revision",
                "updated_at",
            },
            "relationship_event": {
                "relationship_id",
                "event_id",
                "event_type",
                "occurred_at",
                "confidence",
                "source_host",
                "scope_id",
                "reason_code",
                "applied",
                "result_reason",
                "delta_familiarity",
                "delta_trust",
                "delta_closeness",
            },
            "relationship_atmosphere": {
                "relationship_id",
                "scope_id",
                "value",
                "expires_at",
            },
        }
        try:
            connection.execute("BEGIN IMMEDIATE")
            # 版本检查与整个 DDL 迁移共用同一写事务，避免两个
            # 宿主进程同时首次打开旧库时重复迁移。
            current = connection.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
            if current is None:
                raise StoreVersionError("关系数据库缺少 schema_version")
            try:
                current_version = int(current["value"])
            except (TypeError, ValueError) as exc:
                raise StoreVersionError("关系数据库版本无效") from exc
            if current_version == STORE_SCHEMA_VERSION:
                connection.execute("COMMIT")
                return
            if current_version != 1:
                raise StoreVersionError("关系数据库版本已变化，无法安全迁移")

            for table, columns in required_v1.items():
                info = self._table_info(connection, table)
                names = {str(row["name"]) for row in info}
                if not info or not columns.issubset(names):
                    raise StoreVersionError(
                        f"版本 1 关系数据库无法安全迁移：{table}"
                    )
            for row in connection.execute(
                "SELECT DISTINCT source_host FROM relationship_event"
            ).fetchall():
                try:
                    validate_source_host(str(row["source_host"]))
                except RelationshipError as exc:
                    raise StoreVersionError("版本 1 数据包含未知宿主") from exc

            connection.execute(
                """
                CREATE TABLE relationship_event_v2_migration (
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
                    PRIMARY KEY (relationship_id, source_host, event_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE relationship_atmosphere_v2_migration (
                    relationship_id TEXT NOT NULL,
                    source_host TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    value TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    PRIMARY KEY (relationship_id, source_host, scope_id)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO relationship_event_v2_migration
                SELECT relationship_id, event_id, event_type, occurred_at,
                       confidence, source_host, scope_id, reason_code,
                       applied, result_reason, delta_familiarity,
                       delta_trust, delta_closeness
                FROM relationship_event
                """
            )
            connection.execute(
                """
                INSERT INTO relationship_atmosphere_v2_migration
                SELECT a.relationship_id,
                       (
                           SELECT e.source_host
                           FROM relationship_event AS e
                           WHERE e.relationship_id = a.relationship_id
                             AND e.scope_id = a.scope_id
                             AND (
                                 e.applied = 1
                                 OR e.result_reason = 'daily_delta_cap_reached'
                             )
                           ORDER BY e.occurred_at DESC, e.rowid DESC
                           LIMIT 1
                       ),
                       a.scope_id, a.value, a.expires_at
                FROM relationship_atmosphere AS a
                WHERE EXISTS (
                    SELECT 1 FROM relationship_event AS e
                    WHERE e.relationship_id = a.relationship_id
                      AND e.scope_id = a.scope_id
                      AND (
                          e.applied = 1
                          OR e.result_reason = 'daily_delta_cap_reached'
                      )
                )
                """
            )
            old_atmosphere_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM relationship_atmosphere"
                ).fetchone()[0]
            )
            migrated_atmosphere_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM relationship_atmosphere_v2_migration"
                ).fetchone()[0]
            )
            if old_atmosphere_count != migrated_atmosphere_count:
                raise StoreVersionError("版本 1 气氛数据缺少可验证的宿主来源")
            connection.execute("DROP TABLE relationship_event")
            connection.execute("DROP TABLE relationship_atmosphere")
            connection.execute(
                """
                ALTER TABLE relationship_event_v2_migration
                RENAME TO relationship_event
                """
            )
            connection.execute(
                """
                ALTER TABLE relationship_atmosphere_v2_migration
                RENAME TO relationship_atmosphere
                """
            )
            connection.execute(
                """
                CREATE INDEX relationship_event_daily_idx
                ON relationship_event(relationship_id, occurred_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX relationship_event_type_idx
                ON relationship_event(relationship_id, event_type, occurred_at)
                """
            )
            connection.execute(
                "UPDATE meta SET value = '2' WHERE key = 'schema_version'"
            )
            connection.execute("COMMIT")
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def _state_from_row(
        self,
        row: sqlite3.Row | None,
        *,
        now: datetime | None = None,
    ) -> RelationshipState:
        if row is None:
            return RelationshipState.initial(policy=self.policy, now=now)
        return RelationshipState(
            familiarity=int(row["familiarity"]),
            trust=int(row["trust"]),
            closeness=int(row["closeness"]),
            revision=int(row["revision"]),
            updated_at=_parse_utc(str(row["updated_at"])),
        )

    def get_state(self, relationship_id: str) -> RelationshipState:
        relation = _validate_relationship_id(relationship_id)
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM relationship_state WHERE relationship_id = ?",
                (relation,),
            ).fetchone()
            return self._state_from_row(row)
        finally:
            connection.close()

    def apply_event(
        self,
        relationship_id: str,
        event: RelationshipEvent,
    ) -> StoreApplyResult:
        relation = _validate_relationship_id(relationship_id)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM relationship_event
                WHERE relationship_id = ? AND source_host = ? AND event_id = ?
                """,
                (relation, event.source_host, event.event_id),
            ).fetchone()
            state_row = connection.execute(
                "SELECT * FROM relationship_state WHERE relationship_id = ?",
                (relation,),
            ).fetchone()
            state = self._state_from_row(state_row, now=event.occurred_at)
            if existing is not None:
                connection.execute("COMMIT")
                same_payload = (
                    str(existing["event_type"]) == event.event_type.value
                    and str(existing["occurred_at"]) == _utc_text(event.occurred_at)
                    and float(existing["confidence"]) == float(event.confidence)
                    and str(existing["scope_id"]) == event.scope_id
                    and str(existing["reason_code"]) == event.reason_code
                )
                if not same_payload:
                    return StoreApplyResult(
                        state,
                        False,
                        False,
                        "idempotency_conflict",
                        True,
                    )
                return StoreApplyResult(state, False, True, "duplicate_event")

            day = event.occurred_at.astimezone(UTC).date().isoformat()
            daily = connection.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN delta_familiarity > 0 THEN delta_familiarity ELSE 0 END), 0) AS positive_familiarity,
                    COALESCE(SUM(CASE WHEN delta_trust > 0 THEN delta_trust ELSE 0 END), 0) AS positive_trust,
                    COALESCE(SUM(CASE WHEN delta_closeness > 0 THEN delta_closeness ELSE 0 END), 0) AS positive_closeness,
                    COALESCE(SUM(CASE WHEN delta_familiarity < 0 THEN -delta_familiarity ELSE 0 END), 0) AS negative_familiarity,
                    COALESCE(SUM(CASE WHEN delta_trust < 0 THEN -delta_trust ELSE 0 END), 0) AS negative_trust,
                    COALESCE(SUM(CASE WHEN delta_closeness < 0 THEN -delta_closeness ELSE 0 END), 0) AS negative_closeness
                FROM relationship_event
                WHERE relationship_id = ?
                  AND substr(occurred_at, 1, 10) = ?
                  AND applied = 1
                """,
                (relation, day),
            ).fetchone()
            last = connection.execute(
                """
                SELECT occurred_at
                FROM relationship_event
                WHERE relationship_id = ? AND event_type = ? AND applied = 1
                ORDER BY occurred_at DESC
                LIMIT 1
                """,
                (relation, event.event_type.value),
            ).fetchone()
            context = ReductionContext(
                daily_positive_delta=RelationshipDelta(
                    familiarity=int(daily["positive_familiarity"]),
                    trust=int(daily["positive_trust"]),
                    closeness=int(daily["positive_closeness"]),
                ),
                daily_negative_delta=RelationshipDelta(
                    familiarity=int(daily["negative_familiarity"]),
                    trust=int(daily["negative_trust"]),
                    closeness=int(daily["negative_closeness"]),
                ),
                last_same_type_at=(
                    _parse_utc(str(last["occurred_at"])) if last is not None else None
                ),
            )
            reduced = RelationshipReducer().reduce(state, event, context)
            connection.execute(
                """
                INSERT INTO relationship_event(
                    event_id, relationship_id, event_type, occurred_at,
                    confidence, source_host, scope_id, reason_code,
                    applied, result_reason, delta_familiarity,
                    delta_trust, delta_closeness
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    relation,
                    event.event_type.value,
                    _utc_text(event.occurred_at),
                    float(event.confidence),
                    event.source_host,
                    event.scope_id,
                    event.reason_code,
                    int(reduced.applied),
                    reduced.reason,
                    reduced.delta.familiarity,
                    reduced.delta.trust,
                    reduced.delta.closeness,
                ),
            )
            if reduced.applied:
                connection.execute(
                    """
                    INSERT INTO relationship_state(
                        relationship_id, familiarity, trust, closeness,
                        revision, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(relationship_id) DO UPDATE SET
                        familiarity = excluded.familiarity,
                        trust = excluded.trust,
                        closeness = excluded.closeness,
                        revision = excluded.revision,
                        updated_at = excluded.updated_at
                    """,
                    (
                        relation,
                        reduced.state.familiarity,
                        reduced.state.trust,
                        reduced.state.closeness,
                        reduced.state.revision,
                        _utc_text(reduced.state.updated_at),
                    ),
                )
            if reduced.atmosphere is not None:
                connection.execute(
                    """
                    INSERT INTO relationship_atmosphere(
                        relationship_id, source_host, scope_id, value, expires_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(relationship_id, source_host, scope_id) DO UPDATE SET
                        value = excluded.value,
                        expires_at = excluded.expires_at
                    """,
                    (
                        relation,
                        event.source_host,
                        reduced.atmosphere.scope_id,
                        reduced.atmosphere.value.value,
                        _utc_text(reduced.atmosphere.expires_at),
                    ),
                )
            connection.execute("COMMIT")
            return StoreApplyResult(
                reduced.state,
                reduced.applied,
                False,
                reduced.reason,
            )
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def get_atmosphere(
        self,
        relationship_id: str,
        source_host: str,
        scope_id: str,
        *,
        now: datetime | None = None,
    ) -> Atmosphere:
        relation = _validate_relationship_id(relationship_id)
        try:
            host = validate_source_host(source_host)
            validate_scope_id(scope_id)
        except RelationshipError as exc:
            raise StoreError(str(exc)) from exc
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT value, expires_at FROM relationship_atmosphere
                WHERE relationship_id = ? AND source_host = ? AND scope_id = ?
                """,
                (relation, host, scope_id),
            ).fetchone()
            if row is None:
                return Atmosphere.NEUTRAL
            atmosphere = AtmosphereState(
                value=Atmosphere(str(row["value"])),
                scope_id=scope_id,
                expires_at=_parse_utc(str(row["expires_at"])),
            )
            return active_atmosphere(atmosphere, now)
        finally:
            connection.close()

    def export_relationship(self, relationship_id: str) -> dict[str, object]:
        relation = _validate_relationship_id(relationship_id)
        state = self.get_state(relation)
        return {
            "schema_version": STORE_SCHEMA_VERSION,
            "relationship_id": relation,
            "state": {
                "familiarity": state.familiarity,
                "trust": state.trust,
                "closeness": state.closeness,
                "revision": state.revision,
                "updated_at": _utc_text(state.updated_at),
            },
            "excluded": [
                "events",
                "source_identifiers",
                "chat_text",
                "provider_credentials",
                "asset_paths",
            ],
        }

    def reset_relationship(self, relationship_id: str) -> RelationshipState:
        relation = _validate_relationship_id(relationship_id)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM relationship_event WHERE relationship_id = ?",
                (relation,),
            )
            connection.execute(
                "DELETE FROM relationship_atmosphere WHERE relationship_id = ?",
                (relation,),
            )
            connection.execute(
                "DELETE FROM relationship_state WHERE relationship_id = ?",
                (relation,),
            )
            connection.execute("COMMIT")
            return RelationshipState.initial(policy=self.policy)
        finally:
            connection.close()

    def delete_product_data(self) -> tuple[str, ...]:
        removed: list[str] = []
        if self.database_path.exists():
            try:
                connection = self._connect()
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.close()
            except StoreError:
                # 即使数据库已损坏，仍允许用户删除产品可控范围内的本地文件。
                pass
        for candidate in (
            self.database_path,
            Path(f"{self.database_path}-wal"),
            Path(f"{self.database_path}-shm"),
            Path(f"{self.database_path}-journal"),
        ):
            safe = _safe_database_path(candidate)
            if safe.exists():
                try:
                    safe.unlink()
                except OSError as exc:
                    raise StoreError(f"无法删除本地关系数据：{exc}") from exc
                removed.append(safe.name)
        return tuple(removed)
