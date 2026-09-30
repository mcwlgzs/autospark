"""
设备指纹生成和管理模块
用于生成稳定的设备指纹，防止账号被检测为机器人
"""

import hashlib
import random
import string
import platform
import uuid
from typing import Dict, Optional
from datetime import datetime


class DeviceFingerprint:
    """设备指纹生成器"""

    @staticmethod
    def generate_stable_fp(seed: Optional[str] = None) -> str:
        """
        生成稳定的设备指纹

        Args:
            seed: 可选的种子，使用相同种子会生成相同指纹

        Returns:
            32位十六进制字符串
        """
        if seed:
            # 基于种子生成稳定指纹
            return hashlib.sha256(seed.encode()).hexdigest()[:32]

        # 基于系统信息生成半随机指纹
        components = [
            platform.system(),
            platform.version(),
            platform.machine(),
            str(uuid.getnode()),  # MAC地址
            ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        ]
        raw = '|'.join(components)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    @staticmethod
    def generate_random_fp() -> str:
        """生成完全随机的设备指纹（用于一次性场景）"""
        random_str = ''.join(random.choices(
            string.ascii_lowercase + string.digits,
            k=32
        ))
        return hashlib.sha256(random_str.encode()).hexdigest()[:32]

    @staticmethod
    def get_browser_fingerprint(driver) -> Dict:
        """
        从浏览器获取详细指纹信息

        Args:
            driver: Selenium WebDriver实例

        Returns:
            包含各种指纹数据的字典
        """
        try:
            fingerprint_data = driver.execute_script("""
                return {
                    // Canvas指纹
                    canvas: (() => {
                        try {
                            const canvas = document.createElement('canvas');
                            const ctx = canvas.getContext('2d');
                            ctx.textBaseline = 'top';
                            ctx.font = '14px Arial';
                            ctx.fillStyle = '#f60';
                            ctx.fillRect(125, 1, 62, 20);
                            ctx.fillStyle = '#069';
                            ctx.fillText('Browser Fingerprint 🚀', 2, 15);
                            ctx.fillStyle = 'rgba(102, 204, 0, 0.7)';
                            ctx.fillText('Browser Fingerprint 🚀', 4, 17);
                            return canvas.toDataURL();
                        } catch(e) {
                            return 'error';
                        }
                    })(),

                    // WebGL指纹
                    webgl: (() => {
                        try {
                            const canvas = document.createElement('canvas');
                            const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
                            if (!gl) return null;
                            const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
                            return {
                                vendor: gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL),
                                renderer: gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL),
                                version: gl.getParameter(gl.VERSION),
                                shadingLanguageVersion: gl.getParameter(gl.SHADING_LANGUAGE_VERSION)
                            };
                        } catch(e) {
                            return null;
                        }
                    })(),

                    // 插件信息
                    plugins: Array.from(navigator.plugins || []).map(p => ({
                        name: p.name,
                        description: p.description,
                        filename: p.filename
                    })),

                    // 屏幕信息
                    screen: {
                        width: screen.width,
                        height: screen.height,
                        availWidth: screen.availWidth,
                        availHeight: screen.availHeight,
                        colorDepth: screen.colorDepth,
                        pixelDepth: screen.pixelDepth
                    },

                    // 浏览器信息
                    navigator: {
                        userAgent: navigator.userAgent,
                        language: navigator.language,
                        languages: Array.from(navigator.languages || []),
                        platform: navigator.platform,
                        hardwareConcurrency: navigator.hardwareConcurrency,
                        deviceMemory: navigator.deviceMemory,
                        maxTouchPoints: navigator.maxTouchPoints,
                        vendor: navigator.vendor,
                        vendorSub: navigator.vendorSub,
                        productSub: navigator.productSub
                    },

                    // 时区信息
                    timezone: {
                        offset: new Date().getTimezoneOffset(),
                        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone
                    },

                    // 字体检测（简化版）
                    fonts: (() => {
                        try {
                            const baseFonts = ['monospace', 'sans-serif', 'serif'];
                            const testFonts = ['Arial', 'Courier New', 'Georgia', 'Times New Roman', 'Verdana'];
                            const canvas = document.createElement('canvas');
                            const ctx = canvas.getContext('2d');
                            const detected = [];

                            baseFonts.forEach(baseFont => {
                                ctx.font = '72px ' + baseFont;
                                const baseWidth = ctx.measureText('mmmmmmmmmmlli').width;

                                testFonts.forEach(testFont => {
                                    ctx.font = '72px ' + testFont + ',' + baseFont;
                                    const testWidth = ctx.measureText('mmmmmmmmmmlli').width;
                                    if (testWidth !== baseWidth) {
                                        detected.push(testFont);
                                    }
                                });
                            });

                            return [...new Set(detected)];
                        } catch(e) {
                            return [];
                        }
                    })(),

                    // Audio指纹
                    audio: (() => {
                        try {
                            const AudioContext = window.AudioContext || window.webkitAudioContext;
                            if (!AudioContext) return null;

                            const context = new AudioContext();
                            const oscillator = context.createOscillator();
                            const analyser = context.createAnalyser();
                            const gainNode = context.createGain();
                            const scriptProcessor = context.createScriptProcessor(4096, 1, 1);

                            gainNode.gain.value = 0;
                            oscillator.connect(analyser);
                            analyser.connect(scriptProcessor);
                            scriptProcessor.connect(gainNode);
                            gainNode.connect(context.destination);

                            oscillator.start(0);

                            return {
                                sampleRate: context.sampleRate,
                                state: context.state,
                                maxChannelCount: context.destination.maxChannelCount
                            };
                        } catch(e) {
                            return null;
                        }
                    })()
                };
            """)

            return fingerprint_data

        except Exception as e:
            return {'error': str(e)}

    @staticmethod
    def calculate_fingerprint_hash(fingerprint_data: Dict) -> str:
        """
        计算指纹数据的哈希值

        Args:
            fingerprint_data: get_browser_fingerprint返回的字典

        Returns:
            指纹哈希值
        """
        import json

        # 将字典转为稳定的JSON字符串
        json_str = json.dumps(fingerprint_data, sort_keys=True)
        return hashlib.sha256(json_str.encode()).hexdigest()

    @staticmethod
    def generate_douyin_fp() -> str:
        """
        生成抖音风格的设备指纹
        抖音的fp格式通常为：verify_xxxxxx_xxxxxxxx_xxxxxxxx_xxxxxxxx

        Returns:
            抖音风格的设备指纹字符串
        """
        timestamp = int(datetime.now().timestamp() * 1000)
        random_part1 = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        random_part2 = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        random_part3 = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

        return f"verify_{random_part1}_{random_part2}_{random_part3}_{timestamp}"

    @staticmethod
    def validate_fp(fp: str) -> bool:
        """
        验证设备指纹格式是否有效

        Args:
            fp: 设备指纹字符串

        Returns:
            是否有效
        """
        if not fp:
            return False

        # 检查抖音风格指纹
        if fp.startswith('verify_'):
            parts = fp.split('_')
            return len(parts) == 5 and all(part for part in parts)

        # 检查标准32位十六进制指纹
        if len(fp) == 32:
            try:
                int(fp, 16)
                return True
            except ValueError:
                return False

        return False


