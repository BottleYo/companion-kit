from __future__ import annotations

import base64
import json
import unittest

from companion_kit.openai_image_api import (
    HttpResponse,
    ImageApiError,
    OpenAIImageClient,
)
from tests.png_fixture import tiny_png


class RecordingTransport:
    def __init__(self, *, status: int = 200, body: bytes | None = None) -> None:
        self.requests = []
        self.status = status
        self.body = body or json.dumps(
            {"data": [{"b64_json": base64.b64encode(tiny_png()).decode("ascii")}]}
        ).encode("utf-8")

    def __call__(self, request):
        self.requests.append(request)
        return HttpResponse(
            status=self.status,
            headers={"x-request-id": "req_test_123"},
            body=self.body,
        )


class OpenAIImageClientTests(unittest.TestCase):
    def test_generation_uses_fixed_model_quality_and_low_moderation(self) -> None:
        transport = RecordingTransport()
        client = OpenAIImageClient(
            env={"OPENAI_API_KEY": "test-only-key"},
            transport=transport,
        )

        result = client.generate("a fictional adult portrait")

        self.assertEqual(len(transport.requests), 1)
        request = transport.requests[0]
        payload = json.loads(request.body)
        self.assertEqual(request.url, "https://api.openai.com/v1/images/generations")
        self.assertEqual(payload["model"], "gpt-image-2")
        self.assertEqual(payload["quality"], "high")
        self.assertEqual(payload["moderation"], "low")
        self.assertEqual(payload["output_format"], "png")
        self.assertEqual(result.request_id, "req_test_123")
        self.assertEqual(result.image_bytes, tiny_png())

    def test_reference_photo_uses_edits_endpoint_and_multipart(self) -> None:
        transport = RecordingTransport()
        client = OpenAIImageClient(
            env={"OPENAI_API_KEY": "test-only-key"},
            transport=transport,
        )

        client.edit("same fictional adult in a new scene", tiny_png())

        request = transport.requests[0]
        self.assertEqual(request.url, "https://api.openai.com/v1/images/edits")
        self.assertIn("multipart/form-data; boundary=", request.headers["Content-Type"])
        self.assertIn(b'name="model"', request.body)
        self.assertIn(b"gpt-image-2", request.body)
        self.assertIn(b'name="quality"', request.body)
        self.assertIn(b"high", request.body)
        self.assertIn(b'name="moderation"', request.body)
        self.assertIn(b"low", request.body)
        self.assertIn(b'name="image"; filename="reference.png"', request.body)

    def test_missing_key_and_provider_error_fail_closed_without_leaking_body(self) -> None:
        self.assertFalse(OpenAIImageClient(env={}).auth_ready)
        with self.assertRaisesRegex(ImageApiError, "OPENAI_API_KEY"):
            OpenAIImageClient(env={}).generate("portrait")

        transport = RecordingTransport(
            status=429,
            body=b'{"error":{"message":"secret upstream detail"}}',
        )
        client = OpenAIImageClient(
            env={"OPENAI_API_KEY": "test-only-key"},
            transport=transport,
        )
        with self.assertRaises(ImageApiError) as caught:
            client.generate("portrait")
        self.assertNotIn("secret upstream detail", str(caught.exception))
        self.assertIn("429", str(caught.exception))
        self.assertEqual(len(transport.requests), 1)


if __name__ == "__main__":
    unittest.main()
