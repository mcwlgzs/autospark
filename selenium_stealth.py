"""Selenium 反检测和优化模块

使用 undetected-chromedriver 和多种反检测技术提升成功率
v2.2: 新增 HumanBehaviorSimulator - 增强行为模拟
"""

import asyncio
import logging
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
    logging.getLogger(__name__).warning("undetected-chromedriver 未安装，使用标准 Selenium")


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


class HumanBehaviorSimulator:
    """高级人类行为模拟器（v2.2新增）"""

    @staticmethod
    def bezier_curve_mouse_movement(driver, element):
        """
        使用贝塞尔曲线模拟真实鼠标轨迹

        Args:
            driver: WebDriver实例
            element: 目标元素
        """
        try:
            from selenium.webdriver.common.action_chains import ActionChains

            actions = ActionChains(driver)

            # 获取元素位置
            location = element.location
            size = element.size

            # 目标位置（元素中心）
            end_x = location['x'] + size['width'] // 2
            end_y = location['y'] + size['height'] // 2

            # 起点（当前鼠标位置，假设为0,0）
            start_x, start_y = 0, 0

            # 随机控制点（生成曲线）
            ctrl_x = random.randint(
                min(start_x, end_x),
                max(start_x, end_x)
            )
            ctrl_y = random.randint(
                min(start_y, end_y),
                max(start_y, end_y)
            )

            # 沿贝塞尔曲线移动（分20步）
            steps = 20
            for i in range(steps + 1):
                t = i / steps

                # 二次贝塞尔曲线公式
                # B(t) = (1-t)²P0 + 2(1-t)tP1 + t²P2
                x = (1-t)**2 * start_x + 2*(1-t)*t * ctrl_x + t**2 * end_x
                y = (1-t)**2 * start_y + 2*(1-t)*t * ctrl_y + t**2 * end_y

                # 移动鼠标
                if i > 0:  # 跳过第一个点（起点）
                    prev_t = (i-1) / steps
                    prev_x = (1-prev_t)**2 * start_x + 2*(1-prev_t)*prev_t * ctrl_x + prev_t**2 * end_x
                    prev_y = (1-prev_t)**2 * start_y + 2*(1-prev_t)*prev_t * ctrl_y + prev_t**2 * end_y

                    dx = int(x - prev_x)
                    dy = int(y - prev_y)

                    actions.move_by_offset(dx, dy)

                # 随机微小停顿
                time.sleep(random.uniform(0.001, 0.005))

            actions.perform()

        except Exception as e:
            # 如果曲线移动失败，回退到直接点击
            element.click()

    @staticmethod
    def simulate_reading_time(content_length: int) -> float:
        """
        根据内容长度模拟阅读时间

        Args:
            content_length: 内容字符数

        Returns:
            阅读时长（秒）
        """
        # 平均阅读速度：中文300字/分钟，英文250词/分钟
        # 这里按300字/分钟计算
        words_per_second = 300 / 60
        base_time = content_length / words_per_second

        # 添加20%的随机波动
        return base_time * random.uniform(0.8, 1.2)

    @staticmethod
    async def random_scroll_behavior(driver):
        """
        模拟随机滚动行为

        Args:
            driver: WebDriver实例
        """
        scroll_types = ['smooth_scroll', 'jump_scroll', 'read_scroll']
        behavior = random.choice(scroll_types)

        if behavior == 'smooth_scroll':
            # 平滑滚动（小步多次）
            for _ in range(random.randint(2, 5)):
                driver.execute_script("window.scrollBy(0, 100);")
                await asyncio.sleep(random.uniform(0.1, 0.3))

        elif behavior == 'jump_scroll':
            # 跳跃滚动（直接跳到某位置）
            position = random.uniform(0.3, 0.7)
            driver.execute_script(f"window.scrollTo(0, document.body.scrollHeight * {position});")
            await asyncio.sleep(random.uniform(0.5, 1.0))

        else:  # read_scroll
            # 阅读式滚动（模拟边看边滚）
            for _ in range(random.randint(3, 7)):
                driver.execute_script("window.scrollBy(0, 200);")
                await asyncio.sleep(random.uniform(0.3, 0.8))

    @staticmethod
    def random_page_interactions(driver, count: int = 3):
        """
        在页面上执行随机交互（提高真实性）

        Args:
            driver: WebDriver实例
            count: 交互次数
        """
        interactions = [
            # 随机移动鼠标
            lambda: driver.execute_script("""
                var event = new MouseEvent('mousemove', {
                    'view': window,
                    'bubbles': true,
                    'cancelable': true,
                    'clientX': Math.random() * window.innerWidth,
                    'clientY': Math.random() * window.innerHeight
                });
                document.dispatchEvent(event);
            """),

            # 模拟鼠标悬停
            lambda: driver.execute_script("""
                var elements = document.querySelectorAll('a, button, div');
                if (elements.length > 0) {
                    var randomEl = elements[Math.floor(Math.random() * elements.length)];
                    randomEl.dispatchEvent(new Event('mouseenter', {bubbles: true}));
                }
            """),

            # 随机聚焦元素
            lambda: driver.execute_script("""
                var focusable = document.querySelectorAll('a, button, input, textarea');
                if (focusable.length > 0) {
                    var randomEl = focusable[Math.floor(Math.random() * focusable.length)];
                    randomEl.focus();
                    setTimeout(() => randomEl.blur(), 100);
                }
            """)
        ]

        for _ in range(min(count, len(interactions))):
            try:
                random.choice(interactions)()
                time.sleep(random.uniform(0.2, 0.5))
            except:
                pass

    @staticmethod
    def simulate_human_delay(min_seconds: float = 0.5, max_seconds: float = 2.0):
        """
        模拟人类操作延迟

        Args:
            min_seconds: 最小延迟
            max_seconds: 最大延迟
        """
        delay = random.uniform(min_seconds, max_seconds)
        time.sleep(delay)

    @staticmethod
    def add_random_typos(text: str, typo_rate: float = 0.05) -> str:
        """
        在文本中添加随机打字错误（然后修正）

        Args:
            text: 原始文本
            typo_rate: 错误率（0-1）

        Returns:
            包含错误和修正的文本序列
        """
        if random.random() > typo_rate or len(text) < 3:
            return text

        # 随机选择一个位置插入错误字符
        pos = random.randint(1, len(text) - 1)
        wrong_char = random.choice('qwertyuiopasdfghjklzxcvbnm')

        # 返回：正确部分 + 错误字符 + 退格 + 继续
        return text[:pos] + wrong_char + '\b' + text[pos:]


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
