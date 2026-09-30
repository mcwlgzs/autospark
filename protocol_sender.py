"""
AutoSpark v3.0 - 协议发送模块（实验性）
使用抖音API协议直接发送消息，无需启动浏览器
"""
import requests
import json
import time
import hashlib
import random
from typing import Dict, Optional, Tuple
from urllib.parse import urlencode
import logging

logger = logging.getLogger(__name__)


class DouyinProtocolSender:
    """抖音协议发送器"""

    def __init__(self, cookie_data: str):
        """
        初始化协议发送器

        Args:
            cookie_data: Cookie字符串
        """
        self.session = requests.Session()
        self.cookie_data = cookie_data
        self._parse_cookies()

        # 设置请求头
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Referer': 'https://www.douyin.com/',
            'Origin': 'https://www.douyin.com',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
        })

    def _parse_cookies(self):
        """解析Cookie字符串"""
        cookies = {}
        for item in self.cookie_data.split(';'):
            item = item.strip()
            if '=' in item:
                key, value = item.split('=', 1)
                cookies[key] = value

        for key, value in cookies.items():
            self.session.cookies.set(key, value)

    @staticmethod
    def _generate_msToken(length: int = 107) -> str:
        """生成msToken"""
        chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+-_='
        return ''.join(random.choice(chars) for _ in range(length))

    @staticmethod
    def _generate_ttwid() -> str:
        """生成ttwid"""
        timestamp = str(int(time.time() * 1000))
        random_str = ''.join(random.choices('0123456789abcdef', k=16))
        return f"{timestamp}|{random_str}"

    def _get_common_params(self) -> Dict[str, str]:
        """获取通用参数"""
        return {
            'device_platform': 'webapp',
            'aid': '6383',
            'channel': 'channel_pc_web',
            'pc_client_type': '1',
            'version_code': '170400',
            'version_name': '17.4.0',
            'cookie_enabled': 'true',
            'screen_width': '1920',
            'screen_height': '1080',
            'browser_language': 'zh-CN',
            'browser_platform': 'Win32',
            'browser_name': 'Chrome',
            'browser_version': '120.0.0.0',
            'browser_online': 'true',
            'msToken': self._generate_msToken(),
        }

    def send_message(self, to_uid: str, content: str) -> Tuple[bool, str]:
        """
        发送消息（协议方式）

        Args:
            to_uid: 接收者UID
            content: 消息内容

        Returns:
            (是否成功, 消息)
        """
        try:
            # 构建请求URL
            url = 'https://www.douyin.com/aweme/v1/im/send/msg/'

            # 构建参数
            params = self._get_common_params()
            params.update({
                'to_user_id': to_uid,
                'msg_type': '1',  # 1=文本消息
                'content': content,
            })

            # 发送请求
            response = self.session.post(url, params=params, timeout=10)

            if response.status_code != 200:
                return False, f'HTTP状态码错误: {response.status_code}'

            result = response.json()

            # 检查返回结果
            if result.get('status_code') == 0:
                return True, '消息发送成功'
            else:
                error_msg = result.get('status_msg', '未知错误')
                return False, f'发送失败: {error_msg}'

        except requests.exceptions.Timeout:
            return False, '请求超时'
        except requests.exceptions.RequestException as e:
            return False, f'网络请求失败: {str(e)}'
        except json.JSONDecodeError:
            return False, '响应解析失败'
        except Exception as e:
            logger.error(f"协议发送异常: {str(e)}")
            return False, f'发送异常: {str(e)}'

    def get_conversation_list(self) -> Tuple[bool, Optional[list], str]:
        """
        获取会话列表

        Returns:
            (是否成功, 会话列表, 消息)
        """
        try:
            url = 'https://www.douyin.com/aweme/v1/im/conversation/list/'

            params = self._get_common_params()
            params.update({
                'cursor': '0',
                'limit': '20',
            })

            response = self.session.get(url, params=params, timeout=10)

            if response.status_code != 200:
                return False, None, f'HTTP状态码错误: {response.status_code}'

            result = response.json()

            if result.get('status_code') == 0:
                conversations = result.get('data', {}).get('conversations', [])
                return True, conversations, '获取成功'
            else:
                error_msg = result.get('status_msg', '未知错误')
                return False, None, f'获取失败: {error_msg}'

        except Exception as e:
            logger.error(f"获取会话列表异常: {str(e)}")
            return False, None, f'获取异常: {str(e)}'

    def check_cookie_valid(self) -> bool:
        """
        检查Cookie是否有效

        Returns:
            是否有效
        """
        try:
            url = 'https://www.douyin.com/aweme/v1/web/query/user/'

            params = self._get_common_params()

            response = self.session.get(url, params=params, timeout=10)

            if response.status_code != 200:
                return False

            result = response.json()
            return result.get('status_code') == 0

        except Exception as e:
            logger.error(f"检查Cookie有效性异常: {str(e)}")
            return False


