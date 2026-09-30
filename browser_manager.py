"""
浏览器进程生命周期管理模块
跟踪、监控和清理浏览器进程
"""

import asyncio
import psutil
from typing import Dict, Optional, List
from datetime import datetime, timedelta
from selenium import webdriver

from monitoring import Logger
from dal_async import AsyncDAL


class BrowserProcessInfo:
    """浏览器进程信息"""

    def __init__(self, pid: int, account_id: int):
        self.pid = pid
        self.account_id = account_id
        self.started_at = datetime.now()
        self.driver = None
        self.process: Optional[psutil.Process] = None

        try:
            self.process = psutil.Process(pid)
        except psutil.NoSuchProcess:
            pass

    def get_memory_mb(self) -> float:
        """获取内存占用（MB）"""
        if self.process and self.process.is_running():
            return self.process.memory_info().rss / 1024 / 1024
        return 0.0

    def get_cpu_percent(self) -> float:
        """获取CPU占用率"""
        if self.process and self.process.is_running():
            return self.process.cpu_percent(interval=0.1)
        return 0.0

    def is_alive(self) -> bool:
        """检查进程是否存活"""
        if self.process:
            return self.process.is_running()
        return False

    def get_uptime_seconds(self) -> float:
        """获取运行时长（秒）"""
        return (datetime.now() - self.started_at).total_seconds()

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            'pid': self.pid,
            'account_id': self.account_id,
            'started_at': self.started_at.isoformat(),
            'uptime_seconds': self.get_uptime_seconds(),
            'memory_mb': round(self.get_memory_mb(), 2),
            'cpu_percent': round(self.get_cpu_percent(), 2),
            'is_alive': self.is_alive()
        }


