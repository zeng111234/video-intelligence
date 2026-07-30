from __future__ import annotations

from src.models import Platform, PlatformCapability

SUPPORTED_PLATFORMS = (
    Platform.DOUYIN,
    Platform.XIAOHONGSHU,
    Platform.WECHAT_CHANNELS,
)

PLATFORM_LABELS = {
    Platform.DOUYIN: "抖音",
    Platform.BILIBILI: "B站",
    Platform.XIAOHONGSHU: "小红书",
    Platform.WECHAT_CHANNELS: "微信视频号",
    Platform.KUAISHOU: "快手",
}


def platform_label(platform: Platform) -> str:
    return PLATFORM_LABELS[platform]


def platform_capabilities(*, douyin_configured: bool) -> list[PlatformCapability]:
    return [
        PlatformCapability(
            platform=Platform.DOUYIN,
            label="抖音",
            status_label="待授权 / 受限自动获取",
            description=(
                "已配置凭证，仍需用真实 Scope/OAuth 调用验收。"
                if douyin_configured
                else "未配置凭证，不会发起平台请求。"
            ),
            supports_automatic_search=True,
            automatic_search_enabled=douyin_configured,
        ),
        PlatformCapability(
            platform=Platform.XIAOHONGSHU,
            label="小红书",
            status_label="仅手工 / CSV",
            description="未接入全站关键词自动搜索；不会发起平台请求。",
        ),
        PlatformCapability(
            platform=Platform.WECHAT_CHANNELS,
            label="微信视频号",
            status_label="仅手工 / CSV",
            description="未接入全站关键词自动搜索；不会发起平台请求。",
        ),
    ]
