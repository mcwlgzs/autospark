#!/usr/bin/env python
"""
AutoSpark v2.2 数据库升级脚本
独立运行，不依赖复杂的导入
"""

import os
import sqlite3
import sys
from datetime import datetime


def get_db_path():
    """获取数据库路径"""
    # 尝试多个可能的位置
    possible_paths = [
        os.path.join(os.path.dirname(__file__), 'spark.db'),  # 项目根目录
        os.path.join(os.path.dirname(__file__), 'data', 'spark.db'),  # data目录
        'spark.db',  # 当前目录
    ]

    # 环境变量指定的路径
    env_dir = os.getenv('SPARK_DATA_DIR')
    if env_dir:
        possible_paths.insert(0, os.path.join(env_dir, 'spark.db'))

    for db_path in possible_paths:
        if os.path.exists(db_path):
            print(f"✅ 找到数据库: {db_path}")
            return db_path

    # 如果都不存在，尝试创建一个新的
    default_path = os.path.join(os.path.dirname(__file__), 'spark.db')
    print(f"⚠️  数据库文件不存在，将创建新数据库: {default_path}")
    print("提示：如果要升级现有数据库，请确保数据库文件在项目根目录")

    response = input("是否继续创建新数据库？(y/N): ").strip().lower()
    if response != 'y':
        print("已取消")
        sys.exit(0)

    return default_path


def column_exists(cursor, table_name, column_name):
    """检查列是否存在"""
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cursor.fetchall()]
    return column_name in columns


def table_exists(cursor, table_name):
    """检查表是否存在"""
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone() is not None


def upgrade_douyin_accounts(conn, cursor):
    """升级 douyin_accounts 表"""
    print("\n📊 升级 douyin_accounts 表...")

    fields = [
        ('device_fp', 'VARCHAR(64)', '设备指纹'),
        ('login_method', "VARCHAR(20) DEFAULT 'qrcode'", '登录方式'),
        ('cookie_created_at', 'DATETIME', 'Cookie创建时间'),
        ('cookie_last_check', 'DATETIME', 'Cookie最后检查时间'),
        ('cookie_check_interval', 'INTEGER DEFAULT 3600', '检查间隔（秒）'),
        ('daily_operation_count', 'INTEGER DEFAULT 0', '每日操作计数'),
        ('last_operation_at', 'DATETIME', '最后操作时间'),
        ('risk_level', 'INTEGER DEFAULT 0', '风险等级 0-5'),
        ('browser_pid', 'INTEGER DEFAULT 0', '浏览器进程PID'),
        ('last_browser_start', 'DATETIME', '浏览器启动时间'),
    ]

    added_count = 0
    skipped_count = 0

    for field_name, field_type, field_desc in fields:
        if column_exists(cursor, 'douyin_accounts', field_name):
            print(f"  ⏭️  跳过 {field_name} (已存在)")
            skipped_count += 1
        else:
            try:
                sql = f"ALTER TABLE douyin_accounts ADD COLUMN {field_name} {field_type}"
                cursor.execute(sql)
                conn.commit()
                print(f"  ✅ 添加 {field_name} - {field_desc}")
                added_count += 1
            except Exception as e:
                print(f"  ❌ 添加 {field_name} 失败: {e}")

    print(f"\n  📈 统计: 新增 {added_count} 个字段, 跳过 {skipped_count} 个字段")
    return added_count


def upgrade_task_executions(conn, cursor):
    """升级 task_executions 表"""
    print("\n📊 升级 task_executions 表...")

    if not table_exists(cursor, 'task_executions'):
        print("  ⚠️  表不存在，跳过")
        return 0

    fields = [
        ('ip_address', 'VARCHAR(50)', 'IP地址'),
        ('device_fp', 'VARCHAR(64)', '设备指纹'),
    ]

    added_count = 0

    for field_name, field_type, field_desc in fields:
        if column_exists(cursor, 'task_executions', field_name):
            print(f"  ⏭️  跳过 {field_name} (已存在)")
        else:
            try:
                sql = f"ALTER TABLE task_executions ADD COLUMN {field_name} {field_type}"
                cursor.execute(sql)
                conn.commit()
                print(f"  ✅ 添加 {field_name} - {field_desc}")
                added_count += 1
            except Exception as e:
                print(f"  ❌ 添加 {field_name} 失败: {e}")

    return added_count


