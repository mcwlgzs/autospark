# AutoSpark v2.2 集成指南

本文档指导如何将 v2.2 新增的防封模块集成到现有代码中。

---

## 📋 前置准备

### 1. 安装依赖

```bash
pip install psutil
```

### 2. 运行数据库迁移

```bash
python migrations/add_anti_detection_fields.py
```

**注意：** 迁移前建议备份数据库！

---

## 🔌 集成步骤

### 步骤 1：更新 models_async.py

在 `DouyinAccount` 模型中添加新字段：

```python
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean
from sqlalchemy.sql import func

class DouyinAccount(Base):
    __tablename__ = 'douyin_accounts'
    
    # ... 现有字段 ...
    
    # v2.2 新增字段
    device_fp = Column(String(64), comment='设备指纹')
    login_method = Column(String(20), default='qrcode', comment='登录方式')
    cookie_created_at = Column(DateTime, comment='Cookie创建时间')
    cookie_last_check = Column(DateTime, comment='Cookie最后检查时间')
    cookie_check_interval = Column(Integer, default=3600, comment='检查间隔（秒）')
    daily_operation_count = Column(Integer, default=0, comment='每日操作计数')
    last_operation_at = Column(DateTime, comment='最后操作时间')
    risk_level = Column(Integer, default=0, comment='风险等级 0-5')
    browser_pid = Column(Integer, default=0, comment='浏览器进程PID')
    last_browser_start = Column(DateTime, comment='浏览器启动时间')
```

### 步骤 2：创建新模型类

在 `models_async.py` 中添加：

```python
class DouyinQRCode(Base):
    """二维码登录记录"""
    __tablename__ = 'douyin_qrcodes'
    
    id = Column(Integer, primary_key=True)
    token = Column(String(128), unique=True, nullable=False, comment='本地token')
    fp = Column(String(64), comment='设备指纹')
    admin_id = Column(Integer, comment='管理员ID')
    qrcode_data = Column(Text, comment='二维码图片Base64')
    qrcode_url = Column(String(512), comment='二维码URL')
    status = Column(Integer, default=0, comment='状态: 0待扫码 1已扫码 2已确认 3已过期')
    expires_at = Column(DateTime, comment='过期时间')
    created_at = Column(DateTime, server_default=func.now())


class Announcement(Base):
    """系统公告"""
    __tablename__ = 'announcements'
    
    id = Column(Integer, primary_key=True)
    type = Column(String(20), default='system', comment='类型')
    title = Column(String(200), nullable=False, comment='标题')
    content = Column(Text, comment='内容')
    level = Column(String(20), default='info', comment='级别')
    status = Column(Integer, default=1, comment='状态: 0隐藏 1显示')
    show_from = Column(DateTime, comment='显示开始时间')
    show_until = Column(DateTime, comment='显示结束时间')
    created_at = Column(DateTime, server_default=func.now())
```

### 步骤 3：在 protocol_sender.py 中集成

#### 3.1 导入新模块

```python
from device_fingerprint import FingerprintManager
from cookie_monitor import CookieMonitor
from browser_manager import get_browser_manager
from selenium_stealth import HumanBehaviorSimulator
```

#### 3.2 在发送消息前检查 Cookie 健康

```python
async def send_message(self, account_id: int, message: str, **kwargs):
    """发送消息（增强版）"""
    
    # 1. 获取账号
    async with get_async_session() as session:
        account = await session.get(DouyinAccount, account_id)
        if not account:
            raise ValueError(f"账号 {account_id} 不存在")
    
    # 2. 检查 Cookie 健康（v2.2 新增）
    monitor = CookieMonitor()
    report = await monitor.check_account_health(account)
    
    if report.risk_level.value >= 4:  # CRITICAL 或 BANNED
        logger.error(f"账号 {account_id} 风险过高: {report.message}")
        raise Exception(f"账号风险过高: {report.message}")
    
    if report.risk_level.value == 3:  # HIGH
        logger.warning(f"账号 {account_id} 存在风险: {report.message}")
        # 可以选择继续或跳过
    
    # 3. 确保设备指纹存在（v2.2 新增）
    if not account.device_fp:
        fp_manager = FingerprintManager()
        account.device_fp = fp_manager.get_or_create_fp(
            account_id=account.id,
            seed=f"account_{account.id}"
        )
        async with get_async_session() as session:
            session.add(account)
            await session.commit()
    
    # 4. 创建浏览器
    driver = None
    browser_manager = get_browser_manager()
    
    try:
        # 使用现有的浏览器创建逻辑
        driver = self._create_driver(account)
        
        # 注册浏览器进程（v2.2 新增）
        process_info = await browser_manager.register_browser(driver, account.id)
        logger.info(f"浏览器进程已注册: PID={process_info.pid}")
        
        # 5. 模拟人类行为（v2.2 增强）
        # 随机页面交互
        HumanBehaviorSimulator.random_page_interactions(driver, count=2)
        
        # 随机滚动
        await HumanBehaviorSimulator.random_scroll_behavior(driver)
        
        # 6. 执行实际操作
        result = await self._do_send_message(driver, message, **kwargs)
        
        # 7. 更新操作计数（v2.2 新增）
        await monitor.update_operation_count(account.id)
        
        return result
        
    except Exception as e:
        logger.error(f"发送消息失败: {e}")
        raise
        
    finally:
        if driver:
            # 注销浏览器（v2.2 新增）
            await browser_manager.unregister_browser(account.id)
            driver.quit()
```

