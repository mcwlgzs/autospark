-- ========================================
-- 抖音续火花系统 - 数据库初始化脚本
-- 数据库版本: MySQL 8.0+
-- 创建时间: 2026-06-02
-- ========================================

-- 1. 管理员表
DROP TABLE IF EXISTS `admins`;
CREATE TABLE `admins` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '管理员ID',
    `username` VARCHAR(50) NOT NULL COMMENT '用户名',
    `password` VARCHAR(255) NOT NULL COMMENT '密码（加密存储）',
    `nickname` VARCHAR(100) DEFAULT NULL COMMENT '昵称',
    `email` VARCHAR(100) DEFAULT NULL COMMENT '邮箱',
    `phone` VARCHAR(20) DEFAULT NULL COMMENT '手机号',
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '状态：0-禁用，1-正常',
    `last_login_at` DATETIME DEFAULT NULL COMMENT '最后登录时间',
    `last_login_ip` VARCHAR(50) DEFAULT NULL COMMENT '最后登录IP',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_username` (`username`),
    KEY `idx_status` (`status`),
    KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='管理员表';

-- 2. 用户表
DROP TABLE IF EXISTS `users`;
CREATE TABLE `users` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '用户ID',
    `username` VARCHAR(50) NOT NULL COMMENT '用户名（登录账号）',
    `email` VARCHAR(100) DEFAULT NULL COMMENT '邮箱',
    `phone` VARCHAR(20) DEFAULT NULL COMMENT '手机号（可后期绑定）',
    `password` VARCHAR(255) NOT NULL COMMENT '密码',
    `avatar` VARCHAR(500) DEFAULT NULL COMMENT '头像URL',
    `wechat` VARCHAR(100) DEFAULT NULL COMMENT '微信号',
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '状态：1-正常，其余是禁用',
    `balance` DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '账户余额',
    `total_spent` DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '累计消费金额',
    `agent_level` INT NOT NULL DEFAULT 1 COMMENT '代理等级：1-普通用户，2-普通代理，3-高级代理',
    `last_login_at` DATETIME DEFAULT NULL COMMENT '最后登录时间',
    `last_login_ip` VARCHAR(50) DEFAULT NULL COMMENT '最后登录IP',
    `remark` TEXT DEFAULT NULL COMMENT '备注',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_username` (`username`),
    UNIQUE KEY `uk_email` (`email`),
    KEY `idx_phone` (`phone`),
    KEY `idx_status` (`status`),
    KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户表';

