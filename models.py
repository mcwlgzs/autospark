"""数据库模型定义 - 使用SQLAlchemy ORM

设计原则:
1. 兼容SQLite（开发）和MySQL（生产）
2. 保持向后兼容，逐步迁移
3. 清晰的关系定义
4. 自动时间戳管理
"""

from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, DateTime,
    Boolean, Float, ForeignKey, Date, Index, UniqueConstraint
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, Session
from sqlalchemy.pool import StaticPool
import os

Base = declarative_base()


class TimestampMixin:
    """时间戳混入类"""
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class Admin(Base, TimestampMixin):
    """管理员表"""
    __tablename__ = 'admins'

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    nickname = Column(String(100))
    email = Column(String(100))
    status = Column(Integer, nullable=False, default=1)  # 0:禁用 1:正常
    last_login_at = Column(DateTime)
    last_login_ip = Column(String(50))

    # 关系
    douyin_accounts = relationship('DouyinAccount', back_populates='admin', cascade='all, delete-orphan')
    scheduled_tasks = relationship('ScheduledTask', back_populates='admin', cascade='all, delete-orphan')
    operation_logs = relationship('OperationLog', back_populates='admin')
    notify_config = relationship('NotifyConfig', back_populates='admin', uselist=False, cascade='all, delete-orphan')


class DouyinAccount(Base, TimestampMixin):
    """抖音账号表"""
    __tablename__ = 'douyin_accounts'

    id = Column(Integer, primary_key=True, autoincrement=True)
    admin_id = Column(Integer, ForeignKey('admins.id', ondelete='CASCADE'), nullable=False, index=True)
    nickname = Column(String(100))
    uid = Column(String(64))
    sec_uid = Column(String(128))
    unique_id = Column(String(64))
    avatar = Column(String(500))
    cookie_data = Column(Text)  # JSON格式
    session_id = Column(String(256))
    sid_guard = Column(String(256))
    cookie_status = Column(Integer, nullable=False, default=0)  # 0:无效 1:有效 2:待刷新
    cookie_expire = Column(DateTime)
    is_default = Column(Integer, nullable=False, default=0, index=True)  # 0:否 1:是
    status = Column(Integer, nullable=False, default=1, index=True)  # 0:禁用 1:正常
    remark = Column(Text)
    last_login_at = Column(DateTime)

    # 关系
    admin = relationship('Admin', back_populates='douyin_accounts')
    friends = relationship('DouyinFriend', back_populates='account', cascade='all, delete-orphan')
    scheduled_tasks = relationship('ScheduledTask', back_populates='account', cascade='all, delete-orphan')
    task_executions = relationship('TaskExecution', back_populates='account', cascade='all, delete-orphan')
    send_history = relationship('SendHistory', back_populates='account', cascade='all, delete-orphan')


class DouyinFriend(Base, TimestampMixin):
    """好友表"""
    __tablename__ = 'douyin_friends'
    __table_args__ = (
        UniqueConstraint('douyin_account_id', 'uid', name='uq_account_uid'),
        Index('idx_friends_account', 'douyin_account_id'),
        Index('idx_friends_group', 'group_name'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    douyin_account_id = Column(Integer, ForeignKey('douyin_accounts.id', ondelete='CASCADE'), nullable=False)
    uid = Column(String(64), nullable=False)
    sec_uid = Column(String(128))
    unique_id = Column(String(64))
    nickname = Column(String(100))
    remark_name = Column(String(100))  # 自定义备注
    avatar = Column(String(500))
    group_name = Column(String(50))  # 分组
    last_contact_at = Column(DateTime)  # 最后联系时间

    # 关系
    account = relationship('DouyinAccount', back_populates='friends')


class ScheduledTask(Base, TimestampMixin):
    """定时任务表"""
    __tablename__ = 'scheduled_tasks'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100))
    douyin_account_id = Column(Integer, ForeignKey('douyin_accounts.id', ondelete='CASCADE'), nullable=False, index=True)
    admin_id = Column(Integer, ForeignKey('admins.id', ondelete='CASCADE'), nullable=False)
    target_uid = Column(String(64))
    target_nickname = Column(String(100))
    target_remark_name = Column(String(100))

    # 定时配置
    task_type = Column(String(20), nullable=False, default='daily')  # daily/interval
    send_time = Column(String(10), nullable=False, default='22:00')  # HH:MM
    jitter_minutes = Column(Integer, default=40)

    # 消息内容
    message_type = Column(String(20), nullable=False, default='fixed')  # fixed/pool/ai
    message_content = Column(Text)  # 固定消息
    message_pool = Column(Text)  # 消息池（JSON数组）
    ai_prompt = Column(Text)  # AI提示词

    # 特殊功能
    send_sign = Column(Integer, default=0)  # 是否发送灵签
    sign_type = Column(String(20), default='wenchang')
    image_only = Column(Integer, default=0)  # 仅发图片

    # 状态统计
    status = Column(Integer, nullable=False, default=1, index=True)  # 0:禁用 1:启用
    total_runs = Column(Integer, nullable=False, default=0)
    success_runs = Column(Integer, nullable=False, default=0)
    failed_runs = Column(Integer, nullable=False, default=0)
    last_run_at = Column(DateTime, index=True)
    last_run_status = Column(String(20))
    last_error_message = Column(Text)
    next_run_at = Column(DateTime, index=True)

    # 关系
    account = relationship('DouyinAccount', back_populates='scheduled_tasks')
    admin = relationship('Admin', back_populates='scheduled_tasks')
    executions = relationship('TaskExecution', back_populates='task', cascade='all, delete-orphan')
    send_history = relationship('SendHistory', back_populates='task')

    __table_args__ = (
        Index('idx_tasks_next_run', 'next_run_at', 'status'),
    )