#### 3.3 使用增强的行为模拟

```python
async def _do_send_message(self, driver, message: str, **kwargs):
    """实际发送消息的逻辑"""
    
    # 等待消息输入框
    input_box = SmartWait.wait_for_element(
        driver, By.CSS_SELECTOR, 
        'textarea[placeholder*="消息"]',
        timeout=10
    )
    
    if not input_box:
        raise Exception("未找到消息输入框")
    
    # 使用贝塞尔曲线移动到输入框（v2.2 新增）
    HumanBehaviorSimulator.bezier_curve_mouse_movement(driver, input_box)
    
    # 模拟人类延迟
    HumanBehaviorSimulator.simulate_human_delay(0.5, 1.5)
    
    # 输入消息（保留原有逻辑）
    ElementInteractor.safe_send_keys(input_box, message, human_like=True)
    
    # 模拟阅读时间
    reading_time = HumanBehaviorSimulator.simulate_reading_time(len(message))
    await asyncio.sleep(reading_time)
    
    # 查找发送按钮
    send_btn = SmartWait.wait_for_clickable(
        driver, By.CSS_SELECTOR,
        'button[type="submit"]',
        timeout=5
    )
    
    if send_btn:
        # 使用曲线移动到发送按钮
        HumanBehaviorSimulator.bezier_curve_mouse_movement(driver, send_btn)
        HumanBehaviorSimulator.simulate_human_delay(0.3, 0.8)
        ElementInteractor.safe_click(driver, send_btn)
    
    # 等待发送完成
    await asyncio.sleep(2)
    
    return True
```

### 步骤 4：启动后台监控任务

在 `main.py` 或主入口文件中：

```python
import asyncio
from cookie_monitor import CookieMonitor
from browser_manager import start_cleanup_task

async def start_background_tasks():
    """启动后台监控任务"""
    
    # 1. 启动浏览器进程清理任务
    asyncio.create_task(start_cleanup_task())
    logger.info("✅ 浏览器进程清理任务已启动")
    
    # 2. 启动 Cookie 监控任务
    async def cookie_monitor_loop():
        monitor = CookieMonitor()
        while True:
            try:
                # 每小时检查一次
                await asyncio.sleep(3600)
                
                # 自动刷新即将过期的 Cookie
                result = await monitor.auto_refresh_expiring_cookies()
                logger.info(f"Cookie 监控: 刷新 {result['refreshed']} 个, "
                           f"失败 {result['failed']} 个")
                
                # 检查高风险账号
                at_risk = await monitor.get_at_risk_accounts()
                if at_risk:
                    logger.warning(f"⚠️ 发现 {len(at_risk)} 个高风险账号")
                    for account, report in at_risk:
                        logger.warning(f"  - {account.nickname}: {report.message}")
                
            except Exception as e:
                logger.error(f"Cookie 监控任务出错: {e}")
    
    asyncio.create_task(cookie_monitor_loop())
    logger.info("✅ Cookie 监控任务已启动")


# 在应用启动时调用
if __name__ == '__main__':
    # ... 其他初始化代码 ...
    
    # 启动后台任务
    asyncio.run(start_background_tasks())
    
    # ... 启动 FastAPI 等 ...
```

### 步骤 5：更新 dal_async.py（数据访问层）

添加新字段的查询和更新方法：

