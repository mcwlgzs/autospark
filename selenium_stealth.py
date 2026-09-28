"""Selenium 反检测和优化模块

使用 undetected-chromedriver 和多种反检测技术提升成功率
"""

import os
import random
import time
from typing import Optional
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

try:
    import undetected_chromedriver as uc
    USE_UC = True
except ImportError:
    USE_UC = False
    print("⚠️  undetected-chromedriver 未安装，使用标准 Selenium")


class StealthDriver:
    """反检测浏览器驱动"""

    def __init__(
        self,
        chrome_binary: str = None,
        profile_dir: str = None,
        headless: bool = False,
        use_undetected: bool = True
    ):
        self.chrome_binary = chrome_binary
        self.profile_dir = profile_dir
        self.headless = headless
        self.use_undetected = use_undetected and USE_UC
        self.driver = None

    def create_driver(self):
        """创建反检测浏览器实例"""
        if self.use_undetected:
            return self._create_undetected_driver()
        else:
            return self._create_stealth_driver()

    def _create_undetected_driver(self):
        """使用 undetected-chromedriver"""
        options = uc.ChromeOptions()

        # 基础配置
        if self.profile_dir:
            options.add_argument(f'--user-data-dir={self.profile_dir}')

        # 性能优化
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--no-sandbox')

        # 隐私和安全
        options.add_argument('--disable-web-security')
        options.add_argument('--allow-running-insecure-content')

        # 不加载图片（可选，加速）
        # prefs = {"profile.managed_default_content_settings.images": 2}
        # options.add_experimental_option("prefs", prefs)

        if self.headless:
            options.add_argument('--headless=new')

        if self.chrome_binary:
            options.binary_location = self.chrome_binary

        # 创建驱动
        driver = uc.Chrome(
            options=options,
            version_main=None,  # 自动检测版本
            use_subprocess=False
        )

        # 设置隐式等待
        driver.implicitly_wait(10)

        # 设置窗口大小
        if not self.headless:
            driver.set_window_size(1920, 1080)

        # 执行反检测脚本
        self._inject_stealth_js(driver)

        return driver

    def _create_stealth_driver(self):
        """使用标准 Selenium + 反检测配置"""
        options = Options()

        # 基础配置
        if self.profile_dir:
            options.add_argument(f'--user-data-dir={self.profile_dir}')

        # 反检测配置
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)

        # 性能优化
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-gpu')

        # 设置真实的 User-Agent
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        ]
        options.add_argument(f'user-agent={random.choice(user_agents)}')

        if self.headless:
            options.add_argument('--headless=new')

        if self.chrome_binary:
            options.binary_location = self.chrome_binary

        # 创建驱动
        driver = webdriver.Chrome(options=options)

        # 设置隐式等待
        driver.implicitly_wait(10)

        # 设置窗口大小
        if not self.headless:
            driver.set_window_size(1920, 1080)

        # 执行反检测脚本
        self._inject_stealth_js(driver)

        return driver

    def _inject_stealth_js(self, driver):
        """注入反检测 JavaScript"""
        stealth_js = """
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });

        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });

        Object.defineProperty(navigator, 'languages', {
            get: () => ['zh-CN', 'zh', 'en']
        });

        window.chrome = {
            runtime: {}
        };

        Object.defineProperty(navigator, 'permissions', {
            get: () => ({
                query: Promise.resolve({ state: 'granted' })
            })
        });
        """

        try:
            driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': stealth_js
            })
        except:
            # CDP 命令可能不支持，忽略
            pass


