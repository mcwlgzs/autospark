"""异步数据访问层 (Async Data Access Layer)

提供异步数据库操作接口，基于 SQLAlchemy 2.0 异步模式。
"""

from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any, Tuple
from sqlalchemy import select, func, and_, or_, desc, delete, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload

from models_async import (
    Admin, DouyinAccount, DouyinFriend, ScheduledTask,
    TaskExecution, SendHistory, SystemConfig, OperationLog,
    NotifyConfig, get_async_database
)


class AsyncDAL:
    """异步数据访问层"""

    def __init__(self, session: AsyncSession = None):
        self.session = session
        self._own_session = session is None

    async def __aenter__(self):
        if self._own_session:
            self.session = get_async_database().get_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._own_session and self.session:
            if exc_type is None:
                await self.session.commit()
            else:
                await self.session.rollback()
            await self.session.close()

    # ==================== Admin 相关 ====================

    async def get_admin_by_username(self, username: str) -> Optional[Admin]:
        """根据用户名获取管理员"""
        result = await self.session.execute(
            select(Admin).filter_by(username=username, status=1)
        )
        return result.scalar_one_or_none()

    async def get_admin_by_id(self, admin_id: int) -> Optional[Admin]:
        """根据ID获取管理员"""
        result = await self.session.execute(
            select(Admin).filter_by(id=admin_id, status=1)
        )
        return result.scalar_one_or_none()

    async def update_admin_login(self, admin_id: int, ip: str):
        """更新管理员登录信息"""
        await self.session.execute(
            update(Admin)
            .where(Admin.id == admin_id)
            .values(last_login_at=datetime.now(), last_login_ip=ip)
        )
        await self.session.commit()

    async def change_admin_password(self, admin_id: int, new_hash: str):
        """修改管理员密码"""
        result = await self.session.execute(
            update(Admin)
            .where(Admin.id == admin_id)
            .values(password_hash=new_hash)
        )
        await self.session.commit()
        return result.rowcount > 0

    # ==================== DouyinAccount 相关 ====================

    async def get_accounts_by_admin(self, admin_id: int) -> List[DouyinAccount]:
        """获取管理员的所有抖音账号"""
        result = await self.session.execute(
            select(DouyinAccount)
            .filter_by(admin_id=admin_id)
            .order_by(desc(DouyinAccount.is_default), desc(DouyinAccount.created_at))
        )
        return result.scalars().all()

    async def get_account_by_id(self, account_id: int) -> Optional[DouyinAccount]:
        """根据ID获取账号"""
        result = await self.session.execute(
            select(DouyinAccount).filter_by(id=account_id)
        )
        return result.scalar_one_or_none()

    async def get_default_account(self, admin_id: int) -> Optional[DouyinAccount]:
        """获取管理员的默认账号"""
        result = await self.session.execute(
            select(DouyinAccount)
            .filter_by(admin_id=admin_id, is_default=1, status=1)
        )
        return result.scalar_one_or_none()

    async def create_account(self, admin_id: int, **kwargs) -> DouyinAccount:
        """创建抖音账号"""
        # 检查是否是第一个账号
        result = await self.session.execute(
            select(func.count(DouyinAccount.id)).filter_by(admin_id=admin_id)
        )
        count = result.scalar()

        if count == 0:
            kwargs['is_default'] = 1

        account = DouyinAccount(admin_id=admin_id, **kwargs)
        self.session.add(account)
        await self.session.commit()
        await self.session.refresh(account)
        return account

    async def update_account(self, account_id: int, **kwargs) -> bool:
        """更新账号信息"""
        result = await self.session.execute(
            update(DouyinAccount)
            .where(DouyinAccount.id == account_id)
            .values(**kwargs)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def set_default_account(self, account_id: int, admin_id: int) -> bool:
        """设置默认账号"""
        # 取消其他账号的默认状态
        await self.session.execute(
            update(DouyinAccount)
            .where(DouyinAccount.admin_id == admin_id)
            .values(is_default=0)
        )

        # 设置指定账号为默认
        result = await self.session.execute(
            update(DouyinAccount)
            .where(and_(DouyinAccount.id == account_id, DouyinAccount.admin_id == admin_id))
            .values(is_default=1)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def delete_account(self, account_id: int, admin_id: int) -> bool:
        """删除账号（软删除）"""
        result = await self.session.execute(
            update(DouyinAccount)
            .where(and_(DouyinAccount.id == account_id, DouyinAccount.admin_id == admin_id))
            .values(status=0)
        )
        await self.session.commit()
        return result.rowcount > 0

    # ==================== DouyinFriend 相关 ====================

    async def get_friends(self, account_id: int, group: str = None) -> List[DouyinFriend]:
        """获取好友列表"""
        query = select(DouyinFriend).filter_by(douyin_account_id=account_id)
        if group:
            query = query.filter_by(group_name=group)
        query = query.order_by(DouyinFriend.nickname)

        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_friend_by_uid(self, account_id: int, uid: str) -> Optional[DouyinFriend]:
        """根据UID获取好友"""
        result = await self.session.execute(
            select(DouyinFriend)
            .filter_by(douyin_account_id=account_id, uid=uid)
        )
        return result.scalar_one_or_none()

    async def upsert_friend(self, account_id: int, uid: str, **kwargs) -> DouyinFriend:
        """创建或更新好友信息"""
        friend = await self.get_friend_by_uid(account_id, uid)

        if friend:
            # 更新
            for key, value in kwargs.items():
                if hasattr(friend, key):
                    setattr(friend, key, value)
        else:
            # 创建
            friend = DouyinFriend(douyin_account_id=account_id, uid=uid, **kwargs)
            self.session.add(friend)

        await self.session.commit()
        await self.session.refresh(friend)
        return friend

    async def batch_upsert_friends(self, account_id: int, friends_data: List[Dict]) -> int:
        """批量更新好友列表"""
        count = 0
        for data in friends_data:
            uid = data.pop('uid')
            await self.upsert_friend(account_id, uid, **data)
            count += 1
        return count

    async def get_friend_groups(self, account_id: int) -> List[str]:
        """获取好友分组列表"""
        result = await self.session.execute(
            select(DouyinFriend.group_name)
            .filter(
                DouyinFriend.douyin_account_id == account_id,
                DouyinFriend.group_name.isnot(None)
            )
            .distinct()
        )
        return [r[0] for r in result.all()]

    # ==================== ScheduledTask 相关 ====================

    async def get_tasks(self, account_id: int = None, admin_id: int = None,
                       status: int = None) -> List[ScheduledTask]:
        """获取任务列表（使用 eager loading 避免 N+1 查询）"""
        query = select(ScheduledTask).options(
            joinedload(ScheduledTask.account)
        )

        if account_id:
            query = query.filter_by(douyin_account_id=account_id)
        if admin_id:
            query = query.filter_by(admin_id=admin_id)
        if status is not None:
            query = query.filter_by(status=status)

        query = query.order_by(ScheduledTask.created_at.desc())

        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_task_by_id(self, task_id: int) -> Optional[ScheduledTask]:
        """根据ID获取任务"""
        result = await self.session.execute(
            select(ScheduledTask).filter_by(id=task_id)
        )
        return result.scalar_one_or_none()

    async def create_task(self, **kwargs) -> ScheduledTask:
        """创建任务"""
        task = ScheduledTask(**kwargs)
        self.session.add(task)
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def update_task(self, task_id: int, **kwargs) -> bool:
        """更新任务"""
        result = await self.session.execute(
            update(ScheduledTask)
            .where(ScheduledTask.id == task_id)
            .values(**kwargs)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def delete_task(self, task_id: int) -> bool:
        """删除任务"""
        result = await self.session.execute(
            delete(ScheduledTask).where(ScheduledTask.id == task_id)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def get_due_tasks(self, now: datetime = None) -> List[ScheduledTask]:
        """获取到期待执行的任务"""
        if now is None:
            now = datetime.now()

        result = await self.session.execute(
            select(ScheduledTask)
            .filter(
                ScheduledTask.status == 1,
                ScheduledTask.next_run_at <= now
            )
            .options(joinedload(ScheduledTask.account))
        )
        return result.scalars().all()

    async def update_task_stats(self, task_id: int, success: bool,
                               error_msg: str = None, next_run: datetime = None):
        """更新任务统计信息"""
        updates = {
            'total_runs': ScheduledTask.total_runs + 1,
            'last_run_at': datetime.now()
        }

        if success:
            updates['success_runs'] = ScheduledTask.success_runs + 1
            updates['last_run_status'] = 'success'
        else:
            updates['failed_runs'] = ScheduledTask.failed_runs + 1
            updates['last_run_status'] = 'failed'
            if error_msg:
                updates['last_error_message'] = error_msg

        if next_run:
            updates['next_run_at'] = next_run

        await self.session.execute(
            update(ScheduledTask)
            .where(ScheduledTask.id == task_id)
            .values(**updates)
        )
        await self.session.commit()

    # ==================== TaskExecution 相关 ====================

    async def create_execution(self, **kwargs) -> TaskExecution:
        """创建执行记录"""
        execution = TaskExecution(**kwargs)
        self.session.add(execution)
        await self.session.commit()
        await self.session.refresh(execution)
        return execution

    async def update_execution(self, execution_id: int, **kwargs) -> bool:
        """更新执行记录"""
        # 自动计算执行时长
        if 'finished_at' in kwargs:
            execution = await self.session.get(TaskExecution, execution_id)
            if execution and execution.started_at:
                kwargs['duration_seconds'] = (
                    kwargs['finished_at'] - execution.started_at
                ).total_seconds()

        result = await self.session.execute(
            update(TaskExecution)
            .where(TaskExecution.id == execution_id)
            .values(**kwargs)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def get_task_executions(self, task_id: int, limit: int = 100) -> List[TaskExecution]:
        """获取任务执行历史"""
        result = await self.session.execute(
            select(TaskExecution)
            .filter_by(task_id=task_id)
            .order_by(TaskExecution.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def get_executions_stats(self, account_id: int = None,
                                  days: int = 30) -> Dict[str, Any]:
        """获取执行统计"""
        start_date = datetime.now() - timedelta(days=days)
        query = select(TaskExecution).filter(TaskExecution.created_at >= start_date)

        if account_id:
            query = query.filter_by(douyin_account_id=account_id)

        result = await self.session.execute(query)
        executions = result.scalars().all()

        total = len(executions)
        success = sum(1 for e in executions if e.result == 1)
        failed = sum(1 for e in executions if e.result == 0)

        return {
            'total': total,
            'success': success,
            'failed': failed,
            'success_rate': round(success / total * 100, 2) if total > 0 else 0
        }

    # ==================== SendHistory 相关 ====================

    async def check_sent_today(self, account_id: int, target_uid: str,
                              send_date: date = None) -> bool:
        """检查今天是否已发送"""
        if send_date is None:
            send_date = date.today()

        result = await self.session.execute(
            select(SendHistory)
            .filter_by(
                douyin_account_id=account_id,
                target_uid=target_uid,
                send_date=send_date
            )
        )
        return result.scalar_one_or_none() is not None

    async def record_send(self, account_id: int, target_uid: str,
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
        await self.session.commit()

    async def cleanup_old_history(self, days: int = 90) -> int:
        """清理旧的发送历史"""
        cutoff_date = date.today() - timedelta(days=days)
        result = await self.session.execute(
            delete(SendHistory).where(SendHistory.send_date < cutoff_date)
        )
        await self.session.commit()
        return result.rowcount

    # ==================== SystemConfig 相关 ====================

    async def get_config(self, group: str, key: str, default: Any = None) -> Any:
        """获取配置值"""
        result = await self.session.execute(
            select(SystemConfig)
            .filter_by(config_group=group, config_key=key)
        )
        config = result.scalar_one_or_none()

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

    async def set_config(self, group: str, key: str, value: Any):
        """设置配置值"""
        result = await self.session.execute(
            select(SystemConfig)
            .filter_by(config_group=group, config_key=key)
        )
        config = result.scalar_one_or_none()

        if config:
            config.config_value = str(value)
        else:
            config = SystemConfig(
                config_group=group,
                config_key=key,
                config_value=str(value)
            )
            self.session.add(config)

        await self.session.commit()

    async def get_configs_by_group(self, group: str) -> Dict[str, Any]:
        """获取分组下的所有配置"""
        result = await self.session.execute(
            select(SystemConfig)
            .filter_by(config_group=group)
            .order_by(SystemConfig.sort_order)
        )
        configs = result.scalars().all()

        result_dict = {}
        for config in configs:
            result_dict[config.config_key] = await self.get_config(group, config.config_key)

        return result_dict

    # ==================== OperationLog 相关 ====================

    async def log_operation(self, admin_id: int = None, log_type: str = None,
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
        await self.session.commit()
        return log

    async def get_logs(self, admin_id: int = None, log_type: str = None,
                      limit: int = 100, offset: int = 0) -> Tuple[List[OperationLog], int]:
        """获取操作日志"""
        query = select(OperationLog)

        if admin_id:
            query = query.filter_by(admin_id=admin_id)
        if log_type:
            query = query.filter_by(log_type=log_type)

        # 获取总数
        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar()

        # 获取分页数据
        query = query.order_by(OperationLog.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(query)
        logs = result.scalars().all()

        return logs, total

    # ==================== NotifyConfig 相关 ====================

    async def get_notify_config(self, admin_id: int) -> Optional[NotifyConfig]:
        """获取通知配置"""
        result = await self.session.execute(
            select(NotifyConfig).filter_by(admin_id=admin_id)
        )
        return result.scalar_one_or_none()

    async def upsert_notify_config(self, admin_id: int, **kwargs) -> NotifyConfig:
        """创建或更新通知配置"""
        config = await self.get_notify_config(admin_id)

        if config:
            for key, value in kwargs.items():
                if hasattr(config, key):
                    setattr(config, key, value)
        else:
            config = NotifyConfig(admin_id=admin_id, **kwargs)
            self.session.add(config)

        await self.session.commit()
        await self.session.refresh(config)
        return config
