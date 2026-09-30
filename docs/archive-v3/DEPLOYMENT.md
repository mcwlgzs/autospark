# AutoSpark v3.0 部署指南

## 📋 部署前准备

### 系统要求

- **操作系统**: Linux (推荐 Ubuntu 20.04+) / Windows Server / macOS
- **Python**: 3.8 或更高版本
- **内存**: 最低 2GB，推荐 4GB+
- **磁盘**: 最低 10GB 可用空间
- **Chrome**: 用于Selenium自动化

### 依赖软件

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install -y python3 python3-pip python3-venv
sudo apt install -y chromium-browser chromium-chromedriver

# CentOS/RHEL
sudo yum install -y python3 python3-pip
sudo yum install -y chromium chromium-chromedriver
```

---

## 🚀 快速部署（生产环境）

### 1. 下载项目

```bash
cd /opt
git clone https://github.com/mcwlgzs/autospark.git
cd autospark
```

### 2. 创建虚拟环境

```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置环境

```bash
# 复制配置文件
cp .env.example .env

# 编辑配置（重要！）
nano .env
```

**必须修改的配置项：**

```bash
APP_ENV=production
DEBUG=False
JWT_SECRET_KEY=your_random_secret_key_here  # 使用随机字符串
```

### 5. 初始化数据库

```bash
python init_db_v3.py
```

### 6. 创建日志目录

```bash
mkdir -p logs
chmod 755 logs
```

### 7. 启动测试

```bash
python app_v3.py
```

访问 http://localhost:5000 测试是否正常

---

## 🔧 生产环境配置

### 方式1: Systemd服务（推荐）

创建服务文件：

```bash
sudo nano /etc/systemd/system/autospark.service
```

内容：

```ini
[Unit]
Description=AutoSpark v3.0 Service
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/autospark
Environment="PATH=/opt/autospark/venv/bin"
ExecStart=/opt/autospark/venv/bin/python app_v3.py
Restart=always
RestartSec=10

# 日志
StandardOutput=append:/opt/autospark/logs/service.log
StandardError=append:/opt/autospark/logs/service.error.log

[Install]
WantedBy=multi-user.target
```

启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable autospark
sudo systemctl start autospark
sudo systemctl status autospark
```

### 方式2: Gunicorn + Nginx

#### 安装Gunicorn

```bash
pip install gunicorn
```

#### 创建Gunicorn配置

创建 `gunicorn_config.py`：

```python
import multiprocessing

bind = "127.0.0.1:5000"
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
worker_connections = 1000
timeout = 60
keepalive = 2

accesslog = "logs/gunicorn_access.log"
errorlog = "logs/gunicorn_error.log"
loglevel = "info"

daemon = False
pidfile = "logs/gunicorn.pid"
```

#### 启动Gunicorn

```bash
gunicorn -c gunicorn_config.py app_v3:app
```

#### 配置Nginx

```bash
sudo nano /etc/nginx/sites-available/autospark
```

内容：

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket支持（如果需要）
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        # 超时配置
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    # 静态文件
    location /static {
        alias /opt/autospark/static;
        expires 7d;
    }

    # 日志
    access_log /var/log/nginx/autospark_access.log;
    error_log /var/log/nginx/autospark_error.log;
}
```

启用站点：

```bash
sudo ln -s /etc/nginx/sites-available/autospark /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

#### 配置HTTPS（Let's Encrypt）

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

---

## 🐳 Docker部署

### Dockerfile

创建 `Dockerfile`：

```dockerfile
FROM python:3.9-slim

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件
COPY . .

# 创建日志目录
RUN mkdir -p logs

# 暴露端口
EXPOSE 5000

# 启动命令
CMD ["python", "app_v3.py"]
```

### docker-compose.yml

```yaml
version: '3.8'

