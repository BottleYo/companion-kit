from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from companion_kit.photo_authorization import (
    AuthorizationError,
    PhotoAuthorizationStore,
)


class PhotoAuthorizationStoreTests(unittest.TestCase):
    def test_authorization_is_bound_to_request_and_consumed_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            now = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
            store = PhotoAuthorizationStore(
                Path(tmp).resolve() / "authorizations",
                clock=lambda: now,
            )
            plan = store.create(
                task_scope="codex-current-task",
                profile_id="warm_healer",
                profile_version="a" * 64,
                identity_version=1,
                prompt="private full image prompt",
                reference_id=None,
                purpose="prototype",
                operation="generation",
            )

            serialized = plan.to_dict()
            self.assertNotIn("private full image prompt", str(serialized))
            self.assertNotIn("codex-current-task", str(serialized))
            consumed = store.consume(
                plan_id=plan.plan_id,
                task_scope="codex-current-task",
                profile_id="warm_healer",
                profile_version="a" * 64,
                identity_version=1,
                prompt="private full image prompt",
                reference_id=None,
                purpose="prototype",
                operation="generation",
            )
            self.assertEqual(consumed.plan_id, plan.plan_id)
            with self.assertRaises(AuthorizationError):
                store.consume(
                    plan_id=plan.plan_id,
                    task_scope="codex-current-task",
                    profile_id="warm_healer",
                    profile_version="a" * 64,
                    identity_version=1,
                    prompt="private full image prompt",
                    reference_id=None,
                    purpose="prototype",
                    operation="generation",
                )

    def test_mismatch_and_expiry_fail_before_paid_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            current = [datetime(2026, 8, 4, 12, 0, tzinfo=UTC)]
            store = PhotoAuthorizationStore(
                Path(tmp).resolve() / "authorizations",
                clock=lambda: current[0],
                ttl=timedelta(minutes=5),
            )
            plan = store.create(
                task_scope="task-one",
                profile_id="warm_healer",
                profile_version="b" * 64,
                identity_version=1,
                prompt="original prompt",
                reference_id="ref_1234567890abcdef",
                purpose="photo",
                operation="edit",
            )

            with self.assertRaises(AuthorizationError):
                store.consume(
                    plan_id=plan.plan_id,
                    task_scope="task-one",
                    profile_id="warm_healer",
                    profile_version="b" * 64,
                    identity_version=1,
                    prompt="changed prompt",
                    reference_id="ref_1234567890abcdef",
                    purpose="photo",
                    operation="edit",
                )
            with self.assertRaisesRegex(AuthorizationError, "不存在|已经使用"):
                store.consume(
                    plan_id=plan.plan_id,
                    task_scope="task-one",
                    profile_id="warm_healer",
                    profile_version="b" * 64,
                    identity_version=1,
                    prompt="original prompt",
                    reference_id="ref_1234567890abcdef",
                    purpose="photo",
                    operation="edit",
                )

            expiring = store.create(
                task_scope="task-one",
                profile_id="warm_healer",
                profile_version="b" * 64,
                identity_version=1,
                prompt="original prompt",
                reference_id="ref_1234567890abcdef",
                purpose="photo",
                operation="edit",
            )
            current[0] += timedelta(minutes=6)
            with self.assertRaisesRegex(AuthorizationError, "过期"):
                store.consume(
                    plan_id=expiring.plan_id,
                    task_scope="task-one",
                    profile_id="warm_healer",
                    profile_version="b" * 64,
                    identity_version=1,
                    prompt="original prompt",
                    reference_id="ref_1234567890abcdef",
                    purpose="photo",
                    operation="edit",
                )

    def test_creating_a_new_plan_removes_expired_plan_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            current = [datetime(2026, 8, 4, 12, 0, tzinfo=UTC)]
            store = PhotoAuthorizationStore(
                Path(tmp).resolve() / "authorizations",
                clock=lambda: current[0],
                ttl=timedelta(minutes=5),
            )
            old = store.create(
                task_scope="task-one",
                profile_id="warm_healer",
                profile_version="c" * 64,
                identity_version=1,
                prompt="old prompt",
                reference_id=None,
                purpose="prototype",
                operation="generation",
            )
            current[0] += timedelta(minutes=6)
            store.create(
                task_scope="task-two",
                profile_id="warm_healer",
                profile_version="c" * 64,
                identity_version=1,
                prompt="new prompt",
                reference_id=None,
                purpose="prototype",
                operation="generation",
            )

            self.assertFalse((store.root / f"{old.plan_id}.json").exists())


if __name__ == "__main__":
    unittest.main()
