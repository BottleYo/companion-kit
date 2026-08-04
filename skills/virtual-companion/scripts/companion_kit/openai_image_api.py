from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from typing import Callable, Mapping
import urllib.error
import urllib.request
import uuid

from .image_assets import ImageAssetError, sanitize_png
from .image_provider import BASELINE_QUALITY, GPT_IMAGE_2


class ImageApiError(ValueError):
    """OpenAI Image API 请求无法安全完成。"""


_GENERATIONS_URL = "https://api.openai.com/v1/images/generations"
_EDITS_URL = "https://api.openai.com/v1/images/edits"
_OUTPUT_FORMAT = "png"
_OUTPUT_SIZE = "1024x1536"
_MAX_RESPONSE_BYTES = 36 * 1024 * 1024
_MAX_IMAGE_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class ImageApiResult:
    image_bytes: bytes
    request_id: str | None


Transport = Callable[[HttpRequest], HttpResponse]


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # 严格模式不允许把带授权头的请求跟随到另一个地址。
        return None


def _default_transport(request: HttpRequest) -> HttpResponse:
    outbound = urllib.request.Request(
        request.url,
        data=request.body,
        headers=request.headers,
        method=request.method,
    )
    try:
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(outbound, timeout=180) as response:
            body = response.read(_MAX_RESPONSE_BYTES + 1)
            return HttpResponse(
                status=int(response.status),
                headers={key: value for key, value in response.headers.items()},
                body=body,
            )
    except urllib.error.HTTPError as exc:
        body = exc.read(_MAX_RESPONSE_BYTES + 1)
        return HttpResponse(
            status=int(exc.code),
            headers={key: value for key, value in exc.headers.items()},
            body=body,
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ImageApiError("OpenAI Image API 网络请求失败；未自动重试") from exc


def _prompt(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 8_000:
        raise ImageApiError("图片提示必须是 1–8000 个字符")
    return normalized


def _multipart_field(boundary: str, name: str, value: str) -> bytes:
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
        f"{value}\r\n"
    ).encode("utf-8")


class OpenAIImageClient:
    """固定官方端点、模型与画质；不读取配置文件，也不自动重试。"""

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        transport: Transport | None = None,
    ) -> None:
        environment = os.environ if env is None else env
        self._api_key = str(environment.get("OPENAI_API_KEY", "")).strip()
        self._transport = transport or _default_transport

    @property
    def auth_ready(self) -> bool:
        return bool(self._api_key)

    def _headers(self, content_type: str) -> dict[str, str]:
        if not self.auth_ready:
            raise ImageApiError("严格模式需要先在当前宿主环境设置 OPENAI_API_KEY")
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": content_type,
            "Accept": "application/json",
            "User-Agent": "Companion-Kit/0.4",
        }

    def _send(self, request: HttpRequest) -> ImageApiResult:
        try:
            response = self._transport(request)
        except ImageApiError:
            raise
        except Exception as exc:
            raise ImageApiError("OpenAI Image API 网络请求失败；未自动重试") from exc
        if not isinstance(response, HttpResponse):
            raise ImageApiError("OpenAI Image API 返回了无效响应")
        if len(response.body) > _MAX_RESPONSE_BYTES:
            raise ImageApiError("OpenAI Image API 响应超过安全上限")
        if not 200 <= response.status < 300:
            raise ImageApiError(
                f"OpenAI Image API 请求失败（HTTP {response.status}）；未自动重试"
            )
        try:
            payload = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ImageApiError("OpenAI Image API 返回内容无法解析") from exc
        if not isinstance(payload, dict):
            raise ImageApiError("OpenAI Image API 返回结构无效")
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise ImageApiError("OpenAI Image API 未返回唯一图片")
        encoded = data[0].get("b64_json")
        if not isinstance(encoded, str) or not encoded:
            raise ImageApiError("OpenAI Image API 未返回 PNG 数据")
        if len(encoded) > ((_MAX_IMAGE_BYTES + 2) // 3) * 4 + 8:
            raise ImageApiError("OpenAI Image API 图片超过安全上限")
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ImageApiError("OpenAI Image API 图片编码无效") from exc
        if not image_bytes or len(image_bytes) > _MAX_IMAGE_BYTES:
            raise ImageApiError("OpenAI Image API 图片超过安全上限")

        request_id: str | None = None
        for key, value in response.headers.items():
            if key.casefold() == "x-request-id":
                candidate = str(value).strip()
                if candidate and len(candidate) <= 200 and not any(
                    ord(character) < 32 for character in candidate
                ):
                    request_id = candidate
                break
        return ImageApiResult(image_bytes=image_bytes, request_id=request_id)

    def generate(self, prompt: str) -> ImageApiResult:
        payload = {
            "model": GPT_IMAGE_2,
            "prompt": _prompt(prompt),
            "quality": BASELINE_QUALITY,
            "size": _OUTPUT_SIZE,
            "output_format": _OUTPUT_FORMAT,
            "n": 1,
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        return self._send(
            HttpRequest(
                method="POST",
                url=_GENERATIONS_URL,
                headers=self._headers("application/json"),
                body=body,
            )
        )

    def edit(self, prompt: str, reference_png: bytes) -> ImageApiResult:
        try:
            sanitized_reference, _, _ = sanitize_png(reference_png)
        except ImageAssetError as exc:
            raise ImageApiError("身份参考图无效，未发起 API 请求") from exc
        boundary = f"companion-{uuid.uuid4().hex}"
        body = b"".join(
            (
                _multipart_field(boundary, "model", GPT_IMAGE_2),
                _multipart_field(boundary, "prompt", _prompt(prompt)),
                _multipart_field(boundary, "quality", BASELINE_QUALITY),
                _multipart_field(boundary, "size", _OUTPUT_SIZE),
                _multipart_field(boundary, "output_format", _OUTPUT_FORMAT),
                (
                    f"--{boundary}\r\n"
                    'Content-Disposition: form-data; name="image"; '
                    'filename="reference.png"\r\n'
                    "Content-Type: image/png\r\n\r\n"
                ).encode("ascii"),
                sanitized_reference,
                b"\r\n",
                f"--{boundary}--\r\n".encode("ascii"),
            )
        )
        return self._send(
            HttpRequest(
                method="POST",
                url=_EDITS_URL,
                headers=self._headers(f"multipart/form-data; boundary={boundary}"),
                body=body,
            )
        )
