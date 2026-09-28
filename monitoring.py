"""日志和监控模块

使用 structlog 提供结构化日志，集成 Prometheus 监控
"""

import logging
import sys
from datetime import datetime
from typing import Any, Dict

import structlog
from structlog.processors import JSONRenderer, TimeStamper
from structlog.stdlib import add_log_level, filter_by_level


# ==================== 配置结构化日志 ====================

def setup_logging(log_level: str = "INFO", json_logs: bool = True):
    """配置日志系统"""

    # 基础配置
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper())
    )

    # 配置处理器
    processors = [
        filter_by_level,
        add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # 选择渲染器
    if json_logs:
        processors.append(JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    # 配置 structlog
    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


# ==================== 日志工具类 ====================

class Logger:
    """结构化日志记录器"""

    def __init__(self, name: str = None):
        self.logger = structlog.get_logger(name or __name__)

    def info(self, event: str, **kwargs):
        """记录信息"""
        self.logger.info(event, **kwargs)

    def warning(self, event: str, **kwargs):
        """记录警告"""
        self.logger.warning(event, **kwargs)

    def error(self, event: str, **kwargs):
        """记录错误"""
        self.logger.error(event, **kwargs)

    def debug(self, event: str, **kwargs):
        """记录调试信息"""
        self.logger.debug(event, **kwargs)

    def task_started(self, task_id: int, task_name: str, **kwargs):
        """任务开始"""
        self.info(
            "task_started",
            task_id=task_id,
            task_name=task_name,
            timestamp=datetime.now().isoformat(),
            **kwargs
        )

    def task_completed(self, task_id: int, success: bool, duration: float, **kwargs):
        """任务完成"""
        self.info(
            "task_completed",
            task_id=task_id,
            success=success,
            duration_seconds=duration,
            timestamp=datetime.now().isoformat(),
            **kwargs
        )

    def task_failed(self, task_id: int, error: str, **kwargs):
        """任务失败"""
        self.error(
            "task_failed",
            task_id=task_id,
            error=error,
            timestamp=datetime.now().isoformat(),
            **kwargs
        )

    def message_sent(self, account_id: int, friend_uid: str, success: bool, **kwargs):
        """消息发送"""
        event = "message_sent_success" if success else "message_sent_failed"
        self.info(
            event,
            account_id=account_id,
            friend_uid=friend_uid,
            timestamp=datetime.now().isoformat(),
            **kwargs
        )

    def login_attempt(self, username: str, success: bool, ip: str = None, **kwargs):
        """登录尝试"""
        event = "login_success" if success else "login_failed"
        self.info(
            event,
            username=username,
            ip_address=ip,
            timestamp=datetime.now().isoformat(),
            **kwargs
        )

    def api_request(self, method: str, path: str, status_code: int, duration: float, **kwargs):
        """API 请求"""
        self.info(
            "api_request",
            method=method,
            path=path,
            status_code=status_code,
            duration_ms=duration * 1000,
            timestamp=datetime.now().isoformat(),
            **kwargs
        )


# ==================== Prometheus 监控 ====================

try:
    from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    print("⚠️  prometheus-client 未安装，监控功能不可用")


if PROMETHEUS_AVAILABLE:
    # 定义指标

    # 计数器
    request_count = Counter(
        'spark_http_requests_total',
        'Total HTTP requests',
        ['method', 'endpoint', 'status']
    )

    task_executions = Counter(
        'spark_task_executions_total',
        'Total task executions',
        ['task_type', 'status']
    )

    messages_sent = Counter(
        'spark_messages_sent_total',
        'Total messages sent',
        ['account_id', 'status']
    )

    login_attempts = Counter(
        'spark_login_attempts_total',
        'Total login attempts',
        ['status']
    )

    # 直方图（延迟分布）
    request_duration = Histogram(
        'spark_http_request_duration_seconds',
        'HTTP request duration in seconds',
        ['method', 'endpoint']
    )

    task_duration = Histogram(
        'spark_task_duration_seconds',
        'Task execution duration in seconds',
        ['task_type']
    )

    message_send_duration = Histogram(
        'spark_message_send_duration_seconds',
        'Message send duration in seconds',
        ['account_id']
    )

    # 仪表（当前值）
    active_tasks = Gauge(
        'spark_active_tasks',
        'Number of currently active tasks'
    )

    active_accounts = Gauge(
        'spark_active_accounts',
        'Number of active douyin accounts'
    )

    db_connections = Gauge(
        'spark_db_connections',
        'Number of active database connections'
    )


class MetricsCollector:
    """指标收集器"""

    def __init__(self):
        self.enabled = PROMETHEUS_AVAILABLE

    def record_request(self, method: str, endpoint: str, status: int, duration: float):
        """记录HTTP请求"""
        if not self.enabled:
            return

        request_count.labels(
            method=method,
            endpoint=endpoint,
            status=status
        ).inc()

        request_duration.labels(
            method=method,
            endpoint=endpoint
        ).observe(duration)

    def record_task_execution(self, task_type: str, status: str, duration: float):
        """记录任务执行"""
        if not self.enabled:
            return

        task_executions.labels(
            task_type=task_type,
            status=status
        ).inc()

        task_duration.labels(
            task_type=task_type
        ).observe(duration)

    def record_message_sent(self, account_id: int, success: bool, duration: float):
        """记录消息发送"""
        if not self.enabled:
            return

        status = 'success' if success else 'failed'
        messages_sent.labels(
            account_id=str(account_id),
            status=status
        ).inc()

        message_send_duration.labels(
            account_id=str(account_id)
        ).observe(duration)

    def record_login_attempt(self, success: bool):
        """记录登录尝试"""
        if not self.enabled:
            return

        status = 'success' if success else 'failed'
        login_attempts.labels(status=status).inc()

    def set_active_tasks(self, count: int):
        """设置活跃任务数"""
        if not self.enabled:
            return
        active_tasks.set(count)

    def set_active_accounts(self, count: int):
        """设置活跃账号数"""
        if not self.enabled:
            return
        active_accounts.set(count)

    def set_db_connections(self, count: int):
        """设置数据库连接数"""
        if not self.enabled:
            return
        db_connections.set(count)

    def get_metrics(self):
        """获取指标数据（供 /metrics 端点使用）"""
        if not self.enabled:
            return b"# Prometheus not available\n"
        return generate_latest()

    def get_content_type(self):
        """获取内容类型"""
        if not self.enabled:
            return "text/plain"
        return CONTENT_TYPE_LATEST


# ==================== 全局实例 ====================

# 初始化日志
setup_logging(log_level="INFO", json_logs=False)

# 创建全局实例
logger = Logger("spark")
metrics = MetricsCollector()


# ==================== 装饰器 ====================

import functools
import time


def log_execution(func):
    """记录函数执行的装饰器"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        logger.debug(
            "function_started",
            function=func.__name__,
            args=str(args)[:100],
            kwargs=str(kwargs)[:100]
        )

        try:
            result = func(*args, **kwargs)
            duration = time.time() - start
            logger.debug(
                "function_completed",
                function=func.__name__,
                duration_seconds=duration
            )
            return result
        except Exception as e:
            duration = time.time() - start
            logger.error(
                "function_failed",
                function=func.__name__,
                error=str(e),
                duration_seconds=duration
            )
            raise

    return wrapper


def log_async_execution(func):
    """记录异步函数执行的装饰器"""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start = time.time()
        logger.debug(
            "async_function_started",
            function=func.__name__
        )

        try:
            result = await func(*args, **kwargs)
            duration = time.time() - start
            logger.debug(
                "async_function_completed",
                function=func.__name__,
                duration_seconds=duration
            )
            return result
        except Exception as e:
            duration = time.time() - start
            logger.error(
                "async_function_failed",
                function=func.__name__,
                error=str(e),
                duration_seconds=duration
            )
            raise

    return wrapper


# 使用示例
if __name__ == '__main__':
    print("=== 日志测试 ===")

    logger.info("system_started", version="2.0.0", mode="production")

    logger.task_started(
        task_id=1,
        task_name="发送消息",
        account_id=123,
        friend_uid="abc"
    )

    logger.message_sent(
        account_id=123,
        friend_uid="abc",
        success=True,
        message_length=50
    )

    logger.task_completed(
        task_id=1,
        success=True,
        duration=2.5
    )

    print("\n=== 指标测试 ===")

    metrics.record_request("GET", "/api/tasks", 200, 0.15)
    metrics.record_task_execution("daily", "success", 2.5)
    metrics.record_message_sent(123, True, 1.2)
    metrics.set_active_tasks(5)

    print("✅ 日志和监控系统初始化完成")

    print("\n=== 装饰器测试 ===")

    @log_execution
    def test_function(x, y):
        time.sleep(0.1)
        return x + y

    result = test_function(1, 2)
    print(f"结果: {result}")
