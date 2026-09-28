# 部署说明

面向「把面板放到一台 Linux 服务器上长期跑」的场景。本地开发看 `LOCAL_RUN.md`。

---

## 部署前必须知道的三个硬约束

1. **后端只监听 `127.0.0.1:9844`**，从外网直接访问不到。对外必须由 nginx
   （或其它反代）做转发和 TLS 终止，见下面第三节。
2. **这个分支用「可见」Chrome**：扫码登录、短信/刷脸验证都要人看着浏览器操作。
   服务器没有图形界面时，靠 Xvfb 提供一块虚拟屏幕，Chrome 能跑起来，
   但**屏幕是看不见的** —— 需要人工过验证时得另想办法看画面（见第 2.3 节）。
   完全没有显示环境时，后端会直接回 `初始化失败: 未找到可用的 DISPLAY/Xvfb 环境`。
3. **三个目录是全部身家**，删了就要重新扫码：
   - `data/` —— `state.json`（登录 cookie、任务、发送记账）
   - `logs/` —— `app.log`、`backend.log`、`shots/`（失败截图）
   - `chrome-profile/` —— Chrome 用户目录（登录态主要在这里）

   备份/迁移把这三个目录打包带走即可；反过来，**不要**把 `chrome-profile/`
   提交到 Git 或打进镜像（里面有登录凭据）。

---

## 一、Docker Compose（推荐）

```bash
git clone <你的仓库> spark-web && cd spark-web

# 首次必须建好这三个目录并对齐属主：容器里以 uid 1000 运行，
# 宿主机目录不可写的话，启动时会因写不了 chrome-profile 而失败。
mkdir -p data logs chrome-profile
sudo chown -R 1000:1000 data logs chrome-profile

# 可选：把参数写进 .env（没有也能启动，compose 里已声明 required: false）
cat > .env <<'EOF'
TZ=Asia/Shanghai
SPARK_JITTER_MINUTES=40
EOF

docker compose up -d --build
docker compose logs -f
```

验证：`curl -fsS http://127.0.0.1:9844/healthz` 返回 200 JSON 即正常
（这个接口免鉴权，就是给探活用的；镜像里已经配了 `HEALTHCHECK`）。

### 1.1 改配置

所有可调项都是环境变量，写进 `.env` 后 `docker compose up -d` 重新创建容器即可。
常用的几个（`backend.py` 里读的就是这些名字）：

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `TZ` | `Asia/Shanghai` | 时区。**别删**，容器默认 UTC 会让「每天 22:00」变成早上 6 点 |
| `PORT` | `9844` | 容器内监听端口，改了要同步改 `docker-compose.yml` 的端口映射 |
| `SPARK_JITTER_MINUTES` | `40` | 计划时间之后随机推迟的上限（分钟），`0` = 不随机 |
| `SPARK_CATCHUP_GRACE_MINUTES` | `360` | 错过当天时间点后多久内还补跑，`0` = 不补跑 |
| `SPARK_RETRY_AFTER_MINUTES` | `45` | 当日失败后隔多久补发一次，`0` = 关闭补发 |
| `SPARK_TOKEN_TTL_HOURS` | `72` | 面板登录态空闲多久失效，`0` = 永不过期 |
| `SPARK_SHOW_BROWSER` | `1` | 设成 `0` 关掉可见浏览器（**只在完全不需要人工验证时才用**） |

### 1.2 升级

```bash
git pull
docker compose up -d --build
```

`data/`、`logs/`、`chrome-profile/` 都是宿主机卷，重建镜像不会丢登录态。

### 1.3 需要人工过验证时怎么办

容器里的 Chrome 跑在 Xvfb 上，你在终端里看不到它。不过**正常流程不需要看画面**：
面板的登录页会直接把二维码渲染出来（后端接口返回的就是图片数据），
短信/刷脸/密码验证也都在设置页里点选和提交。

失败现场截图会被保存到 `logs/shots/`，需要时取出来看：

```bash
docker compose cp spark-web:/app/logs/shots/. ./shots/
```

如果确实要看到容器里那块虚拟屏（比如某个控件点不到，想肉眼确认页面长什么样），
可以在容器里临时开一个 VNC：

```bash
docker compose exec spark-web bash -c 'apt-get update && apt-get install -y x11vnc'
docker compose exec spark-web bash -c 'x11vnc -display "$DISPLAY" -forever -shared -nopw -listen 0.0.0.0 -rfbport 5900'
```

再把 `5900` 映射到宿主机，用 VNC 客户端连上去。

还有一种更省事的办法：**在带桌面的机器上先扫码登录**，
再把 `chrome-profile/` 和 `data/` 拷到服务器上，登录态可以直接复用。

---

## 二、systemd 直装（不用 Docker）

```bash
# 1. 系统依赖：Chromium + 驱动 + 虚拟显示 + 中文字体（缺字体页面中文会变方框）
sudo apt update
sudo apt install -y chromium chromium-driver xvfb fonts-noto-cjk python3 python3-venv python3-pip

# 2. 代码就位
sudo useradd --create-home --shell /bin/bash spark     # 已存在就跳过
sudo cp -r . /opt/spark-web
sudo chown -R spark:spark /opt/spark-web
sudo chmod +x /opt/spark-web/start-backend.sh          # 从 Windows 传过来的文件常常没有 x 位

# 3. 装服务
sudo cp deploy/spark-web.service /etc/systemd/system/spark-web.service
sudo systemctl daemon-reload
sudo systemctl enable --now spark-web

# 4. 看状态和日志
systemctl status spark-web
journalctl -u spark-web -f
```

