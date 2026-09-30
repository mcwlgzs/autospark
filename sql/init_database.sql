-- ============================================
-- AutoSpark v3.0 - 完整数据库初始化脚本
-- 用于全新安装（不是升级）
-- ============================================

-- 系统配置表
CREATE TABLE IF NOT EXISTS `system_config` (
    `id` INTEGER PRIMARY KEY AUTOINCREMENT,
    `config_key` VARCHAR(100) NOT NULL UNIQUE,
    `config_value` TEXT,
    `description` VARCHAR(255),
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 用户表
CREATE TABLE IF NOT EXISTS `users` (
    `id` INTEGER PRIMARY KEY AUTOINCREMENT,
    `username` VARCHAR(50) NOT NULL UNIQUE,
    `password` VARCHAR(255) NOT NULL,
    `email` VARCHAR(100),
    `status` INTEGER NOT NULL DEFAULT 1,
    `is_vip` INTEGER NOT NULL DEFAULT 0,
    `vip_activated_at` DATETIME,
    `douyin_account_limit` INTEGER NOT NULL DEFAULT 1,
    `scheduled_task_limit` INTEGER NOT NULL DEFAULT 1,
    `last_login_at` DATETIME,
    `last_login_ip` VARCHAR(50),
    `remark` TEXT,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 卡密表
CREATE TABLE IF NOT EXISTS `card_keys` (
    `id` INTEGER PRIMARY KEY AUTOINCREMENT,
    `card_key` VARCHAR(64) NOT NULL UNIQUE,
    `card_type` VARCHAR(20) NOT NULL DEFAULT 'vip',
    `status` INTEGER NOT NULL DEFAULT 1,
    `douyin_account_limit` INTEGER NOT NULL DEFAULT -1,
    `scheduled_task_limit` INTEGER NOT NULL DEFAULT -1,
    `batch_id` VARCHAR(50),
    `created_by` INTEGER,
    `used_user_id` INTEGER,
    `used_at` DATETIME,
    `remark` TEXT,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (`created_by`) REFERENCES `users`(`id`),
    FOREIGN KEY (`used_user_id`) REFERENCES `users`(`id`)
);

-- 二维码登录表
CREATE TABLE IF NOT EXISTS `douyin_qrcodes` (
    `id` INTEGER PRIMARY KEY AUTOINCREMENT,
    `user_id` INTEGER NOT NULL,
    `token` VARCHAR(64) NOT NULL UNIQUE,
    `qrcode_url` TEXT,
    `status` INTEGER NOT NULL DEFAULT 0,
    `cookie_data` TEXT,
    `expires_at` DATETIME,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`)
);

-- 公告表
CREATE TABLE IF NOT EXISTS `announcements` (
    `id` INTEGER PRIMARY KEY AUTOINCREMENT,
    `title` VARCHAR(200) NOT NULL,
    `content` TEXT NOT NULL,
    `type` VARCHAR(20) NOT NULL DEFAULT 'site',
    `enabled` INTEGER NOT NULL DEFAULT 1,
    `sort_order` INTEGER NOT NULL DEFAULT 0,
    `created_by` INTEGER,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (`created_by`) REFERENCES `users`(`id`)
);

-- 抖音账号表
CREATE TABLE IF NOT EXISTS `douyin_accounts` (
    `id` INTEGER PRIMARY KEY AUTOINCREMENT,
    `user_id` INTEGER NOT NULL DEFAULT 1,
    `account_name` VARCHAR(100),
    `douyin_uid` VARCHAR(50),
    `cookie_data` TEXT NOT NULL,
    `status` INTEGER NOT NULL DEFAULT 1,
    `send_mode` VARCHAR(20) DEFAULT 'browser',
    `browser_pid` INTEGER DEFAULT 0,
    `last_browser_start` DATETIME,
    `last_used_at` DATETIME,
    `remark` TEXT,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`)
);

-- 定时任务表
CREATE TABLE IF NOT EXISTS `scheduled_tasks` (
    `id` INTEGER PRIMARY KEY AUTOINCREMENT,
    `user_id` INTEGER NOT NULL DEFAULT 1,
    `account_id` INTEGER NOT NULL,
    `task_name` VARCHAR(100),
    `schedule_time` VARCHAR(50),
    `message_content` TEXT,
    `target_list` TEXT,
    `status` INTEGER NOT NULL DEFAULT 1,
    `last_run_at` DATETIME,
    `next_run_at` DATETIME,
    `run_count` INTEGER DEFAULT 0,
    `success_count` INTEGER DEFAULT 0,
    `remark` TEXT,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`),
    FOREIGN KEY (`account_id`) REFERENCES `douyin_accounts`(`id`)
);

-- 消息发送记录表
CREATE TABLE IF NOT EXISTS `message_logs` (
    `id` INTEGER PRIMARY KEY AUTOINCREMENT,
    `user_id` INTEGER NOT NULL DEFAULT 1,
    `account_id` INTEGER NOT NULL,
    `task_id` INTEGER,
    `to_uid` VARCHAR(50),
    `to_name` VARCHAR(100),
    `content` TEXT,
    `send_mode` VARCHAR(20),
    `status` INTEGER NOT NULL DEFAULT 0,
    `error_msg` TEXT,
    `duration_ms` INTEGER,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`),
    FOREIGN KEY (`account_id`) REFERENCES `douyin_accounts`(`id`),
    FOREIGN KEY (`task_id`) REFERENCES `scheduled_tasks`(`id`)
);