def create_qrcode_table(conn, cursor):
    """创建 douyin_qrcodes 表"""
    print("\n📊 创建 douyin_qrcodes 表...")

    if table_exists(cursor, 'douyin_qrcodes'):
        print("  ⏭️  表已存在，跳过")
        return False

    try:
        sql = """
        CREATE TABLE douyin_qrcodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token VARCHAR(128) UNIQUE NOT NULL,
            fp VARCHAR(64),
            admin_id INTEGER,
            qrcode_data TEXT,
            qrcode_url VARCHAR(512),
            status INTEGER DEFAULT 0,
            expires_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (admin_id) REFERENCES admins(id) ON DELETE CASCADE
        )
        """
        cursor.execute(sql)

        # 创建索引
        cursor.execute("CREATE INDEX idx_qrcodes_token ON douyin_qrcodes(token)")
        cursor.execute("CREATE INDEX idx_qrcodes_status ON douyin_qrcodes(status)")
        cursor.execute("CREATE INDEX idx_qrcodes_admin ON douyin_qrcodes(admin_id)")

        conn.commit()
        print("  ✅ 创建成功")
        return True
    except Exception as e:
        print(f"  ❌ 创建失败: {e}")
        return False


def create_announcements_table(conn, cursor):
    """创建 announcements 表"""
    print("\n📊 创建 announcements 表...")

    if table_exists(cursor, 'announcements'):
        print("  ⏭️  表已存在，跳过")
        return False

    try:
        sql = """
        CREATE TABLE announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type VARCHAR(20) DEFAULT 'system',
            title VARCHAR(200) NOT NULL,
            content TEXT,
            level VARCHAR(20) DEFAULT 'info',
            status INTEGER DEFAULT 1,
            show_from DATETIME,
            show_until DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
        cursor.execute(sql)

        # 创建索引
        cursor.execute("CREATE INDEX idx_announcements_status ON announcements(status)")
        cursor.execute("CREATE INDEX idx_announcements_type ON announcements(type)")

        conn.commit()
        print("  ✅ 创建成功")
        return True
    except Exception as e:
        print(f"  ❌ 创建失败: {e}")
        return False


def create_backup(db_path):
    """创建数据库备份"""
    backup_path = f"{db_path}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    try:
        import shutil
        shutil.copy2(db_path, backup_path)
        print(f"✅ 数据库备份成功: {backup_path}")
        return backup_path
    except Exception as e:
        print(f"⚠️  备份失败: {e}")
        return None


def main():
    """主函数"""
    print("=" * 60)
    print("AutoSpark v2.2 数据库升级工具")
    print("=" * 60)

    # 获取数据库路径
    db_path = get_db_path()
    print(f"\n📂 数据库路径: {db_path}")

    # 创建备份
    print("\n🔄 创建备份...")
    backup_path = create_backup(db_path)

    # 连接数据库
    print("\n🔌 连接数据库...")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        print("✅ 连接成功")
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        sys.exit(1)

    try:
        # 执行升级
        print("\n" + "=" * 60)
        print("开始升级...")
        print("=" * 60)

        total_changes = 0

        # 1. 升级 douyin_accounts
        total_changes += upgrade_douyin_accounts(conn, cursor)

        # 2. 升级 task_executions
        total_changes += upgrade_task_executions(conn, cursor)

        # 3. 创建 douyin_qrcodes
        if create_qrcode_table(conn, cursor):
            total_changes += 1

        # 4. 创建 announcements
        if create_announcements_table(conn, cursor):
            total_changes += 1

        # 提交所有更改
        conn.commit()

        print("\n" + "=" * 60)
        print("升级完成！")
        print("=" * 60)
        print(f"\n📊 统计:")
        print(f"  - 总变更: {total_changes} 项")
        if backup_path:
            print(f"  - 备份位置: {backup_path}")

        print("\n✅ 数据库已升级到 v2.2")
        print("\n📝 后续步骤:")
        print("  1. 检查应用是否正常启动")
        print("  2. 测试新功能（Cookie监控、设备指纹）")
        print("  3. 查看 INTEGRATION_GUIDE.md 了解如何集成")

    except Exception as e:
        print(f"\n❌ 升级失败: {e}")
        conn.rollback()

        if backup_path:
            print(f"\n⚠️  请从备份恢复: {backup_path}")

        sys.exit(1)

    finally:
        cursor.close()
        conn.close()


if __name__ == '__main__':
    main()
