"""状态迁移工具 - 从JSON文件迁移到数据库

使用方式:
    python migrate_state.py

功能:
    1. 读取 data/state.json 中的数据
    2. 迁移到数据库中
    3. 备份原文件
    4. 保持数据完整性
"""

import json
import os
import shutil
from datetime import datetime, date
from typing import Dict, Any

from models import init_database, get_database
from dal import DAL
import hashlib


def migrate_from_state_json(state_path: str = None):
    """从state.json迁移到数据库"""

    if state_path is None:
        data_dir = os.getenv('SPARK_DATA_DIR', os.path.join(os.path.dirname(__file__), 'data'))
        state_path = os.path.join(data_dir, 'state.json')

    if not os.path.exists(state_path):
        print(f"❌ 状态文件不存在: {state_path}")
        return False

    # 读取state.json
    try:
        with open(state_path, 'r', encoding='utf-8') as f:
            state_data = json.load(f)
        print(f"✅ 读取状态文件成功: {len(state_data)} 个字段")
    except Exception as e:
        print(f"❌ 读取状态文件失败: {e}")
        return False

    # 初始化数据库
    try:
        db = init_database()
        print("✅ 数据库初始化成功")
    except Exception as e:
        print(f"❌ 数据库初始化失败: {e}")
        return False

    # 开始迁移
    dal = DAL()
    migrated_counts = {
        'admin': 0,
        'tasks': 0,
        'notify': 0,
        'history': 0
    }

    try:
        # 1. 迁移管理员密码
        if 'password_hash' in state_data:
            password_hash = state_data['password_hash']
            # 获取默认管理员
            admin = dal.get_admin_by_username('admin')
            if admin:
                admin.password_hash = password_hash
                dal.session.commit()
                migrated_counts['admin'] += 1
                print(f"✅ 迁移管理员密码")

        # 2. 迁移定时任务
        if 'tasks' in state_data and isinstance(state_data['tasks'], dict):
            tasks_data = state_data['tasks']
            admin = dal.get_admin_by_username('admin')

            # 假设使用默认账号，如果没有则创建一个
            account = dal.get_default_account(admin.id)
            if not account:
                # 创建一个临时账号（需要后续补充cookie）
                account = dal.create_account(
                    admin_id=admin.id,
                    nickname='迁移的账号（需补充登录）',
                    status=0,  # 禁用状态
                    remark='从旧系统迁移，需要重新登录'
                )
                print(f"⚠️  创建临时抖音账号 (ID: {account.id})，需要后续登录")

            for task_key, task_info in tasks_data.items():
                try:
                    # 解析任务信息
                    name = task_info.get('name', '')
                    time_str = task_info.get('time', '22:00')
                    text = task_info.get('text', '')
                    sign = task_info.get('sign', False)
                    image_only = task_info.get('image_only', False)

                    # 创建任务
                    task = dal.create_task(
                        name=f"迁移任务-{name}" if name else None,
                        douyin_account_id=account.id,
                        admin_id=admin.id,
                        target_uid='',  # 旧系统没有uid，需要后续补充
                        target_nickname=name,
                        task_type='daily',
                        send_time=time_str,
                        jitter_minutes=40,
                        message_type='fixed' if text else 'pool',
                        message_content=text if text else None,
                        send_sign=1 if sign else 0,
                        image_only=1 if image_only else 0,
                        status=1
                    )
                    migrated_counts['tasks'] += 1
                    print(f"✅ 迁移任务: {name}")

                except Exception as e:
                    print(f"⚠️  迁移任务 {task_key} 失败: {e}")
                    continue

        # 3. 迁移通知配置
        if 'notify' in state_data and isinstance(state_data['notify'], dict):
            notify_data = state_data['notify']
            admin = dal.get_admin_by_username('admin')

            try:
                config_dict = {}

                # 推送配置
                if 'push_url' in notify_data:
                    config_dict['push_enabled'] = 1
                    config_dict['push_url'] = notify_data['push_url']
                    config_dict['push_on_success'] = 1 if notify_data.get('push_on_success') else 0
                    config_dict['push_on_failure'] = 1 if notify_data.get('push_on_failure', True) else 0

                # 邮件配置
                email_settings = notify_data.get('email', {})
                if email_settings:
                    config_dict['email_enabled'] = 1 if notify_data.get('email_enabled') else 0
                    config_dict['smtp_host'] = email_settings.get('smtp_host', '')
                    config_dict['smtp_port'] = email_settings.get('smtp_port', 465)
                    config_dict['smtp_username'] = email_settings.get('smtp_user', '')
                    config_dict['smtp_password'] = email_settings.get('smtp_password', '')
                    config_dict['from_email'] = email_settings.get('from_addr', '')
                    config_dict['from_name'] = email_settings.get('from_name', '')
                    config_dict['to_email'] = email_settings.get('to_addr', '')
                    config_dict['email_on_success'] = 1 if email_settings.get('on_success') else 0
                    config_dict['email_on_failure'] = 1 if email_settings.get('on_failure', True) else 0

                if config_dict:
                    dal.upsert_notify_config(admin.id, **config_dict)
                    migrated_counts['notify'] += 1
                    print(f"✅ 迁移通知配置")

            except Exception as e:
                print(f"⚠️  迁移通知配置失败: {e}")

        # 4. 迁移发送历史（防重复）
        if 'send_history' in state_data and isinstance(state_data['send_history'], dict):
            history_data = state_data['send_history']
            admin = dal.get_admin_by_username('admin')
            account = dal.get_default_account(admin.id)

            if account:
                for history_key, history_info in history_data.items():
                    try:
                        # 解析 key: "uid|YYYY-MM-DD"
                        if '|' in history_key:
                            uid, date_str = history_key.split('|', 1)
                            send_date = datetime.strptime(date_str, '%Y-%m-%d').date()

                            # 记录历史
                            dal.record_send(
                                account_id=account.id,
                                target_uid=uid,
                                send_date=send_date
                            )
                            migrated_counts['history'] += 1

                    except Exception as e:
                        print(f"⚠️  迁移历史记录 {history_key} 失败: {e}")
                        continue

                print(f"✅ 迁移发送历史: {migrated_counts['history']} 条")

        dal.session.commit()

        # 备份原文件
        backup_path = f"{state_path}.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(state_path, backup_path)
        print(f"✅ 原文件已备份到: {backup_path}")

        # 输出迁移总结
        print("\n" + "="*50)
        print("📊 迁移完成统计:")
        print(f"  - 管理员: {migrated_counts['admin']}")
        print(f"  - 定时任务: {migrated_counts['tasks']}")
        print(f"  - 通知配置: {migrated_counts['notify']}")
        print(f"  - 发送历史: {migrated_counts['history']}")
        print("="*50)

        if migrated_counts['tasks'] > 0:
            print("\n⚠️  重要提示:")
            print("  1. 旧任务中的好友信息不完整，需要重新配置")
            print("  2. 迁移创建的账号需要重新登录")
            print("  3. 建议检查所有任务配置")

        return True

    except Exception as e:
        dal.session.rollback()
        print(f"❌ 迁移失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        dal.session.close()


def main():
    """主函数"""
    print("="*50)
    print("🔄 抖音续火花系统 - 数据迁移工具")
    print("="*50)
    print()

    # 确认迁移
    response = input("⚠️  此操作将迁移 state.json 数据到数据库，是否继续? (yes/no): ")
    if response.lower() not in ('yes', 'y'):
        print("❌ 已取消迁移")
        return

    print()
    success = migrate_from_state_json()

    if success:
        print("\n✅ 迁移成功！")
        print("\n📝 后续步骤:")
        print("  1. 启动系统: python backend.py")
        print("  2. 登录管理后台")
        print("  3. 在「账号管理」页面添加/登录抖音账号")
        print("  4. 在「任务管理」页面检查并配置任务")
    else:
        print("\n❌ 迁移失败，请检查错误信息")


if __name__ == '__main__':
    main()