-- 系统日志表
CREATE TABLE IF NOT EXISTS `system_logs` (
    `id` INTEGER PRIMARY KEY AUTOINCREMENT,
    `user_id` INTEGER,
    `level` VARCHAR(20) NOT NULL,
    `category` VARCHAR(50),
    `message` TEXT NOT NULL,
    `details` TEXT,
    `ip_address` VARCHAR(50),
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (`user_id`) REFERENCES `users`(`id`)
);

-- ============================================
-- 索引
-- ============================================

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
CREATE INDEX IF NOT EXISTS idx_card_keys_status ON card_keys(status);
CREATE INDEX IF NOT EXISTS idx_card_keys_batch ON card_keys(batch_id);
CREATE INDEX IF NOT EXISTS idx_qrcodes_token ON douyin_qrcodes(token);
CREATE INDEX IF NOT EXISTS idx_qrcodes_status ON douyin_qrcodes(status);
CREATE INDEX IF NOT EXISTS idx_announcements_type ON announcements(type, enabled);
CREATE INDEX IF NOT EXISTS idx_accounts_user ON douyin_accounts(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_user ON scheduled_tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON scheduled_tasks(status);
CREATE INDEX IF NOT EXISTS idx_message_logs_user ON message_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_message_logs_created ON message_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_system_logs_user ON system_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_system_logs_level ON system_logs(level);

-- ============================================
-- 视图
-- ============================================

-- 用户统计视图
CREATE VIEW IF NOT EXISTS v_user_stats AS
SELECT
    u.id,
    u.username,
    u.is_vip,
    COUNT(DISTINCT da.id) as account_count,
    COUNT(DISTINCT st.id) as task_count,
    COUNT(DISTINCT CASE WHEN ml.created_at >= date('now') THEN ml.id END) as today_messages,
    MAX(u.last_login_at) as last_login_at
FROM users u
LEFT JOIN douyin_accounts da ON u.id = da.user_id
LEFT JOIN scheduled_tasks st ON u.id = st.user_id
LEFT JOIN message_logs ml ON u.id = ml.user_id
GROUP BY u.id;

-- 卡密统计视图
CREATE VIEW IF NOT EXISTS v_card_key_stats AS
SELECT
    batch_id,
    COUNT(*) as total_count,
    SUM(CASE WHEN status = 1 THEN 1 ELSE 0 END) as unused_count,
    SUM(CASE WHEN status = 2 THEN 1 ELSE 0 END) as used_count,
    SUM(CASE WHEN status = 0 THEN 1 ELSE 0 END) as banned_count,
    MIN(created_at) as batch_created_at
FROM card_keys
GROUP BY batch_id;

-- ============================================
-- 初始数据
-- ============================================

-- 插入系统配置
INSERT INTO system_config (config_key, config_value, description) VALUES
    ('system_version', '3.0.0', '系统版本号'),
    ('message_send_mode', 'browser', '默认消息发送模式: browser/protocol'),
    ('allow_fallback', '1', '是否允许发送模式自动回退: 0/1'),
    ('max_retry_times', '3', '消息发送最大重试次数'),
    ('browser_cleanup_interval', '300', '浏览器清理间隔（秒）'),
    ('qrcode_expire_minutes', '5', '二维码过期时间（分钟）'),
    ('jwt_expire_hours', '72', 'JWT Token过期时间（小时）'),
    ('default_account_limit', '1', '新用户默认账号数限制'),
    ('default_task_limit', '1', '新用户默认任务数限制'),
    ('enable_registration', '1', '是否开放注册: 0/1');

-- 创建默认管理员账号（密码: admin123）
-- bcrypt hash of 'admin123'
INSERT INTO users (
    username,
    password,
    status,
    is_vip,
    douyin_account_limit,
    scheduled_task_limit,
    vip_activated_at,
    remark
) VALUES (
    'admin',
    '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5idgdJ7P.VRoG',
    1,
    1,
    -1,
    -1,
    CURRENT_TIMESTAMP,
    '系统默认管理员账号'
);

-- 插入示例公告
INSERT INTO announcements (title, content, type, enabled, sort_order, created_by) VALUES
    ('🎉 欢迎使用AutoSpark v3.0',
     '全新多用户系统已上线！支持卡密激活、二维码登录、双模式发送等功能。立即注册体验！',
     'site', 1, 1, 1),
    ('🔐 安全提醒',
     '请妥善保管您的账号密码和卡密，不要与他人分享。系统采用JWT认证和bcrypt加密，确保您的数据安全。',
     'site', 1, 2, 1),
    ('💡 使用提示',
     '新用户注册后默认为普通用户，可通过激活卡密成为永久VIP会员，享受无限制使用权限。',
     'user', 1, 1, 1);

-- ============================================
-- 完成
-- ============================================

-- 记录初始化完成
INSERT INTO system_logs (level, category, message, details) VALUES
    ('INFO', 'SYSTEM', 'Database initialized', 'AutoSpark v3.0 database initialization completed successfully');

SELECT '✅ 数据库初始化完成！' as result;
SELECT '默认管理员账号: admin / admin123' as info;
SELECT '⚠️  请登录后立即修改密码！' as warning;