-- 3. 抖音账号表
DROP TABLE IF EXISTS `douyin_accounts`;
CREATE TABLE `douyin_accounts` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '账号ID',
    `user_id` BIGINT UNSIGNED NOT NULL COMMENT '所属用户ID',
    `nickname` VARCHAR(100) DEFAULT NULL COMMENT '抖音昵称',
    `uid` VARCHAR(64) DEFAULT NULL COMMENT '抖音uid',
    `sec_uid` VARCHAR(128) DEFAULT NULL COMMENT '抖音sec_uid',
    `unique_id` VARCHAR(64) DEFAULT NULL COMMENT '抖音unique_id',
    `avatar` VARCHAR(500) DEFAULT NULL COMMENT '抖音头像',
    `cookie_data` TEXT DEFAULT NULL COMMENT '完整Cookie数据',
    `session_id` VARCHAR(256) DEFAULT NULL COMMENT 'sessionid',
    `sid_guard` VARCHAR(256) DEFAULT NULL COMMENT 'sid_guard',
    `cookie_status` TINYINT NOT NULL DEFAULT 0 COMMENT 'Cookie状态：0-无效，1-有效，2-待刷新',
    `cookie_expire` DATETIME DEFAULT NULL COMMENT 'Cookie过期时间',
    `cookie_remark` TEXT DEFAULT NULL COMMENT 'Cookie备注',
    `is_default` TINYINT NOT NULL DEFAULT 0 COMMENT '是否默认账号：0-否，1-是',
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '状态：0-禁用，1-正常',
    `remark` TEXT DEFAULT NULL COMMENT '账号备注',
    `last_login_at` DATETIME DEFAULT NULL COMMENT '最后登录时间',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    KEY `idx_user_id` (`user_id`),
    KEY `idx_cookie_status` (`cookie_status`),
    KEY `idx_status` (`status`),
    KEY `idx_is_default` (`is_default`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='抖音账号表';

-- 4. 抖音好友表
DROP TABLE IF EXISTS `douyin_friends`;
CREATE TABLE `douyin_friends` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '记录ID',
    `douyin_id` BIGINT UNSIGNED NOT NULL COMMENT '抖音账号ID',
    `uid` VARCHAR(64) NOT NULL COMMENT '好友的抖音uid',
    `sec_uid` VARCHAR(128) NOT NULL COMMENT '好友的抖音sec_uid',
    `unique_id` VARCHAR(64) DEFAULT NULL COMMENT '好友的抖音号',
    `nickname` VARCHAR(100) DEFAULT NULL COMMENT '好友昵称',
    `remark_name` VARCHAR(100) DEFAULT NULL COMMENT '好友备注名',
    `avatar` VARCHAR(500) DEFAULT NULL COMMENT '好友头像',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_douyin_uid` (`douyin_id`, `uid`),
    KEY `idx_douyin_id` (`douyin_id`),
    KEY `idx_uid` (`uid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='抖音好友表';

-- 5. 抖音扫码二维码表
DROP TABLE IF EXISTS `douyin_qrcodes`;
CREATE TABLE `douyin_qrcodes` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '记录ID',
    `token` VARCHAR(128) NOT NULL COMMENT '本地token',
    `fp` VARCHAR(64) DEFAULT NULL COMMENT '设备指纹',
    `user_id` BIGINT UNSIGNED NOT NULL COMMENT '请求用户ID',
    `qrcode` TEXT DEFAULT NULL COMMENT 'Base64二维码图片',
    `qrcode_url` VARCHAR(512) DEFAULT NULL COMMENT '二维码URL',
    `status` TINYINT NOT NULL DEFAULT 0 COMMENT '状态：0-待扫码，1-已扫码，2-已确认，3-已过期',
    `expires_at` DATETIME NOT NULL COMMENT '过期时间',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_token` (`token`),
    KEY `idx_user_id` (`user_id`),
    KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='抖音扫码二维码表';

-- 6. 套餐表
DROP TABLE IF EXISTS `packages`;
CREATE TABLE `packages` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '套餐ID',
    `name` VARCHAR(100) NOT NULL COMMENT '套餐名称',
    `description` TEXT DEFAULT NULL COMMENT '套餐描述',
    `duration_days` INT NOT NULL COMMENT '套餐天数',
    `original_price` DECIMAL(10,2) NOT NULL COMMENT '原价',
    `price` DECIMAL(10,2) NOT NULL COMMENT '现价',
    `daily_limit` INT NOT NULL DEFAULT -1 COMMENT '每日任务数量限制，-1表示不限制',
    `douyin_account_limit` INT NOT NULL DEFAULT 1 COMMENT '抖音账号数量限制，-1表示不限制',
    `scheduled_task_limit` INT NOT NULL DEFAULT 1 COMMENT '定时任务数量限制，-1表示不限制',
    `used_count` INT NOT NULL DEFAULT 0 COMMENT '已售出数量',
    `stock` INT DEFAULT NULL COMMENT '库存，-1表示无限库存',
    `sort_order` INT NOT NULL DEFAULT 0 COMMENT '排序（越大越靠前）',
    `is_recommended` TINYINT NOT NULL DEFAULT 0 COMMENT '是否推荐：0-否，1-是',
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '状态：0-下架，1-上架',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    KEY `idx_status` (`status`),
    KEY `idx_price` (`price`),
    KEY `idx_sort_order` (`sort_order`),
    KEY `idx_is_recommended` (`is_recommended`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='套餐表';

-- 7. 订单表
DROP TABLE IF EXISTS `orders`;
CREATE TABLE `orders` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '订单ID',
    `order_no` VARCHAR(64) NOT NULL COMMENT '订单号（唯一）',
    `user_id` BIGINT UNSIGNED NOT NULL COMMENT '用户ID',
    `package_id` BIGINT UNSIGNED NOT NULL COMMENT '套餐ID',
    `package_name` VARCHAR(100) DEFAULT '' COMMENT '套餐名称',
    `pay_amount` DECIMAL(10,2) NOT NULL COMMENT '实际支付金额',
    `pay_method` VARCHAR(20) DEFAULT 'balance' COMMENT '支付方式：balance/wechat/alipay',
    `is_recharge` BOOLEAN NOT NULL DEFAULT FALSE COMMENT '是否为余额充值订单：0-否，1-是',
    `status` TINYINT NOT NULL DEFAULT 0 COMMENT '订单状态：0-待支付，1-已支付，2-已完成，3-已取消，4-已退款',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_order_no` (`order_no`),
    KEY `idx_user_id` (`user_id`),
    KEY `idx_package_id` (`package_id`),
    KEY `idx_status` (`status`),
    KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='订单表';

-- 7.1 卡密表
DROP TABLE IF EXISTS `card_keys`;
CREATE TABLE `card_keys` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '卡密ID',
    `card_key` VARCHAR(64) NOT NULL COMMENT '卡密',
    `package_id` BIGINT UNSIGNED NOT NULL COMMENT '套餐ID',
    `package_name` VARCHAR(100) DEFAULT '' COMMENT '套餐名称（冗余）',
    `duration_days` INT DEFAULT 0 COMMENT '套餐天数（冗余，方便查询）',
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '状态：0-已封禁，1-未使用，2-已使用',
    `used_user_id` BIGINT UNSIGNED DEFAULT NULL COMMENT '使用用户ID',
    `used_order_id` BIGINT UNSIGNED DEFAULT NULL COMMENT '使用订单ID',
    `used_at` DATETIME DEFAULT NULL COMMENT '使用时间',
    `batch_no` VARCHAR(32) DEFAULT NULL COMMENT '批次号（批量生成时使用）',
    `created_by` BIGINT UNSIGNED DEFAULT NULL COMMENT '创建管理员ID',
    `purchased_by` BIGINT UNSIGNED DEFAULT NULL COMMENT '购买用户ID（代理批卡）',
    `purchase_price` DECIMAL(10,2) DEFAULT NULL COMMENT '购买单价（代理批卡）',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_card_key` (`card_key`),
    KEY `idx_package_id` (`package_id`),
    KEY `idx_status` (`status`),
    KEY `idx_batch_no` (`batch_no`),
    KEY `idx_used_user_id` (`used_user_id`),
    KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='卡密表';

-- 7.2 代理等级配置表
DROP TABLE IF EXISTS `agent_levels`;
CREATE TABLE `agent_levels` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '等级ID',
    `level` INT NOT NULL COMMENT '等级值：1-普通用户，2-普通代理，3-高级代理',
    `name` VARCHAR(50) NOT NULL COMMENT '等级名称（可自定义）',
    `price_rate` DECIMAL(5,2) NOT NULL DEFAULT 100.00 COMMENT '卡密购买价格比例(%)：70代表七折',
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '状态：0-禁用，1-启用',
    `remark` VARCHAR(255) DEFAULT '' COMMENT '备注',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_level` (`level`),
    KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='代理等级配置表';

-- 7.3 代理等级初始数据
INSERT INTO `agent_levels` (`level`, `name`, `price_rate`, `status`, `remark`) VALUES
(1, '普通用户', 100.00, 1, '默认等级，无批卡权限'),
(2, '普通代理', 90.00, 1, '享受九折批卡价格'),
(3, '高级代理', 70.00, 1, '享受七折批卡价格');

-- 8. 用户套餐关系表
DROP TABLE IF EXISTS `user_packages`;
CREATE TABLE `user_packages` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '记录ID',
    `user_id` BIGINT UNSIGNED NOT NULL COMMENT '用户ID',
    `package_id` BIGINT UNSIGNED NOT NULL COMMENT '套餐ID',
    `order_id` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '订单ID',
    `end_time` DATETIME NOT NULL COMMENT '套餐结束时间',
    `total_uses` INT NOT NULL DEFAULT 0 COMMENT '已使用次数',
    `daily_uses` INT NOT NULL DEFAULT 0 COMMENT '今日使用次数',
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '状态：0-未激活，1-使用中，2-已过期，3-已用完',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    KEY `idx_user_id` (`user_id`),
    KEY `idx_package_id` (`package_id`),
    KEY `idx_status` (`status`),
    KEY `idx_end_time` (`end_time`),
    UNIQUE KEY `uk_user_id` (`user_id`) COMMENT '一个用户只能有一个套餐记录'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户套餐关系表';

-- 9. 抖音任务表
DROP TABLE IF EXISTS `douyin_tasks`;
CREATE TABLE `douyin_tasks` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '任务ID',
    `douyin_id` BIGINT UNSIGNED NOT NULL COMMENT '抖音账号ID（关联douyin_accounts）',
    `user_id` BIGINT UNSIGNED NOT NULL COMMENT '用户ID',
    `scheduled_task_id` BIGINT UNSIGNED DEFAULT NULL COMMENT '定时任务ID（NULL表示手动测试任务）',
    `task_type` VARCHAR(32) NOT NULL COMMENT '任务类型：send_message-发送消息, refresh_friends-刷新好友, get_messages-获取私信',
    `target_uid` VARCHAR(64) DEFAULT NULL COMMENT '目标用户UID（发送消息时使用）',
    `target_nickname` VARCHAR(100) DEFAULT NULL COMMENT '目标用户昵称',
    `target_remark_name` VARCHAR(100) DEFAULT NULL COMMENT '目标用户备注名',
    `content` TEXT DEFAULT NULL COMMENT '消息内容',
    `status` TINYINT NOT NULL DEFAULT 0 COMMENT '状态：0-等待中，1-执行中，2-已完成，3-失败',
    `result` TINYINT DEFAULT NULL COMMENT '结果：0-失败，1-成功',
    `error_message` TEXT DEFAULT NULL COMMENT '错误信息',
    `ip_address` VARCHAR(50) DEFAULT NULL COMMENT '操作IP',
    `browser_pid` INT NOT NULL DEFAULT 0 COMMENT '浏览器主进程PID',
    `retry_count` INT NOT NULL DEFAULT 0 COMMENT '重试次数',
    `started_at` DATETIME DEFAULT NULL COMMENT '开始执行时间',
    `finished_at` DATETIME DEFAULT NULL COMMENT '完成时间',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    KEY `idx_douyin_id` (`douyin_id`),
    KEY `idx_user_id` (`user_id`),
    KEY `idx_scheduled_task_id` (`scheduled_task_id`),
    KEY `idx_task_type` (`task_type`),
    KEY `idx_status` (`status`),
    KEY `idx_created_at` (`created_at`),
    KEY `idx_douyin_status` (`douyin_id`, `status`),
    KEY `idx_douyin_created` (`douyin_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='抖音任务表';

-- 10. 用户日志表
DROP TABLE IF EXISTS `user_logs`;
CREATE TABLE `user_logs` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '日志ID',
    `user_id` BIGINT UNSIGNED DEFAULT NULL COMMENT '用户ID',
    `admin_id` BIGINT UNSIGNED DEFAULT NULL COMMENT '管理员ID',
    `log_type` VARCHAR(32) NOT NULL COMMENT '日志类型',
    `action` VARCHAR(100) DEFAULT NULL COMMENT '操作动作',
    `content` TEXT DEFAULT NULL COMMENT '日志内容',
    `before_value` VARCHAR(255) DEFAULT NULL COMMENT '变更前值',
    `after_value` VARCHAR(255) DEFAULT NULL COMMENT '变更后值',
    `amount` DECIMAL(10,2) DEFAULT NULL COMMENT '变更金额',
    `ip_address` VARCHAR(50) DEFAULT NULL COMMENT 'IP地址',
    `user_agent` VARCHAR(500) DEFAULT NULL COMMENT '浏览器信息',
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '状态：0-失败，1-成功',
    `error_msg` VARCHAR(255) DEFAULT NULL COMMENT '错误信息',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    KEY `idx_user_id` (`user_id`),
    KEY `idx_admin_id` (`admin_id`),
    KEY `idx_log_type` (`log_type`),
    KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户日志表';

-- 11. 定时任务表
DROP TABLE IF EXISTS `scheduled_tasks`;
CREATE TABLE `scheduled_tasks` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '任务ID',
    `name` VARCHAR(100) DEFAULT NULL COMMENT '任务名称',
    `douyin_id` BIGINT UNSIGNED NOT NULL COMMENT '抖音账号ID',
    `user_id` BIGINT UNSIGNED NOT NULL COMMENT '用户ID',
    `task_type` VARCHAR(20) NOT NULL DEFAULT 'cron' COMMENT '任务类型：cron-固定时间，interval-间隔执行',
    `cron_expression` VARCHAR(64) DEFAULT NULL COMMENT 'Cron表达式',
    `interval_value` INT DEFAULT NULL COMMENT '间隔值',
    `interval_unit` VARCHAR(20) DEFAULT NULL COMMENT '间隔单位：minute/hour',
    `target_uid` VARCHAR(64) DEFAULT NULL COMMENT '目标用户UID',
    `target_nickname` VARCHAR(100) DEFAULT NULL COMMENT '目标用户昵称',
    `target_remark_name` VARCHAR(100) DEFAULT NULL COMMENT '目标用户备注',
    `message_content` TEXT DEFAULT NULL COMMENT '消息内容',
    `message_type` VARCHAR(20) NOT NULL DEFAULT 'fixed' COMMENT '消息类型：fixed-固定内容，ai-AI生成',
    `ai_style` VARCHAR(500) DEFAULT '' COMMENT 'AI提示词（用户自定义）',
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '状态：0-禁用，1-启用',
    `total_runs` INT NOT NULL DEFAULT 0 COMMENT '累计执行次数',
    `last_run_time` DATETIME DEFAULT NULL COMMENT '上次执行时间',
    `last_result` TINYINT DEFAULT NULL COMMENT '上次执行结果：0-失败，1-成功',
    `last_error_message` TEXT DEFAULT NULL COMMENT '上次执行错误信息',
    `next_run_time` DATETIME DEFAULT NULL COMMENT '下次执行时间',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    KEY `idx_douyin_id` (`douyin_id`),
    KEY `idx_user_id` (`user_id`),
    KEY `idx_status` (`status`),
    KEY `idx_next_run_time` (`next_run_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='定时任务表';

-- 12. 系统配置表
DROP TABLE IF EXISTS `system_configs`;
CREATE TABLE `system_configs` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '配置ID',
    `group_key` VARCHAR(50) NOT NULL COMMENT '配置分组键',
    `config_key` VARCHAR(50) NOT NULL COMMENT '配置键',
    `config_value` TEXT DEFAULT NULL COMMENT '配置值',
    `config_type` VARCHAR(20) NOT NULL DEFAULT 'string' COMMENT '配置类型：string/number/boolean/json',
    `config_name` VARCHAR(100) DEFAULT NULL COMMENT '配置名称',
    `config_desc` VARCHAR(255) DEFAULT NULL COMMENT '配置描述',
    `sort_order` INT NOT NULL DEFAULT 0 COMMENT '排序',
    `is_system` TINYINT NOT NULL DEFAULT 0 COMMENT '是否系统配置：0-否，1-是',
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '状态：0-禁用，1-启用',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_group_key` (`group_key`, `config_key`),
    KEY `idx_group_key` (`group_key`),
    KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='系统配置表';

-- 13. 公告表
DROP TABLE IF EXISTS `announcements`;
CREATE TABLE `announcements` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '公告ID',
    `type` VARCHAR(20) NOT NULL COMMENT '公告类型：site-网站首页弹窗，user-用户首页弹窗',
    `title` VARCHAR(200) NOT NULL COMMENT '公告标题',
    `content` TEXT NOT NULL COMMENT '公告内容',
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '状态：0-禁用，1-启用',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    KEY `idx_type` (`type`),
    KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='公告表';

-- 初始化系统配置数据
INSERT INTO `system_configs` (`group_key`, `config_key`, `config_value`, `config_type`, `config_name`, `config_desc`, `sort_order`, `is_system`, `status`) VALUES
-- 基础配置
('basic', 'site_name', '抖音续火花系统', 'string', '网站名称', '系统显示名称', 1, 1, 1),
('basic', 'site_logo', '/static/logo.png', 'string', '系统Logo', '系统Logo路径', 2, 1, 1),
('basic', 'site_favicon', '/static/favicon.ico', 'string', '网站Favicon', '网站Favicon图标路径', 3, 1, 1),
('basic', 'icp_number', '', 'string', 'ICP备案号', '网站ICP备案号', 4, 1, 1),
('basic', 'gaba_number', '', 'string', '公安备案号', '网站公安联网备案号', 5, 1, 1),
('basic', 'service_phone', '', 'string', '客服电话', '客服联系电话', 6, 1, 1),
('basic', 'service_email', '', 'string', '客服邮箱', '客服联系邮箱', 7, 1, 1),
('basic', 'service_qq', '', 'string', '客服QQ', '客服联系QQ', 8, 1, 1),
('basic', 'service_wechat', '', 'string', '客服微信', '客服联系微信', 9, 1, 1),

-- 邮箱配置
('email', 'register_verify', 'true', 'boolean', '注册验证码', '注册时是否需要邮箱验证', 1, 1, 1),
('email', 'smtp_host', '', 'string', 'SMTP服务器', '邮箱SMTP服务器地址', 2, 1, 1),
('email', 'smtp_port', '465', 'number', 'SMTP端口', '邮箱SMTP端口', 3, 1, 1),
('email', 'smtp_username', '', 'string', 'SMTP账号', '邮箱用户名/邮箱地址', 4, 1, 1),
('email', 'smtp_password', '', 'string', 'SMTP密码', '邮箱密码或授权码', 5, 1, 1),
('email', 'from_email', '', 'string', '发件人邮箱', '发送邮件使用的邮箱地址', 6, 1, 1),
('email', 'from_name', '抖音续火花系统', 'string', '发件人名称', '发送邮件时显示的发件人名称', 7, 1, 1),

-- 系统配置
('system', 'site_url', '', 'string', '站点地址', '网站访问地址，用于支付回调等场景，如 https://www.example.com', 0, 1, 1),
('system', 'register_gift_package', '', 'string', '注册赠送套餐', '新用户注册时自动赠送的套餐ID，为空则不赠送', 1, 1, 1),
('system', 'message_send_mode', 'browser', 'string', '消息发送方式', 'browser-浏览器自动化（默认，稳定）；protocol-协议发送（试点，完全按协议发送，不启动浏览器，失败不回退）', 2, 1, 1),

-- 支付配置
('payment', 'pay_url', '', 'string', '支付平台地址', '易支付平台地址，如 https://pay.example.com', 1, 1, 1),
('payment', 'pay_pid', '', 'string', '商户ID', '支付平台分配的商户ID（pid）', 2, 1, 1),
('payment', 'pay_key', '', 'string', '商户密钥', '支付平台的商户密钥（key）', 3, 1, 1),
('payment', 'pay_enabled_wechat', 'false', 'boolean', '启用微信支付', '是否启用微信支付', 4, 1, 1),
('payment', 'pay_enabled_alipay', 'true', 'boolean', '启用支付宝', '是否启用支付宝', 5, 1, 1),
('payment', 'pay_enabled_qq', 'false', 'boolean', '启用QQ支付', '是否启用QQ支付', 6, 1, 1),
('payment', 'pay_enabled_usdt', 'false', 'boolean', '启用USDT支付', '是否启用USDT(TRC20)支付', 7, 1, 1),

-- AI配置
('ai', 'ai_enabled', 'false', 'boolean', '启用AI功能', '是否启用AI生成消息功能', 1, 1, 1),
('ai', 'ai_model', 'gpt-3.5-turbo', 'string', 'AI模型', 'AI模型名称，如 gpt-3.5-turbo、gpt-4', 2, 1, 1),
('ai', 'ai_api_key', '', 'string', 'AI API密钥', '第三方AI服务API密钥', 3, 1, 1),
('ai', 'ai_api_base', 'https://api.openai.com/v1', 'string', 'AI API地址', 'AI服务API地址（可配置代理）', 4, 1, 1),
('ai', 'ai_temperature', '0.8', 'string', 'AI温度参数', 'AI生成内容的随机性，0-2之间，越高越随机', 5, 1, 1),
('ai', 'ai_max_tokens', '1000', 'string', 'AI最大token数', 'AI生成内容的最大token数', 6, 1, 1),
('ai', 'ai_default_prompt', '对方是我的抖音好友，请生成一句简短的续火花消息', 'string', '默认AI提示词', 'AI生成消息的默认提示词', 7, 1, 1);


-- 为经常查询的字段创建复合索引
CREATE INDEX idx_user_packages_active ON user_packages(user_id, status, end_time);
CREATE INDEX idx_scheduled_tasks_next_run ON scheduled_tasks(next_run_time, status);

-- 初始化管理员账号
INSERT INTO `admins` (`username`, `password`, `nickname`, `email`, `status`, `created_at`, `updated_at`) VALUES
('admin', '$2a$12$w/mxkDyltpWmZC4vFd7jseTRyT106BhgBh0.8o9w.AltnfTe6buY6', '超级管理员', 'admin@admin.com', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

