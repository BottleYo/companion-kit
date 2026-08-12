from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Iterable


GPT_IMAGE_2 = "gpt-image-2"
BASELINE_QUALITY = "high"
BASELINE_MODERATION = "low"
_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{1,63}$")
_HOSTS = {"codex", "openclaw", "hermes", "claude"}


class ProviderContractError(ValueError):
    """图片 Provider 声明不满足公共能力协议。"""


class ProviderRouteKind(str, Enum):
    HOST_NATIVE = "host_native"
    OPENAI_API = "openai_api"
    COMPATIBLE = "compatible"


class ProviderProof(str, Enum):
    DIRECT_REQUEST = "direct_request"
    RECEIPT_VERIFIED = "receipt_verified"
    HOST_MANAGED = "host_managed"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class ImageProviderRoute:
    id: str
    host: str
    kind: ProviderRouteKind
    proof: ProviderProof
    available: bool
    auth_ready: bool
    requested_model: str
    upstream_model: str
    requested_quality: str
    supports_reference_edit: bool
    can_return_result: bool
    reported_model: str | None = None
    reported_quality: str | None = None
    explicitly_enabled: bool = False

    def __post_init__(self) -> None:
        if not _ID_RE.fullmatch(self.id):
            raise ProviderContractError("Provider id 格式无效")
        if self.host not in _HOSTS:
            raise ProviderContractError("Provider host 不受支持")
        if not isinstance(self.kind, ProviderRouteKind):
            raise ProviderContractError("Provider route kind 不受支持")
        if not isinstance(self.proof, ProviderProof):
            raise ProviderContractError("Provider proof 不受支持")
        for label, value in (
            ("available", self.available),
            ("auth_ready", self.auth_ready),
            ("supports_reference_edit", self.supports_reference_edit),
            ("can_return_result", self.can_return_result),
            ("explicitly_enabled", self.explicitly_enabled),
        ):
            if not isinstance(value, bool):
                raise ProviderContractError(f"{label} 必须是布尔值")
        for label, value in (
            ("requested_model", self.requested_model),
            ("upstream_model", self.upstream_model),
            ("requested_quality", self.requested_quality),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ProviderContractError(f"{label} 不能为空")
        expected_proofs = {
            ProviderRouteKind.HOST_NATIVE: {ProviderProof.HOST_MANAGED},
            ProviderRouteKind.OPENAI_API: {
                ProviderProof.DIRECT_REQUEST,
                ProviderProof.RECEIPT_VERIFIED,
            },
            ProviderRouteKind.COMPATIBLE: {ProviderProof.UNVERIFIED},
        }
        if self.proof not in expected_proofs[self.kind]:
            raise ProviderContractError("Provider kind 与 proof 不匹配")
        if self.proof is ProviderProof.DIRECT_REQUEST:
            if self.reported_model is not None or self.reported_quality is not None:
                raise ProviderContractError("请求侧证明不能伪造 Provider 回显字段")

    @property
    def receipt_proves_baseline(self) -> bool:
        return (
            self.proof is ProviderProof.RECEIPT_VERIFIED
            and self.reported_model == GPT_IMAGE_2
            and self.reported_quality == BASELINE_QUALITY
        )

    @property
    def direct_request_proves_baseline(self) -> bool:
        return (
            self.kind is ProviderRouteKind.OPENAI_API
            and self.proof is ProviderProof.DIRECT_REQUEST
            and self.requested_model == GPT_IMAGE_2
            and self.upstream_model == GPT_IMAGE_2
            and self.requested_quality == BASELINE_QUALITY
        )

    @property
    def baseline_proven(self) -> bool:
        return self.direct_request_proves_baseline or self.receipt_proves_baseline

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "host": self.host,
            "kind": self.kind.value,
            "proof": self.proof.value,
            "available": self.available,
            "auth_ready": self.auth_ready,
            "requested_model": self.requested_model,
            "upstream_model": self.upstream_model,
            "requested_quality": self.requested_quality,
            "reported_model": self.reported_model,
            "reported_quality": self.reported_quality,
            "supports_reference_edit": self.supports_reference_edit,
            "can_return_result": self.can_return_result,
            "explicitly_enabled": self.explicitly_enabled,
        }


@dataclass(frozen=True)
class ProviderSelection:
    route: ImageProviderRoute | None
    reason: str

    @property
    def enabled(self) -> bool:
        return self.route is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "reason": self.reason,
            "route": self.route.to_dict() if self.route else None,
        }


def _baseline_shape(route: ImageProviderRoute) -> bool:
    return (
        route.available
        and route.auth_ready
        and route.upstream_model == GPT_IMAGE_2
        and route.requested_quality == BASELINE_QUALITY
        and route.supports_reference_edit
        and route.can_return_result
    )


def _eligible(
    route: ImageProviderRoute,
    *,
    strict_consistency: bool,
) -> bool:
    if not _baseline_shape(route):
        return False
    if strict_consistency:
        return route.baseline_proven
    if route.proof in {
        ProviderProof.DIRECT_REQUEST,
        ProviderProof.RECEIPT_VERIFIED,
    }:
        return route.baseline_proven
    if route.proof is ProviderProof.HOST_MANAGED:
        return route.kind is ProviderRouteKind.HOST_NATIVE
    return (
        route.kind is ProviderRouteKind.COMPATIBLE
        and route.explicitly_enabled
    )


def select_image_provider(
    routes: Iterable[ImageProviderRoute],
    *,
    expected_host: str,
    strict_consistency: bool = False,
) -> ProviderSelection:
    candidates = list(routes)
    if expected_host not in _HOSTS:
        return ProviderSelection(None, "unsupported_expected_host")
    if any(route.host != expected_host for route in candidates):
        return ProviderSelection(None, "route_host_mismatch")
    ids = [route.id for route in candidates]
    if len(ids) != len(set(ids)):
        return ProviderSelection(None, "duplicate_provider_id")
    eligible = [
        route
        for route in candidates
        if _eligible(route, strict_consistency=strict_consistency)
    ]
    if not eligible:
        return ProviderSelection(None, "no_eligible_gpt_image_2_route")

    priority = {
        ProviderRouteKind.HOST_NATIVE: 0,
        ProviderRouteKind.OPENAI_API: 1,
        ProviderRouteKind.COMPATIBLE: 2,
    }
    selected = min(eligible, key=lambda route: (priority[route.kind], route.id))
    if selected.kind is ProviderRouteKind.HOST_NATIVE:
        reason = "selected_host_native"
    elif selected.kind is ProviderRouteKind.OPENAI_API:
        reason = (
            "selected_direct_openai_api"
            if selected.proof is ProviderProof.DIRECT_REQUEST
            else "selected_receipt_verified_api"
        )
    else:
        reason = "selected_explicit_compatible_gateway"
    return ProviderSelection(selected, reason)
