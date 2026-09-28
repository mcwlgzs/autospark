-- ========================================
-- 抖音续火花系统 - 数据库Schema（简化版）
-- 基于douyin_huohua参考，但简化为当前需求
-- 支持: SQLite（开发）/ MySQL 8.0+（生产）
-- ========================================

-- 1. 管理员表
CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    nickname VARCHAR(100),
    email VARCHAR(100),
    status TINYINT NOT NULL DEFAULT 1, -- 0:禁用 1:正常
    last_login_at DATETIME,
    last_login_ip VARCHAR(50),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_admins_username ON admins(username);
CREATE INDEX IF NOT EXISTS idx_admins_status ON admins(status);

-- 2. 抖音账号表
CREATE TABLE IF NOT EXISTS douyin_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER NOT NULL, -- 所属管理员
    nickname VARCHAR(100), -- 抖音昵称
    uid VARCHAR(64), -- 抖音uid
    sec_uid VARCHAR(128), -- 抖音sec_uid
    unique_id VARCHAR(64), -- 抖音号
    avatar VARCHAR(500), -- 头像URL
    cookie_data TEXT, -- 完整Cookie JSON
    session_id VARCHAR(256), -- sessionid
    sid_guard VARCHAR(256), -- sid_guard
    cookie_status TINYINT NOT NULL DEFAULT 0, -- 0:无效 1:有效 2:待刷新
    cookie_expire DATETIME, -- Cookie过期时间
    is_default TINYINT NOT NULL DEFAULT 0, -- 是否默认账号 0:否 1:是
    status TINYINT NOT NULL DEFAULT 1, -- 0:禁用 1:正常
    remark TEXT, -- 备注
    last_login_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (admin_id) REFERENCES admins(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_douyin_accounts_admin ON douyin_accounts(admin_id);
CREATE INDEX IF NOT EXISTS idx_douyin_accounts_default ON douyin_accounts(is_default);
CREATE INDEX IF NOT EXISTS idx_douyin_accounts_status ON douyin_accounts(status);

-- 3. 好友表
CREATE TABLE IF NOT EXISTS douyin_friends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    douyin_account_id INTEGER NOT NULL,
    uid VARCHAR(64) NOT NULL,
    sec_uid VARCHAR(128),
    unique_id VARCHAR(64),
    nickname VARCHAR(100),
    remark_name VARCHAR(100), -- 自定义备注
    avatar VARCHAR(500),
    group_name VARCHAR(50), -- 分组
    last_contact_at DATETIME, -- 最后联系时间
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (douyin_account_id) REFERENCES douyin_accounts(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_friends_account_uid ON douyin_friends(douyin_account_id, uid);
CREATE INDEX IF NOT EXISTS idx_friends_account ON douyin_friends(douyin_account_id);
CREATE INDEX IF NOT EXISTS idx_friends_group ON douyin_friends(group_name);

-- 4. 定时任务表
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(100), -- 任务名称（可选）
    douyin_account_id INTEGER NOT NULL, -- 关联账号
    admin_id INTEGER NOT NULL, -- 创建者
    target_uid VARCHAR(64), -- 目标好友uid
    target_nickname VARCHAR(100), -- 目标昵称
    target_remark_name VARCHAR(100), -- 目标备注

    -- 定时配置
    task_type VARCHAR(20) NOT NULL DEFAULT 'daily', -- daily:每日固定时间 interval:间隔执行
    send_time VARCHAR(10) NOT NULL DEFAULT '22:00', -- 发送时间 HH:MM
    jitter_minutes INTEGER DEFAULT 40, -- 随机延迟窗口（分钟）

    -- 消息内容
    message_type VARCHAR(20) NOT NULL DEFAULT 'fixed', -- fixed:固定 pool:消息池 ai:AI生成
    message_content TEXT, -- 固定消息内容（为空则从池中取）
    message_pool TEXT, -- 消息池（JSON数组）
    ai_prompt TEXT, -- AI提示词

    -- 特殊功能
    send_sign TINYINT DEFAULT 0, -- 是否发送灵签 0:否 1:是
    sign_type VARCHAR(20) DEFAULT 'wenchang', -- 灵签类型
    image_only TINYINT DEFAULT 0, -- 仅发图片 0:否 1:是

    -- 状态统计
    status TINYINT NOT NULL DEFAULT 1, -- 0:禁用 1:启用
    total_runs INTEGER NOT NULL DEFAULT 0, -- 累计执行次数
    success_runs INTEGER NOT NULL DEFAULT 0, -- 成功次数
    failed_runs INTEGER NOT NULL DEFAULT 0, -- 失败次数
    last_run_at DATETIME, -- 上次执行时间
    last_run_status VARCHAR(20), -- 上次执行状态
    last_error_message TEXT, -- 上次错误信息
    next_run_at DATETIME, -- 下次执行时间

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (douyin_account_id) REFERENCES douyin_accounts(id) ON DELETE CASCADE,
    FOREIGN KEY (admin_id) REFERENCES admins(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tasks_account ON scheduled_tasks(douyin_account_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON scheduled_tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_next_run ON scheduled_tasks(next_run_at, status);

-- 5. 任务执行历史表
CREATE TABLE IF NOT EXISTS task_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL, -- 关联任务
    douyin_account_id INTEGER NOT NULL,
    target_uid VARCHAR(64),
    target_nickname VARCHAR(100),

    -- 执行信息
    execution_type VARCHAR(20) NOT NULL, -- scheduled:定时 manual:手动 retry:重试
    message_content TEXT, -- 实际发送的内容
    message_type VARCHAR(20), -- 消息类型

    -- 结果
    status VARCHAR(20) NOT NULL, -- pending:等待 running:执行中 success:成功 failed:失败
    result TINYINT, -- 0:失败 1:成功
    error_type VARCHAR(50), -- 错误类型
    error_message TEXT, -- 错误详情
    retry_count INTEGER DEFAULT 0, -- 重试次数

    -- 时间
    started_at DATETIME,
    finished_at DATETIME,
    duration_seconds REAL, -- 执行耗时（秒）
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id) ON DELETE CASCADE,
    FOREIGN KEY (douyin_account_id) REFERENCES douyin_accounts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_executions_task ON task_executions(task_id);
CREATE INDEX IF NOT EXISTS idx_executions_status ON task_executions(status);
CREATE INDEX IF NOT EXISTS idx_executions_created ON task_executions(created_at);

-- 6. 发送历史去重表（继承原有设计）
CREATE TABLE IF NOT EXISTS send_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    douyin_account_id INTEGER NOT NULL,
    target_uid VARCHAR(64) NOT NULL,
    send_date DATE NOT NULL, -- 发送日期
    task_id INTEGER, -- 关联任务（可为空，手动发送时）
    execution_id INTEGER, -- 关联执行记录
    message_hash VARCHAR(64), -- 消息内容哈希（用于去重）
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (douyin_account_id) REFERENCES douyin_accounts(id) ON DELETE CASCADE,
    FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id) ON DELETE SET NULL,
    FOREIGN KEY (execution_id) REFERENCES task_executions(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_history_unique ON send_history(douyin_account_id, target_uid, send_date);
CREATE INDEX IF NOT EXISTS idx_history_date ON send_history(send_date);

-- 7. 系统配置表
CREATE TABLE IF NOT EXISTS system_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config_group VARCHAR(50) NOT NULL, -- 配置组: basic/notify/ai/system
    config_key VARCHAR(50) NOT NULL, -- 配置键
    config_value TEXT, -- 配置值
    config_type VARCHAR(20) NOT NULL DEFAULT 'string', -- string/number/boolean/json
    config_name VARCHAR(100), -- 显示名称
    config_desc VARCHAR(255), -- 描述
    is_sensitive TINYINT DEFAULT 0, -- 是否敏感信息（密码等）
    sort_order INTEGER DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_config_group_key ON system_configs(config_group, config_key);

-- 8. 操作日志表
CREATE TABLE IF NOT EXISTS operation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER,
    log_type VARCHAR(32) NOT NULL, -- login/task/send/config/account
    action VARCHAR(100), -- 操作动作
    content TEXT, -- 日志内容
    target_type VARCHAR(50), -- 目标类型
    target_id INTEGER, -- 目标ID
    ip_address VARCHAR(50),
    user_agent TEXT,
    status TINYINT NOT NULL DEFAULT 1, -- 0:失败 1:成功
    error_msg TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (admin_id) REFERENCES admins(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_logs_admin ON operation_logs(admin_id);
CREATE INDEX IF NOT EXISTS idx_logs_type ON operation_logs(log_type);
CREATE INDEX IF NOT EXISTS idx_logs_created ON operation_logs(created_at);

-- 9. 消息通知配置表
CREATE TABLE IF NOT EXISTS notify_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER NOT NULL,

    -- 推送配置
    push_enabled TINYINT DEFAULT 0,
    push_url VARCHAR(500),
    push_on_success TINYINT DEFAULT 0,
    push_on_failure TINYINT DEFAULT 1,

    -- 邮件配置
    email_enabled TINYINT DEFAULT 0,
    smtp_host VARCHAR(255),
    smtp_port INTEGER DEFAULT 465,
    smtp_username VARCHAR(255),
    smtp_password VARCHAR(255), -- 加密存储
    from_email VARCHAR(255),
    from_name VARCHAR(100),
    to_email VARCHAR(255),
    email_on_success TINYINT DEFAULT 0,
    email_on_failure TINYINT DEFAULT 1,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (admin_id) REFERENCES admins(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_notify_admin ON notify_configs(admin_id);

-- ========================================
-- 初始化数据
-- ========================================

-- 默认管理员（密码: admin123）
INSERT OR IGNORE INTO admins (id, username, password_hash, nickname, status)
VALUES (1, 'admin', '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5yvTtkO9PL5Sm', '系统管理员', 1);

-- 默认系统配置
INSERT OR IGNORE INTO system_configs (config_group, config_key, config_value, config_type, config_name, config_desc) VALUES
('basic', 'site_name', '抖音续火花系统', 'string', '系统名称', '系统显示名称'),
('basic', 'version', '1.2.0', 'string', '系统版本', '当前版本号'),
('system', 'default_jitter_minutes', '40', 'number', '默认随机延迟', '任务执行随机延迟窗口（分钟）'),
('system', 'default_send_time', '22:00', 'string', '默认发送时间', '新任务的默认发送时间'),
('system', 'max_retry_count', '3', 'number', '最大重试次数', '任务失败后最多重试次数'),
('system', 'cleanup_history_days', '90', 'number', '历史清理天数', '自动清理多少天前的执行历史'),
('ai', 'ai_enabled', 'false', 'boolean', '启用AI', '是否启用AI消息生成'),
('ai', 'ai_provider', 'openai', 'string', 'AI提供商', 'openai/azure/custom'),
('ai', 'ai_api_base', 'https://api.openai.com/v1', 'string', 'API地址', 'AI服务API地址'),
('ai', 'ai_api_key', '', 'string', 'API密钥', 'AI服务密钥', 1),
('ai', 'ai_model', 'gpt-3.5-turbo', 'string', 'AI模型', '使用的模型名称'),
('ai', 'ai_temperature', '0.8', 'number', '温度参数', '生成随机性 0-2'),
('ai', 'ai_default_prompt', '你是一个帮助维护朋友关系的助手。请生成一条简短、自然、温暖的问候消息，用于维持抖音好友关系。消息应该: 1. 简短(20字以内) 2. 自然随意 3. 不要太正式 4. 适合日常联系。只返回消息内容，不要解释。', 'string', '默认提示词', 'AI生成的默认提示词');

-- ========================================
-- 视图（可选，便于查询）
-- ========================================

-- 任务执行统计视图
CREATE VIEW IF NOT EXISTS v_task_statistics AS
SELECT
    t.id,
    t.name,
    t.target_nickname,
    t.status,
    t.total_runs,
    t.success_runs,
    t.failed_runs,
    ROUND(CAST(t.success_runs AS REAL) / NULLIF(t.total_runs, 0) * 100, 2) AS success_rate,
    t.last_run_at,
    t.next_run_at,
    a.nickname AS account_nickname
FROM scheduled_tasks t
LEFT JOIN douyin_accounts a ON t.douyin_account_id = a.id;

-- 今日任务执行概览
CREATE VIEW IF NOT EXISTS v_today_executions AS
SELECT
    DATE(created_at) AS date,
    status,
    COUNT(*) AS count,
    SUM(CASE WHEN result = 1 THEN 1 ELSE 0 END) AS success_count,
    SUM(CASE WHEN result = 0 THEN 1 ELSE 0 END) AS failed_count,
    AVG(duration_seconds) AS avg_duration
FROM task_executions
WHERE DATE(created_at) = DATE('now')
GROUP BY DATE(created_at), status;
