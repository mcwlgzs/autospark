"""数据访问层 (Data Access Layer)

提供统一的数据库操作接口，封装业务逻辑需要的查询和更新操作。
"""

from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any, Tuple
from sqlalchemy import func, and_, or_, desc
from sqlalchemy.orm import Session

from models import (
    Admin, DouyinAccount, DouyinFriend, ScheduledTask,
    TaskExecution, SendHistory, SystemConfig, OperationLog,
    NotifyConfig, get_database
)


class DAL:
    """数据访问层"""

    def __init__(self, session: Session = None):
        self.session = session or get_database().get_session()
        self._auto_close = session is None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._auto_close:
            if exc_type is None:
                self.session.commit()
            else:
                self.session.rollback()
            self.session.close()

    # ==================== Admin 相关 ====================

    def get_admin_by_username(self, username: str) -> Optional[Admin]:
        """根据用户名获取管理员"""
        return self.session.query(Admin).filter_by(username=username, status=1).first()

    def get_admin_by_id(self, admin_id: int) -> Optional[Admin]:
        """根据ID获取管理员"""
        return self.session.query(Admin).filter_by(id=admin_id, status=1).first()

    def update_admin_login(self, admin_id: int, ip: str):
        """更新管理员登录信息"""
        admin = self.get_admin_by_id(admin_id)
        if admin:
            admin.last_login_at = datetime.now()
            admin.last_login_ip = ip
            self.session.commit()

    def change_admin_password(self, admin_id: int, new_hash: str):
        """修改管理员密码"""
        admin = self.get_admin_by_id(admin_id)
        if admin:
            admin.password_hash = new_hash
            self.session.commit()
            return True
        return False

    # ==================== DouyinAccount 相关 ====================

    def get_accounts_by_admin(self, admin_id: int) -> List[DouyinAccount]:
        """获取管理员的所有抖音账号"""
        return self.session.query(DouyinAccount)\
            .filter_by(admin_id=admin_id)\
            .order_by(desc(DouyinAccount.is_default), desc(DouyinAccount.created_at))\
            .all()

    def get_account_by_id(self, account_id: int) -> Optional[DouyinAccount]:
        """根据ID获取账号"""
        return self.session.query(DouyinAccount).filter_by(id=account_id).first()

    def get_default_account(self, admin_id: int) -> Optional[DouyinAccount]:
        """获取管理员的默认账号"""
        return self.session.query(DouyinAccount)\
            .filter_by(admin_id=admin_id, is_default=1, status=1)\
            .first()

    def create_account(self, admin_id: int, **kwargs) -> DouyinAccount:
        """创建抖音账号"""
        # 如果是第一个账号，自动设为默认
        existing_count = self.session.query(DouyinAccount)\
            .filter_by(admin_id=admin_id)\
            .count()

        if existing_count == 0:
            kwargs['is_default'] = 1

        account = DouyinAccount(admin_id=admin_id, **kwargs)
        self.session.add(account)
        self.session.commit()
        return account

    def update_account(self, account_id: int, **kwargs) -> bool:
        """更新账号信息"""
        account = self.get_account_by_id(account_id)
        if not account:
            return False

        for key, value in kwargs.items():
            if hasattr(account, key):
                setattr(account, key, value)

        self.session.commit()
        return True

    def set_default_account(self, account_id: int, admin_id: int) -> bool:
        """设置默认账号"""
        # 取消其他账号的默认状态
        self.session.query(DouyinAccount)\
            .filter_by(admin_id=admin_id)\
            .update({'is_default': 0})

        # 设置指定账号为默认
        account = self.get_account_by_id(account_id)
        if account and account.admin_id == admin_id:
            account.is_default = 1
            self.session.commit()
            return True
        return False

    def delete_account(self, account_id: int, admin_id: int) -> bool:
        """删除账号（软删除）"""
        account = self.get_account_by_id(account_id)
        if account and account.admin_id == admin_id:
            account.status = 0
            self.session.commit()
            return True
        return False

    # ==================== DouyinFriend 相关 ====================

    def get_friends(self, account_id: int, group: str = None) -> List[DouyinFriend]:
        """获取好友列表"""
        query = self.session.query(DouyinFriend).filter_by(douyin_account_id=account_id)
        if group:
            query = query.filter_by(group_name=group)
        return query.order_by(DouyinFriend.nickname).all()

    def get_friend_by_uid(self, account_id: int, uid: str) -> Optional[DouyinFriend]:
        """根据UID获取好友"""
        return self.session.query(DouyinFriend)\
            .filter_by(douyin_account_id=account_id, uid=uid)\
            .first()

    def upsert_friend(self, account_id: int, uid: str, **kwargs) -> DouyinFriend:
        """创建或更新好友信息"""
        friend = self.get_friend_by_uid(account_id, uid)
        if friend:
            # 更新
            for key, value in kwargs.items():
                if hasattr(friend, key):
                    setattr(friend, key, value)
        else:
            # 创建
            friend = DouyinFriend(douyin_account_id=account_id, uid=uid, **kwargs)
            self.session.add(friend)

        self.session.commit()
        return friend

    def batch_upsert_friends(self, account_id: int, friends_data: List[Dict]) -> int:
        """批量更新好友列表"""
        count = 0
        for data in friends_data:
            uid = data.pop('uid')
            self.upsert_friend(account_id, uid, **data)
            count += 1
        return count

    def update_friend_remark(self, account_id: int, uid: str, remark: str) -> bool:
        """更新好友备注"""
        friend = self.get_friend_by_uid(account_id, uid)
        if friend:
            friend.remark_name = remark
            self.session.commit()
            return True
        return False

    def get_friend_groups(self, account_id: int) -> List[str]:
        """获取好友分组列表"""
        result = self.session.query(DouyinFriend.group_name)\
            .filter(
                DouyinFriend.douyin_account_id == account_id,
                DouyinFriend.group_name.isnot(None)
            )\
            .distinct()\
            .all()
        return [r[0] for r in result]

    # ==================== ScheduledTask 相关 ====================

    def get_tasks(self, account_id: int = None, admin_id: int = None,
                  status: int = None) -> List[ScheduledTask]:
        """获取任务列表"""
        query = self.session.query(ScheduledTask)
        if account_id:
            query = query.filter_by(douyin_account_id=account_id)
        if admin_id:
            query = query.filter_by(admin_id=admin_id)
        if status is not None:
            query = query.filter_by(status=status)
        return query.order_by(ScheduledTask.created_at.desc()).all()

    def get_task_by_id(self, task_id: int) -> Optional[ScheduledTask]:
        """根据ID获取任务"""
        return self.session.query(ScheduledTask).filter_by(id=task_id).first()

    def create_task(self, **kwargs) -> ScheduledTask:
        """创建任务"""
        task = ScheduledTask(**kwargs)
        self.session.add(task)
        self.session.commit()
        return task

    def update_task(self, task_id: int, **kwargs) -> bool:
        """更新任务"""
        task = self.get_task_by_id(task_id)
        if not task:
            return False

        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)

        self.session.commit()
        return True

    def delete_task(self, task_id: int) -> bool:
        """删除任务"""
        task = self.get_task_by_id(task_id)
        if task:
            self.session.delete(task)
            self.session.commit()
            return True
        return False

    def get_due_tasks(self, now: datetime = None) -> List[ScheduledTask]:
        """获取到期待执行的任务"""
        if now is None:
            now = datetime.now()

        return self.session.query(ScheduledTask)\
            .filter(
                ScheduledTask.status == 1,
                ScheduledTask.next_run_at <= now
            )\
            .all()

    def update_task_stats(self, task_id: int, success: bool,
                          error_msg: str = None, next_run: datetime = None):
        """更新任务统计信息"""
        task = self.get_task_by_id(task_id)
        if not task:
            return

        task.total_runs += 1
        if success:
            task.success_runs += 1
            task.last_run_status = 'success'
        else:
            task.failed_runs += 1
            task.last_run_status = 'failed'
            task.last_error_message = error_msg

        task.last_run_at = datetime.now()
        if next_run:
            task.next_run_at = next_run

        self.session.commit()

    # ==================== TaskExecution 相关 ====================

    def create_execution(self, **kwargs) -> TaskExecution:
        """创建执行记录"""
        execution = TaskExecution(**kwargs)
        self.session.add(execution)
        self.session.commit()
        return execution

    def update_execution(self, execution_id: int, **kwargs) -> bool:
        """更新执行记录"""
        execution = self.session.query(TaskExecution).filter_by(id=execution_id).first()
        if not execution:
            return False

        for key, value in kwargs.items():
            if hasattr(execution, key):
                setattr(execution, key, value)

        # 自动计算执行时长
        if 'finished_at' in kwargs and execution.started_at:
            execution.duration_seconds = (
                execution.finished_at - execution.started_at
            ).total_seconds()

        self.session.commit()
        return True

    def get_task_executions(self, task_id: int, limit: int = 100) -> List[TaskExecution]:
        """获取任务执行历史"""
        return self.session.query(TaskExecution)\
            .filter_by(task_id=task_id)\
            .order_by(TaskExecution.created_at.desc())\
            .limit(limit)\
            .all()

    def get_executions_stats(self, account_id: int = None,
                            days: int = 30) -> Dict[str, Any]:
        """获取执行统计"""
        start_date = datetime.now() - timedelta(days=days)
        query = self.session.query(TaskExecution)\
            .filter(TaskExecution.created_at >= start_date)

        if account_id:
            query = query.filter_by(douyin_account_id=account_id)

        total = query.count()
        success = query.filter_by(result=1).count()
        failed = query.filter_by(result=0).count()

        return {
            'total': total,
            'success': success,
            'failed': failed,
            'success_rate': round(success / total * 100, 2) if total > 0 else 0
        }

    # ==================== SendHistory 相关 ====================

    def check_sent_today(self, account_id: int, target_uid: str,
                         send_date: date = None) -> bool:
        """检查今天是否已发送"""
        if send_date is None:
            send_date = date.today()

        exists = self.session.query(SendHistory)\
            .filter_by(
                douyin_account_id=account_id,
                target_uid=target_uid,
                send_date=send_date
            )\
            .first()

        return exists is not None

    def record_send(self, account_id: int, target_uid: str,
                    task_id: int = None, execution_id: int = None,
                    message_hash: str = None, send_date: date = None):
        """记录发送历史"""
        if send_date is None:
            send_date = date.today()

        history = SendHistory(
            douyin_account_id=account_id,
            target_uid=target_uid,
            send_date=send_date,
            task_id=task_id,
            execution_id=execution_id,
            message_hash=message_hash
        )
        self.session.add(history)
        self.session.commit()

    def cleanup_old_history(self, days: int = 90):
        """清理旧的发送历史"""
        cutoff_date = date.today() - timedelta(days=days)
        deleted = self.session.query(SendHistory)\
            .filter(SendHistory.send_date < cutoff_date)\
            .delete()
        self.session.commit()
        return deleted

    # ==================== SystemConfig 相关 ====================

    def get_config(self, group: str, key: str, default: Any = None) -> Any:
        """获取配置值"""
        config = self.session.query(SystemConfig)\
            .filter_by(config_group=group, config_key=key)\
            .first()

        if not config:
            return default

        value = config.config_value

        # 类型转换
        if config.config_type == 'number':
            try:
                return int(value) if '.' not in value else float(value)
            except (ValueError, TypeError):
                return default
        elif config.config_type == 'boolean':
            return value.lower() in ('true', '1', 'yes')
        elif config.config_type == 'json':
            import json
            try:
                return json.loads(value)
            except:
                return default

        return value

    def set_config(self, group: str, key: str, value: Any):
        """设置配置值"""
        config = self.session.query(SystemConfig)\
            .filter_by(config_group=group, config_key=key)\
            .first()

        if config:
            config.config_value = str(value)
        else:
            config = SystemConfig(
                config_group=group,
                config_key=key,
                config_value=str(value)
            )
            self.session.add(config)

        self.session.commit()

    def get_configs_by_group(self, group: str) -> Dict[str, Any]:
        """获取分组下的所有配置"""
        configs = self.session.query(SystemConfig)\
            .filter_by(config_group=group)\
            .order_by(SystemConfig.sort_order)\
            .all()

        result = {}
        for config in configs:
            result[config.config_key] = self.get_config(group, config.config_key)

        return result

    # ==================== OperationLog 相关 ====================

    def log_operation(self, admin_id: int = None, log_type: str = None,
                     action: str = None, content: str = None,
                     ip: str = None, user_agent: str = None,
                     status: int = 1, error_msg: str = None, **kwargs):
        """记录操作日志"""
        log = OperationLog(
            admin_id=admin_id,
            log_type=log_type,
            action=action,
            content=content,
            ip_address=ip,
            user_agent=user_agent,
            status=status,
            error_msg=error_msg,
            **kwargs
        )
        self.session.add(log)
        self.session.commit()
        return log

    def get_logs(self, admin_id: int = None, log_type: str = None,
                 limit: int = 100, offset: int = 0) -> Tuple[List[OperationLog], int]:
        """获取操作日志"""
        query = self.session.query(OperationLog)

        if admin_id:
            query = query.filter_by(admin_id=admin_id)
        if log_type:
            query = query.filter_by(log_type=log_type)

        total = query.count()
        logs = query.order_by(OperationLog.created_at.desc())\
            .limit(limit)\
            .offset(offset)\
            .all()

        return logs, total

    # ==================== NotifyConfig 相关 ====================

    def get_notify_config(self, admin_id: int) -> Optional[NotifyConfig]:
        """获取通知配置"""
        return self.session.query(NotifyConfig)\
            .filter_by(admin_id=admin_id)\
            .first()

    def upsert_notify_config(self, admin_id: int, **kwargs) -> NotifyConfig:
        """创建或更新通知配置"""
        config = self.get_notify_config(admin_id)
        if config:
            for key, value in kwargs.items():
                if hasattr(config, key):
                    setattr(config, key, value)
        else:
            config = NotifyConfig(admin_id=admin_id, **kwargs)
            self.session.add(config)

        self.session.commit()
        return config
