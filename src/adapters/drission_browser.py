"""Dual-engine browser wrapper supporting Playwright and DrissionPage.

This module provides a unified API for browser automation, allowing easy
switching between Playwright and DrissionPage engines.

Configuration:
    Set environment variable BROWSER_ENGINE to choose engine:
    - drission: Use DrissionPage (better anti-detection, recommended)
    - playwright: Use Playwright (fallback)

Usage:
    from src.adapters.drission_browser import connect_over_cdp, is_available

    # Connect to existing Chrome
    browser = connect_over_cdp(9222)
    context = browser.contexts[0]
    page = context.pages[0] or context.new_page()
    page.goto('https://example.com')
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

# Engine selection via environment variable
BROWSER_ENGINE = os.environ.get('BROWSER_ENGINE', 'drission').lower()

# 反检测注入脚本:每次页面导航前由 Playwright add_init_script 注入,
# 隐藏自动化特征,降低平台风控概率。任何一步失败都不影响页面加载。
ANTI_DETECTION_INIT_SCRIPT = """
(() => {
  try {
    // 隐藏 webdriver 标识
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    // 伪装插件列表
    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
    // 伪装语言设置
    Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
    // 覆盖权限查询行为
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
      parameters.name === 'notifications'
        ? Promise.resolve({state: Notification.permission})
        : originalQuery(parameters)
    );
    // 隐藏自动化相关特征
    Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
    Object.defineProperty(navigator, 'vendor', {get: () => 'Google Inc.'});
  } catch (e) { /* 反检测注入失败不阻断页面 */ }
})();
"""

# Check available engines
try:
    from DrissionPage import ChromiumPage as DrissionChromiumPage
    from DrissionPage import ChromiumOptions as DrissionChromiumOptions
    HAS_DRISSION = True
except ImportError:
    HAS_DRISSION = False
    DrissionChromiumPage = None
    DrissionChromiumOptions = None

try:
    from playwright.sync_api import (
        sync_playwright,
        Error as PlaywrightError,
    )
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    sync_playwright = None
    PlaywrightError = Exception


def is_available() -> bool:
    """Check if preferred engine is available."""
    if BROWSER_ENGINE == 'drission':
        return HAS_DRISSION
    return HAS_PLAYWRIGHT


def get_engine() -> str:
    """Get current engine name."""
    if BROWSER_ENGINE == 'drission' and HAS_DRISSION:
        return 'drission'
    if HAS_PLAYWRIGHT:
        return 'playwright'
    raise ImportError("No browser engine available. Install DrissionPage or Playwright.")


# ============================================================================
# Unified API Classes
# ============================================================================

class NetworkPacket:
    """Unified network response packet."""

    def __init__(self, packet: Any, engine: str) -> None:
        self._packet = packet
        self._engine = engine

    @property
    def url(self) -> str:
        if self._engine == 'drission':
            return self._packet.url if hasattr(self._packet, 'url') else ''
        return self._packet.url if hasattr(self._packet, 'url') else ''

    @property
    def status(self) -> int:
        if self._engine == 'drission':
            if hasattr(self._packet, 'response'):
                return self._packet.response.status_code
            return 0
        return self._packet.status if hasattr(self._packet, 'status') else 0

    def json(self) -> Any:
        """Parse response body as JSON."""
        import json
        if self._engine == 'drission':
            if hasattr(self._packet, 'response') and hasattr(self._packet.response, 'body'):
                body = self._packet.response.body
                if isinstance(body, str):
                    return json.loads(body)
                return body
        else:
            try:
                return self._packet.json()
            except Exception:
                pass
        return {}

    def text(self) -> str:
        """Get response body as text."""
        if self._engine == 'drission':
            if hasattr(self._packet, 'response') and hasattr(self._packet.response, 'body'):
                body = self._packet.response.body
                if isinstance(body, bytes):
                    return body.decode('utf-8', errors='replace')
                return str(body)
        else:
            try:
                return self._packet.text()
            except Exception:
                pass
        return ''


class NetworkListener:
    """Unified network listener."""

    def __init__(self, page: 'Page', engine: str) -> None:
        self._page = page
        self._engine = engine
        self._captured: list[NetworkPacket] = []
        self._callback: Optional[Any] = None

    def start(self, url_filter: Optional[str] = None) -> None:
        """Start listening for network requests."""
        self._captured = []
        if self._engine == 'drission':
            self._page._native.listen.start(url_filter or '')
        else:
            def _capture(response):
                try:
                    pkt = NetworkPacket(response, self._engine)
                    if url_filter and url_filter not in response.url:
                        return
                    self._captured.append(pkt)
                except Exception:
                    pass
            self._callback = _capture
            self._page._native.on('response', _capture)

    def stop(self) -> None:
        """Stop listening."""
        if self._engine == 'drission':
            try:
                self._page._native.listen.stop()
            except Exception:
                pass
        else:
            if self._callback:
                try:
                    self._page._native.remove_listener('response', self._callback)
                except Exception:
                    pass
                self._callback = None

    def steps(self, count: int = 999, timeout: float = 2) -> list[NetworkPacket]:
        """Get captured packets (DrissionPage only)."""
        if self._engine == 'drission':
            packets = []
            try:
                for pkt in self._page._native.listen.steps(count=count, timeout=timeout):
                    packets.append(NetworkPacket(pkt, self._engine))
            except Exception:
                pass
            return packets
        return self._captured

    @property
    def captured(self) -> list[NetworkPacket]:
        """Get all captured packets."""
        return self._captured


class Locator:
    """Unified element locator."""

    def __init__(self, page: 'Page', selector: str, engine: str) -> None:
        self._page = page
        self._selector = selector
        self._engine = engine

    def first(self) -> Optional[Any]:
        """Get first matching element."""
        try:
            if self._engine == 'drission':
                return self._page._native.ele(self._selector)
            else:
                return self._page._native.locator(self._selector)
        except Exception:
            return None

    def all(self) -> list[Any]:
        """Get all matching elements."""
        try:
            if self._engine == 'drission':
                return self._page._native.eles(self._selector)
            else:
                return self._page._native.locator(self._selector).all()
        except Exception:
            return []

    def inner_text(self, timeout: int = 3000) -> str:
        """Get inner text of first matching element."""
        if self._engine == 'drission':
            ele = self.first()
            return ele.text if ele else ''
        else:
            try:
                return self.first().inner_text(timeout=timeout)
            except Exception:
                return ''

    def get_attribute(self, name: str) -> Optional[str]:
        """Get attribute of first matching element."""
        if self._engine == 'drission':
            ele = self.first()
            return ele.attr(name) if ele else None
        else:
            try:
                return self.first().get_attribute(name)
            except Exception:
                return None

    def evaluate(self, expression: str) -> Any:
        """Evaluate JavaScript on first matching element."""
        if self._engine == 'drission':
            ele = self.first()
            return self._page._native.run_js(expression, ele) if ele else None
        else:
            try:
                return self.first().evaluate(expression)
            except Exception:
                return None

    def evaluate_all(self, expression: str) -> list[Any]:
        """Evaluate JavaScript on all matching elements."""
        if self._engine == 'drission':
            elements = self.all()
            results = []
            for ele in elements:
                try:
                    result = self._page._native.run_js(expression, ele)
                    results.append(result)
                except Exception:
                    results.append(None)
            return results
        else:
            try:
                return self.first().evaluate(expression)
            except Exception:
                return []

    def click(self) -> None:
        """Click first matching element."""
        if self._engine == 'drission':
            ele = self.first()
            if ele:
                ele.click()
        else:
            try:
                self.first().click()
            except Exception:
                pass

    def fill(self, value: str) -> None:
        """Fill input field."""
        if self._engine == 'drission':
            ele = self.first()
            if ele:
                ele.clear()
                ele.input(value)
        else:
            try:
                self.first().fill(value)
            except Exception:
                pass


class Keyboard:
    """Unified keyboard operations."""

    def __init__(self, page: 'Page', engine: str) -> None:
        self._page = page
        self._engine = engine

    def press(self, key: str) -> None:
        """Press a key."""
        if self._engine == 'drission':
            self._page._native.keyboard.press(key)
        else:
            self._page._native.keyboard.press(key)

    def type(self, text: str) -> None:
        """Type text."""
        if self._engine == 'drission':
            self._page._native.keyboard.type(text)
        else:
            self._page._native.keyboard.type(text)


class Page:
    """Unified page wrapper."""

    def __init__(self, native_page: Any, engine: str, owns_page: bool = True) -> None:
        self._native = native_page
        self._engine = engine
        self._owns_page = owns_page
        self._timeout = 30000
        self._network_listener = NetworkListener(self, engine)
        self._keyboard = Keyboard(self, engine)
        self._init_scripts: list[str] = []

    @property
    def url(self) -> str:
        """Get current URL."""
        if self._engine == 'drission':
            return self._native.url
        return self._native.url

    @property
    def html(self) -> str:
        """Get page HTML."""
        if self._engine == 'drission':
            return self._native.html
        return self._native.content()

    @property
    def title(self) -> str:
        """Get page title."""
        if self._engine == 'drission':
            return self._native.title
        return self._native.title()

    @property
    def listen(self) -> NetworkListener:
        """Get network listener."""
        return self._network_listener

    @property
    def keyboard(self) -> Keyboard:
        """Get keyboard."""
        return self._keyboard

    def goto(self, url: str, wait_until: str = 'domcontentloaded') -> Any:
        """Navigate to URL."""
        if self._engine == 'drission':
            self._native.get(url)
            if wait_until == 'domcontentloaded':
                self.wait(0.5)
            elif wait_until == 'networkidle':
                self.wait(2)
        else:
            self._native.goto(url, wait_until=wait_until)
        return self

    def go_back(self) -> None:
        """Go back."""
        if self._engine == 'drission':
            self._native.back()
        else:
            self._native.go_back()

    def go_forward(self) -> None:
        """Go forward."""
        if self._engine == 'drission':
            self._native.forward()
        else:
            self._native.go_forward()

    def reload(self) -> None:
        """Reload page."""
        if self._engine == 'drission':
            self._native.refresh()
        else:
            self._native.reload()

    def wait_for_timeout(self, ms: int) -> None:
        """Wait for specified milliseconds."""
        time.sleep(ms / 1000)

    def wait(self, seconds: float) -> None:
        """Wait for specified seconds."""
        time.sleep(seconds)

    def evaluate(self, expression: str) -> Any:
        """Evaluate JavaScript."""
        if self._engine == 'drission':
            return self._native.run_js(expression)
        else:
            return self._native.evaluate(expression)

    def locator(self, selector: str) -> Locator:
        """Create a locator for elements matching selector."""
        return Locator(self, selector, self._engine)

    def get_by_text(self, text: str, exact: bool = False) -> Locator:
        """Find element by text content."""
        if self._engine == 'drission':
            if exact:
                return Locator(self, f'text:{text}', self._engine)
            return Locator(self, f'text:{text}', self._engine)
        else:
            if exact:
                return Locator(self, f'text={text}', self._engine)
            return Locator(self, f'text={text}', self._engine)

    def query_selector(self, selector: str) -> Optional[Any]:
        """Find first element matching selector."""
        try:
            if self._engine == 'drission':
                return self._native.ele(selector)
            else:
                return self._native.locator(selector)
        except Exception:
            return None

    def query_selector_all(self, selector: str) -> list[Any]:
        """Find all elements matching selector."""
        try:
            if self._engine == 'drission':
                return self._native.eles(selector)
            else:
                return self._native.locator(selector).all()
        except Exception:
            return []

    def click(self, selector: str) -> None:
        """Click element matching selector."""
        ele = self.query_selector(selector)
        if ele:
            if self._engine == 'drission':
                ele.click()
            else:
                try:
                    ele.click()
                except Exception:
                    pass

    def fill(self, selector: str, value: str) -> None:
        """Fill input field."""
        ele = self.query_selector(selector)
        if ele:
            if self._engine == 'drission':
                ele.clear()
                ele.input(value)
            else:
                try:
                    ele.fill(value)
                except Exception:
                    pass

    def press(self, key: str) -> None:
        """Press a key."""
        self._keyboard.press(key)

    def scroll_down(self, pixels: int = 300) -> None:
        """Scroll down by pixels."""
        if self._engine == 'drission':
            self._native.scroll.down(pixels)
        else:
            self._native.evaluate(f'window.scrollBy(0, {pixels})')

    def scroll_up(self, pixels: int = 300) -> None:
        """Scroll up by pixels."""
        if self._engine == 'drission':
            self._native.scroll.up(pixels)
        else:
            self._native.evaluate(f'window.scrollBy(0, -{pixels})')

    def scroll_to_bottom(self) -> None:
        """Scroll to bottom of page."""
        if self._engine == 'drission':
            self._native.scroll.to_bottom()
        else:
            self._native.evaluate('window.scrollTo(0, document.body.scrollHeight)')

    def set_default_timeout(self, ms: int) -> None:
        """Set default timeout for operations."""
        self._timeout = ms
        if self._engine == 'playwright':
            self._native.set_default_timeout(ms)

    def close(self) -> None:
        """Close the page."""
        if self._owns_page:
            try:
                if self._engine == 'drission':
                    self._native.close()
                else:
                    self._native.close()
            except Exception:
                pass

    def is_closed(self) -> bool:
        """Check if page is closed."""
        try:
            _ = self.url
            return False
        except Exception:
            return True

    def add_init_script(self, script: str) -> None:
        """Add initialization script (runs on every navigation)."""
        self._init_scripts.append(script)
        if self._engine == 'drission':
            self._native.run_js(script)
        else:
            try:
                self._native.context.add_init_script(script)
            except Exception:
                pass


class BrowserContext:
    """Unified browser context."""

    def __init__(self, browser: 'Browser', context: Any = None) -> None:
        self._browser = browser
        self._context = context

    def new_page(self) -> Page:
        """Create a new page in this context."""
        if self._browser._engine == 'drission':
            return Page(self._browser._native, 'drission', owns_page=False)
        else:
            page = self._context.new_page()
            return Page(page, 'playwright', owns_page=True)

    @property
    def pages(self) -> list[Page]:
        """Get all pages in this context."""
        if self._browser._engine == 'drission':
            return [Page(self._browser._native, 'drission', owns_page=False)]
        else:
            return [Page(p, 'playwright', owns_page=False) for p in self._context.pages]

    def add_init_script(self, script: str) -> None:
        """Add initialization script to all pages."""
        if self._browser._engine == 'drission':
            self._browser._native.run_js(script)
        else:
            self._context.add_init_script(script)


class Browser:
    """Unified browser wrapper."""

    def __init__(self, native: Any, engine: str, owns_browser: bool = True) -> None:
        self._native = native
        self._engine = engine
        self._owns_browser = owns_browser
        self._playwright = None
        self._playwright_browser = None

    @property
    def contexts(self) -> list[BrowserContext]:
        """Get browser contexts."""
        if self._engine == 'drission':
            return [BrowserContext(self)]
        else:
            return [BrowserContext(self, ctx) for ctx in self._playwright_browser.contexts]

    def new_page(self) -> Page:
        """Create a new page."""
        if self._engine == 'drission':
            return Page(self._native, 'drission', owns_page=False)
        else:
            page = self._playwright_browser.new_page()
            return Page(page, 'playwright', owns_page=True)

    def close(self) -> None:
        """Close browser."""
        if self._owns_browser:
            try:
                if self._engine == 'drission':
                    self._native.quit()
                else:
                    if self._playwright_browser:
                        self._playwright_browser.close()
                    if self._playwright:
                        self._playwright.__exit__(None, None, None)
            except Exception:
                pass

    def quit(self) -> None:
        """Quit browser."""
        self.close()


# ============================================================================
# Factory Functions
# ============================================================================

def connect_over_cdp(debug_port: int) -> Browser:
    """Connect to Chrome via CDP and return a Browser instance.

    This is the main entry point. Supports both DrissionPage and Playwright.

    Args:
        debug_port: Chrome DevTools Protocol port

    Returns:
        Browser instance

    Raises:
        ConnectionError: If connection fails
        ImportError: If no engine is available
    """
    engine = get_engine()

    if engine == 'drission':
        return _connect_drission(debug_port)
    else:
        return _connect_playwright(debug_port)


def _connect_drission(debug_port: int) -> Browser:
    """Connect using DrissionPage."""
    co = DrissionChromiumOptions()
    co.set_local_port(debug_port)

    try:
        page = DrissionChromiumPage(co)
        return Browser(page, 'drission', owns_browser=False)
    except Exception as e:
        raise ConnectionError(
            f"Failed to connect to Chrome on port {debug_port} via DrissionPage: {e}"
        ) from e


def _connect_playwright(debug_port: int) -> Browser:
    """Connect using Playwright."""
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{debug_port}")
        result = Browser(browser, 'playwright', owns_browser=False)
        result._playwright = pw
        result._playwright_browser = browser
        return result
    except Exception as e:
        raise ConnectionError(
            f"Failed to connect to Chrome on port {debug_port} via Playwright: {e}"
        ) from e


def create_browser(headless: bool = False, **kwargs) -> Browser:
    """Create a new browser instance.

    Args:
        headless: Run in headless mode
        **kwargs: Additional browser options

    Returns:
        Browser instance
    """
    engine = get_engine()

    if engine == 'drission':
        co = DrissionChromiumOptions()
        if headless:
            co.headless()
        co.set_argument('--disable-blink-features=AutomationControlled')
        co.set_argument('--no-first-run')
        co.set_argument('--no-default-browser-check')
        co.set_argument('--disable-infobars')
        for key, value in kwargs.items():
            co.set_argument(f'--{key}={value}')
        page = DrissionChromiumPage(co)
        return Browser(page, 'drission', owns_browser=True)
    else:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=headless)
        result = Browser(browser, 'playwright', owns_browser=True)
        result._playwright = pw
        result._playwright_browser = browser
        return result