```python
from datetime import datetime, timedelta

class DouyinAccountDAL:
    """抖音账号数据访问层"""
    
    def __init__(self, session):
        self.session = session
    
    async def update_device_fingerprint(self, account_id: int, fp: str):
        """更新设备指纹"""
        account = await self.session.get(DouyinAccount, account_id)
        if account:
            account.device_fp = fp
            await self.session.commit()
    
    async def update_cookie_status(
        self, 
        account_id: int, 
        status: str,
        created_at: datetime = None
    ):
        """更新 Cookie 状态"""
        account = await self.session.get(DouyinAccount, account_id)
        if account:
            account.cookie_status = status
            if created_at:
                account.cookie_created_at = created_at
            account.cookie_last_check = datetime.now()
            await self.session.commit()
    
    async def increment_operation_count(self, account_id: int):
        """增加操作计数"""
        account = await self.session.get(DouyinAccount, account_id)
        if account:
            # 检查是否是新的一天
            if (not account.last_operation_at or 
                account.last_operation_at.date() < datetime.now().date()):
                account.daily_operation_count = 1
            else:
                account.daily_operation_count += 1
            
            account.last_operation_at = datetime.now()
            await self.session.commit()
    
    async def update_risk_level(self, account_id: int, level: int):
        """更新风险等级"""
        account = await self.session.get(DouyinAccount, account_id)
        if account:
            account.risk_level = level
            await self.session.commit()
    
    async def get_accounts_by_risk_level(self, min_level: int = 3):
        """获取高风险账号"""
        from sqlalchemy import select
        
        stmt = select(DouyinAccount).where(
            DouyinAccount.risk_level >= min_level,
            DouyinAccount.status == 1
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()
```

---

## 📊 监控和日志

### 添加 Prometheus 指标

在 `metrics.py` 或主文件中：

```python
from prometheus_client import Gauge, Counter

# Cookie 过期监控
cookie_expiry_gauge = Gauge(
    'douyin_cookie_days_until_expiry',
    'Days until cookie expires',
    ['account_id', 'nickname']
)

# 风险等级监控
risk_level_gauge = Gauge(
    'douyin_account_risk_level',
    'Account risk level (0-5)',
    ['account_id', 'nickname']
)

# 浏览器进程监控
browser_process_gauge = Gauge(
    'douyin_active_browser_processes',
    'Number of active browser processes'
)

browser_memory_gauge = Gauge(
    'douyin_browser_memory_mb',
    'Browser memory usage in MB',
    ['account_id']
)

# 操作计数
daily_operations_counter = Counter(
    'douyin_daily_operations_total',
    'Total daily operations',
    ['account_id', 'operation_type']
)


# 更新指标的示例
async def update_metrics():
    """更新 Prometheus 指标"""
    monitor = CookieMonitor()
    browser_manager = get_browser_manager()
    
    # 更新所有账号的指标
    async with get_async_session() as session:
        accounts = await session.execute(select(DouyinAccount))
        accounts = accounts.scalars().all()
        
        for account in accounts:
            # Cookie 过期时间
            if account.cookie_created_at:
                days = (datetime.now() - account.cookie_created_at).days
                cookie_expiry_gauge.labels(
                    account_id=account.id,
                    nickname=account.nickname
                ).set(max(0, 30 - days))  # 假设 30 天过期
            
            # 风险等级
            risk_level_gauge.labels(
                account_id=account.id,
                nickname=account.nickname
            ).set(account.risk_level)
    
    # 浏览器进程统计
    stats = browser_manager.get_statistics()
    browser_process_gauge.set(stats['total_count'])
```

---

## 🧪 测试

### 测试 Cookie 监控

```python
import asyncio
from cookie_monitor import CookieMonitor

async def test_cookie_monitor():
    monitor = CookieMonitor()
    
    # 检查所有账号
    reports = await monitor.check_all_accounts()
    
    print(f"\n📊 检查了 {len(reports)} 个账号:")
    for report in reports:
        print(f"\n账号 {report.account_id}:")
        print(f"  状态: {report.message}")
        print(f"  风险等级: {report.risk_level.name} ({report.risk_level.value})")
        print(f"  建议操作: {report.action}")
        print(f"  Cookie 过期: {report.days_until_expiry} 天后")
        print(f"  今日操作: {report.daily_operations}/{report.max_daily_operations}")

if __name__ == '__main__':
    asyncio.run(test_cookie_monitor())
```

### 测试设备指纹

```python
from device_fingerprint import DeviceFingerprint, FingerprintManager

def test_fingerprint():
    # 测试稳定指纹生成
    fp1 = DeviceFingerprint.generate_stable_fp("test_seed")
    fp2 = DeviceFingerprint.generate_stable_fp("test_seed")
    assert fp1 == fp2, "相同 seed 应生成相同指纹"
    print(f"✅ 稳定指纹: {fp1}")
    
    # 测试抖音风格指纹
    dy_fp = DeviceFingerprint.generate_douyin_fp()
    assert dy_fp.startswith("verify_"), "应该以 verify_ 开头"
    print(f"✅ 抖音指纹: {dy_fp}")
    
    # 测试管理器
    manager = FingerprintManager()
    fp = manager.get_or_create_fp(account_id=1, seed="account_1")
    print(f"✅ 管理器指纹: {fp}")

if __name__ == '__main__':
    test_fingerprint()
```

### 测试浏览器管理