services:
  autospark:
    build: .
    container_name: autospark
    ports:
      - "5000:5000"
    volumes:
      - ./spark.db:/app/spark.db
      - ./logs:/app/logs
    environment:
      - APP_ENV=production
      - DEBUG=False
      - JWT_SECRET_KEY=${JWT_SECRET_KEY}
    restart: always
```

### 部署

```bash
# 构建镜像
docker-compose build

# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

---

## 🔒 安全加固

### 1. 修改JWT密钥

```bash
# 生成随机密钥
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

将生成的密钥写入 `.env` 文件的 `JWT_SECRET_KEY`

### 2. 修改默认管理员密码

登录后立即在用户中心修改密码

### 3. 配置防火墙

```bash
# Ubuntu/Debian (UFW)
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP
sudo ufw allow 443/tcp   # HTTPS
sudo ufw enable

# CentOS/RHEL (firewalld)
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

### 4. 限制管理员访问

在 `.env` 中配置：

```bash
ENABLE_IP_WHITELIST=True
ADMIN_IP_WHITELIST=127.0.0.1,your_ip_address
```

### 5. 启用HTTPS

```bash
# 编辑 .env
ENABLE_HTTPS=True
SSL_CERT_PATH=/path/to/cert.pem
SSL_KEY_PATH=/path/to/key.pem
```

---

## 📊 监控与维护

### 查看日志

```bash
# 应用日志
tail -f logs/autospark.log

# 系统服务日志
sudo journalctl -u autospark -f

# Nginx日志
tail -f /var/log/nginx/autospark_access.log
```

### 数据库备份

```bash
# 手动备份
sqlite3 spark.db ".backup backup_$(date +%Y%m%d_%H%M%S).db"

# 自动备份脚本
echo "0 2 * * * cd /opt/autospark && sqlite3 spark.db \".backup backups/spark_\$(date +\%Y\%m\%d).db\"" | crontab -
```

### 清理日志

```bash
# 创建日志轮转配置
sudo nano /etc/logrotate.d/autospark
```

内容：

```
/opt/autospark/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0644 www-data www-data
}
```

---

## 🚨 故障排查

### 服务无法启动

```bash
# 检查日志
sudo journalctl -u autospark -n 50

# 检查端口占用
sudo netstat -tlnp | grep 5000

# 检查Python环境
source venv/bin/activate
python -c "import flask; print(flask.__version__)"
```

### 数据库错误

```bash
# 检查数据库文件权限
ls -la spark.db

# 修复权限
sudo chown www-data:www-data spark.db
```

### Chrome/Selenium错误

```bash
# 检查Chrome版本
chromium-browser --version

# 检查ChromeDriver
chromedriver --version

# 重新安装
sudo apt install --reinstall chromium-browser chromium-chromedriver
```

---

## 📈 性能优化

### 1. 数据库优化

```sql
-- 定期优化数据库
VACUUM;
ANALYZE;

-- 清理旧日志（保留30天）
DELETE FROM message_logs WHERE created_at < datetime('now', '-30 days');
DELETE FROM system_logs WHERE created_at < datetime('now', '-30 days');
```

### 2. 增加Worker数量

编辑 `gunicorn_config.py`：

```python
workers = 8  # 根据CPU核心数调整
```

### 3. Redis缓存（可选）

安装Redis：

```bash
sudo apt install redis-server
```

配置缓存（需要代码修改）

---

## 🔄 版本升级

### 从v2.x升级到v3.0

```bash
# 备份数据库
cp spark.db spark.db.backup

# 拉取最新代码
git pull

# 更新依赖
pip install -r requirements.txt

# 执行升级脚本
python init_db_v3.py spark.db

# 重启服务
sudo systemctl restart autospark
```

---

## 📞 技术支持

- **文档**: [GitHub Wiki](https://github.com/mcwlgzs/autospark/wiki)
- **Issues**: [GitHub Issues](https://github.com/mcwlgzs/autospark/issues)
- **邮箱**: mcwlgzs@example.com

---

**AutoSpark v3.0 - 企业级抖音火花自动化管理系统** 🔥
