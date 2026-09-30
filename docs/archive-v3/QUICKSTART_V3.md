# AutoSpark v3.0 快速开始指南

## 🚀 快速开始

### 1. 环境要求

- Python 3.8+
- SQLite 3 / MySQL 5.7+
- Chrome浏览器（用于Selenium自动化）

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 初始化数据库

**新安装：**
```bash
python init_db_v3.py
```

**从v2.1升级：**
```bash
python init_db_v3.py spark.db
# 脚本会自动备份并升级数据库
```

### 4. 启动应用

```bash
python app_v3.py
```

应用将在 http://localhost:5000 启动

### 5. 默认账号

**管理员账号：**
- 用户名: `admin`
- 密码: `admin123`

⚠️ **请登录后立即修改密码！**

---

## 📖 功能介绍

### 多用户系统

- ✅ 用户注册/登录（JWT认证）
- ✅ 用户资料管理
- ✅ VIP永久会员
- ✅ 资源限制（账号数、任务数）

### 卡密激活

- ✅ 批量生成卡密（1-1000个）
- ✅ 卡密激活即永久VIP
- ✅ 批次管理
- ✅ 使用追踪

### 二维码登录

- ✅ 生成抖音登录二维码
- ✅ 实时状态追踪
- ✅ Cookie自动获取

### 公告系统

- ✅ 网站首页公告
- ✅ 用户后台公告
- ✅ 支持排序和启用/禁用

### 双模式发送

- **Browser模式**：浏览器自动化（稳定）
- **Protocol模式**：协议发送（实验性，快速）

---

## 🔌 API接口

### 用户接口

```
POST   /api/user/register          - 用户注册
POST   /api/user/login             - 用户登录
GET    /api/user/info              - 获取用户信息
POST   /api/user/update            - 更新用户信息
POST   /api/user/change-password   - 修改密码
POST   /api/user/activate-card     - 激活卡密
GET    /api/user/announcements     - 获取公告列表
```

### 管理员接口

```
GET    /api/admin/users            - 用户列表
POST   /api/admin/cards/generate   - 生成卡密
GET    /api/admin/announcements    - 公告管理
GET    /api/admin/dashboard        - 仪表盘
```

完整API文档请查看 [UPGRADE_V3.0.md](UPGRADE_V3.0.md)

---

## 💡 使用示例

### 用户注册

```bash
curl -X POST http://localhost:5000/api/user/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser",
    "password": "password123",
    "email": "test@example.com"
  }'
```

### 用户登录

```bash
curl -X POST http://localhost:5000/api/user/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser",
    "password": "password123"
  }'
```

### 激活卡密

```bash
curl -X POST http://localhost:5000/api/user/activate-card \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "card_key": "ABCD1234EFGH5678"
  }'
```

### 生成卡密（管理员）

```bash
curl -X POST http://localhost:5000/api/admin/cards/generate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ADMIN_TOKEN" \
  -d '{
    "count": 10,
    "douyin_account_limit": -1,
    "scheduled_task_limit": -1
  }'
```

---

## 🔧 配置说明

### JWT配置

编辑 `auth.py`：

```python
JWT_SECRET_KEY = "your-secret-key"  # 生产环境务必修改
JWT_EXPIRATION_HOURS = 72  # token过期时间
```

### 消息发送模式

在系统配置中设置：

```sql
UPDATE system_config 
SET config_value = 'protocol'  -- 或 'browser'
WHERE config_key = 'message_send_mode';
```

---

## 📊 数据库管理

### 查看用户统计

```sql
SELECT * FROM v_user_stats;
```

### 查看卡密统计

```sql
SELECT * FROM v_card_key_stats;
```

### 手动创建VIP用户

```sql
UPDATE users 
SET is_vip = 1,
    vip_activated_at = CURRENT_TIMESTAMP,
    douyin_account_limit = -1,
    scheduled_task_limit = -1
WHERE username = 'testuser';
```

---

## 🐛 故障排查

### 问题1：JWT token无效

**原因**：服务器重启后密钥改变  
**解决**：将JWT_SECRET_KEY写入配置文件

### 问题2：用户无法创建账号

**检查**：
1. 用户是否达到限制上限
2. 查看 `douyin_account_limit` 字段
3. -1 表示不限制

### 问题3：卡密激活失败

**检查**：
1. 卡密是否存在
2. 卡密状态是否为"未使用"（status=1）
3. 数据库事务是否正常提交

---

## 📝 更新日志

查看完整更新日志：[CHANGELOG_V3.0.md](CHANGELOG_V3.0.md)

---

## 🔒 安全建议

1. **修改JWT密钥**
2. **启用HTTPS**
3. **定期备份数据库**
4. **设置防火墙规则**
5. **限制管理员IP**

---

## 📚 文档

- [升级指南](UPGRADE_V3.0.md)
- [更新日志](CHANGELOG_V3.0.md)
- [API文档](UPGRADE_V3.0.md#新增api接口)

---

## 🤝 贡献

欢迎提交Issue和Pull Request！

---

## 📄 许可证

MIT License

---

**AutoSpark v3.0 - 让抖音火花管理更简单** 🔥