```python
import asyncio
from browser_manager import get_browser_manager
from selenium_stealth import create_stealth_driver

async def test_browser_manager():
    manager = get_browser_manager()
    driver = None
    
    try:
        # 创建浏览器
        driver = create_stealth_driver(headless=True)
        
        # 注册
        info = await manager.register_browser(driver, account_id=999)
        print(f"✅ 注册成功: PID={info.pid}, 内存={info.get_memory_mb()}MB")
        
        # 统计
        stats = manager.get_statistics()
        print(f"✅ 活跃进程: {stats['total_count']}")
        
        # 等待一段时间
        await asyncio.sleep(5)
        
        # 注销
        await manager.unregister_browser(account_id=999)
        print("✅ 注销成功")
        
    finally:
        if driver:
            driver.quit()

if __name__ == '__main__':
    asyncio.run(test_browser_manager())
```

---

## ⚙️ 配置建议

### 环境变量

可以在 `.env` 文件中添加：

```bash
# Cookie 监控配置
COOKIE_EXPIRE_WARNING_DAYS=3
COOKIE_MAX_DAILY_OPERATIONS=100
COOKIE_OPERATION_INTERVAL_SECONDS=300

# 浏览器管理配置
BROWSER_MAX_LIFETIME_HOURS=12
BROWSER_MAX_MEMORY_MB=2048
BROWSER_CLEANUP_INTERVAL_SECONDS=300

# 行为模拟配置
HUMAN_BEHAVIOR_ENABLE_BEZIER=true
HUMAN_BEHAVIOR_MIN_DELAY=0.5
HUMAN_BEHAVIOR_MAX_DELAY=2.0
```

### 读取配置

```python
import os

class Config:
    # Cookie 监控
    EXPIRE_WARNING_DAYS = int(os.getenv('COOKIE_EXPIRE_WARNING_DAYS', 3))
    MAX_DAILY_OPERATIONS = int(os.getenv('COOKIE_MAX_DAILY_OPERATIONS', 100))
    OPERATION_INTERVAL_SECONDS = int(os.getenv('COOKIE_OPERATION_INTERVAL_SECONDS', 300))
    
    # 浏览器管理
    MAX_BROWSER_LIFETIME_HOURS = int(os.getenv('BROWSER_MAX_LIFETIME_HOURS', 12))
    MAX_MEMORY_MB = int(os.getenv('BROWSER_MAX_MEMORY_MB', 2048))
    CLEANUP_INTERVAL = int(os.getenv('BROWSER_CLEANUP_INTERVAL_SECONDS', 300))
    
    # 行为模拟
    ENABLE_BEZIER = os.getenv('HUMAN_BEHAVIOR_ENABLE_BEZIER', 'true').lower() == 'true'
    MIN_DELAY = float(os.getenv('HUMAN_BEHAVIOR_MIN_DELAY', 0.5))
    MAX_DELAY = float(os.getenv('HUMAN_BEHAVIOR_MAX_DELAY', 2.0))
```

---

## 🎯 集成检查清单

- [ ] 安装 `psutil` 依赖
- [ ] 运行数据库迁移脚本
- [ ] 更新 `models_async.py` 添加新字段
- [ ] 创建 `DouyinQRCode` 和 `Announcement` 模型
- [ ] 在 `protocol_sender.py` 中集成 Cookie 监控
- [ ] 在消息发送前检查设备指纹
- [ ] 在浏览器创建时注册进程
- [ ] 使用增强的行为模拟
- [ ] 启动后台监控任务
- [ ] 添加 Prometheus 指标
- [ ] 运行测试脚本验证功能
- [ ] 配置环境变量
- [ ] 查看日志确认正常运行

---

## 📝 注意事项

1. **渐进式集成**：不需要一次性集成所有功能，可以先集成 Cookie 监控，再逐步添加其他功能

2. **性能影响**：
   - Cookie 监控建议每小时运行一次
   - 浏览器清理每 5 分钟运行
   - 行为模拟会增加单次操作时间（提高真实性）

3. **向后兼容**：所有新字段都有默认值，不影响现有功能

4. **错误处理**：建议在关键位置添加 try-except，避免新功能导致主流程失败

5. **日志记录**：建议为新功能添加详细日志，便于调试和监控

---

## 🆘 常见问题

### Q: 迁移脚本报错怎么办？

A: 检查数据库连接配置，确保 `database.py` 中的 `DATABASE_URL` 正确。如果字段已存在，脚本会自动跳过。

### Q: Cookie 监控一直报高风险？

A: 检查 `cookie_monitor.py` 中的阈值配置，可能需要根据实际情况调整 `MAX_DAILY_OPERATIONS` 等参数。

### Q: 浏览器进程无法注册？

A: 确保安装了 `psutil`，并且 WebDriver 已成功创建。可以先运行测试脚本验证。

### Q: 行为模拟太慢？

A: 可以通过环境变量调整延迟时间，或在不需要高度真实性的场景禁用部分功能。

---

**集成完成后，AutoSpark 将拥有业界领先的防封能力！** 🎉
