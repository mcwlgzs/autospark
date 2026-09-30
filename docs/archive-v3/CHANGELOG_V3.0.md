# AutoSpark v3.0 更新日志

## [3.0.0] - 2026-09-28

### 🎉 重大更新：多用户系统

AutoSpark v3.0 是一次重大架构升级，引入了完整的商业化多用户系统。

---

## 新增功能

### 1. 多用户系统 ⭐
- ✅ 用户注册/登录系统
- ✅ JWT身份认证
- ✅ 用户资料管理（头像、邮箱、微信等）
- ✅ 密码修改
- ✅ VIP永久会员体系
- ✅ 资源限制管理（账号数、任务数）
- ✅ 用户状态管理（正常/禁用）

### 2. 卡密激活系统 🎫
- ✅ 批量生成卡密（支持1-1000个）
- ✅ 卡密激活即永久VIP
- ✅ 批次管理和追踪
- ✅ 卡密状态（未使用/已使用/已封禁）
- ✅ 使用记录追踪
- ✅ 自定义资源限制（账号数、任务数）

### 3. 二维码登录 📱
- ✅ 生成抖音登录二维码
- ✅ 实时状态追踪
  - 待扫码
  - 已扫码
  - 已确认
  - 已过期
- ✅ Cookie自动获取
- ✅ 设备指纹追踪

### 4. 公告系统 📢
- ✅ 网站首页公告（site）
- ✅ 用户后台公告（user）
- ✅ 公告排序
- ✅ 启用/禁用控制
- ✅ 富文本内容支持

### 5. 双模式消息发送 🚀
- ✅ **Browser模式**（浏览器自动化）
  - 原有Selenium方式
  - 稳定性高
  - 兼容性好
  
- ✅ **Protocol模式**（实验性）
  - 直接调用抖音API
  - 无需启动浏览器
  - 发送速度快
  - 支持降级到Browser模式

### 6. 权限系统 🔐
- ✅ `@login_required` - 登录验证
- ✅ `@admin_required` - 管理员权限
- ✅ `@vip_required` - VIP权限
- ✅ `@check_resource_limit` - 资源限制检查

---

## API接口

### 用户接口（新增）

```
POST   /api/user/register          - 用户注册
POST   /api/user/login             - 用户登录
GET    /api/user/info              - 获取用户信息
POST   /api/user/update            - 更新用户信息
POST   /api/user/change-password   - 修改密码
POST   /api/user/activate-card     - 激活卡密
GET    /api/user/announcements     - 获取公告列表
GET    /api/user/stats             - 用户统计（管理员）
```

### 管理员接口（新增）

```
GET    /api/admin/users                    - 用户列表
GET    /api/admin/users/:id                - 用户详情
POST   /api/admin/users/:id/status         - 更新用户状态
POST   /api/admin/users/:id/vip            - 设置VIP

GET    /api/admin/cards                    - 卡密列表
GET    /api/admin/cards/batches            - 批次列表
POST   /api/admin/cards/generate           - 生成卡密
POST   /api/admin/cards/:id/ban            - 封禁卡密
DELETE /api/admin/cards/:id                - 删除卡密

GET    /api/admin/announcements            - 公告列表
POST   /api/admin/announcements            - 创建公告
PUT    /api/admin/announcements/:id        - 更新公告
DELETE /api/admin/announcements/:id        - 删除公告
POST   /api/admin/announcements/:id/toggle - 切换状态

GET    /api/admin/dashboard                - 仪表盘
```

---

## 数据库变更

### 新增表

1. **users** - 用户表
   - 基础信息（用户名、密码、邮箱等）
   - VIP状态
   - 资源限制
   - 登录信息

2. **card_keys** - 卡密表
   - 卡密字符串
   - 卡密类型
   - 资源配额
   - 使用状态

3. **douyin_qrcodes** - 二维码登录表
   - Token
   - 二维码图片
   - 扫码状态
   - Cookie数据

4. **announcements** - 公告表
   - 公告类型
   - 标题/内容
   - 排序/状态

### 修改表

