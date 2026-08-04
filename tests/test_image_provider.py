import unittest

from companion_kit.image_provider import (
    ImageProviderRoute,
    ProviderProof,
    ProviderRouteKind,
    select_image_provider,
)


def route(
    route_id: str,
    *,
    kind: ProviderRouteKind,
    proof: ProviderProof,
    host: str = "codex",
    requested_model: str = "gpt-image-2",
    upstream_model: str = "gpt-image-2",
    requested_quality: str = "high",
    reported_model: str | None = "gpt-image-2",
    reported_quality: str | None = "high",
    reference_edit: bool = True,
    explicitly_enabled: bool = False,
) -> ImageProviderRoute:
    return ImageProviderRoute(
        id=route_id,
        host=host,
        kind=kind,
        proof=proof,
        available=True,
        auth_ready=True,
        requested_model=requested_model,
        upstream_model=upstream_model,
        requested_quality=requested_quality,
        reported_model=reported_model,
        reported_quality=reported_quality,
        supports_reference_edit=reference_edit,
        can_return_result=True,
        explicitly_enabled=explicitly_enabled,
    )


class ImageProviderTests(unittest.TestCase):
    def test_routes_from_multiple_hosts_fail_closed(self) -> None:
        codex = route(
            "codex-native",
            kind=ProviderRouteKind.HOST_NATIVE,
            proof=ProviderProof.HOST_MANAGED,
        )
        hermes = route(
            "hermes-native",
            kind=ProviderRouteKind.HOST_NATIVE,
            proof=ProviderProof.HOST_MANAGED,
            host="hermes",
        )

        selection = select_image_provider([codex, hermes], expected_host="codex")

        self.assertFalse(selection.enabled)
        self.assertEqual(selection.reason, "route_host_mismatch")

    def test_single_route_from_wrong_host_cannot_be_selected(self) -> None:
        hermes = route(
            "hermes-native",
            kind=ProviderRouteKind.HOST_NATIVE,
            proof=ProviderProof.HOST_MANAGED,
            host="hermes",
        )

        selection = select_image_provider([hermes], expected_host="codex")

        self.assertFalse(selection.enabled)
        self.assertEqual(selection.reason, "route_host_mismatch")

    def test_easy_mode_prefers_eligible_host_native_route(self) -> None:
        native = route(
            "codex-native",
            kind=ProviderRouteKind.HOST_NATIVE,
            proof=ProviderProof.HOST_MANAGED,
            reported_model=None,
            reported_quality=None,
        )
        api = route(
            "openai-api",
            kind=ProviderRouteKind.OPENAI_API,
            proof=ProviderProof.VERIFIED,
        )

        selection = select_image_provider([api, native], expected_host="codex")

        self.assertTrue(selection.enabled)
        self.assertEqual(selection.route.id, "codex-native")
        self.assertEqual(selection.reason, "selected_host_native")

    def test_strict_mode_requires_verified_receipt(self) -> None:
        native = route(
            "codex-native",
            kind=ProviderRouteKind.HOST_NATIVE,
            proof=ProviderProof.HOST_MANAGED,
            reported_model=None,
            reported_quality=None,
        )
        api = route(
            "openai-api",
            kind=ProviderRouteKind.OPENAI_API,
            proof=ProviderProof.VERIFIED,
        )

        selection = select_image_provider(
            [native, api],
            expected_host="codex",
            strict_consistency=True,
        )

        self.assertEqual(selection.route.id, "openai-api")
        self.assertEqual(selection.reason, "selected_verified_api")

    def test_hermes_virtual_tier_can_prove_real_upstream_model(self) -> None:
        hermes = route(
            "hermes-openai",
            kind=ProviderRouteKind.OPENAI_API,
            proof=ProviderProof.VERIFIED,
            requested_model="gpt-image-2-high",
            upstream_model="gpt-image-2",
            host="hermes",
        )

        selection = select_image_provider(
            [hermes],
            expected_host="hermes",
            strict_consistency=True,
        )

        self.assertTrue(selection.enabled)
        self.assertEqual(selection.route.upstream_model, "gpt-image-2")

    def test_other_model_or_missing_reference_edit_fails_closed(self) -> None:
        wrong_model = route(
            "other-model",
            kind=ProviderRouteKind.OPENAI_API,
            proof=ProviderProof.VERIFIED,
            requested_model="other-image-model",
            upstream_model="other-image-model",
            reported_model="other-image-model",
        )
        no_edit = route(
            "no-edit",
            kind=ProviderRouteKind.OPENAI_API,
            proof=ProviderProof.VERIFIED,
            reference_edit=False,
        )

        selection = select_image_provider(
            [wrong_model, no_edit],
            expected_host="codex",
        )

        self.assertFalse(selection.enabled)
        self.assertIsNone(selection.route)
        self.assertEqual(selection.reason, "no_eligible_gpt_image_2_route")

    def test_compatible_gateway_requires_explicit_opt_in(self) -> None:
        implicit = route(
            "compatible",
            kind=ProviderRouteKind.COMPATIBLE,
            proof=ProviderProof.UNVERIFIED,
        )
        explicit = route(
            "compatible",
            kind=ProviderRouteKind.COMPATIBLE,
            proof=ProviderProof.UNVERIFIED,
            explicitly_enabled=True,
        )

        self.assertFalse(
            select_image_provider([implicit], expected_host="codex").enabled
        )
        self.assertTrue(
            select_image_provider([explicit], expected_host="codex").enabled
        )


if __name__ == "__main__":
    unittest.main()
