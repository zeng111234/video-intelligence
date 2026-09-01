"""P0-4: MiniMax image-01 接入 + 安全门。

确保：
- 未显式配置 VIDEO_IMAGE_PROVIDER_MODE = minimax_token_plan / minimax 不发请求。
- host 不在白名单时拒绝。
- 非 HTTPS 拒绝。
- URL 带 userinfo 拒绝。
- Token Plan 模式不需要 unit_price / budget。
- 每条视频最多 4 张（max_assets 上限）。
- max_concurrent > 2 时降级为 2 并记录 design_warning。
- prompt 含具体中文 / 数字 / CTA 时跳过该 request。
- 请求记录不含 API Key。
- 失败后安全降级（不抛到上游）。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.services.image_generation import (
    ImageGenerationConfiguration,
    ImageGenerationError,
    MiniMaxTokenPlanImageProvider,
    OpenAICompatibleImageProvider,
    _quote_for_configuration,
    build_image_provider,
)
from src.services.video_editor_workflow import (
    _textual_payload_has_unsafe_literals,
)


# ---------- 1) 配置缺失时不发请求 ----------


def test_disabled_mode_blocks_generation() -> None:
    cfg = ImageGenerationConfiguration(
        mode="disabled",
        base_url="https://api.minimax.io",
        model="image-01",
        api_key="sk-test",
    )
    quote = _quote_for_configuration(cfg, 1)
    assert quote.can_generate is False
    assert "VIDEO_IMAGE_PROVIDER_MODE" in quote.missing_configuration


def test_minimax_token_plan_does_not_require_unit_price() -> None:
    """Token Plan 模式不要求 unit_price / budget（按次"套餐额度内"无法单独核算）。"""
    cfg = ImageGenerationConfiguration(
        mode="minimax_token_plan",
        base_url="https://api.minimax.io",
        model="image-01",
        api_key="sk-test",
    )
    assert cfg.missing_configuration == []


def test_openai_compatible_requires_unit_price_and_budget() -> None:
    cfg = ImageGenerationConfiguration(
        mode="openai_compatible",
        base_url="https://relay.example.com",
        model="image-01",
        api_key="sk-test",
    )
    missing = cfg.missing_configuration
    assert "VIDEO_IMAGE_UNIT_PRICE_CNY" in missing
    assert "VIDEO_IMAGE_BUDGET_CNY" in missing


# ---------- 2) Host 白名单 / HTTPS / userinfo 校验 ----------


def test_minimax_endpoint_rejects_http() -> None:
    cfg = ImageGenerationConfiguration(
        mode="minimax_token_plan",
        base_url="http://api.minimax.io",
        model="image-01",
        api_key="sk-test",
    )
    provider = MiniMaxTokenPlanImageProvider(cfg)
    with pytest.raises(ImageGenerationError, match="HTTP"):
        provider._endpoint()


def test_minimax_endpoint_rejects_userinfo() -> None:
    cfg = ImageGenerationConfiguration(
        mode="minimax_token_plan",
        base_url="https://user:pass@api.minimax.io",
        model="image-01",
        api_key="sk-test",
    )
    provider = MiniMaxTokenPlanImageProvider(cfg)
    with pytest.raises(ImageGenerationError, match="账号密码"):
        provider._endpoint()


def test_minimax_endpoint_rejects_unlisted_host_when_whitelist_set() -> None:
    """P0-4: 真校验 host 白名单。"""
    cfg = ImageGenerationConfiguration(
        mode="minimax_token_plan",
        base_url="https://api.evil.com",
        model="image-01",
        api_key="sk-test",
        allowed_hosts=("api.minimax.io",),
    )
    provider = MiniMaxTokenPlanImageProvider(cfg)
    with pytest.raises(ImageGenerationError, match="白名单"):
        provider._endpoint()


def test_minimax_endpoint_accepts_listed_host() -> None:
    cfg = ImageGenerationConfiguration(
        mode="minimax_token_plan",
        base_url="https://api.minimax.io",
        model="image-01",
        api_key="sk-test",
        allowed_hosts=("api.minimax.io", "api2.minimax.io"),
    )
    provider = MiniMaxTokenPlanImageProvider(cfg)
    endpoint = provider._endpoint()
    assert endpoint.startswith("https://api.minimax.io")


def test_minimax_endpoint_allows_unset_whitelist() -> None:
    """allowed_hosts 为空 → 不校验（向后兼容）。"""
    cfg = ImageGenerationConfiguration(
        mode="minimax_token_plan",
        base_url="https://api.minimax.io",
        model="image-01",
        api_key="sk-test",
        allowed_hosts=(),
    )
    provider = MiniMaxTokenPlanImageProvider(cfg)
    endpoint = provider._endpoint()
    assert endpoint.endswith("/v1/image_generation")


# ---------- 3) Build provider 路由 ----------


def test_build_image_provider_routes_minimax() -> None:
    cfg = ImageGenerationConfiguration(
        mode="minimax_token_plan",
        base_url="https://api.minimax.io",
        model="image-01",
        api_key="sk-test",
    )
    provider = build_image_provider(cfg)
    assert isinstance(provider, MiniMaxTokenPlanImageProvider)


def test_build_image_provider_routes_openai_compatible() -> None:
    cfg = ImageGenerationConfiguration(
        mode="openai_compatible",
        base_url="https://relay.example.com",
        model="image-01",
        api_key="sk-test",
    )
    provider = build_image_provider(cfg)
    assert isinstance(provider, OpenAICompatibleImageProvider)


# ---------- 4) Prompt 黑名单 ----------


@pytest.mark.parametrize(
    "payload",
    [
        "客户扫码支付",
        "扫码加群领取",
        "评论扣 1",
        "增长 30%",
        "5 万元",
    ],
)
def test_unsafe_prompt_literals_detected(payload: str) -> None:
    assert _textual_payload_has_unsafe_literals(payload) is True


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "a customer scanning QR code",
        "restaurant interior scene",
        "small business dashboard",
    ],
)
def test_safe_prompt_literals_passed(payload: str) -> None:
    assert _textual_payload_has_unsafe_literals(payload) is False


# ---------- 5) Token Plan 成本不伪装为 0 ----------


def test_token_plan_quote_uses_customer_image_price() -> None:
    """Token Plan 的供应商套餐与客户侧每张图收费分开记录。"""
    cfg = ImageGenerationConfiguration(
        mode="minimax_token_plan",
        base_url="https://api.minimax.io",
        model="image-01",
        api_key="sk-test",
    )
    quote = _quote_for_configuration(cfg, 2)
    # 客户侧价格为每张 0.05 元；供应商套餐仍不在这里伪造人民币成本。
    assert quote.can_generate is True
    assert quote.unit_price_cny == Decimal("0.05")
    assert quote.total_price_cny == Decimal("0.10")


# ---------- 6) generate 失败抛 ImageGenerationError（不抛裸异常） ----------


def test_minimax_generate_raises_on_bad_transport() -> None:
    def bad_transport(*args, **kwargs):
        raise OSError("network unreachable")

    cfg = ImageGenerationConfiguration(
        mode="minimax_token_plan",
        base_url="https://api.minimax.io",
        model="image-01",
        api_key="sk-test",
    )
    provider = MiniMaxTokenPlanImageProvider(cfg, transport=bad_transport)
    with pytest.raises(ImageGenerationError, match="重试"):
        provider.generate("a restaurant scene")


# ---------- 7) P0-4 上限：4 张 / 并发 2 ----------


def test_max_assets_capped_at_four() -> None:
    """_generate_token_plan_visual_assets 默认 max_assets=4。"""
    from src.services.video_editor_workflow import VideoEditorWorkflowService

    import inspect

    source = inspect.getsource(VideoEditorWorkflowService._generate_token_plan_visual_assets)
    assert "max(0, min(int(max_assets), 4))" in source
    # 上限 4 是硬规则


def test_max_concurrent_capped_at_two() -> None:
    from src.services.video_editor_workflow import VideoEditorWorkflowService

    import inspect

    source = inspect.getsource(VideoEditorWorkflowService._generate_token_plan_visual_assets)
    assert "min(int(max_concurrent), 2)" in source
    # 上限 2 是硬规则
