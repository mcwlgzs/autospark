import re, os, gzip, shutil, random, functools, hashlib
import urllib.parse
from selenium import webdriver
from selenium.webdriver import Keys
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import NoSuchElementException
from selenium.common.exceptions import SessionNotCreatedException
from selenium.webdriver.common.by import By
import requests
import time, uvicorn
from datetime import datetime, timedelta
import json, base64, queue, uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, Request, Body
from fastapi.middleware.cors import CORSMiddleware
import threading
import subprocess

from state_store import StateStore
from notifier import (
    mask_email,
    mask_push_url,
    private_host_reason,
    push,
    send_email,
    validate_email_settings,
    validate_push_url,
)
# 纯逻辑（时间 / 文案池 / 补跑判定 / 去重 / 错误分级 / 密码 / token）都在 spark_core：
# 那边不依赖 selenium 和 fastapi，可以脱离浏览器和环境直接跑单元测试。
# 定时调度也从 schedule 库换成了自带 ticker —— schedule 既不持久化也没有异常隔离，
# 一次异常就能把调度线程打死，而这里「每天都得发出去」是硬需求。
from spark_core import (
    TokenBook,
    classify_error,
    format_time,
    guard_decision,
    hash_password,
    history_clean,
    history_key,
    image_kind,
    image_payload_error,
    image_record_text,
    make_history_record,
    parse_message_pool,
    password_policy_error,
    planned_run_at,
    render_message,
    render_sign_text,
    retry_due,
    retryable,
    run_due,
    send_status_from_reason,
    should_stop_round,
    sign_summary,
    today_str,
    verify_password,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHROME_PROFILE_DIR = os.getenv('CHROME_PROFILE_DIR', os.path.join(BASE_DIR, 'chrome-profile'))
DEFAULT_CHROME_BINARY = (
    r'C:\Program Files\Google\Chrome\Application\chrome.exe'
    if os.name == 'nt'
    else '/opt/google/chrome/chrome'
)

CHROMEDRIVER_PATH = os.getenv('CHROMEDRIVER_PATH', shutil.which('chromedriver') or '')
service = Service(executable_path=CHROMEDRIVER_PATH) if os.path.exists(CHROMEDRIVER_PATH) else None
CHROME_BINARY = os.getenv('CHROME_BINARY', DEFAULT_CHROME_BINARY)
VERSION = '1.2.0'
# 运行期数据目录。默认放在项目里；容器部署或跑测试时可以指到别处
# （测试指向临时目录，就不会动到真实面板的 state.json）。
DATA_DIR = os.getenv('SPARK_DATA_DIR', os.path.join(BASE_DIR, 'data'))
LOG_DIR = os.getenv('SPARK_LOG_DIR', os.path.join(BASE_DIR, 'logs'))


# ==================== 运行参数（全部可以用环境变量覆盖） ====================
def _env_int(name, default):
    try:
        return int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        return default


def _env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


# 可见浏览器是这个分支的核心：扫码登录和短信/刷脸二次验证都要人工在同一台机器上完成。
# 只有在「登录态已就绪、完全不需要人工验证」的场景才建议关掉（SPARK_SHOW_BROWSER=0）。
off_ui = not _env_bool('SPARK_SHOW_BROWSER', True)
# 定时任务不再卡死在固定那一分钟：在基准时间之后随机推迟 0~N 分钟执行（降低机器特征）。
JITTER_MINUTES = _env_int('SPARK_JITTER_MINUTES', 40)
# 机器重启/崩溃导致错过当天时间点后，多久之内还补跑；0 = 不补跑。
CATCHUP_GRACE_MINUTES = _env_int('SPARK_CATCHUP_GRACE_MINUTES', 360)
# 当日失败后隔多久只对失败的好友补发一次；0 = 关闭补发。
RETRY_AFTER_MINUTES = _env_int('SPARK_RETRY_AFTER_MINUTES', 45)
# 面板登录状态空闲多久失效（小时）；0 = 永不过期。
TOKEN_TTL_HOURS = _env_int('SPARK_TOKEN_TTL_HOURS', 72)
# 允许跨域访问面板的来源（逗号分隔）。前端开发态走 Vite 代理属于同源，其实不需要 CORS；
# 这里默认只放行本机开发端口，不再用 "*" 通配（配合 credentials 时那等于对全网开放）。
CORS_ORIGINS = [item.strip() for item in os.getenv(
    'SPARK_CORS_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173').split(',') if item.strip()]
# 只有来自这些反代地址的请求才信任 X-Real-IP / X-Forwarded-For：
# 直连端口时伪造这两个头就能无限重置登录失败计数，节流等于没有。
TRUSTED_PROXIES = frozenset(item.strip() for item in os.getenv(
    'SPARK_TRUSTED_PROXIES', '127.0.0.1,::1').split(',') if item.strip())
# 启动时若已存在定时任务，就自动把浏览器拉起来。
# 关掉的话，重启后必须有人打开面板点一次「初始化浏览器」，否则任务到点发不出去。
AUTO_INIT_BROWSER = _env_bool('SPARK_AUTO_INIT_BROWSER', True)
# 留空文案默认从本地文案池里抽，不再请求第三方名言接口。
REMOTE_QUOTE = _env_bool('SPARK_REMOTE_QUOTE', False)
# 推送地址和 SMTP 主机默认拒绝本机 / 内网。本机邮局需要时再打开。
ALLOW_PRIVATE_PUSH = _env_bool('SPARK_ALLOW_PRIVATE_PUSH', False)
# 只有显式设置时才覆盖 Chrome 自己的 UA。写死旧版本会和真实浏览器不一致。
CHROME_USER_AGENT = os.getenv('SPARK_USER_AGENT', '').strip()
# 后端监听地址。默认只绑 127.0.0.1：对外的访问一律交给 nginx 反代 + HTTPS，
# 后端裸奔在 0.0.0.0 上意味着面板密码和 token 在内网里明文乱跑。
# 安全中心会把这个值读出来如实展示，所以它必须是唯一的真源（不能再写死在 uvicorn.run 里）。
BIND_HOST = os.getenv('SPARK_HOST', '127.0.0.1').strip() or '127.0.0.1'

# ==================== 图片发送 / 文昌帝君灵签 ====================
# 图片是从第三方接口给的直链下载下来的。下载目录放在 data/ 下而不是系统临时目录：
# 一是临时目录会被系统清理，二是这里的东西可以按天数和数量上限自动回收。
IMAGE_DIR = os.path.join(DATA_DIR, 'images')
IMAGE_MAX_BYTES = _env_int('SPARK_IMAGE_MAX_MB', 5) * 1024 * 1024
# 下载签图超时。给得比文案接口长一点，图片体积大。
IMAGE_FETCH_TIMEOUT = _env_int('SPARK_IMAGE_TIMEOUT', 10)
# 图片上传到聊天框后，等预览图出现的最长时间。网络慢时这一步最耗时。
IMAGE_UPLOAD_TIMEOUT = _env_int('SPARK_IMAGE_UPLOAD_TIMEOUT', 20)
# 图片消息的送达确认窗口。比纯文字长：图片要经过上传 + 服务端转存才出现在消息列表里。
IMAGE_CONFIRM_TIMEOUT = float(os.getenv('SPARK_IMAGE_SEND_TIMEOUT', '25'))
# 签文接口。换供应商时改环境变量即可，不用动代码。
WENCHANG_URL = os.getenv('SPARK_WENCHANG_URL', 'https://v2.xxapi.cn/api/wenchangdijunrandom')
# 下载目录的自动回收：超过天数或超过文件数上限就先删最旧的。上限存在的意义是
# 防止接口哪天返回随机图链导致目录无限膨胀。
IMAGE_KEEP_DAYS = _env_int('SPARK_IMAGE_KEEP_DAYS', 30)
IMAGE_MAX_FILES = _env_int('SPARK_IMAGE_MAX_FILES', 300)


def ensure_display_env():
    if os.name == 'nt':
        return None
    if os.getenv('DISPLAY'):
        return os.getenv('DISPLAY')
    for candidate in (':91', ':99', ':1'):
        if os.path.exists(f'/tmp/.X11-unix/X{candidate[1:]}'):
            os.environ['DISPLAY'] = candidate
            return candidate
    return None


def cleanup_stale_browser_processes():
    if os.name == 'nt':
        return
    pkill_path = shutil.which('pkill')
    if pkill_path:
        for pattern in (CHROMEDRIVER_PATH, CHROME_PROFILE_DIR):
            if pattern:
                subprocess.run([pkill_path, '-f', pattern], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    for entry in ('SingletonCookie', 'SingletonLock', 'SingletonSocket', 'DevToolsActivePort'):
        target = os.path.join(CHROME_PROFILE_DIR, entry)
        try:
            if os.path.lexists(target):
                os.unlink(target)
        except Exception:
            pass


def build_chrome_options():
    options = webdriver.ChromeOptions()
    if os.path.exists(CHROME_BINARY):
        options.binary_location = CHROME_BINARY
    options.add_experimental_option('excludeSwitches', ['enable-logging'])
    options.add_argument('log-level=3')
    if CHROME_USER_AGENT:
        options.add_argument('user-agent=%s' % CHROME_USER_AGENT)
    if off_ui:
        # 无头模式只适合「登录态已就绪」的场景：扫码 / 短信 / 刷脸验证都无法人工完成
        options.add_argument('--headless=new')
    options.add_experimental_option('excludeSwitches', ['enable-automation', 'useAutomationExtension'])
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-infobars')
    options.add_argument('--disable-notifications')
    options.add_argument('--disable-popup-blocking')
    # 原先的 --disable-web-security 已去掉：面板操作的始终是同源页面，用不上它，
    # 留着只会白白削弱浏览器的同源策略。
    options.add_argument('--ignore-certificate-errors')
    options.add_argument('--no-sandbox')
    options.add_argument('--window-size=1400,3200')
    options.add_argument(f'--user-data-dir={CHROME_PROFILE_DIR}')
    options.add_argument('--remote-debugging-port=0')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--start-maximized')
    options.add_argument("--force-device-scale-factor=0.25")
    return options


# ---------------------------------------------------------------- 反检测
# Selenium / WebDriver 会在浏览器里留下两处「不需要任何高级手段就能读到」的
# 自动化痕迹：
#   1. navigator.webdriver === true（真实浏览器里这个属性存在且是 false）；
#   2. chromedriver 往 window 上挂的 cdc_ / $cdc_ 开头的变量。
# 抖音的风控脚本第一件事就是读它。这里把这两处抹掉。
#
# 刻意「只抹痕、不伪造」：
#   * 不改 User-Agent、不动 navigator.plugins / languages、不改分辨率。
#     这个程序跑的是本机真实 Chrome + 持久化 profile，这些值本来就是真的；
#     手工换成假的只会造出「UA 说 Windows、navigator.platform 说别的」这种
#     更扎眼的自相矛盾 —— 业界的经验是「指纹一致」比「指纹伪造」重要得多。
#   * selenium_stealth.py 里那份 _inject_stealth_js() 顺手还会随机 UA、
#     把 navigator.plugins 换成一串假数字，所以没有直接复用它。
STEALTH_JS = """
(() => {
  try {
    Object.defineProperty(navigator, 'webdriver', { get: () => false, configurable: true });
  } catch (e) {}
  try {
    for (const key of Object.keys(window)) {
      if (key.indexOf('cdc_') === 0 || key.indexOf('$cdc_') === 0) {
        try { delete window[key]; } catch (e) {}
      }
    }
  } catch (e) {}
})();
"""

# 反检测脚本是否真的注入成功过。安全中心如实报告这一项：
# 「以为开了反检测其实没开」比「明确知道没开」危险得多。
_stealth_injected = False

# 本轮最后一次成功启动的浏览器版本（由 driver.capabilities 读出）。
# 单独记着是因为安全中心要显示「自动化引擎版本」，而浏览器没跑的时候
# capabilities 是拿不到的 —— 那时显示「本轮还没启动过」比显示一个假版本强。
_browser_version = ''


def _inject_stealth_js(target):
    """把反检测脚本挂到每一个新文档上，保证在页面自己的 JS 之前生效。"""
    global _stealth_injected
    try:
        target.execute_cdp_cmd(
            'Page.addScriptToEvaluateOnNewDocument', {'source': STEALTH_JS}
        )
        _stealth_injected = True
        return True
    except Exception as exc:
        _stealth_injected = False
        # 注入失败不影响登录和发送，所以不能因此中断初始化；
        # 但自动化痕迹会暴露出去，必须留下记录（安全中心会据此报警）。
        log_event('warn', '浏览器', '反检测脚本注入失败（登录和发送不受影响，但自动化痕迹会暴露）', exc)
        return False


def _body_text():
    try:
        return driver.find_element(By.TAG_NAME, 'body').text or ''
    except Exception:
        return ''


def _is_two_factor_page():
    text = _body_text()
    keywords = ['二次', '身份', '认证', '验证', '验证码', '扫脸', '手机号', '获取验证码', '安全']
    return any(keyword in text for keyword in keywords) and '扫码登录' not in text


def _find_first(by, selectors):
    last_error = None
    for selector in selectors:
        try:
            elements = driver.find_elements(by, selector)
            for element in elements:
                if element.is_displayed() and element.is_enabled():
                    return element
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise NoSuchElementException(str(selectors))


def _click_first_xpath(xpaths):
    element = _find_first(By.XPATH, xpaths)
    try:
        element.click()
    except Exception:
        driver.execute_script('arguments[0].click()', element)
    return element


# ---------- 登录页元素定位（中英文双语兼容） ----------
# 香港机房 IP 会被抖音判到国际版英文登录页，旧代码只写了中文/旧 DOM 的选择器，
# 导致国家码、手机号、发送按钮全部匹配不上。以下选择器同时覆盖两种页面。

LOGIN_AREA_CODE_XPATHS = [
    '//input[@name="web-login-area-code-input"]',
    '//input[@aria-label="国家/地区"]',
    '//input[@role="combobox"]',
    '//*[@id="douyin_login_comp_normal_input_id"]/div[1]/div/input',
    '//input[contains(@placeholder, "区号")]',
    '//input[contains(@placeholder, "国家")]',
]

LOGIN_PHONE_XPATHS = [
    '//*[@id="normal-input"]',
    '//input[@placeholder="Phone number"]',
    '//input[contains(@placeholder, "手机号")]',
    '//input[contains(@placeholder, "手机")]',
    '//input[@inputmode="tel" and @type="tel"]',
]

LOGIN_SEND_CODE_XPATHS = [
    '//*[@id="douyin_login_comp_button_input_id"]',
    '//*[normalize-space()="Send code"]',
    '//*[contains(normalize-space(), "获取验证码")]',
    '//*[contains(normalize-space(), "发送验证码")]',
]

LOGIN_CODE_XPATHS = [
    '//*[@id="button-input"]',
    '//input[@placeholder="Enter code"]',
    '//input[contains(@placeholder, "验证码")]',
    '//input[contains(@placeholder, "短信")]',
]

LOGIN_SUBMIT_XPATHS = [
    '//*[@id="douyin_login_comp_btn_id"]',
    '//*[normalize-space()="Log in"]',
    '//*[normalize-space()="登录"]',
    '//*[normalize-space()="下一步"]',
]

VERIFY_PANEL_XPATHS = [
    '//*[contains(@class, "second_verify_panel_new")]',
    '//*[contains(@class, "uc_verification_component_layout")]',
]

PHONE_LOGIN_TAB_XPATHS = [
    '//*[normalize-space()="Use Phone"]',
    '//*[normalize-space()="手机号登录"]',
    '//*[normalize-space()="验证码登录"]',
]


def _exists(xpaths, by=By.XPATH):
    for xpath in xpaths:
        try:
            for element in driver.find_elements(by, xpath):
                if element.is_displayed():
                    return True
        except Exception:
            continue
    return False


def _try_click(xpaths, by=By.XPATH):
    """尽量点击第一个命中元素，失败返回 False（不抛异常）。"""
    try:
        element = _find_first(by, xpaths)
    except Exception:
        return False
    try:
        element.click()
    except Exception:
        try:
            driver.execute_script('arguments[0].click()', element)
        except Exception:
            return False
    return True


def _set_value(element, value):
    """兼容 React 受控输入的赋值：先 send_keys，校验不通过再用原生 setter 注入。"""
    if element is None:
        return False
    target = (value or '').strip()
    try:
        element.click()
    except Exception:
        pass
    try:
        element.clear()
    except Exception:
        pass
    try:
        element.send_keys(value)
    except Exception:
        pass
    if (element.get_attribute('value') or '').strip() == target:
        return True
    try:
        driver.execute_script(
            "var el=arguments[0],v=arguments[1];"
            "var setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;"
            "setter.call(el,v);"
            "el.dispatchEvent(new Event('input',{bubbles:true}));"
            "el.dispatchEvent(new Event('change',{bubbles:true}));",
            element, value)
    except Exception:
        pass
    return (element.get_attribute('value') or '').strip() == target


def _set_area_code(areacode):
    """在国际版/中文版登录页选择国家码（如 +86）。返回 (是否成功, 当前值)。"""
    target_code = (areacode or '').replace(' ', '')
    try:
        field = _find_first(By.XPATH, LOGIN_AREA_CODE_XPATHS)
    except Exception:
        return False, '未找到国家码输入框'
    current = (field.get_attribute('value') or '').replace(' ', '')
    if current == target_code:
        return True, current
    try:
        field.click()
    except Exception:
        try:
            driver.execute_script('arguments[0].click()', field)
        except Exception:
            pass
    deadline = time.time() + 8
    options = []
    while time.time() < deadline:
        options = driver.find_elements(By.CSS_SELECTOR, '#select-ul li')
        if options:
            break
        time.sleep(0.3)
    if not options:
        return False, current
    picked = None
    for option in options:
        if target_code and target_code in (option.text or '').replace(' ', ''):
            picked = option
            break
    if picked is None:
        return False, current
    try:
        driver.execute_script('arguments[0].scrollIntoView({block:"center"});', picked)
        driver.execute_script('arguments[0].click()', picked)
    except Exception:
        try:
            picked.click()
        except Exception:
            pass
    time.sleep(1)
    return (field.get_attribute('value') or '').replace(' ', '') == target_code, \
        (field.get_attribute('value') or '').replace(' ', '')


def _is_verify_panel():
    """抖音「身份验证」二次验证面板是否出现。"""
    return _exists(VERIFY_PANEL_XPATHS)


def _verify_panel_text():
    try:
        return driver.execute_script(
            "var el=document.querySelector('[class*=second_verify_panel_new]');"
            "return el?(el.innerText||''):'';") or ''
    except Exception:
        return ''


def _verify_sms_sent():
    """面板上是否已显示验证码已下发。

    已下发时应直接输入验证码，重复点「重新发送」既无必要，也会多烧短信并抬高风控概率。
    """
    text = _verify_panel_text() or ''
    return ('已发送至' in text) or ('短信已发送' in text) or ('已发送' in text)


def _masked_phone():
    """从页面文案里抽取掩码手机号，例如 192****0867。"""
    try:
        text = driver.execute_script('return document.body.innerText||\'\';') or ''
    except Exception:
        return None
    match = re.search(r'(?:\+\d{1,4}[\s-]?)?\d{2,4}[\s*＊]{2,}\d{2,4}', text)
    return match.group(0).strip() if match else None


def _login_error_text():
    """抓取登录区域内可见的报错文案（用于判断发送是否真的失败）。"""
    try:
        text = driver.execute_script(
            "var box=document.getElementById('douyin_login_comp_flat_panel')||"
            "document.querySelector('[class*=second_verify_panel_new]')||document.body;"
            "return box.innerText||'';") or ''
    except Exception:
        return ''
    keywords = ['失败', '错误', '频繁', '异常', '受限', '限制', '过于', '稍后', '不正确', '无效',
                'invalid', 'failed', 'too many', 'error', 'try again', 'risk']
    for line in text.splitlines():
        line = line.strip()
        if line and any(word.lower() in line.lower() for word in keywords):
            return line
    return ''


def _wait_send_result(timeout=15):
    """轮询判断短信到底有没有真的下发，返回 (状态, 说明)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.5)
        if _is_verify_panel():
            return 'challenge', '抖音要求先完成身份验证（二次验证）'
        button_text = ''
        try:
            button = _find_first(By.XPATH, LOGIN_SEND_CODE_XPATHS)
            button_text = ((button.text or '') + (button.get_attribute('textContent') or '')).strip()
        except Exception:
            pass
        if re.search(r'\d+\s*[sS秒]', button_text) or 'Resend' in button_text or '重新' in button_text:
            return 'sent', '验证码已下发'
        error = _login_error_text()
        if error:
            return 'error', error
    return 'unknown', '未检测到发送结果，抖音未返回明确状态'


VERIFY_OPTION_MAP = {
    'sms': '接收短信验证码',
    'face': '手机刷脸验证',
    'password': '验证登录密码',
    'sms_send': '发送短信验证',
}

# 抖音「身份验证」面板的按钮文案会随版本/A-B 实验变化，
# 这里按同义词逐个匹配（参考 prometheus-relay 的 _find_sms_method）。
VERIFY_OPTION_SYNONYMS = {
    '接收短信验证码': ('接收短信验证码', '短信验证码', '短信验证', '手机接收验证码',
                 '短信接收验证码', '手机验证码', '手机号验证', '手机验证',
                 '接收验证码', '收验证码', '使用短信验证', '通过短信验证'),
    '发送短信验证': ('发送短信验证', '发送短信验证码', '发送验证码', '获取验证码', '重新发送'),
    '验证登录密码': ('验证登录密码', '登录密码验证', '密码验证', '验证密码'),
    '手机刷脸验证': ('手机刷脸验证', '刷脸验证', '人脸验证', '扫脸验证', '人脸识别'),
}


def _verify_option_element(text):
    """定位「身份验证」面板里的某个验证方式条目（支持同义文案）。"""
    for label in VERIFY_OPTION_SYNONYMS.get(text, (text,)):
        try:
            return _find_first(By.XPATH, [
                '//*[contains(@class,"second_verify_panel_new") or contains(@class,"uc_verification_component_layout")]'
                '//*[normalize-space()="%s"]/ancestor-or-self::*[contains(@class,"list_item")][1]' % label,
                '//*[contains(@class,"list_item")]//*[normalize-space()="%s"]'
                '/ancestor-or-self::*[contains(@class,"list_item")][1]' % label,
                '//*[contains(@class,"second_verify_panel_new") or contains(@class,"uc_verification_component_layout")]'
                '//*[normalize-space()="%s"]' % label,
                '//*[normalize-space()="%s"]' % label,
            ])
        except Exception:
            continue
    return None


def _click_verify_option(text):
    element = _verify_option_element(text)
    if element is None:
        return False
    try:
        driver.execute_script('arguments[0].click()', element)
    except Exception:
        try:
            element.click()
        except Exception:
            return False
    return True


# 抖音的身份验证面板分两种形态（现场取证 2026-09-14）：
#   1) 初态：4 种验证方式以 list_item 形式平铺在面板上；
#   2) 已激活某一方式的态（例如短信已下发）：面板只保留当前方式，
#      其余方式（验证登录密码 / 手机刷脸验证）必须点「无法验证通过？选择其他验证方式」
#      跳到 /verify-list 列表页才会出现。
# 旧实现只走形态 1，所以短信态下点「验证登录密码」永远返回「未找到入口」。
VERIFY_METHODS_ENTRY_TEXTS = ('选择其他验证方式', '其他验证方式', '无法验证通过')

VERIFY_METHODS_ENTRY_XPATHS = [
    '//*[normalize-space()="%s"]' % text for text in VERIFY_METHODS_ENTRY_TEXTS
] + [
    '//*[contains(text(),"%s")]' % text for text in VERIFY_METHODS_ENTRY_TEXTS
]

# 只有方式列表页才会出现的条目：用它确认「回退」是否真的成功。
# 注意：不能拿单个方式名去页面上做文本匹配 —— 实测密码输入页的面板标题字面就是
# 「验证登录密码」，会导致「已经在列表页」的误判（点了「换一种方式」却什么都没发生）。
VERIFY_LIST_MIN_NAMES = 2

# 兜底回退控件。SDK 的 onCancel 在子步骤上是 replace 到 /verify-list（不是关闭验证），
# 所以校验失败页上的「取消」等价于「回退到方式列表」。为避免误关整个验证面板，
# 「取消」只在面板作用域内匹配，全局兜底不含它。
VERIFY_BACK_CONTROL_XPATHS = [
    '//*[contains(@class,"second_verify_panel_new") or contains(@class,"uc_verification_component")]'
    '//*[normalize-space()="%s"]' % text
    for text in ('返回', '上一步', '重新选择验证方式', '取消')
] + [
    '//*[normalize-space()="%s"]' % text
    for text in ('返回', '上一步', '重新选择验证方式')
]


def _wait_for_any(xpaths, timeout=4):
    """轮询等待任一选择器出现并可见。"""
    deadline = time.time() + timeout
    while True:
        if _first_displayed(xpaths) is not None:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.5)


def _wait_until(predicate, timeout=4):
    """轮询等待条件成立（谓词抛异常按不成立处理）。"""
    deadline = time.time() + timeout
    while True:
        try:
            if predicate():
                return True
        except Exception:
            pass
        if time.time() >= deadline:
            return False
        time.sleep(0.5)


def _click_any(xpaths):
    """React onClick 优先、DOM 点击兜底地点击第一个命中元素。"""
    element = _first_displayed(xpaths)
    if element is None:
        return False
    if _invoke_react_click(element):
        return True
    return _try_click(xpaths)


def _visible_list_items():
    """当前可见的方式条目数量（方式列表页会有多个 list_item）。"""
    count = 0
    try:
        elements = driver.find_elements(By.XPATH, '//*[contains(@class,"list_item")]')
    except Exception:
        return 0
    for element in elements:
        try:
            if element.is_displayed():
                count += 1
        except Exception:
            continue
    return count


def _verify_method_list_visible():
    """是否真的停在「选验证方式」列表页（而不是某个方式的输入页）。

    判据必须避开「页面上有某个方式名」这种弱信号：密码输入页的面板标题就是
    「验证登录密码」（实测），只看文案会把输入页当成列表页，于是「换一种方式」
    会立刻返回成功但一下都不点。
    """
    # 只要还停在某个方式的输入控件上，就不是列表页
    if _first_displayed(VERIFY_PASSWORD_INPUT_XPATHS) is not None:
        return False
    if _first_displayed(VERIFY_CODE_INPUT_PANEL_XPATHS) is not None:
        return False
    if _visible_list_items() > 0:
        return True
    # 退化判据：面板内同时出现多个方式名，才说明这是「选方式」而不是「某个方式」
    text = _verify_panel_text() or ''
    found = sum(1 for label in VERIFY_METHOD_LABELS if label in text)
    return found >= VERIFY_LIST_MIN_NAMES


def _in_verify_flow():
    """当前页面是否在二次身份验证流程里（面板 / 方式列表页 / 方式输入页任一）。

    不能用 _is_verify_panel() 单独判断：验证方式列表页（/verify-list）与部分子步骤
    不含 second_verify_panel_new / uc_verification_component_layout 这两个 class，
    一旦漏判，前端的验证方式选择区会整块消失，用户就「无法回退到其他验证」。

    也不能靠「页面上有没有 password 输入框」兜底：面板自己的「修改密码」弹窗就有，
    会造成「明明没有二次验证却显示验证中」。
    """
    return _is_verify_panel() or _exists(VERIFY_METHODS_ENTRY_XPATHS) \
        or _verify_method_list_visible()


def _open_verify_methods():
    """回退到验证方式列表页（/verify-list），以便重新选择验证方式。

    两条路径：面板上的「无法验证通过？选择其他验证方式」，以及 SDK 的返回/取消控件。
    回退后必须确认方式列表真的出现了才算成功 —— 否则谎报成功会让前端以为可以选，
    实际点了还是没用（「换一种方式」点了没反应就是这么来的）。
    """
    if _verify_method_list_visible():
        return True
    if _click_any(VERIFY_METHODS_ENTRY_XPATHS) and _wait_until(_verify_method_list_visible, 4):
        return True
    if _click_any(VERIFY_BACK_CONTROL_XPATHS) and _wait_until(_verify_method_list_visible, 4):
        return True
    return False


def _method_probe_xpaths(key):
    """该验证方式切过去之后，页面上应该出现的控件（用来确认切换真的成功了）。"""
    if key in ('sms', 'sms_send'):
        # 必须用「仅面板」这一组：完整列表里带登录页的 #button-input，
        # 只要登录框还显示着就会误判成「已经在短信步骤」。
        return VERIFY_CODE_INPUT_PANEL_XPATHS
    if key == 'password':
        return VERIFY_PASSWORD_INPUT_XPATHS
    if key == 'face':
        return VERIFY_QR_XPATHS
    return None


def _enter_verify_method(key):
    """切换到指定验证方式的输入态。返回 (是否成功, 失败原因)。

    依次尝试：面板上已有的方式条目 -> 「选择其他验证方式」列表页里的条目。
    """
    label = VERIFY_OPTION_MAP.get(key, key)
    probes = _method_probe_xpaths(key)
    if probes and _first_displayed(probes) is not None:
        return True, None
    if _click_verify_option(label) and (not probes or _wait_for_any(probes, 3)):
        return True, None
    if _open_verify_methods():
        if _click_verify_option(label) and (not probes or _wait_for_any(probes, 4)):
            return True, None
        return False, '已进入「其他验证方式」列表，但未找到「%s」入口' % label
    return False, '未找到「%s」入口' % label


def _verify_account():
    try:
        return driver.execute_script(
            "var el=document.querySelector('[class*=name-eQ7kOH]');"
            "return el?(el.innerText||'').trim():'';") or None
    except Exception:
        return None


def _verify_options():
    try:
        elements = driver.find_elements(By.CSS_SELECTOR, '[class*="list_item-ZI1VMT"]')
    except Exception:
        return []
    options = []
    for element in elements:
        try:
            text = (element.text or '').strip().replace('\n', ' / ')
        except Exception:
            continue
        if text and element.is_displayed():
            options.append(text)
    return options


# 手机刷脸验证的二维码：原来只给整页截图，在 520px 宽的弹窗里二维码被等比压到几十像素，
# 手机扫不出来。这里把二维码元素本身单独取出来，前端按大尺寸渲染。
# 只取验证面板作用域内的元素，避免误抓登录页的 #animate_qrcode_container。
VERIFY_QR_XPATHS = [
    '//*[contains(@class,"second_verify_panel_new")]//canvas',
    '//*[contains(@class,"uc_verification_component")]//canvas',
    '//*[contains(@class,"second_verify_panel_new")]//*[contains(@class,"qr")]//canvas',
    '//*[contains(@class,"second_verify_panel_new")]//*[contains(@class,"qr")]//img',
    '//*[contains(@class,"uc_verification_component")]//*[contains(@class,"qr")]//img',
    '//*[contains(@class,"second_verify_panel_new")]//img',
    '//*[contains(@class,"uc_verification_component")]//img',
]

VERIFY_QR_SRC_JS = """
var el = arguments[0];
if (!el) return null;
var tag = el.tagName.toLowerCase();
if (tag === 'canvas') {
  try { return el.toDataURL('image/png'); } catch (e) { return null; }
}
if (tag === 'img') {
  return el.currentSrc || el.src || null;
}
return null;
"""


def _verify_qr_element():
    """在身份验证面板里定位二维码元素（canvas 或足够大的 img）。"""
    for xpath in VERIFY_QR_XPATHS:
        try:
            elements = driver.find_elements(By.XPATH, xpath)
        except Exception:
            continue
        for element in elements:
            try:
                if not element.is_displayed():
                    continue
                size = element.size or {}
                if (size.get('width') or 0) < 60 or (size.get('height') or 0) < 60:
                    continue  # 过滤图标、头像等小图
                if element.tag_name.lower() == 'img':
                    src = element.get_attribute('src') or ''
                    if len(src) < 64:
                        continue  # 占位图
                return element
            except Exception:
                continue
    return None


def _fetch_binary(url, timeout=8):
    """服务端拉取图片二进制（requests 优先，标准库兜底）。"""
    try:
        referer = driver.current_url or 'https://www.douyin.com/'
    except Exception:
        referer = 'https://www.douyin.com/'
    headers = {'Referer': referer, 'User-Agent': 'Mozilla/5.0'}
    try:
        import requests
        resp = requests.get(url, timeout=timeout, headers=headers)
        if resp.status_code == 200 and resp.content:
            return resp.content
    except Exception:
        pass
    try:
        import urllib.request
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return None


def _verify_qr_image():
    """返回刷脸验证二维码（data URI），拿不到返回 None。"""
    element = _verify_qr_element()
    if element is None:
        return None
    raw = None
    try:
        raw = driver.execute_script(VERIFY_QR_SRC_JS, element)
    except Exception:
        raw = None
    if raw and raw.startswith('data:image') and len(raw) > 256:
        return raw
    if raw and raw.startswith('http'):
        content = _fetch_binary(raw)
        if content:
            return 'data:image/png;base64,' + base64.b64encode(content).decode()
    # 兜底：元素级截图。元素级截图只含二维码本身，不会像整页截图那样被压缩。
    try:
        shot = element.screenshot_as_base64
        if shot:
            return 'data:image/png;base64,' + shot
    except Exception:
        pass
    return None


def _current_verify_method():
    """推断当前处于哪种验证方式，供前端只显示该方式的操作区。

    返回 sms / password / face / None。顺序不能调：刷脸页不会有验证码输入框，
    但短信页可能同时残留其它元素，所以先判特征最强的二维码，再判密码框，最后是验证码框。
    """
    try:
        if _verify_qr_element() is not None:
            return 'face'
    except Exception:
        pass
    if _first_displayed(VERIFY_PASSWORD_INPUT_XPATHS) is not None:
        return 'password'
    if _first_displayed(VERIFY_CODE_INPUT_PANEL_XPATHS) is not None:
        return 'sms'
    return None


def _verify_snapshot(with_shot=False):
    """返回二次验证面板的完整状态，供前端展示与选择。"""
    # 用 _in_verify_flow 而不是 _is_verify_panel：只要还处在验证流程里（含方式列表页、
    # 各方式的填写页），前端就必须继续显示验证方式选择区，否则验证失败后无处可退。
    active = _in_verify_flow()
    # 刷脸二维码单独给出高清图（仅验证流程内才去查 DOM，避免无谓开销）
    qr = None
    method = None
    if active:
        try:
            qr = _verify_qr_image()
        except Exception:
            qr = None
        try:
            method = _current_verify_method()
        except Exception:
            method = None
    data = {
        'active': active,
        'method': method,
        'qr': qr,
        'account': _verify_account(),
        'masked_phone': _masked_phone(),
        'options': _verify_options() if active else [],
        'text': (_verify_panel_text() or _body_text()) if active else _body_text(),
        'logged_in': _detect_logged_in_state(),
    }
    if with_shot:
        try:
            data['screenshot'] = driver.get_screenshot_as_base64()
        except Exception:
            data['screenshot'] = None
    return data


def _trigger_verify_sms():
    """处理抖音「身份验证」面板：选择「接收短信验证码」并触发发送。"""
    # 验证码已下发时不要重复点重发（避免多烧短信、抬高风控）
    if _verify_sms_sent() and _first_displayed(VERIFY_CODE_INPUT_XPATHS) is not None:
        masked = _masked_phone()
        return {'code': 200,
                'data': '验证码已发送至账号绑定手机号 %s，请直接填验证码' % (masked or '（见页面提示）')}
    if not _click_verify_option(VERIFY_OPTION_MAP['sms']):
        return {'code': 400, 'data': '身份验证页未找到「接收短信验证码」入口'}
    time.sleep(2)
    # 部分流程选完方式后还需点一次「发送短信验证」
    if _click_verify_option(VERIFY_OPTION_MAP['sms_send']):
        time.sleep(2)
    masked = _masked_phone()
    panel_text = _verify_panel_text()
    if masked:
        return {'code': 200, 'data': '验证码已发送至账号绑定手机号 %s，请查收（短信发往账号绑定号码，与页面填写的号码无关）' % masked}
    if '已发送' in panel_text or '重发' in panel_text or '秒' in panel_text:
        return {'code': 200, 'data': '验证码已发送至账号绑定手机号，请查收'}
    error = _login_error_text()
    if error:
        return {'code': 400, 'data': error}
    return {'code': 400, 'data': '已进入身份验证页，但未确认短信是否下发，请在页面查看'}


# 文案取不到时的兜底：宁可发一句普通的话，也不能因为第三方接口挂了就不发（火花会断）
FALLBACK_MESSAGES = (
    '今天的火花也不能断呀 🔥',
    '来啦来啦，今天也来续个火花 🔥',
    '冒个泡，火花保住了 🔥',
)


def AiqingGongyu_text():
    """取一条「爱情公寓」语录，作为用户没填文案时的默认内容。

    旧实现既没有 timeout 也没有异常兜底：第三方站点一旦挂住，这个请求会永久阻塞。
    而它是在同步路由里被调用的，占着的就是 FastAPI 线程池的额度 —— 多来几次面板就整体卡死。
    """
    try:
        req = requests.get('https://v2.xxapi.cn/api/aiqinggongyu', timeout=5)
        if req.status_code == 200:
            content = (req.json() or {}).get('data')
            if content and str(content).strip():
                return str(content).strip()
    except Exception as exc:
        log_event('warn', '定时任务', '获取每日文案失败，改用本地兜底文案', exc)
    return random.choice(FALLBACK_MESSAGES)


# ==================== 一言（hitokoto）：任务的正文可以「发送时现取一句」 ====================
# 地址和超时都走环境变量：换镜像站不用改代码，测试也能把它指到本地桩上，不发真实请求。
# ?c=k 是按用户要求限定「哲学」分类（hitokoto 的 c 参数：k=哲学）。不带分类会随机到
# 动画 / 游戏 / 抖机灵那些不适合发给好友的内容；要换分类改这个环境变量即可。
HITOKOTO_URL = os.getenv('SPARK_HITOKOTO_URL', 'https://v1.hitokoto.cn/?c=k')
HITOKOTO_TIMEOUT = _env_int('SPARK_HITOKOTO_TIMEOUT', 8)


def _format_hitokoto(text, payload):
    """把一句一言拼成最终要发出去的那条消息：『正文』—— 「来源 作者」。

    例：『我觉得世界无聊，是因为我的世界本身已经足够有趣了。』—— 「永远的七日之都 璐璐」
    from / from_who 可能缺一个甚至两个都缺，缺谁就不写谁；两个都没有时只发正文，
    不留半个空括号 —— 宁可少写，也不要发出「—— 「」」这种东西。
    """
    source = str(payload.get('from') or '').strip()
    author = str(payload.get('from_who') or '').strip()
    who = ' '.join(part for part in (source, author) if part)
    if not who:
        return text
    return '『%s』—— 「%s」' % (text, who)


def fetch_hitokoto():
    """取一句一言，成功返回**要发出去的那整段文字**，取不到返回 None。

    返回的是完整的一条消息：正文用 『』 包起来，后面接 —— 「来源 作者」（用户 m03008
    指定的格式）。面板上「取一条试试」看到的就是真正发出去的那段文字。

    失败一律返回 None 而不是抛出去：调用方（定时任务）的第一目标是「火花不能断」，
    取不到就用任务自己的文案兜底，绝不能因为这个第三方接口挂了就整天不发。
    """
    try:
        req = requests.get(HITOKOTO_URL, timeout=HITOKOTO_TIMEOUT)
        if req.status_code != 200:
            log_event('warn', '一言', '一言接口返回异常状态码 %s' % req.status_code)
            return None
        payload = req.json() or {}
        if not isinstance(payload, dict):
            log_event('warn', '一言', '一言接口返回的不是一个 JSON 对象')
            return None
        value = payload.get('hitokoto')
        text = str(value).strip() if value else ''
        if not text:
            log_event('warn', '一言', '一言接口没有返回可用内容')
            return None
        return _format_hitokoto(text, payload)
    except Exception as exc:
        log_event('warn', '一言', '获取一言失败', exc)
        return None


# 定时任务的内容来源：'text' = 用任务自己存的文案池；'hitokoto' = 每次发送前现取一句；
# 'girlfriend' = 按发送时刻的时段现渲染一条天气问候（见下面的「女朋友模式」）
TASK_SOURCES = ('text', 'hitokoto', 'girlfriend')


def _normalise_task_source(value):
    """归一化任务的内容来源：None / 空 / 非法值一律回落到 'text'。"""
    source = str(value or '').strip().lower()
    return source if source in TASK_SOURCES else 'text'


def _task_source_error(value):
    """校验请求体里的 source：没传（None / 空）合法，除此之外只认 TASK_SOURCES。"""
    if value is None or not str(value).strip():
        return None
    if str(value).strip().lower() not in TASK_SOURCES:
        return '内容来源不支持，只支持 text、hitokoto 或 girlfriend'
    return None


def _task_source_note(source):
    """任务日志里那句「正文从哪来」的说明；'text' 是默认口径，不加尾巴。"""
    if source == 'hitokoto':
        return '（正文取一言接口，写下的文案只当兜底）'
    if source == 'girlfriend':
        return '（正文按发送时刻现渲染天气问候，写下的文案只当兜底）'
    return ''


# ==================== 女朋友模式（和风天气 + 农历 + 问候语） ====================
# 给「早安 / 午安 / 晚安」加一个内容来源：按发送时刻的时段，用和风天气的真实天气
# 渲染一条情侣贺卡式的问候语。Host 与 Key 都由用户在设置面板里填 —— 和风天气给每个
# 项目分配的专属域名都不一样，代码里不硬编码任何密钥。
#
# 冻死的接口（前端 src/api/douyin.js 与 src/views/Settings.vue 已按这套字段写好）：
#   GET  /Api/Girlfriend/Config  -> {'code':200,'data':{...配置字段...,'city_resolved':bool,
#                                    'city_resolved_text':str,'meet_days':int|None}}
#     city_resolved 只说明「坐标有了」，面板上要显示的城市名是 city_resolved_text
#     （解析出来的正式名 + 省市，没解析过就是用户填的原文）—— 只回布尔会让界面显示成 'true'。
#   POST /Api/Girlfriend/Config  -> 同上（body 是部分字段，未知字段忽略）
#   GET  /Api/Girlfriend/Weather?refresh=0 -> {'code':200,'data':{'city','lat','lon','tz','current','daily','text'}}
#   GET  /Api/Girlfriend/Preview?period=auto|morning|noon|night -> {'code':200,'data':{'period','period_text','text'}}
# 失败一律是 {'code':400,'data':'<中文原因>'}，绝不把异常抛给调用方。
GF_CONFIG_FIELDS = ('enabled', 'host', 'key', 'city', 'lat', 'lon', 'tz',
                    'meet_date', 'her_name', 'my_name', 'city_name', 'city_adm')
# 落盘结构：除 enabled 是布尔，其余都是字符串。缺字段一律按默认值补齐，
# 于是老 state.json 里没有 girlfriend 键、或者以后加了新字段，都能读。
GF_DEFAULT_CONFIG = {
    'enabled': False,
    'host': '',
    'key': '',
    'city': '',
    'lat': '',
    'lon': '',
    'tz': '',
    'meet_date': '',
    'her_name': '',
    'my_name': '',
    # city_name / city_adm 不是用户填的，是 GeoAPI 解析出来的城市正式名与省市
    # （'义乌' + '浙江省 金华'）。面板上「已解析城市」显示的就是它们 —— 只回一个
    # 布尔的话，界面会直接显示成 'true'（我自己踩过这个坑）。城市一改就一起作废。
    'city_name': '',
    'city_adm': '',
}
# 和风天气每个项目的 Host 形如 https://xxxxx.qweatherapi.com（每项目一个专属域名）。
# 超时走环境变量，测试里可以调小，免得 stub 出问题时把测试挂死。
QWEATHER_TIMEOUT = _env_int('SPARK_QWEATHER_TIMEOUT', 10)
GF_PERIOD_TEXT = {'morning': '早安', 'noon': '午安', 'night': '晚安'}
# 问候语首行（用户卡片口气）：时段词并进「哈喽哈喽」那一句，不再单独占一行。
# my_name 有值时才在「这里是」后面插「来自{称呼}的」，所以绝不会出现空的「来自」。
GF_GREETING = {
    'morning': '哈喽哈喽~早安呀，这里是爱心提醒哦：',
    'noon': '哈喽哈喽~午安呀，这里是爱心提醒哦：',
    'night': '哈喽哈喽~晚安呀，这里是爱心提醒哦：',
}
GF_WEEKDAYS = ('一', '二', '三', '四', '五', '六', '日')
# 八个方位。和风天气给的是 16 方位码（n / nne / ne / ene …），中途方位就近归并。
GF_WIND_DIRECTIONS = (
    ('n', '北'), ('ne', '东北'), ('e', '东'), ('se', '东南'),
    ('s', '南'), ('sw', '西南'), ('w', '西'), ('nw', '西北'),
)
GF_COMPASS_16 = ('n', 'nne', 'ne', 'ene', 'e', 'ese', 'se', 'sse',
                 's', 'ssw', 'sw', 'wsw', 'w', 'wnw', 'nw', 'nnw')


def _girlfriend_config():
    """读女朋友模式配置：缺的字段一律补默认值，返回的永远是一份可直接用的 dict。"""
    stored = STATE.get('girlfriend')
    config = dict(GF_DEFAULT_CONFIG)
    if isinstance(stored, dict):
        for field in GF_CONFIG_FIELDS:
            if field in stored:
                config[field] = stored[field]
    config['enabled'] = _bool_flag(config.get('enabled'))
    for field in GF_CONFIG_FIELDS:
        if field == 'enabled':
            continue
        value = config.get(field)
        config[field] = '' if value is None else str(value).strip()
    return config


def _girlfriend_view(config=None):
    """接口回显用的视图：配置字段 + 派生字段。

    Key 是凭据，照「消息通知」的口径不回明文：前端拿 key_set 显示「已配置」，
    保存时留空即表示「不修改已保存的那一份」。
    """
    config = _girlfriend_config() if config is None else config
    view = dict(config)
    view['key'] = ''
    view['key_set'] = bool(config.get('key'))
    view['city_resolved'] = bool(str(config.get('lat') or '').strip()
                                 and str(config.get('lon') or '').strip())
    # 面板上「已解析城市：」后面要显示的是城市名，不是 true/false。
    # 优先用 GeoAPI 解析出来的正式名（义乌 + 浙江省 金华），没解析过就退回用户填的原文。
    resolved_name = str(config.get('city_name') or '').strip() or config['city']
    resolved_adm = str(config.get('city_adm') or '').strip()
    if resolved_name and resolved_adm:
        view['city_resolved_text'] = '%s（%s）' % (resolved_name, resolved_adm)
    else:
        view['city_resolved_text'] = resolved_name
    view['meet_days'] = girlfriend_meet_days(config)
    return view


def _parse_meet_date(value):
    """把 'YYYY-MM-DD' 解析成 datetime：空 / 格式不对 / 日期不存在都返回 None。"""
    text = str(value or '').strip()
    if not text or not re.match(r'^\d{4}-\d{2}-\d{2}$', text):
        return None
    try:
        return datetime.strptime(text, '%Y-%m-%d')
    except ValueError:
        return None


def _valid_meet_date(value):
    """相识日期是否是一个合法的 YYYY-MM-DD（供保存接口做 400 校验）。"""
    return _parse_meet_date(value) is not None


def girlfriend_meet_days(config=None, moment=None):
    """相识天数：(今天 - 相识日期).days + 1；没填或格式不对返回 None。

    第 1 天就是相识当天（不填日期这一行整行不出现）。
    """
    config = _girlfriend_config() if config is None else config
    meet = _parse_meet_date(config.get('meet_date'))
    if meet is None:
        return None
    today = (moment or datetime.now()).date()
    return (today - meet.date()).days + 1


def girlfriend_period(moment=None):
    """发送时刻的小时决定时段：< 11 早安、11–16 午安、≥ 17 晚安。"""
    hour = (moment or datetime.now()).hour
    if hour < 11:
        return 'morning'
    if hour < 17:
        return 'noon'
    return 'night'


def _gf_nested(node, *path):
    """按路径取嵌套字段：中途不是 dict 就返回 None（和风天气的字段层级不固定）。"""
    current = node
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _gf_first(*values):
    """取第一个「有值」的候选：None / 空串 / 空白串都跳过。"""
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                return value.strip()
            continue
        return value
    return None


def _gf_number(value):
    """转成 float；转不动就返回 None（缺字段的整行不出现，不要写 0）。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _gf_round(value):
    """四舍五入取整。

    不能直接用 round()：它是「银行家舍入」，round(2.5) == 2 —— 天气里的 2.5℃
    被舍成 2℃ 属于肉眼可见的错。
    """
    number = float(value)
    return int(number + 0.5) if number >= 0 else -int(-number + 0.5)


def _gf_display_number(value):
    """整数就按整数显示（aqi 123.0 → '123'），否则原样。"""
    number = _gf_number(value)
    if number is None:
        return str(value)
    return str(int(number)) if float(number).is_integer() else str(number)


def _gf_wind_direction(compass):
    """把 compass 归并成 8 个中文方位；none / vrb / 未知取值返回 None（整行省略）。"""
    text = str(compass or '').strip().lower()
    if not text or text in ('none', 'vrb') or text not in GF_COMPASS_16:
        return None
    index = GF_COMPASS_16.index(text)
    return GF_WIND_DIRECTIONS[(index + 1) // 2 % 8][1]


def _gf_humidity_percent(value):
    """湿度按小数制（0.95 → 95%）；万一是百分制（>1.5）就原样用，别渲染成 5500%。"""
    number = _gf_number(value)
    if number is None:
        return None
    if number <= 1.5:
        number *= 100
    return _gf_round(number)


def _gf_temp_band(tmax):
    """把最高温归到一个档位：None / 'freezing' / 'cold' / 'cool' / 'mild' / 'hot'。

    阈值**只有这一份**：`_gf_advice` 的建议正文和「{她}…」那行都从它派生，
    别在两处各写一套（改档位只需要改这里）。
    """
    high = _gf_number(tmax)
    if high is None:
        return None
    high = _gf_round(high)
    if high <= 0:
        return 'freezing'
    if high < 10:
        return 'cold'
    if high < 18:
        return 'cool'
    if high < 26:
        return 'mild'
    return 'hot'


def _gf_advice(tmax, precipitation):
    """按最高温给建议，降水为雨/雪再补一句；取不到就返回空列表（整段不出现）。"""
    lines = []
    high = _gf_number(tmax)
    band = _gf_temp_band(high)
    if band is not None:
        high = _gf_round(high)
        lines.append({
            # 「仅为」只在真的冷的时候用：34℃ 说「最高温度仅为 34℃」是反话。
            'freezing': '今日最高温度仅为 %d℃，冷得很~',
            'cold': '今日最高温度仅为 %d℃，可冷了~',
            'cool': '今日最高温度 %d℃，有点凉，记得加件外套',
            'mild': '今日最高温度 %d℃，温度刚好，出去走走吧',
            'hot': '今日最高温度 %d℃，有点热，记得多喝水',
        }[band] % high)
    kind = str(precipitation or '').strip().lower()
    if kind == 'rain':
        lines.append('今天有雨，记得带伞哦~')
    elif kind == 'snow':
        lines.append('今天有雪，出门当心路滑~')
    return lines


def _gf_her_line(her_name, tmax):
    """「{她}…」那行：也按最高温分档（34℃ 不该让人「注意保暖」）。

    没填昵称就整行不出现；拿不到温度时用不预设冷热的中性说法。
    """
    name = str(her_name or '').strip()
    if not name:
        return None
    tail = {
        'freezing': '可要注意保暖哦~',
        'cold': '可要注意保暖哦~',
        'cool': '记得添件衣服哦~',
        'mild': '今天天气不错，出去走走吧~',
        'hot': '记得多喝水哦~',
    }.get(_gf_temp_band(tmax), '记得照顾好自己哦~')
    return name + tail


def _gf_greeting_line(period, my_name=None):
    """问候语首行：把时段词并进「哈喽哈喽」那句；没填称呼就绝不出现空的「来自」。"""
    line = GF_GREETING.get(period) or GF_GREETING[girlfriend_period()]
    name = str(my_name or '').strip()
    if name:
        line = line.replace('这里是', '这里是来自%s的' % name, 1)
    return line


# ---- 农历：内置一张 1900–2100 的压缩表（每年一个整数），不引入任何新依赖 ----
# 已用 1900–2100 全部 201 个春节（正月初一的阳历日期）逐条校验，全部命中；
# 用户截图里的 2022-01-24 = 腊月廿二、2024-02-10 = 正月初一 也对得上。
LUNAR_INFO = [
    0x04bd8, 0x04ae0, 0x0a570, 0x054d5, 0x0d260, 0x0d950, 0x16554, 0x056a0, 0x09ad0, 0x055d2,
    0x04ae0, 0x0a5b6, 0x0a4d0, 0x0d250, 0x1d255, 0x0b540, 0x0d6a0, 0x0ada2, 0x095b0, 0x14977,
    0x04970, 0x0a4b0, 0x0b4b5, 0x06a50, 0x06d40, 0x1ab54, 0x02b60, 0x09570, 0x052f2, 0x04970,
    0x06566, 0x0d4a0, 0x0ea50, 0x06e95, 0x05ad0, 0x02b60, 0x186e3, 0x092e0, 0x1c8d7, 0x0c950,
    0x0d4a0, 0x1d8a6, 0x0b550, 0x056a0, 0x1a5b4, 0x025d0, 0x092d0, 0x0d2b2, 0x0a950, 0x0b557,
    0x06ca0, 0x0b550, 0x15355, 0x04da0, 0x0a5b0, 0x14573, 0x052b0, 0x0a9a8, 0x0e950, 0x06aa0,
    0x0aea6, 0x0ab50, 0x04b60, 0x0aae4, 0x0a570, 0x05260, 0x0f263, 0x0d950, 0x05b57, 0x056a0,
    0x096d0, 0x04dd5, 0x04ad0, 0x0a4d0, 0x0d4d4, 0x0d250, 0x0d558, 0x0b540, 0x0b6a0, 0x195a6,
    0x095b0, 0x049b0, 0x0a974, 0x0a4b0, 0x0b27a, 0x06a50, 0x06d40, 0x0af46, 0x0ab60, 0x09570,
    0x04af5, 0x04970, 0x064b0, 0x074a3, 0x0ea50, 0x06b58, 0x055c0, 0x0ab60, 0x096d5, 0x092e0,
    0x0c960, 0x0d954, 0x0d4a0, 0x0da50, 0x07552, 0x056a0, 0x0abb7, 0x025d0, 0x092d0, 0x0cab5,
    0x0a950, 0x0b4a0, 0x0baa4, 0x0ad50, 0x055d9, 0x04ba0, 0x0a5b0, 0x15176, 0x052b0, 0x0a930,
    0x07954, 0x06aa0, 0x0ad50, 0x05b52, 0x04b60, 0x0a6e6, 0x0a4e0, 0x0d260, 0x0ea65, 0x0d530,
    0x05aa0, 0x076a3, 0x096d0, 0x04afb, 0x04ad0, 0x0a4d0, 0x1d0b6, 0x0d250, 0x0d520, 0x0dd45,
    0x0b5a0, 0x056d0, 0x055b2, 0x049b0, 0x0a577, 0x0a4b0, 0x0aa50, 0x1b255, 0x06d20, 0x0ada0,
    0x14b63, 0x09370, 0x049f8, 0x04970, 0x064b0, 0x168a6, 0x0ea50, 0x06b20, 0x1a6c4, 0x0aae0,
    0x0a2e0, 0x0d2e3, 0x0c960, 0x0d557, 0x0d4a0, 0x0da50, 0x05d55, 0x056a0, 0x0a6d0, 0x055d4,
    0x052d0, 0x0a9b8, 0x0a950, 0x0b4a0, 0x0b6a6, 0x0ad50, 0x055a0, 0x0aba4, 0x0a5b0, 0x052b0,
    0x0b273, 0x06930, 0x07337, 0x06aa0, 0x0ad50, 0x14b55, 0x04b60, 0x0a570, 0x054e4, 0x0d160,
    0x0e968, 0x0d520, 0x0daa0, 0x16aa6, 0x056d0, 0x04ae0, 0x0a9d4, 0x0a2d0, 0x0d150, 0x0f252,
    0x0d520,
]
LUNAR_BASE = datetime(1900, 1, 31)
LUNAR_MONTHS = ('正月', '二月', '三月', '四月', '五月', '六月',
                '七月', '八月', '九月', '十月', '冬月', '腊月')
LUNAR_DAYS = ('初一', '初二', '初三', '初四', '初五', '初六', '初七', '初八', '初九', '初十',
              '十一', '十二', '十三', '十四', '十五', '十六', '十七', '十八', '十九', '二十',
              '廿一', '廿二', '廿三', '廿四', '廿五', '廿六', '廿七', '廿八', '廿九', '三十')


def _lunar_month_days(year, month):
    """农历某年某月是小月（29 天）还是大月（30 天）：第 month 位是 1 就是大月。"""
    return 30 if (LUNAR_INFO[year - 1900] & (0x10000 >> month)) else 29


def _lunar_leap_month(year):
    """农历某年的闰月（1–12），0 表示不闰。"""
    return LUNAR_INFO[year - 1900] & 0xf


def _lunar_leap_days(year):
    """农历某年闰月的天数：0 表示不闰，否则 29 / 30（看第 16 位）。"""
    if not _lunar_leap_month(year):
        return 0
    return 30 if (LUNAR_INFO[year - 1900] & 0x10000) else 29


def _lunar_year_days(year):
    """农历某年的总天数（12 个农历月 + 可能的闰月）。"""
    days = 348
    for month in range(12):
        if LUNAR_INFO[year - 1900] & (0x8000 >> month):
            days += 1
    return days + _lunar_leap_days(year)


def _lunar(year, month, day):
    """阳历 → 农历中文（'腊月廿二' / '正月初一' / '闰三月初一'）。

    只覆盖 1900-01-31 ~ 2100 年（表能算准的范围），超出返回 None ——
    「宁缺勿错」：算不准就不写农历这一行，也不瞎猜。
    """
    try:
        offset = (datetime(year, month, day) - LUNAR_BASE).days
    except ValueError:
        return None
    if offset < 0:
        return None
    lunar_year = 1900
    while lunar_year <= 2100:
        days = _lunar_year_days(lunar_year)
        if offset < days:
            break
        offset -= days
        lunar_year += 1
    else:
        return None
    leap = _lunar_leap_month(lunar_year)
    lunar_month, is_leap = 1, False
    while lunar_month <= 12:
        if leap and lunar_month == leap + 1 and not is_leap:
            span, is_leap = _lunar_leap_days(lunar_year), True
        else:
            span = _lunar_month_days(lunar_year, lunar_month)
        if offset < span:
            break
        offset -= span
        if is_leap:
            is_leap = False
        lunar_month += 1
    if lunar_month > 12 or offset > 29:
        return None
    prefix = '闰' if is_leap else ''
    return prefix + LUNAR_MONTHS[lunar_month - 1] + LUNAR_DAYS[offset]


# ---- 和风天气请求 ----
def _gf_api_get(host, key, path, params=None):
    """统一的 QWeather GET：(payload, None) 或 (None, 中文原因)。

    认证用 X-QW-Api-Key 请求头（新控制台的用法），不再用老的 ?key=。
    网络异常 / 非 200 / 不是 JSON / 错误码全部在这里收成一句中文原因，
    绝不抛给调用方 —— 天气接口挂了不该让整个发送任务炸掉。

    实测结论（用只读探针打真实 Host，各端点返回的顶层 key）：
        /geo/v2/city/lookup               -> ['code', 'location', 'refer']            code == '200'
        /weather/v1/current/{lat}/{lon}    -> ['metadata', 'condition', ...]            **没有 code**
        /weather/v1/daily/{lat}/{lon}      -> ['metadata', 'days']                      **没有 code**
        /airquality/v1/current/{lat}/{lon} -> ['metadata', 'indexes', ...]              **没有 code**
    也就是：老式 GeoAPI 靠顶层 `code` 报状态，新版 v1 天气 / 空气质量接口**根本不返回 code**，
    新接口的错误走 HTTP 4xx/5xx + {'error': {'status':…, 'type':…, 'title':…}}。
    所以这里 **字段不存在 ≠ 出错**：`code` 只在存在且非空时才校验。
    （这里一度写成「无条件要求 code == '200'」，真实环境 /weather/v1/* 全被误判成
    「错误码 未知」，取不到天气 —— 别改回去。）
    """
    base = str(host or '').strip().rstrip('/')
    if not base:
        return None, '请先在设置里填写和风天气 Host（形如 https://xxxxx.qweatherapi.com）'
    if not base.startswith(('http://', 'https://')):
        base = 'https://' + base
    try:
        response = requests.get(base + path, params=params or {},
                                headers={'X-QW-Api-Key': str(key or '').strip()},
                                timeout=QWEATHER_TIMEOUT)
    except Exception as exc:
        return None, '连接和风天气失败：%s' % exc
    status = getattr(response, 'status_code', None)
    if status in (401, 403):
        return None, '和风天气拒绝了这次请求（HTTP %s）：请检查 Host 与 Key 是否配套' % status
    if status != 200:
        return None, '和风天气返回 HTTP %s' % status
    try:
        payload = response.json()
    except Exception:
        return None, '和风天气返回的内容不是合法 JSON'
    if not isinstance(payload, dict):
        return None, '和风天气返回的数据结构不是预期的对象'
    # 1) 新 v1 接口的错误对象优先（正常 200 也可能带 error，把能拿到的都写进原因里）
    error = payload.get('error')
    if isinstance(error, dict) and error:
        title = str(error.get('title') or '').strip()
        kind = str(error.get('type') or '').strip()
        status_value = error.get('status')
        detail = title or kind or ('HTTP %s' % status_value if status_value is not None else '未知')
        if status_value is not None and (title or kind):
            detail = '%s（HTTP %s）' % (detail, status_value)
        return None, '和风天气返回错误：%s' % detail
    # 2) 老式接口的 code：只有存在且非空才校验。新 v1 天气 / 空气接口没有这个字段，
    #    此时直接当成功放行（字段不存在 ≠ 出错），否则真实环境永远取不到天气。
    code = str(payload.get('code') or '').strip()
    if code and code != '200':
        return None, '和风天气返回错误码 %s（请检查 Host、Key、城市名或坐标）' % code
    return payload, None


def _gf_resolve_location(config, refresh=False):
    """定位：优先用配置里缓存的坐标，没有（或 refresh）才查 GeoAPI。

    返回 ({'lat','lon','tz','name'}, None) 或 (None, 中文原因)。查到就写回配置，
    下一次不用再打 GeoAPI —— 免费额度是有限的。
    """
    lat = str(config.get('lat') or '').strip()
    lon = str(config.get('lon') or '').strip()
    tz = str(config.get('tz') or '').strip()
    city = str(config.get('city') or '').strip()
    # 正式城市名与省市：命中缓存时沿用上次解析的结果，别把「义乌」退回成用户填的「义乌市」。
    # resolved 标记「这次真的打了 GeoAPI」，调用方据此决定要不要把解析结果写回配置
    # （缓存命中时 name/adm 只是回显，写回去等于用用户原文覆盖掉正式名）。
    name = str(config.get('city_name') or '').strip() or city
    adm = str(config.get('city_adm') or '').strip()
    resolved = False
    if refresh or not (lat and lon):
        if city:
            payload, error = _gf_api_get(config.get('host'), config.get('key'),
                                         '/geo/v2/city/lookup',
                                         {'location': city, 'range': 'cn',
                                          'number': 1, 'lang': 'zh'})
            if error:
                return None, error
            # GeoAPI 是老式接口，一定返回顶层 code；这里显式再确认一次，
            # 别把上面 v1 接口「没有 code 也算成功」的规则放松到 GeoAPI 上。
            geo_code = str(payload.get('code') or '').strip()
            if geo_code != '200':
                return None, '和风天气返回错误码 %s（请检查 Host、Key、城市名或坐标）' % (
                    geo_code or '未知')
            locations = payload.get('location')
            found = None
            if isinstance(locations, list):
                for item in locations:
                    if isinstance(item, dict) and _gf_first(item.get('lat'), item.get('lon')):
                        found = item
                        break
            if found is None:
                return None, '和风天气没有查到城市「%s」，请换一个写法试试' % city
            lat = str(_gf_first(found.get('lat')) or '').strip()
            lon = str(_gf_first(found.get('lon')) or '').strip()
            tz = str(_gf_first(found.get('tz')) or tz).strip()
            name = str(_gf_first(found.get('name')) or city).strip()
            adm = ' '.join(part for part in (
                str(_gf_first(found.get('adm1')) or '').strip(),
                str(_gf_first(found.get('adm2')) or '').strip()) if part)
            resolved = True
    if not (lat and lon):
        return None, '请先在设置里填写城市，或直接填好经纬度'
    return {'lat': lat, 'lon': lon, 'tz': tz, 'name': name, 'adm': adm,
            'resolved': resolved}, None


def _gf_air_index(payload):
    """从 airquality 的 indexes[] 里挑一项 AQI：返回 (aqi, 类别) 或 None。

    注意这里的 `code` 是空气质量**指标**的代码，跟接口返回码同名不同义：
    真实 v1 接口给的是 `cn-mee`（中国生态环境部标准 AQI，中国大陆用户看的就是这个），
    老式/其它套餐才用笼统的 `aqi`。所以按 `cn-mee` → `aqi` → 第一个带数值 aqi 的项
    依次挑（任务描述里的编号清单写的是 `aqi` 在前，但测试要求「同时存在时优先 cn-mee」，
    这里按中国大陆口径实现）。取不到就返回 None —— 「尽力而为，少一行」的既有行为不变。
    """
    indexes = (payload or {}).get('indexes')
    if not isinstance(indexes, list):
        return None
    items = [item for item in indexes if isinstance(item, dict)]

    def pick(code):
        for item in items:
            if str(item.get('code') or '').strip().lower() == code:
                return item
        return None

    def as_pair(item):
        if not item:
            return None
        aqi = _gf_number(_gf_first(item.get('aqi')))
        category = str(_gf_first(item.get('category')) or '')
        if aqi is None and not category:
            return None
        return (aqi, category)

    for code in ('cn-mee', 'aqi'):
        pair = as_pair(pick(code))
        if pair is not None:
            return pair
    for item in items:
        aqi = _gf_number(_gf_first(item.get('aqi')))
        if aqi is None:
            continue
        category = str(_gf_first(item.get('category')) or '')
        return (aqi, category)
    return None


def collect_girlfriend_weather(config, location):
    """取实时 / 每日 / 空气质量。

    实时或每日任一失败就整条失败（没有温度和天气的问候语没有意义）；
    空气质量是「尽力而为」：和风天气的空气质量是另一个接口、也可能不在套餐里，
    取不到就悄悄少写「空气」这一行，不影响整条问候语。
    """
    host = str(config.get('host') or '').strip()
    key = str(config.get('key') or '').strip()
    lat = str(location.get('lat') or '').strip()
    lon = str(location.get('lon') or '').strip()
    current, error = _gf_api_get(host, key, '/weather/v1/current/%s/%s' % (lat, lon),
                                 {'lang': 'zh'})
    if error:
        return None, error
    daily, error = _gf_api_get(host, key, '/weather/v1/daily/%s/%s' % (lat, lon),
                               {'days': 1, 'lang': 'zh'})
    if error:
        return None, error
    days = daily.get('days')
    day = days[0] if isinstance(days, list) and days and isinstance(days[0], dict) else {}
    air = None
    air_payload, air_error = _gf_api_get(host, key,
                                         '/airquality/v1/current/%s/%s' % (lat, lon),
                                         {'lang': 'zh'})
    if not air_error:
        air = _gf_air_index(air_payload)
    return {'current': current, 'daily': day, 'air': air}, None


def build_girlfriend_message(config, moment, period, data, quote=None):
    """把配置 + 天气数据 + 时段渲染成一条短信（纯函数：不发任何网络请求）。

    形状（缺数据的整行不出现）：
        这是我们相识的第 N 天        ← 只有 meet_date 能解析时才出现
        蚌埠 | 2026年09月28日 | 星期一   ← 城市为空就只剩「日期 | 星期X」
        农历 | 八月十八              ← 农历算不出来就整行不出现
        <空行>
        今日天气状况：
        天气： 小雪
        东南风： 3级
        温度： -1℃ ~ 3℃
        湿度： 95%
        空气： 轻度污染 | 123
        <空行>
        哈喽哈喽~早安呀，这里是来自小明的爱心提醒哦：
        今日最高温度仅为 3℃，可冷了~
        今天有雪，出门当心路滑~
        小明可要注意保暖哦~
        <空行>
        『正文』—— 「来源 作者」
    """
    config = config or {}
    moment = moment or datetime.now()
    if period not in GF_GREETING:
        period = girlfriend_period(moment)
    data = data or {}
    my_name = str(config.get('my_name') or '').strip()
    her_name = str(config.get('her_name') or '').strip()

    # 城市要出现在日期行里（用户卡片的排版），所以先算出来。
    # 优先用配置里用户自己填的原文：天气接口那侧是 GeoAPI 的解析名（义乌市），
    # 预览接口走缓存坐标时拿不到解析名（义乌）—— 同一份配置不该因为走哪条路而变脸。
    city = str(_gf_first(config.get('city'), data.get('city')) or '').strip()

    # 1) 日期块
    date_lines = []
    meet_days = girlfriend_meet_days(config, moment)
    if meet_days is not None:
        date_lines.append('这是我们相识的第 %d 天' % meet_days)
    date_line = '%s | 星期%s' % (moment.strftime('%Y年%m月%d日'),
                                 GF_WEEKDAYS[moment.weekday()])
    if city:
        # 城市为空时不要留下多余的「 | 」前缀
        date_line = '%s | %s' % (city, date_line)
    date_lines.append(date_line)
    lunar = _lunar(moment.year, moment.month, moment.day)
    if lunar:
        date_lines.append('农历 | %s' % lunar)

    # 2) 天气块（每日预报优先，实时数据兜底）
    current = data.get('current') or {}
    day = data.get('daily') or {}
    daytime = _gf_nested(day, 'daytime') or {}
    condition = _gf_first(_gf_nested(daytime, 'condition', 'text'),
                          _gf_nested(current, 'condition', 'text'))
    compass = _gf_first(_gf_nested(daytime, 'wind', 'direction', 'compass'),
                        _gf_nested(current, 'wind', 'direction', 'compass'))
    scale = _gf_first(_gf_nested(daytime, 'wind', 'scale'),
                      _gf_nested(current, 'wind', 'scale'))
    direction = _gf_wind_direction(compass)
    tmax = _gf_first(_gf_nested(day, 'temperatureMax', 'value'))
    tmin = _gf_first(_gf_nested(day, 'temperatureMin', 'value'))
    humidity = _gf_humidity_percent(_gf_first(_gf_nested(current, 'humidity'),
                                              _gf_nested(daytime, 'humidity')))
    weather_lines = ['今日天气状况：']
    if condition:
        weather_lines.append('天气： %s' % condition)
    if direction and scale:
        weather_lines.append('%s风： %s级' % (direction, scale))
    elif direction:
        weather_lines.append('%s风' % direction)
    elif scale:
        weather_lines.append('风力： %s级' % scale)
    if tmax is not None and tmin is not None:
        weather_lines.append('温度： %d℃ ~ %d℃' % (_gf_round(tmin), _gf_round(tmax)))
    elif tmax is not None:
        weather_lines.append('温度： 最高 %d℃' % _gf_round(tmax))
    elif tmin is not None:
        weather_lines.append('温度： 最低 %d℃' % _gf_round(tmin))
    if humidity is not None:
        weather_lines.append('湿度： %d%%' % humidity)
    air = data.get('air')
    if air:
        aqi_value, air_category = air
        if air_category and aqi_value is not None:
            weather_lines.append('空气： %s | %s' % (air_category, _gf_display_number(aqi_value)))
        elif aqi_value is not None:
            weather_lines.append('空气： %s' % _gf_display_number(aqi_value))
        elif air_category:
            weather_lines.append('空气： %s' % air_category)

    # 3) 问候块：时段词并进「哈喽哈喽」那句，不再单独占一行
    greeting_lines = [_gf_greeting_line(period, my_name)]
    greeting_lines.extend(_gf_advice(tmax, _gf_first(
        _gf_nested(daytime, 'precipitation', 'type'),
        _gf_nested(current, 'precipitation', 'type'))))
    if her_name:
        hers = _gf_her_line(her_name, tmax)
        if hers:
            greeting_lines.append(hers)

    message = '\n\n'.join(('\n'.join(date_lines),
                           '\n'.join(weather_lines),
                           '\n'.join(greeting_lines)))
    if quote:
        # 一言单独成段（和用户给的示例一致），取不到就整行不出现
        message += '\n\n' + str(quote).strip()
    # 曾经在这里拼过一行「[点我有惊喜] ❤️🧡💛💚💙」；用户 2026-09-28 明确要求移除，
    # 别再往问候里加任何彩蛋尾巴（多一行就多一分像群发的味道）。
    return message


def girlfriend_render(period='auto', moment=None, refresh=False):
    """取定位 + 天气并渲染：返回 (text, data, error)，error 是一句中文原因。

    发送路径和预览路径共用这一份逻辑，保证「预览里看到什么，发出去就是什么」。
    """
    config = _girlfriend_config()
    moment = moment or datetime.now()
    if period not in GF_GREETING:
        period = girlfriend_period(moment)
    if not str(config.get('host') or '').strip():
        return None, None, '请先在设置里填写和风天气 Host（形如 https://xxxxx.qweatherapi.com）'
    if not str(config.get('key') or '').strip():
        return None, None, '请先在设置里填写和风天气 Key'
    location, error = _gf_resolve_location(config, refresh=refresh)
    if error:
        return None, None, error
    weather, error = collect_girlfriend_weather(config, location)
    if error:
        return None, None, error
    if not weather.get('current') and not weather.get('daily'):
        return None, None, '和风天气没有返回可用的天气数据'
    # 定位结果缓存进配置：下次直接拿坐标，不再打 GeoAPI
    fields = [('lat', location.get('lat')), ('lon', location.get('lon')),
              ('tz', location.get('tz'))]
    if location.get('resolved'):
        # 只有真的打过 GeoAPI 才写正式名：缓存命中时 location 里的 name/adm 只是回显，
        # 写回去等于拿用户填的「义乌市」覆盖掉解析出来的「义乌」。
        fields.append(('city_name', location.get('name')))
        fields.append(('city_adm', location.get('adm')))
    dirty = False
    for field, value in fields:
        if str(config.get(field) or '').strip() != str(value or '').strip():
            config[field] = str(value or '')
            dirty = True
    if dirty:
        STATE.set('girlfriend', config)
    data = dict(weather)
    data['city'] = str(_gf_first(location.get('name'), config.get('city')) or '').strip()
    # 定位结果一并回给调用方：面板要用它回填 lat/lon/tz（缓存命中时也要能拿到）
    data['lat'] = str(location.get('lat') or '')
    data['lon'] = str(location.get('lon') or '')
    data['tz'] = str(location.get('tz') or '')
    text = build_girlfriend_message(config, moment, period, data, quote=fetch_hitokoto())
    return text, data, None


def render_girlfriend_text(period='auto', moment=None, refresh=False):
    """发送路径用的薄包装：成功返回文案，失败返回 None（调用方自己决定兜底）。"""
    return girlfriend_render(period=period, moment=moment, refresh=refresh)[0]


# ==================== 文昌帝君灵签（每日图文） ====================
def fetch_wenchang_sign():
    """取一条文昌帝君灵签，返回 {'title','poem','content','pic'}；取不到返回 None。

    注意这里**不**回落到本地兜底文案：签文是用户主动勾选的内容，随机换成一句
    「今天也要加油」并不是他想要的。调用方负责决定取不到时怎么办
    （手动发送直接报错，定时任务退化成纯文字，因为「火花不能断」是硬需求）。
    """
    try:
        req = requests.get(WENCHANG_URL, timeout=IMAGE_FETCH_TIMEOUT)
        if req.status_code != 200:
            log_event('warn', '灵签', '灵签接口返回异常状态码 %s' % req.status_code)
            return None
        body = req.json() or {}
        if str(body.get('code')) != '200':
            log_event('warn', '灵签', '灵签接口返回失败：%s' % body.get('msg'))
            return None
        data = body.get('data') or {}
        if not isinstance(data, dict):
            log_event('warn', '灵签', '灵签接口数据结构不符合预期')
            return None
        sign = {
            'title': str(data.get('title') or '').strip(),
            'poem': str(data.get('poem') or '').strip(),
            'content': str(data.get('content') or '').strip(),
            'pic': str(data.get('pic') or '').strip(),
        }
        if not (sign['title'] or sign['poem'] or sign['content']):
            log_event('warn', '灵签', '灵签接口返回了空签文')
            return None
        return sign
    except Exception as exc:
        log_event('warn', '灵签', '获取灵签失败', exc)
        return None


def _bool_flag(value):
    """把请求体里的开关字段解析成布尔。

    前端任务表单传的是 JSON 布尔；手动发送那条路径按老习惯传的是字符串
    （'wenchang' / '1' / 'true' 都算开），这样面板和脚本调用都能用。
    注意不能直接用 bool(value)：字符串 'false' 也是真。
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on', 'wenchang', 'sign')


# ==================== 图片下载与回收 ====================
def _prune_dir(directory, keep_days, max_files):
    """按天数和数量上限回收目录里的旧文件（先删最旧的）。

    图片下载目录和失败截图目录用的是同一套规则：天数管「留多久」，
    数量上限管「最多占多少」—— 后者是为了防止接口返回随机图链时把磁盘写满。
    """
    try:
        if not os.path.isdir(directory):
            return 0
        entries = []
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            if not os.path.isfile(path):
                continue
            try:
                entries.append((os.path.getmtime(path), path))
            except OSError:
                continue
        entries.sort()
        removed = 0
        if keep_days and keep_days > 0:
            deadline = time.time() - keep_days * 86400
            for mtime, path in list(entries):
                if mtime < deadline:
                    try:
                        os.remove(path)
                        entries.remove((mtime, path))
                        removed += 1
                    except OSError:
                        continue
        if max_files and max_files > 0 and len(entries) > max_files:
            for _mtime, path in entries[:len(entries) - max_files]:
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    continue
        return removed
    except Exception as exc:
        log_event('warn', '清理', '清理目录 %s 失败' % directory, exc)
        return 0


def _prune_images():
    """回收 data/images 下的旧签图。"""
    return _prune_dir(IMAGE_DIR, IMAGE_KEEP_DAYS, IMAGE_MAX_FILES)


def download_image(url, timeout=IMAGE_FETCH_TIMEOUT):
    """把灵签图片下载到本地，返回 (本地路径, 失败原因)；成功时原因是 None。

    为什么要落到本地文件而不是想办法直接把 URL 交给浏览器：聊天框的上传控件
    只接受本机文件（file input），让浏览器去下载再上传反而多一层不确定。
    """
    try:
        url = str(url or '').strip()
        if not url:
            return None, '图片地址为空'
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ('http', 'https'):
            return None, '图片地址必须是 http/https 链接'
        # 面板可以被远程访问，而图片地址是从第三方接口拿的：这里挡掉本机/内网地址，
        # 避免接口被劫持后把面板变成探测内网的工具（和推送地址用的是同一套判断）。
        private_reason = private_host_reason(parts.hostname)
        if private_reason:
            return None, '图片地址指向内网，已拒绝下载（%s）' % private_reason
        req = requests.get(url, timeout=timeout, stream=True)
        if req.status_code != 200:
            return None, '下载图片失败（HTTP %s）' % req.status_code
        content_type = req.headers.get('Content-Type', '') if hasattr(req, 'headers') else ''
        if content_type and not str(content_type).lower().startswith('image/'):
            return None, '下载到的不是图片（Content-Type: %s）' % content_type
        # 边读边判上限：等整个响应读完再判大小，接口返回一个超大盘时内存就爆了。
        chunks = []
        total = 0
        for chunk in req.iter_content(65536):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if IMAGE_MAX_BYTES and total > IMAGE_MAX_BYTES:
                return None, '图片超过 %.1f MB 上限' % (IMAGE_MAX_BYTES / 1024.0 / 1024.0)
        data = b''.join(chunks)
        error = image_payload_error(data, content_type, IMAGE_MAX_BYTES)
        if error:
            return None, error
        suffix, _mime = image_kind(data)
        # 用 URL + 内容一起算文件名：同一个签图重复发送不会重复下载落盘。
        digest = hashlib.sha1(url.encode('utf-8') + data).hexdigest()[:20]
        os.makedirs(IMAGE_DIR, exist_ok=True)
        path = os.path.join(IMAGE_DIR, digest + suffix)
        if not os.path.exists(path):
            # 先写 .part 再原子改名：避免下载中途失败留下一个半截文件被当成完整图片上传。
            tmp = path + '.part'
            with open(tmp, 'wb') as fp:
                fp.write(data)
            os.replace(tmp, path)
        _prune_images()
        return path, None
    except Exception as exc:
        return None, '下载图片出错：%s' % exc


# 注：原先这里有个交互式 CLI 用的 Get_Cooke()，里面有死循环 + driver.close() + exit()。
# 那是命令行时代留下的东西，Web 服务里被误调用会把整个后端进程结束掉，已删除。


# format_time 已移到 spark_core（纯函数、可单测），文件顶部统一 import。


class TrueString:
    def __init__(self, is_bool, string, status=None):
        self.is_bool = is_bool
        self.string = string
        if status in ('success', 'failed', 'unknown'):
            self.status = status
        else:
            self.status = 'success' if is_bool else None


class UserFriendsInfo:
    def __init__(self, username, avatar, fire, ambiguous=False):
        self.username = username
        self.avatar = avatar
        self.fire = fire
        self.ambiguous = bool(ambiguous)


# ---------- 聊天输入框定位 ----------
# 抖音的类名是构建期拼出来的（如 conversationConversationListwrapper），会随版本变化。
# 旧实现把编辑器写死成 div[class="messageEditorimChatEditorContainer"]，
# 实测该类名在当前版本已经不存在（真实页面上 [class*=messageEditor] 命中数为 0），
# 所以这里按「行为特征」找输入框：contenteditable / textarea，类名只作加速线索。
# 优先 data-e2e（本项目 _detect_logged_in_state 已在用），再退到行为特征。
CHAT_EDITOR_XPATHS = [
    '//*[@data-slate-editor="true"]',
    '//*[contains(@class,"messageEditorimChatEditorContainer")]//*[@contenteditable="true"]',
    '//*[contains(@class,"messageMsgInput")]//*[@contenteditable="true"]',
    '//*[@data-placeholder="发送消息"]',
    '//*[@data-e2e="message-input"]',
    '//*[@data-e2e="chat-input"]',
    '//div[@contenteditable="true" and contains(@class,"imChatEditor")]',
    '//div[contains(@class,"messageEditor")]//*[@contenteditable="true"]',
    '//div[contains(@class,"messageEditor")]//textarea',
    '//div[@contenteditable="true"][@role="textbox"]',
    '//*[@contenteditable="true"]',
    '//textarea[not(@type="hidden")]',
]

# 实测（2026-09-14 真实页面）：发送按钮是输入行右侧的 svg
#   <svg class="messageMsgInputpublishBtn messageMsgInputpublishRedBtn e2e-send-msg-btn" ...>
# 左边那个同款 svg 是表情按钮（messageMsgInputiconAction），别点错。
CHAT_SEND_XPATHS = [
    '//*[contains(@class,"messageMsgInputpublishBtn")]',
    '//*[contains(@class,"e2e-send-msg-btn")]',
    '//*[@data-e2e="send-msg-btn"]',
    '//*[contains(@class,"messageMsgInputinputAction")]//*[contains(@class,"publishBtn")]',
    '//*[normalize-space()="发送"]',
]

CHAT_EDITOR_TEXT_JS = """
var el = arguments[0];
if (!el) return null;
if (el.tagName && el.tagName.toLowerCase() === 'textarea') return el.value || '';
return el.innerText || '';
"""

# 空的 Slate 编辑器 innerText 是 '\\n' 或零宽空格（\\u200b），
# 而 Python 的 str.strip() 不认零宽空格，会把「已清空」误判成「没发出去」。
CHAT_EDITOR_EMPTY_CHARS = ' \t\r\n\u00a0\u200b\u200c\u200d\ufeff'


def _chat_editor():
    """返回当前打开会话里的消息输入框（找不到返回 None）。"""
    for xpath in CHAT_EDITOR_XPATHS:
        try:
            elements = driver.find_elements(By.XPATH, xpath)
        except Exception:
            continue
        for element in elements:
            try:
                if element.is_displayed() and element.is_enabled():
                    return element
            except Exception:
                continue
    return None


def _wait_chat_editor(timeout=10):
    """点开会话后等输入框出现。"""
    deadline = time.time() + timeout
    while True:
        editor = _chat_editor()
        if editor is not None:
            return editor
        if time.time() >= deadline:
            return None
        time.sleep(0.5)


def _chat_editor_text(element):
    """读输入框内容；读不到返回 None（不要和「空字符串 = 已清空」混淆）。"""
    try:
        value = driver.execute_script(CHAT_EDITOR_TEXT_JS, element)
    except Exception:
        return None
    return None if value is None else value.strip(CHAT_EDITOR_EMPTY_CHARS)


def _wait_message_sent(editor, text, timeout=6):
    """回车/点发送后，输入框被清空即认为发出去了。

    只有确实读到「空」才判定成功；读不到内容（None）继续等，
    避免元素被 React 重新渲染时误报成功。

    注意：这只是「已提交」的弱证据，不代表真的送达。只要能读到消息列表，
    就应该用 _confirm_message_delivered 判定；本函数只作为读不到列表时的兜底。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.5)
        current = _chat_editor_text(editor)
        if current == '':
            return True
        if current is not None and current != text:
            return True
    return False


# ==================== 发送结果确认（终态判定）====================
# 实测/参考开源项目的结论：抖音会先渲染己方消息气泡，之后才挂发送状态 ——
# 先转圈（发送中），再要么恢复正常，要么翻成红色重试标记。
# 所以「气泡出现了」不等于「发送成功」，「输入框清空了」更不等于。
# 这里的规则：新气泡必须持续干净 SEND_INITIAL_CLEAN_GRACE 秒才算成功；
# 出现过转圈就要等它消失并再稳定 SEND_STABLE_INTERVAL 秒；
# 超过总预算一律不算成功 —— 宁可说「未确认」，也不谎报「已送达」。
SEND_CONFIRM_TIMEOUT = float(os.getenv('SPARK_SEND_TIMEOUT', '15'))    # 单条消息确认预算
SEND_POLL_INTERVAL = float(os.getenv('SPARK_SEND_POLL', '0.2'))         # 轮询间隔
SEND_STABLE_INTERVAL = 0.5                                              # 转圈消失后的稳定观察
# 新气泡「干净」要连续观察多久才算成功。默认 1.0s：
# 这是「确认送达」与「用户等待」之间的平衡点 —— 抖音挂重试标记通常在一两拍之内，
# 1 秒足够发现；再长就纯粹是让人干等了（实测 2s 时用户能明显感到变慢）。
# 想让判定更保守可设 SPARK_SEND_GRACE=2。
SEND_INITIAL_CLEAN_GRACE = float(os.getenv('SPARK_SEND_GRACE', '1.0'))
CHAT_BUBBLE_SEEN_TAG = 'data-spark-seen'
FAILURE_SHOT_DIR = os.path.join(LOG_DIR, 'shots')

CHAT_OUTGOING_PROBE_JS = r"""
var TEXT = arguments[0], MODE = arguments[1], TAG = 'data-spark-seen';
var SELECTORS = ['.messageMessageListlist', '[class*="messageMessageListlist"]',
                 '[class*="messageMessageListwrapper"]', '[class*="MessageList"]'];
var list = null;
for (var i = 0; i < SELECTORS.length; i++) {
  var candidate = document.querySelector(SELECTORS[i]);
  if (candidate) { list = candidate; break; }
}
function norm(value) { return (value || '').replace(/[\s\u200B\u200C\u200D\uFEFF]+/g, ' ').trim(); }
if (!list) return {list: false, total: 0, matches: 0, untagged: 0, state: 'none'};
// 只取最外层气泡：内层 contentBox 也带 messageBox 类名，取内层会把状态图标漏在外面
var raw = list.querySelectorAll('[class*="messageBox"], [class*="MessageBox"], [class*="msgBox"]');
var items = [];
for (var i = 0; i < raw.length; i++) {
  var parent = raw[i].parentElement, nested = false;
  while (parent && parent !== list) {
    var cls = (parent.getAttribute && parent.getAttribute('class')) || '';
    if (cls.indexOf('messageBox') >= 0 || cls.indexOf('MessageBox') >= 0 || cls.indexOf('msgBox') >= 0) {
      nested = true; break;
    }
    parent = parent.parentElement;
  }
  if (!nested) items.push(raw[i]);
}
if (MODE === 'tag') {
  var target0 = norm(TEXT), matches0 = 0;
  for (var i = 0; i < items.length; i++) {
    if (norm(items[i].innerText).indexOf(target0) >= 0) matches0++;
    items[i].setAttribute(TAG, '1');
  }
  return {list: true, total: items.length, matches: matches0, untagged: 0, state: 'none'};
}
var target = norm(TEXT), matches = 0, untagged = 0, newest = null, newestTop = null;
for (var i = 0; i < items.length; i++) {
  if (norm(items[i].innerText).indexOf(target) < 0) continue;
  matches++;
  if (!items[i].hasAttribute(TAG)) {
    untagged++;
    // 真机取证（2026-09-28 probe3）：这个列表是 column-reverse，DOM 里第一条就是**最新**的
    // （idx=0 top=2776 在视觉最下面，idx=15 top=-397 在视觉最上面）。取「DOM 里最后一个匹配」
    // 等于取最旧的那条消息，方向和送达状态都会用错人 —— 要按「视觉最靠下（top 最大）」挑。
    var top = items[i].getBoundingClientRect().top;
    if (newest === null || top > newestTop) { newest = items[i]; newestTop = top; }
  }
}
var out = {list: true, total: items.length, matches: matches, untagged: untagged, state: 'none', side: 'unknown'};
if (!newest) return out;
function bubbleSide(el) {
  var node = el, depth = 0;
  while (node && node !== list && depth < 8) {
    var cls = ((node.getAttribute && node.getAttribute('class')) || '') + ' ' +
              ((node.getAttribute && node.getAttribute('data-e2e')) || '');
    if (/self|isFromMe|fromMe|isMe|messageRight|msg-right|rightMessage|ownMessage|is-self/i.test(cls)) return 'self';
    if (/fromOther|isFromOther|messageLeft|msg-left|leftMessage|is-other/i.test(cls)) return 'other';
    node = node.parentElement;
    depth++;
  }
  // 真机取证（2026-09-28 实际 DOM）：抖音把发送方标记挂在**气泡内层**的 contentBox 上，
  // 我方的 class 是「messageMessageBoxcontentBox messageMessageBoxisFromMe」，
  // 对方只有「messageMessageBoxcontentBox」。上面那段只看自己和祖先，永远找不到它，
  // side 恒为 unknown，于是一条明明已经送达的消息被判成「未确认」。
  // 所以标记必须往下找一层。
  var inner = el.querySelector('[class*="contentBox"], [class*="ContentBox"]');
  if (inner) {
    var icls = ((inner.getAttribute && inner.getAttribute('class')) || '');
    if (/isFromMe|fromMe|isMe|ownMessage|messageRight|rightMessage/i.test(icls)) return 'self';
    return 'other';
  }
  // 兜底：气泡行往往和列表一样宽（真机 1052 vs 1064），拿整行中心点必然判不出方向。
  var rect = el.getBoundingClientRect();
  var box = list.getBoundingClientRect();
  if (!rect.width || !box.width) return 'unknown';
  var mid = rect.left + rect.width / 2;
  var center = box.left + box.width / 2;
  if (rect.width < box.width * 0.9) {
    if (mid >= center + 20) return 'self';
    if (mid <= center - 20) return 'other';
  }
  return 'unknown';
}
out.side = bubbleSide(newest);
if (/发送失败/.test(newest.innerText || '')) { out.state = 'failed'; return out; }
var FAILURE = ['[class*="SendStatusretry"]', '[class*="sendFailed"]', '[class*="SendFailed"]',
               '[aria-label*="重试"]', '[title*="重试"]'];
var PENDING = ['[class*="spin"]', '[class*="Spin"]', '[data-icon="spin"]', '[class*="sending"]'];
function hit(scope, selectors) {
  for (var i = 0; i < selectors.length; i++) {
    try { if (scope.querySelector(selectors[i])) return true; } catch (error) {}
  }
  return false;
}
if (hit(newest, FAILURE)) { out.state = 'failed'; return out; }
if (hit(newest, PENDING)) { out.state = 'pending'; return out; }
// 没有明确的发送方标记时，不能仅凭气泡位置推断消息属于当前账号。
if (out.side === 'self') out.state = 'clean';
out.untagged = untagged;
out.matches = matches;
return out;
"""


def _chat_probe(text, mode='check'):
    """读取消息列表状态。mode='tag' 给现存气泡打标记；mode='check' 返回匹配/状态快照。"""
    try:
        result = driver.execute_script(CHAT_OUTGOING_PROBE_JS, text, mode)
    except Exception:
        return {}
    return result if isinstance(result, dict) else {}


def _tag_outgoing_messages(text=''):
    """发送前给现存气泡打标记，之后「未标记的新气泡」才是这一条。"""
    return _chat_probe(text, 'tag')


def _editor_still_has(editor, text):
    """输入框里是否还留着这条内容（= 压根没提交出去，可以安全换触发方式）。"""
    current = _chat_editor_text(editor)
    if not current:
        return False
    flat_text = _editor_flatten(text)
    if not flat_text:
        return False
    flat_current = _editor_flatten(current)
    # 拉平后再比：Ace 编辑器会在换行前插零宽空格，严格子串比不出「还留着」。
    return flat_current == flat_text or flat_current in flat_text or flat_text in flat_current


# ==================== 图片消息：上传与送达确认 ====================
# 为什么图片要单独一套判据：文字消息的「已送达」靠「新气泡里出现这段文字」，
# 图片气泡里根本没有文字（innerText 是空的），拿文字判据去测必然一直判「未确认」。
# 记账键只看「日期 + 好友」，一旦误记成未确认，当天就不会再补发 —— 所以这里必须
# 用「气泡里有没有一张大图」作为独立判据，并单独打一套标记属性，
# 免得和文字路径的 data-spark-seen 互相污染。
CHAT_IMAGE_SEEN_TAG = 'data-spark-img-seen'
# 上传控件（file input）。抖音的聊天输入区有一个隐藏的 file input，按图片/文件两用：
# 优先找明确声明 accept=image 的，找不到就退到任意 file input。
CHAT_FILE_INPUT_XPATHS = (
    '//input[@type="file" and contains(@accept,"image")]',
    '//input[@type="file" and contains(@accept,"*")]',
    '//input[@type="file"]',
)
# 图片按钮：file input 不存在时（版式差异）先点一下图片按钮，控件通常才会被渲染出来。
CHAT_IMAGE_BUTTON_XPATHS = (
    '//*[contains(@class,"messageMsgInputicon") and (contains(@class,"image") or contains(@class,"Image"))]',
    '//*[contains(@class,"imageUpload") or contains(@class,"ImageUpload")]',
    '//*[contains(@class,"messageMsgInput")]//*[local-name()="svg" and contains(@class,"image")]/ancestor::*[self::div or self::span][1]',
    '//*[@aria-label="图片" or @aria-label="发送图片" or @title="图片"]',
)

# 把隐藏的 file input 临时「露出来」。ChromeDriver 对 display:none 的上传控件经常
# 直接抛 ElementNotInteractable，所以先把它挪到视口里可见的位置，发送完再还原样式。
CHAT_REVEAL_FILE_INPUT_JS = r"""
var el = arguments[0];
if (!el) return false;
if (!el.hasAttribute('data-spark-style')) {
  el.setAttribute('data-spark-style', el.getAttribute('style') || '');
}
el.style.display = 'block';
el.style.visibility = 'visible';
el.style.opacity = '1';
el.style.position = 'fixed';
el.style.left = '0px';
el.style.top = '0px';
el.style.width = '2px';
el.style.height = '2px';
el.style.zIndex = '2147483647';
return true;
"""

CHAT_RESTORE_FILE_INPUT_JS = r"""
var el = arguments[0];
if (!el) return false;
if (el.hasAttribute('data-spark-style')) {
  var prev = el.getAttribute('data-spark-style');
  if (prev) { el.setAttribute('style', prev); } else { el.removeAttribute('style'); }
  el.removeAttribute('data-spark-style');
}
return true;
"""

# 抖音的「确认发送文件」浮层。上传图片/文件之后这个模态层会盖住输入框，问一句
# 「发送给 XXX：xxx.jpg 57.4 KB」，给「取消 / 发送」两个按钮 —— 在它上面按回车
# 不会发送任何东西。
#
# 真机取证（2026-09-28）：当时只对输入框按了回车，于是图片一直挂在浮层里没发出去，
# 25 秒后被判成「未确认」；紧接着退化发文字时，输入框又被这个模态层挡住，报出
# 「消息内容没有写进输入框」。两个症状其实是同一个原因。
CHAT_SEND_FILE_MODAL_JS = r"""
var mode = arguments[0] || 'check';
var box = document.querySelector('.MsgInputSendFileModalbox');
if (!box) return {present: false, mode: mode};
var sure = box.querySelector('.MsgInputSendFileModalbtnSure');
var cancle = box.querySelector('.MsgInputSendFileModalbtnCancle');
var title = box.querySelector('.MsgInputSendFileModaltitleBox');
var out = {present: true, mode: mode,
           title: title ? (title.innerText || '').replace(/\s+/g, ' ').trim() : '',
           has_sure: !!sure, has_cancel: !!cancle};
if (mode === 'check') return out;
var target = (mode === 'confirm') ? sure : cancle;
if (!target) { out.clicked = false; return out; }
try { target.click(); out.clicked = true; }
catch (e) { out.clicked = false; out.error = String(e); }
return out;
"""

# 上传后输入框上方会出现待发送图片的预览。往上找 5 层再找大图，
# 小图（表情、头像）不算 —— 这里要的就是「一张真的图片已经挂上去了」。
CHAT_IMAGE_PREVIEW_JS = r"""
var editor = arguments[0];
if (!editor) return {ok: false, found: 0};
var root = editor, depth = 0;
while (root.parentElement && depth < 5) { root = root.parentElement; depth++; }
var imgs = root.querySelectorAll('img'), found = 0;
for (var i = 0; i < imgs.length; i++) {
  var img = imgs[i], rect = img.getBoundingClientRect();
  var small = (rect.width > 0 && rect.width < 60) || (rect.height > 0 && rect.height < 60);
  var big = (rect.width >= 60 && rect.height >= 60) ||
            (img.naturalWidth >= 80 && img.naturalHeight >= 80);
  if (big && !small) found++;
}
return {ok: found > 0, found: found};
"""

# 图片气泡探测：结构（列表选择、最外层气泡、左右方向、转圈/重试标记）与文字版完全一致，
# 只把「气泡里有目标文字」换成「气泡里有大图」。两套逻辑刻意保持同构，
# 这样抖音改版式时两边一起改，不会出现只有一边失效的情况。
CHAT_IMAGE_PROBE_JS = r"""
var MODE = arguments[0], TAG = 'data-spark-img-seen';
var SELECTORS = ['.messageMessageListlist', '[class*="messageMessageListlist"]',
                 '[class*="messageMessageListwrapper"]', '[class*="MessageList"]'];
var list = null;
for (var i = 0; i < SELECTORS.length; i++) {
  var candidate = document.querySelector(SELECTORS[i]);
  if (candidate) { list = candidate; break; }
}
if (!list) return {list: false, total: 0, matches: 0, untagged: 0, state: 'none'};
var raw = list.querySelectorAll('[class*="messageBox"], [class*="MessageBox"], [class*="msgBox"]');
var items = [];
for (var i = 0; i < raw.length; i++) {
  var parent = raw[i].parentElement, nested = false;
  while (parent && parent !== list) {
    var cls = (parent.getAttribute && parent.getAttribute('class')) || '';
    if (cls.indexOf('messageBox') >= 0 || cls.indexOf('MessageBox') >= 0 || cls.indexOf('msgBox') >= 0) {
      nested = true; break;
    }
    parent = parent.parentElement;
  }
  if (!nested) items.push(raw[i]);
}
// 表情贴纸、头像、状态图标都很小，只有渲染出来或原始尺寸足够大的才算「一张图片」。
// 但**头像必须排除**：真机取证（2026-09-28 probe/probe2）每条气泡里都有 36x36 渲染、
// naturalWidth/Height=168 的头像，只按尺寸判会让「图片气泡」匹配到列表里的每一条消息
// （实测 16 条里 13 条被算成图片），新图也就永远挑不出「新」的那一条。
// 注意头像容器 class 是 commonIMAvataravatarContainer，而图片气泡自己带
// messageMessageBoxhideAvatar —— 所以这里**不能**用 /avatar/i（会把 hideAvatar 一起误伤）。
function isAvatarImage(el, node) {
  var cur = node;
  while (cur && cur !== el) {
    var cls = (cur.getAttribute && cur.getAttribute('class')) || '';
    if (/commonIMAvatar|avatarContainer/i.test(cls)) return true;
    cur = cur.parentElement;
  }
  return false;
}
function hasPhoto(el) {
  var imgs = el.querySelectorAll('img');
  for (var i = 0; i < imgs.length; i++) {
    var img = imgs[i];
    if (isAvatarImage(el, img)) continue;
    var rect = img.getBoundingClientRect();
    if (rect.width >= 80 && rect.height >= 80) return true;
    if (img.naturalWidth >= 80 && img.naturalHeight >= 80) return true;
  }
  var canvases = el.querySelectorAll('canvas');
  for (var j = 0; j < canvases.length; j++) {
    var cr = canvases[j].getBoundingClientRect();
    if (cr.width >= 80 && cr.height >= 80) return true;
  }
  return false;
}
if (MODE === 'tag') {
  var matches0 = 0;
  for (var i = 0; i < items.length; i++) {
    if (hasPhoto(items[i])) matches0++;
    items[i].setAttribute(TAG, '1');
  }
  return {list: true, total: items.length, matches: matches0, untagged: 0, state: 'none'};
}
var matches = 0, untagged = 0, newest = null, newestTop = null;
for (var i = 0; i < items.length; i++) {
  if (!hasPhoto(items[i])) continue;
  matches++;
  if (!items[i].hasAttribute(TAG)) {
    untagged++;
    // 与文字探针同理：列表是 column-reverse，DOM 里最后一条匹配是最**旧**的。真机就是
    // 这样取到了 03/21 的一条旧系统消息（side=other），于是新发的图永远判成未送达、
    // 每张图白等满 25 秒。改成按「视觉最靠下（top 最大）」挑最新那条。
    var top = items[i].getBoundingClientRect().top;
    if (newest === null || top > newestTop) { newest = items[i]; newestTop = top; }
  }
}
var out = {list: true, total: items.length, matches: matches, untagged: untagged, state: 'none', side: 'unknown'};
if (!newest) return out;
function bubbleSide(el) {
  var node = el, depth = 0;
  while (node && node !== list && depth < 8) {
    var cls = ((node.getAttribute && node.getAttribute('class')) || '') + ' ' +
              ((node.getAttribute && node.getAttribute('data-e2e')) || '');
    if (/self|isFromMe|fromMe|isMe|messageRight|msg-right|rightMessage|ownMessage|is-self/i.test(cls)) return 'self';
    if (/fromOther|isFromOther|messageLeft|msg-left|leftMessage|is-other/i.test(cls)) return 'other';
    node = node.parentElement;
    depth++;
  }
  // 真机取证（2026-09-28 实际 DOM）：抖音把发送方标记挂在**气泡内层**的 contentBox 上，
  // 我方的 class 是「messageMessageBoxcontentBox messageMessageBoxisFromMe」，
  // 对方只有「messageMessageBoxcontentBox」。上面那段只看自己和祖先，永远找不到它，
  // side 恒为 unknown，于是一条明明已经送达的消息被判成「未确认」。
  // 所以标记必须往下找一层。
  var inner = el.querySelector('[class*="contentBox"], [class*="ContentBox"]');
  if (inner) {
    var icls = ((inner.getAttribute && inner.getAttribute('class')) || '');
    if (/isFromMe|fromMe|isMe|ownMessage|messageRight|rightMessage/i.test(icls)) return 'self';
    return 'other';
  }
  // 兜底：气泡行往往和列表一样宽（真机 1052 vs 1064），拿整行中心点必然判不出方向。
  var rect = el.getBoundingClientRect();
  var box = list.getBoundingClientRect();
  if (!rect.width || !box.width) return 'unknown';
  var mid = rect.left + rect.width / 2;
  var center = box.left + box.width / 2;
  if (rect.width < box.width * 0.9) {
    if (mid >= center + 20) return 'self';
    if (mid <= center - 20) return 'other';
  }
  return 'unknown';
}
out.side = bubbleSide(newest);
if (/发送失败/.test(newest.innerText || '')) { out.state = 'failed'; return out; }
var FAILURE = ['[class*="SendStatusretry"]', '[class*="sendFailed"]', '[class*="SendFailed"]',
               '[aria-label*="重试"]', '[title*="重试"]'];
var PENDING = ['[class*="spin"]', '[class*="Spin"]', '[data-icon="spin"]', '[class*="sending"]'];
function hit(scope, selectors) {
  for (var i = 0; i < selectors.length; i++) {
    try { if (scope.querySelector(selectors[i])) return true; } catch (error) {}
  }
  return false;
}
if (hit(newest, FAILURE)) { out.state = 'failed'; return out; }
if (hit(newest, PENDING)) { out.state = 'pending'; return out; }
if (out.side === 'self') out.state = 'clean';
out.untagged = untagged;
out.matches = matches;
return out;
"""


def _chat_image_probe(mode='check'):
    """读取消息列表里的图片气泡状态（mode='tag' 打标记 / mode='check' 快照）。"""
    try:
        result = driver.execute_script(CHAT_IMAGE_PROBE_JS, mode)
    except Exception:
        return {}
    return result if isinstance(result, dict) else {}


def _find_file_input():
    """找聊天输入区的上传控件；找不到先把图片按钮点出来再找一次。"""
    for xpath in CHAT_FILE_INPUT_XPATHS:
        try:
            elements = driver.find_elements(By.XPATH, xpath)
        except Exception:
            continue
        for element in elements:
            return element
    for xpath in CHAT_IMAGE_BUTTON_XPATHS:
        try:
            buttons = driver.find_elements(By.XPATH, xpath)
        except Exception:
            continue
        for button in buttons:
            try:
                if not button.is_displayed():
                    continue
                driver.execute_script('arguments[0].scrollIntoView({block:"center"});', button)
                button.click()
            except Exception:
                continue
            time.sleep(0.6)
            for file_xpath in CHAT_FILE_INPUT_XPATHS:
                try:
                    found = driver.find_elements(By.XPATH, file_xpath)
                except Exception:
                    continue
                if found:
                    return found[0]
    return None


def _upload_image(editor, path):
    """把本地图片塞进聊天输入区，返回 (是否成功, 失败原因)。

    这里不模拟「点图片按钮 → 系统文件选择框」那条路：原生文件选择框是操作系统窗口，
    Selenium 点不到。直接对 file input 送路径是唯一稳定的做法。
    """
    if not path or not os.path.isfile(path):
        return False, '图片文件不存在：%s' % path
    element = _find_file_input()
    if element is None:
        return False, '没找到聊天输入区的图片上传控件'
    revealed = False
    try:
        revealed = bool(driver.execute_script(CHAT_REVEAL_FILE_INPUT_JS, element))
    except Exception:
        revealed = False
    try:
        element.send_keys(path)
    except Exception as exc:
        return False, '上传图片失败：%s' % exc
    finally:
        if revealed:
            try:
                driver.execute_script(CHAT_RESTORE_FILE_INPUT_JS, element)
            except Exception:
                pass
    return True, None


def _wait_image_preview(editor, timeout=IMAGE_UPLOAD_TIMEOUT):
    """等输入框里出现待发送图片的预览（= 图片真的挂上去了，可以回车了）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = driver.execute_script(CHAT_IMAGE_PREVIEW_JS, editor)
        except Exception:
            result = None
        if isinstance(result, dict) and result.get('ok'):
            return True
        time.sleep(0.3)
    return False


def _send_file_modal(mode='check'):
    """读/操作抖音的「确认发送文件」浮层。

    mode='check'   只读，返回 {'present': bool, 'title': 发送给谁, ...}
    mode='confirm' 点它的「发送」
    mode='cancel'  点它的「取消」（清场用，避免模态层留在页面上挡输入框）

    浮层不存在时一律返回 {'present': False}，调用方不必区分「读失败」和「没浮层」。
    """
    try:
        return driver.execute_script(CHAT_SEND_FILE_MODAL_JS, mode) or {'present': False}
    except Exception as exc:
        return {'present': False, 'error': str(exc)}


def _dismiss_send_file_modal(name, reason):
    """浮层还开着就点掉，返回是否真的点过。

    失败路径上必须调用：只要这个模态层留着，调用方退化发文字时输入框必然写不进去。
    """
    if not _send_file_modal('check').get('present'):
        return False
    _send_file_modal('cancel')
    log_event('warn', '发图片', '给「%s」%s，已取消清场' % (name, reason))
    return True


FAILURE_SHOT_KEEP_DAYS = _env_int('SPARK_SHOT_KEEP_DAYS', 7)
FAILURE_SHOT_MAX_FILES = _env_int('SPARK_SHOT_MAX_FILES', 200)


def _prune_failure_shots():
    """清理过期失败截图。

    每次发送失败/未确认都会存一张整页截图（几百 KB 到 1 MB），旧实现只写不删，
    长期挂机迟早把磁盘吃满。这里按「保留 7 天 + 最多 200 张」双重上限清理。
    规则和图片下载目录一样，所以共用 _prune_dir。
    """
    return _prune_dir(FAILURE_SHOT_DIR, FAILURE_SHOT_KEEP_DAYS, FAILURE_SHOT_MAX_FILES)


def _save_failure_shot(label):
    """失败现场截图，返回相对路径（截图失败不影响主流程）。"""
    try:
        os.makedirs(FAILURE_SHOT_DIR, exist_ok=True)
        safe = re.sub(r'[^0-9A-Za-z_-]', '', label or 'shot') or 'shot'
        # 文件名带上微秒：同一秒内连续失败两次时不会互相覆盖
        filename = '%s-%s.png' % (datetime.now().strftime('%Y%m%d-%H%M%S-%f'), safe)
        driver.save_screenshot(os.path.join(FAILURE_SHOT_DIR, filename))
        _prune_failure_shots()
        return 'logs/shots/%s' % filename
    except Exception:
        return None


def _confirm_message_delivered(text, before_matches, timeout=SEND_CONFIRM_TIMEOUT, probe_fn=None):
    """发送后确认这条消息真的出现在消息列表里，并等到终态。

    返回 (结果, 诊断信息)：
      'success'     已出现且状态正常
      'failed'      页面提示发送失败（重试标记 / 「发送失败」文案）
      'unconfirmed' 列表里没出现新消息，或状态一直不确定（转圈没结束）
      'nolist'      读不到消息列表，无法判断（由调用方兜底）
    """
    started = time.time()
    deadline = started + timeout
    clean_since = None
    saw_pending = False
    last = {}
    # 文字消息和图片消息的「新气泡」判据不一样（一个是气泡文本，一个是气泡里有大图），
    # 但等待、稳定、超时的时序逻辑完全一样，所以这里把「怎么看一眼」抽成参数：
    # 文字走默认的 _chat_probe(text,'check')，图片由调用方传入自己的探测函数。
    probe_once = probe_fn or (lambda: _chat_probe(text, 'check'))
    while time.time() < deadline:
        probe = probe_once()
        last = probe
        if not probe:
            # 连脚本都执行不了（页面已跳走/元素被销毁），等同于读不到列表
            return 'nolist', {'probe': {}, 'elapsed': round(time.time() - started, 1)}
        if not probe.get('list'):
            return 'nolist', {'probe': probe, 'elapsed': round(time.time() - started, 1)}
        is_new = (probe.get('untagged', 0) >= 1
                  and probe.get('matches', 0) > (before_matches or 0))
        # 方向判不出来，或明显是对方的气泡，都不能当成「我们发出去了」。
        if is_new and probe.get('side') != 'self':
            is_new = False
        state = probe.get('state') or 'none'
        if is_new:
            if state == 'failed':
                return 'failed', {'probe': probe, 'elapsed': round(time.time() - started, 1)}
            if state == 'pending':
                saw_pending = True
                clean_since = None
            elif state == 'clean':
                now = time.time()
                if saw_pending:
                    # 转圈刚消失：重试标记可能晚一拍才挂上，先稳定一段再复查
                    time.sleep(SEND_STABLE_INTERVAL)
                    again = probe_once()
                    if again.get('state') == 'failed':
                        return 'failed', {'probe': again, 'elapsed': round(time.time() - started, 1)}
                    if again.get('state') != 'pending':
                        return 'success', {'probe': again, 'elapsed': round(time.time() - started, 1)}
                    clean_since = None
                else:
                    if clean_since is None:
                        clean_since = now
                    elif now - clean_since >= SEND_INITIAL_CLEAN_GRACE:
                        return 'success', {'probe': probe, 'elapsed': round(time.time() - started, 1)}
        time.sleep(SEND_POLL_INTERVAL)
    return 'unconfirmed', {'probe': last, 'elapsed': round(time.time() - started, 1),
                           'saw_pending': saw_pending}


# 判断「当前真正打开的会话」是不是目标好友。这是防止把消息发错人的安全阀，
# 所以宁可判否也不能误判成「已打开」。
# 实测聊天头部是 RightPanelHeadertitle（昵称在 x=366，会话列表只有 302px 宽），
# 所以不能用「在视口右半边」这种猜测；判定顺序：
#   1) 头部标题非空 => 以头部为准（不是这个名字就直接判否）
#   2) 头部缺失/为空（版式不同）=> 必须「输入框已渲染」且「昵称出现在会话列表之外」
# 注意必须是 raw 字符串：里面的 JS 正则 \s 在普通字符串里是「无效转义序列」，
# Python 3.12 会报 SyntaxWarning，未来的版本会直接变成 SyntaxError。
CHAT_OPEN_NAME_JS = r"""
var name = arguments[0];
var expect = (name || '').replace(/\s+/g, ' ').trim();
// 标题必须取「类名正好是 RightPanelHeadertitle」的那个元素。
// 以前用 [class*="RightPanelHeadertitle"]，querySelector 会先命中外层容器
// RightPanelHeadertitleContainer —— 它的 innerText 是「昵称 火花数」（实测「MK空白 699」），
// 而这里是严格相等比较，于是会话明明已经打开也会被判成「没打开」，然后在这里直接
// return false 把后面的兜底扫描也短路掉。实测取证：真实页面上 head.title === 'MK空白'
// 而 head.container.innerText === 'MK空白 699'。带火花的会话因此全部无法确认。
var exacts = document.querySelectorAll('[class~="RightPanelHeadertitle"]');
if (exacts.length) {
  for (var e = 0; e < exacts.length; e++) {
    var et = (exacts[e].innerText || '').replace(/\s+/g, ' ').trim();
    if (!et) continue;
    if (et === expect) return true;
    // 只容忍尾巴上「空格 + 火花数」这种附加内容（要求有空白分隔，避免把
    // 昵称自带的数字，比如「小明123」，误当成火花数抹掉而认错人）。
    if (et.replace(/\s+\d+$/, '').trim() === expect) return true;
  }
  return false;
}
var head = document.querySelector('[class*="RightPanelHeadertitle"]');
if (head) {
  var t = (head.innerText || '').replace(/\s+/g, ' ').trim();
  if (t) {
    if (t === expect) return true;
    if (t.replace(/\s+\d+$/, '').trim() === expect) return true;
    return false;
  }
}
var editor = document.querySelector('[data-slate-editor="true"], [class*="messageEditor"] [contenteditable="true"]');
if (!editor) return false;
var skip = document.querySelectorAll('.conversationConversationListwrapper, [data-e2e="im-list"], [data-e2e="conversation-item"]');
var nodes = document.querySelectorAll('div, span, p, h1, h2');
for (var i = 0; i < nodes.length; i++) {
  var el = nodes[i];
  if ((el.innerText || '').trim() !== name) continue;
  var inList = false;
  for (var j = 0; j < skip.length; j++) {
    if (skip[j].contains(el)) { inList = true; break; }
  }
  if (inList) continue;
  var r = el.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) continue;
  return true;
}
return false;
"""

# contenteditable 用 send_keys 常被 React 编辑器忽略：execCommand('insertText')
# 等价于真实输入，会触发 beforeinput/input，是开源自动化项目里的通用做法。
# 已在本机真实 Slate 编辑器上验证可写入。
CHAT_INSERT_JS = """
var el = arguments[0], v = arguments[1];
if (!el) return false;
try { el.focus(); } catch (e) {}
try {
  var sel = window.getSelection();
  var range = document.createRange();
  range.selectNodeContents(el);
  sel.removeAllRanges();
  sel.addRange(range);
} catch (e) {}
try {
  if (document.execCommand('insertText', false, v)) return true;
} catch (e) {}
try {
  el.dispatchEvent(new InputEvent('beforeinput', {bubbles: true, cancelable: true,
                                                  inputType: 'insertText', data: v}));
  el.textContent = v;
  el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: v}));
  return true;
} catch (e) {}
return false;
"""

CHAT_ENTER_JS = """
var el = arguments[0];
if (!el) return false;
try { el.focus(); } catch (e) {}
['keydown', 'keypress', 'keyup'].forEach(function (type) {
  el.dispatchEvent(new KeyboardEvent(type, {bubbles: true, cancelable: true,
                                            key: 'Enter', code: 'Enter', keyCode: 13, which: 13}));
});
return true;
"""


def _open_chat_is(name):
    """当前打开的会话对象是不是 name（昵称需要出现在页面右半区）。"""
    try:
        return bool(driver.execute_script(CHAT_OPEN_NAME_JS, name))
    except Exception:
        return False


def _open_conversation(name, row_xpath, timeout=8):
    """点开会话，并确认右边打开的确实是 name。返回 (是否成功, 失败原因)。

    实测（2026-09-14 真实页面）会话行上只挂了 React onMouseDown：
      <div class="conversationConversationItemwrapper" data-e2e="conversation-item">
        事件 = ['onMouseDown', 'onContextMenu']，没有 onClick
    Selenium 的真实点击和 CDP 派发的 trusted 鼠标事件都点不开（实测 12 秒无反应），
    只有直接调用 props.onMouseDown 有效（实测 1 秒内打开）。三种方式都留作兜底。
    """
    try:
        row = driver.find_element(By.XPATH, value=row_xpath)
    except Exception as exc:
        return False, '找不到会话行: %s' % exc
    if _open_chat_is(name):
        return True, None
    candidates = [
        ('react-mousedown', lambda: _invoke_react_handler(row, 'onMouseDown'), 8),
        ('selenium-click', row.click, 3),
        ('dom-click', lambda: driver.execute_script('arguments[0].click();', row), 2),
    ]
    tried = []
    for label, action, wait in candidates:
        try:
            action()
        except Exception:
            continue
        tried.append(label)
        if _wait_until(lambda: _open_chat_is(name), wait):
            return True, None
    return False, '点击后没能确认已打开与「%s」的会话（已尝试：%s）' % (name, '/'.join(tried) or '无')


# 新版抖音聊天框是 Ace 系编辑器（每行一个 div.ace-line），**每一行末尾都会多一个零宽空格**。
# 于是「写进去了」的严格子串比较（'甲\n乙' in '甲\u200b\n乙'）必然为假：代码以为没写进去，
# 再换第二种方式写一遍，内容越写越乱，最后报「消息内容没有写进输入框」。
# 比较前统一抹掉空白与零宽字符。
_EDITOR_NOISE_RE = re.compile(r'[\s\u00a0\u200b\u200c\u200d\ufeff]+')


def _editor_flatten(text):
    """拉平编辑器文本，只用于比较（抹掉换行、空格、零宽字符）。"""
    return _EDITOR_NOISE_RE.sub('', text or '')


# 编辑器异步落 DOM：写完立刻读 innerText 可能还是旧内容（真机上连读 4 秒都是空），
# 所以每次写入之后要等结果落定再判成败。机器慢可以调大 SPARK_EDITOR_SETTLE_MS。
try:
    _EDITOR_SETTLE_SECONDS = max(0.5, float(os.getenv('SPARK_EDITOR_SETTLE_MS', '3000')) / 1000.0)
except (TypeError, ValueError):
    _EDITOR_SETTLE_SECONDS = 3.0


def _editor_contains(editor, text):
    """输入框里是否已经有这段内容（拉平后包含，不是严格子串）。"""
    target = _editor_flatten(text)
    if not target:
        return True
    current = _chat_editor_text(editor)
    return bool(current) and target in _editor_flatten(current)


def _wait_editor_contains(editor, text, timeout=None):
    """等编辑器把内容落定后再判断，避免把「还没渲染出来」当成「没写进去」。"""
    if timeout is None:
        timeout = _EDITOR_SETTLE_SECONDS
    deadline = time.time() + max(float(timeout), 0.3)
    while True:
        try:
            if _editor_contains(editor, text):
                return True
        except Exception:
            pass
        if time.time() >= deadline:
            return False
        time.sleep(0.1)


def _cdp_insert_text(editor, text):
    """用 CDP 的 Input.insertText 往输入框写字。

    2026-09-28 真机定案（%TEMP%\\spark-edit\\methods6.json，在刚重载出来的干净编辑器上）：
    只有这条路能把 4 行签文一字不差地写进去；同一个编辑器上
    `document.execCommand('insertText')` 会返回 true 却一个字都不写。
    Input.insertText 只作用于获得焦点的可编辑元素，所以必须先 focus。
    """
    driver.execute_script('arguments[0].focus();', editor)
    driver.execute_cdp_cmd('Input.insertText', {'text': text})


def _insert_editor_dom(editor, text):
    """最兜底的一招：textarea 走原生 value setter，其余非 Slate 元素写 textContent。

    Slate / Ace 这类编辑器由自己的内部模型管理 DOM，直接改 textContent 会把它写坏
    （真机上就是这样把内容搅乱的），所以对这种元素直接放弃。
    """
    driver.execute_script(
        "var el=arguments[0],v=arguments[1];"
        "var tag=(el.tagName||'').toLowerCase();"
        "if(tag==='textarea'){"
        "  var d=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value');"
        "  if(d&&d.set){d.set.call(el,v);}"
        "}else if(!el.getAttribute('data-slate-editor')){"
        "  el.textContent=v;"
        "}else{return false;}"
        "el.dispatchEvent(new Event('input',{bubbles:true}));"
        "el.dispatchEvent(new Event('change',{bubbles:true}));",
        editor, text)


def _type_into_editor(editor, text):
    """把内容写进聊天输入框（contenteditable / textarea 都能用）。

    顺序按真机实测的可靠度排：CDP Input.insertText → 老的 execCommand → DOM 兜底。
    故意**不用** editor.send_keys(text)：send_keys 碰到 '\\n' 会真按一次回车，
    而这条消息是在下一行才用回车提交的 —— 那会把半条消息提前发出去。
    """
    target = (text or '').strip()
    if not target:
        return True
    if _editor_contains(editor, target):
        return True                       # 已经在了，绝不重复写
    attempts = (
        ('cdp-input', lambda: _cdp_insert_text(editor, target)),
        ('execCommand', lambda: driver.execute_script(CHAT_INSERT_JS, editor, target)),
        ('dom-fallback', lambda: _insert_editor_dom(editor, target)),
    )
    for _label, action in attempts:
        try:
            action()
        except Exception:
            continue
        if _wait_editor_contains(editor, target):
            return True
    return False


_FRIEND_KEY_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,96}$')

FRIEND_LIST_JS = r"""
function hashKey(text) {
  var h = 0;
  var value = String(text || '');
  for (var i = 0; i < value.length; i++) h = ((h << 5) - h + value.charCodeAt(i)) | 0;
  return 'k' + (h >>> 0).toString(16);
}
var rows = document.querySelectorAll('[data-e2e="conversation-item"], .conversationConversationItemwrapper');
var out = [];
for (var i = 0; i < rows.length; i++) {
  var row = rows[i];
  // 真机 DOM（2026-09-28 在真实会话页实测 49 条会话，结构如下）：
  //   <div class="conversationConversationItemtitleWrapper">
  //     <div class="conversationConversationItemtitle">昵称</div>
  //     <div class="ConversationItemTagNextToTitlewrapper">
  //       <div class="ConversationItemTagNextToTitleleft"></div>             <- 火花徽章（有火花时才有文字）
  //       <div class="ConversationItemTagNextToTitleleft">
  //         <div class="ConversationItemTagNextToTitletimeStr">03/17</div>   <- 最后一条消息的时间
  //       </div>
  //     </div>
  //   </div>
  // 昵称只能取最里层那个 Itemtitle。以前用的 [class*="title"] 会命中外层 Wrapper
  // （它的 class 里同样含 "title" 子串，而且文档顺序更靠前），于是昵称被拼成
  // 「昵称 + 火花徽章 + 时间」—— 用户看到的「火花数量显示在昵称里」就是这个。
  // 而且那个时间是「最后一条消息的日期」，天天在变，连任务/好友匹配都会跟着失效。
  var name = '';
  var nameEl = row.querySelector('[class*="Itemtitle"]:not([class*="Wrapper"])')
            || row.querySelector('[class*="ConversationName"]')
            || row.querySelector('[class*="conversationName"]');
  if (nameEl) name = (nameEl.innerText || '').replace(/\s+/g, ' ').trim();
  if (!name) {
    var lines = (row.innerText || '').split('\n').map(function (part) { return part.trim(); }).filter(Boolean);
    name = (lines[0] || '').replace(/\s+/g, ' ').trim();
  }
  if (!name) continue;
  var img = row.querySelector('img');
  var avatar = img ? (img.getAttribute('src') || '') : '';
  // 火花徽章和时间挤在同一个「昵称旁边的 tag 容器」里：容器文字减掉时间文字，
  // 剩下的才是火花（没有火花的会话里那个 left 是空 div，这里自然得到空串）。
  var fire = '';
  var timeEl = row.querySelector('[class*="timeStr"]');
  var timeText = timeEl ? (timeEl.innerText || '').replace(/\s+/g, ' ').trim() : '';
  var tagEl = row.querySelector('[class*="TagNextToTitle"]');
  if (tagEl) {
    var tagText = (tagEl.innerText || '').replace(/\s+/g, ' ').trim();
    if (timeText) tagText = tagText.split(timeText).join(' ').replace(/\s+/g, ' ').trim();
    fire = tagText;
  }
  if (!fire) {
    var fireEl = row.querySelector('[class*="fire"], [class*="Fire"], [class*="spark"]');
    if (fireEl) fire = (fireEl.innerText || '').replace(/\s+/g, ' ').trim();
  }
  var rawId = row.getAttribute('data-id') || row.getAttribute('data-conversation-id') || row.id || '';
  var key = hashKey((rawId || (name + '|' + avatar)) + '#' + i);
  row.setAttribute('data-spark-key', key);
  out.push({name: name, key: key, avatar: avatar, fire: fire});
}
return out;
"""

FRIEND_SCROLL_JS = r"""
var box = document.querySelector('[data-e2e="im-list"]')
  || document.querySelector('.conversationConversationListwrapper');
if (!box) return false;
var scroller = box;
while (scroller && scroller !== document.body) {
  if (scroller.scrollHeight > scroller.clientHeight + 20) break;
  scroller = scroller.parentElement;
}
if (!scroller) return false;
var mode = arguments[0] || 'down';
if (mode === 'top') {
  scroller.scrollTop = 0;
  return true;
}
var step = Math.max(scroller.clientHeight * 0.8, 240);
var next = Math.min(scroller.scrollTop + step, scroller.scrollHeight);
if (next <= scroller.scrollTop + 1) return false;
scroller.scrollTop = next;
return true;
"""


def _friend_xpath(key):
    if not _FRIEND_KEY_RE.match(str(key or '')):
        return None
    return '//*[@data-spark-key="%s"]' % key


def group_friend_rows(rows):
    """按昵称归并会话行。重名记入 ambiguous，不再用后者覆盖前者。"""
    counts = {}
    cleaned = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = ' '.join(str(row.get('name') or '').split())
        key = str(row.get('key') or '')
        if not name or not _FRIEND_KEY_RE.match(key):
            continue
        cleaned.append({
            'name': name,
            'key': key,
            'avatar': row.get('avatar') or '',
            'fire': row.get('fire') or '',
        })
        counts[name] = counts.get(name, 0) + 1
    unique = {}
    ambiguous = set()
    for item in cleaned:
        if counts[item['name']] > 1:
            ambiguous.add(item['name'])
        else:
            unique[item['name']] = item
    return unique, ambiguous, cleaned



class Douyin:
    def __init__(self, driver):
        self.driver = driver  # 将 driver 作为实例属性
        # 会话名 -> 行 XPATH。旧实现把它写成类属性（所有实例共享）且从不清空，
        # 而会话顺序会随新消息重排，残留的旧 XPATH 可能指向另一个会话。
        self.friends_xpath_list = {}
        self.friend_ambiguous = set()
        self.friend_catalog = []
        # 最近一次发送的明细（确认方式 / 耗时 / 图片是否送出）。先给个空值：
        # 图片失败退化成文字时要往里补一句「图片未送达」，若此刻属性还不存在就会崩。
        self.last_send_detail = {}

    def _read_visible_friends(self):
        try:
            raw = driver.execute_script(FRIEND_LIST_JS)
        except Exception:
            return []
        return raw if isinstance(raw, list) else []

    def _remember_friends(self, collected, visible):
        unique, ambiguous, cleaned = group_friend_rows(collected)
        visible_unique, _visible_ambiguous, _visible_rows = group_friend_rows(visible)
        self.friend_ambiguous = set(ambiguous)
        self.friend_catalog = cleaned
        self.friends_xpath_list = {}
        for name, item in visible_unique.items():
            if name in ambiguous:
                continue
            xpath = _friend_xpath(item['key'])
            if xpath:
                self.friends_xpath_list[name] = xpath
        temp = []
        emitted = set()
        for item in cleaned:
            if item['name'] in emitted:
                continue
            emitted.add(item['name'])
            temp.append(UserFriendsInfo(
                item['name'], item['avatar'], item['fire'], ambiguous=item['name'] in ambiguous))
        return temp

    def known_names(self):
        """这次读到的全部会话名（按出现顺序去重）。

        friend_catalog 是滚动过程中见过的所有会话（本机两百多条），
        friends_xpath_list 只是「当前画面」里那几十行 —— 报「共读到 N 个」时必须用前者，
        否则会报出一个比用户实际会话数小得多的数字，看着像读错了。
        """
        names = []
        for item in self.friend_catalog or []:
            item_name = item.get('name') if isinstance(item, dict) else None
            if item_name and item_name not in names:
                names.append(item_name)
        if not names:
            names = list((self.friends_xpath_list or {}).keys())
        return names

    def locate_friend(self, name):
        """定位会话行，返回可点击的 xpath；找不到返回 None。

        会话列表是虚拟滚动：Updara_FrinderList 只给当前画面里的行建索引，
        滚过去的那批会话只有名字、没有位置。所以索引里没有就从顶部滚下去找一遍
        （_scroll_until_friend）—— 发消息一直这么做，预检漏了这一步，
        于是出现「好友列表里明明有、预检却说会话列表里没有」。
        """
        if not name:
            return None
        row_xpath = (self.friends_xpath_list or {}).get(name)
        if row_xpath is None and hasattr(self, '_scroll_until_friend'):
            self._scroll_until_friend(name)
            row_xpath = (self.friends_xpath_list or {}).get(name)
        return row_xpath

    def _scroll_until_friend(self, name, max_steps=60):
        """把目标会话滚进当前画面再定位。虚拟列表滚出视口后，旧位置选择器会失效。

        以前固定只滚 8 步就放弃，而本机的会话列表有两百多条、一屏只挂几十行，
        8 步到不了底 —— 于是大量「好友列表里看得到、发送/预检却说没有」。
        现在一直滚到「找到」或「滚不动（到底了）」为止，用 max_steps 兜住时间上限。
        """
        if not name:
            return
        try:
            driver.execute_script(FRIEND_SCROLL_JS, 'top')
        except Exception:
            return
        time.sleep(0.2)
        for _ in range(max_steps):
            batch = self._read_visible_friends()
            unique, ambiguous, _cleaned = group_friend_rows(batch)
            if name in ambiguous:
                self.friend_ambiguous.add(name)
                self.friends_xpath_list.pop(name, None)
                return
            if name in unique:
                xpath = _friend_xpath(unique[name]['key'])
                if xpath:
                    self.friends_xpath_list[name] = xpath
                return
            try:
                moved = driver.execute_script(FRIEND_SCROLL_JS, 'down')
            except Exception:
                return
            if not moved:
                return
            time.sleep(0.25)

    def PrintfFrinder(self):

        print(f'\n⏭️ 好友列表 共获取{len(self.friends_xpath_list)}位:\n------------------')
        for index, value in self.friends_xpath_list.items():
            print(index)
        print('------------------')

    def Updara_FrinderList(self):
        """读取会话列表。

        昵称只做显示。滚出来的列表里一旦重名，就记成 ambiguous，
        不再用后一个覆盖前一个。点击只用当前画面上刚打的 data-spark-key。
        """
        collected = []
        seen = set()

        def absorb(batch):
            added = 0
            for row in batch or []:
                key = str((row or {}).get('key') or '') if isinstance(row, dict) else ''
                if not key or key in seen:
                    continue
                seen.add(key)
                collected.append(row)
                added += 1
            return added

        try:
            driver.execute_script(FRIEND_SCROLL_JS, 'top')
        except Exception:
            pass
        time.sleep(0.15)
        absorb(self._read_visible_friends())
        stagnant = 0
        for _ in range(6):
            try:
                moved = driver.execute_script(FRIEND_SCROLL_JS, 'down')
            except Exception:
                break
            if not moved:
                break
            time.sleep(0.25)
            added = absorb(self._read_visible_friends())
            if added == 0:
                stagnant += 1
                if stagnant >= 2:
                    break
            else:
                stagnant = 0
        visible = self._read_visible_friends()
        absorb(visible)
        return self._remember_friends(collected, visible)

    def _send_image_in_chat(self, editor, name, path, paced=False):
        """在当前打开的会话里发一张本地图片（图片排在文字前面）。

        图片和文字是两条消息，所以图片单独做一次送达确认：判据是
        「消息列表出现新的、带大图的气泡，且没有重试标记」（见 CHAT_IMAGE_PROBE_JS）。
        返回 TrueString；is_bool=False 表示图片没有确认送达，调用方自己决定是否
        退化成纯文字（定时任务会退化，手动发送会如实报错）。
        """
        ok, reason = _upload_image(editor, path)
        if not ok:
            self.last_send_detail = {
                '确认方式': '图片没有进入输入框',
                '图片': os.path.basename(path or ''),
            }
            log_event('error', '发图片', '给「%s」上传图片失败：%s' % (name, reason))
            return TrueString(False, '%s。这张图片没有发送' % reason, status='failed')

        # 给现存气泡打标记必须在图片真的发出去之前，否则新气泡会被算进 before。
        before = _chat_image_probe('tag')
        before_matches = before.get('matches', 0)

        # 上传后有两种界面，必须先分清再动手：
        #   A) 抖音弹出「确认发送文件」浮层（当前版本的行为）—— 在它上面按回车毫无作用，
        #      必须先点浮层里的「发送」，否则图片永远发不出去，浮层还会挡住后面的文字输入；
        #   B) 图片直接挂在输入区（老行为）—— 回车发送。
        modal = _send_file_modal('check')
        if not modal.get('present'):
            # 没等到预览不中止：部分版式下预览不在这个 DOM 子树里，判不到不等于图片没挂上。
            # 真正的判据是发送后消息列表里有没有出现新的图片气泡。
            if not _wait_image_preview(editor):
                log_event('warn', '发图片', '给「%s」上传图片后没等到预览，继续尝试发送' % name)
            # 预览等完之后再看一眼浮层：它是模态层，只要它还在，回车就是白按。
            modal = _send_file_modal('check')

        modal_note = None
        if modal.get('present'):
            if not _send_file_modal('confirm').get('clicked'):
                _send_file_modal('cancel')
                log_event('error', '发图片',
                          '给「%s」遇到抖音的「确认发送文件」浮层，但点不到它的「发送」按钮' % name)
                self.last_send_detail = {
                    '确认方式': '卡在抖音的「确认发送文件」浮层',
                    '图片': os.path.basename(path or ''),
                    '浮层': modal.get('title', ''),
                }
                return TrueString(False,
                                  '图片没有发送：抖音弹出了「确认发送文件」浮层，但没能点到它的'
                                  '「发送」按钮。这张图片没有发送，请在抖音里手动确认后重试',
                                  status='failed')
            modal_note = modal.get('title') or '发送文件'
            log_event('info', '发图片',
                      '给「%s」已点掉抖音的「确认发送文件」浮层：%s' % (name, modal_note))
        else:
            if paced:
                human_pause(0.2, 0.8)
            try:
                editor.send_keys(Keys.ENTER)
            except Exception:
                pass
        result, detail = _confirm_message_delivered(
            None, before_matches, timeout=IMAGE_CONFIRM_TIMEOUT,
            probe_fn=lambda: _chat_image_probe('check'))
        # 兜底：浮层如果还开着（点了发送但抖音没关它，或者走了别的分支），先取消再往下走 ——
        # 只要它留着，调用方退化发文字时输入框必然写不进去。
        _dismiss_send_file_modal(name, '发送图片后「确认发送文件」浮层仍未关闭')

        image_name = os.path.basename(path or '')
        if result == 'success':
            self.last_send_detail = {
                '耗时': '%.1f 秒' % detail.get('elapsed', 0),
                '确认方式': '消息列表出现新的图片气泡且未见重试标记',
                '图片': image_name,
                '列表气泡数': detail.get('probe', {}).get('total'),
            }
            if modal_note:
                self.last_send_detail['确认步骤'] = modal_note
            return TrueString(True, None)

        # 这里**不**像文字那样换触发方式重试：图片一旦挂在输入区，
        # 再按一次回车就有把同一张图发两遍的风险，而重复发图比晚一点发更难解释。
        shot = _save_failure_shot(
            'send-image-failed' if result == 'failed' else 'send-image-unconfirmed')
        if shot:
            detail['screenshot'] = shot
        if result == 'failed':
            log_event('error', '发图片', '给「%s」发送图片失败（页面提示可重试）' % name, detail)
            message = ('抖音提示这张图片发送失败（消息旁有重试标记）：对方没有收到这张图。'
                       '请在抖音里手动重试，或稍后再试一次')
            status = 'failed'
        elif result == 'nolist':
            log_event('error', '发图片', '给「%s」发送图片后读不到消息列表，无法确认' % name, detail)
            message = ('图片发送状态未确认：无法读取消息列表，未能确认这张图是否送达。'
                       '为避免重复发送，本次不会自动重试')
            status = 'unknown'
        else:
            log_event('error', '发图片', '给「%s」发送图片状态未确认' % name, detail)
            message = ('图片发送状态未确认：消息列表里没有出现新的图片气泡。'
                       '为避免重复发送，本次不会自动重试')
            status = 'unknown'
        return TrueString(False, message, status=status)

    def Send_Frinder(self, name: str, text: str, paced: bool = False, image=None):
        """打开与 name 的会话并发送 text（可选先发一张本地图片 image）。

        旧实现有三个坑，全部修掉：
          1. 编辑器类名写死成 messageEditorimChatEditorContainer（当前版本已不存在）；
          2. 好友名匹配不上时函数走到结尾返回 None，接口层 out.is_bool 直接 AttributeError → 500；
          3. 点开会话只 sleep 1.5s 就开始输入，输入框还没渲染出来就会失败。
        本函数任何分支都返回 TrueString，不再让接口层崩。

        返回 True 的**唯一**依据是「消息列表里确实多了个没打过标记、且没有重试标记的新气泡」
        （见 _confirm_message_delivered）。读不到消息列表时一律返回 False（未确认），
        不允许把「输入框空了」当成发送成功的证据 —— 那会让漏发被记成成功。

        图文为什么在同一次调用里发（image 先、text 后）：整个项目对用户的承诺是
        「每位好友每天恰好一条」—— 上层记账键只看（日期, 好友），一次任务只记一次账。
        如果拆成两次调用，第二条会被当天的去重直接跳过（或反之重复记账），
        所以图片和文字必须在同一次会话打开、同一次记账里完成。
        """
        if not name:
            return TrueString(False, '好友名为空', status='failed')
        if not (text or '').strip() and not image:
            return TrueString(False, '消息内容为空', status='failed')
        self.Updara_FrinderList()
        if name in (self.friend_ambiguous or set()):
            return TrueString(False, '有多位叫「%s」的会话，无法确定发给谁，这次没有发送' % name,
                              status='failed')
        if not self.friends_xpath_list and not self.friend_catalog:
            return TrueString(False, '没读到会话列表：可能未登录、会话列表未加载出来，或列表选择器已失效',
                              status='failed')
        row_xpath = self.locate_friend(name)
        if name in (self.friend_ambiguous or set()):
            return TrueString(False, '有多位叫「%s」的会话，无法确定发给谁，这次没有发送' % name,
                              status='failed')
        if row_xpath is None:
            names = self.known_names()
            return TrueString(
                False,
                '会话列表里没有「%s」（共读到 %d 个会话，例如：%s）。请在好友列表里刷新后重新选择。'
                % (name, len(names), '、'.join(names[:8])),
                status='failed')
        opened, reason = _open_conversation(name, row_xpath)
        if not opened:
            return TrueString(False, '%s。为避免把消息发错人，这次没有发送' % reason)
        editor = _wait_chat_editor(10)
        if editor is None:
            return TrueString(False, '打开会话后 10 秒内没找到消息输入框（聊天输入框的结构可能又变了）')
        # 随机节奏只在定时/批量发送时用：那是无人等待的场景，节奏自然一点更安全；
        # 手动点发送时用户就在屏幕前等，多停一秒都是白等。
        if paced:
            human_pause(0.3, 1.2)
        image_note = ''
        if image:
            image_out = self._send_image_in_chat(editor, name, image, paced=paced)
            if not image_out.is_bool:
                # 图片没确认送达时不直接放弃：定时任务里「续火花」是硬要求，
                # 退化成纯文字也比什么都不发强 —— 文字是一条独立的消息，
                # 不存在「把图片发两遍」的风险。失败原因记进发送明细并在通知里带出来。
                image_note = '未送达：%s' % image_out.string
                self.last_send_detail = dict(getattr(self, 'last_send_detail', None) or {})
                self.last_send_detail['图片'] = image_note
                if not (text or '').strip():
                    return image_out
                log_event('warn', '发图片',
                          '给「%s」发图未成功，本次退化为纯文字：%s' % (name, image_out.string))
            elif not (text or '').strip():
                return TrueString(True, None)
            # 图片发出去后输入区通常会被重新渲染，元素引用可能已经失效，重新取一次。
            editor = _wait_chat_editor(5) or editor
            if paced:
                human_pause(0.3, 1.0)
        if not _type_into_editor(editor, text):
            return TrueString(False, '消息内容没有写进输入框')

        # 发送前给消息列表里现存的气泡打标记，同时拿到「当前已含同样文本的气泡数」：
        # 之后只有「未标记的新气泡 + 匹配数变多」才可能是刚发出的这一条。
        before = _chat_probe(text, 'tag')
        before_matches = before.get('matches', 0)

        if paced:
            human_pause(0.2, 0.8)
        try:
            editor.send_keys(Keys.ENTER)
        except Exception:
            pass
        result, detail = _confirm_message_delivered(text, before_matches)
        if result == 'success':
            self.last_send_detail = {
                '耗时': '%.1f 秒' % detail.get('elapsed', 0),
                '确认方式': '消息列表出现新气泡且未见重试标记',
                '列表气泡数': detail.get('probe', {}).get('total'),
            }
            # 退化成纯文字之后这一句会覆盖上面的明细，别把「图片没送出」抹掉 ——
            # 通知和面板都靠它说清楚对方到底收到了什么。
            if image_note:
                self.last_send_detail['图片'] = image_note
            return TrueString(True, None)

        # 关键安全阀：只有「输入框里还留着内容」才说明这一下没提交出去，
        # 此时换别的触发方式不会重复发送；一旦已经提交（输入框空了）就不再补触发，
        # 否则会把同一条消息发两遍。
        if result != 'nolist' and _editor_still_has(editor, text):
            triggers = (
                ('react-onclick',
                 lambda: _invoke_react_handler(_first_displayed(CHAT_SEND_XPATHS), 'onClick')),
                ('dom-click', lambda: _try_click(CHAT_SEND_XPATHS)),
                ('enter-event', lambda: driver.execute_script(CHAT_ENTER_JS, editor)),
            )
            for label, trigger in triggers:
                try:
                    trigger()
                except Exception:
                    continue
                result, detail = _confirm_message_delivered(text, before_matches, timeout=8)
                if result == 'success':
                    return TrueString(True, None)
                if not _editor_still_has(editor, text):
                    detail['via'] = label
                    break

        if result == 'nolist':
            # 读不到消息列表 —— 这里以前会把「输入框被清空」升格成成功，那是个会静默
            # 漏发的缺陷：消息列表的选择器一旦失效（抖音改版、或发送过程中被别的请求
            # 刷新了页面），这条路径就会对着一条根本没发出去的消息回 success，
            # 上层据此写进发送记账，当天的去重于是把这个人整天跳过 ——
            # 面板显示成功、火花却断了，而且没有任何失败通知。
            #
            # 现在的口径：读不到列表就是**无法确认**，绝不判成功。
            # 上层会把这条记成 unknown（当天不重发，安全方向正确），并保留现场截图，
            # 让用户自己去抖音确认一眼。
            cleared = _wait_message_sent(editor, text, timeout=5)
            detail['editor_cleared'] = cleared
            shot = _save_failure_shot('send-unverified')
            if shot:
                detail['screenshot'] = shot
            log_event('error', '发消息',
                      '无法读取消息列表，无法确认「%s」是否已送达（按未确认处理）' % name, detail)
            self.last_send_detail = {
                '耗时': '%.1f 秒' % detail.get('elapsed', 0),
                '确认方式': '读不到消息列表，无法确认送达',
                '输入框已清空': bool(cleared),
            }
            return TrueString(
                False, '发送状态未确认：无法读取消息列表，未能确认这条消息是否送达（输入框%s）。'
                       '为避免重复发送，本次不会自动重试，请到抖音里确认一下。'
                       % ('已清空，通常说明已提交' if cleared else '仍留有内容，很可能没发出去'))

        shot = _save_failure_shot('send-failed' if result == 'failed' else 'send-unconfirmed')
        if shot:
            detail['screenshot'] = shot
        if result == 'failed':
            log_event('error', '发消息', '给「%s」发送失败（页面提示可重试）' % name, detail)
            return TrueString(False, '抖音提示这条消息发送失败（消息旁有重试标记）：对方没有收到。'
                                     '请在抖音里手动重试，或稍后再试一次')
        log_event('error', '发消息', '给「%s」发送状态未确认' % name, detail)
        return TrueString(False, '发送状态未确认：消息列表里没有出现这条新消息。'
                                 '为避免重复发送，本次不会自动重试，请到抖音确认是否已送达')

    def Find_Friends(self, name: str):
        friends = self.Updara_FrinderList()
        if not friends and not self.friends_xpath_list:
            return TrueString(False, '没读到会话列表：可能未登录、列表没加载出来，或页面选择器已失效',
                              status='failed')
        try:
            if name in (self.friend_ambiguous or set()):
                return TrueString(False, '有多位叫「%s」的会话，无法确定发给谁' % name, status='failed')
            known = {item.username for item in friends or []}
            if name in (self.friends_xpath_list or {}) or (name in known and name not in (self.friend_ambiguous or set())):
                return TrueString(True, None)
            if hasattr(self, '_scroll_until_friend'):
                self._scroll_until_friend(name)
            if name in (self.friend_ambiguous or set()):
                return TrueString(False, '有多位叫「%s」的会话，无法确定发给谁' % name, status='failed')
            if name in (self.friends_xpath_list or {}):
                return TrueString(True, None)
            return TrueString(False, '会话列表里没有「%s」' % name, status='failed')
        except Exception as e:
            return TrueString(False, e, status='failed')

    def LoginInit(self):
        try:
            dle_user = driver.find_element(By.XPATH,
                                           value='//*[@id="douyin_login_comp_flat_panel"]/div/div[2]/div/div[4]/p')
            dle_user.click()
        except:
            pass


init = False
Login_is_bool = False
driver = None
douyin = None
# 可重入锁：串行化所有浏览器操作，同时允许嵌套（如发送流程里再调 ensure_browser_ready）
browser_lock = threading.RLock()

# ==================== 浏览器存活检测（心跳） ====================
# 为什么不能直接问 WebDriver：_driver_alive() 会执行一次 execute_script，这是一次真实的
# HTTP 调用。Chrome 渲染进程卡住时（弹出模态框、页面忙、进程假死）它会一直挂到 Selenium
# 自己的超时。而它被 ticker 线程调用（check_due_tasks → _browser_ready_for_send），
# 于是**整个调度会跟着停摆**，而且不会有任何日志 —— 面板照旧显示任务列表，实际再也不发。
# 同时也别让 Docker 的 HEALTHCHECK（每 30 秒一次）去戳同一个卡住的会话。
#
# 方案：由调度线程自己按固定间隔做一次真实探测，把结果与时间戳记在内存里；
# 其他所有地方只读这份心跳。心跳过期即视为不可用（宁可为假、不可为真 ——
# 真到要发的时候，发送流程自己会再确认一次并把失败如实记下来）。
BROWSER_HEARTBEAT_SECONDS = 20.0
_browser_heartbeat = {'at': 0.0, 'ok': False}
# 注：FastAPI 实例与 CORS 配置挪到「调度 / 任务」那段代码之后才创建，
# 因为它的 lifespan（启动时恢复任务并拉起 ticker、退出时收掉浏览器）依赖那些函数。

# ==================== 信息日志 ====================
# 供面板「信息日志」页查看的程序运行记录：浏览器 / 登录 / 验证 / 发消息 / 定时任务。
# 存 logs/app.log，每行一条 JSON（写入简单、前端解析无歧义）；
# 超过上限只保留最近若干行，避免长期运行把磁盘写满。
APP_LOG_FILE = os.path.join(LOG_DIR, 'app.log')
APP_LOG_MAX_BYTES = 2 * 1024 * 1024
APP_LOG_KEEP_LINES = 2000
APP_LOG_LEVELS = ('success', 'info', 'warn', 'error')
# 信息日志页「分类」下拉的数据源。这个元组必须覆盖所有 log_event 用到的分类：
# 以前只列了 8 个，实际用到 19 个，导致「通知」「一言」「灵签」这些日志在页面上
# 根本筛不出来（用户报「检查下消息通知有没有bug」时发现的）。
# 新增测试 test_every_log_category_is_selectable 会用 AST 扫 log_event 调用点兜住这件事。
APP_LOG_CATEGORIES = ('浏览器', '登录', '验证', '发消息', '定时任务', '任务管理', '消息记录', '系统',
                      '通知', '发图片', '灵签', '一言', '女朋友模式', '试发', '预检', '风控',
                      '账号', '好友列表', '清理')
_app_log_lock = threading.Lock()


def _trim_app_log():
    """日志超过上限时只保留最近若干行（调用方须已持 _app_log_lock）。"""
    try:
        with open(APP_LOG_FILE, 'r', encoding='utf-8', errors='replace') as handle:
            lines = handle.readlines()
        with open(APP_LOG_FILE, 'w', encoding='utf-8') as handle:
            handle.writelines(lines[-APP_LOG_KEEP_LINES:])
    except Exception:
        pass


def log_event(level, category, message, detail=None):
    """写一条信息日志。日志失败绝不影响主流程。"""
    entry = {
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'level': level if level in APP_LOG_LEVELS else 'info',
        'category': category or '系统',
        'message': '' if message is None else str(message),
    }
    if detail:
        entry['detail'] = str(detail)[:1000]
    try:
        os.makedirs(os.path.dirname(APP_LOG_FILE), exist_ok=True)
        with _app_log_lock:
            with open(APP_LOG_FILE, 'a', encoding='utf-8') as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + '\n')
            if os.path.getsize(APP_LOG_FILE) > APP_LOG_MAX_BYTES:
                _trim_app_log()
    except Exception:
        pass
    return entry


def _notify_config(force=False):
    """补齐通知配置。旧的 state.json 里没有邮箱字段，这里补默认值。

    force=True 表示「必须包含邮箱字段」：通知配置整体落盘时用，避免旧的
    state.json 缺字段时被 setdefault 补成一半（缺 email_ssl 等键）。
    后台线程里读取配置不传 force，安全优先，不给调用方抛异常的机会。
    """
    raw = dict(STATE.get('notify') or {})
    defaults = (DEFAULT_STATE.get('notify') or {})
    try:
        for key, value in defaults.items():
            raw.setdefault(key, value)
    except RuntimeError:
        if force:
            raise
    return raw


def _email_settings(config):
    config = config or {}
    return {
        'host': config.get('email_host'),
        'port': config.get('email_port') or 465,
        'username': config.get('email_username'),
        'password': config.get('email_password'),
        'from': config.get('email_from') or config.get('email_username'),
        'to': config.get('email_to'),
        'ssl': bool(config.get('email_ssl', True)),
    }


# 接口里的邮箱字段名 -> state.json 里的键名。前端用短名（host/port/to…），
# 落盘沿用 email_ 前缀。两套名字必须显式映射，靠字符串拼接很容易写错。
_EMAIL_KEYS = {
    'host': 'email_host',
    'port': 'email_port',
    'username': 'email_username',
    'password': 'email_password',
    'from': 'email_from',
    'to': 'email_to',
    'ssl': 'email_ssl',
}


def _email_configured(config):
    """邮箱是否已配置到「能发信」的程度。email_to 为空时开开关只会静默失败，所以一并作为判据。"""
    config = config or {}
    return bool(config.get('email_host') and config.get('email_to'))


def _email_config_view(config):
    """邮箱配置的接口回显：地址做打码，密码只告诉前端「已设置」。"""
    config = config or {}
    view = {
        'enabled': bool(config.get('email_enabled')),
        'configured': _email_configured(config),
        'host': str(config.get('email_host') or ''),
        'port': config.get('email_port') or 465,
        'ssl': bool(config.get('email_ssl', True)),
        'username_set': bool(config.get('email_username')),
        'password_set': bool(config.get('email_password')),
        'from_set': bool(config.get('email_from')),
        'from_masked': mask_email(config.get('email_from') or config.get('email_username')),
        'to_masked': mask_email(config.get('email_to')),
    }
    return view


# 邮箱密码落盘前先做哈希标记：state.json 里本来就是明文凭据，这里不是为了防泄漏，
# 而是为了判断「前端原样回传的密码」和「已保存的密码」是否一致（一致就不必重写）。
_PASSWORD_ECHO_RE = re.compile(r'^sha256:[0-9a-f]{64}$')
_EMAIL_ADDRESS_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _password_fingerprint(password):
    return 'sha256:%s' % hashlib.sha256(str(password or '').encode('utf-8')).hexdigest()


def _is_password_echo(value):
    """password 回传的是本接口自己发的标记（sha256:...），不是用户输入的密码。"""
    return bool(_PASSWORD_ECHO_RE.match(str(value or '').strip()))


def _apply_email_config(payload, config):
    """把前端提交的邮箱设置合并进 config，返回错误说明（通过返回 None）。

    必须能同时表达三件事，所以把 None 和空串区分开：
      * 键不存在 / 传 None —— 不修改（前端留空表示「保持已保存的值」）；
      * 传空串 —— 清空该字段（前端点「清空」或确实想删掉）；
      * 传值 —— 覆盖（密码位若是本接口发的 sha256 标记，说明用户没改，原样保留）。
    """
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return '邮箱配置格式不正确'
    if 'enabled' in payload:
        config['email_enabled'] = bool(payload.get('enabled'))
    for field, key in _EMAIL_KEYS.items():
        if field not in payload:
            continue
        value = payload.get(field)
        if value is None:
            continue
        if field == 'port':
            try:
                port = int(value)
            except (TypeError, ValueError):
                return 'SMTP 端口不正确'
            if port < 1 or port > 65535:
                return 'SMTP 端口不正确'
            config[key] = port
        elif field == 'ssl':
            config[key] = bool(value)
        elif field == 'password':
            password = str(value)
            # 用户没动密码框时前端会原样回传 fingerprint，此时保留库里的真密码
            if not (_is_password_echo(password) and password == config.get(key)):
                config[key] = password
        else:
            config[key] = str(value).strip()
    # 开关打开前必须校验「配完这次提交之后」的完整配置，否则开关开了却永远发不出去
    if config.get('email_enabled'):
        problem = validate_email_settings(_email_settings(config), allow_private=ALLOW_PRIVATE_PUSH)
        if problem:
            return problem
    return None


def _notify_worker(config, webhook, email_on, title, content):
    """通知线程真正干活的那一段。

    单独抽成模块级函数有两个原因：
      1. 整段包了 try/except：推送 / 邮件里任何一个没预料到的异常，以前都会让 daemon
         线程直接死掉 —— 这条通知等于从来没发过，日志里也一个字都没有（notifier.push
         曾经对 `[]` 这种合法 JSON 抛 AttributeError，就是这么静默消失的）。
      2. 抽出来之后测试可以直接调用它并断言「异常确实进了日志」；跑在线程里就只能
         靠 sleep 抢时序，那是不稳定的测试。
    推送地址和邮箱密码都属于凭据，绝不写进日志。
    """
    try:
        if webhook:
            ok, detail = push(config.get('url'), title, content, allow_private=ALLOW_PRIVATE_PUSH)
            if ok:
                log_event('info', '通知', '已推送：%s' % title)
            else:
                log_event('warn', '通知', '推送失败：%s' % detail)
        if email_on:
            ok, detail = send_email(_email_settings(config), title, content, allow_private=ALLOW_PRIVATE_PUSH)
            if ok:
                log_event('info', '通知', '已发邮件：%s' % title)
            else:
                log_event('warn', '通知', '邮件发送失败：%s' % detail)
    except Exception as exc:
        log_event('error', '通知', '通知发送异常：%s' % exc)


def notify(title, content, force=False):
    """发一条消息通知（异步，不阻塞发送流程）。

    force=True 表示这是告警类通知（风控、登录失效），不受「成功也通知」开关影响。
    推送地址和邮箱密码都属于凭据，绝不写进日志。
    """
    config = _notify_config()
    if not config.get('enabled'):
        return False
    if not force and not config.get('on_success'):
        return False
    webhook = bool(config.get('url'))
    email_on = bool(config.get('email_enabled') and config.get('email_host') and config.get('email_to'))
    if not webhook and not email_on:
        return False

    threading.Thread(target=_notify_worker,
                     args=(config, webhook, email_on, title, content), daemon=True).start()
    return True


def read_app_log(level=None, category=None, keyword=None, page=1, size=50):
    """倒序读取日志（最新在前），支持按级别/分类/关键字过滤与分页。"""
    entries = []
    try:
        with open(APP_LOG_FILE, 'r', encoding='utf-8', errors='replace') as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if not isinstance(item, dict):
                    continue
                if level and item.get('level') != level:
                    continue
                if category and item.get('category') != category:
                    continue
                if keyword:
                    haystack = '%s %s %s' % (item.get('message', ''), item.get('detail', ''),
                                             item.get('category', ''))
                    if keyword.lower() not in haystack.lower():
                        continue
                entries.append(item)
    except FileNotFoundError:
        entries = []
    except Exception:
        entries = []
    entries.reverse()
    total = len(entries)
    try:
        size = max(1, min(int(size), 200))
        page = max(1, int(page))
    except (TypeError, ValueError):
        size, page = 50, 1
    start = (page - 1) * size
    return {
        'total': total,
        'page': page,
        'size': size,
        'pages': (total + size - 1) // size,
        'list': entries[start:start + size],
    }


def clear_app_log():
    """清空日志文件。"""
    try:
        with _app_log_lock:
            with open(APP_LOG_FILE, 'w', encoding='utf-8'):
                pass
        return True
    except Exception:
        return False


# ==================== 状态持久化 ====================
# 定时任务、登录密码、通知配置、发送记账都落在这里，重启不再丢。
STATE_FILE = os.path.join(DATA_DIR, 'state.json')
DEFAULT_STATE = {
    'password_hash': None,   # None = 还没改过密码，用内置默认密码
    'tasks': [],             # 定时任务原始字段（重建任务用）
    'notify': {
        'enabled': False,
        'url': '',
        'on_success': False,
        'email_enabled': False,
        'email_host': '',
        'email_port': 465,
        'email_username': '',
        'email_password': '',
        'email_from': '',
        'email_to': '',
        'email_ssl': True,
    },
    'send_history': {},      # 发送记账：{'日期|好友': {'status','at','text'}}
    'retry_queue': {},       # 当日补发队列：{'date','due_at','done','items'}
    'manual_retries': {},    # 人工重发标记：{'日期|好友': 日期}
    'girlfriend': dict(GF_DEFAULT_CONFIG),   # 女朋友模式配置（和风天气 Host/Key/城市…）
}
STATE = StateStore(STATE_FILE, defaults=DEFAULT_STATE)

if STATE.load_error:
    log_event('error', '系统', '状态文件读取失败，已按默认值启动', STATE.load_error)
if not STATE.get('password_hash'):
    log_event('warn', '系统', '当前使用内置默认密码，请尽快在「设置」页修改')


# 默认密码：只在内置兜底时生效（state.json 里一旦有哈希，就以落盘的为准）
_password = '123456'


def _stored_password_hash():
    """落盘的密码哈希（None = 从来没改过密码，仍在使用内置默认密码）。"""
    stored = STATE.get('password_hash')
    return stored if stored else None


def _password_hash() -> str:
    """当前生效的密码哈希。"""
    return _stored_password_hash() or hash_password(_password)


def check_password(password) -> bool:
    """校验面板密码。

    哈希格式是 PBKDF2-HMAC-SHA256 加盐（旧实现是无盐 SHA-256，state.json 泄露即可秒破）。
    老用户这里做透明升级：只要用旧格式校验通过一次，就顺手把存储换成新格式，
    既补上了强度，也不会把任何人锁在门外。
    """
    stored = _stored_password_hash()
    if not stored:
        # 还没改过密码：与内置默认密码比对
        return str(password or '') == _password
    ok, needs_upgrade = verify_password(password, stored)
    if ok and needs_upgrade:
        if STATE.set('password_hash', hash_password(password)):
            log_event('warn', '系统', '检测到旧格式（无盐 SHA-256）密码哈希，已自动升级为 PBKDF2 加盐存储')
    return ok


def using_default_password():
    """还没改过面板密码。默认密码不能发消息，也不能跑定时任务。"""
    return not _stored_password_hash()


def default_password_block():
    if using_default_password():
        return {'code': 403, 'data': '当前仍是默认密码。请先到设置页修改密码，之后才能发消息和创建定时任务'}
    return None


def setup_guard():
    """默认密码期间的兜底拦截：只用来挡「拿了默认密码就能接管面板 / 把消息发出去」的动作。

    为什么必须有这一层：内置默认密码是 123456（6 位数字），只把这个状态当成
    「还不能发消息」是不够的 —— 实测拿默认密码登录后可以直接调 /Api/ChangePassword
    把密码改成自己的，之后 must_change_password 变 False，守卫就再也拦不住了，
    等于任何人先扫到面板就能把机主锁在门外。
    放行的是「改密码 + 只读/初始化查询」这些把面板配好所必需的接口；
    浏览器初始化、扫码登录绑定抖音等属于「接管抖音账号」的动作也一并挡住，
    因为在此期间它们同样不该被一个只知道 123456 的人触发。
    """
    if using_default_password():
        return {'code': 403,
                'data': '当前仍是内置默认密码（123456）。为防止面板被他人接管，'
                        '请先在「设置」页修改密码，之后才能进行绑定账号、发消息、'
                        '创建定时任务和修改通知配置'}
    return None


# 面板会话 token：带空闲过期、可一键全部失效（旧实现是永不过期的内存 set，
# 改密码也踢不掉别人，而且只增不减）
_valid_tokens = TokenBook(TOKEN_TTL_HOURS)
_last_login_ip = '无'


def generate_token() -> str:
    return _valid_tokens.issue()


def verify_token(token: str) -> bool:
    return _valid_tokens.verify(token)


def remove_token(token: str):
    _valid_tokens.revoke(token)


def require_auth(authorization: str = Header(None)):
    if not authorization or not authorization.startswith('Bearer '):
        return {'code': 401, 'data': '未授权'}
    token = authorization[7:]
    if not verify_token(token):
        return {'code': 401, 'data': '未授权'}
    return None


# 定时任务存储：只有这一份 task_meta，它同时是调度的依据和落盘的内容。
# 旧实现把任务同时存在 schedule 的 job 对象和 task_meta 两处，两边必须手动同步，
# 于是出现了「改时间但时间没变 → 新 job 被自己 pop 出来 cancel 掉」这种必现 bug。
task_meta = {}                     # 任务ID -> {'time','name','text','last_run_date'}
_task_lock = threading.RLock()     # 保护 task_meta 与发送队列（请求线程和调度线程都会碰）


# ==================== 定时调度（自带 ticker，不再依赖 schedule 库） ====================
# 为什么换掉 schedule：
#   1. 它不持久化、也不处理「错过」：进程在那一分钟不在（重启/崩溃/升级），任务就永久跳过。
#      而「今天必须发出去」正是本项目的硬需求 —— 漏一天火花就断了；
#   2. 它内部没有锁，请求线程里增删任务会与调度线程并发改 job 列表；
#   3. job 抛出的异常会直接把调度线程打死，而且没有任何日志：
#      面板照旧显示任务列表，用户以为在跑，其实再也不会发。
# 现在：ticker 周期检查「今天该不该发、要不要补跑、要不要补发」，
# 判定逻辑全在 spark_core（纯函数、有单测）；真正发送交给独立 worker 线程串行执行，
# 这样一次可能耗时 60 秒的发送只会排队，不会卡住 ticker，也不会占满 HTTP 线程池。
_scheduler_thread = None
_worker_thread = None
_scheduler_stop = threading.Event()
_scheduler_started = False
_send_queue = queue.Queue()
TICK_INTERVAL_SECONDS = 5
KIND_LABELS = {'task': '定时', 'catchup': '补跑', 'retry': '当日补发', 'confirm': '人工重发',
               'manual': '手动发送', 'test': '试发'}


def _enqueue_send(name, text, kind='task', task_id=None):
    """把一次发送排进队列（真正的执行在 worker 线程里）。"""
    _send_queue.put({'task_id': task_id, 'name': name, 'text': text, 'kind': kind})


def _persist_tasks():
    """把定时任务落到 state.json（内存对象没法序列化，只存重建所需字段）。"""
    with _task_lock:
        items = []
        for task_id, meta in task_meta.items():
            entry = {'task_id': task_id}
            entry.update(meta)
            items.append(entry)
    STATE.set('tasks', items)


def _register_task(play_time, name, text, task_id=None, mark_today=False, sign=False, source=None):
    """登记一条定时任务（内存 + 落盘），返回任务ID。

    task_id 用随机串，不再用「时间_好友名」拼：好友名里带下划线或后缀相同时
    （A 与 B_A），旧拼法会让两条任务互相误判成同一条。
    mark_today=True 用于「新建/改时间时今天这个点已经过去」的场景：直接记为今天已跑，
    免得刚建完任务就立刻补发一条，用户完全没有预期。
    sign=True 表示这条任务每天发「文昌帝君灵签」图文（签图 + 当天签文）。
    source='hitokoto' 表示这条任务的正文在发送时现取一句一言（写下来的 text 只当兜底）。
    """
    task_id = task_id or uuid.uuid4().hex[:12]
    meta = {
        'time': play_time,
        'name': name,
        'text': text,
        # 灵签开关按任务存：同一个人可以今天发签文、明天改回普通文案
        'sign': bool(sign),
        # 内容来源也按任务存：'text'（默认）用上面的文案池，'hitokoto' 发送时现取一句
        'source': _normalise_task_source(source),
        # last_run_date：今天是否已经跑过（对外展示 + 老字段兼容）
        # last_planned_date：已发出的那一次属于哪一天（跨午夜任务靠它判断「这次做过没有」）
        'last_run_date': today_str() if mark_today else None,
        'last_planned_date': today_str() if mark_today else None,
    }
    with _task_lock:
        task_meta[task_id] = meta
    _persist_tasks()
    return task_id


def restore_tasks():
    """启动时恢复落盘的定时任务（旧实现只放内存，重启即全部消失）。"""
    saved = STATE.get('tasks') or []
    if not isinstance(saved, list):
        return 0
    restored = 0
    skipped = 0
    with _task_lock:
        for item in saved:
            if not isinstance(item, dict):
                continue
            task_id = item.get('task_id')
            name = item.get('name')
            if not (task_id and name) or task_id in task_meta:
                skipped += 1
                continue
            task_meta[task_id] = {
                'time': format_time(item.get('time')),
                'name': name,
                # 文案池允许为空：空 = 每次发送时再去取一句（每天都不同）
                'text': item.get('text') or '',
                # 老 state.json 没有 sign 字段：缺省就是普通文字任务
                'sign': bool(item.get('sign')),
                # 老 state.json 没有 source 字段：缺省就是「用自己写的文案」
                'source': _normalise_task_source(item.get('source')),
                'last_run_date': item.get('last_run_date'),
                # 老 state.json 没有这个字段：留 None，_occurrence_done 会自动退回
                # 按 last_run_date 判断，不会因为缺字段就把当天重发一遍
                'last_planned_date': item.get('last_planned_date'),
            }
            restored += 1
    if skipped:
        log_event('warn', '任务管理', '跳过 %d 条字段不全的定时任务记录' % skipped)
    if restored:
        log_event('success', '任务管理', '已从状态文件恢复 %d 个定时任务' % restored)
    return restored


def _resolve_text(text):
    """把任务里存的文案池解析成这一次真正要发的内容。

    文案池支持一行一条（随机挑一条）和 {date}/{weekday} 占位符；
    留空则每次发送前现取一句 —— 旧实现是建任务时取一次就固定下来，
    结果每天给同一个人发的都是同一句话，本身就是很明显的机器特征。
    """
    rendered = render_message(text)
    if rendered:
        return rendered
    if REMOTE_QUOTE:
        return AiqingGongyu_text()
    return random.choice(FALLBACK_MESSAGES)


def _send_outcome(out):
    """把发送结果收成 (status, reason)。优先用显式 status，避免再靠文案猜。"""
    if out is not None and getattr(out, 'is_bool', False):
        return 'success', None
    reason = out.string if out is not None else '没有返回结果（内部异常）'
    status = getattr(out, 'status', None) if out is not None else None
    if status not in ('failed', 'unknown'):
        status = send_status_from_reason(reason)
    return status, reason


def _manual_retry_used(name):
    book = STATE.get('manual_retries') or {}
    return bool(book.get(_history_key(name)))


def run_scheduled_send(name, text=None, kind='task', task_id=None):
    """执行一次（定时 / 补跑 / 补发）发送，返回终态字符串。

    返回 'success' / 'failed:<原因>' / 'unknown:<原因>' / 'skipped:<原因>' / 'stopped:<原因>'。
    worker 用返回值决定「要不要安排当日补发」「要不要停下整轮」。
    """
    # 同一天多个任务的触发时刻可能挨得很近，先随机错开一点，避免挤在同一拍上
    if using_default_password():
        log_event('warn', '定时任务', '跳过「%s」：仍在使用默认密码' % name)
        return 'skipped:仍在使用默认密码'
    human_pause(1.0, 15.0)
    task_now = (task_meta.get(task_id) or {}) if task_id else {}
    sign_flag = bool(task_now.get('sign'))
    sign_data = fetch_wenchang_sign() if sign_flag else None
    if sign_flag and not sign_data:
        # 签文取不到就退化成普通文案：定时任务的第一目标是「火花不能断」，
        # 不能因为第三方接口挂了就整天不发。
        log_event('warn', '定时任务', '给「%s」取文昌帝君灵签失败，本次只发文字' % name)
    # 内容来源不是「文字池」的任务：每次发送前现取 / 现渲染一份。取不到就走下面的
    # 兜底链，绝不返回空内容 —— 三方接口挂了不是「今天不发」的理由。
    source_now = str(task_now.get('source') or 'text')
    hitokoto_text = None
    if source_now == 'hitokoto':
        hitokoto_text = fetch_hitokoto()
        if not hitokoto_text:
            log_event('warn', '一言', '给「%s」取一言失败，本次改用任务自己的文案' % name)
    # 女朋友模式：按发送时刻的时段现渲染一条天气问候（没填 Host/Key、城市查不到、
    # 天气接口挂了都会返回 None），同样退回任务自己写的文案。
    girlfriend_text = None
    if source_now == 'girlfriend':
        girlfriend_text = render_girlfriend_text()
        if not girlfriend_text:
            log_event('warn', '女朋友模式', '给「%s」渲染天气问候失败，本次改用任务自己的文案' % name)
    # 正文取法（和面板上的说明、以及 /Time/test 的试发保持一致）：
    #   一言任务 + 取到了      -> 用取到的那句话
    #   女朋友模式 + 渲染成功  -> 用渲染出来的天气问候
    #   灵签任务 + 文案留空    -> 用当天签文
    #   灵签任务 + 写了文案    -> 用用户写的（签图照发；旧实现是签文覆盖它，等于把用户写的字丢了）
    #   普通任务               -> 文案池渲染 / 留空现取一条
    content = ''
    if hitokoto_text:
        content = hitokoto_text
    if not content and girlfriend_text:
        content = girlfriend_text
    if not content and sign_data and not (text or '').strip():
        content = render_sign_text(sign_data)
    if not content:
        if source_now in ('hitokoto', 'girlfriend'):
            # 现取失败时的退化链：任务自己的文案 -> 本地兜底。
            # 这里刻意不走 _resolve_text：用户点名要的是「一言 / 女朋友模式」，接口挂了就
            # 老老实实用他自己写的字兜底，别背着他换成另一个来源（名言接口）。
            content = render_message(text) or random.choice(FALLBACK_MESSAGES)
        else:
            content = _resolve_text(text)
    image_path = None
    if sign_data and sign_data.get('pic'):
        image_path, image_error = download_image(sign_data['pic'])
        if image_error:
            # 图片是锦上添花，下载失败不影响把文字发出去
            log_event('warn', '定时任务', '给「%s」下载签图失败，本次只发文字：%s' % (name, image_error))
        else:
            log_event('info', '定时任务', '已下载签图：%s' % image_path)
    # 记账用文本：图片本身不进 state.json（那是 JSON 原子写），只留一句可读摘要。
    # 去重和送达记录都按它走，所以「图文」这一天只记一条账。
    record_text = image_record_text(content, sign_summary(sign_data)) if image_path else content
    label = KIND_LABELS.get(kind, kind)
    log_event('info', '定时任务', '开始%s：给「%s」发消息' % (label, name), record_text)
    if douyin is None:
        log_event('error', '定时任务', '%s失败：浏览器未初始化（请先在首页初始化）' % label)
        return 'skipped:浏览器未初始化'
    # 发送前体检：风控 / 登录失效时整轮停下（继续硬发只会招来更严厉的限制）
    problem = send_precheck()
    if problem:
        log_event('error', '定时任务', '跳过「%s」：%s' % (name, problem))
        return 'stopped:%s' % problem
    blocked = send_guard(name, record_text, scope='task')
    if blocked:
        log_event('warn', '定时任务', '跳过「%s」：%s' % (name, blocked))
        return 'skipped:%s' % blocked
    with browser_lock:
        # 发送前先记 'unknown'：万一进程在中途被杀，当天也不会重复发
        _history_mark(name, record_text, 'unknown', kind=kind)
        try:
            out = Douyin.Send_Frinder(douyin, name, content, paced=True, image=image_path)
        except Exception as exc:
            _history_mark(name, record_text, 'unknown', exc, kind=kind)
            log_event('error', '定时任务', '给「%s」自动发消息异常' % name, exc)
            notify('自动续火花失败', '给「%s」自动发消息时出错：%s' % (name, exc), force=True)
            return 'unknown:%s' % exc
    if out is not None and out.is_bool:
        _history_mark(name, record_text, 'success', kind=kind)
        detail = dict(getattr(douyin, 'last_send_detail', None) or {})
        detail['内容'] = record_text
        log_event('success', '定时任务', '已自动发送给「%s」' % name,
                  json.dumps(detail, ensure_ascii=False) if detail else record_text)
        # 通知里说实话：签图没确认送出时不能报「图文」（用户会以为对方收到了那张图）。
        image_note = str(detail.get('图片') or '')
        image_lost = image_note.startswith('未送达')
        notify('自动续火花成功',
               '已给「%s」发出今日%s。%s' % (name,
                                            '灵签（图文）' if image_path and not image_lost else '消息',
                                            '签图没有送出（%s）' % image_note if image_lost else ''))
        return 'success'
    status, reason = _send_outcome(out)
    _history_mark(name, record_text, status, reason, kind=kind)
    # 日志里带上归类（transient / permanent / authentication / rate_limit），
    # 排查时一眼能看出「是页面结构变了，还是被风控了，还是登录掉了」
    log_event('error', '定时任务', '给「%s」自动发消息失败（%s）' % (name, classify_error(reason)), reason)
    # 发送失败要让用户马上知道（面板不会一直开着）
    notify('自动续火花失败', '给「%s」发消息失败：%s' % (name, reason), force=True)
    return '%s:%s' % (status, reason)


# ---------- 当日失败补发 ----------
# 参考开源项目的做法：本轮有好友失败时，隔一段时间只对失败的那几位补发一次，每天最多一次。
# 只补「临时性失败」：结果不确定（可能已经送达）和永久性错误再发一遍，等于让对方收到两条。
def schedule_daily_retry(name, text, reason):
    """把一位需要当日补发的好友记进队列，返回是否记上了。"""
    if RETRY_AFTER_MINUTES <= 0 or not retryable(reason):
        return False
    today = today_str()
    with _task_lock:
        state = STATE.get('retry_queue') or {}
        if state.get('date') != today:
            state = {'date': today, 'done': False, 'due_at': None, 'items': []}
        items = state.get('items') or []
        if any(item.get('name') == name for item in items):
            return False
        items.append({'name': name, 'text': text, 'reason': str(reason)[:200]})
        state['items'] = items
        if not state.get('due_at'):
            state['due_at'] = (datetime.now() + timedelta(minutes=RETRY_AFTER_MINUTES)
                               ).strftime('%Y-%m-%d %H:%M:%S')
        STATE.set('retry_queue', state)
    log_event('warn', '定时任务', '已安排 %d 分钟后只对「%s」补发一次' % (RETRY_AFTER_MINUTES, name), reason)
    return True


def check_daily_retry(now=None):
    """当日补发到点没有；到了就取走队列并标记已完成（每天最多一次）。"""
    moment = now or datetime.now()
    with _task_lock:
        state = STATE.get('retry_queue') or {}
        if not state or state.get('date') != today_str(moment):
            return []
        if state.get('done') or not state.get('items'):
            return []
        if not retry_due(_parse_dt(state.get('due_at')), moment, state.get('done')):
            return []
        items = list(state.get('items') or [])
        state['done'] = True
        STATE.set('retry_queue', state)
    log_event('info', '定时任务', '开始当日补发：%d 位好友' % len(items),
              '、'.join(item.get('name') or '' for item in items))
    return items


def _clear_retry_queue(reason):
    """取消当日补发队列（例如已经触发风控，再补发只会更糟）。"""
    with _task_lock:
        state = STATE.get('retry_queue') or {}
        if not state or not state.get('items'):
            return False
        state['done'] = True
        state['items'] = []
        STATE.set('retry_queue', state)
    log_event('warn', '定时任务', '已取消当日补发：%s' % reason)
    return True


def _parse_dt(value):
    """把落盘的时间字符串解析回 datetime（解析失败返回 None）。"""
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.strptime(str(value), '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


# ---------- 调度检查 ----------
BROWSER_WAIT_LOG_SECONDS = 600      # 浏览器没就绪时，这条提醒最多 10 分钟刷一次
_last_browser_wait_log = 0.0


def _browser_ready_for_send():
    """现在能不能真的发消息（浏览器会话是否还活着）。

    刻意**不做** WebDriver 调用：这个函数跑在 ticker 线程上，而探测本身可能因为
    Chrome 卡住而长时间不返回，一旦卡住整个调度就停了（详见 _driver_alive 的注释）。
    真实探测由 _refresh_browser_heartbeat() 在每轮 tick 开头完成，这里只读结果。
    """
    try:
        return bool(douyin is not None and _driver_alive(probe=False))
    except Exception:
        return False


_last_default_password_log = 0.0


def _planned_or_none(play_time, day, task_id):
    """某一天的「计划执行时刻」；时间字段非法时返回 None。"""
    try:
        return planned_run_at(play_time, day, task_id, JITTER_MINUTES)
    except Exception:
        return None


def time_format_error(value):
    """校验 HH:MM（也接受 H:MM），返回错误说明（通过返回 None）。

    format_time 容错性很强：None / '' / 'zzz' 都会被静默换成 22:00。
    对「用户明确提交了一个时间」的接口来说，静默替换比报错更糟 ——
    用户以为设的是别的时间，任务却天天 22:00 发。
    """
    text = str(value or '').strip()
    if not text:
        return '请先选择发送时间'
    if not re.match(r'^\d{1,2}:\d{1,2}$', text):
        return '时间格式不对，请用 HH:MM（例如 21:30）'
    hour, minute = (int(part) for part in text.split(':'))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return '时间超出范围，请用 00:00 ~ 23:59'
    return None


def _occurrence_done(meta, day_key):
    """某一天这次发送是否已经做过。

    last_planned_date 是新增字段，记录「已发出的那一次属于哪一天」；
    老 state.json 里没有它，退回旧的 last_run_date 语义（= 发出当天），
    这样升级后不会因为缺字段就把历史当成没发过、当天重发一条。
    """
    if meta.get('last_planned_date'):
        return meta.get('last_planned_date') == day_key
    return meta.get('last_run_date') == day_key


def pending_occurrence(task_id, meta, moment, grace_minutes, only_future=False):
    """挑出此刻「待发」的那一次：返回 (计划时刻, 归属日期)，没有则 (None, None)。

    为什么不能只用「当前日期」算：
        计划时刻 = 基准时间 + 当天随机偏移（默认窗口 40 分钟）。任务设在 23:50 时，
        计划时刻会落到次日 00:00~00:30。旧实现每次都按「此刻的日期」重算，
        于是次日 00:10 那一跳算的是「次日 23:50 的任务」——还在未来，不发；
        而当天那一次的机会就被永久跳过了（实测 23:50 的任务 8 天只发出 2 次）。
    现在的规则：
        * 待发 = 计划时刻已到、但还在补跑窗口内、且这一次还没发过；
        * 只在宽限窗口内回溯一天（窗口 >= 24 小时才看前天），窗口给了几天就补几天；
        * 同一天的计划时刻在当天固定，所以「一天最多发一次」不会被破坏；
        * only_future=True 时要求计划时刻在 moment 之后（只用来算「下一次几点」，
          发不发仍然由 check_due_tasks 按上面的规则决定）。
    """
    day = moment.date()
    # 按「基准日越早越优先」检查。这里有个容易写错的点：不能因为「今天还不到点」就
    # 直接跳过昨天 —— 今天 23:53 还没到，不代表昨天 00:06 那次已经发过了
    # （正是这一条让修复前 8 天只发 2 次）。所以先收集所有候选，再按时间取最早那个。
    days = [day, day - timedelta(days=1)]
    if grace_minutes is not None and grace_minutes >= 1440:
        days.append(day - timedelta(days=2))
    candidates = []
    for plan_day in days:
        planned = _planned_or_none(meta.get('time'), plan_day, task_id)
        if planned is None:
            continue
        if only_future and planned <= moment:
            continue
        if run_due(planned, moment, None, grace_minutes) and not _occurrence_done(meta, today_str(plan_day)):
            candidates.append((planned, plan_day))
    if not candidates:
        return None, None
    # 取最早的那次：先补昨天，再考虑今天
    return min(candidates, key=lambda item: item[0])


def check_due_tasks(now=None):
    """检查所有任务今天该不该发，该发的排进队列，返回本次入队的任务ID列表。

    「到点 / 补跑 / 一天只发一次」的判定在 spark_core.run_due 里（有单测）。
    注意先把 last_run_date 写成今天再入队：一次发送最长可能跑 60 秒，
    期间 ticker 还会继续检查，不先记账就会重复入队、同一条发两遍。
    """
    if using_default_password():
        global _last_default_password_log
        now_ts = time.time()
        if now_ts - _last_default_password_log > 3600:
            _last_default_password_log = now_ts
            log_event('warn', '系统', '仍在使用默认密码，定时发送已暂停。请先到设置页修改密码')
        return []
    moment = now or datetime.now()
    with _task_lock:
        snapshot = [(task_id, dict(meta)) for task_id, meta in task_meta.items()]
    due = []
    for task_id, meta in snapshot:
        name = meta.get('name')
        planned, plan_day = pending_occurrence(task_id, meta, moment, CATCHUP_GRACE_MINUTES)
        if planned is None:
            # 时间字段本身非法时给一条日志，否则任务会静默地永远不发
            if _planned_or_none(meta.get('time'), moment.date(), task_id) is None:
                log_event('error', '任务管理', '任务「%s」的时间无法解析，已跳过' % name)
            continue
        due.append((task_id, meta, planned, today_str(plan_day)))
    if not due:
        return []

    # 关键：浏览器没就绪时**不要**「发出去」。
    # 否则这一轮会被记成一次失败的发送、并且把 last_run_date 写成今天，
    # 于是等用户几分钟后点「初始化浏览器」时，今天的额度已经被浪费掉了 ——
    # 火花照样断，正是这次改造要解决的问题。
    # 这里选择什么都不做（不记账、不入队），等浏览器就绪后 ticker 自然会补上。
    if not _browser_ready_for_send():
        global _last_browser_wait_log
        now_ts = time.time()
        if now_ts - _last_browser_wait_log >= BROWSER_WAIT_LOG_SECONDS:
            _last_browser_wait_log = now_ts
            names = '、'.join((item[1].get('name') or '') for item in due[:5])
            log_event('warn', '定时任务',
                      '有 %d 个任务到点了，但浏览器还没初始化，暂不发送' % len(due),
                      '等初始化浏览器后会自动补上（涉及：%s）' % names)
        return []

    fired = []
    for task_id, meta, planned, plan_day in due:
        name = meta.get('name')
        with _task_lock:
            current = task_meta.get(task_id)
            # 再查一次：入队前把「这一次属于哪一天」记下来，避免 ticker 重复入队。
            # last_run_date 仍然写今天 —— 它同时是「今天已经跑过」的对外语义
            # （_task_view / 前端展示、以及老字段的兼容读法都认它）。
            if not current or _occurrence_done(current, plan_day):
                continue
            current['last_planned_date'] = plan_day
            current['last_run_date'] = today_str(moment)
        _persist_tasks()
        # 迟到 90 秒以上算补跑（说明进程当时不在），日志里区分开，便于排查「为什么现在才发」
        late = (moment - planned).total_seconds() > 90
        kind = 'catchup' if late else 'task'
        log_event('info', '定时任务',
                  '%s：给「%s」排队发送' % (KIND_LABELS[kind], name),
                  '计划时间 %s（归属 %s）' % (planned.strftime('%Y-%m-%d %H:%M:%S'), plan_day))
        _enqueue_send(name, meta.get('text'), kind=kind, task_id=task_id)
        fired.append(task_id)
    return fired


# ---------- 线程 ----------
def _send_worker():
    """串行执行发送队列。

    队列里同一时刻只有一个发送在跑（browser_lock 也保证浏览器串行）。
    这里必须把所有异常吃掉：worker 一死，定时任务就全静默失效了。
    """
    while not _scheduler_stop.is_set():
        try:
            job = _send_queue.get(timeout=1)
        except queue.Empty:
            continue
        try:
            result = run_scheduled_send(job.get('name'), job.get('text'),
                                        kind=job.get('kind') or 'task', task_id=job.get('task_id'))
        except Exception as exc:
            log_event('error', '定时任务', '发送任务执行异常（已隔离，队列继续）', exc)
            result = 'unknown:%s' % exc
        try:
            if not isinstance(result, str):
                continue
            if result.startswith('failed:') or result.startswith('unknown:'):
                reason = result.split(':', 1)[1]
                if should_stop_round(reason):
                    # 命中风控 / 登录失效：这时候再补发只会让限制更严，连补发队列一起取消
                    _clear_retry_queue(reason)
                else:
                    schedule_daily_retry(job.get('name'), job.get('text'), reason)
            elif result.startswith('stopped:'):
                # 发送前体检就没过（风控/掉线）：同样不补发
                _clear_retry_queue(result.split(':', 1)[1])
        except Exception as exc:
            log_event('error', '定时任务', '处理发送结果时出错（已隔离）', exc)


def _scheduler_loop():
    """调度主循环：异常全部就地隔离，绝不让线程死掉。

    旧实现是 `while True: schedule.run_pending()`，抛一次异常线程就永久退出，
    而且没有任何日志 —— 面板照常显示任务列表，实际一条也不会再发。
    """
    while not _scheduler_stop.is_set():
        try:
            # 每轮 tick 开头刷新一次浏览器心跳。这是整个进程里唯一会主动探测 WebDriver
            # 存活的地方，而且拿的是短超时锁：拿不到说明浏览器正忙（= 活着），不打扰它。
            _refresh_browser_heartbeat()
        except Exception:
            pass
        try:
            check_due_tasks()
        except Exception as exc:
            log_event('error', '定时任务', '检查定时任务时异常（已忽略，循环继续）', exc)
        try:
            # 补发同样要求浏览器就绪：否则队列会被标记成「已补发」，
            # 今天这次机会就白白浪费了（等就绪后再取，日期不匹配时会自动作废）。
            if _browser_ready_for_send():
                for item in check_daily_retry():
                    _enqueue_send(item.get('name'), item.get('text'), kind='retry')
        except Exception as exc:
            log_event('error', '定时任务', '检查当日补发时异常（已忽略，循环继续）', exc)
        try:
            # 顺手清理过期会话，避免长期运行内存缓慢增长
            _valid_tokens.prune()
        except Exception:
            pass
        _scheduler_stop.wait(TICK_INTERVAL_SECONDS)


def start_scheduler():
    """启动调度线程与发送 worker（幂等）。"""
    global _scheduler_thread, _worker_thread, _scheduler_started
    with _task_lock:
        if _scheduler_started:
            return _scheduler_thread
        _scheduler_stop.clear()
        _worker_thread = threading.Thread(target=_send_worker, name='spark-send-worker', daemon=True)
        _worker_thread.start()
        _scheduler_thread = threading.Thread(target=_scheduler_loop, name='spark-scheduler', daemon=True)
        _scheduler_thread.start()
        _scheduler_started = True
    log_event('success', '系统', '定时调度已启动（随机窗口 %d 分钟，补跑窗口 %d 分钟，补发延迟 %d 分钟）'
              % (JITTER_MINUTES, CATCHUP_GRACE_MINUTES, RETRY_AFTER_MINUTES))
    return _scheduler_thread


def stop_scheduler():
    """停掉调度线程与 worker（服务退出时调用）。"""
    global _scheduler_started
    _scheduler_stop.set()
    with _task_lock:
        _scheduler_started = False



def _reset_browser_state():
    global init, Login_is_bool, driver, douyin, _stealth_injected
    old_driver = driver
    driver = None
    douyin = None
    init = False
    Login_is_bool = False
    # 驱动没了，注入过的那份脚本自然也没了 —— 下次初始化要重新注入并重新统计。
    _stealth_injected = False
    # 心跳也要一起清：否则 probe=False 的调用方会拿着上一次的成功心跳
    # 认为「浏览器还活着」，而实际上驱动已经被我们 quit 掉了。
    _browser_heartbeat['ok'] = False
    _browser_heartbeat['at'] = 0.0
    if old_driver is not None:
        try:
            old_driver.quit()
        except Exception:
            pass


# ==================== HTTP 服务 ====================
def exc_text(exc):
    """把异常转成「可以回给客户端」的文案：保留原因，抹掉本机路径。

    selenium 的原始异常里常带绝对路径，例如
        Message: session deleted / C:\\Users\\admin\\AppData\\...\\chrome-profile\\Default
    这类信息对定位问题有用（"session deleted" 才是关键），但把服务器目录结构
    回给任何一个拿着面板 token 的人没必要。日志里仍然记完整原文（log_event），
    只有响应体走这里。
    """
    text = ' '.join(str(exc or '').split())
    # 先抹掉 Windows 盘符路径，再抹掉 POSIX 绝对路径（>=2 层，避免误伤 /Api/xxx 这种）
    text = re.sub(r'[A-Za-z]:\\[^\s\'"]*', '<路径>', text)
    text = re.sub(r'(?<![\w/])/(?:[\w.\-]+/)+[\w.\-]+', '<路径>', text)
    return text[:300]


# ==================== HTTP 服务 ====================
# 实例在这里才创建（而不是文件开头）：lifespan 依赖上面的任务恢复 / 调度线程 /
# 浏览器重置函数，必须等它们都定义好。
@asynccontextmanager
async def lifespan(_app):
    """启动：恢复落盘任务、拉起调度；退出：收掉浏览器进程。

    旧实现只在 `if __name__ == '__main__'` 里恢复任务 —— 用 `uvicorn backend:app`
    启动时任务会全部消失；而且没有任何退出钩子，服务重启后 chrome / chromedriver 会残留。
    """
    restore_tasks()
    start_scheduler()
    log_event('info', '系统', '服务已启动（版本 %s，退出请使用 Ctrl-C，会自动关闭浏览器）' % VERSION)
    # 无人值守的关键一步：重启后浏览器不会自己存在，而定时任务到点需要它。
    # 放到后台线程里做：拉起 Chrome 要十几秒，不能卡住服务启动。
    if AUTO_INIT_BROWSER and task_meta:
        threading.Thread(target=_auto_init_browser_worker, name='spark-auto-init', daemon=True).start()
    try:
        yield
    finally:
        stop_scheduler()
        _reset_browser_state()


app = FastAPI(
    lifespan=lifespan,
    # 关掉自动生成的接口文档：/docs、/redoc、/openapi.json 都是免鉴权的，
    # 会把全部路由和参数结构（包括手机号、验证码、授权码这些字段名）摊给任何
    # 能访问到端口的人。面板自己不需要这几个页面。
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# CORS：面板前端与后端同源（开发态走 Vite 代理），所以这里只需要放行本机开发端口。
# 旧的 allow_origins=["*"] + allow_credentials=True 是矛盾配置 —— 一旦带上 Cookie，
# Starlette 会回显任意来源，等于对全网开放。
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=['GET', 'POST'],
    allow_headers=['Authorization', 'Content-Type'],
)


def _driver_alive(probe=True):
    """浏览器会话是否还活着。

    probe=True  —— 真的问一次 WebDriver（execute_script），**只允许调度线程和已经把
                   浏览器操作串行化的调用方**使用；
    probe=False —— 只读心跳，不做任何 IO。给 healthz 这类高频/免鉴权接口用。

    这里的 probe 参数是刻意做成一望即知的：谁在热路径上真的碰了浏览器，
    代码里必须显式写出来，不能再有"看着像读变量、实际发了一次 HTTP"的调用。
    """
    if driver is None:
        return False
    if not probe:
        return bool(_browser_heartbeat['ok']
                    and (time.time() - _browser_heartbeat['at']) < BROWSER_HEARTBEAT_SECONDS)
    try:
        driver.execute_script('return document.readyState')
        _browser_heartbeat['at'] = time.time()
        _browser_heartbeat['ok'] = True
        return True
    except Exception:
        _browser_heartbeat['ok'] = False
        return False


def _refresh_browser_heartbeat():
    """由调度线程按 tick 调用：带锁做一次真实探测，失败即视为不可用。"""
    if driver is None:
        _browser_heartbeat['ok'] = False
        return False
    acquired = browser_lock.acquire(timeout=0.2)
    try:
        # 浏览器正忙（有人在发送）时不去打扰它：心跳保持上一次的结果，
        # 因为"正忙"本身就说明浏览器还活着。
        if not acquired:
            return bool(_browser_heartbeat['ok'])
        return _driver_alive(probe=True)
    finally:
        if acquired:
            browser_lock.release()


def _detect_logged_in_state():
    if driver is None:
        return False
    # 存在登录面板 / 扫码容器 => 一律视为未登录
    for xpath in (
        '//*[@id="douyin_login_comp_flat_panel"]',
        '//*[@id="animate_qrcode_container"]',
    ):
        try:
            if driver.find_elements(By.XPATH, xpath):
                return False
        except Exception:
            pass

    xpaths = [
        '//div[contains(@class, "conversationConversationListwrapper")]',
        '//div[contains(@data-e2e, "im-list")]',
        '//div[contains(@class, "im-container")]',
    ]
    for xpath in xpaths:
        try:
            elements = driver.find_elements(By.XPATH, xpath)
            if any(element.is_displayed() for element in elements):
                return True
        except Exception:
            continue

    # 兜底：URL 含 /chat 且持有会话 cookie（未登录时不会有 sessionid）
    try:
        body = driver.execute_script('return document.body.innerText||"";') or ''
    except Exception:
        body = ''
    # 页面仍渲染登录入口时，无论 URL/cookie 如何都判为未登录（防止导航瞬间误判）
    login_markers = ('Log in to Douyin', 'Scan to Log In', 'Use Phone', 'Use Password',
                     '扫码登录', '登录抖音', '手机号登录')
    if any(marker in body for marker in login_markers):
        return False
    try:
        has_session = any((cookie.get('name') in AUTH_COOKIE_NAMES) for cookie in driver.get_cookies())
        return '/chat' in (driver.current_url or '') and has_session
    except Exception:
        return False


# ==================== 风控 / 登录失效检测 ====================
# 参考开源项目的做法：发送前先确认页面没被风控、登录没掉。
# 这两类问题继续往下做只会招来更严的限制，所以宁可整轮停下并通知用户。
RISK_MARKERS = ('安全验证', '完成验证', '验证身份', '操作频繁', '请稍后再试')
LOGIN_REQUIRED_MARKERS = ('扫码登录', '验证码登录', '登录后即可', 'Log in to Douyin', 'Scan to Log In')

RISK_DETECT_JS = """
var markers = arguments[0], hits = [];
var nodes = document.querySelectorAll('div, span, p, h1, h2, h3, button, a');
for (var i = 0; i < nodes.length; i++) {
  var el = nodes[i];
  if (el.children.length > 2) continue;          // 只看叶子附近的节点，避免大容器整段命中
  var text = (el.innerText || '').trim();
  if (!text || text.length > 40) continue;
  var rect = el.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) continue;
  for (var j = 0; j < markers.length; j++) {
    if (text.indexOf(markers[j]) >= 0) { hits.push(markers[j]); break; }
  }
  if (hits.length > 0) return hits[0];
}
return null;
"""


def detect_risk_control():
    """页面是否出现风控/安全验证提示，返回命中的标记文字（没有则 None）。"""
    if driver is None:
        return None
    try:
        hit = driver.execute_script(RISK_DETECT_JS, list(RISK_MARKERS))
    except Exception:
        return None
    if hit:
        return hit
    try:
        url = driver.current_url or ''
    except Exception:
        url = ''
    if 'verify' in url or 'captcha' in url:
        return '页面跳转到验证地址'
    return None


def detect_login_lost():
    """登录是否已失效（返回说明文字，没失效返回 None）。"""
    if driver is None:
        return '浏览器未初始化'
    try:
        body = driver.execute_script('return document.body.innerText||"";') or ''
    except Exception:
        body = ''
    if body and any(marker in body for marker in LOGIN_REQUIRED_MARKERS):
        return '抖音要求重新登录（登录状态已失效）'
    if not _detect_logged_in_state():
        return '抖音登录状态已失效'
    return None


# ==================== 发送前的统一体检 ====================
# 一旦命中风控或登录失效，会把「发送」整体暂停一段时间：继续重试只会加重风控，
# 而且用户看不到任何提示。暂停状态写进信息日志并推送通知（如已配置）。
_pause_lock = threading.Lock()
_send_paused_until = 0
_send_paused_reason = ''
RISK_PAUSE_SECONDS = 1800
AUTH_PAUSE_SECONDS = 600


def _pause_sending(reason, seconds):
    global _send_paused_until, _send_paused_reason
    with _pause_lock:
        _send_paused_until = max(_send_paused_until, time.time() + seconds)
        _send_paused_reason = reason


def _clear_send_pause():
    global _send_paused_until, _send_paused_reason
    with _pause_lock:
        _send_paused_until = 0
        _send_paused_reason = ''


def _send_pause_reason():
    """当前是否处于暂停窗口，是则返回可读原因。"""
    with _pause_lock:
        if _send_paused_until > time.time():
            remain = int(_send_paused_until - time.time())
            return '%s（已暂停发送，约 %d 分钟后自动恢复；也可重新登录后手动清除）' % (
                _send_paused_reason or '发送已暂停', max(1, remain // 60))
    return None


def send_precheck(notify_on_pause=True):
    """发送前体检：返回 None 表示可以发；否则返回不能发送的原因。"""
    paused = _send_pause_reason()
    if paused:
        return paused
    if driver is None:
        return '浏览器未初始化'
    risk = detect_risk_control()
    if risk:
        reason = '被抖音风控拦截（页面提示「%s」）' % risk
        _pause_sending(reason, RISK_PAUSE_SECONDS)
        log_event('error', '风控', '检测到风控，已暂停所有发送', reason)
        if notify_on_pause:
            notify('抖音风控告警', '%s。已自动暂停发送 30 分钟，请人工完成验证后再继续。' % reason, force=True)
        return reason
    login_problem = detect_login_lost()
    if login_problem:
        _pause_sending(login_problem, AUTH_PAUSE_SECONDS)
        log_event('error', '登录', '登录状态异常，已暂停所有发送', login_problem)
        if notify_on_pause:
            notify('抖音登录失效', '%s。为保证不会把消息发错人，已暂停发送，请到设置页重新登录。'
                   % login_problem, force=True)
        return login_problem
    return None


# ==================== 浏览器操作串行化 ====================
# WebDriver 会话不是线程安全的：前端在按秒轮询（GetFriendsList / GetScrlk / GetLogin），
# 如果这些请求和一次发送交错执行，会出现元素失效、拿到别人页面状态之类的怪问题。
# 旧实现只在「初始化浏览器」时加了锁，其余操作全是裸奔，这里统一串起来。
BROWSER_LOCK_WAIT_SECONDS = _env_int('SPARK_LOCK_WAIT_SECONDS', 15)


_panel_cache = {}
_panel_cache_lock = threading.Lock()


def _cache_get(key):
    with _panel_cache_lock:
        item = _panel_cache.get(key)
        return dict(item) if isinstance(item, dict) else None


def _cache_put(key, value):
    if not isinstance(value, dict) or value.get('code') not in (200, '200'):
        return
    stored = dict(value)
    stored.pop('stale', None)
    with _panel_cache_lock:
        _panel_cache[key] = stored


def serialized(func=None, *, wait=None, cache=None):
    """给操作浏览器的接口加串行化保护（保留原签名，FastAPI 依赖注入不受影响）。

    与旧实现的关键差别：拿不到锁就快速失败，而不是无限排队。
    这些路由是同步 def，跑在 FastAPI 的 AnyIO 线程池里（默认 40 个额度），
    而前端在按秒轮询。一次发送持锁最长可能到 60 秒，旧写法会让所有轮询请求
    都堵在锁上、白占线程池额度；额度一满，连登录接口都拿不到线程，整个面板打不开
    （表现就是「面板忽然登录不上、nginx 502」）。
    这里等 BROWSER_LOCK_WAIT_SECONDS 秒拿不到就返回 409，让调用方自己去重试。

    另外用的是 RLock：同一个线程内嵌套加锁（发送流程内部会再进 ensure_browser_ready）
    不会自锁。

    wait= 可以给单个路由覆盖等待时长。默认 15 秒对「一次点击」级别的接口合适，
    但有些接口天然慢（发短信验证码要等抖音响应），有些则被前端按秒轮询、
    多等一秒都是白占线程 —— 这两类各自需要不同的预算，所以做成参数而不是全局常量。

    支持两种写法：@serialized 和 @serialized(wait=5)。
    cache= 给只读轮询用：浏览器正忙时返回上一份成功结果，并带上 stale=True。
    返回缓存前仍要先过鉴权，避免忙的时候把截图或好友列表漏给未登录请求。
    """
    def decorate(target):
        @functools.wraps(target)
        def wrapper(*args, **kwargs):
            budget = BROWSER_LOCK_WAIT_SECONDS if wait is None else wait
            if not browser_lock.acquire(timeout=budget):
                auth_err = require_auth(kwargs.get('authorization'))
                if auth_err:
                    return auth_err
                if cache:
                    cached = _cache_get(cache)
                    if cached is not None:
                        cached['stale'] = True
                        return cached
                return {'code': 409, 'data': '浏览器正忙（可能正在发送消息），请稍后再试'}
            try:
                result = target(*args, **kwargs)
                if cache:
                    _cache_put(cache, result)
                return result
            finally:
                browser_lock.release()
        return wrapper

    if func is not None:
        return decorate(func)
    return decorate


def human_pause(low, high):
    """随机停顿一下，避免每次都卡在同一节拍上（固定节奏更容易被风控盯上）。"""
    try:
        time.sleep(random.uniform(low, high))
    except Exception:
        pass


# ==================== 发送记账（防重复发送） ====================
# 参考开源项目的做法：发送前先记账，成功再改成 success；结果不确定的当天也不再重发 ——
# 「报失败但其实已送达」时再补一次，对方就会收到两条。
# 判定逻辑（键的构造、作用域语义）都在 spark_core，这里只负责读写 state.json。
# 手动发送不再有「冷却时间」这类拦人机制，只有定时链路还会按天拦重复。
HISTORY_KEEP_DAYS = 7


def _history_key(name, text=None):
    """记账键 = 日期 + 好友。

    旧实现把文案的哈希也拼进键里，于是「改一下任务文案」或「每天随机文案」
    就能绕过当天去重，同一天给同一个人发两次。键只看日期和好友，
    文案本身作为记录内容存下来，这才符合「今天已经发过就别再发」的本意。
    """
    return history_key(today_str(), name)


# 记账是「读出来 -> 改一条 -> 整体写回」，这是一段 read-modify-write：
# 两个调用点并发时会互相覆盖（丢 success 记录 → 当天可能再发一条；丢 unknown
# → 去重信息缺失）。现存调用点恰好都在 browser_lock / @serialized 里，所以一直是
# 「偶然安全」；这里加一把专用锁，让正确性不再依赖调用方的自觉。
_history_lock = threading.RLock()


def _history_mark(name, text, status, detail=None, kind=None):
    with _history_lock:
        history = history_clean(STATE.get('send_history') or {}, today_str(), HISTORY_KEEP_DAYS)
        history[_history_key(name)] = make_history_record(
            status, text=text, detail=detail, kind=kind)
        return STATE.set('send_history', history)


def _history_recent(name, text=None):
    """同一位好友当天的发送记录（没有则 None）。"""
    with _history_lock:
        history = STATE.get('send_history') or {}
        return history.get(_history_key(name))


def send_guard(name, text, scope='task'):
    """发送前的重复检查，返回阻止原因（None = 放行）。

    scope='task'   定时 / 补跑 / 补发：当天已经发过（含结果不确定）就跳过，绝不重复打扰对方；
    scope='manual' 手动发送：一律放行。

    手动发送不再做任何重复拦截（原来是 90 秒冷却）：面板上那一下是人自己点的，
    再点一次就是想再发一次；把它拦下来只会让人以为程序坏了。需要防重复的是定时
    那条链路，所以 task 作用域的「当天已发过就跳过」保持不变。
    """
    if scope == 'manual':
        return None
    reason = guard_decision(_history_recent(name), scope)
    if reason:
        return '「%s」：%s' % (name, reason)
    return None


# ==================== 错误分级与重试 ====================
# 这一段（classify_error / retry_delay / should_stop_round / retryable 与各 HINTS 常量）
# 已整体搬到 spark_core：那边不依赖 selenium，可以脱离浏览器直接跑单测。
# 而且旧实现的问题不只是「放错地方」—— 这几个函数在 backend.py 里压根没有任何调用点，
# 注释宣称的「临时性错误会自动重试」其实从未发生。现在调度 worker 真的会用它：
# 只有 retryable() 判为临时性失败的才进「当日补发」队列，风控/登录失效则直接停整轮。


# ==================== 登录失败节流 ====================
# 面板登录接口没有失败限制时可以被在线爆破；这里对同一来源做窗口计数。
LOGIN_FAIL_LIMIT = 5
LOGIN_LOCK_SECONDS = 600
_login_failures = {}
_login_locked = {}


def _client_ip(request):
    """取真实来源 IP（只采信可信反代传来的头）。

    本服务监听 127.0.0.1，正常都跑在 nginx 之后，直接用 request.client.host
    拿到的永远是 127.0.0.1 —— 那样所有访问者共用一个失败计数，节流形同虚设。
    但反过来无脑信任 X-Real-IP / X-Forwarded-For 也不对：能直连 9844 的人
    （或者反代用了 $proxy_add_x_forwarded_for 透传时）每次换一个头就能拿到全新额度，
    「失败 5 次锁定」就完全失效了。
    所以只有在请求确实来自可信反代地址时，才采信这两个头。
    """
    if request is None:
        return '127.0.0.1'
    try:
        peer = (request.client.host if request.client else '') or '127.0.0.1'
        if peer not in TRUSTED_PROXIES:
            return peer
        headers = request.headers or {}
        real_ip = (headers.get('x-real-ip') or '').strip()
        if real_ip:
            return real_ip
        forwarded = (headers.get('x-forwarded-for') or '').strip()
        if forwarded:
            return forwarded.split(',')[0].strip()
        return peer
    except Exception:
        return '127.0.0.1'


def _prune_login_throttle(now=None):
    """清理过期的失败/锁定记录：这两张表旧实现只增不减，长期运行会一直涨。"""
    moment = now or time.time()
    for ip, until in list(_login_locked.items()):
        if until <= moment:
            _login_locked.pop(ip, None)
    for ip, stamps in list(_login_failures.items()):
        kept = [stamp for stamp in stamps if moment - stamp < LOGIN_LOCK_SECONDS]
        if kept:
            _login_failures[ip] = kept
        else:
            _login_failures.pop(ip, None)


def _login_throttle_remaining(ip):
    """该来源是否还在锁定期，返回剩余秒数（0 = 可以尝试）。"""
    until = _login_locked.get(ip, 0)
    return int(until - time.time()) if until > time.time() else 0


def _record_login_failure(ip):
    """记录一次失败；达到阈值则锁定并返回 True。"""
    now = time.time()
    _prune_login_throttle(now)
    window = [stamp for stamp in _login_failures.get(ip, []) if now - stamp < LOGIN_LOCK_SECONDS]
    window.append(now)
    if len(window) >= LOGIN_FAIL_LIMIT:
        _login_locked[ip] = now + LOGIN_LOCK_SECONDS
        _login_failures.pop(ip, None)
        return True
    _login_failures[ip] = window
    return False


def _clear_login_failures(ip):
    _login_failures.pop(ip, None)
    _login_locked.pop(ip, None)


def ensure_browser_ready():
    global init, driver, douyin, Login_is_bool, _browser_version
    with browser_lock:
        if init and _driver_alive():
            return None
        if init and not _driver_alive():
            _reset_browser_state()
        try:
            if os.name != 'nt':
                display_value = ensure_display_env()
                if not display_value:
                    return {'code': 500, 'data': '初始化失败: 未找到可用的 DISPLAY/Xvfb 环境'}
                cleanup_stale_browser_processes()
            options = build_chrome_options()
            driver = webdriver.Chrome(service=service, options=options) if service else webdriver.Chrome(options=options)
            driver.set_window_size(1400, 3200)
            try:
                _browser_version = str((driver.capabilities or {}).get('browserVersion') or '')
            except Exception:
                _browser_version = ''
            # 必须在这里注入：脚本要赶在抖音页面自己的 JS 之前挂上去。
            # 失败不阻止初始化（能登录能发送），但安全中心会报警。
            _inject_stealth_js(driver)
            driver.get('https://www.douyin.com/chat?isPopup=1 ')
            # 刚建出来的浏览器当然是活的，但 _driver_alive(probe=False) 只读心跳，
            # 而心跳要等下一轮调度 tick 才会被刷新。中间这段时间 /healthz 会误报
            # browser_ready=false，安全中心也会误报「会话已失效」。
            # 此刻正持着 browser_lock，可以安全地真碰一次浏览器把心跳立起来。
            _driver_alive(probe=True)
            douyin = Douyin(driver)
            init = True
            Login_is_bool = _detect_logged_in_state()
            # 调度线程不再挂在这里：它由应用 lifespan 启动。
            # 旧实现把「启动调度」绑在浏览器初始化上 —— 没初始化过浏览器就永远不会调度，
            # 而任务明明已经存在（重启后用户不去点初始化，当天的火花就静默漏发了）。
            return None
        except SessionNotCreatedException as e:
            _reset_browser_state()
            if "This version of Microsoft Edge WebDriver only supports" in str(e):
                return {'code': 400, 'data': '需要更新浏览器驱动!'}
            log_event('error', '浏览器', '浏览器会话创建失败', e)
            return {'code': 400, 'data': '浏览器会话创建失败: %s' % exc_text(e)}
        except Exception as e:
            _reset_browser_state()
            log_event('error', '浏览器', '浏览器初始化失败', e)
            return {'code': 500, 'data': '初始化失败: %s' % exc_text(e)}


def require_browser_session():
    """确认浏览器会话可用；不可用时给出可直接展示的提示。

    探测本身有讲究（见 _driver_alive 的 docstring）：
      * 已经在一个 @serialized 路由里时，本线程持有 browser_lock，此时真探一次是安全的
        （锁内没有在途的发送，不会与别人交错）；
      * 调度线程维护的心跳可能刚好过期（上一个 tick 在发送、心跳保持上一次结果），
        所以「心跳过期」不等于「浏览器死了」，必须再真探一次才能下结论 —— 但**不能在
        拿不到锁的时候下结论**：拿不到锁说明有人正在操作浏览器，那恰恰证明它活着。
    旧实现两条 if 各探一次，而且拿不到锁也照杀会话：一次瞬时失败就 old_driver.quit()，
    正在发送的消息直接变成「结果未确认」（当天不再重发 = 漏一天）。
    """
    if not init:
        return {'code': 400, 'data': '浏览器未初始化，请先初始化浏览器'}
    if _driver_alive(probe=False):
        return None
    acquired = browser_lock.acquire(timeout=0.5)
    if not acquired:
        # 别人正拿着锁操作浏览器：说明它活着，这次不打扰、也不重置
        return {'code': 409, 'data': '浏览器正忙（可能正在发送消息），请稍后再试'}
    try:
        if _driver_alive(probe=True):
            return None
    finally:
        browser_lock.release()
    _reset_browser_state()
    return {'code': 400, 'data': '浏览器会话已失效，请重新初始化浏览器'}


def _auto_init_browser_worker():
    """开机自动把浏览器拉起来，让面板真正无人值守。

    旧行为：服务重启后必须有人打开面板点一次「初始化浏览器」，
    否则所有定时任务到点都发不出去（表现为「日志显示到点了，但没有发送记录」）。
    这里只在「已经有定时任务」时才动手，避免纯管理用途也常驻一个 Chrome。
    """
    try:
        if _browser_ready_for_send():
            return
        log_event('info', '浏览器', '检测到已有定时任务，正在自动初始化浏览器…')
        err = ensure_browser_ready()
        if err:
            detail = err.get('data') if isinstance(err, dict) else err
            log_event('error', '浏览器', '自动初始化失败，可在首页手动重试', detail)
        else:
            log_event('success', '浏览器', '自动初始化完成，定时任务可以正常发送了')
    except Exception as exc:
        log_event('error', '浏览器', '自动初始化异常，可在首页手动重试', exc)


start_time = datetime.now()


# 抖音操作
@app.get('/Home')
def Home(authorization: str = Header(None)):
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    return {
        'code': 200,
        'time': start_time,
        'uptime_seconds': int((datetime.now() - start_time).total_seconds()),
        'must_change_password': using_default_password(),
        'pause': _send_pause_reason(),
    }


@app.get('/Api/Init')  # 初始化浏览器
@serialized
def Init(authorization: str = Header(None)):
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    setup_guard_err = setup_guard()
    if setup_guard_err:
        return setup_guard_err

    if init and _driver_alive():
        return {'code': 200, 'data': 'init Repeated!'}
    init_err = ensure_browser_ready()
    if init_err:
        detail = init_err.get('data') if isinstance(init_err, dict) else init_err
        log_event('error', '浏览器', '浏览器初始化失败', detail)
        return init_err
    log_event('success', '浏览器', '浏览器初始化完成，可以登录抖音了')
    return {'code': 200, 'data': 'success'}


@app.get('/Api/GetInit')
# 前端按秒轮询这个接口。忙的时候直接回上一份结果，不要占着锁干等。
@serialized(wait=0.2, cache='init')
def GetInit(authorization: str = Header(None)):
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    if init and not _driver_alive(probe=False):
        # 这里只读心跳：GetInit 是前端按秒轮询的，不能每次都真发一次 WebDriver 请求，
        # 更不能在别人正发送时把会话重置掉（拿不到锁时 _refresh_browser_heartbeat 会保守地保留旧心跳）
        _reset_browser_state()
    return {'code': 200, 'data': 'Yes' if init else 'No'}


# 抖音登录凭证 Cookie 名（参考 MinG-98/prometheus-relay 的 AUTH_COOKIE_NAMES）
AUTH_COOKIE_NAMES = frozenset(
    {'sessionid', 'sessionid_ss', 'sid_guard', 'sid_tt', 'uid_tt', 'uid_tt_ss'}
)
COOKIE_ALLOWED_FIELDS = frozenset(
    {'name', 'value', 'domain', 'path', 'expires', 'httpOnly', 'secure', 'sameSite'}
)

# 手机号登录提交后，等登录态真正稳定下来再看结果。
# 为什么需要重试而不是 sleep 一次就判：提交验证码之后页面会跳转、会话列表要加载，
# 这段时间里 _detect_logged_in_state() 可能两边都查不到（既没渲染出会话列表、
# 也还没拿到 sessionid）。宁可多等两秒，也不能把「还没加载完」判成「没登录」。
LOGIN_CONFIRM_ATTEMPTS = 8
LOGIN_CONFIRM_POLL = 0.5


def sanitise_cookies(cookies):
    """清洗导入的 Cookie：只保留抖音域下的合法字段，且必须含登录凭证。

    返回 [] 表示这份 Cookie 无法用于登录（避免出现「导入成功但实际未登录」）。
    """
    if not isinstance(cookies, (list, tuple)):
        return []
    clean = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        name, value = cookie.get('name'), cookie.get('value')
        if not isinstance(name, str) or not name:
            continue
        if not isinstance(value, str) or not value:
            continue
        domain = str(cookie.get('domain') or '').lower().lstrip('.')
        if domain and domain != 'douyin.com' and not domain.endswith('.douyin.com'):
            continue
        clean.append({key: cookie[key] for key in COOKIE_ALLOWED_FIELDS if key in cookie})
    if not clean or not AUTH_COOKIE_NAMES.intersection(c['name'] for c in clean):
        return []
    return clean


@app.post('/Api/login')  # 登录 传入cooke
@serialized
def Login(cooke: str = Body(default=None), gzip_flag: bool = Body(default=False), authorization: str = Header(None)):
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    setup_guard_err = setup_guard()
    if setup_guard_err:
        return setup_guard_err
    browser_err = require_browser_session()
    if browser_err:
        return browser_err
    global Login_is_bool
    if cooke:
        try:
            decoded_bytes = base64.b64decode(cooke)
            if gzip_flag:
                try:
                    decoded_bytes = gzip.decompress(decoded_bytes)
                except Exception:
                    return {'code': '404',
                            'data': 'login-error-gzip decompress failed, check cookie format and gzip flag'}
            cookie_list = decoded_bytes.decode('utf-8')
            # 安全修复：原实现用 eval() 执行请求体内容，可被构造为任意代码执行。
            # 现在直接按 JSON 解析（导出端本来就是 json.dumps 出来的），再校验域名/字段/凭证。
            # 不要退回「把 true/false 全文替换后 ast.literal_eval」的老写法：
            # cookie 值里出现 true/false 子串（base64 字母表里很常见）会被静默改成 True/False，
            # 导入的会话就跟导出时不一致了。
            parsed = json.loads(base64.b64decode(cookie_list).decode('utf-8'))
            cookies = sanitise_cookies(parsed)
            if not cookies:
                return {'code': '404',
                        'data': 'login-error-cookie 中不含抖音登录凭证（sessionid 等），或域名不合法'}
            for cookie in cookies:
                driver.add_cookie(cookie)
        except Exception as e:
            return {'code': '404', 'data': f'login-error-cookie parse error: {str(e)}'}
        driver.refresh()
        try:
            login_type_element = driver.find_element(By.XPATH, '//*[@id="douyin_login_comp_flat_panel"]/picture')
            login_type = login_type_element.text
            log_event('error', '登录', 'Cookie 登录失败：抖音仍显示登录面板')
            return {'code': '404', 'data': 'login-error-cooker cant login'}
        except NoSuchElementException:
            log_event('success', '登录', 'Cookie 导入登录成功')
            Login_is_bool = True
            return {'code': '200', 'data': 'ok'}
    else:
        return {'code': '404', 'data': 'login-error-not cooker'}  # # @#z


@app.get('/Api/Pnglogin')  # 扫码登录
@serialized
def PngLogin(authorization: str = Header(None)):
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    setup_guard_err = setup_guard()
    if setup_guard_err:
        return setup_guard_err
    browser_err = require_browser_session()
    if browser_err:
        return browser_err
    global Login_is_bool
    time.sleep(1)
    if _detect_logged_in_state():
        if not Login_is_bool:
            log_event('success', '登录', '扫码登录成功')
        Login_is_bool = True
        return {'code': '200', 'data': 'ok'}
    if _is_two_factor_page():
        Login_is_bool = False
        log_event('warn', '登录', '登录需要二次验证（短信/刷脸/密码），请在设置页完成验证')
        return {'code': 202, 'data': 'two_factor_required'}
    cooke = driver.get_cookies()
    if cooke:
        try:
            login_type_element = driver.find_element(By.XPATH, '//*[@id="douyin_login_comp_flat_panel"]/picture')
            login_type = login_type_element.text
            return {'code': '404', 'data': 'login-error-qr-not-confirmed'}
        except NoSuchElementException:
            log_event('success', '登录', '扫码登录成功')
            Login_is_bool = True
            return {'code': '200', 'data': 'ok'}
    else:
        return {'code': '404', 'data': 'login-error-not cooker'}  # # @#z


@app.get('/Api/GetLogin')  # 获取登录
@serialized(wait=0.2, cache='login')
def GetLogin(authorization: str = Header(None)):
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    global Login_is_bool
    # 只读心跳：与 GetInit 同理 —— 这个接口被前端按秒轮询，不该真发 WebDriver 请求
    if init and not _driver_alive(probe=False):
        _reset_browser_state()
    if init:
        Login_is_bool = _detect_logged_in_state()
    return {'code': 200, 'data': 'Yes' if Login_is_bool else 'No'}


QR_CONTAINER_SELECTOR = '#animate_qrcode_container'


def _qr_container():
    """返回扫码登录容器（优先可见元素）。"""
    try:
        elements = driver.find_elements(By.CSS_SELECTOR, QR_CONTAINER_SELECTOR)
    except Exception:
        return None
    for element in elements:
        try:
            if element.is_displayed():
                return element
        except Exception:
            continue
    return elements[0] if elements else None


def _qr_img_src():
    """兼容新旧 DOM，取二维码图片的 data URI。"""
    selectors = (
        (By.CSS_SELECTOR, '#animate_qrcode_container img'),
        (By.XPATH, '//*[@id="animate_qrcode_container"]//img'),
    )
    for by, selector in selectors:
        try:
            for element in driver.find_elements(by, selector):
                src = element.get_attribute('src')
                if src and len(src) > 64:
                    return src
        except Exception:
            continue
    return None


def _qr_refresh_element():
    """二维码过期时页面会渲染 Refresh / 刷新 元素，返回该可见元素。"""
    candidates = [
        '//*[@id="animate_qrcode_container"]//p[normalize-space()="Refresh"]',
        '//*[@id="animate_qrcode_container"]//*[normalize-space()="Refresh"]',
        '//*[@id="animate_qrcode_container"]//*[contains(normalize-space(), "刷新")]',
    ]
    for xpath in candidates:
        try:
            for element in driver.find_elements(By.XPATH, xpath):
                if element.is_displayed():
                    return element
        except Exception:
            continue
    return None


def _qr_expired():
    """判断当前二维码是否已过期：容器文案命中关键词，或存在 Refresh 元素。"""
    container = _qr_container()
    try:
        text = (container.text or '').lower() if container is not None else ''
    except Exception:
        text = ''
    markers = ('expired', '过期', '失效', 'refresh', '刷新')
    if any(marker in text for marker in markers):
        return True
    return _qr_refresh_element() is not None


def _click_qr_refresh():
    """点一下二维码上的刷新控件（存在才点）。"""
    element = _qr_refresh_element()
    if element is None:
        return False
    try:
        element.click()
        return True
    except Exception:
        try:
            driver.execute_script('arguments[0].click();', element)
            return True
        except Exception:
            return False


def _hover_qr():
    """把鼠标移到二维码上：部分版本的刷新控件是 hover 才出现的。"""
    container = _qr_container()
    if container is None:
        return False
    try:
        from selenium.webdriver.common.action_chains import ActionChains
        ActionChains(driver).move_to_element(container).perform()
        time.sleep(0.8)
        return True
    except Exception:
        return False


def _ensure_fresh_qr_src(force=False):
    """返回可用的二维码 data URI；force=True 时无条件尝试换一张新码。

    注意：以前「刷新」是没用的 —— 二维码没过期时这里直接返回同一个 src，
    前端拿到一模一样的图，用户看到的就是「点了没反应」。force 必须真的去点刷新控件。
    """
    container = _qr_container()
    if container is None:
        return None
    src = _qr_img_src()
    if src and not force and not _qr_expired():
        return src
    old_src = src
    # 依次尝试：直接点刷新控件 -> 悬停后再找一遍再点
    if not _click_qr_refresh():
        if _hover_qr():
            _click_qr_refresh()
    deadline = time.time() + 12
    while time.time() < deadline:
        time.sleep(0.6)
        new_src = _qr_img_src()
        if new_src and new_src != old_src and not _qr_expired():
            return new_src
    # 兜底：拿到的还是同一张（或刷新控件不存在）。只要它现在仍然有效就照常返回，
    # 由前端对比前后 src 决定文案，如实告诉用户「没有变化」，不谎报刷新成功。
    current = _qr_img_src()
    if current and not _qr_expired():
        return current
    return None


@app.get('/Api/login/Init/GetLoginPng')  # 获取登录扫码
@serialized(wait=0.4, cache='qr')
def GetLoginPng(refresh: int = 0, authorization: str = Header(None)):
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    setup_guard_err = setup_guard()
    if setup_guard_err:
        return setup_guard_err
    browser_err = require_browser_session()
    if browser_err:
        return browser_err
    global Login_is_bool
    if _detect_logged_in_state():
        Login_is_bool = True
        return {'code': 200, 'data': '', 'status': 'already_logged_in'}
    # 不在扫码页时先切回扫码标签
    if _qr_img_src() is None:
        try:
            Douyin.LoginInit(douyin)
            time.sleep(1)
        except Exception:
            pass
    login_src = _ensure_fresh_qr_src(force=bool(refresh))
    if login_src:
        return {'code': 200, 'data': login_src, 'status': 'ok'}
    if _detect_logged_in_state():
        Login_is_bool = True
        return {'code': 200, 'data': '', 'status': 'already_logged_in'}
    return {'code': 404, 'data': 'cant find LoginPng src attribute'}


@app.post('/Api/login/Init/GetCooker')  # 获取cooke
@serialized
def GetCooke(password: str = Body(default=None, embed=True), authorization: str = Header(None)):
    """导出 Cookie。

    安全修复：旧实现是 GET + query string，面板密码会明文写进 nginx access log
    和程序日志（实测抓到过 `?password=***`）；改为 POST 请求体传递。
    注意 embed=True：请求体形如 {"password": "..."}，否则 FastAPI 会把整个
    JSON 对象当成 password 字符串，直接 422。
    """
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    # 先验密码再看浏览器：否则密码输错也会显示「浏览器未初始化」，既误导用户
    # 又把「浏览器是否已就绪」暴露给未通过密码校验的调用方。
    if not password or not check_password(password):
        return {'code': 400, 'data': '密码错误'}
    setup_guard_err = setup_guard()
    if setup_guard_err:
        return setup_guard_err
    browser_err = require_browser_session()
    if browser_err:
        return browser_err
    if Login_is_bool:
        cooke = driver.get_cookies()
        cookie_json = json.dumps(cooke)
        cookie_base64 = base64.b64encode(cookie_json.encode('utf-8')).decode('utf-8')
        return {'code': 200, 'data': {'cooke': cookie_base64}}
    else:
        return {'code': 400, 'data': '未登录'}


@app.get('/Api/GetFriendsList')  # 获取好友列表
@serialized(wait=0.4, cache='friends')
def GetFrindesList(authorization: str = Header(None)):
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    setup_guard_err = setup_guard()
    if setup_guard_err:
        return setup_guard_err
    browser_err = require_browser_session()
    if browser_err:
        return browser_err
    try:
        friends_list = douyin.Updara_FrinderList()
        if len(friends_list) == 0:
            return {'code': 404, 'data': '暂无好友或页面未加载'}
        dicts = {}
        ambiguous = []
        for v in friends_list:
            if getattr(v, 'ambiguous', False):
                if v.username not in ambiguous:
                    ambiguous.append(v.username)
                continue
            dicts[v.username] = [v.avatar, v.fire]
        return {'code': 200, 'data': {'count': len(friends_list), 'list': dicts, 'ambiguous': ambiguous}}
    except Exception as e:
        log_event('error', '好友列表', '读取好友列表失败', e)
        return {'code': 404, 'data': '读取好友列表失败：%s' % exc_text(e)}


def _send_now(name, text, image_path=None, record_text=None, scope='manual',
              category='发消息', success_text='发送成功', kind='manual'):
    """真实执行一次发送并按（日期 + 好友）记账，返回接口响应 dict。

    手动发送和「立即试发」（/Time/test）走的是同一段代码：差别只在 scope
    （'manual' = 只防手滑连点；'task' = 当天已发过就跳过）与日志/提示文案。
    鉴权和前置准备（取签、下载图片、解析文案）由调用方负责，因为各路由的
    校验顺序不一样（比如 /Time/test 要先确认任务存在）。
    """
    if record_text is None:
        record_text = text or ''
    # 体检（风控 / 登录失效）与防连点检查都放在串行化区间里：
    # 连点两次时第二次会排队到第一次结束之后，那时记账已写入，才能正确拦住重复发送。
    problem = send_precheck()
    if problem:
        return {'code': 404, 'data': '已暂停发送：%s' % problem}
    blocked = send_guard(name, record_text, scope=scope)
    if blocked:
        log_event('warn', category, '已拦住一次重复发送', {'好友': name, '原因': blocked})
        return {'code': 404, 'data': blocked}
    _history_mark(name, record_text, 'unknown', kind=kind)
    try:
        out = Douyin.Send_Frinder(douyin, name, text or '', image=image_path)
    except Exception as exc:
        _history_mark(name, record_text, 'unknown', exc, kind=kind)
        log_event('error', category, '给「%s」发送时出错' % name, exc)
        return {'code': 404, 'data': '发送时出错: %s' % exc_text(exc)}
    if out is None:
        # 兜底：Send_Frinder 任何分支都应返回 TrueString，这里保证接口层不会 500
        _history_mark(name, record_text, 'unknown', '无返回结果', kind=kind)
        log_event('error', category, '给「%s」发送没有返回结果' % name)
        return {'code': 404, 'data': '发送没有返回结果（内部异常）'}
    if out.is_bool:
        _history_mark(name, record_text, 'success', kind=kind)
        detail = dict(getattr(douyin, 'last_send_detail', None) or {})
        detail['内容'] = record_text
        log_event('success', category, '已发送给「%s」' % name,
                  json.dumps(detail, ensure_ascii=False) if detail else record_text)
        return {'code': 200, 'data': success_text}
    # 结果不确定时记 'unknown'：当天不再自动重发，避免对方收到两条。
    # 归类规则同样走 spark_core（见 run_scheduled_send 处的注释）。
    _history_mark(name, record_text, send_status_from_reason(out.string), out.string, kind=kind)
    log_event('error', category, '给「%s」发送失败' % name, out.string)
    return {'code': 404, 'data': out.string}


@app.post('/Api/Send')
@serialized
def Send(payload: dict = Body(default=None), name: str = None, text: str = None,
         authorization: str = Header(None)):
    """手动给某位好友发一条消息。

    从 GET + query 改成 POST + 请求体：好友昵称和消息正文都是隐私内容，
    放 query string 会被原样写进 nginx access log、浏览器历史和 Referer。
    """
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    setup_guard_err = setup_guard()
    if setup_guard_err:
        return setup_guard_err
    browser_err = require_browser_session()
    if browser_err:
        return browser_err
    body = payload or {}
    name = body.get('name', name)
    text = body.get('text', text)
    # sign='wenchang' 表示「附带一张文昌帝君灵签图 + 当天签文」。
    # 勾选后文案可以留空：留空就用签文本身当文字。
    sign_flag = _bool_flag(body.get('sign'))
    # image_only：只发图，不附任何文字（配合 sign 就是「只发签图」）。
    image_only = _bool_flag(body.get('image_only'))
    image_url = str(body.get('image_url') or '').strip()
    sign_data = fetch_wenchang_sign() if sign_flag else None
    if sign_flag and sign_data is None:
        # 用户明确点了「灵签」，此时静默换成一句随机文案是骗人，直接报错让他自己决定。
        return {'code': 404, 'data': '取文昌帝君灵签失败：接口没有返回可用签文，'
                                     '请稍后再试，或改用普通文案发送'}
    sign_label = None
    if sign_data:
        sign_label = sign_summary(sign_data)
        if not (text or '').strip():
            text = render_sign_text(sign_data)
        if not image_url:
            image_url = sign_data.get('pic') or ''
    # source='hitokoto'：正文改成「现取一句一言」。
    # 这里严格比字符串、不做 _bool_flag 那种宽松解析：写成 'yes'/1 这种值时静默退化成
    # 普通文案，用户会以为自己发的是一言，其实是自己填的那句 —— 宁可不生效也不要骗人。
    # 放在灵签之后，是为了让「取到了」优先于签文/用户文案，和定时任务的优先级一致。
    if str(body.get('source') or '').strip().lower() == 'hitokoto':
        hitokoto_text = fetch_hitokoto()
        if hitokoto_text:
            text = hitokoto_text
        else:
            log_event('warn', '一言', '给「%s」取一言失败，本次退回你填写的文案' % (name or '好友'))
            if not (text or '').strip() and not image_url:
                # 兜底链的最后一级：一句话都取不到也得有内容，不能因此整条不发
                text = random.choice(FALLBACK_MESSAGES)
    # source='girlfriend'：正文改成「按当前时段渲染的天气问候」（面板上的手动发送也支持）。
    # 口径与定时任务、/Api/Girlfriend/Preview 完全一致：渲染失败就退回用户填的文案。
    if str(body.get('source') or '').strip().lower() == 'girlfriend':
        girlfriend_text = render_girlfriend_text()
        if girlfriend_text:
            text = girlfriend_text
        else:
            log_event('warn', '女朋友模式',
                      '给「%s」渲染天气问候失败，本次退回你填写的文案' % (name or '好友'))
            if not (text or '').strip() and not image_url:
                text = random.choice(FALLBACK_MESSAGES)
    if not name or (not (text or '').strip() and not image_url):
        # 允许「只发一张图」：文字和图片是两条独立消息，图片本身就能当内容。
        return {'code': 400, 'data': '好友名和消息内容都要填'}
    image_path = None
    if image_url:
        image_path, image_error = download_image(image_url)
        if image_error:
            # 图片没准备好就整条不发：用户勾的是「图文」，发半条更容易造成误解。
            return {'code': 404, 'data': '图片没准备好，本次没有发送：%s' % image_error}
    if image_only and image_path is None:
        # 「只发图」但根本没有图：报错，而不是把文字当内容发出去（那是另一回事）
        return {'code': 400, 'data': '选了「只发图」，但这次没有可发的图片'
                                     '（没勾灵签、签文接口没返回签图，或没给图片地址）'}
    if image_only:
        # 只发图：用户明确只要那张图，填了文案也不带出去
        # （免得「我只想测一下图能不能发」变成图文两条）。
        text = ''
    # 记账文本带上图片标记：发送记录里一眼能看出这条是图文，而不是只看到签文正文。
    record_text = image_record_text(text, sign_label) if image_path else (text or '')
    return _send_now(name, text, image_path=image_path, record_text=record_text, scope='manual')


@app.get('/Api/Hitokoto/Preview')
def HitokotoPreview(authorization: str = Header(None)):
    """取一句一言给面板预览（「取一条试试」按钮），不发送任何消息。

    这里**不加** @serialized：本函数只发一个外网 HTTP 请求，不排队、不占会话，
    挂上串行锁只会让发送高峰期连预览都点不动。
    """
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    text = fetch_hitokoto()
    if not text:
        return {'code': 400, 'data': '一言接口暂时取不到内容，请稍后再试'}
    return {'code': 200, 'data': {'text': text}}


# ==================== 女朋友模式接口 ====================
# 这四个接口不碰浏览器，所以**不挂 @serialized**：@serialized 会占住全局 browser_lock，
# 而它们最坏要等 4 次外部 HTTP（geo/current/daily/airquality，每次最长 QWEATHER_TIMEOUT），
# 真占住锁约 40 秒，会把正好撞上的定时发送挤成 409「浏览器正忙」。
@app.get('/Api/Girlfriend/Config')
def GetGirlfriendConfig(authorization: str = Header(None)):
    """读取女朋友模式配置。Key 不回明文，只回 key_set 让面板显示「已配置」。"""
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    return {'code': 200, 'data': _girlfriend_view()}


# 同上：纯配置读写，不碰浏览器，不挂 @serialized（别为了一个存配置的请求去占 browser_lock）。
@app.post('/Api/Girlfriend/Config')
def SetGirlfriendConfig(payload: dict = Body(default=None), authorization: str = Header(None)):
    """保存女朋友模式配置：body 是部分字段，未知字段忽略，字符串统一 strip。

    Key 特殊处理（它是凭据）：留空表示「不修改已保存的那一份」——面板拿不到明文，
    留空是常态，不能因此把用户已经存好的 Key 抹掉。
    """
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    body = payload or {}
    if not isinstance(body, dict):
        return {'code': 400, 'data': '请求体必须是一个 JSON 对象'}
    # 先校验、后落盘：日期不合法就直接返回，不会留下「改了一半」的配置
    if 'meet_date' in body and str(body.get('meet_date') or '').strip() \
            and not _valid_meet_date(body.get('meet_date')):
        return {'code': 400, 'data': '相识日期格式应为 YYYY-MM-DD'}
    config = _girlfriend_config()
    old_city = config['city']
    for field in GF_CONFIG_FIELDS:
        if field not in body:
            continue
        value = body.get(field)
        if field == 'enabled':
            config[field] = _bool_flag(value)
            continue
        text_value = '' if value is None else str(value).strip()
        if field == 'key' and not text_value:
            continue
        config[field] = text_value
    if config['city'] != old_city:
        # 城市变了就必须作废缓存的坐标/时区，否则会拿旧城市的天气当成新城市的发出去；
        # city_name / city_adm 是「已解析城市」那行显示用的，一并不作废就会显示旧城市。
        for field in ('lat', 'lon', 'tz', 'city_name', 'city_adm'):
            if field not in body:
                config[field] = ''
    if not STATE.set('girlfriend', config):
        return {'code': 500, 'data': '女朋友模式配置保存失败（请检查 data 目录的权限与磁盘空间），'
                                     '本次修改没有生效'}
    log_event('info', '女朋友模式', '配置已更新', '城市=%s 相识日期=%s' % (
        config['city'] or '（空）', config['meet_date'] or '（空）'))
    return {'code': 200, 'data': _girlfriend_view(config)}


# 同上：这一路只在调外部天气接口，不碰浏览器，不挂 @serialized。
@app.get('/Api/Girlfriend/Weather')
def GirlfriendWeather(refresh: str = None, authorization: str = Header(None)):
    """取一次真实天气并渲染（面板上的「测试天气」）。

    refresh=1 时重新解析城市（用户刚改了城市名，缓存里的坐标必须作废）。
    """
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    text, data, error = girlfriend_render(refresh=_bool_flag(refresh))
    if error:
        return {'code': 400, 'data': error}
    view = _girlfriend_view()
    return {'code': 200, 'data': {
        'city': data.get('city') or '',
        'lat': data.get('lat') or '',
        'lon': data.get('lon') or '',
        'tz': data.get('tz') or '',
        'current': data.get('current') or {},
        'daily': data.get('daily') or {},
        'text': text,
        # 面板「测试天气」之后要顺手把解析出来的城市回显出来（这一步已经写过配置了）
        'city_resolved': view.get('city_resolved', False),
        'city_resolved_text': view.get('city_resolved_text') or '',
    }}


# 同上：预览只是即时调一次天气接口渲染，不碰浏览器，不挂 @serialized。
@app.get('/Api/Girlfriend/Preview')
def GirlfriendPreview(period: str = None, authorization: str = Header(None)):
    """按时段预览问候语（面板上的「取一条试试」），不发送任何消息。

    period 只认 auto / morning / noon / night，其它值一律按 auto（当前时刻）处理，
    免得面板传个脏值就整个报错。
    """
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    moment = datetime.now()
    wanted = str(period or '').strip().lower()
    if wanted not in GF_GREETING:
        wanted = 'auto'
    resolved = girlfriend_period(moment) if wanted == 'auto' else wanted
    text, _data, error = girlfriend_render(period=resolved, moment=moment)
    if error:
        return {'code': 400, 'data': error}
    return {'code': 200, 'data': {
        'period': resolved,
        'period_text': GF_PERIOD_TEXT[resolved],
        'text': text,
    }}


# 抖音账号资料接口（参考 prometheus-relay 的 PROFILE_PATHS / _probe_profile）
PROFILE_API_PATHS = (
    '/aweme/v1/creator/user/info/',
    '/aweme/v1/creator/pc/user/info/',
    '/web/api/media/user/info/',
)

PROFILE_FETCH_JS = """
async (paths) => {
  const out = [];
  for (const path of paths) {
    try {
      const response = await fetch(path, {credentials: 'include', cache: 'no-store'});
      let payload = null;
      try { payload = await response.json(); } catch (e) { payload = null; }
      out.push({path: path, status: response.status, payload: payload});
    } catch (e) {
      out.push({path: path, status: 0, payload: null});
    }
  }
  return out;
}
"""


def _deep_find_value(node, keys, depth=0):
    """在嵌套 JSON 中找出第一个非空的目标字段。"""
    if depth > 8:
        return None
    if isinstance(node, dict):
        for key in keys:
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in node.values():
            found = _deep_find_value(value, keys, depth + 1)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _deep_find_value(value, keys, depth + 1)
            if found:
                return found
    return None


def _fetch_profile():
    """用浏览器自身的登录态请求抖音资料接口，返回 (昵称, 抖音号)。"""
    try:
        results = driver.execute_async_script(
            "var cb = arguments[arguments.length - 1];"
            "(" + PROFILE_FETCH_JS + ")(arguments[0])"
            ".then(function (r) { cb(r); }).catch(function () { cb([]); });",
            list(PROFILE_API_PATHS))
    except Exception:
        return None, None
    if isinstance(results, dict):
        results = [results]
    if not isinstance(results, list):
        return None, None
    for item in results:
        if not isinstance(item, dict) or item.get('status') != 200:
            continue
        payload = item.get('payload')
        nickname = _deep_find_value(payload, ('nickname', 'nick_name', 'nickName'))
        unique_id = _deep_find_value(payload, ('unique_id', 'uniqueId', 'short_id', 'douyin_id'))
        if nickname:
            return nickname, unique_id
    return None, None


def _fetch_profile_nickname():
    nickname, _ = _fetch_profile()
    return nickname


@app.get('/Api/GetUsername')
@serialized
def GetUserInfo(authorization: str = Header(None)):
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    setup_guard_err = setup_guard()
    if setup_guard_err:
        return setup_guard_err
    browser_err = require_browser_session()
    if browser_err:
        return browser_err
    if Login_is_bool:
        nickname = _fetch_profile_nickname()
        if nickname:
            _account_mark_seen(nickname)
            return {'code': 200, 'data': nickname}
        match = re.search(r'\\"nickname\\":\\"([^\\"]+)\\"', driver.page_source)
        if match:
            text = match.group(0)
            clean = text.replace('\\"', '"')
            data = json.loads('{' + clean + '}')
            _account_mark_seen(data['nickname'])
            return {'code': 200, 'data': data['nickname']}
        else:
            return {'code': 400, 'data': '已登录,但未获取到用户名'}
    else:
        return {'code': 400, 'data': '未登录'}


@app.get('/Api/GetScrlk')  # 获取截图
@serialized(wait=0.4, cache='shot')
def GetScrlk(authorization: str = Header(None)):
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    setup_guard_err = setup_guard()
    if setup_guard_err:
        return setup_guard_err
    browser_err = require_browser_session()
    if browser_err:
        return browser_err
    try:
        # 直接拿 WebDriver 的 base64 截图，不再往「进程当前工作目录」写 temp.png：
        # 旧写法那个临时文件路径跟 BASE_DIR 无关，中途异常就会留下垃圾文件，
        # 而且 save_screenshot 失败后再 open 还会抛一个跟截图无关的 FileNotFoundError。
        return {'code': 200, 'data': driver.get_screenshot_as_base64()}
    except Exception as e:
        return {'code': 400, 'data': f'截图错误:{e}'}


@app.get('/Api/DieLogin')  # 取消登录
@serialized
def DieLogin(authorization: str = Header(None)):
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    setup_guard_err = setup_guard()
    if setup_guard_err:
        return setup_guard_err
    browser_err = require_browser_session()
    if browser_err:
        return browser_err
    driver.delete_all_cookies()
    driver.refresh()
    log_event('warn', '登录', '已清除浏览器 Cookie（取消抖音登录状态）')
    return {'code': 200, 'data': '已清除Cooke'}


@app.post('/Api/LoginPhone')
# 这个接口内部要等抖音响应验证码请求（_wait_send_result 最长 15 秒），
# 比一次点击慢得多，所以单独给它更长的等锁预算。
@serialized(wait=20)
def authorization(payload: dict = Body(default=None), areacode: str = None, phone: str = None,
                  authorization: str = Header(None)):
    """发送手机号登录验证码。

    手机号从 query 挪到请求体：它同样是个人隐私，放 URL 里会进 nginx access log
    与浏览器历史（和密码、短信验证码同一类问题）。
    """
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    setup_guard_err = setup_guard()
    if setup_guard_err:
        return setup_guard_err
    browser_err = require_browser_session()
    if browser_err:
        return browser_err
    body = payload or {}
    areacode = body.get('areacode', areacode)
    phone = body.get('phone', phone)
    if not phone:
        return {'code': 400, 'data': '请先填写手机号'}
    try:
        # 情况一：抖音已弹出「身份验证」二次验证面板（机房 IP 常见）
        if _is_verify_panel():
            return _trigger_verify_sms()

        # 情况二：常规手机号 / 验证码登录表单
        _try_click(PHONE_LOGIN_TAB_XPATHS)
        time.sleep(1)

        code_ok, current_code = _set_area_code(areacode)
        if not code_ok:
            return {'code': 400, 'data': f'国家码设置失败（当前 {current_code or "空"}），未发送验证码'}

        try:
            phone_input = _find_first(By.XPATH, LOGIN_PHONE_XPATHS)
        except Exception:
            return {'code': 400, 'data': '未找到手机号输入框，未发送验证码'}

        if not _set_value(phone_input, phone):
            actual = (phone_input.get_attribute('value') or '').strip()
            return {'code': 400, 'data': f'手机号未能写入输入框（当前值: {actual or "空"}），未发送验证码'}
        time.sleep(0.5)

        if not _try_click(LOGIN_SEND_CODE_XPATHS):
            return {'code': 400, 'data': '未找到「发送验证码」按钮，未发送验证码'}

        status, message = _wait_send_result()
        if status == 'sent':
            return {'code': 200, 'data': message}
        if status == 'challenge':
            return {'code': 202, 'data': message}
        return {'code': 400, 'data': message}
    except Exception as e:
        log_event('error', '登录', '验证码登录失败', e)
        return {'code': 400, 'data': '发送失败: %s' % exc_text(e)}


@app.post('/Api/LoginPhoneInput')
# 这个接口自己会轮询等登录态（LOGIN_CONFIRM_ATTEMPTS × LOGIN_CONFIRM_POLL ≈ 4 秒），
# 加上提交动作本身，给 20 秒预算。
@serialized(wait=20)
def authorizations(payload: dict = Body(default=None), code: str = None, authorization: str = Header(None)):
    """提交短信验证码，完成手机号登录。

    从 GET + query 改成 POST + 请求体：短信验证码属于凭据，
    放 URL 里会被写进 nginx access log 与浏览器历史（同 ChangePassword 那类问题）。
    """
    global Login_is_bool
    code = (payload or {}).get('code', code)
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    setup_guard_err = setup_guard()
    if setup_guard_err:
        return setup_guard_err
    browser_err = require_browser_session()
    if browser_err:
        return browser_err
    try:
        inp = _find_first(By.XPATH, LOGIN_CODE_XPATHS)
        _set_value(inp, code)

        _try_click(LOGIN_SUBMIT_XPATHS)
        time.sleep(3)
        if _is_two_factor_page():
            return {'code': 400, 'data': 'secondary verification is still required'}
        # 判定登录成功必须有**正面证据**：会话列表渲染出来、或拿到了 sessionid。
        # 旧写法反过来用「找不到登录面板里的 <picture>」证明成功 —— 那不是成功证据，
        # 只是"没找着"：抖音改版、页面正在跳转、元素还没渲染完，都会让它误报成功，
        # 于是面板显示已登录，实际所有发送都会失败。
        # _detect_logged_in_state() 先查会话列表/登录面板，再查认证 cookie，
        # 而且它「看到登录入口就判否」，所以这里给它几秒重试等页面稳定下来。
        for _ in range(LOGIN_CONFIRM_ATTEMPTS):
            if _detect_logged_in_state():
                Login_is_bool = True
                log_event('success', '登录', '手机号登录成功（已确认登录态）')
                return {'code': 200, 'data': 'login success'}
            time.sleep(LOGIN_CONFIRM_POLL)
        Login_is_bool = False
        log_event('warn', '登录', '提交验证码后未能确认登录态，请在浏览器窗口里看一眼')
        return {'code': 400, 'data': '提交后未能确认登录状态（未检测到会话列表或登录凭据），'
                                     '请在浏览器窗口里确认是否还需要二次验证'}
    except Exception as e:
        return {'code': 400, 'data': str(e)}


# ---------- 抖音「身份验证」（二次验证）功能 ----------
# 面板提供 4 种方式：接收短信验证码 / 手机刷脸验证 / 验证登录密码 / 发送短信验证

VERIFY_CODE_INPUT_XPATHS = [
    # 现场证据（2026-09-14，真实面板停在「验证登录密码」步骤时实测）：
    #   1. 验证面板与登录页各有一个 id="button-input"，面板内的那个才是二次验证的验证码框，
    #      必须排在登录页选择器前面，否则验证码会被填进登录框；
    #   2. 面板作用域的 //*[...]//input 会命中 <input type="password">（密码输入框也在面板里），
    #      所以必须显式排除 password，否则宽泛选择器会把密码框当成验证码框。
    '//*[contains(@class,"second_verify_panel_new")]//input[@type="tel"]',
    '//*[contains(@class,"second_verify_panel_new")]//input[@type="text"]',
    '//*[contains(@class,"second_verify_panel_new")]//input[@type="number"]',
    '//*[contains(@class,"second_verify_panel_new")]//input[not(@type="password")]',
    '//*[contains(@class,"uc_verification_component")]//input[not(@type="password")]',
    '//input[contains(@placeholder,"验证码")]',
    '//input[@autocomplete="one-time-code"]',
    '//input[contains(@aria-label,"验证码")]',
    '//input[contains(@name,"verify")]',
    '//input[@inputmode="numeric"]',
] + LOGIN_CODE_XPATHS

# 仅面板作用域内的验证码输入框（不含登录页元素）。判断「当前是不是短信验证这一步」、
# 以及往哪里填验证码，都只能用这一组：带上登录页的 #button-input 的话，
# 只要登录页那个框还显示着，就会被误判成「已经在短信步骤」。
VERIFY_CODE_INPUT_PANEL_XPATHS = VERIFY_CODE_INPUT_XPATHS[:5]

# 三种验证方式的文案，用来判断页面是不是「选验证方式」的列表页
VERIFY_METHOD_LABELS = ('接收短信验证码', '手机刷脸验证', '验证登录密码')

VERIFY_SUBMIT_TEXTS = ('确认', '确定', '提交', '验证', '下一步', '完成', '立即验证',
                       '确认验证', '提交验证', 'Verify', 'Submit', 'Confirm', 'Next')

VERIFY_SUBMIT_XPATHS = [
    # 优先面板内的控件，避免误点到登录页残留的「登录 / Log in」按钮
    '//*[contains(@class,"second_verify_panel_new")]//*[normalize-space()="%s"]' % text
    for text in VERIFY_SUBMIT_TEXTS
] + [
    '//*[normalize-space()="%s"]' % text for text in VERIFY_SUBMIT_TEXTS
] + LOGIN_SUBMIT_XPATHS

VERIFY_PASSWORD_INPUT_XPATHS = [
    # 注意：这里不能放 //*[...second_verify_panel_new]//input，
    # 它会命中同一面板里的「验证码」输入框，导致密码被敲进验证码框。
    '//input[@type="password"]',
    '//input[contains(@placeholder, "密码")]',
    '//input[contains(@placeholder, "password")]',
    '//*[contains(@class,"second_verify_panel_new")]//input[@type="password"]',
]

# 抖音拒绝验证码/密码时的页面文案（参考 prometheus-relay 的 failure_terms）
VERIFY_FAIL_TERMS = (
    '验证码错误', '验证码不正确', '验证码无效', '验证码已失效', '验证码已过期',
    '验证失败', '校验失败', '请重新输入验证码', '请输入正确的验证码',
)


def _first_displayed(xpaths):
    try:
        return _find_first(By.XPATH, xpaths)
    except Exception:
        return None


# 抖音二次验证 SDK 的按钮在 DOM 层不响应鼠标事件，必须直接调用 React 的 onClick。
# 现场取证（2026-09-14）：
#   - 按钮 onClick 源码为 function(){ l || null==n || n() }，l 是 disabled/loading 守卫；
#   - 验证码写进输入框后 React 是异步提交，立刻点击时 l 仍为 true，点击会静默空转
#     （表现为：前端 200、抖音零请求、页面无任何报错）；
#   - 该 SDK 对 Selenium 真实点击、乃至 CDP 派发的 trusted 鼠标事件都不响应，
#     只有直接调用 props.onClick 生效。
VERIFY_BUTTON_READY_JS = """
var el = arguments[0];
if (!el) return null;
var fk = Object.keys(el).find(function (k) { return k.indexOf('__reactFiber$') === 0; });
if (!fk) return null;
var node = el[fk], depth = 0;
while (node && depth < 14) {
  var p = node.memoizedProps;
  if (p && typeof p === 'object' && ('primaryButtonDisabled' in p || 'onSubmit' in p)) {
    return JSON.stringify({
      primaryButtonDisabled: p.primaryButtonDisabled === true,
      submitLoading: p.submitLoading === true,
      codeLen: typeof p.code === 'string' ? p.code.length : null
    });
  }
  node = node.return; depth++;
}
return null;
"""

VERIFY_REACT_CLICK_JS = """
var el = arguments[0];
var node = el, depth = 0;
while (node && depth < 6) {
  var k = Object.keys(node).find(function (n) { return n.indexOf('__reactProps$') === 0; });
  if (k && node[k] && typeof node[k].onClick === 'function') {
    node[k].onClick();
    return true;
  }
  node = node.parentElement; depth++;
}
return false;
"""

# 通用版：调用元素（或祖先）React props 上任意事件处理函数，并补一个合成事件对象。
# 抖音的大量控件只挂了 React props 事件：真实鼠标点击、CDP 派发的 trusted 事件都无效，
# 只有直接调用 props 上的处理函数才生效（实测会话行只有 onMouseDown，没有 onClick）。
REACT_HANDLER_JS = """
var el = arguments[0], name = arguments[1];
if (!el) return false;
var node = el, depth = 0;
while (node && depth < 8) {
  var k = Object.keys(node).find(function (n) { return n.indexOf('__reactProps$') === 0; });
  if (k && node[k] && typeof node[k][name] === 'function') {
    var rect = node.getBoundingClientRect ? node.getBoundingClientRect()
                                          : {left: 0, top: 0, width: 0, height: 0};
    var x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
    var fake = {
      type: String(name).replace(/^on/, '').toLowerCase(),
      target: node, currentTarget: node, srcElement: node,
      button: 0, buttons: 1, detail: 1, isTrusted: true, timeStamp: Date.now(),
      clientX: x, clientY: y, pageX: x, pageY: y, screenX: x, screenY: y,
      altKey: false, ctrlKey: false, shiftKey: false, metaKey: false,
      preventDefault: function () {}, stopPropagation: function () {},
      stopImmediatePropagation: function () {}, persist: function () {},
      nativeEvent: {button: 0, buttons: 1, clientX: x, clientY: y, isTrusted: true}
    };
    try { node[k][name](fake); return true; }
    catch (err) {
      try { node[k][name](); return true; } catch (err2) { return false; }
    }
  }
  node = node.parentElement; depth++;
}
return false;
"""


def _invoke_react_handler(element, name='onClick'):
    """直接调用元素 React props 上的事件处理函数（抖音控件唯一可靠的触发方式）。"""
    if element is None:
        return False
    try:
        return bool(driver.execute_script(REACT_HANDLER_JS, element, name))
    except Exception:
        return False


def _invoke_react_click(element):
    """直接调用元素 React props 上的 onClick（绕开 SDK 的鼠标事件问题）。"""
    if element is None:
        return False
    try:
        return bool(driver.execute_script(VERIFY_REACT_CLICK_JS, element))
    except Exception:
        return False


def _verify_submit_readiness():
    """读取验证面板的 React 状态，判断「验证」按钮此刻是否真的可提交。"""
    for xpath in VERIFY_SUBMIT_XPATHS:
        try:
            elements = driver.find_elements(By.XPATH, xpath)
        except Exception:
            continue
        for element in elements:
            try:
                if not element.is_displayed():
                    continue
                raw = driver.execute_script(VERIFY_BUTTON_READY_JS, element)
            except Exception:
                continue
            if raw:
                try:
                    return json.loads(raw)
                except Exception:
                    return None
    return None


VERIFY_REQUEST_COUNT_JS = """
return (window.performance && performance.getEntriesByType)
  ? performance.getEntriesByType('resource').length : null;
"""


def _verify_request_count():
    """页面已发出的子请求数，用来判断「点了验证按钮但抖音压根没收到」。"""
    try:
        count = driver.execute_script(VERIFY_REQUEST_COUNT_JS)
    except Exception:
        return None
    try:
        return int(count)
    except (TypeError, ValueError):
        return None


def _verify_refill(element, value):
    """把值重新灌进输入框并补发 React 需要的事件（按钮守卫仍为 true 时用）。"""
    if element is None or value is None:
        return False
    try:
        driver.execute_script(
            "var el=arguments[0],v=arguments[1];"
            "el.focus();"
            "var d=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');"
            "if(d&&d.set){d.set.call(el,'');}"
            "el.dispatchEvent(new Event('input',{bubbles:true}));"
            "if(d&&d.set){d.set.call(el,v);}"
            "el.dispatchEvent(new Event('input',{bubbles:true}));"
            "el.dispatchEvent(new Event('change',{bubbles:true}));",
            element, value)
    except Exception:
        return False
    return True


def _verify_submit_blocked(readiness):
    return bool(readiness) and (readiness.get('primaryButtonDisabled')
                                or readiness.get('submitLoading'))


def _submit_verify_input(element, value=None):
    """提交验证码/密码，返回 (提交方式, 按钮状态快照)。

    与旧实现的区别：不再因为「按钮还不可提交」就提前放弃 —— 先把值重新灌一遍
    （React 受控组件可能没吃到状态），再照样调用 onClick，
    真实结果交给 _wait_verify_outcome 判断，而不是提前编一个「SDK 未就绪」。
    实测这是该 SDK 唯一稳定生效的提交路径：它对真实点击乃至 CDP
    trusted 鼠标事件都不响应，只有直接调用 props.onClick 生效。
    """
    readiness = None
    for _ in range(12):  # 最多等约 6 秒，等 React 把「已填好」提交到按钮状态
        readiness = _verify_submit_readiness()
        if not _verify_submit_blocked(readiness):
            break
        time.sleep(0.5)
    if _verify_submit_blocked(readiness):
        _verify_refill(element, value)
        for _ in range(6):  # 重新灌值后再等约 3 秒
            readiness = _verify_submit_readiness()
            if not _verify_submit_blocked(readiness):
                break
            time.sleep(0.5)
    if _invoke_react_click(_first_displayed(VERIFY_SUBMIT_XPATHS)):
        return 'react-onclick', readiness
    # 异构版式兜底：React props 取不到时退回真实点击与回车
    if _try_click(VERIFY_SUBMIT_XPATHS):
        return 'dom', readiness
    try:
        element.send_keys(Keys.ENTER)
        return 'enter', readiness
    except Exception:
        return None, readiness


def _verify_submit_result(submitted, readiness, before, result, label='身份验证'):
    """把提交方式与「是否真的发出了请求」合并进返回体，失败时给可执行的下一步。"""
    result['submitted_via'] = submitted
    result['readiness'] = readiness
    after = _verify_request_count()
    if before is not None and after is not None:
        result['requests_delta'] = after - before
    if result.get('code') == 200:
        log_event('success', '验证', '%s通过，登录成功' % label)
        return result
    if submitted is None:
        result['data'] = '没能点到「验证」按钮（面板上没找到可用的提交按钮），可点「重新选择验证方式」后再试'
    elif result.get('requests_delta') == 0:
        result['data'] = ('已点击「验证」，但抖音没有收到任何请求（按钮仍处于不可提交状态）。'
                          '可点「重新选择验证方式」换一种方式，或刷新状态后重试')
    log_event('error', '验证', '%s未通过' % label, result.get('data'))
    return result


def _verify_fail_text():
    """返回抖音给出的验证码/密码拒绝文案（没有则返回空串）。

    只扫描验证面板与登录区域，避免页面隐藏模板造成误报。
    """
    text = ' '.join((_verify_panel_text() or '', _login_error_text() or ''))
    for term in VERIFY_FAIL_TERMS:
        if term in text:
            return term
    return ''


def _wait_verify_outcome(timeout=8):
    """提交后等待抖音给出结果：出现报错 / 面板消失 / 已登录，三者任一即返回。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.5)
        if _verify_fail_text():
            return
        if not _is_verify_panel():
            return
        if _detect_logged_in_state():
            return


def _finish_verify(message_ok, message_fail=None):
    """统一收尾：明确报错 => 报错；面板仍在 => 未通过；确认已登录 => 通过；否则不确定。"""
    global Login_is_bool
    data = _verify_snapshot()
    fail_text = _verify_fail_text()
    hint = '，可点「重新选择验证方式」换一种方式，或重试'
    if fail_text:
        return {'code': 400, 'data': '抖音返回「%s」%s' % (fail_text, hint), 'state': data}
    if data.get('active'):
        return {'code': 400, 'data': (message_fail or '验证未通过，身份验证面板仍在') + hint,
                'state': data}
    if data.get('logged_in'):
        Login_is_bool = True
        return {'code': 200, 'data': message_ok, 'state': data}
    return {'code': 400, 'data': '未检测到验证结果，请刷新状态确认', 'state': data}


@app.get('/Api/Verify/State')
@serialized(wait=0.4, cache='verify')
def VerifyState(shot: int = 0, authorization: str = Header(None)):
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    browser_err = require_browser_session()
    if browser_err:
        return browser_err
    return {'code': 200, 'data': _verify_snapshot(with_shot=bool(shot))}


@app.get('/Api/Verify/Select')
@serialized
def VerifySelect(option: str, shot: int = 0, authorization: str = Header(None)):
    """选择身份验证方式：sms=接收短信验证码 / face=手机刷脸验证 / password=验证登录密码 / sms_send=发送短信验证"""
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    browser_err = require_browser_session()
    if browser_err:
        return browser_err
    key = (option or '').strip().lower()
    if key not in VERIFY_OPTION_MAP:
        return {'code': 400, 'data': '不支持的验证方式: %s（可选 sms / face / password / sms_send）' % option}
    if not _in_verify_flow():
        return {'code': 400, 'data': '当前页面没有「身份验证」面板'}
    label = VERIFY_OPTION_MAP[key]
    # sms = 首次下发（已在填写验证码就不重复发，避免多烧短信、抬高风控）；
    # sms_send = 用户主动点「重新发送」，必须真的点到发送按钮，否则不能报成功。
    target = 'sms' if key == 'sms_send' else key
    already_sent = (key == 'sms' and _verify_sms_sent()
                    and _first_displayed(VERIFY_CODE_INPUT_PANEL_XPATHS) is not None)
    if not already_sent:
        ok, reason = _enter_verify_method(target)
        if not ok:
            return {'code': 400, 'data': reason}
        time.sleep(1)
        if target == 'sms':
            # 短信方式：选完入口后通常还需点一次「发送短信验证」才真正下发
            if not _click_verify_option(VERIFY_OPTION_MAP['sms_send']) and key == 'sms_send':
                return {'code': 400, 'data': '未找到「重新发送验证码」入口'}
            time.sleep(2)
    data = _verify_snapshot(with_shot=bool(shot))
    if key in ('sms', 'sms_send'):
        if data.get('masked_phone'):
            message = '验证码已发送至账号绑定手机号 %s（短信发往账号绑定号码，与页面填写的号码无关）' % data['masked_phone']
        else:
            message = '已触发「%s」，请在页面核对结果' % label
        log_event('info', '验证', '%s：%s' % (label, message))
        return {'code': 200, 'data': message, 'state': data}
    if key == 'face':
        log_event('info', '验证', '已进入手机刷脸验证，等待扫码')
        return {'code': 200, 'data': '已进入手机刷脸验证，请用手机扫描页面上的二维码完成', 'state': data}
    log_event('info', '验证', '已选择「%s」' % label)
    return {'code': 200, 'data': '已选择「%s」，请输入后提交' % label, 'state': data}


@app.get('/Api/Verify/Back')
@serialized
def VerifyBack(shot: int = 0, authorization: str = Header(None)):
    """回退到验证方式列表，供上一次验证失败后改用其它验证方式。"""
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    browser_err = require_browser_session()
    if browser_err:
        return browser_err
    if not _in_verify_flow():
        return {'code': 400, 'data': '当前页面不在二次验证流程里，无需回退'}
    if not _open_verify_methods():
        return {'code': 400,
                'data': '未能回到验证方式列表（页面上既没有「选择其他验证方式」，也没有可用的返回/取消控件）'}
    log_event('info', '验证', '已回到验证方式列表，准备换一种验证方式')
    return {'code': 200, 'data': '已回到验证方式列表，请重新选择验证方式',
            'state': _verify_snapshot(with_shot=bool(shot))}


@app.post('/Api/Verify/Code')
@serialized
def VerifyCode(payload: dict = Body(default=None), code: str = None, authorization: str = Header(None)):
    """提交短信验证码（仅在「身份验证」面板存在时有效）。

    验证码从 query 挪到请求体：它是凭据，放 URL 里会被写进访问日志。
    """
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    browser_err = require_browser_session()
    if browser_err:
        return browser_err
    code = (payload or {}).get('code', code)
    if not code:
        return {'code': 400, 'data': '请先填写验证码'}
    if not _in_verify_flow():
        return {'code': 400, 'data': '当前页面没有「身份验证」面板，无法提交该验证码'}
    # 只用「仅面板」这一组找输入框：完整列表里的登录页 #button-input 也可见，
    # 用它会把这个验证码填进登录框。
    inp = _first_displayed(VERIFY_CODE_INPUT_PANEL_XPATHS)
    if inp is None:
        return {'code': 400,
                'data': '当前不在短信验证步骤（面板里没有验证码输入框），请先点「短信验证码」，或点「换一种方式」后重选'}
    if not _set_value(inp, code):
        return {'code': 400, 'data': '验证码未能写入输入框'}
    before = _verify_request_count()
    submitted, readiness = _submit_verify_input(inp, code)
    _wait_verify_outcome()
    result = _finish_verify('验证通过，登录成功')
    return _verify_submit_result(submitted, readiness, before, result, label='短信验证码')


@app.post('/Api/Verify/Password')
@serialized
def VerifyPassword(payload: dict = Body(default=None), authorization: str = Header(None)):
    """用账号登录密码完成身份验证（密码只用于填入浏览器，不落盘、不记录）。"""
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    browser_err = require_browser_session()
    if browser_err:
        return browser_err
    password = (payload or {}).get('password') or ''
    if not password:
        return {'code': 400, 'data': '请输入账号登录密码'}
    if _first_displayed(VERIFY_PASSWORD_INPUT_XPATHS) is None:
        ok, reason = _enter_verify_method('password')
        if not ok:
            return {'code': 400, 'data': reason}
    inp = _first_displayed(VERIFY_PASSWORD_INPUT_XPATHS)
    if inp is None:
        return {'code': 400, 'data': '未找到密码输入框'}
    if not _set_value(inp, password):
        return {'code': 400, 'data': '密码未能写入输入框'}
    before = _verify_request_count()
    submitted, readiness = _submit_verify_input(inp, password)
    _wait_verify_outcome()
    result = _finish_verify('验证通过，登录成功', '登录密码不正确或验证未通过')
    return _verify_submit_result(submitted, readiness, before, result, label='登录密码')


@app.get('/Api/LoginDebug')
@serialized(wait=2)
def LoginDebug(authorization: str = Header(None)):
    global Login_is_bool
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    browser_err = require_browser_session()
    if browser_err:
        return browser_err
    if Login_is_bool == False:
        Login_is_bool = True
        return {'code': 200, 'data': 'OK'}
    else:
        return {'code': 400, 'data': '已是登录状态,无需设定'}


# ==================== 定时任务操作 ====================
def _find_task_by_name(name):
    """按好友名找任务ID。

    任务ID改成了随机串，不再用「时间_好友名」拼：好友名里带下划线或后缀相同时
    （A 与 B_A），旧拼法会让两条任务互相误判成同一条。
    """
    with _task_lock:
        for task_id, meta in task_meta.items():
            if meta.get('name') == name:
                return task_id
    return None


def _task_view(task_id, meta, moment=None):
    """把一条任务渲染成前端需要的结构。"""
    moment = moment or datetime.now()
    # 先看「已经到点但还没发」的那一次（可能归属昨天、计划时刻落在今天凌晨，
    # 跨午夜任务的真实执行时间就属于这种）；没有的话再给出真正意义上的下一次。
    planned, _plan_day = pending_occurrence(task_id, meta, moment, CATCHUP_GRACE_MINUTES)
    if planned is None:
        planned, _plan_day = pending_occurrence(task_id, meta, moment, CATCHUP_GRACE_MINUTES,
                                                only_future=True)
    if planned is None:
        # 今天这次已经发过了：显示明天的计划时刻
        planned = _planned_or_none(meta.get('time'), moment.date() + timedelta(days=1), task_id)
    return {
        'task_id': task_id,
        'time': meta.get('time'),
        'name': meta.get('name'),
        'text': meta.get('text'),
        # 前端用它显示「灵签」标记并回填编辑表单；老任务没有这个字段，统一给 False
        'sign': bool(meta.get('sign')),
        # 同上：前端用它回填「内容来源」单选框，老任务没有这个字段就按 'text'
        'source': str(meta.get('source') or 'text'),
        # 实际执行时刻 = 基准时间 + 当天随机偏移，这里直接给出下一次真实时间
        'next_run': planned.strftime('%Y-%m-%d %H:%M:%S') if planned else '',
        'last_run_date': meta.get('last_run_date'),
    }


@app.post('/Time/add')
@serialized
def add_time(payload: dict = Body(default=None), time: str = None, name: str = None, text: str = None,
             source: str = None, authorization: str = Header(None)):
    """给好友新增一条每日定时任务。

    从 GET + query 改成 POST + 请求体：好友昵称和消息正文都会进 nginx access log。
    另外补上了 require_browser_session()：原实现直接调用 douyin.Find_Friends，
    浏览器没初始化时是 AttributeError（500），而不是一句能看懂的提示。

    source：'text'（默认）/ 'hitokoto'，后者表示正文在每次发送前现取一句一言。
    """
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    setup_guard_err = setup_guard()
    if setup_guard_err:
        return setup_guard_err
    browser_err = require_browser_session()
    if browser_err:
        return browser_err
    body = payload or {}
    time = body.get('time', time)
    name = body.get('name', name)
    text = body.get('text', text)
    source = body.get('source', source)
    # 昵称先 strip 再判空：只填空格能通过原来的 `if not name`，
    # 结果生成一条「名字为空」的任务，而 _find_task_by_name('') 会让后续同名任务互相误判。
    name = str(name or '').strip()
    if not name:
        return {'code': 400, 'data': '请先选择好友'}
    # 内容来源非法时直接报错，不能静默按 text 存下：用户以为这条任务在发一言，
    # 实际发的是自己写死的文案，属于最难查的那种「没生效」。
    source_error = _task_source_error(source)
    if source_error:
        return {'code': 400, 'data': source_error}
    # 时间必须显式校验：format_time 对 None/''/'zzz' 会静默回落到 22:00，
    # 用户以为自己设了别的时间，任务却每天 22:00 发出去。
    time_error = time_format_error(time)
    if time_error:
        return {'code': 400, 'data': time_error}
    if _find_task_by_name(name):
        return {'code': 400, 'data': '好友 %s 已有定时任务，请先删除或修改' % name}
    # 确认这位好友真的在会话列表里，免得任务建在一个点不到的名字上
    temp = douyin.Find_Friends(name)
    if not temp.is_bool:
        return {'code': 404, 'data': temp.string or '会话列表里没有这位好友'}
    play_time = format_time(time)
    blocked = default_password_block()
    if blocked:
        return blocked
    msg = (text or '').strip()
    sign_flag = _bool_flag(body.get('sign'))
    source_flag = _normalise_task_source(source)
    task_id = uuid.uuid4().hex[:12]
    # 今天这个时间点已经过去了：直接记为「今天已跑」，免得刚建完任务就立刻补发一条
    planned = planned_run_at(play_time, datetime.now().date(), task_id, JITTER_MINUTES)
    _register_task(play_time, name, msg, task_id=task_id, mark_today=planned <= datetime.now(),
                   sign=sign_flag, source=source_flag)
    pool = parse_message_pool(msg)
    log_event('info', '任务管理', '添加定时任务：%s 每天 %s 左右自动%s%s' % (
        name, play_time, '发文昌帝君灵签图文' if sign_flag else '发消息',
        _task_source_note(source_flag)),
        '文案池 %d 条：%s' % (len(pool), pool[0]) if pool else (msg or '（灵签：每天取当天签文）'))
    return {
        'code': 200,
        'data': '已添加定时任务：%s 左右（含 %d 分钟随机窗口）' % (play_time, JITTER_MINUTES),
        'task_id': task_id,
        'next_run': _task_view(task_id, task_meta.get(task_id) or {}).get('next_run'),
    }


@app.post('/Time/del')
def del_time(payload: dict = Body(default=None), task_id: str = None, authorization: str = Header(None)):
    """按任务ID删除定时任务。"""
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    setup_guard_err = setup_guard()
    if setup_guard_err:
        return setup_guard_err
    task_id = (payload or {}).get('task_id', task_id)
    with _task_lock:
        meta = task_meta.pop(task_id, None)
    if not meta:
        return {'code': 404, 'data': '任务ID不存在'}
    _persist_tasks()
    log_event('info', '任务管理', '删除定时任务：%s（%s）' % (meta.get('name'), meta.get('time')))
    return {'code': 200, 'data': '已删除任务: %s' % task_id}


@app.post('/Time/edit')
def edit_time(payload: dict = Body(default=None), name: str = None, new_time: str = None,
              text: str = None, sign: str = None, source: str = None,
              authorization: str = Header(None)):
    """修改某位好友的定时任务（时间 / 文案）。

    旧实现有个必现 bug：新时间与旧时间相同时（前端会预填当前时间，用户直接点「修改」，
    或者 9:23 与 09:23 这种等价写法），new_task_id == old_task_id，
    于是它刚建好的 job 又被 `scheduled_tasks.pop(old_task_id)` 取出来 cancel 掉，
    任务彻底消失，接口却回一句「修改成功」。
    现在只改 task_meta 的字段，任务ID保持不变，结构上就不可能自删。
    同时不再用新名言覆盖用户自定义文案（旧实现每次改时间都会把文案换成随机名言）。
    """
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    setup_guard_err = setup_guard()
    if setup_guard_err:
        return setup_guard_err
    body = payload or {}
    name = body.get('name', name)
    new_time = body.get('new_time', new_time)
    text = body.get('text', text)
    sign = body.get('sign', sign)
    source = body.get('source', source)
    # 「没传 sign」和「传了 sign=false」必须区分开：前者保持原样，后者是用户
    # 明确把一条灵签任务改回普通任务。所以这里不能用 _bool_flag(None) 一起吞掉。
    sign_given = ('sign' in body) or (sign is not None)
    sign_flag = _bool_flag(sign)
    # source 照抄同一套逻辑：没传 = 保持原值（老前端不发这个字段，不能把任务悄悄改回 text），
    # 传了才覆盖。非法值报错而不是静默按 text 覆盖。
    source_given = ('source' in body) or (source is not None)
    if source_given:
        source_error = _task_source_error(source)
        if source_error:
            return {'code': 400, 'data': source_error}
    new_source = _normalise_task_source(source)
    # 与 /Time/add 同样的理由：非法时间不能被静默替换成 22:00
    if new_time is not None:
        time_error = time_format_error(new_time)
        if time_error:
            return {'code': 400, 'data': time_error}
    task_id = _find_task_by_name(name) if name else None
    if not task_id:
        return {'code': 404, 'data': '好友 %s 没有定时任务' % name}
    play_time = format_time(new_time)
    old_sign = None
    old_source = None
    with _task_lock:
        meta = dict(task_meta.get(task_id) or {})
        old_time = meta.get('time')
        old_sign = bool(meta.get('sign'))
        old_source = _normalise_task_source(meta.get('source'))
        meta['time'] = play_time
        if text is not None:
            meta['text'] = str(text).strip() or ''
        if sign_given:
            meta['sign'] = sign_flag
        if source_given:
            meta['source'] = new_source
        if play_time != old_time:
            # 改了时间就允许今天按新时间再发一次；但若今天的新时间点也已经过去，
            # 仍然记为今天已跑，避免用户改完立刻收到一条意料之外的消息
            planned = planned_run_at(play_time, datetime.now().date(), task_id, JITTER_MINUTES)
            meta['last_run_date'] = today_str() if planned <= datetime.now() else None
        task_meta[task_id] = meta
    _persist_tasks()
    log_event('info', '任务管理', '修改定时任务：%s 从 %s 改为 %s' % (name, old_time, play_time))
    if sign_given and sign_flag != old_sign:
        log_event('info', '任务管理', '%s 的定时任务%s灵签图文' % (
            name, '改为发送' if sign_flag else '改回普通文字，不再发送'))
    if source_given and new_source != old_source:
        log_event('info', '任务管理', '%s 的定时任务正文改为%s' % (
            name, {'hitokoto': '一言接口现取一句（写下的文案只当兜底）',
                   'girlfriend': '按发送时刻现渲染天气问候（写下的文案只当兜底）'}
            .get(new_source, '任务里自己写的文案')))
    return {
        'code': 200,
        'data': '已将 %s 的定时任务从 %s 修改为 %s' % (name, old_time, play_time),
        'old_time': old_time,
        'new_time': play_time,
        'task_id': task_id,
        'next_run': _task_view(task_id, meta).get('next_run'),
    }


@app.post('/Time/test')
@serialized
def test_task_send(payload: dict = Body(default=None), task_id: str = None,
                   authorization: str = Header(None)):
    """立即试发一条定时任务（**真发一条**），用来当场验证这条任务到底能不能发出去。

    为什么需要它：定时任务只在设定时间（还要叠加 0~N 分钟随机窗口）触发，
    用户想确认「灵签图文能不能发出去」就只能干等一天；这里按任务当前配置立刻发一次，
    结果直接回给面板，失败原因也能马上看到（配合 /Api/Logs 的失败截图）。

    记账口径（重要）：
      * 用 scope='manual'：只防手滑连点，不会因为「今天已经发过」就跳过 ——
        否则已经收过今天消息的好友就试不出来了；
      * **不写 last_run_date**：今天该到点的那次任务照常执行；
      * 但试发成功会写进（日期 + 好友）的发送记录，于是那次正常发送会被当成
        「今天已经发过」而跳过 —— 这正是「每位好友每天恰好一条」的本意，
        所以返回文案里会把这句话明确告诉用户。
    """
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    setup_guard_err = setup_guard()
    if setup_guard_err:
        return setup_guard_err
    browser_err = require_browser_session()
    if browser_err:
        return browser_err
    body = payload or {}
    task_id = body.get('task_id', task_id)
    image_only = _bool_flag(body.get('image_only'))
    with _task_lock:
        meta = dict(task_meta.get(task_id) or {})
    if not meta:
        return {'code': 404, 'data': '任务不存在（可能刚被删掉），请刷新任务列表后重试'}
    name = meta.get('name')
    if not name:
        return {'code': 404, 'data': '这条任务没有好友名，无法试发'}
    text = meta.get('text') or ''
    sign_data = fetch_wenchang_sign() if meta.get('sign') else None
    if meta.get('sign') and sign_data is None:
        return {'code': 404, 'data': '取文昌帝君灵签失败：接口没有返回可用签文，请稍后再试'}
    image_path = None
    if sign_data and sign_data.get('pic'):
        image_path, image_error = download_image(sign_data['pic'])
        if image_error:
            return {'code': 404, 'data': '签图没准备好，本次没有试发：%s' % image_error}
        log_event('info', '试发', '已下载签图：%s' % image_path)
    if image_only and image_path is None:
        return {'code': 400, 'data': '没有可以单独发的图：这条任务不是灵签任务，'
                                     '或者签文接口这次没返回签图'}
    # 文案口径与 run_scheduled_send 完全一致（试发必须和真发同样的内容，否则试了也白试）：
    # 灵签任务留空就用当天签文，写了文案就用用户写的；女朋友模式现渲染天气问候；
    # 普通任务留空就现取一条（语录）。
    if _normalise_task_source(meta.get('source')) == 'girlfriend':
        # 渲染失败也一样：退回任务里写下的文案 / 现取一条，绝不空着手试发
        text = render_girlfriend_text() or render_message(text) or random.choice(FALLBACK_MESSAGES)
    elif sign_data and not (text or '').strip():
        text = render_sign_text(sign_data)
    else:
        text = _resolve_text(text)
    if image_only:
        text = ''
    if not (text or '').strip() and image_path is None:
        return {'code': 400, 'data': '这条任务既没有文案也没有签图，试发不出内容'}
    record_text = image_record_text(text, sign_summary(sign_data)) if image_path else text
    if image_path and not (text or '').strip():
        kind = '只发签图'
    elif image_path:
        kind = '灵签图文'
    else:
        kind = '文案'
    log_event('info', '试发', '立即试发任务：给「%s」发%s' % (name, kind), record_text)
    result = _send_now(name, text, image_path=image_path, record_text=record_text,
                       scope='manual', category='试发', success_text='试发成功',
                       kind='test')
    if result.get('code') == 200:
        return {'code': 200, 'data': '试发成功（%s）。这次已计入「%s」今天的发送记录，'
                                     '今天的定时任务不会再重复发一条。' % (kind, name)}
    return result


@app.get('/Time/getlist')
def get_time_list(authorization: str = Header(None)):
    """获取当前所有定时任务列表。"""
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    moment = datetime.now()
    with _task_lock:
        snapshot = [(task_id, dict(meta)) for task_id, meta in task_meta.items()]
    tasks = [_task_view(task_id, meta, moment) for task_id, meta in snapshot]
    tasks.sort(key=lambda item: item.get('time') or '')
    return {
        'code': 200,
        'data': {
            'count': len(tasks),
            'tasks': tasks,
            # 让前端能说明「为什么实际发送时间不是整点」
            'jitter_minutes': JITTER_MINUTES,
            'catchup_grace_minutes': CATCHUP_GRACE_MINUTES,
            'retry_after_minutes': RETRY_AFTER_MINUTES,
        },
    }


# 后台登录
@app.post('/Api/Login/Admin')
def admin_login(username: str = Body(default=None), password: str = Body(default=None),
                request: Request = None):
    """面板登录。

    安全修复：
      * 旧实现是 GET + query string，密码会明文写进 nginx access log 与程序日志；
      * 旧实现没有任何失败次数限制，配合固定用户名 admin 可被在线爆破。
    现在失败累计到 LOGIN_FAIL_LIMIT 次就把来源 IP 临时锁 LOGIN_LOCK_SECONDS 秒。
    """
    global _last_login_ip
    ip = _client_ip(request)
    locked = _login_throttle_remaining(ip)
    if locked:
        log_event('warn', '系统', '登录尝试过于频繁，已临时锁定该来源', {'ip': ip, '剩余秒': locked})
        return {'code': 429, 'data': '尝试次数过多，请 %d 秒后再试' % locked}
    if username == 'admin' and password and check_password(password):
        _last_login_ip = ip
        _clear_login_failures(ip)
        token = generate_token()
        log_event('info', '系统', '面板登录成功', {'ip': ip})
        return {'code': 200, 'data': token, 'must_change_password': using_default_password()}
    # 区分「用户名填错」和「密码不对」。用户名是硬编码的唯一账号，登录页也预填了
    # admin，它不是秘密；而旧实现两种错都只回一句「登录失败」，用户没法判断到底
    # 哪个字段填错了 —— 这正是最容易被误当成「密码丢了」的地方。
    # 密码那一路额外给出找回入口：面板没有、也不该有「忘记密码」自助入口
    # （那等于给爆破开了后门），重置只能在服务器上跑 reset_password.py。
    reason = '登录失败。若已忘记密码，在服务器上执行 python reset_password.py 重置'
    if username != 'admin':
        reason = '用户名固定为 admin（本程序只有一个面板账号），请检查是否填错'
    if _record_login_failure(ip):
        log_event('error', '系统', '登录连续失败，已临时锁定该来源', {'ip': ip})
        return {'code': 429, 'data': '尝试次数过多，请 %d 秒后再试' % LOGIN_LOCK_SECONDS}
    # 只记用户名，绝不记密码
    log_event('warn', '系统', '面板登录失败', {'ip': ip, '用户名': username})
    return {'code': 400, 'data': reason}


@app.get('/Api/GetLastLoginIP')
def get_last_login_ip(authorization: str = Header(None)):
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    return {'code': 200, 'data': _last_login_ip}


# 退出登录
@app.post('/Api/logout')
def logout(authorization: str = Header(None)):
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    token = authorization[7:]
    remove_token(token)
    log_event('info', '登录', '面板已退出登录')
    return {'code': 200, 'data': '已退出登录'}


# 密码修改
@app.post('/Api/ChangePassword')
def change_password(payload: dict = Body(default=None), old_password: str = None, new_password: str = None,
                    authorization: str = Header(None)):
    """修改面板密码。

    两条安全修复：
      1. 从 GET + query string 改成 POST + 请求体 —— 旧写法会把新旧密码明文写进
         nginx access log、浏览器历史与 Referer。同一个文件里 /Api/login/Init/GetCooker
         早就因为同样的原因改成 POST 了，这里当时漏改；
      2. 改完密码立即失效全部已签发的登录 token。旧实现只换哈希，
         旧 token 仍然长期可用（等于「密码泄露后改密码也没用」）。
         现在所有浏览器都会被踢回登录页，这是刻意的。
    """
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    body = payload or {}
    old_password = body.get('old_password', old_password)
    new_password = body.get('new_password', new_password)
    global _password
    if not check_password(old_password):
        return {'code': 400, 'data': '原密码错误'}
    policy_error = password_policy_error(new_password)
    if policy_error:
        return {'code': 400, 'data': policy_error}
    if str(new_password) == str(old_password):
        return {'code': 400, 'data': '新密码不能与原密码相同'}
    # 先落盘，成功了再改内存并吊销会话。
    # 旧顺序是先改 _password 再落盘：落盘失败时会返回「本次修改未生效」，
    # 但内存里其实已经生效了 —— 提示与事实不符，重启后密码又变回去，
    # 用户会被自己锁在门外，而且失败路径上旧 token 不会被吊销。
    if not STATE.set('password_hash', hash_password(new_password)):
        return {'code': 500, 'data': '密码保存失败（请检查 data 目录权限与磁盘空间），本次修改未生效'}
    _password = str(new_password)
    revoked = _valid_tokens.revoke_all()
    log_event('warn', '系统', '登录密码已修改并保存，已强制 %d 个登录会话重新登录' % revoked)
    return {'code': 200, 'data': '密码修改成功，请重新登录', 'revoked_sessions': revoked}


# ==================== 消息通知接口 ====================
@app.get('/Api/Notify/Get')
def GetNotify(authorization: str = Header(None)):
    """读取通知配置（推送地址只回显打码结果，邮箱密码只回「是否已设置」）。"""
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    config = _notify_config()
    view = {
        'enabled': bool(config.get('enabled')),
        'on_success': bool(config.get('on_success')),
        'url_masked': mask_push_url(config.get('url')),
        'url_set': bool(config.get('url')),
        # 邮箱配置单独一层：邮箱的 enabled 是「邮箱子开关」，和上面的总开关同名字段
        # 平铺在一起会被后者覆盖，前端读到的总开关就成了邮箱开关。
        'email': _email_config_view(config),
    }
    return {'code': 200, 'data': view}


@app.post('/Api/Notify/Set')
def SetNotify(enabled: bool = Body(default=False), url: str = Body(default=None),
              on_success: bool = Body(default=False), email: dict = Body(default=None),
              authorization: str = Header(None)):
    """保存通知配置。url 传 None 表示不改，传空串表示清空；email 同理。"""
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    setup_guard_err = setup_guard()
    if setup_guard_err:
        return setup_guard_err
    # force=True：通知配置每次都是整体落盘，旧 state.json 缺的键这里一次性补齐
    config = _notify_config(force=True)
    if url is not None:
        url = url.strip()
        if url:
            # 必须和 notify()/TestNotify 一样带上 allow_private：报错文案明确让用户去设
            # SPARK_ALLOW_PRIVATE_PUSH=1，但保存这一步不认这个开关的话，本机 webhook
            # （ntfy / Uptime Kuma / 内网脚本）永远存不进来，那句话就成了骗人的。
            invalid = validate_push_url(url, allow_private=ALLOW_PRIVATE_PUSH)
            if invalid:
                return {'code': 400, 'data': invalid}
            config['url'] = url
        else:
            config['url'] = ''
    problem = _apply_email_config(email, config)
    if problem:
        return {'code': 400, 'data': problem}
    config['enabled'] = bool(enabled)
    config['on_success'] = bool(on_success)
    # 总开关开着、两个通道却都没配：notify() 在第一道门禁就会静默 return False，
    # 面板上却显示「通知已开启」，真出事时一条都收不到、也没有任何提示。
    # 这里直接拦住并说清要配什么（邮箱配置在上面 _apply_email_config 里已经合进 config）。
    if config['enabled'] and not (config.get('url') or _email_configured(config)):
        return {'code': 400,
                'data': '开启通知前请先填写推送地址，或把邮箱通知配置完整（SMTP 服务器 + 收件邮箱）'
                        ' —— 两个通道都没有的话，通知发不出去，界面上也不会有任何提示'}
    if not STATE.set('notify', config):
        return {'code': 500, 'data': '通知配置保存失败（请检查 data 目录权限与磁盘空间），本次修改未生效'}
    log_event('info', '通知', '通知配置已更新',
              {'开启': config['enabled'], '地址已设置': bool(config.get('url')),
               '成功也通知': config['on_success'],
               '邮箱已开启': bool(config.get('email_enabled')),
               '邮箱已配置': _email_configured(config),
               '收件邮箱': mask_email(config.get('email_to'))})
    view = {
        'url_masked': mask_push_url(config.get('url')),
        'enabled': config['enabled'],
        'on_success': config['on_success'],
        'url_set': bool(config.get('url')),
        # 同 GetNotify：邮箱字段单独一层，避免 email_enabled 覆盖总开关 enabled
        'email': _email_config_view(config),
    }
    return {'code': 200, 'data': view}


@app.post('/Api/Notify/Test')
def TestNotify(channel: str = Body(default=''), email: dict = Body(default=None),
               authorization: str = Header(None)):
    """发一条测试通知。channel：push=只测推送 / email=只测邮件 / 留空=两者都测。

    email 可以带上表单里还没保存的邮箱设置（密码留空则用已保存的），
    这样「先测通再保存」和「保存后再测」都走得通。
    """
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    setup_guard_err = setup_guard()
    if setup_guard_err:
        return setup_guard_err
    channel = str(channel or '').strip().lower()
    if channel not in ('', 'push', 'email'):
        return {'code': 400, 'data': '未知的测试通道，只支持 push 或 email'}
    # force=True：下面要按 _email_settings(config) 取邮箱字段，缺键必须补齐
    config = _notify_config(force=True)
    if channel == 'email' or (not channel and email is not None):
        problem = _apply_email_config(email, config)
        if problem:
            return {'code': 400, 'data': problem}
    url = config.get('url')
    want_push = channel == 'push' or (not channel and bool(url))
    # 走默认分支时必须同时看开关和配置：关掉邮箱却还收到测试邮件是说不通的；
    # 显式 channel='email' 表示「我就是要测邮件」，此时不看开关，只看配置。
    email_ready = _email_configured(config)
    want_email = channel == 'email' or (
        not channel and bool(config.get('email_enabled')) and email_ready)
    if channel == 'email' and not email_ready:
        return {'code': 400, 'data': '请先填写 SMTP 服务器和收件邮箱（或先保存邮箱配置）'}
    if not want_push and not want_email:
        return {'code': 400, 'data': '请先填写并保存推送地址，或填写并保存邮箱配置'}
    title = '火花助手 · 测试通知'
    content = '这是一条测试通知，收到即表示通知配置成功。'
    results = {}
    if want_push:
        ok, detail = push(url, title, content, allow_private=ALLOW_PRIVATE_PUSH)
        results['push'] = {'ok': ok, 'detail': detail}
        log_event('info' if ok else 'warn', '通知', '测试推送%s' % ('成功' if ok else '失败'), None if ok else detail)
    if want_email:
        ok, detail = send_email(_email_settings(config), title, content, allow_private=ALLOW_PRIVATE_PUSH)
        results['email'] = {'ok': ok, 'detail': detail}
        log_event('info' if ok else 'warn', '通知', '测试邮件%s：%s' % ('成功' if ok else '失败', mask_email(config.get('email_to'))),
                  None if ok else detail)
    if not all(item['ok'] for item in results.values()):
        failed = '；'.join('%s：%s' % ('推送' if key == 'push' else '邮件', item['detail'])
                           for key, item in results.items() if not item['ok'])
        return {'code': 404, 'data': '测试失败 —— %s' % failed, 'results': results}
    if want_push and want_email:
        return {'code': 200, 'data': '推送和测试邮件都已发出，请查看手机与邮箱', 'results': results}
    kind = '推送' if want_push else '邮件'
    return {'code': 200, 'data': '%s已发出，请查看%s' % (kind, '手机' if want_push else '邮箱'),
            'results': results}


# ==================== 发送预检（Dry Run） ====================
@app.post('/Api/Send/Check')
@serialized
def SendCheck(payload: dict = Body(default=None), name: str = None, authorization: str = Header(None)):
    """预检：只验证登录、定位好友、确认能打开会话，不发送任何消息。

    对应「怕把消息发错人」的顾虑：先跑一遍全流程，确认无误再真发。
    （好友昵称同样从 query 挪到了请求体，理由同 /Api/Send。）
    """
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    setup_guard_err = setup_guard()
    if setup_guard_err:
        return setup_guard_err
    browser_err = require_browser_session()
    if browser_err:
        return browser_err
    name = (payload or {}).get('name', name)
    if not name:
        return {'code': 400, 'data': '请先选择好友'}
    problem = send_precheck(notify_on_pause=False)
    if problem:
        return {'code': 404, 'data': '预检未通过：%s' % problem}
    try:
        douyin.Updara_FrinderList()
    except Exception as exc:
        log_event('error', '预检', '读取会话列表失败', exc)
        return {'code': 404, 'data': '预检未通过：读取会话列表失败（%s）' % exc_text(exc)}
    if name in (douyin.friend_ambiguous or set()):
        return {'code': 404, 'data': '预检未通过：有多位叫「%s」的会话，无法确定发给谁' % name}
    # 会话列表是虚拟滚动：Updara_FrinderList 只给「当前画面」里那几十行建索引，
    # 滚过去的两百多个会话只有名字、没有可点的位置。发消息那边遇到这种情况会
    # 从顶部滚下去把人找回来（locate_friend），预检以前直接拿索引判「有没有」，
    # 于是「好友列表里明明有、预检却说不存在」—— 这里补上同一步找回。
    row_xpath = douyin.locate_friend(name)
    if row_xpath is None:
        known = douyin.known_names()
        if name in known:
            return {'code': 404, 'data': '预检未通过：「%s」在会话列表里（共读到 %d 个），'
                                         '但滚遍整份列表也没定位到它的那一行。'
                                         '请在抖音窗口里手动滚到它，再点一次预检。'
                                         % (name, len(known))}
        return {'code': 404, 'data': '预检未通过：会话列表里没有「%s」（共读到 %d 个，例如：%s）。'
                                     '请在好友列表里刷新后重新选择。'
                                     % (name, len(known), '、'.join(known[:8]))}
    opened, reason = _open_conversation(name, row_xpath)
    if not opened:
        return {'code': 404, 'data': '预检未通过：%s' % reason}
    editor = _wait_chat_editor(8)
    if editor is None:
        return {'code': 404, 'data': '预检未通过：会话已打开但找不到消息输入框'}
    has_list = _chat_probe('', 'check').get('list', False)
    log_event('info', '发消息', '预检通过：已确认能打开与「%s」的会话（未发送任何消息）' % name)
    return {'code': 200, 'data': '预检通过：已确认可以打开与「%s」的会话，未发送任何消息' % name,
            'message_list_readable': bool(has_list)}


# ==================== 信息日志接口 ====================
@app.get('/Api/Logs')
def GetLogs(page: int = 1, size: int = 50, level: str = None, category: str = None,
            keyword: str = None, authorization: str = Header(None)):
    """读取信息日志（最新在前，支持级别/分类/关键字过滤与分页）。"""
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    data = read_app_log(level=(level or '').strip() or None,
                        category=(category or '').strip() or None,
                        keyword=(keyword or '').strip() or None,
                        page=page, size=size)
    data['levels'] = list(APP_LOG_LEVELS)
    data['categories'] = list(APP_LOG_CATEGORIES)
    return {'code': 200, 'data': data}


@app.post('/Api/Logs/Clear')
def ClearLogs(authorization: str = Header(None)):
    """清空信息日志。"""
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    if not clear_app_log():
        return {'code': 404, 'data': '清空日志失败，请检查 logs 目录权限'}
    log_event('info', '系统', '信息日志已被手动清空')
    return {'code': 200, 'data': '日志已清空'}


# ==================== 安全中心 ====================
# 一次只读巡检，把「面板会不会被别人接管」和「抖音号会不会被风控」拆成一条条
# 可以判定的检查项。为什么不放在前端拼：这些结论要读密码哈希格式、登录节流表、
# 浏览器会话、Cookie 到期时间和文件 mtime —— 前端一个都拿不到。
SECURITY_STATUS_PENALTY = {'risk': 30, 'warn': 10, 'info': 0, 'ok': 0}


def _human_size(num):
    try:
        num = float(num)
    except (TypeError, ValueError):
        return '未知'
    for unit in ('B', 'KB', 'MB'):
        if num < 1024:
            return ('%d B' % num) if unit == 'B' else ('%.1f %s' % (num, unit))
        num /= 1024.0
    return '%.1f GB' % num


def _check(check_id, group, title, status, detail, hint=''):
    return {
        'id': check_id,
        'group': group,
        'title': title,
        'status': status,
        'detail': detail,
        'hint': hint,
    }


def _pkg_version(name):
    """读已安装包的版本号；没装或读不到就返回空串（不编版本号）。"""
    try:
        from importlib import metadata
        return metadata.version(name)
    except Exception:
        return ''


# 只读一次浏览器**自己报出来**的指纹，刻意什么都不改：
# 只有读到真值，面板上显示的才是「抖音实际看到的你」。
_FINGERPRINT_JS = """
return (function () {
  var out = {
    ua: navigator.userAgent || '',
    lang: (navigator.languages || []).join(',') || navigator.language || '',
    tz: '', hc: navigator.hardwareConcurrency || 0,
    dpr: window.devicePixelRatio || 0,
    inner: window.innerWidth + 'x' + window.innerHeight,
    outer: window.outerWidth + 'x' + window.outerHeight,
    webgl: ''
  };
  try { out.tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) {}
  try {
    var c = document.createElement('canvas');
    var gl = c.getContext('webgl') || c.getContext('experimental-webgl');
    if (gl) {
      var ext = gl.getExtension('WEBGL_debug_renderer_info');
      if (ext) {
        out.webgl = (gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) || '') + ' / ' +
                    (gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) || '');
      } else {
        out.webgl = gl.getParameter(gl.VERSION) || '';
      }
    }
  } catch (e) {}
  return out;
})();
"""


def _browser_fingerprint():
    """读一次真实浏览器当前呈现的指纹（只读，不改）。抢不到锁就返回 None。

    抢 browser_lock 是因为正在发消息时同时操作 driver 会打架；
    抢不到就返回 None 而不是编一个值 —— 安全中心的价值全在「如实」。
    """
    if not init or driver is None:
        return None
    if not browser_lock.acquire(timeout=0.5):
        return None
    try:
        data = driver.execute_script(_FINGERPRINT_JS)
    except Exception as exc:
        log_event('warn', '浏览器', '读取浏览器指纹失败', exc)
        return None
    finally:
        browser_lock.release()
    return data if isinstance(data, dict) else None


def _read_auth_cookies():
    """读一次当前浏览器里的抖音登录凭证 Cookie（只读，不改）。

    返回 None 表示「这次没读到」：浏览器没起、没登录、或者抢不到锁。
    调用方必须区分 None 和「读到了但是零个凭证」—— 后者是真出事了。
    """
    if not init or driver is None or not Login_is_bool:
        return None
    if not browser_lock.acquire(timeout=0.5):
        return None
    try:
        names, stamps = [], []
        for item in (driver.get_cookies() or []):
            if not isinstance(item, dict) or item.get('name') not in AUTH_COOKIE_NAMES:
                continue
            names.append(item['name'])
            expiry = item.get('expiry')
            if isinstance(expiry, (int, float)) and expiry > 0:
                stamps.append(float(expiry))
    except Exception as exc:
        log_event('warn', '浏览器', '读取登录凭证 Cookie 失败', exc)
        return None
    finally:
        browser_lock.release()
    earliest = min(stamps) if stamps else None
    return {
        'checked': True,
        'names': sorted(set(names)),
        'auth_cookies': len(set(names)),
        'earliest': datetime.fromtimestamp(earliest).strftime('%Y-%m-%d %H:%M:%S') if earliest else '',
        'earliest_ts': earliest,
        'ahead_hours': round((earliest - time.time()) / 3600.0, 1) if earliest else None,
    }


def _security_checks(request):
    """产出全部检查项。拆出来是为了能单独测，不必起 HTTP 请求。"""
    now = datetime.now()
    checks = []

    # ---------- 面板：密码 ----------
    stored = _stored_password_hash()
    if not stored:
        checks.append(_check(
            'panel_password', '面板', '面板密码', 'risk',
            '仍在使用内置默认密码 123456',
            '任何知道这个程序的人都能登进面板、导出抖音登录凭证。'
            '请到「设置」页改密码，或在服务器执行 python reset_password.py。'))
    elif str(stored).startswith('pbkdf2_sha256$'):
        checks.append(_check(
            'panel_password', '面板', '面板密码', 'ok',
            '已设置自定义密码（PBKDF2-SHA256 加盐，20 万轮）'))
    else:
        checks.append(_check(
            'panel_password', '面板', '面板密码', 'warn',
            '密码是旧版无盐 SHA-256 哈希，强度偏弱',
            '下次用面板登录成功后会透明升级为 PBKDF2；'
            '也可以直接跑 python reset_password.py 重设。'))

    # ---------- 面板：会话 ----------
    session_count = len(_valid_tokens)
    checks.append(_check(
        'panel_sessions', '面板', '登录会话', 'ok',
        '当前 %d 个有效会话，空闲 %d 小时自动失效；最后登录 IP：%s'
        % (session_count, TOKEN_TTL_HOURS, _last_login_ip or '无')))

    # ---------- 面板：登录节流 ----------
    try:
        _prune_login_throttle()
    except Exception:
        pass
    locked = []
    for ip in list(_login_locked.keys()):
        try:
            remaining = int(_login_throttle_remaining(ip))
        except Exception:
            remaining = 0
        if remaining > 0:
            locked.append((ip, remaining))
    failed = {ip: n for ip, n in list(_login_failures.items()) if n}
    if locked:
        checks.append(_check(
            'login_throttle', '面板', '登录节流', 'warn',
            '%d 个来源因连续登录失败被临时锁定：%s'
            % (len(locked), '；'.join('%s 还需 %d 秒' % (ip, sec) for ip, sec in locked)),
            '这是防爆破机制。如果是自己输错密码，等倒计时结束再试。'))
    elif failed:
        checks.append(_check(
            'login_throttle', '面板', '登录节流', 'info',
            '近期有登录失败记录：%s（连续 %d 次会锁定该 IP %d 秒）'
            % ('；'.join('%s 失败 %d 次' % (ip, n) for ip, n in failed.items()),
               LOGIN_FAIL_LIMIT, LOGIN_LOCK_SECONDS),
            '如果这不是你自己输错，说明有人在猜密码，请确认面板没有直接暴露到公网。'))
    else:
        checks.append(_check(
            'login_throttle', '面板', '登录节流', 'ok',
            '没有异常登录尝试（连续失败 %d 次锁定该 IP %d 秒）'
            % (LOGIN_FAIL_LIMIT, LOGIN_LOCK_SECONDS)))

    # ---------- 面板：监听范围 ----------
    if BIND_HOST in ('127.0.0.1', 'localhost', '::1'):
        checks.append(_check(
            'listen_scope', '面板', '监听范围', 'ok',
            '后端只监听 %s，外部无法直连（对外访问请走 nginx 反代 + HTTPS）' % BIND_HOST))
    else:
        checks.append(_check(
            'listen_scope', '面板', '监听范围', 'warn',
            '后端监听在 %s，不只是本机' % BIND_HOST,
            '同网段或公网能直接访问面板端口，密码和 token 会明文过网。'
            '除非确实需要，否则改回 SPARK_HOST=127.0.0.1 并交给 nginx 反代。'))

    # ---------- 面板：传输加密 ----------
    try:
        real_ip = _client_ip(request) or ''
    except Exception:
        real_ip = ''
    local_client = real_ip in ('127.0.0.1', '::1', 'localhost', '')
    proto = (request.headers.get('x-forwarded-proto') or request.url.scheme or '').lower()
    if proto == 'https':
        checks.append(_check(
            'transport', '面板', '传输加密', 'ok',
            '当前通过 HTTPS 访问（来源：%s）' % (real_ip or '未知')))
    elif local_client:
        checks.append(_check(
            'transport', '面板', '传输加密', 'info',
            '当前是明文 HTTP，但请求来自本机（%s），不经过网络' % (real_ip or '本机'),
            '把面板开放给外网时必须套 HTTPS，否则密码和 token 会在链路上被人看到。'))
    else:
        checks.append(_check(
            'transport', '面板', '传输加密', 'risk',
            '通过明文 HTTP 从 %s 访问面板' % (real_ip or '外部地址'),
            '密码和 token 会以明文在网络上传输，请立刻在 nginx 上配 HTTPS。'))

    # ---------- 面板：跨域白名单 ----------
    if '*' in CORS_ORIGINS:
        checks.append(_check(
            'cors', '面板', '跨域白名单', 'warn',
            'CORS 允许任意来源（*）',
            '配合凭据使用时等于对全网开放，请把 SPARK_CORS_ORIGINS 收成具体域名。'))
    else:
        checks.append(_check(
            'cors', '面板', '跨域白名单', 'ok',
            '仅放行 %d 个来源：%s' % (len(CORS_ORIGINS), '、'.join(CORS_ORIGINS) or '无')))

    # ---------- 面板：高权限接口 ----------
    exposed_safely = local_client or proto == 'https'
    checks.append(_check(
        'power_endpoints', '面板', '高权限接口', 'info' if exposed_safely else 'warn',
        '持有面板密码即可调用：/Api/login/Init/GetCooker（导出抖音全量 Cookie）、'
        '/Api/LoginDebug（把会话标记成已登录）、/Api/ChangePassword（改密码并踢掉全部会话）',
        '它们都要 Bearer token，所以风险等于「面板密码泄漏 + 面板可达」（当前：%s）。'
        % ('仅本机' if local_client else ('HTTPS' if proto == 'https' else '明文外网'))))

    # ---------- 账号：抖音登录态 ----------
    browser_ready = bool(init and _driver_alive(probe=False))
    if not init:
        checks.append(_check(
            'douyin_session', '账号', '抖音登录态', 'info',
            '浏览器还没初始化',
            '到首页点「初始化浏览器」，然后在「设置」页扫码登录抖音。'))
    elif not browser_ready:
        checks.append(_check(
            'douyin_session', '账号', '抖音登录态', 'risk',
            '浏览器会话已失效（进程可能已经退出）',
            '重新到首页点一次「初始化浏览器」。在此之前定时任务不会发送。'))
    elif Login_is_bool:
        checks.append(_check(
            'douyin_session', '账号', '抖音登录态', 'ok',
            '已登录抖音，浏览器会话正常'))
    else:
        checks.append(_check(
            'douyin_session', '账号', '抖音登录态', 'warn',
            '浏览器正常，但抖音未登录',
            '到「设置」页扫码登录；未登录时发送会被拦下。'))

    # ---------- 防封号：设备指纹 ----------
    fp = _browser_fingerprint() if browser_ready else None
    if not browser_ready:
        checks.append(_check(
            'fingerprint', '防封号', '设备指纹', 'info',
            '浏览器还没启动，暂未读取',
            '启动浏览器后这里显示的是它**实际报出来**的指纹（UA / 视口 / 缩放 / 时区 / '
            '语言 / CPU 核数 / WebGL 显卡串）—— 浏览器自己报的值，不是我们编的。'))
    elif fp is None:
        checks.append(_check(
            'fingerprint', '防封号', '设备指纹', 'info',
            '暂未读取（浏览器正忙于发送，或读取失败）',
            '点右上角「重新检查」再试一次。'))
    else:
        try:
            dpr = float(fp.get('dpr') or 0)
        except (TypeError, ValueError):
            dpr = 0.0
        detail = ('视口 %s（外窗口 %s，缩放 %.2f）；时区 %s；语言 %s；CPU %s 核；WebGL %s'
                  % (fp.get('inner') or '?', fp.get('outer') or '?', dpr,
                     fp.get('tz') or '?', fp.get('lang') or '?', fp.get('hc') or '?',
                     fp.get('webgl') or '（读不到）'))
        if (CHROME_USER_AGENT or '').strip():
            ua_note = ('UA 被 SPARK_USER_AGENT 覆盖了 —— 真实 Chrome 的 UA 本来就是真的，'
                       '改成假的是反效果，建议清空这个变量')
        else:
            ua_note = '用的是真实 Chrome UA'
        hint = ('%s：%s。\n本机没有启用任何指纹伪造：device_fingerprint.py 是没接线的遗留模块，'
                'backend.py 里对它的引用为 0。这是有意的 —— 浏览器是真的，指纹就是真的，'
                '比手工伪造一套自相矛盾的值更安全。' % (ua_note, fp.get('ua') or '?'))
        if dpr and abs(dpr - 1.0) > 0.01:
            checks.append(_check(
                'fingerprint', '防封号', '设备指纹', 'warn',
                detail + '。缩放 %g 是 --force-device-scale-factor 强制出来的，'
                '真实 Chrome 几乎不会是这种值，等于自己给自己加了个特征' % dpr,
                hint))
        else:
            checks.append(_check(
                'fingerprint', '防封号', '设备指纹', 'ok', detail, hint))

    # ---------- 防封号：自动化引擎 ----------
    selenium_ver = _pkg_version('selenium') or '未知'
    playwright_ver = _pkg_version('playwright')
    if playwright_ver:
        pw_line = ('Playwright %s 已安装，但 playwright_manager.py 没有任何入口引用，'
                   '当前跑的不是这条路' % playwright_ver)
    else:
        pw_line = ('未安装 Playwright；仓库里的 playwright_manager.py 存在但没有任何入口引用，'
                   '装了也不会被用到')
    chrome_ver = ('版本 %s' % _browser_version) if _browser_version else '本轮还没启动过，启动后显示'
    checks.append(_check(
        'automation_engine', '防封号', '自动化引擎',
        'ok' if selenium_ver != '未知' else 'warn',
        'Selenium %s + 真实 Chrome（%s）；%s' % (selenium_ver, chrome_ver, pw_line),
        '抖音这条链路是「真实 Chrome + webdriver 驱动」在跑。换成 Playwright 等于重写 '
        'ensure_browser_ready() 和整个 Douyin 类（200+ 行），而且换过去不会自动更安全：'
        '决定封号率的是网络出口和发送频率，不是驱动库。'))

    # ---------- 防封号：风控识别与自动停发 ----------
    checks.append(_check(
        'risk_detect', '防封号', '风控识别与停发', 'ok',
        '监控 %d 个风控特征词（%s）；命中后暂停发信 %d 分钟，登录失效暂停 %d 分钟'
        % (len(RISK_MARKERS), '、'.join(RISK_MARKERS),
           RISK_PAUSE_SECONDS // 60, AUTH_PAUSE_SECONDS // 60),
        '发送前会先探测页面（RISK_DETECT_JS），看到验证页或「操作频繁」就主动停发，'
        '而不是硬撞。失败还会按指数退避重试、当天补发一次。'
        '这是整条链路上最能降低封号率的一环。'))

    # ---------- 防封号：反检测注入 ----------
    if not init:
        checks.append(_check(
            'anti_detection', '防封号', '反检测注入', 'info',
            '浏览器未初始化，尚未注入',
            '初始化浏览器时会自动注入，不需要额外操作。'))
    elif _stealth_injected:
        checks.append(_check(
            'anti_detection', '防封号', '反检测注入', 'ok',
            '已注入：chromedriver 留在 window 上的 cdc_ 痕迹变量已清空，'
            'navigator.webdriver 为 false',
            '实测注入前有 7 个 cdc_ 变量、注入后为 0（navigator.webdriver 本来就是 false，'
            '那是 --disable-blink-features=AutomationControlled 的功劳）。'
            '这里刻意只抹自动化痕迹、不伪造 UA / 分辨率 / 插件：本机真实 Chrome 的这些值'
            '本来就是真的，改成假的反而更容易被识别。'))
    else:
        checks.append(_check(
            'anti_detection', '防封号', '反检测注入', 'warn',
            '未注入：chromedriver 会在 window 上留下 cdc_ 开头的痕迹变量，页面直接就能读到',
            '先看「信息日志」里有没有「反检测脚本注入失败」，然后重启后端重新初始化浏览器。'))

    # ---------- 账号：发送暂停 ----------
    try:
        pause_reason = _send_pause_reason()
    except Exception:
        pause_reason = None
    if pause_reason:
        checks.append(_check(
            'send_pause', '防封号', '发送暂停', 'warn', pause_reason,
            '这是自动保护：撞到风控或登录失效时会主动停发，避免继续触发风控。'))
    elif using_default_password():
        checks.append(_check(
            'send_pause', '防封号', '发送暂停', 'warn',
            '定时发送处于暂停状态：面板还在用内置默认密码',
            '到「设置」页改掉密码后会自动恢复发送。'))
    else:
        checks.append(_check(
            'send_pause', '防封号', '发送暂停', 'ok', '没有暂停，发送通道正常'))

    # ---------- 账号：登录凭证有效期 ----------
    cookie_info = (_read_auth_cookies() if (browser_ready and Login_is_bool) else None) or {
        'checked': False, 'auth_cookies': 0, 'earliest': '', 'ahead_hours': None}

    if not cookie_info['checked']:
        checks.append(_check(
            'cookie_expiry', '账号', '登录凭证有效期', 'info',
            '暂未读取（浏览器未就绪，或正忙于发送）'))
    elif not cookie_info['auth_cookies']:
        checks.append(_check(
            'cookie_expiry', '账号', '登录凭证有效期', 'risk',
            '没有找到任何抖音登录凭证 Cookie',
            '面板显示已登录但凭证不在，发送前会被拦下。请重新扫码登录抖音。'))
    elif cookie_info['ahead_hours'] is None:
        checks.append(_check(
            'cookie_expiry', '账号', '登录凭证有效期', 'ok',
            '拿到 %d 个登录凭证 Cookie；抖音没有给它们写到期时间（由服务端控制）'
            % cookie_info['auth_cookies'],
            '服务端随时可能让登录态失效。真失效时后端会暂停发送并记日志，不会硬撞。'))
    elif cookie_info['ahead_hours'] < 24:
        checks.append(_check(
            'cookie_expiry', '账号', '登录凭证有效期', 'warn',
            '最早到期的凭证在 %s（约 %s 小时后）'
            % (cookie_info['earliest'], cookie_info['ahead_hours']),
            '到期后会掉登录，请提前重新扫码。'))
    else:
        checks.append(_check(
            'cookie_expiry', '账号', '登录凭证有效期', 'ok',
            '最早到期的凭证在 %s（约 %s 小时后）'
            % (cookie_info['earliest'], cookie_info['ahead_hours'])))

    # ---------- 账号：发送节奏 ----------
    checks.append(_check(
        'pacing', '防封号', '发送节奏', 'info',
        '每天在计划时间前后随机 %d 分钟内发送；定时任务里同一好友当天只发一条'
        % (JITTER_MINUTES,),
        '抖动和按天去重都在，但目前没有「账号级每日发送总量上限」。任务别铺太满，'
        '固定时间、固定条数、天天一样最容易触发风控。'))

    # ---------- 账号：今日发送量 ----------
    history = STATE.get('send_history') or {}
    today = today_str()
    sent_today = sum(1 for key in history if str(key).startswith(today))
    checks.append(_check(
        'daily_volume', '防封号', '今日发送量', 'info',
        '今天已产生 %d 条发送记录；当前定时任务 %d 个' % (sent_today, len(task_meta)),
        '自己心里留个上限：新号、久未使用、刚改过密码的号都要更保守。'))

    # ---------- 数据：状态文件 ----------
    if os.path.exists(STATE_FILE):
        try:
            state_size = os.path.getsize(STATE_FILE)
            state_mtime = datetime.fromtimestamp(
                os.path.getmtime(STATE_FILE)).strftime('%Y-%m-%d %H:%M:%S')
        except OSError:
            state_size, state_mtime = 0, '未知'
        try:
            backups = sum(1 for name in os.listdir(os.path.dirname(STATE_FILE) or '.')
                          if name.startswith('state.json.bak-'))
        except OSError:
            backups = 0
        detail = '状态文件 %s（%s，最后修改 %s）' % (STATE_FILE, _human_size(state_size), state_mtime)
        if backups:
            detail += '；另有 %d 份备份' % backups
        checks.append(_check('state_file', '数据', '状态文件', 'ok', detail))
    else:
        checks.append(_check(
            'state_file', '数据', '状态文件', 'warn',
            '找不到 %s' % STATE_FILE,
            '刚部署时属正常（后端首次启动会创建）。但如果之前明明有任务，'
            '说明数据目录被换过或权限不对。'))

    # ---------- 数据：登录态目录 ----------
    if os.path.isdir(CHROME_PROFILE_DIR):
        lock_file = os.path.join(CHROME_PROFILE_DIR, 'SingletonLock')
        if os.path.exists(lock_file) and not init:
            checks.append(_check(
                'profile_dir', '数据', '登录态目录', 'warn',
                'chrome-profile 里残留 SingletonLock，但后端并没有在跑浏览器',
                '多半是上次异常退出留下的。如果初始化浏览器报「user-data-dir 已被占用」，'
                '关掉所有 Chrome 后删掉 %s。' % lock_file))
        else:
            checks.append(_check(
                'profile_dir', '数据', '登录态目录', 'ok',
                '登录态目录 %s 正常（抖音的登录状态就存在这里，不要删、不要提交进 git）'
                % CHROME_PROFILE_DIR))
    else:
        checks.append(_check(
            'profile_dir', '数据', '登录态目录', 'info',
            '登录态目录还不存在（%s）' % CHROME_PROFILE_DIR,
            '首次初始化浏览器时会自动创建。'))

    # ---------- 数据：运行日志 ----------
    if os.path.exists(APP_LOG_FILE):
        try:
            log_size = _human_size(os.path.getsize(APP_LOG_FILE))
        except OSError:
            log_size = '未知'
        checks.append(_check(
            'app_log', '数据', '运行日志', 'ok',
            '%s（%s）' % (APP_LOG_FILE, log_size),
            '登录、发送、风控暂停等事件都会记在这里，排查问题先看它。'))
    else:
        checks.append(_check('app_log', '数据', '运行日志', 'info', '还没有日志文件'))

    # ---------- 运行时 ----------
    scheduler_alive = bool(_scheduler_thread and _scheduler_thread.is_alive())
    checks.append(_check(
        'runtime', '运行时', '运行状态', 'ok' if scheduler_alive else 'risk',
        '版本 %s，已运行 %d 秒；调度线程%s；定时任务 %d 个；发送队列 %d；待补发 %d'
        % (VERSION, int((now - start_time).total_seconds()),
           '正常' if scheduler_alive else '已停止', len(task_meta), _send_queue.qsize(),
           len(((STATE.get('retry_queue') or {}).get('items')) or [])),
        '' if scheduler_alive else '调度线程没了就不会再自动发送，请重启后端。'))

    return checks, {
        'browser_ready': browser_ready,
        'logged_in': bool(Login_is_bool),
        'stealth_injected': bool(_stealth_injected),
        'browser_version': _browser_version or '未启动',
        'viewport': (fp or {}).get('inner') or '',
        'dpr': (fp or {}).get('dpr') or '',
        'timezone': (fp or {}).get('tz') or '',
        'webgl': (fp or {}).get('webgl') or '',
        'selenium': selenium_ver,
        'playwright': playwright_ver or '未安装',
        'sessions': session_count,
        'sent_today': sent_today,
        'tasks': len(task_meta),
        'bind_host': BIND_HOST,
        'client_ip': real_ip or '本机',
        'scheme': proto or 'http',
    }


@app.get('/Api/Security/Overview')
def SecurityOverview(request: Request, authorization: str = Header(None)):
    """安全中心汇总巡检（只读）。"""
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err

    checks, facts = _security_checks(request)
    penalty = sum(SECURITY_STATUS_PENALTY.get(item['status'], 0) for item in checks)
    score = max(0, 100 - penalty)
    if score >= 85:
        level = 'good'
    elif score >= 60:
        level = 'warn'
    else:
        level = 'risk'
    counts = {'ok': 0, 'info': 0, 'warn': 0, 'risk': 0}
    for item in checks:
        counts[item['status']] = counts.get(item['status'], 0) + 1

    return {
        'code': 200,
        'data': {
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'score': score,
            'level': level,
            'counts': counts,
            'checks': checks,
            'facts': facts,
        },
    }


@app.post('/Api/Security/RevokeAll')
def SecurityRevokeAll(authorization: str = Header(None), payload: dict = Body(None)):
    """把全部面板会话踢下线（包括发起这次请求的这一个）。

    要求重新输一次密码：这是「怀疑面板密码泄漏」时按的按钮，
    只凭一个可能已经泄漏的 token 就能把主人自己锁在外面不合理。
    """
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    body = payload or {}
    if not check_password(body.get('password')):
        return {'code': 400, 'data': '密码不正确'}
    revoked = _valid_tokens.revoke_all()
    log_event('warn', '系统', '已吊销全部面板会话（安全中心手动操作）', {'revoked': revoked})
    return {'code': 200, 'data': {'revoked': revoked}}


# ==================== 抖音账号 ====================
# 单用户模式：本程序只登录一个抖音账号，就是 chrome-profile/ 里的那一个。
# 所以这里是「账号信息」而不是「账号管理」—— 没有增删改账号这回事。
# 除了用户自己填的备注，其余字段全部从真实运行状态里现读，不落盘、不编造。
# 历史背景：v3 多用户版有 douyin_accounts 表和一套账号 CRUD，已随多用户版一起删除；
# 面板上也一度有个 Accounts 页面调用 /Api/Accounts/*，而 backend.py 从没实现过那些路由，
# 那个页面点开永远是空的 —— 所以它当时被删掉了。这里是为它补上真实实现。
ACCOUNT_NOTE_MAX = 100


def _account_meta():
    """抖音账号的本地备注信息（备注 / 昵称 / 添加时间）。读不到就是空字典。"""
    meta = STATE.get('douyin_account')
    return dict(meta) if isinstance(meta, dict) else {}


def _account_meta_update(**fields):
    meta = _account_meta()
    meta.update(fields)
    STATE.set('douyin_account', meta)
    return meta


def _account_mark_seen(nickname=None):
    """第一次确认真实登录成功时记下时间；有昵称就顺手存下来。

    只写一次（added_at 已有就不再改），避免每轮轮询都写盘。
    """
    meta = _account_meta()
    changed = {}
    if not meta.get('added_at'):
        changed['added_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if nickname and nickname != meta.get('nickname'):
        changed['nickname'] = nickname
    if changed:
        _account_meta_update(**changed)
    return changed


@app.get('/Api/Account/Info')
@serialized
def AccountInfo(authorization: str = Header(None)):
    """当前抖音账号的真实状态（单用户模式下一个账号）。

    要挂 @serialized：这里虽然只是「读」，但读的是 WebDriver（cookie 到期时间）
    和 douyin 好友列表缓存，跨线程读 driver 和跨线程写一样是未定义行为 ——
    发送进行中被前端刷新一次，就可能读到半个页面甚至让发送流程报未确认。
    browser_lock 是 RLock，所以里面 _read_auth_cookies() 再拿一次锁不会自锁。
    """
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err

    meta = _account_meta()
    browser_ok = bool(init and _driver_alive(probe=False))
    logged_in = bool(browser_ok and Login_is_bool)

    # 已经真的登录了但还没记过添加时间 —— 现在补上（只会写这一次）。
    if logged_in and not meta.get('added_at'):
        _account_mark_seen()
        meta = _account_meta()

    cookie = _read_auth_cookies() if logged_in else None
    if not logged_in:
        cookie_status, cookie_text = 'unlogged', '未登录'
    elif cookie is None:
        cookie_status, cookie_text = 'unknown', '暂未读取（浏览器正忙）'
    elif not cookie['auth_cookies']:
        cookie_status, cookie_text = 'missing', '异常：没有凭证 Cookie'
    elif cookie['ahead_hours'] is None:
        cookie_status, cookie_text = 'valid', '有效（%d 个凭证，无到期时间）' % cookie['auth_cookies']
    elif cookie['ahead_hours'] < 0:
        cookie_status, cookie_text = 'expired', '已过期'
    elif cookie['ahead_hours'] < 24:
        cookie_status, cookie_text = 'expiring', '即将到期（%.1f 小时后）' % cookie['ahead_hours']
    else:
        cookie_status, cookie_text = 'valid', '有效（%d 个凭证）' % cookie['auth_cookies']

    # 好友数只从内存里读现成的，不为了刷新一个数字去开一次好友列表。
    friend_count = None
    cached_friends = _cache_get('friends')
    if isinstance(cached_friends, dict) and isinstance(cached_friends.get('data'), dict):
        value = cached_friends['data'].get('count')
        if isinstance(value, int):
            friend_count = value
    if friend_count is None and douyin is not None:
        names = getattr(douyin, 'friends_xpath_list', None)
        if names:
            friend_count = len(names)

    today = datetime.now().strftime('%Y-%m-%d')
    sent_today = sum(1 for key in (STATE.get('send_history') or {}) if str(key).startswith(today))

    return {'code': 200, 'data': {
        'id': 'default',
        'nickname': meta.get('nickname') or '',
        'note': meta.get('note') or '',
        'note_max': ACCOUNT_NOTE_MAX,
        'added_at': meta.get('added_at') or '',
        'cookie_status': cookie_status,
        'cookie_text': cookie_text,
        'cookie_count': cookie['auth_cookies'] if cookie else 0,
        'cookie_names': cookie['names'] if cookie else [],
        'cookie_expire': cookie['earliest'] if cookie else '',
        'cookie_ahead_hours': cookie['ahead_hours'] if cookie else None,
        'friend_count': friend_count,
        'friend_count_known': friend_count is not None,
        'sent_today': sent_today,
        'logged_in': logged_in,
        'browser_ready': browser_ok,
        'stealth_injected': bool(_stealth_injected),
        'engine': 'Selenium %s + Chrome %s'
                  % (_pkg_version('selenium') or '未知', _browser_version or '未启动'),
        'profile_dir': CHROME_PROFILE_DIR,
    }}


@app.post('/Api/Account/Note')
def AccountSetNote(authorization: str = Header(None), payload: dict = Body(None)):
    """保存账号备注：纯本地标记，不影响登录和发送。"""
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err
    body = payload if isinstance(payload, dict) else {}
    note = str(body.get('note') or '').strip()
    if len(note) > ACCOUNT_NOTE_MAX:
        return {'code': 400, 'data': '备注最多 %d 个字' % ACCOUNT_NOTE_MAX}
    _account_meta_update(note=note)
    log_event('info', '账号', '更新抖音账号备注', note)
    return {'code': 200, 'data': {'note': note}}


# ==================== 消息记录 ====================
# 面板上「消息记录」页读的就是这里。
#
# 数据源只有一个：state.json 里的 send_history —— 也就是发送记账本身。
# 不另建一份记录，是因为记账是**已经存在且必须存在**的（防重复发送靠它），
# 再抄一份只会多出一个可能对不上的真相；而且进程被 kill 掉时，唯一写得进去的
# 就是这份。
#
# 键的格式要注意历史包袱：老版本把文案哈希也拼进键里（'日期|好友|哈希'），
# 现在只保留 '日期|好友'。所以这里两种都解析，否则用户在旧数据上会看到空名字。
HISTORY_STATUS_TEXT = {
    'success': '成功',
    'failed': '失败',
    'unknown': '结果未确认',
}


def _history_rows():
    """把 send_history 摊平成按时间倒序的列表。"""
    history = STATE.get('send_history') or {}
    rows = []
    for key, value in history.items():
        parts = str(key).split('|')
        record = value if isinstance(value, dict) else {}
        status = str(record.get('status') or 'unknown')
        kind = str(record.get('kind') or '')
        rows.append({
            'key': str(key),
            'day': parts[0] if parts else '',
            'name': parts[1] if len(parts) > 1 else '',
            'status': status,
            'status_text': HISTORY_STATUS_TEXT.get(status, status),
            'kind': kind,
            'kind_text': KIND_LABELS.get(kind, '') if kind else '',
            'at': str(record.get('at') or ''),
            'text': str(record.get('text') or ''),
            'detail': str(record.get('detail') or ''),
        })
    # 时间倒序。没有 at 的（手改过 / 异常数据）退回按日期排，保证顺序稳定可翻页。
    rows.sort(key=lambda item: (item['at'] or item['day'] or '', item['key']), reverse=True)
    return rows


@app.get('/Api/History/List')
def HistoryList(page: int = 1, size: int = 20, status: str = None, keyword: str = None,
                days: int = 0, authorization: str = Header(None)):
    """发送记录列表（分页 + 筛选）。"""
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err

    all_rows = _history_rows()

    # 统计始终基于**全部**记录，不随筛选变化 —— 否则用户一筛「失败」，
    # 头上的「成功 N 条」就跟着变成 0，看不出整体情况。
    stats = {'success': 0, 'failed': 0, 'unknown': 0}
    for row in all_rows:
        if row['status'] in stats:
            stats[row['status']] += 1

    rows = all_rows
    if status in HISTORY_STATUS_TEXT:
        rows = [row for row in rows if row['status'] == status]

    if days and days > 0:
        cutoff = (datetime.now() - timedelta(days=int(days) - 1)).strftime('%Y-%m-%d')
        rows = [row for row in rows if row['day'] >= cutoff]

    keyword = (keyword or '').strip()
    if keyword:
        lowered = keyword.lower()
        rows = [row for row in rows
                if lowered in row['name'].lower()
                or lowered in row['text'].lower()
                or lowered in row['detail'].lower()]

    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = 20
    page = max(1, page)
    size = min(200, max(1, size))

    total = len(rows)
    pages = max(1, (total + size - 1) // size)
    page = min(page, pages)
    start = (page - 1) * size

    retry_state = STATE.get('retry_queue') or {}
    retry_items = retry_state.get('items') or []
    manual = STATE.get('manual_retries') or {}

    return {'code': 200, 'data': {
        'list': rows[start:start + size],
        'total': total,
        'page': page,
        'size': size,
        'pages': pages,
        'stats': stats,
        'keep_days': HISTORY_KEEP_DAYS,
        'record_count': len(all_rows),
        'oldest': all_rows[-1]['day'] if all_rows else '',
        'newest': all_rows[0]['day'] if all_rows else '',
        # 补发队列和人工重发标记也一并给出来：它们解释了「为什么会有记录」，
        # 但本身不是记录，所以不混进 list 里。
        'retry': {
            'date': str(retry_state.get('date') or ''),
            'due_at': str(retry_state.get('due_at') or ''),
            'done': bool(retry_state.get('done')),
            'names': [str(item.get('name') or '')
                      for item in retry_items if isinstance(item, dict)],
        },
        'manual_retries': sorted(str(key) for key in manual.keys()),
    }}


@app.post('/Api/History/Clear')
def HistoryClear(payload: dict = Body(default=None), authorization: str = Header(None)):
    """清空消息记录（台账）。

    这份记账同时是「今天已经给这个人发过」的判定依据，所以默认只清今天以前的
    记录（scope='old'）；要连今天一起清必须显式 scope='all'。清空只影响台账，
    不改动任何发送状态。
    """
    auth_err = require_auth(authorization)
    if auth_err:
        return auth_err

    body = payload if isinstance(payload, dict) else {}
    scope = str(body.get('scope') or 'old')
    if scope not in ('old', 'all'):
        return {'code': 400, 'data': '清空范围不支持，只支持 old 或 all'}

    today = today_str()
    with _history_lock:
        history = STATE.get('send_history') or {}
        if not isinstance(history, dict):
            history = {}
        if scope == 'all':
            kept = {}
        else:
            # 键是「日期|好友」（旧版还有「日期|好友|哈希」），日期取第一段。
            kept = {key: value for key, value in history.items()
                    if str(key).split('|', 1)[0] == today}
        removed = len(history) - len(kept)
        STATE.set('send_history', kept)

    log_event('info', '消息记录',
              '清空了 %d 条消息记录（scope=%s）' % (removed, scope))
    return {'code': 200, 'data': {'removed': removed, 'kept': len(kept), 'scope': scope}}


# ==================== 健康检查 ====================
@app.get('/healthz')
def Healthz():
    """免鉴权的探活接口，给 Docker HEALTHCHECK / systemd / nginx 用。

    只回状态摘要，不含任何凭据：容器编排和监控要靠它判断进程是不是真的活着
    （之前的写法是「端口开着就算活着」，调度线程死了也发现不了）。
    """
    now = datetime.now()
    return {
        'code': 200,
        'data': {
            'status': 'ok',
            'version': VERSION,
            'started_at': start_time.strftime('%Y-%m-%d %H:%M:%S'),
            'uptime_seconds': int((now - start_time).total_seconds()),
            # 只读心跳，不碰 WebDriver：这个接口免鉴权，而且 Docker HEALTHCHECK
            # 每 30 秒就会打一次，绝不能让它去戳一个可能已经卡死的浏览器会话。
            'browser_ready': bool(init and _driver_alive(probe=False)),
            'logged_in': bool(Login_is_bool),
            'tasks': len(task_meta),
            'scheduler_alive': bool(_scheduler_thread and _scheduler_thread.is_alive()),
            'queue_depth': _send_queue.qsize(),
            'pending_retry': len(((STATE.get('retry_queue') or {}).get('items')) or []),
        },
    }


if __name__ == "__main__":
    # 任务的恢复与调度线程的启动都交给 lifespan（uvicorn 启动时会触发），
    # 这样 `python backend.py` 和 `uvicorn backend:app` 两种启动方式行为一致；
    # 旧实现只在 __main__ 里恢复任务，用 uvicorn 启动时任务会全部消失。
    port = int(os.getenv('PORT', '9844'))
    uvicorn.run(
        app,
        host=BIND_HOST,
        port=port,
        reload=False
    )
