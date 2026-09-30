# AutoSpark v3.0 - 快速入门（3分钟上手）

## 🚀 三步启动

### 步骤1: 安装依赖（1分钟）

```bash
pip install -r requirements.txt
```

### 步骤2: 初始化数据库（30秒）

```bash
python init_db_v3.py
```

### 步骤3: 启动应用（30秒）

```bash
# Linux/Mac
bash start.sh

# Windows
start.bat

# 或直接运行
python app_v3.py
```

**访问**: http://localhost:5000

---

## 👤 默认账号

- **用户名**: `admin`
- **密码**: `admin123`

⚠️ **登录后立即修改密码！**

---

## 📝 首次使用流程

### 1. 注册普通用户

访问 http://localhost:5000/templates/register.html

- 输入用户名、密码、邮箱
- 点击"注册"

### 2. 生成卡密（管理员）

```bash
# 使用管理员账号登录后调用API
curl -X POST http://localhost:5000/api/admin/cards/generate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <管理员Token>" \
  -d '{
    "count": 1,
    "douyin_account_limit": -1,
    "scheduled_task_limit": -1
  }'
```

### 3. 激活VIP

访问 http://localhost:5000/templates/dashboard.html

- 在"激活卡密"区域输入卡密
- 点击"立即激活"
- 成为永久VIP 🎉

### 4. 添加抖音账号

- 使用二维码扫码获取Cookie
- 或手动添加Cookie
- 开始自动发送消息

---

## 🛠️ 常用命令

### 查看日志
```bash
tail -f logs/autospark.log
```

### 重启服务
```bash
# Systemd
sudo systemctl restart autospark

# 手动
python app_v3.py
```

### 备份数据库
```bash
sqlite3 spark.db ".backup backup_$(date +%Y%m%d).db"
```

---

## 🔧 快速配置

### 修改端口

编辑 `app_v3.py`:

```python
app.run(
    host='0.0.0.0',
    port=8000,  # 改为你的端口
    debug=True
)
```

### 修改JWT密钥

编辑 `auth.py`:

```python
JWT_SECRET_KEY = "你的随机密钥"
```

或使用 `.env` 文件:

```bash
JWT_SECRET_KEY=your_random_secret_key
```

---

## 📊 API快速测试

### 1. 注册
```bash
curl -X POST http://localhost:5000/api/user/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser",
    "password": "password123"
  }'
```

### 2. 登录
```bash
curl -X POST http://localhost:5000/api/user/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser",
    "password": "password123"
  }'
```

### 3. 获取用户信息
```bash
curl http://localhost:5000/api/user/info \
  -H "Authorization: Bearer <你的Token>"
```

---

## 🐛 常见问题

### Q1: 端口被占用
```bash
# 查找占用进程
lsof -i :5000

# 杀死进程
kill -9 <PID>
```

### Q2: 模块未安装
```bash
pip install --upgrade -r requirements.txt
```

### Q3: 数据库初始化失败
```bash
# 删除旧数据库
rm spark.db

# 重新初始化
python init_db_v3.py
```

---

## 📚 下一步

- 📖 阅读 [完整文档](README.md)
- 🚀 查看 [部署指南](docs/DEPLOYMENT.md)
- 📝 查看 [API文档](docs/UPGRADE_V3.0.md)
- 🔄 查看 [更新日志](docs/CHANGELOG_V3.0.md)

---

**5分钟快速上手 AutoSpark v3.0！** 🔥
