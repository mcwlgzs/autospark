-- ========================================
-- AutoSpark v2.2 数据库升级SQL
-- 直接在SQLite或MySQL客户端执行
-- ========================================

-- 备份提醒：执行前请备份数据库！
-- SQLite: cp spark.db spark.db.backup
-- MySQL: mysqldump spark > spark_backup.sql

-- ========================================
-- 1. 升级 douyin_accounts 表
-- ========================================

-- 1.1 设备指纹字段
ALTER TABLE douyin_accounts ADD COLUMN device_fp VARCHAR(64);

-- 1.2 登录方式字段
ALTER TABLE douyin_accounts ADD COLUMN login_method VARCHAR(20) DEFAULT 'qrcode';

-- 1.3 Cookie创建时间
ALTER TABLE douyin_accounts ADD COLUMN cookie_created_at DATETIME;

-- 1.4 Cookie最后检查时间
ALTER TABLE douyin_accounts ADD COLUMN cookie_last_check DATETIME;

-- 1.5 检查间隔（秒）
ALTER TABLE douyin_accounts ADD COLUMN cookie_check_interval INTEGER DEFAULT 3600;

-- 1.6 每日操作计数
ALTER TABLE douyin_accounts ADD COLUMN daily_operation_count INTEGER DEFAULT 0;

-- 1.7 最后操作时间
ALTER TABLE douyin_accounts ADD COLUMN last_operation_at DATETIME;

-- 1.8 风险等级（0-5）
ALTER TABLE douyin_accounts ADD COLUMN risk_level INTEGER DEFAULT 0;

-- 1.9 浏览器进程PID
ALTER TABLE douyin_accounts ADD COLUMN browser_pid INTEGER DEFAULT 0;

-- 1.10 浏览器启动时间
ALTER TABLE douyin_accounts ADD COLUMN last_browser_start DATETIME;

-- ========================================
-- 2. 升级 task_executions 表（如果存在）
-- ========================================

-- 2.1 IP地址字段
-- ALTER TABLE task_executions ADD COLUMN ip_address VARCHAR(50);

-- 2.2 设备指纹字段
-- ALTER TABLE task_executions ADD COLUMN device_fp VARCHAR(64);

-- 注：如果task_executions表不存在，忽略上述两条

-- ========================================
-- 3. 创建二维码登录表
-- ========================================

CREATE TABLE IF NOT EXISTS douyin_qrcodes (
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
);

CREATE INDEX IF NOT EXISTS idx_qrcodes_token ON douyin_qrcodes(token);
CREATE INDEX IF NOT EXISTS idx_qrcodes_status ON douyin_qrcodes(status);
CREATE INDEX IF NOT EXISTS idx_qrcodes_admin ON douyin_qrcodes(admin_id);

-- ========================================
-- 4. 创建公告表
-- ========================================

CREATE TABLE IF NOT EXISTS announcements (
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
);

CREATE INDEX IF NOT EXISTS idx_announcements_status ON announcements(status);
CREATE INDEX IF NOT EXISTS idx_announcements_type ON announcements(type);

-- ========================================
-- 升级完成！
-- ========================================

-- 验证升级：
-- SELECT * FROM douyin_accounts LIMIT 1;
-- SELECT * FROM douyin_qrcodes LIMIT 1;
-- SELECT * FROM announcements LIMIT 1;

-- 如有字段已存在的错误，说明已经升级过，可以忽略
