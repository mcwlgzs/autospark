# AutoSpark v3.0 多用户系统升级指南

## 升级概览

AutoSpark v3.0 引入了完整的多用户系统，支持用户注册、卡密激活、二维码登录、公告系统和双模式消息发送。

### 核心特性

1. **多用户系统**
   - 用户注册/登录（JWT认证）
   - 用户资料管理
   - 资源限制（抖音账号数、定时任务数）
   - VIP永久会员体系

2. **卡密激活系统**
   - 批量生成卡密
   - 卡密激活即永久VIP
   - 批次管理
   - 使用追踪

3. **二维码登录**
   - 生成抖音登录二维码
   - 实时状态追踪（待扫码/已扫码/已确认/已过期）
   - Cookie自动获取

4. **公告系统**
   - 网站首页公告
   - 用户后台公告
   - 支持排序和启用/禁用

5. **双模式发送**
   - **browser模式**：浏览器自动化（原有方式，稳定）
   - **protocol模式**：协议发送（实验性，无需浏览器，速度快）

---

## 升级步骤

### 1. 备份数据库

```bash
# SQLite备份
cp spark.db spark.db.backup

# MySQL备份
mysqldump -u root -p spark_db > spark_db_backup.sql
```

### 2. 安装新依赖

```bash
pip install -r requirements.txt
```

新增依赖：
- `PyJWT==2.8.0` - JWT认证
- `passlib[bcrypt]==1.7.4` - 密码加密
- `python-multipart==0.0.9` - 表单数据解析

### 3. 执行数据库升级脚本

```bash
# SQLite
sqlite3 spark.db < sql/upgrade_v3.0_multi_user.sql

# MySQL
mysql -u root -p spark_db < sql/upgrade_v3.0_multi_user.sql
```

### 4. 更新配置文件

编辑 `config.py`，添加：

```python
# JWT配置
JWT_SECRET_KEY = "your-secret-key-here"  # 生产环境务必修改
JWT_EXPIRATION_HOURS = 72

# 系统配置
ENABLE_USER_REGISTER = True  # 是否允许用户注册
DEFAULT_DOUYIN_ACCOUNT_LIMIT = 1  # 新用户默认账号数限制
DEFAULT_SCHEDULED_TASK_LIMIT = 1  # 新用户默认任务数限制

# 消息发送模式
MESSAGE_SEND_MODE = 'browser'  # browser 或 protocol
```

### 5. 启动应用

```bash
python app.py
```

---

## 新增API接口

### 用户接口 (`/api/user`)

| 接口 | 方法 | 说明 | 认证 |
|------|------|------|------|
| `/api/user/register` | POST | 用户注册 | 否 |
| `/api/user/login` | POST | 用户登录 | 否 |
| `/api/user/info` | GET | 获取用户信息 | 是 |
| `/api/user/update` | POST | 更新用户信息 | 是 |
| `/api/user/change-password` | POST | 修改密码 | 是 |
| `/api/user/activate-card` | POST | 激活卡密 | 是 |
| `/api/user/announcements` | GET | 获取公告列表 | 否 |

### 管理员接口 (`/api/admin`)

| 接口 | 方法 | 说明 | 认证 |
|------|------|------|------|
| `/api/admin/users` | GET | 获取用户列表 | 管理员 |
| `/api/admin/users/:id` | GET | 获取用户详情 | 管理员 |
| `/api/admin/users/:id/status` | POST | 更新用户状态 | 管理员 |
| `/api/admin/users/:id/vip` | POST | 设置VIP | 管理员 |
| `/api/admin/cards` | GET | 获取卡密列表 | 管理员 |
| `/api/admin/cards/generate` | POST | 生成卡密 | 管理员 |
| `/api/admin/cards/:id/ban` | POST | 封禁卡密 | 管理员 |
| `/api/admin/cards/:id` | DELETE | 删除卡密 | 管理员 |
| `/api/admin/announcements` | GET/POST | 公告管理 | 管理员 |
| `/api/admin/dashboard` | GET | 仪表盘统计 | 管理员 |

---

## 使用示例

### 1. 用户注册

```bash
curl -X POST http://localhost:5000/api/user/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser",
    "password": "password123",
    "email": "test@example.com"
  }'
```

### 2. 用户登录

```bash
curl -X POST http://localhost:5000/api/user/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser",
    "password": "password123"
  }'
```

返回：
```json
{
  "code": 200,
  "msg": "登录成功",
  "data": {
    "token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
    "user": {
      "id": 1,
      "username": "testuser",
      "is_vip": 0,
      ...
    }
  }
}
```

### 3. 激活卡密

```bash
curl -X POST http://localhost:5000/api/user/activate-card \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "card_key": "ABCD1234EFGH5678"
  }'
```

### 4. 生成卡密（管理员）

```bash
curl -X POST http://localhost:5000/api/admin/cards/generate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ADMIN_TOKEN" \
  -d '{
    "count": 10,
    "card_type": "vip",
    "douyin_account_limit": -1,
    "scheduled_task_limit": -1,
    "remark": "测试批次"
  }'
```

