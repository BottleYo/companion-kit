from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from companion_kit.event_job_store import (
    EventJobContext,
    EventJobError,
    EventJobShape,
    EventJobStore,
)


class EventJobStoreTests(unittest.TestCase):
    def _context(self, *, host: str = "openclaw") -> EventJobContext:
        return EventJobContext(
            host=host,
            instance_scope="local-instance-a",
            conversation_scope="current-conversation-a",
            request_event_id="inbound-event-001",
        )

    def _shape(self) -> EventJobShape:
        return EventJobShape(
            profile_id="warm_healer",
            profile_version="a" * 64,
            identity_version=1,
            prompt="仅用于摘要的通用测试画面",
            reference_id=None,
            purpose="prototype",
            operation="generation",
        )

    def test_persists_only_hashes_and_rejects_duplicate_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventJobStore(Path(tmp).resolve() / "jobs")
            context = self._context()
            shape = self._shape()

            job = store.create(
                context=context,
                shape=shape,
                authorization_plan_id="plan_" + "1" * 24,
            )

            stored_text = next(store.root.glob("job_*.json")).read_text(
                encoding="utf-8"
            )
            stored = json.loads(stored_text)
            self.assertEqual(stored["host"], "openclaw")
            self.assertNotIn(context.instance_scope, stored_text)
            self.assertNotIn(context.conversation_scope, stored_text)
            self.assertNotIn(context.request_event_id, stored_text)
            self.assertNotIn(shape.prompt, stored_text)
            self.assertNotIn("artifact_path", stored)
            self.assertEqual(job.stage, "planned")

            with self.assertRaisesRegex(EventJobError, "已经处理"):
                store.create(
                    context=context,
                    shape=shape,
                    authorization_plan_id="plan_" + "2" * 24,
                )

    def test_same_event_id_in_different_conversations_does_not_collide(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventJobStore(Path(tmp).resolve() / "jobs")
            first = self._context()
            second = EventJobContext(
                host=first.host,
                instance_scope=first.instance_scope,
                conversation_scope="another-current-conversation",
                request_event_id=first.request_event_id,
            )

            first_job = store.create(
                context=first,
                shape=self._shape(),
                authorization_plan_id="plan_" + "7" * 24,
            )
            second_job = store.create(
                context=second,
                shape=self._shape(),
                authorization_plan_id="plan_" + "8" * 24,
            )

            self.assertNotEqual(first_job.job_id, second_job.job_id)

    def test_generation_and_handoff_are_claimed_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventJobStore(Path(tmp).resolve() / "jobs")
            context = self._context(host="hermes")
            shape = self._shape()
            plan_id = "plan_" + "3" * 24
            job = store.create(
                context=context,
                shape=shape,
                authorization_plan_id=plan_id,
            )

            generating = store.claim_generation(
                job_id=job.job_id,
                context=context,
                shape=shape,
                authorization_plan_id=plan_id,
            )
            self.assertEqual(generating.stage, "generating")
            with self.assertRaisesRegex(EventJobError, "不能重复"):
                store.claim_generation(
                    job_id=job.job_id,
                    context=context,
                    shape=shape,
                    authorization_plan_id=plan_id,
                )

            generated = store.mark_generated(
                job_id=job.job_id,
                context=context,
                asset_id="cand_" + "4" * 16,
            )
            self.assertEqual(generated.stage, "generated")
            handed_off = store.claim_handoff(
                job_id=job.job_id,
                context=context,
                asset_id="cand_" + "4" * 16,
                handoff_artifact_id="art_" + "b" * 16,
            )
            self.assertEqual(handed_off.stage, "delivery_unknown")
            self.assertEqual(
                handed_off.handoff_artifact_id,
                "art_" + "b" * 16,
            )
            with self.assertRaisesRegex(EventJobError, "不能重复"):
                store.claim_handoff(
                    job_id=job.job_id,
                    context=context,
                    asset_id="cand_" + "4" * 16,
                    handoff_artifact_id="art_" + "c" * 16,
                )

            delivered = store.mark_delivered(
                job_id=job.job_id,
                context=context,
                asset_id="cand_" + "4" * 16,
            )
            self.assertEqual(delivered.stage, "delivered")

    def test_host_or_scope_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventJobStore(Path(tmp).resolve() / "jobs")
            context = self._context()
            shape = self._shape()
            plan_id = "plan_" + "5" * 24
            job = store.create(
                context=context,
                shape=shape,
                authorization_plan_id=plan_id,
            )

            for changed in (
                EventJobContext(
                    host="hermes",
                    instance_scope=context.instance_scope,
                    conversation_scope=context.conversation_scope,
                    request_event_id=context.request_event_id,
                ),
                EventJobContext(
                    host=context.host,
                    instance_scope="another-instance",
                    conversation_scope=context.conversation_scope,
                    request_event_id=context.request_event_id,
                ),
                EventJobContext(
                    host=context.host,
                    instance_scope=context.instance_scope,
                    conversation_scope="another-conversation",
                    request_event_id=context.request_event_id,
                ),
            ):
                with self.subTest(changed=changed):
                    with self.assertRaises(EventJobError):
                        store.claim_generation(
                            job_id=job.job_id,
                            context=changed,
                            shape=shape,
                            authorization_plan_id=plan_id,
                        )

            self.assertEqual(store.read(job.job_id).stage, "planned")
            confirmation_event = EventJobContext(
                host=context.host,
                instance_scope=context.instance_scope,
                conversation_scope=context.conversation_scope,
                request_event_id="inbound-confirmation-002",
            )
            generating = store.claim_generation(
                job_id=job.job_id,
                context=confirmation_event,
                shape=shape,
                authorization_plan_id=plan_id,
            )
            self.assertEqual(generating.stage, "generating")

    def test_expired_final_jobs_are_pruned_without_replaying(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            now = [datetime(2026, 8, 4, 12, 0, tzinfo=UTC)]
            store = EventJobStore(
                Path(tmp).resolve() / "jobs",
                clock=lambda: now[0],
                ttl=timedelta(hours=1),
            )
            context = self._context()
            shape = self._shape()
            plan_id = "plan_" + "6" * 24
            job = store.create(
                context=context,
                shape=shape,
                authorization_plan_id=plan_id,
            )
            store.claim_generation(
                job_id=job.job_id,
                context=context,
                shape=shape,
                authorization_plan_id=plan_id,
            )
            store.mark_failed(job_id=job.job_id, context=context)
            now[0] += timedelta(hours=2)

            self.assertEqual(store.prune_expired(), 1)
            with self.assertRaises(EventJobError):
                store.read(job.job_id)

    def test_expired_job_cannot_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            now = [datetime(2026, 8, 4, 12, 0, tzinfo=UTC)]
            store = EventJobStore(
                Path(tmp).resolve() / "jobs",
                clock=lambda: now[0],
                ttl=timedelta(hours=1),
            )
            context = self._context()
            shape = self._shape()
            plan_id = "plan_" + "9" * 24
            job = store.create(
                context=context,
                shape=shape,
                authorization_plan_id=plan_id,
            )
            now[0] += timedelta(hours=2)

            with self.assertRaisesRegex(EventJobError, "过期"):
                store.claim_generation(
                    job_id=job.job_id,
                    context=context,
                    shape=shape,
                    authorization_plan_id=plan_id,
                )
            self.assertFalse((store.root / f"{job.job_id}.json").exists())

    def test_tampered_job_record_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventJobStore(Path(tmp).resolve() / "jobs")
            job = store.create(
                context=self._context(),
                shape=self._shape(),
                authorization_plan_id="plan_" + "a" * 24,
            )
            path = store.root / f"{job.job_id}.json"
            stored = json.loads(path.read_text(encoding="utf-8"))
            stored["identity_version"] = "not-an-integer"
            path.write_text(json.dumps(stored), encoding="utf-8")

            with self.assertRaisesRegex(EventJobError, "身份版本"):
                store.read(job.job_id)

    def test_job_root_rejects_symbolic_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            real = base / "real-jobs"
            real.mkdir()
            linked = base / "linked-jobs"
            try:
                linked.symlink_to(real, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("当前平台不支持符号链接")

            with self.assertRaisesRegex(EventJobError, "符号链接"):
                EventJobStore(linked)


if __name__ == "__main__":
    unittest.main()