class BrowserProcessManager:
    """浏览器进程管理器"""

    def __init__(self):
        self.processes: Dict[int, BrowserProcessInfo] = {}  # account_id -> info
        self.locks: Dict[int, asyncio.Lock] = {}
        self.logger = Logger("BrowserProcessManager")

        # 配置
        self.max_browser_lifetime_hours = 12  # 最大生存时间
        self.zombie_check_interval = 300  # 僵尸检查间隔（秒）
        self.max_memory_mb = 2048  # 最大内存占用（MB）

    async def register_browser(
        self,
        driver: webdriver.Chrome,
        account_id: int
    ) -> BrowserProcessInfo:
        """
        注册浏览器进程

        Args:
            driver: WebDriver实例
            account_id: 账号ID

        Returns:
            进程信息对象
        """
        # 获取浏览器进程PID
        pid = self._get_browser_pid(driver)

        if not pid:
            self.logger.error("无法获取浏览器PID", account_id=account_id)
            raise RuntimeError("Failed to get browser PID")

        # 创建进程信息
        info = BrowserProcessInfo(pid, account_id)
        info.driver = driver

        # 注册
        self.processes[account_id] = info
        self.locks[account_id] = asyncio.Lock()

        # 更新数据库
        async with AsyncDAL() as dal:
            await dal.update_account(account_id, {
                'browser_pid': pid,
                'last_browser_start': datetime.now()
            })

        self.logger.info(
            "浏览器进程已注册",
            account_id=account_id,
            pid=pid,
            memory_mb=round(info.get_memory_mb(), 2)
        )

        return info

    async def unregister_browser(self, account_id: int, force_quit: bool = False):
        """
        注销浏览器进程

        Args:
            account_id: 账号ID
            force_quit: 是否强制退出
        """
        if account_id not in self.processes:
            return

        info = self.processes[account_id]

        try:
            # 退出WebDriver
            if info.driver:
                try:
                    info.driver.quit()
                except Exception as e:
                    self.logger.warning(
                        "WebDriver退出异常",
                        account_id=account_id,
                        error=str(e)
                    )

            # 强制杀死进程
            if force_quit and info.process and info.process.is_running():
                info.process.kill()
                self.logger.warning(
                    "强制终止浏览器进程",
                    account_id=account_id,
                    pid=info.pid
                )

        except Exception as e:
            self.logger.error(
                "注销浏览器失败",
                account_id=account_id,
                error=str(e)
            )
        finally:
            # 清理
            del self.processes[account_id]
            if account_id in self.locks:
                del self.locks[account_id]

            # 更新数据库
            async with AsyncDAL() as dal:
                await dal.update_account(account_id, {
                    'browser_pid': 0
                })

            self.logger.info("浏览器进程已注销", account_id=account_id)

    async def get_browser_lock(self, account_id: int) -> asyncio.Lock:
        """获取账号的浏览器锁"""
        if account_id not in self.locks:
            self.locks[account_id] = asyncio.Lock()
        return self.locks[account_id]

    def get_process_info(self, account_id: int) -> Optional[BrowserProcessInfo]:
        """获取进程信息"""
        return self.processes.get(account_id)

    def get_all_processes(self) -> List[BrowserProcessInfo]:
        """获取所有进程信息"""
        return list(self.processes.values())

    def get_process_count(self) -> int:
        """获取进程数量"""
        return len(self.processes)

    def get_total_memory_mb(self) -> float:
        """获取总内存占用（MB）"""
        return sum(info.get_memory_mb() for info in self.processes.values())

    async def cleanup_zombie_browsers(self) -> Dict:
        """
        清理僵尸浏览器进程

        Returns:
            清理统计
        """
        self.logger.info("开始清理僵尸浏览器进程")

        cleaned = 0
        errors = 0

        for account_id, info in list(self.processes.items()):
            try:
                # 检查进程是否存活
                if not info.is_alive():
                    self.logger.warning(
                        "发现僵尸进程",
                        account_id=account_id,
                        pid=info.pid
                    )
                    await self.unregister_browser(account_id)
                    cleaned += 1
                    continue

                # 检查运行时长
                uptime_hours = info.get_uptime_seconds() / 3600
                if uptime_hours > self.max_browser_lifetime_hours:
                    self.logger.warning(
                        "浏览器运行时间过长",
                        account_id=account_id,
                        uptime_hours=round(uptime_hours, 2)
                    )
                    await self.unregister_browser(account_id, force_quit=True)
                    cleaned += 1
                    continue

                # 检查内存占用
                memory_mb = info.get_memory_mb()
                if memory_mb > self.max_memory_mb:
                    self.logger.warning(
                        "浏览器内存占用过高",
                        account_id=account_id,
                        memory_mb=round(memory_mb, 2)
                    )
                    await self.unregister_browser(account_id, force_quit=True)
                    cleaned += 1
                    continue

            except Exception as e:
                self.logger.error(
                    "清理进程失败",
                    account_id=account_id,
                    error=str(e)
                )
                errors += 1

        result = {
            'cleaned': cleaned,
            'errors': errors,
            'remaining': len(self.processes),
            'timestamp': datetime.now().isoformat()
        }

        self.logger.info("僵尸进程清理完成", **result)

        return result

    async def cleanup_orphaned_pids(self):
        """清理数据库中的孤儿PID"""
        async with AsyncDAL() as dal:
            accounts = await dal.get_all_accounts()

            cleaned = 0
            for account in accounts:
                if hasattr(account, 'browser_pid') and account.browser_pid:
                    # 检查进程是否存在
                    try:
                        process = psutil.Process(account.browser_pid)
                        if not process.is_running():
                            await dal.update_account(account.id, {'browser_pid': 0})
                            cleaned += 1
                    except psutil.NoSuchProcess:
                        await dal.update_account(account.id, {'browser_pid': 0})
                        cleaned += 1

            if cleaned > 0:
                self.logger.info("清理孤儿PID完成", count=cleaned)

    async def force_cleanup_all(self):
        """强制清理所有浏览器进程"""
        self.logger.warning("强制清理所有浏览器进程")

        for account_id in list(self.processes.keys()):
            try:
                await self.unregister_browser(account_id, force_quit=True)
            except Exception as e:
                self.logger.error(
                    "强制清理失败",
                    account_id=account_id,
                    error=str(e)
                )

    def get_statistics(self) -> Dict:
        """获取统计信息"""
        processes = self.get_all_processes()

        if not processes:
            return {
                'total_count': 0,
                'total_memory_mb': 0,
                'avg_memory_mb': 0,
                'avg_uptime_hours': 0,
                'processes': []
            }

        total_memory = sum(p.get_memory_mb() for p in processes)
        total_uptime = sum(p.get_uptime_seconds() for p in processes)

        return {
            'total_count': len(processes),
            'total_memory_mb': round(total_memory, 2),
            'avg_memory_mb': round(total_memory / len(processes), 2),
            'avg_uptime_hours': round(total_uptime / len(processes) / 3600, 2),
            'processes': [p.to_dict() for p in processes]
        }

    def _get_browser_pid(self, driver: webdriver.Chrome) -> Optional[int]:
        """
        获取浏览器主进程PID

        Args:
            driver: WebDriver实例

        Returns:
            PID或None
        """
        try:
            # 方法1: 通过service属性获取
            if hasattr(driver, 'service') and hasattr(driver.service, 'process'):
                service_pid = driver.service.process.pid
                # ChromeDriver进程的父进程就是Chrome浏览器
                try:
                    parent = psutil.Process(service_pid).parent()
                    if parent:
                        return parent.pid
                except:
                    pass

            # 方法2: 通过session_id查找
            if hasattr(driver, 'session_id'):
                # 遍历所有Chrome进程
                for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                    try:
                        if 'chrome' in proc.info['name'].lower():
                            cmdline = proc.info.get('cmdline', [])
                            if cmdline and '--test-type' not in cmdline:
                                # 找到主进程
                                return proc.info['pid']
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue

            return None

        except Exception as e:
            self.logger.error("获取浏览器PID失败", error=str(e))
            return None


# 全局管理器实例
_browser_manager: Optional[BrowserProcessManager] = None


def get_browser_manager() -> BrowserProcessManager:
    """获取全局浏览器管理器实例"""
    global _browser_manager
    if _browser_manager is None:
        _browser_manager = BrowserProcessManager()
    return _browser_manager


# 后台清理任务
async def start_cleanup_task():
    """启动后台清理任务"""
    manager = get_browser_manager()
    logger = Logger("BrowserCleanupTask")

    while True:
        try:
            await asyncio.sleep(manager.zombie_check_interval)

            logger.info("执行定时清理任务")

            # 清理僵尸进程
            result = await manager.cleanup_zombie_browsers()

            # 清理孤儿PID
            await manager.cleanup_orphaned_pids()

            # 输出统计
            stats = manager.get_statistics()
            logger.info(
                "浏览器进程统计",
                count=stats['total_count'],
                memory_mb=stats['total_memory_mb']
            )

        except Exception as e:
            logger.error("清理任务异常", error=str(e))
            await asyncio.sleep(60)


# 使用示例
async def main():
    manager = get_browser_manager()

    # 启动清理任务
    cleanup_task = asyncio.create_task(start_cleanup_task())

    # 模拟注册浏览器
    # driver = create_stealth_driver()
    # await manager.register_browser(driver, account_id=1)

    # 获取统计
    stats = manager.get_statistics()
    print(f"统计信息: {stats}")

    # 清理
    # await manager.cleanup_zombie_browsers()

    # 取消任务
    cleanup_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