class FingerprintManager:
    """设备指纹管理器（带缓存）"""

    def __init__(self):
        self._cache: Dict[str, str] = {}

    def get_or_create_fp(self, account_id: int, existing_fp: Optional[str] = None) -> str:
        """
        获取或创建设备指纹

        Args:
            account_id: 账号ID
            existing_fp: 已存在的指纹（如果有）

        Returns:
            设备指纹
        """
        cache_key = f"account_{account_id}"

        # 1. 检查缓存
        if cache_key in self._cache:
            return self._cache[cache_key]

        # 2. 使用已存在的指纹
        if existing_fp and DeviceFingerprint.validate_fp(existing_fp):
            self._cache[cache_key] = existing_fp
            return existing_fp

        # 3. 生成新指纹（基于账号ID，保证稳定性）
        seed = f"douyin_account_{account_id}"
        new_fp = DeviceFingerprint.generate_stable_fp(seed)
        self._cache[cache_key] = new_fp

        return new_fp

    def generate_session_fp(self, account_id: int) -> str:
        """
        生成会话级别的指纹（每次会话不同）

        Args:
            account_id: 账号ID

        Returns:
            会话指纹
        """
        return DeviceFingerprint.generate_douyin_fp()

    def clear_cache(self, account_id: Optional[int] = None):
        """清除指纹缓存"""
        if account_id is None:
            self._cache.clear()
        else:
            cache_key = f"account_{account_id}"
            self._cache.pop(cache_key, None)


# 使用示例
if __name__ == "__main__":
    # 1. 生成稳定指纹
    fp1 = DeviceFingerprint.generate_stable_fp("account_123")
    fp2 = DeviceFingerprint.generate_stable_fp("account_123")
    print(f"稳定指纹测试: {fp1 == fp2}")  # True

    # 2. 生成随机指纹
    random_fp = DeviceFingerprint.generate_random_fp()
    print(f"随机指纹: {random_fp}")

    # 3. 生成抖音风格指纹
    douyin_fp = DeviceFingerprint.generate_douyin_fp()
    print(f"抖音指纹: {douyin_fp}")

    # 4. 验证指纹
    print(f"抖音指纹有效: {DeviceFingerprint.validate_fp(douyin_fp)}")
    print(f"随机指纹有效: {DeviceFingerprint.validate_fp(random_fp)}")

    # 5. 使用管理器
    manager = FingerprintManager()
    fp_a = manager.get_or_create_fp(1)
    fp_b = manager.get_or_create_fp(1)
    print(f"管理器缓存测试: {fp_a == fp_b}")  # True
