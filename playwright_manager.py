"""
Playwright浏览器自动化支持
作为Selenium的替代方案，更现代、更快速
"""

import asyncio
from playwright.async_api import async_playwright, Browser, Page, BrowserContext
from typing import Optional, Dict, Any
import json


class PlaywrightManager:
    """Playwright浏览器管理器"""

    def __init__(self):
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.contexts: Dict[int, BrowserContext] = {}  # account_id -> context
        self.pages: Dict[int, Page] = {}  # account_id -> page

    async def initialize(self):
        """初始化Playwright"""
        if not self.playwright:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=False,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                    '--disable-setuid-sandbox'
                ]
            )

    async def create_context(
        self,
        account_id: int,
        cookies: Optional[list] = None,
        user_agent: Optional[str] = None
    ) -> BrowserContext:
        """
        创建浏览器上下文

        Args:
            account_id: 账号ID
            cookies: Cookie列表
            user_agent: User-Agent

        Returns:
            BrowserContext实例
        """
        if not self.browser:
            await self.initialize()

        # 创建上下文
        context = await self.browser.new_context(
            user_agent=user_agent or 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            viewport={'width': 1920, 'height': 1080},
            locale='zh-CN',
            timezone_id='Asia/Shanghai'
        )

        # 设置Cookie
        if cookies:
            await context.add_cookies(cookies)

        # 反检测设置
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        self.contexts[account_id] = context
        return context

    async def get_or_create_page(self, account_id: int) -> Page:
        """
        获取或创建页面

        Args:
            account_id: 账号ID

        Returns:
            Page实例
        """
        if account_id in self.pages:
            return self.pages[account_id]

        # 获取或创建上下文
        if account_id not in self.contexts:
            await self.create_context(account_id)

        context = self.contexts[account_id]
        page = await context.new_page()

        self.pages[account_id] = page
        return page

    async def send_douyin_message(
        self,
        account_id: int,
        to_uid: str,
        content: str,
        cookie_data: str
    ) -> tuple[bool, str]:
        """
        使用Playwright发送抖音消息

        Args:
            account_id: 账号ID
            to_uid: 目标用户ID
            content: 消息内容
            cookie_data: Cookie数据

        Returns:
            (是否成功, 错误信息)
        """
        try:
            # 解析Cookie
            cookies = self._parse_cookies(cookie_data)

            # 创建上下文
            if account_id not in self.contexts:
                await self.create_context(account_id, cookies)

            # 获取页面
            page = await self.get_or_create_page(account_id)

            # 访问抖音消息页面
            await page.goto(f'https://www.douyin.com/user/{to_uid}')
            await page.wait_for_load_state('networkidle')

            # 点击"私信"按钮
            send_button = await page.wait_for_selector('text=私信', timeout=10000)
            await send_button.click()

            # 等待输入框出现
            input_box = await page.wait_for_selector('textarea[placeholder*="消息"]', timeout=10000)

            # 输入消息
            await input_box.fill(content)

            # 点击发送
            send_btn = await page.wait_for_selector('button:has-text("发送")', timeout=5000)
            await send_btn.click()

            # 等待发送完成
            await page.wait_for_timeout(1000)

            return True, ""

        except Exception as e:
            return False, str(e)

    async def close_context(self, account_id: int):
        """
        关闭浏览器上下文

        Args:
            account_id: 账号ID
        """
        if account_id in self.pages:
            await self.pages[account_id].close()
            del self.pages[account_id]

        if account_id in self.contexts:
            await self.contexts[account_id].close()
            del self.contexts[account_id]

    async def close_all(self):
        """关闭所有浏览器资源"""
        for page in self.pages.values():
            await page.close()

        for context in self.contexts.values():
            await context.close()

        if self.browser:
            await self.browser.close()

        if self.playwright:
            await self.playwright.stop()

        self.pages.clear()
        self.contexts.clear()
        self.browser = None
        self.playwright = None

    def _parse_cookies(self, cookie_data: str) -> list:
        """
        解析Cookie数据为Playwright格式

        Args:
            cookie_data: Cookie JSON字符串

        Returns:
            Cookie列表
        """
        try:
            cookies_dict = json.loads(cookie_data)

            cookies = []
            for name, value in cookies_dict.items():
                cookies.append({
                    'name': name,
                    'value': value,
                    'domain': '.douyin.com',
                    'path': '/'
                })

            return cookies

        except Exception:
            return []


# 全局实例
_playwright_manager: Optional[PlaywrightManager] = None


def get_playwright_manager() -> PlaywrightManager:
    """获取全局Playwright管理器"""
    global _playwright_manager
    if _playwright_manager is None:
        _playwright_manager = PlaywrightManager()
    return _playwright_manager


# 使用示例
async def main():
    manager = get_playwright_manager()

    try:
        # 初始化
        await manager.initialize()

        # 发送消息
        cookie_data = '{"sessionid": "xxx", "sid_guard": "xxx"}'
        success, error = await manager.send_douyin_message(
            account_id=1,
            to_uid='123456',
            content='Hello from Playwright!',
            cookie_data=cookie_data
        )

        if success:
            print("消息发送成功")
        else:
            print(f"消息发送失败: {error}")

    finally:
        await manager.close_all()


if __name__ == '__main__':
    asyncio.run(main())