class SmartWait:
    """智能等待工具"""

    @staticmethod
    def wait_for_element(
        driver,
        by: By,
        value: str,
        timeout: int = 10,
        condition = None
    ):
        """等待元素出现"""
        try:
            if condition is None:
                condition = EC.presence_of_element_located

            element = WebDriverWait(driver, timeout).until(
                condition((by, value))
            )
            return element
        except TimeoutException:
            return None

    @staticmethod
    def wait_for_any(driver, locators: list, timeout: int = 10):
        """等待多个选择器中的任意一个"""
        try:
            return WebDriverWait(driver, timeout).until(
                lambda d: any(
                    d.find_elements(by, value)
                    for by, value in locators
                )
            )
        except TimeoutException:
            return None

    @staticmethod
    def wait_for_clickable(driver, by: By, value: str, timeout: int = 10):
        """等待元素可点击"""
        return SmartWait.wait_for_element(
            driver, by, value, timeout,
            EC.element_to_be_clickable
        )

    @staticmethod
    def wait_for_visible(driver, by: By, value: str, timeout: int = 10):
        """等待元素可见"""
        return SmartWait.wait_for_element(
            driver, by, value, timeout,
            EC.visibility_of_element_located
        )

    @staticmethod
    def human_like_delay(min_ms: int = 500, max_ms: int = 2000):
        """人类式延迟"""
        delay = random.uniform(min_ms, max_ms) / 1000.0
        time.sleep(delay)

    @staticmethod
    def random_mouse_movement(driver):
        """随机鼠标移动（模拟人类行为）"""
        try:
            from selenium.webdriver.common.action_chains import ActionChains

            # 获取窗口尺寸
            size = driver.get_window_size()
            width = size['width']
            height = size['height']

            # 随机移动
            actions = ActionChains(driver)
            for _ in range(random.randint(2, 5)):
                x = random.randint(0, width)
                y = random.randint(0, height)
                actions.move_by_offset(x, y)
                SmartWait.human_like_delay(100, 500)

            actions.perform()
        except:
            pass


class ElementInteractor:
    """元素交互工具"""

    @staticmethod
    def safe_click(driver, element, retry: int = 3):
        """安全点击（处理遮挡、JS点击等）"""
        for i in range(retry):
            try:
                # 先尝试滚动到元素
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});",
                    element
                )
                SmartWait.human_like_delay(300, 800)

                # 尝试普通点击
                element.click()
                return True
            except:
                if i == retry - 1:
                    # 最后尝试 JS 点击
                    try:
                        driver.execute_script("arguments[0].click();", element)
                        return True
                    except:
                        return False
                SmartWait.human_like_delay(500, 1000)

        return False

    @staticmethod
    def safe_send_keys(element, text: str, human_like: bool = True):
        """安全输入（模拟人类打字）"""
        try:
            element.clear()
            SmartWait.human_like_delay(200, 500)

            if human_like:
                # 逐字输入，随机延迟
                for char in text:
                    element.send_keys(char)
                    time.sleep(random.uniform(0.05, 0.15))
            else:
                element.send_keys(text)

            return True
        except:
            return False

    @staticmethod
    def get_element_safely(driver, by: By, value: str, timeout: int = 5):
        """安全获取元素"""
        try:
            return SmartWait.wait_for_element(driver, by, value, timeout)
        except:
            return None

    @staticmethod
    def retry_operation(func, retry: int = 3, delay: int = 1):
        """重试操作"""
        for i in range(retry):
            try:
                result = func()
                return result
            except Exception as e:
                if i == retry - 1:
                    raise e
                time.sleep(delay)


def create_stealth_driver(
    chrome_binary: str = None,
    profile_dir: str = None,
    headless: bool = False,
    use_undetected: bool = True
):
    """创建反检测浏览器驱动（便捷函数）"""
    stealth = StealthDriver(
        chrome_binary=chrome_binary,
        profile_dir=profile_dir,
        headless=headless,
        use_undetected=use_undetected
    )
    return stealth.create_driver()


# 使用示例
if __name__ == '__main__':
    # 创建反检测驱动
    driver = create_stealth_driver(
        headless=False,
        use_undetected=True
    )

    try:
        # 访问测试页面
        driver.get('https://www.douyin.com')

        # 使用智能等待
        element = SmartWait.wait_for_element(
            driver,
            By.CSS_SELECTOR,
            'input[placeholder*="搜索"]',
            timeout=10
        )

        if element:
            # 人类式输入
            ElementInteractor.safe_send_keys(element, '测试', human_like=True)
            print("✅ 反检测驱动工作正常")
        else:
            print("❌ 元素未找到")

        time.sleep(5)
    finally:
        driver.quit()
