"""发布适配器包。

提供多平台发布适配器：
- SandboxPublisher: 沙箱/演示发布器
- DouyinPublisher: 抖音发布适配器（需真实凭证）
- KuaishouPublisher: 快手发布适配器（需真实凭证）
- WeChatChannelsPublisher: 视频号发布适配器（需真实凭证）
"""

from src.adapters.publishers.sandbox import SandboxPublisher

__all__ = ["SandboxPublisher"]
