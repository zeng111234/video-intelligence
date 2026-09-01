from decimal import Decimal

import pytest

from src.services.image_generation import (
    ImageGenerationConfiguration,
    ImageGenerationError,
    MiniMaxTokenPlanImageProvider,
    OpenAICompatibleImageProvider,
    build_image_provider,
)


def test_missing_relay_configuration_never_allows_paid_generation():
    provider = OpenAICompatibleImageProvider(ImageGenerationConfiguration())

    quote = provider.quote(3)

    assert quote.can_generate is False
    assert quote.total_price_cny is None
    assert "VIDEO_IMAGE_API_KEY" in quote.missing_configuration


def test_quote_blocks_when_generation_exceeds_per_video_budget():
    config = ImageGenerationConfiguration(
        mode="openai_compatible",
        base_url="https://relay.example.com",
        model="image-model",
        api_key="secret",
        unit_price_cny=Decimal("0.80"),
        budget_cny=Decimal("1.00"),
    )

    quote = OpenAICompatibleImageProvider(config).quote(2)

    assert quote.can_generate is False
    assert quote.total_price_cny == Decimal("1.60")


def test_b64_generation_parses_result_without_network_call():
    config = ImageGenerationConfiguration(
        mode="openai_compatible",
        base_url="https://relay.example.com",
        model="image-model",
        api_key="secret",
        unit_price_cny=Decimal("0.10"),
        budget_cny=Decimal("1.00"),
    )
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append((method, url, headers, body))
        return {"data": [{"b64_json": "aGVsbG8="}]}

    result = OpenAICompatibleImageProvider(config, transport=transport).generate("办公室数据图")

    assert result.image_bytes == b"hello"
    assert len(calls) == 1
    assert calls[0][0] == "POST"


def test_non_b64_relay_response_is_blocked():
    config = ImageGenerationConfiguration(
        mode="openai_compatible",
        base_url="https://relay.example.com",
        model="image-model",
        api_key="secret",
        unit_price_cny=Decimal("0.10"),
        budget_cny=Decimal("1.00"),
    )
    provider = OpenAICompatibleImageProvider(
        config, transport=lambda *args: {"data": [{"url": "https://cdn.example.com/a.png"}]}
    )

    with pytest.raises(ImageGenerationError, match="Base64"):
        provider.generate("办公室数据图")


def test_default_transport_does_not_inherit_desktop_proxy_by_default(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    class FakeClient:
        def __init__(self, *, timeout, trust_env):
            captured.update(timeout=timeout, trust_env=trust_env)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, url, headers, content):
            return FakeResponse()

    monkeypatch.setattr("src.services.image_generation.httpx.Client", FakeClient)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    OpenAICompatibleImageProvider._default_transport(
        "POST", "https://api.minimax.io/v1/image_generation", {}, b"{}", 3.0
    )

    assert captured == {"timeout": 3.0, "trust_env": False}


def test_keyword_generation_switch_is_on_by_default_and_keeps_manifest_path():
    config = ImageGenerationConfiguration.from_env(
        {
            "VIDEO_IMAGE_MANIFEST_PATH": "C:/tmp/generated-manifest.json",
        }
    )

    assert config.mode == "minimax_token_plan"
    assert config.autogenerate_on_keyword_match is True
    assert config.manifest_path == "C:/tmp/generated-manifest.json"


def test_minimax_token_plan_configuration_uses_safe_defaults_and_separate_key():
    config = ImageGenerationConfiguration.from_env(
        {
            "VIDEO_IMAGE_PROVIDER_MODE": "minimax_token_plan",
            "MINIMAX_TOKEN_PLAN_KEY": "secret",
        }
    )

    assert config.is_minimax_token_plan is True
    assert config.base_url == "https://api.minimax.io"
    assert config.model == "image-01"
    assert config.missing_configuration == []
    assert isinstance(build_image_provider(config), MiniMaxTokenPlanImageProvider)


def test_minimax_token_plan_accepts_the_official_domestic_endpoint():
    config = ImageGenerationConfiguration.from_env(
        {
            "VIDEO_IMAGE_PROVIDER_MODE": "minimax_token_plan",
            "VIDEO_IMAGE_BASE_URL": "https://api.minimaxi.com",
            "MINIMAX_TOKEN_PLAN_KEY": "secret",
        }
    )

    assert config.base_url == "https://api.minimaxi.com"
    assert "api.minimaxi.com" in config.allowed_hosts
    assert MiniMaxTokenPlanImageProvider(config)._endpoint() == (
        "https://api.minimaxi.com/v1/image_generation"
    )


def test_minimax_token_plan_builds_official_request_and_parses_base64():
    config = ImageGenerationConfiguration.from_env(
        {
            "VIDEO_IMAGE_PROVIDER_MODE": "minimax_token_plan",
            "MINIMAX_TOKEN_PLAN_KEY": "secret",
        }
    )
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append((method, url, headers, body))
        return {"data": {"image_base64": ["/9j/4AAQSkZJRgABAQ=="]}}

    result = MiniMaxTokenPlanImageProvider(config, transport=transport).generate(
        "现代门店客户关系场景"
    )

    assert result.provider == "minimax_token_plan"
    assert result.image_bytes.startswith(b"\xff\xd8")
    assert len(calls) == 1
    assert calls[0][1] == "https://api.minimax.io/v1/image_generation"
    assert b'"model":"image-01"' in calls[0][3]
    assert b'"aspect_ratio":"9:16"' in calls[0][3]
    assert b'"response_format":"base64"' in calls[0][3]


def test_minimax_provider_error_is_exposed_without_secret_values():
    config = ImageGenerationConfiguration.from_env(
        {
            "VIDEO_IMAGE_PROVIDER_MODE": "minimax_token_plan",
            "MINIMAX_TOKEN_PLAN_KEY": "secret-token",
        }
    )
    provider = MiniMaxTokenPlanImageProvider(
        config,
        transport=lambda *args: {
            "base_resp": {
                "status_code": 1004,
                "status_msg": "quota unavailable",
            }
        },
    )

    with pytest.raises(ImageGenerationError, match="1004.*quota unavailable") as exc:
        provider.generate("a realistic restaurant customer scene")

    assert "secret-token" not in str(exc.value)


def test_minimax_provider_accepts_a_single_base64_string_for_compatibility():
    config = ImageGenerationConfiguration.from_env(
        {
            "VIDEO_IMAGE_PROVIDER_MODE": "minimax_token_plan",
            "MINIMAX_TOKEN_PLAN_KEY": "secret",
        }
    )
    provider = MiniMaxTokenPlanImageProvider(
        config,
        transport=lambda *args: {"data": {"image_base64": "/9j/4AAQSkZJRgABAQ=="}},
    )

    result = provider.generate("a realistic restaurant customer scene")

    assert result.image_bytes.startswith(b"\xff\xd8")


def test_minimax_missing_key_never_calls_transport():
    calls = []
    config = ImageGenerationConfiguration.from_env(
        {"VIDEO_IMAGE_PROVIDER_MODE": "minimax_token_plan"}
    )
    provider = MiniMaxTokenPlanImageProvider(
        config, transport=lambda *args: calls.append(args)
    )

    quote = provider.quote(2)

    assert quote.can_generate is False
    assert "MINIMAX_TOKEN_PLAN_KEY" in quote.missing_configuration
    assert calls == []