第一次启动时 `start-backend.sh` 会创建 `.venv` 并安装依赖，所以会慢一阵子，
状态里显示它在跑是正常的；等 `/healthz` 通了才算就绪。

> Debian/Ubuntu 的 `chromium` 与 `selenium` 是强耦合的：`apt upgrade` 把 Chromium
> 大版本升上去之后，建议手动回归一次「打开浏览器 → 扫码 → 发一条」，
> 因为驱动不匹配时的报错（`SessionNotCreatedException`）不会自己告诉你原因。

---

## 三、nginx 反向代理 + HTTPS

后端只监听 `127.0.0.1`，**直接暴露 9844 到公网等于把面板送人**：
面板密码是明文 POST 的，没有 TLS 就是裸奔。所以对外只开 nginx。

前端是 Vue 3 + `createWebHistory`（history 模式），需要 nginx 托管构建产物
并做 SPA 回退：

```bash
npm ci && npm run build      # 产物在 dist/
```

```nginx
server {
    listen 443 ssl http2;
    server_name spark.example.com;

    ssl_certificate     /etc/letsencrypt/live/spark.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/spark.example.com/privkey.pem;

    # 前端静态产物
    root /opt/spark-web/dist;
    index index.html;

    # history 模式：直接访问 /friends 这类路由时磁盘上没有对应文件，
    # 必须回退到 index.html 交给前端路由，否则刷新页面就 404。
    location / {
        try_files $uri $uri/ /index.html;
    }

    # 关键：proxy_pass 末尾这个 "/" 不能省。
    # 前端 axios 的 baseURL 是 '/api'（开发态由 vite 的 rewrite 把 /api 前缀去掉），
    # 所以生产环境的 nginx 必须做同样的剥离：
    #   /api/Api/Logs  ->  http://127.0.0.1:9844/Api/Logs
    # 少了这个斜杠就会把 /api 原样带过去，后端全部 404。
    location /api/ {
        proxy_pass http://127.0.0.1:9844/;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # 扫码初始化要拉起 Chrome，可能几十秒，别让 nginx 先超时。
        proxy_read_timeout 180s;
        proxy_send_timeout 180s;
    }

    # 免鉴权探活接口，方便外部监控。
    location = /healthz {
        proxy_pass http://127.0.0.1:9844/healthz;
        access_log off;
    }
}
```

证书用 certbot 签发即可：

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d spark.example.com
```

### 三个容易踩的点

1. **`X-Real-IP` 能不能生效，取决于后端信不信这个反代。**
   `backend.py` 默认只相信 `127.0.0.1,::1` 传来的 `X-Real-IP`/`X-Forwarded-For`
   （用来做「登录失败 N 次锁定」，不信的话所有客户端会共用一个失败计数）。
   - systemd 直装：nginx 和后端在同一台机器上，源地址就是 `127.0.0.1`，不用改。
   - Docker 部署：容器里看到的源地址是 docker 网桥网关（一般是 `172.17.0.1`
     或 `172.18.0.1`），需要把它加进去，例如
     `SPARK_TRUSTED_PROXIES=127.0.0.1,::1,172.17.0.1`。
2. **同源部署不需要动 CORS。** 前端和 `/api` 都由同一个 nginx 提供，属于同源。
   只有当你要把前端放到另一个域名时，才需要把那个来源加进
   `SPARK_CORS_ORIGINS`（默认只放行本机 5173 开发端口）。
3. **不要给面板开公网无鉴权访问。** 面板能直接操作抖音账号，
   建议再加一层：nginx 的 `allow/deny` 限制来源 IP，或者套一层 Basic Auth
   （注意后端自己的登录接口仍然保留，两者不冲突）。

调试阶段想临时直连后端（比如本机没装 nginx），用 SSH 隧道比改监听地址安全：

```bash
ssh -L 9844:127.0.0.1:9844 user@server
# 然后本机访问 http://127.0.0.1:9844
```

---

## 四、宝塔面板 / aaPanel

用宝塔（或 aaPanel）管理服务器的话看 [`baota-deploy.md`](./baota-deploy.md)，
里面有完整步骤：系统依赖、Python 解释器怎么指向面板装的那份
（`SPARK_PYTHON`）、用「进程守护管理器」常驻、宝塔站点的 nginx 反代配置
（含必须剥掉 `/api` 前缀这个必踩的坑）、HTTPS、备份与验收清单。

要点先放在这里：

- 后端只监听 `127.0.0.1:9844`，宝塔站点只反代 `/api/` 与 `/healthz`，
  **不要**用「反向代理」界面代理 `/`（会把静态前端也代理走）。
- 系统时区必须是 `Asia/Shanghai`，否则「每天 22:00」会变成早上 6 点。
- 只需要一份实例（Supervisor 或 Python 项目管理器二选一），
  也**不需要**用宝塔计划任务做定时发送 —— 后端自带调度。
- 防火墙只放 80/443，不要放行 9844。
