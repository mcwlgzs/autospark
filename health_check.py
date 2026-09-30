"""
抖音火花助手（单用户版）环境自检。

用法：
    python health_check.py

检查 Python 版本、依赖是否装齐、核心文件是否在、Chrome / chromedriver
能不能找到、backend.py 能不能正常导入。任何一项红了就先修它，
不然启动后端时只会看到更难懂的报错。

输出的标记一律用 ASCII（[OK] / [WARN] / [ERROR]），不用 ✓ ✗ ⚠ 这类符号：
Windows 控制台默认是 GBK 代码页，打这些字符会直接抛 UnicodeEncodeError。
"""

import importlib
import os
import shutil
import sys
from pathlib import Path

# 无论从哪个目录调用，都按脚本所在目录来判断文件在不在。
BASE_DIR = Path(__file__).resolve().parent
os.chdir(BASE_DIR)
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

OK = '[OK]'
WARN = '[WARN]'
ERROR = '[ERROR]'


def _safe_print(text):
    """控制台编码不认识某个字符时退化成替换字符，不让自检自己崩掉。"""
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or 'ascii'
        print(text.encode(encoding, errors='replace').decode(encoding, errors='replace'))


class HealthChecker:
    """系统健康检查器"""

    def __init__(self):
        self.errors = []
        self.warnings = []
        self.info = []

    def check_python_version(self):
        """检查 Python 版本"""
        version = sys.version_info
        if version < (3, 10):
            self.errors.append(
                f"Python 版本过低: {version.major}.{version.minor}，本项目需要 3.10 及以上"
            )
        else:
            self.info.append(f"Python 版本: {version.major}.{version.minor}.{version.micro}")

    def check_required_modules(self):
        """检查 backend.py 真正需要的第三方模块"""
        required = {
            'fastapi': 'Web 框架',
            'uvicorn': 'ASGI 服务器',
            'selenium': '浏览器自动化',
            'requests': 'HTTP 客户端',
        }

        for module, purpose in required.items():
            try:
                importlib.import_module(module)
                self.info.append(f"模块 {module} 已安装（{purpose}）")
            except ImportError:
                self.errors.append(
                    f"模块 {module} 未安装（{purpose}）—— 先跑 pip install -r requirements.txt"
                )

        # python-multipart 的导入名随版本变过：新版叫 python_multipart，旧版叫 multipart。
        for name in ('python_multipart', 'multipart'):
            try:
                importlib.import_module(name)
                self.info.append(f"模块 {name} 已安装（FastAPI 表单解析）")
                break
            except ImportError:
                continue
        else:
            self.errors.append(
                "模块 python-multipart 未安装（FastAPI 表单解析）—— 先跑 pip install -r requirements.txt"
            )

    def check_project_files(self):
        """检查项目核心文件"""
        required_files = [
            'backend.py',
            'spark_core.py',
            'state_store.py',
            'notifier.py',
            'selenium_stealth.py',
            'health_check.py',
            'requirements.txt',
            'package.json',
            'vite.config.js',
            'index.html',
            'src/main.js',
            'start-backend.ps1',
            'start-backend.sh',
        ]

        for file in required_files:
            if os.path.exists(file):
                self.info.append(f"文件 {file} 存在")
            else:
                self.errors.append(f"文件 {file} 不存在")

    def check_directories(self):
        """检查必需的目录"""
        required_dirs = ['src', 'src/views', 'tests', 'deploy', 'docs']
        for dir_name in required_dirs:
            if os.path.isdir(dir_name):
                self.info.append(f"目录 {dir_name}/ 存在")
            else:
                self.warnings.append(f"目录 {dir_name}/ 不存在")

        # 运行时目录：不存在不是错误，后端第一次启动会自己建。
        for dir_name in ('data', 'logs'):
            if os.path.isdir(dir_name):
                self.info.append(f"目录 {dir_name}/ 存在")
            else:
                self.warnings.append(f"目录 {dir_name}/ 不存在（后端首次启动会自动创建）")

    def check_frontend(self):
        """检查面板依赖"""
        if os.path.isdir('node_modules'):
            self.info.append("node_modules/ 已安装，可以直接 npm run dev")
        else:
            self.warnings.append("node_modules/ 不存在 —— 跑面板前先执行 npm install")

        if os.path.isdir('dist'):
            self.info.append("dist/ 已构建（nginx 可直接托管这个目录）")
        else:
            self.warnings.append("dist/ 不存在 —— 要部署静态站点时先执行 npm run build")

    def check_runtime(self):
        """导入 backend.py，并检查 Chrome / chromedriver 是否可用"""
        try:
            import backend
        except Exception as exc:
            self.errors.append(f"backend.py 导入失败: {type(exc).__name__}: {exc}")
            return

        self.info.append(f"backend.py 可正常导入，注册 {len(backend.app.routes)} 条路由")

        # 没装独立的 chromedriver 不算错：backend.py 只有在 CHROMEDRIVER_PATH
        # 指向的文件真实存在时才自己建 Service，否则交给 selenium 处理，
        # 而 selenium 4 会用内置的 Selenium Manager 在首次启动时自动匹配
        # 并下载对应版本的 driver。
        driver_path = getattr(backend, 'CHROMEDRIVER_PATH', '') or ''
        if driver_path and os.path.exists(driver_path):
            self.info.append(f"chromedriver: {driver_path}")
        elif shutil.which('chromedriver'):
            self.info.append(f"chromedriver: {shutil.which('chromedriver')}")
        else:
            self.info.append(
                "没有单独的 chromedriver —— selenium 4 会在首次启动浏览器时"
                "用内置 Selenium Manager 自动匹配下载，保持能联网即可"
                "（也可以装好后用 CHROMEDRIVER_PATH 指定）"
            )

        chrome_binary = getattr(backend, 'CHROME_BINARY', '') or ''
        if chrome_binary and os.path.exists(chrome_binary):
            self.info.append(f"Chrome: {chrome_binary}")
        else:
            self.errors.append("没找到 Chrome —— 装一个，或用 CHROME_BINARY 指定路径")

        profile_dir = getattr(backend, 'CHROME_PROFILE_DIR', '') or ''
        if profile_dir and os.path.isdir(profile_dir):
            self.info.append(f"登录态目录: {profile_dir}")
            # SingletonLock 还在，说明可能有 Chrome 进程正占着这个 profile，
            # 此时再启动后端会起不来（user-data-dir 是独占的）。
            lock = os.path.join(profile_dir, 'SingletonLock')
            if os.path.exists(lock):
                self.warnings.append(
                    f"{lock} 存在 —— 可能还有 Chrome 占着这个登录态目录，"
                    "请先关掉那个窗口，否则后端初始化浏览器会失败"
                )
        else:
            self.warnings.append(
                f"登录态目录不存在（{profile_dir or '未配置'}）"
                "—— 首次访问面板时点「初始化浏览器」即可"
            )

        if getattr(backend, 'using_default_password', None) and backend.using_default_password():
            self.warnings.append(
                "面板仍是内置默认密码 123456 —— 首次登录后请立刻在「设置」页修改，"
                "否则后端会拒绝发消息 / 建定时任务"
            )

    def check_configuration(self):
        """检查配置文件"""
        if os.path.exists('.env'):
            self.info.append("配置文件 .env 存在")
        else:
            self.info.append("没有 .env（正常，缺省值就能跑；需要改配置时参考 .env.example）")

        if os.path.exists('.env.example'):
            self.info.append("配置示例 .env.example 存在")
        else:
            self.warnings.append("配置示例 .env.example 不存在")

    def run_all_checks(self):
        """运行所有检查"""
        _safe_print('=' * 62)
        _safe_print('  抖音火花助手（单用户版）环境自检')
        _safe_print('=' * 62)
        _safe_print('')

        steps = [
            ('1. 检查 Python 环境...', self.check_python_version),
            ('2. 检查依赖模块...', self.check_required_modules),
            ('3. 检查项目文件...', self.check_project_files),
            ('4. 检查目录结构...', self.check_directories),
            ('5. 检查面板依赖...', self.check_frontend),
            ('6. 检查配置...', self.check_configuration),
            ('7. 检查浏览器运行时...', self.check_runtime),
        ]
        for title, fn in steps:
            _safe_print(title)
            fn()

        _safe_print('')
        _safe_print('=' * 62)
        _safe_print('  检查结果')
        _safe_print('=' * 62)
        _safe_print('')

        if self.info:
            _safe_print(f"{OK} 信息 ({len(self.info)} 项):")
            for msg in self.info:
                _safe_print(f"  {OK} {msg}")
            _safe_print('')

        if self.warnings:
            _safe_print(f"{WARN} 警告 ({len(self.warnings)} 项):")
            for msg in self.warnings:
                _safe_print(f"  {WARN} {msg}")
            _safe_print('')

        if self.errors:
            _safe_print(f"{ERROR} 错误 ({len(self.errors)} 项):")
            for msg in self.errors:
                _safe_print(f"  {ERROR} {msg}")
            _safe_print('')

        _safe_print('=' * 62)
        if self.errors:
            _safe_print('环境检查失败，请修复上述错误后再启动')
            return False
        if self.warnings:
            _safe_print('环境检查通过，但有警告信息需要注意')
            return True
        _safe_print('环境检查完全通过，可以启动应用')
        return True


def main():
    checker = HealthChecker()
    success = checker.run_all_checks()
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
