from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from companion_kit.event_adapter import (
    EventAdapterError,
    format_current_reply_handoff,
    openclaw_native_preview_request,
)
from tests.png_fixture import tiny_png


class EventAdapterTests(unittest.TestCase):
    def test_openclaw_native_preview_is_exact_but_host_managed(self) -> None:
        request = openclaw_native_preview_request("通用虚构成年人在窗边阅读")

        self.assertEqual(request["tool"], "image_generate")
        self.assertEqual(request["arguments"]["model"], "openai/gpt-image-2")
        self.assertEqual(request["arguments"]["quality"], "high")
        self.assertEqual(request["arguments"]["count"], 1)
        self.assertEqual(request["arguments"]["outputFormat"], "png")
        self.assertEqual(
            request["arguments"]["openai"],
            {"moderation": "low"},
        )
        self.assertEqual(request["proof"], "host_managed")
        self.assertNotIn("target", request["arguments"])

    def test_handoff_never_accepts_an_arbitrary_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp).resolve() / "result.png"
            image.write_bytes(tiny_png())

            openclaw = format_current_reply_handoff(
                host="openclaw",
                artifact_path=image,
            )
            hermes = format_current_reply_handoff(
                host="hermes",
                artifact_path=image,
            )

            self.assertEqual(openclaw["transport"], "current_reply_message_tool")
            self.assertEqual(openclaw["media"], str(image))
            self.assertNotIn("target", openclaw)
            self.assertEqual(hermes["transport"], "current_response_media")
            self.assertEqual(hermes["response_directive"], f"MEDIA:{image}")

            with self.assertRaises(EventAdapterError):
                format_current_reply_handoff(
                    host="claude",
                    artifact_path=image,
                )

            image.write_bytes(b"not-a-png")
            with self.assertRaisesRegex(EventAdapterError, "安全 PNG"):
                format_current_reply_handoff(
                    host="hermes",
                    artifact_path=image,
                )


if __name__ == "__main__":
    unittest.main()
