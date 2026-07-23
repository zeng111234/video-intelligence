"""平台配置驱动模块

集中管理所有发布平台的配置信息，包括：
- 平台 URL
- CSS/XPath 选择器（多选择器容错）
- 平台能力（支持的格式、大小限制等）
- 功能开关

参考: MediaPublishPlatform 的 platform_configs.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class PlatformType(str, Enum):
    """平台类型"""
    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    BILIBILI = "bilibili"
    KUAISHOU = "kuaishou"
    WECHAT_CHANNELS = "wechat_channels"
    TIKTOK = "tiktok"
    YOUTUBE = "youtube"


@dataclass
class PlatformCapability:
    """平台能力配置"""
    supports_video: bool = True
    supports_image: bool = True
    max_video_size_mb: int = 500
    max_image_size_mb: int = 20
    supported_video_formats: list[str] = field(default_factory=lambda: ["mp4", "mov"])
    supported_image_formats: list[str] = field(default_factory=lambda: ["jpg", "jpeg", "png"])
    max_title_length: int = 100
    max_description_length: int = 2000
    max_tags: int = 10
    supports_schedule: bool = False
    supports_location: bool = False
    supports_thumbnail: bool = True


@dataclass
class PlatformSelectors:
    """平台页面选择器（多选择器容错）"""
    # 上传相关
    upload_button: list[str] = field(default_factory=list)
    file_input: list[str] = field(default_factory=list)
    upload_progress: list[str] = field(default_factory=list)
    upload_complete: list[str] = field(default_factory=list)

    # 内容编辑
    title_input: list[str] = field(default_factory=list)
    description_input: list[str] = field(default_factory=list)
    tags_input: list[str] = field(default_factory=list)

    # 封面/缩略图
    thumbnail_button: list[str] = field(default_factory=list)
    thumbnail_upload: list[str] = field(default_factory=list)
    thumbnail_confirm: list[str] = field(default_factory=list)

    # 发布
    publish_button: list[str] = field(default_factory=list)
    schedule_button: list[str] = field(default_factory=list)
    confirm_button: list[str] = field(default_factory=list)

    # 状态检测
    success_indicator: list[str] = field(default_factory=list)
    error_indicator: list[str] = field(default_factory=list)


@dataclass
class PlatformConfig:
    """平台完整配置"""
    key: str
    name: str
    icon: str
    color: str
    bg_color: str
    description: str

    # URL 配置
    creator_url: str
    login_url: str
    personal_url: str

    # 选择器配置
    selectors: PlatformSelectors

    # 能力配置
    capability: PlatformCapability

    # 功能开关
    skip_cookie_verify: bool = False
    headless: bool = True
    need_login_check: bool = True


# ============================================================
# 平台配置定义
# ============================================================

PLATFORM_CONFIGS: dict[str, PlatformConfig] = {
    "douyin": PlatformConfig(
        key="douyin",
        name="抖音",
        icon="🎵",
        color="#000000",
        bg_color="#f0f0f0",
        description="日活 7 亿+，短视频首选平台",
        creator_url="https://creator.douyin.com/creator-micro/content/upload",
        login_url="https://creator.douyin.com/",
        personal_url="https://creator.douyin.com/creator-micro/home",
        selectors=PlatformSelectors(
            upload_button=[
                "input[type='file']",
                "[class*='upload'] input[type='file']",
                "[class*='upload-btn']",
            ],
            file_input=["input[type='file'][accept*='video']"],
            upload_progress=["[class*='progress']", "[class*='uploading']"],
            upload_complete=["[class*='success']", "[class*='complete']"],
            title_input=[
                "[data-testid='title-input']",
                "[class*='title'] input",
                "[class*='title'] textarea",
                "input[placeholder*='标题']",
                "textarea[placeholder*='标题']",
            ],
            description_input=[
                "[data-testid='desc-input']",
                "[class*='desc'] textarea",
                "textarea[placeholder*='描述']",
                "textarea[placeholder*='正文']",
            ],
            tags_input=[
                "[class*='tag'] input",
                "input[placeholder*='标签']",
                "input[placeholder*='话题']",
            ],
            publish_button=[
                "button:has-text('发布')",
                "[class*='publish'] button",
                "button[class*='submit']",
                "button:has-text('Publish')",
            ],
            success_indicator=[
                "[class*='success']",
                "text='发布成功'",
                "text='上传完成'",
            ],
        ),
        capability=PlatformCapability(
            max_video_size_mb=500,
            max_title_length=55,
            max_description_length=1000,
            supports_schedule=True,
            supports_location=True,
        ),
    ),

    "xiaohongshu": PlatformConfig(
        key="xiaohongshu",
        name="小红书",
        icon="📕",
        color="#ff2442",
        bg_color="#fff0f3",
        description="种草社区，女性用户为主",
        creator_url="https://creator.xiaohongshu.com/publish/publish",
        login_url="https://creator.xiaohongshu.com/",
        personal_url="https://creator.xiaohongshu.com/",
        selectors=PlatformSelectors(
            upload_button=[
                "input[type='file']",
                "[class*='upload'] input",
                "[class*='upload-btn']",
            ],
            file_input=["input[type='file'][accept*='video']"],
            title_input=[
                "[class*='title'] input",
                "input[placeholder*='标题']",
                "#title",
            ],
            description_input=[
                "[class*='content'] [contenteditable='true']",
                "[class*='desc'] textarea",
                "textarea[placeholder*='描述']",
                "#content-textarea",
            ],
            tags_input=[
                "[class*='tag'] input",
                "input[placeholder*='标签']",
                "input[placeholder*='话题']",
            ],
            thumbnail_button=[
                "[class*='cover'] button",
                "button:has-text('更换封面')",
            ],
            publish_button=[
                "button:has-text('发布')",
                "[class*='publish'] button",
                "button[class*='submit']",
            ],
            success_indicator=[
                "[class*='success']",
                "text='发布成功'",
            ],
        ),
        capability=PlatformCapability(
            max_video_size_mb=100,
            max_title_length=20,
            max_description_length=1000,
            max_tags=5,
            supports_schedule=True,
        ),
    ),

    "bilibili": PlatformConfig(
        key="bilibili",
        name="B站",
        icon="📺",
        color="#00a1d6",
        bg_color="#f0f9ff",
        description="年轻用户聚集地，内容生态丰富",
        creator_url="https://member.bilibili.com/platform/upload/video/frame",
        login_url="https://member.bilibili.com/",
        personal_url="https://member.bilibili.com/platform/home",
        selectors=PlatformSelectors(
            upload_button=[
                "input[type='file']",
                "[class*='upload'] input",
                "[class*='bcc-upload'] input",
            ],
            file_input=["input[type='file'][accept*='video']"],
            title_input=[
                "[class*='title'] input",
                "input[placeholder*='标题']",
                "input[maxlength='80']",
            ],
            description_input=[
                "[class*='desc'] textarea",
                "textarea[placeholder*='简介']",
                "textarea[maxlength='2000']",
            ],
            tags_input=[
                "[class*='tag'] input",
                "input[placeholder*='标签']",
                "input[placeholder*='相关话题']",
            ],
            publish_button=[
                "button:has-text('投稿')",
                "button:has-text('发布')",
                "[class*='submit'] button",
            ],
            success_indicator=[
                "[class*='success']",
                "text='投稿成功'",
                "text='发布成功'",
            ],
        ),
        capability=PlatformCapability(
            max_video_size_mb=8192,
            max_title_length=80,
            max_description_length=2000,
            max_tags=10,
            supports_schedule=True,
        ),
    ),

    "kuaishou": PlatformConfig(
        key="kuaishou",
        name="快手",
        icon="🎬",
        color="#ff4906",
        bg_color="#fff5f0",
        description="下沉市场覆盖广，直播带货强",
        creator_url="https://cp.kuaishou.com/article/publish/video",
        login_url="https://cp.kuaishou.com/",
        personal_url="https://cp.kuaishou.com/",
        selectors=PlatformSelectors(
            upload_button=[
                "input[type='file']",
                "[class*='upload'] input",
            ],
            file_input=["input[type='file'][accept*='video']"],
            title_input=[
                "[class*='title'] input",
                "input[placeholder*='标题']",
                "textarea[placeholder*='描述']",
            ],
            publish_button=[
                "button:has-text('发布')",
                "[class*='publish'] button",
            ],
            success_indicator=[
                "[class*='success']",
                "text='发布成功'",
            ],
        ),
        capability=PlatformCapability(
            max_video_size_mb=500,
            max_title_length=50,
            max_description_length=500,
        ),
    ),

    "wechat_channels": PlatformConfig(
        key="wechat_channels",
        name="视频号",
        icon="💬",
        color="#07c160",
        bg_color="#f0fff4",
        description="微信生态，私域流量入口",
        creator_url="https://channels.weixin.qq.com/platform/post/create",
        login_url="https://channels.weixin.qq.com/",
        personal_url="https://channels.weixin.qq.com/platform/post/list",
        selectors=PlatformSelectors(
            upload_button=[
                "input[type='file']",
                "[class*='upload'] input",
            ],
            file_input=["input[type='file'][accept*='video']"],
            title_input=[
                "[class*='title'] input",
                "input[placeholder*='描述']",
                "textarea[placeholder*='描述']",
            ],
            publish_button=[
                "button:has-text('发表')",
                "button:has-text('发布')",
                "[class*='publish'] button",
            ],
            success_indicator=[
                "[class*='success']",
                "text='发表成功'",
            ],
        ),
        capability=PlatformCapability(
            max_video_size_mb=500,
            max_title_length=1000,
            supports_schedule=True,
        ),
    ),
}


def get_platform_config(platform_key: str) -> PlatformConfig | None:
    """获取平台配置"""
    return PLATFORM_CONFIGS.get(platform_key)


def get_all_platforms() -> list[PlatformConfig]:
    """获取所有平台配置"""
    return list(PLATFORM_CONFIGS.values())


def get_platform_names() -> dict[str, str]:
    """获取平台名称映射"""
    return {key: config.name for key, config in PLATFORM_CONFIGS.items()}
