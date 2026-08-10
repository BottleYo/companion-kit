from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from companion_kit.codex_image_receipts import (
    CodexImageReceiptError,
    CodexImageReceiptStore,
    extract_codex_generated_paths,
)
from companion_kit.initializer import initialize_profile
from companion_kit.hook_health import HookHealthStore, POST_TOOL_USE
from tests.png_fixture import tiny_png


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "virtual-companion"
HOOK = PROJECT_ROOT / "hooks" / "codex_image_receipt.py"


class CodexImageReceiptTests(unittest.TestCase):
    def test_current_task_receipt_is_single_use_and_contains_no_path_or_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            codex_home = root / "codex-home"
            image = codex_home / "generated_images" / "candidate.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(tiny_png())
            receipt_root = root / "receipts"
            store = CodexImageReceiptStore(receipt_root)

            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                self.assertEqual(
                    store.record(
                        session_id="current-codex-task",
                        tool_use_id="image-call-one",
                        paths=(image,),
                    ),
                    1,
                )
                payload = "".join(
                    path.read_text(encoding="utf-8")
                    for path in receipt_root.glob("*.json")
                )
                self.assertNotIn("current-codex-task", payload)
                self.assertNotIn(str(image), payload)
                self.assertEqual(
                    store.consume(
                        session_id="current-codex-task",
                        source_path=image,
                    ),
                    tiny_png(),
                )
                with self.assertRaises(CodexImageReceiptError):
                    store.consume(
                        session_id="current-codex-task",
                        source_path=image,
                    )

    def test_current_task_edit_verification_is_read_only_and_session_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            codex_home = root / "codex-home"
            image = codex_home / "generated_images" / "editable.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(tiny_png())
            store = CodexImageReceiptStore(root / "receipts")

            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                store.record(
                    session_id="current-task",
                    tool_use_id="image-call",
                    paths=(image,),
                )

                self.assertEqual(
                    store.verify(session_id="current-task", source_path=image),
                    tiny_png(),
                )
                self.assertEqual(
                    store.verify(session_id="current-task", source_path=image),
                    tiny_png(),
                )
                with self.assertRaises(CodexImageReceiptError):
                    store.verify(session_id="another-task", source_path=image)

                image.write_bytes(tiny_png(metadata=b"changed-after-verification"))
                with self.assertRaises(CodexImageReceiptError):
                    store.verify(session_id="current-task", source_path=image)

    def test_latest_verification_tracks_latest_success_and_duplicate_is_not_new(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            codex_home = root / "codex-home"
            first = codex_home / "generated_images" / "first.png"
            second = codex_home / "generated_images" / "second.png"
            first.parent.mkdir(parents=True)
            first.write_bytes(tiny_png(metadata=b"first"))
            second.write_bytes(tiny_png(metadata=b"second"))
            store = CodexImageReceiptStore(root / "receipts")

            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                self.assertEqual(
                    store.record(
                        session_id="current-task",
                        tool_use_id="first-call",
                        paths=(first,),
                    ),
                    1,
                )
                self.assertEqual(
                    store.record(
                        session_id="current-task",
                        tool_use_id="first-call",
                        paths=(first,),
                    ),
                    0,
                )
                self.assertEqual(
                    store.record(
                        session_id="current-task",
                        tool_use_id="second-call",
                        paths=(second,),
                    ),
                    1,
                )
                self.assertEqual(
                    store.verify_latest(
                        session_id="current-task",
                        source_path=second,
                    ),
                    second.read_bytes(),
                )
                with self.assertRaises(CodexImageReceiptError):
                    store.verify_latest(
                        session_id="current-task",
                        source_path=first,
                    )

    def test_old_or_other_task_image_cannot_be_staged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            codex_home = root / "codex-home"
            image = codex_home / "generated_images" / "old-task.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(tiny_png())
            store = CodexImageReceiptStore(root / "receipts")

            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                store.record(
                    session_id="old-task",
                    tool_use_id="image-call-old",
                    paths=(image,),
                )
                with self.assertRaisesRegex(CodexImageReceiptError, "当前 Codex 任务"):
                    store.consume(session_id="new-task", source_path=image)

    def test_changed_file_no_longer_matches_image_tool_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            codex_home = root / "codex-home"
            image = codex_home / "generated_images" / "candidate.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(tiny_png())
            store = CodexImageReceiptStore(root / "receipts")

            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                store.record(
                    session_id="current-task",
                    tool_use_id="image-call",
                    paths=(image,),
                )
                image.write_bytes(tiny_png(metadata=b"changed-after-tool"))
                with self.assertRaises(CodexImageReceiptError):
                    store.consume(session_id="current-task", source_path=image)

    def test_expired_receipt_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            codex_home = root / "codex-home"
            image = codex_home / "generated_images" / "candidate.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(tiny_png())
            current = [datetime(2026, 8, 5, 10, 0, tzinfo=UTC)]
            store = CodexImageReceiptStore(
                root / "receipts",
                clock=lambda: current[0],
                ttl=timedelta(minutes=10),
            )

            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                store.record(
                    session_id="current-task",
                    tool_use_id="image-call",
                    paths=(image,),
                )
                current[0] += timedelta(minutes=11)
                with self.assertRaises(CodexImageReceiptError):
                    store.consume(session_id="current-task", source_path=image)

    def test_post_tool_hook_records_only_path_returned_by_image_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            companion_home = root / "companion-home"
            codex_home = root / "codex-home"
            image = codex_home / "generated_images" / "candidate.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(tiny_png())
            environment = os.environ.copy()
            environment.update(
                {
                    "COMPANION_HOME": str(companion_home),
                    "CODEX_HOME": str(codex_home),
                    "PLUGIN_ROOT": str(PROJECT_ROOT),
                }
            )
            initialize_profile(
                skill_root=SKILL_ROOT,
                template_id="warm_healer",
                display_name="小禾",
                output=companion_home / "profiles" / "default.toml",
            )

            completed = subprocess.run(
                ["python3", str(HOOK)],
                input=json.dumps(
                    {
                        "hook_event_name": "PostToolUse",
                        "session_id": "hook-current-task",
                        "tool_use_id": "hook-image-call",
                        "tool_name": "image_gen__imagegen",
                        "tool_response": {"output_hint": str(image)},
                    }
                ),
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )

            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr, "")
            with patch.dict(
                os.environ,
                {
                    "COMPANION_HOME": str(companion_home),
                    "CODEX_HOME": str(codex_home),
                },
                clear=True,
            ):
                consumed = CodexImageReceiptStore().consume(
                    session_id="hook-current-task",
                    source_path=image,
                )
            self.assertEqual(consumed, tiny_png())
            health = HookHealthStore(
                root=companion_home / "system" / "hook-health",
                plugin_root=PROJECT_ROOT,
            ).status(POST_TOOL_USE)
            self.assertTrue(health.verified)

    def test_post_tool_hook_rejects_non_image_tool_when_called_directly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            companion_home = root / "companion-home"
            codex_home = root / "codex-home"
            image = codex_home / "generated_images" / "candidate.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(tiny_png())
            environment = os.environ.copy()
            environment.update(
                {
                    "COMPANION_HOME": str(companion_home),
                    "CODEX_HOME": str(codex_home),
                    "PLUGIN_ROOT": str(PROJECT_ROOT),
                }
            )
            initialize_profile(
                skill_root=SKILL_ROOT,
                template_id="warm_healer",
                display_name="小禾",
                output=companion_home / "profiles" / "default.toml",
            )

            completed = subprocess.run(
                ["python3", str(HOOK)],
                input=json.dumps(
                    {
                        "hook_event_name": "PostToolUse",
                        "session_id": "hook-current-task",
                        "tool_use_id": "not-an-image-call",
                        "tool_name": "exec_command",
                        "tool_response": {"output_hint": str(image)},
                    }
                ),
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )

            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr, "")
            with patch.dict(
                os.environ,
                {
                    "COMPANION_HOME": str(companion_home),
                    "CODEX_HOME": str(codex_home),
                },
                clear=True,
            ):
                with self.assertRaises(CodexImageReceiptError):
                    CodexImageReceiptStore().consume(
                        session_id="hook-current-task",
                        source_path=image,
                    )
                health = HookHealthStore(
                    root=companion_home / "system" / "hook-health",
                    plugin_root=PROJECT_ROOT,
                ).status(POST_TOOL_USE)
                self.assertFalse(health.verified)

    def test_path_extraction_ignores_data_urls_and_unrelated_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp).resolve() / "codex-home"
            image = codex_home / "generated_images" / "candidate.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(tiny_png())
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                paths = extract_codex_generated_paths(
                    {
                        "image_url": "data:image/png;base64,private",
                        "output_hint": f"Saved image:\n{image}",
                        "note": str(Path(tmp).resolve() / "unrelated.png"),
                    }
                )
            self.assertEqual(paths, (image,))


if __name__ == "__main__":
    unittest.main()