---

## 前端集成

### 认证拦截器（Axios示例）

```javascript
import axios from 'axios';

// 请求拦截器
axios.interceptors.request.use(config => {
  const token = localStorage.getItem('token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// 响应拦截器
axios.interceptors.response.use(
  response => response,
  error => {
    if (error.response?.status === 401) {
      // token过期，跳转登录页
      localStorage.removeItem('token');
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);
```

### 用户注册页面

```javascript
async function register() {
  try {
    const response = await axios.post('/api/user/register', {
      username: document.getElementById('username').value,
      password: document.getElementById('password').value,
      email: document.getElementById('email').value
    });
    
    if (response.data.code === 200) {
      alert('注册成功，请登录');
      window.location.href = '/login';
    }
  } catch (error) {
    alert(error.response?.data?.msg || '注册失败');
  }
}
```

### 卡密激活

```javascript
async function activateCard() {
  try {
    const cardKey = document.getElementById('cardKey').value;
    const response = await axios.post('/api/user/activate-card', {
      card_key: cardKey
    });
    
    if (response.data.code === 200) {
      alert('恭喜您成为永久VIP会员！');
      location.reload();
    }
  } catch (error) {
    alert(error.response?.data?.msg || '激活失败');
  }
}
```

---

## 数据库结构变更

### 新增表

1. **users** - 用户表
2. **card_keys** - 卡密表
3. **douyin_qrcodes** - 二维码登录表
4. **announcements** - 公告表

### 修改表

为以下表添加 `user_id` 字段：
- `douyin_account`
- `scheduled_task`
- `task_execution`
- `send_history`
- `operation_log`

---

## 兼容性说明

### 向后兼容

- 原有的 `admin` 表保留，管理员账号独立
- 所有旧数据的 `user_id` 默认为 1（系统管理员）
- 原有API接口继续可用，新增认证装饰器不影响管理员接口

### 数据迁移

如果需要将现有数据关联到特定用户：

```sql
-- 将某个管理员的数据迁移到新用户
UPDATE douyin_account SET user_id = 2 WHERE admin_id = 1;
UPDATE scheduled_task SET user_id = 2 WHERE admin_id = 1;
```

---

## 安全建议

1. **生产环境修改JWT密钥**
   ```python
   JWT_SECRET_KEY = secrets.token_urlsafe(32)  # 生成强随机密钥
   ```

2. **启用HTTPS**
   ```python
   app.run(ssl_context=('cert.pem', 'key.pem'))
   ```

3. **设置密码强度要求**
   - 最小长度：6位（可在 `auth.py` 中调整）
   - 可选：要求包含字母和数字

4. **限制注册频率**
   - 建议添加IP限流
   - 可选：添加邮箱验证

5. **定期清理过期数据**
   ```python
   # 清理7天前的过期二维码
   from dal_user import DouyinQrcodeDAL
   qrcode_dal = DouyinQrcodeDAL(session)
   qrcode_dal.cleanup_expired_qrcodes(days=7)
   ```

---

## 性能优化

### 1. 数据库索引

升级脚本已自动创建所有必要索引：
- `idx_users_username`
- `idx_card_keys_status`
- `idx_douyin_qrcodes_token`
- 等...

### 2. Token缓存

建议使用Redis缓存已验证的token：

```python
import redis
r = redis.Redis(host='localhost', port=6379, db=0)

# 验证token时先查缓存
cached_payload = r.get(f"token:{token}")
if cached_payload:
    return json.loads(cached_payload)
```

### 3. 查询优化

使用视图快速获取统计数据：
```sql
SELECT * FROM v_user_stats;
SELECT * FROM v_card_key_stats;
```

---

## 故障排查

### 问题1：JWT token无效

**原因**：服务器重启后密钥改变  
**解决**：将JWT_SECRET_KEY写入配置文件或环境变量

### 问题2：卡密激活失败

**检查**：
1. 卡密是否存在
2. 卡密状态是否为"未使用"
3. 数据库事务是否正常提交

### 问题3：用户无法创建账号/任务

**检查**：
1. 用户是否达到限制上限
2. 查看 `douyin_account_limit` 和 `scheduled_task_limit`
3. -1 表示不限制

---

## 下一步计划

v3.1 功能规划：
- [ ] 邮箱验证注册
- [ ] 找回密码功能
- [ ] 用户积分系统
- [ ] 使用量统计图表
- [ ] 协议模式完善（AI优化、反爬虫对抗）
- [ ] Webhook通知
- [ ] API调用限流

---

## 技术支持

- GitHub: https://github.com/mcwlgzs/autospark
- Issues: https://github.com/mcwlgzs/autospark/issues

---

**升级完成后，请使用默认管理员账号登录：**
- 用户名：`admin`
- 密码：`admin123`

登录后请立即修改密码！
