"""浏览器自动化引擎

使用 Playwright 实现多平台内容发布自动化。
参考 MediaPublishPlatform 的 baseFileUploader.py。

核心流程：
1. 检测文件类型
2. 验证 Cookie/登录状态
3. 启动浏览器（反检测）
4. 导航到创作者页面
5. 上传媒体文件
6. 填写标题/描述/标签
7. 设置封面（可选）
8. 点击发布
9. 验证发布结果
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from src.services.platform_config import (
    PlatformConfig,
    PlatformSelectors,
    get_platform_config,
)

logger = logging.getLogger(__name__)


class UploadStatus(str, Enum):
    """上传状态"""
    PENDING = "pending"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    PUBLISHING = "publishing"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class PublishTask:
    """发布任务"""
    task_id: str
    platform: str
    file_path: str
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    thumbnail_path: str | None = None
    schedule_time: str | None = None

    # 状态
    status: UploadStatus = UploadStatus.PENDING
    progress: int = 0
    message: str = ""
    error: str | None = None

    # 结果
    publish_url: str | None = None


@dataclass
class PublishResult:
    """发布结果"""
    success: bool
    task_id: str
    platform: str
    message: str
    publish_url: str | None = None
    error: str | None = None


class BrowserAutomation:
    """浏览器自动化引擎"""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._browser = None
        self._context = None

    async def init_browser(self):
        """初始化浏览器"""
        try:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--no-sandbox",
                    "--disable-features=OptimizationGuideOnDeviceModel",
                ],
            )
            logger.info("浏览器初始化成功")
        except ImportError:
            logger.error("Playwright 未安装，请运行: pip install playwright && playwright install chromium")
            raise
        except Exception as e:
            logger.error(f"浏览器初始化失败: {e}")
            raise

    async def close(self):
        """关闭浏览器"""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if hasattr(self, '_playwright') and self._playwright:
            await self._playwright.stop()

    async def create_context(self, cookies: list[dict] | None = None):
        """创建浏览器上下文"""
        if not self._browser:
            await self.init_browser()

        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )

        # 注入 Cookie
        if cookies:
            await self._context.add_cookies(cookies)

        # 注入反检测脚本
        await self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
        """)

        return self._context

    async def publish_video(self, task: PublishTask, on_progress: Callable | None = None) -> PublishResult:
        """发布视频到指定平台"""
        config = get_platform_config(task.platform)
        if not config:
            return PublishResult(
                success=False,
                task_id=task.task_id,
                platform=task.platform,
                message=f"不支持的平台: {task.platform}",
                error="unsupported_platform",
            )

        try:
            # 更新状态
            task.status = UploadStatus.UPLOADING
            task.progress = 10
            task.message = "正在打开浏览器..."
            if on_progress:
                on_progress(task)

            # 创建上下文
            context = await self.create_context()
            page = await context.new_page()

            # 1. 导航到创作者页面
            task.progress = 20
            task.message = "正在打开创作者页面..."
            if on_progress:
                on_progress(task)

            await page.goto(config.creator_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            # 2. 检查登录状态
            task.progress = 30
            task.message = "检查登录状态..."
            if on_progress:
                on_progress(task)

            if config.need_login_check:
                is_logged_in = await self._check_login(page, config)
                if not is_logged_in:
                    return PublishResult(
                        success=False,
                        task_id=task.task_id,
                        platform=task.platform,
                        message="未登录，请先登录账号",
                        error="not_logged_in",
                    )

            # 3. 上传文件
            task.progress = 40
            task.message = "正在上传文件..."
            if on_progress:
                on_progress(task)

            upload_success = await self._upload_file(page, config.selectors, task.file_path)
            if not upload_success:
                return PublishResult(
                    success=False,
                    task_id=task.task_id,
                    platform=task.platform,
                    message="文件上传失败",
                    error="upload_failed",
                )

            # 4. 等待上传完成
            task.progress = 60
            task.message = "等待上传完成..."
            if on_progress:
                on_progress(task)

            await self._wait_upload_complete(page, config.selectors)

            # 5. 填写标题和描述
            task.progress = 70
            task.message = "正在填写标题和描述..."
            if on_progress:
                on_progress(task)

            await self._fill_content(page, config.selectors, task.title, task.description, task.tags)

            # 6. 点击发布
            task.progress = 90
            task.message = "正在发布..."
            if on_progress:
                on_progress(task)

            publish_success = await self._click_publish(page, config.selectors)
            if not publish_success:
                return PublishResult(
                    success=False,
                    task_id=task.task_id,
                    platform=task.platform,
                    message="发布失败",
                    error="publish_failed",
                )

            # 7. 验证发布结果
            task.progress = 100
            task.status = UploadStatus.SUCCESS
            task.message = "发布成功"
            if on_progress:
                on_progress(task)

            return PublishResult(
                success=True,
                task_id=task.task_id,
                platform=task.platform,
                message="发布成功",
                publish_url=page.url,
            )

        except Exception as e:
            logger.error(f"发布失败: {e}")
            task.status = UploadStatus.FAILED
            task.error = str(e)
            return PublishResult(
                success=False,
                task_id=task.task_id,
                platform=task.platform,
                message=f"发布失败: {str(e)}",
                error=str(e),
            )
        finally:
            await self.close()

    async def _check_login(self, page, config: PlatformConfig) -> bool:
        """检查登录状态"""
        try:
            # 检查是否跳转到登录页
            current_url = page.url
            if "login" in current_url or "signin" in current_url or "auth" in current_url:
                return False

            # 检查页面内容
            content = await page.content()
            login_keywords = ["登录", "login", "signin", "注册"]
            for keyword in login_keywords:
                if keyword in content.lower():
                    # 进一步确认是否真的需要登录
                    # 有些页面可能只是有登录链接
                    pass

            return True
        except Exception as e:
            logger.warning(f"登录检查失败: {e}")
            return True  # 假设已登录

    async def _upload_file(self, page, selectors: PlatformSelectors, file_path: str) -> bool:
        """上传文件"""
        try:
            # 查找文件输入元素
            file_input = await self._find_element(page, selectors.file_input or selectors.upload_button)
            if not file_input:
                logger.error("未找到文件上传元素")
                return False

            # 上传文件
            await file_input.set_input_files(file_path)
            logger.info(f"文件已选择: {file_path}")
            return True
        except Exception as e:
            logger.error(f"文件上传失败: {e}")
            return False

    async def _wait_upload_complete(self, page, selectors: PlatformSelectors, timeout: int = 300):
        """等待上传完成"""
        try:
            # 等待上传进度消失或成功指示出现
            for selector in selectors.upload_complete:
                try:
                    await page.wait_for_selector(selector, timeout=timeout * 1000)
                    logger.info(f"上传完成指示: {selector}")
                    return True
                except Exception:
                    continue

            # 如果没有明确的完成指示，等待一段时间
            await asyncio.sleep(5)
            return True
        except Exception as e:
            logger.warning(f"等待上传完成超时: {e}")
            return False

    async def _fill_content(self, page, selectors: PlatformSelectors, title: str, description: str, tags: list[str]):
        """填写内容"""
        try:
            # 填写标题
            title_input = await self._find_element(selectors.title_input)
            if title_input:
                await title_input.click()
                await title_input.fill("")
                await title_input.type(title, delay=50)
                logger.info(f"标题已填写: {title}")

            # 填写描述
            desc_input = await self._find_element(selectors.description_input)
            if desc_input:
                await desc_input.click()
                await desc_input.fill("")
                await desc_input.type(description, delay=30)
                logger.info("描述已填写")

            # 添加标签
            if tags:
                tags_input = await self._find_element(selectors.tags_input)
                if tags_input:
                    for tag in tags[:5]:  # 限制标签数量
                        await tags_input.click()
                        await tags_input.type(tag, delay=30)
                        await page.keyboard.press("Enter")
                        await asyncio.sleep(0.5)
                    logger.info(f"标签已添加: {tags}")

        except Exception as e:
            logger.error(f"填写内容失败: {e}")

    async def _click_publish(self, page, selectors: PlatformSelectors) -> bool:
        """点击发布按钮"""
        try:
            publish_btn = await self._find_element(selectors.publish_button)
            if not publish_btn:
                logger.error("未找到发布按钮")
                return False

            await publish_btn.click()
            logger.info("发布按钮已点击")

            # 等待发布完成
            await asyncio.sleep(3)

            # 检查是否有成功指示
            for selector in selectors.success_indicator:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        logger.info(f"发布成功指示: {selector}")
                        return True
                except Exception:
                    continue

            # 检查是否有错误指示
            for selector in selectors.error_indicator:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        error_text = await element.text_content()
                        logger.error(f"发布错误: {error_text}")
                        return False
                except Exception:
                    continue

            logger.warning("未检测到平台明确的发布成功信号，按未确认处理")
            return False

        except Exception as e:
            logger.error(f"点击发布失败: {e}")
            return False

    async def _find_element(self, page, selectors: list[str]):
        """查找元素（多选择器容错）"""
        for selector in selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    return element
            except Exception:
                continue
        return None


# 全局实例
browser_automation = BrowserAutomation()