class TaskExecution(Base):
    """任务执行历史表"""
    __tablename__ = 'task_executions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey('scheduled_tasks.id', ondelete='CASCADE'), nullable=False, index=True)
    douyin_account_id = Column(Integer, ForeignKey('douyin_accounts.id', ondelete='CASCADE'), nullable=False)
    target_uid = Column(String(64))
    target_nickname = Column(String(100))

    # 执行信息
    execution_type = Column(String(20), nullable=False)  # scheduled/manual/retry
    message_content = Column(Text)
    message_type = Column(String(20))

    # 结果
    status = Column(String(20), nullable=False, index=True)  # pending/running/success/failed
    result = Column(Integer)  # 0:失败 1:成功
    error_type = Column(String(50))
    error_message = Column(Text)
    retry_count = Column(Integer, default=0)

    # 时间
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    duration_seconds = Column(Float)
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)

    # 关系
    task = relationship('ScheduledTask', back_populates='executions')
    account = relationship('DouyinAccount', back_populates='task_executions')
    send_history = relationship('SendHistory', back_populates='execution')


class SendHistory(Base):
    """发送历史去重表"""
    __tablename__ = 'send_history'
    __table_args__ = (
        UniqueConstraint('douyin_account_id', 'target_uid', 'send_date', name='uq_account_uid_date'),
        Index('idx_history_date', 'send_date'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    douyin_account_id = Column(Integer, ForeignKey('douyin_accounts.id', ondelete='CASCADE'), nullable=False)
    target_uid = Column(String(64), nullable=False)
    send_date = Column(Date, nullable=False)
    task_id = Column(Integer, ForeignKey('scheduled_tasks.id', ondelete='SET NULL'))
    execution_id = Column(Integer, ForeignKey('task_executions.id', ondelete='SET NULL'))
    message_hash = Column(String(64))  # 消息哈希用于去重
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    # 关系
    account = relationship('DouyinAccount', back_populates='send_history')
    task = relationship('ScheduledTask', back_populates='send_history')
    execution = relationship('TaskExecution', back_populates='send_history')


class SystemConfig(Base, TimestampMixin):
    """系统配置表"""
    __tablename__ = 'system_configs'
    __table_args__ = (
        UniqueConstraint('config_group', 'config_key', name='uq_group_key'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    config_group = Column(String(50), nullable=False)
    config_key = Column(String(50), nullable=False)
    config_value = Column(Text)
    config_type = Column(String(20), nullable=False, default='string')  # string/number/boolean/json
    config_name = Column(String(100))
    config_desc = Column(String(255))
    is_sensitive = Column(Integer, default=0)  # 是否敏感（密码等）
    sort_order = Column(Integer, default=0)


class OperationLog(Base):
    """操作日志表"""
    __tablename__ = 'operation_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    admin_id = Column(Integer, ForeignKey('admins.id', ondelete='SET NULL'), index=True)
    log_type = Column(String(32), nullable=False, index=True)  # login/task/send/config/account
    action = Column(String(100))
    content = Column(Text)
    target_type = Column(String(50))
    target_id = Column(Integer)
    ip_address = Column(String(50))
    user_agent = Column(Text)
    status = Column(Integer, nullable=False, default=1)  # 0:失败 1:成功
    error_msg = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)

    # 关系
    admin = relationship('Admin', back_populates='operation_logs')


class NotifyConfig(Base, TimestampMixin):
    """消息通知配置表"""
    __tablename__ = 'notify_configs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    admin_id = Column(Integer, ForeignKey('admins.id', ondelete='CASCADE'), nullable=False, unique=True)

    # 推送配置
    push_enabled = Column(Integer, default=0)
    push_url = Column(String(500))
    push_on_success = Column(Integer, default=0)
    push_on_failure = Column(Integer, default=1)

    # 邮件配置
    email_enabled = Column(Integer, default=0)
    smtp_host = Column(String(255))
    smtp_port = Column(Integer, default=465)
    smtp_username = Column(String(255))
    smtp_password = Column(String(255))  # 需要加密
    from_email = Column(String(255))
    from_name = Column(String(100))
    to_email = Column(String(255))
    email_on_success = Column(Integer, default=0)
    email_on_failure = Column(Integer, default=1)

    # 关系
    admin = relationship('Admin', back_populates='notify_config')


# ==================== 数据库连接管理 ====================

class Database:
    """数据库管理类"""

    def __init__(self, database_url: str = None):
        if database_url is None:
            # 默认使用SQLite
            data_dir = os.getenv('SPARK_DATA_DIR', os.path.join(os.path.dirname(__file__), 'data'))
            os.makedirs(data_dir, exist_ok=True)
            db_path = os.path.join(data_dir, 'spark.db')
            database_url = f'sqlite:///{db_path}'

        self.database_url = database_url

        # SQLite需要特殊配置
        if database_url.startswith('sqlite'):
            self.engine = create_engine(
                database_url,
                connect_args={'check_same_thread': False},
                poolclass=StaticPool,
                echo=False
            )
        else:
            # MySQL/PostgreSQL
            self.engine = create_engine(
                database_url,
                pool_pre_ping=True,
                pool_recycle=3600,
                echo=False
            )

    def create_tables(self):
        """创建所有表"""
        Base.metadata.create_all(self.engine)

    def get_session(self) -> Session:
        """获取数据库会话"""
        from sqlalchemy.orm import sessionmaker
        SessionLocal = sessionmaker(bind=self.engine)
        return SessionLocal()

    def init_default_data(self):
        """初始化默认数据"""
        session = self.get_session()
        try:
            # 检查是否已有管理员
            admin = session.query(Admin).filter_by(username='admin').first()
            if not admin:
                # 创建默认管理员（密码: admin123）
                admin = Admin(
                    username='admin',
                    password_hash='$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5yvTtkO9PL5Sm',
                    nickname='系统管理员',
                    status=1
                )
                session.add(admin)

            # 检查系统配置
            config_count = session.query(SystemConfig).count()
            if config_count == 0:
                # 初始化系统配置
                default_configs = [
                    ('basic', 'site_name', '抖音续火花系统', 'string', '系统名称', '系统显示名称'),
                    ('basic', 'version', '1.2.0', 'string', '系统版本', '当前版本号'),
                    ('system', 'default_jitter_minutes', '40', 'number', '默认随机延迟', '任务执行随机延迟窗口（分钟）'),
                    ('system', 'default_send_time', '22:00', 'string', '默认发送时间', '新任务的默认发送时间'),
                    ('system', 'max_retry_count', '3', 'number', '最大重试次数', '任务失败后最多重试次数'),
                    ('ai', 'ai_enabled', 'false', 'boolean', '启用AI', '是否启用AI消息生成'),
                ]

                for i, (group, key, value, type_, name, desc) in enumerate(default_configs):
                    config = SystemConfig(
                        config_group=group,
                        config_key=key,
                        config_value=value,
                        config_type=type_,
                        config_name=name,
                        config_desc=desc,
                        sort_order=i
                    )
                    session.add(config)

            session.commit()
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()


# 全局数据库实例
_db_instance: Optional[Database] = None


def get_database(database_url: str = None) -> Database:
    """获取数据库实例（单例）"""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database(database_url)
    return _db_instance


def init_database(database_url: str = None):
    """初始化数据库"""
    db = get_database(database_url)
    db.create_tables()
    db.init_default_data()
    return db