class MessageSenderFactory:
    """消息发送器工厂"""

    @staticmethod
    def create_sender(mode: str, **kwargs):
        """
        创建消息发送器

        Args:
            mode: 发送模式 'browser' 或 'protocol'
            **kwargs: 其他参数

        Returns:
            发送器实例
        """
        if mode == 'protocol':
            cookie_data = kwargs.get('cookie_data')
            if not cookie_data:
                raise ValueError("协议模式需要提供cookie_data参数")
            return DouyinProtocolSender(cookie_data)
        elif mode == 'browser':
            # 浏览器模式使用原有的selenium实现
            from douyin import DouyinAutomation
            return DouyinAutomation(**kwargs)
        else:
            raise ValueError(f"不支持的发送模式: {mode}")

    @staticmethod
    def get_available_modes() -> list:
        """获取可用的发送模式"""
        return ['browser', 'protocol']

    @staticmethod
    def get_mode_description(mode: str) -> str:
        """获取模式描述"""
        descriptions = {
            'browser': '浏览器自动化模式（默认，稳定性高）',
            'protocol': '协议发送模式（实验性，无需浏览器，速度快但可能失败）',
        }
        return descriptions.get(mode, '未知模式')


def send_message_with_fallback(cookie_data: str, to_uid: str, content: str,
                               preferred_mode: str = 'browser',
                               allow_fallback: bool = True) -> Tuple[bool, str, str]:
    """
    带降级的消息发送

    Args:
        cookie_data: Cookie数据
        to_uid: 接收者UID
        content: 消息内容
        preferred_mode: 首选模式
        allow_fallback: 是否允许降级到浏览器模式

    Returns:
        (是否成功, 消息, 实际使用的模式)
    """
    # 尝试首选模式
    try:
        if preferred_mode == 'protocol':
            logger.info(f"尝试使用协议模式发送消息到 {to_uid}")
            sender = DouyinProtocolSender(cookie_data)
            success, msg = sender.send_message(to_uid, content)

            if success:
                return True, msg, 'protocol'

            logger.warning(f"协议模式发送失败: {msg}")

            # 如果允许降级，切换到浏览器模式
            if allow_fallback:
                logger.info("降级到浏览器模式")
                from douyin import DouyinAutomation

                automation = DouyinAutomation()
                # 这里需要实现浏览器模式的发送逻辑
                # success = automation.send_message_with_cookie(cookie_data, to_uid, content)
                return False, "浏览器模式降级功能待实现", 'browser'
            else:
                return False, msg, 'protocol'

        elif preferred_mode == 'browser':
            logger.info(f"使用浏览器模式发送消息到 {to_uid}")
            from douyin import DouyinAutomation

            automation = DouyinAutomation()
            # 这里需要实现浏览器模式的发送逻辑
            return False, "浏览器模式功能待集成", 'browser'

        else:
            return False, f"不支持的发送模式: {preferred_mode}", preferred_mode

    except Exception as e:
        logger.error(f"消息发送异常: {str(e)}")
        return False, f"发送异常: {str(e)}", preferred_mode