为以下表添加 `user_id` 字段：
- `douyin_account`
- `scheduled_task`
- `task_execution`
- `send_history`
- `operation_log`

### 新增视图

- `v_user_stats` - 用户统计视图
- `v_card_key_stats` - 卡密统计视图

---

## 技术栈更新

### 新增依赖

```
PyJWT==2.8.0                    # JWT认证
passlib[bcrypt]==1.7.4          # 密码加密
python-multipart==0.0.9         # 表单解析
```

### 核心模块

| 文件 | 说明 | 行数 |
|------|------|------|
| `models_user.py` | 用户相关数据模型 | ~300 |
| `dal_user.py` | 用户数据访问层 | ~450 |
| `api_user.py` | 用户API路由 | ~350 |
| `api_admin.py` | 管理员API路由 | ~500 |
| `auth.py` | 认证和权限模块 | ~250 |
| `protocol_sender.py` | 协议发送模块 | ~300 |

**总计新增代码：~2150行**

---

## 安全增强

1. **密码加密**
   - 使用bcrypt算法（rounds=12）
   - 密码强度验证

2. **JWT认证**
   - Token过期时间：72小时
   - 支持多种传递方式（Header/Query/Form）

3. **权限分级**
   - 普通用户
   - VIP用户
   - 管理员

4. **资源隔离**
   - 用户只能访问自己的资源
   - 管理员可管理所有资源

---

## 配置项

新增系统配置：

```python
# JWT配置
JWT_SECRET_KEY = "autospark_secret_key"
JWT_EXPIRATION_HOURS = 72

# 用户注册
enable_user_register = True
default_douyin_account_limit = 1
default_scheduled_task_limit = 1

# VIP配置
vip_douyin_account_limit = -1  # -1表示不限制
vip_scheduled_task_limit = -1

# 消息发送模式
message_send_mode = 'browser'  # browser 或 protocol

# 二维码登录
enable_qrcode_login = True

# 公告功能
announcement_enabled = True
```

---

## 兼容性

### ✅ 向后兼容

- 保留原有 `admin` 表和所有接口
- 旧数据自动关联到系统管理员（user_id=1）
- 原有功能全部保留

### ⚠️ 注意事项

1. 首次升级需要执行数据库迁移脚本
2. 生产环境务必修改JWT密钥
3. 建议启用HTTPS
4. 默认管理员账号：admin/admin123（请立即修改）

---

## 性能优化

1. **数据库索引**
   - 为所有常用查询字段添加索引
   - 复合索引优化多条件查询

2. **统计视图**
   - 预计算统计数据
   - 减少实时查询压力

3. **JWT缓存**
   - 支持Redis缓存（可选）
   - 减少重复验证开销

---

## 已知问题

1. **Protocol模式**
   - 实验性功能，可能因抖音API变更而失效
   - 建议在生产环境使用Browser模式

2. **邮箱验证**
   - 暂未实现注册邮箱验证
   - v3.1计划添加

3. **找回密码**
   - 暂未实现
   - v3.1计划添加

---

## 升级指南

详见 [UPGRADE_V3.0.md](UPGRADE_V3.0.md)

简要步骤：
1. 备份数据库
2. 安装新依赖：`pip install -r requirements.txt`
3. 执行升级脚本：`sqlite3 spark.db < sql/upgrade_v3.0_multi_user.sql`
4. 更新配置文件
5. 重启应用

---

## 致谢

感谢 `douyin_huohua` 项目提供的商业化设计参考。

---

## v2.1 功能回顾

保留v2.1的所有功能：
- ✅ 异步数据库操作（models_async.py, dal_async.py）
- ✅ Selenium反检测（selenium_stealth.py）
- ✅ 安全增强（security.py）
- ✅ 监控日志（monitoring.py）

---

## 下一版本预告（v3.1）

计划功能：
- 邮箱验证注册
- 找回密码
- 用户积分系统
- 使用量统计图表
- 协议模式完善
- Webhook通知
- API限流

---

**版本代号**：Aurora（极光）

**发布日期**：2026-09-28

**Git标签**：v3.0.0
