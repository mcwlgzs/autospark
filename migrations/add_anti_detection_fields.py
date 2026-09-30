"""
数据库迁移脚本 - 添加防封相关字段
基于douyin_huohua对比分析的改进
"""

from datetime import datetime
from sqlalchemy import text
from models_async import async_engine, AsyncDatabase


async def upgrade_database():
    """升级数据库架构 - 添加防封字段"""

    migrations = [
        # ========================================
        # 1. douyin_accounts表 - 添加防封字段
        # ========================================
        {
            'name': '添加设备指纹字段',
            'sqlite': """
                ALTER TABLE douyin_accounts
                ADD COLUMN device_fp VARCHAR(64);
            """,
            'mysql': """
                ALTER TABLE douyin_accounts
                ADD COLUMN device_fp VARCHAR(64) DEFAULT NULL COMMENT '设备指纹';
            """
        },
        {
            'name': '添加登录方式字段',
            'sqlite': """
                ALTER TABLE douyin_accounts
                ADD COLUMN login_method VARCHAR(20) DEFAULT 'qrcode';
            """,
            'mysql': """
                ALTER TABLE douyin_accounts
                ADD COLUMN login_method VARCHAR(20) DEFAULT 'qrcode' COMMENT '登录方式：qrcode/password/session';
            """
        },
        {
            'name': '添加Cookie创建时间',
            'sqlite': """
                ALTER TABLE douyin_accounts
                ADD COLUMN cookie_created_at DATETIME;
            """,
            'mysql': """
                ALTER TABLE douyin_accounts
                ADD COLUMN cookie_created_at DATETIME DEFAULT NULL COMMENT 'Cookie创建时间';
            """
        },
        {
            'name': '添加Cookie最后检查时间',
            'sqlite': """
                ALTER TABLE douyin_accounts
                ADD COLUMN cookie_last_check DATETIME;
            """,
            'mysql': """
                ALTER TABLE douyin_accounts
                ADD COLUMN cookie_last_check DATETIME DEFAULT NULL COMMENT 'Cookie最后检查时间';
            """
        },
        {
            'name': '添加Cookie检查间隔',
            'sqlite': """
                ALTER TABLE douyin_accounts
                ADD COLUMN cookie_check_interval INTEGER DEFAULT 3600;
            """,
            'mysql': """
                ALTER TABLE douyin_accounts
                ADD COLUMN cookie_check_interval INT DEFAULT 3600 COMMENT 'Cookie检查间隔（秒）';
            """
        },
        {
            'name': '添加每日操作计数',
            'sqlite': """
                ALTER TABLE douyin_accounts
                ADD COLUMN daily_operation_count INTEGER DEFAULT 0;
            """,
            'mysql': """
                ALTER TABLE douyin_accounts
                ADD COLUMN daily_operation_count INT DEFAULT 0 COMMENT '每日操作次数';
            """
        },
        {
            'name': '添加最后操作时间',
            'sqlite': """
                ALTER TABLE douyin_accounts
                ADD COLUMN last_operation_at DATETIME;
            """,
            'mysql': """
                ALTER TABLE douyin_accounts
                ADD COLUMN last_operation_at DATETIME DEFAULT NULL COMMENT '最后操作时间';
            """
        },
        {
            'name': '添加风险等级',
            'sqlite': """
                ALTER TABLE douyin_accounts
                ADD COLUMN risk_level INTEGER DEFAULT 0;
            """,
            'mysql': """
                ALTER TABLE douyin_accounts
                ADD COLUMN risk_level INT DEFAULT 0 COMMENT '风险等级 0-5';
            """
        },
        {
            'name': '添加浏览器进程PID',
            'sqlite': """
                ALTER TABLE douyin_accounts
                ADD COLUMN browser_pid INTEGER DEFAULT 0;
            """,
            'mysql': """
                ALTER TABLE douyin_accounts
                ADD COLUMN browser_pid INT DEFAULT 0 COMMENT '浏览器进程PID';
            """
        },
        {
            'name': '添加浏览器启动时间',
            'sqlite': """
                ALTER TABLE douyin_accounts
                ADD COLUMN last_browser_start DATETIME;
            """,
            'mysql': """
                ALTER TABLE douyin_accounts
                ADD COLUMN last_browser_start DATETIME DEFAULT NULL COMMENT '浏览器最后启动时间';
            """
        },

        # ========================================
        # 2. task_executions表 - 添加跟踪字段
        # ========================================
        {
            'name': '添加IP地址记录',
            'sqlite': """
                ALTER TABLE task_executions
                ADD COLUMN ip_address VARCHAR(50);
            """,
            'mysql': """
                ALTER TABLE task_executions
                ADD COLUMN ip_address VARCHAR(50) DEFAULT NULL COMMENT 'IP地址';
            """
        },
        {
            'name': '添加设备指纹记录',
            'sqlite': """
                ALTER TABLE task_executions
                ADD COLUMN device_fp VARCHAR(64);
            """,
            'mysql': """
                ALTER TABLE task_executions
                ADD COLUMN device_fp VARCHAR(64) DEFAULT NULL COMMENT '使用的设备指纹';
            """
        },

        # ========================================
        # 3. 创建二维码登录表
        # ========================================
        {
            'name': '创建二维码登录表',
            'sqlite': """
                CREATE TABLE IF NOT EXISTS douyin_qrcodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token VARCHAR(128) NOT NULL UNIQUE,
                    fp VARCHAR(64),
                    admin_id INTEGER,
                    qrcode_data TEXT,
                    qrcode_url VARCHAR(512),
                    status INTEGER DEFAULT 0,
                    expires_at DATETIME NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (admin_id) REFERENCES admins(id) ON DELETE CASCADE
                );
            """,
            'mysql': """
                CREATE TABLE IF NOT EXISTS douyin_qrcodes (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    token VARCHAR(128) NOT NULL UNIQUE COMMENT '本地token',
                    fp VARCHAR(64) DEFAULT NULL COMMENT '设备指纹',
                    admin_id BIGINT UNSIGNED COMMENT '请求管理员ID',
                    qrcode_data TEXT DEFAULT NULL COMMENT 'Base64二维码图片',
                    qrcode_url VARCHAR(512) DEFAULT NULL COMMENT '二维码URL',
                    status TINYINT NOT NULL DEFAULT 0 COMMENT '状态：0-待扫码，1-已扫码，2-已确认，3-已过期',
                    expires_at DATETIME NOT NULL COMMENT '过期时间',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_token (token),
                    KEY idx_admin_id (admin_id),
                    KEY idx_status (status),
                    FOREIGN KEY (admin_id) REFERENCES admins(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='抖音扫码二维码表';
            """
        },
        {
            'name': '创建二维码表索引',
            'sqlite': """
                CREATE INDEX IF NOT EXISTS idx_qrcodes_admin ON douyin_qrcodes(admin_id);
                CREATE INDEX IF NOT EXISTS idx_qrcodes_status ON douyin_qrcodes(status);
            """,
            'mysql': ""  # MySQL在CREATE TABLE时已创建
        },

        # ========================================
        # 4. 创建公告表
        # ========================================
        {
            'name': '创建公告表',
            'sqlite': """
                CREATE TABLE IF NOT EXISTS announcements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type VARCHAR(20) NOT NULL,
                    title VARCHAR(200) NOT NULL,
                    content TEXT NOT NULL,
                    level VARCHAR(20) DEFAULT 'info',
                    status INTEGER DEFAULT 1,
                    show_from DATETIME,
                    show_until DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """,
            'mysql': """
                CREATE TABLE IF NOT EXISTS announcements (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    type VARCHAR(20) NOT NULL COMMENT '类型：system/maintenance/feature',
                    title VARCHAR(200) NOT NULL COMMENT '标题',
                    content TEXT NOT NULL COMMENT '内容',
                    level VARCHAR(20) DEFAULT 'info' COMMENT '级别：info/warning/danger',
                    status TINYINT NOT NULL DEFAULT 1 COMMENT '状态：0-禁用，1-启用',
                    show_from DATETIME DEFAULT NULL COMMENT '显示开始时间',
                    show_until DATETIME DEFAULT NULL COMMENT '显示结束时间',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (id),
                    KEY idx_type (type),
                    KEY idx_status (status),
                    KEY idx_show_time (show_from, show_until)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='公告表';
            """
        },

        # ========================================
        # 5. 优化索引
        # ========================================
        {
            'name': '添加账号防封复合索引',
            'sqlite': """
                CREATE INDEX IF NOT EXISTS idx_account_risk
                ON douyin_accounts(admin_id, cookie_status, risk_level);
            """,
            'mysql': """
                CREATE INDEX idx_account_risk
                ON douyin_accounts(admin_id, cookie_status, risk_level);
            """
        },
        {
            'name': '添加账号操作时间索引',
            'sqlite': """
                CREATE INDEX IF NOT EXISTS idx_account_operation
                ON douyin_accounts(last_operation_at);
            """,
            'mysql': """
                CREATE INDEX idx_account_operation
                ON douyin_accounts(last_operation_at);
            """
        },
    ]

    # 检测数据库类型
    db = AsyncDatabase()
    db_type = 'sqlite' if 'sqlite' in str(async_engine.url) else 'mysql'

    print(f"\n{'='*60}")
    print(f"开始数据库迁移 - {db_type.upper()}")
    print(f"{'='*60}\n")

    success_count = 0
    skip_count = 0
    error_count = 0

    async with async_engine.begin() as conn:
        for idx, migration in enumerate(migrations, 1):
            migration_name = migration['name']
            sql = migration.get(db_type, '')

            if not sql or sql.strip() == '':
                skip_count += 1
                print(f"[{idx}/{len(migrations)}] ⏭️  跳过: {migration_name}")
                continue

            try:
                # 对于多条SQL语句，分别执行
                for statement in sql.split(';'):
                    statement = statement.strip()
                    if statement:
                        await conn.execute(text(statement))

                success_count += 1
                print(f"[{idx}/{len(migrations)}] ✅ 成功: {migration_name}")

            except Exception as e:
                error_msg = str(e)

                # 忽略"已存在"的错误
                if any(keyword in error_msg.lower() for keyword in [
                    'already exists',
                    'duplicate column',
                    'duplicate key',
                    '已存在'
                ]):
                    skip_count += 1
                    print(f"[{idx}/{len(migrations)}] ⏭️  已存在: {migration_name}")
                else:
                    error_count += 1
                    print(f"[{idx}/{len(migrations)}] ❌ 失败: {migration_name}")
                    print(f"   错误: {error_msg}")

    print(f"\n{'='*60}")
    print(f"迁移完成")
    print(f"{'='*60}")
    print(f"✅ 成功: {success_count}")
    print(f"⏭️  跳过: {skip_count}")
    print(f"❌ 失败: {error_count}")
    print(f"{'='*60}\n")

    return {
        'success': success_count,
        'skipped': skip_count,
        'errors': error_count
    }


async def downgrade_database():
    """降级数据库（回滚迁移）- 慎用！"""
    print("\n⚠️  警告: 此操作将删除新增的字段和表！")
    print("建议先备份数据库！")

    # 这里可以添加回滚逻辑
    # 但通常不建议自动回滚，因为可能导致数据丢失
    pass


# 使用示例
if __name__ == "__main__":
    import asyncio

    async def main():
        print("\n🚀 AutoSpark v2.2 数据库迁移工具")
        print("基于douyin_huohua对比分析的改进\n")

        result = await upgrade_database()

        if result['errors'] == 0:
            print("✅ 迁移成功完成！")
        else:
            print(f"⚠️  迁移完成，但有 {result['errors']} 个错误")

    asyncio.run(main())
