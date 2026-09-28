# AutoSpark - 抖音火花助手

抖音火花助手 Web 管理平台，基于 Vue 3 + Element Plus 构建，提供抖音好友火花自动续期的可视化管理系统。

本项目通过 Selenium 驱动本机或服务器上的真实 Chrome 页面，方便完成抖音扫码登录和短信二次验证。

> ⚠️ **风险提示**：自动化发私信违反抖音社区公约，可能导致账号被限流、要求安全验证甚至封禁。
> 本项目仅限**本人账号、少量好友、每天一条**的个人自用场景，请勿用于批量营销或多账号运营。
> 使用前请先读完下面的「安全与风险」，并自行承担使用后果。

**📨 GitHub**: [https://github.com/mcwlgzs/autospark](https://github.com/mcwlgzs/autospark)

**🌟 如果该项目对您有帮助，欢迎 Star 支持！**

# 法律声明

**重要提示**：本程序仅供个人学习和研究使用。请严格遵守相关法律法规，不得将本程序用于任何违法或侵权行为。

---
## 界面截图

*项目截图待补充*

## 默认信息
账户: admin 密码:123456

> 首次登录后请立刻在「设置」页改掉默认密码（新密码至少 8 位且不能是纯数字）。
> 改密码会强制所有已登录的浏览器重新登录，这是刻意设计。

## 平台支持

- 支持 Windows
- 支持 Linux
- 支持 Docker（容器内用 Xvfb 提供虚拟显示）
- 不依赖 Windows 专有的 Edge Driver 路径
- Linux 服务器部署时需要可用的图形显示环境，因为这里使用的是可视化 Chrome，不是 headless 模式

Linux 下如果没有桌面环境，请提前准备 X11/Xvfb。
后端会优先使用显式传入的 `DISPLAY`，也会自动尝试常见的 `:91`、`:99`、`:1`。

## 功能特性

### 账户管理
- 扫码登录（抖音 App 扫码授权）
- 手机号登录（含区号；手机号与验证码均走请求体，不进访问日志）
- 手动登录（Base64Cookie 方式）
- Cookie 一键导出
- 登录状态实时监测
- 上次登录 IP 记录
- 管理员密码修改（PBKDF2 加盐存储，改完强制重新登录）
- 二次验证（短信 / 刷脸 / 登录密码 / 重新发送短信）

### 好友管理
- 好友列表展示（头像、火花天数、按有无火花筛选）
- 好友搜索过滤
- 实时刷新好友数据
- 一键发送消息
- 一键发送文昌帝君灵签图文（现取一条当天签文：先发签图、再发签文文字）
- 灵签可「只发签图」（不附任何文字），用来单独验证图片这条路
- 发送预检（Dry Run）：只验证能不能打开会话，不发任何消息

### 定时任务
- 为好友创建每日定时发送任务
- 批量勾选多个好友一次创建（多选续火）
- 支持文案池（一行一条，发送时随机挑一条）与 `{date}`、`{weekday}` 占位符
- 留空 = 每天发送前现取一条名言（不是固定成同一句）
- 修改任务时间 / 文案、删除任务、批量删除
- **随机时间窗**：实际发送时间 = 设定时间再随机推迟 0~N 分钟（默认 40 分钟）
- **错过补跑**：机器重启/崩溃错过当天时间点后，在宽限窗口内（默认 6 小时）自动补发
- **当日补发**：本轮发送失败时，约 45 分钟后只对失败的好友补发一次，每天最多一次
- **灵签任务**：勾选「灵签」后该任务每天发送文昌帝君灵签图文（签图 + 当天签文）；
  任务里写了文案就用你写的（签图照发），留空才用当天签文；
  签文接口取不到时退化成纯文字（**火花不能断**），签图没确认送出时通知会写明「签图没有送出」
- **立即试发**：任务列表每行的「试发」按钮会**立刻按这条任务的配置真发一条**（灵签任务就是
  签图 + 签文），用来当场验证任务能不能发出去，不用等到设定时间；试发成功会占用该好友
  当天的发送记录，所以今天那次定时发送会自动跳过（仍是「每位好友每天一条」）

### 运行监控
- 首页看板：浏览器/登录状态、好友数、任务数、运行时长
- 信息日志页：浏览器 / 登录 / 验证 / 发消息 / 定时任务 / 任务管理分类检索
- 失败现场自动截图（保留 7 天、最多 200 张，自动清理）
- 签图缓存自动清理（`data/images/`，保留 30 天、最多 300 张）
- 消息推送通知（成功/失败/风控/掉线）
- `/healthz` 免鉴权探活接口（给 Docker HEALTHCHECK、systemd、监控用）

---

## 运行机制（和旧版的差别）

定时任务不再依赖 `schedule` 库，而是后端内置的 ticker + 发送队列：

```text
ticker（每 5 秒）
  ├─ 今天该发了吗？  ── 到点 / 错过补跑 / 一天只发一次 ──┐
  ├─ 补发队列到点了吗？ ── 只补失败的好友，每天一次 ─────┤
  └─ 清理过期登录会话                                  │
                                                       ▼
                                        发送队列 → worker 线程串行执行
                                                       │
                              打开会话 → 校验会话标题 → 输入 → 回车 → 确认新气泡
```

这样设计解决了旧实现的三个硬伤：

1. **错过就永远不补**：`schedule` 只在进程活着时触发，那一分钟不在，任务就永久跳过 ——
   而漏一天火花就断了；
2. **一次异常打死调度线程**：旧调度循环没有异常隔离，线程一死没有任何日志，
   面板照旧显示任务列表，实际再也不会发；
3. **固定整点太规律**：现在带随机窗口；好友之间也有随机间隔，降低机器特征。

两个容易踩的细节，都已经处理掉：

- **浏览器没就绪时不会「假发一次」**：重启后如果有人没打开面板点「初始化浏览器」，
  任务到点时会等待（日志每 10 分钟提醒一次），而不是记一次失败并把当天的额度烧掉；
  默认还会在启动时自动初始化浏览器（`SPARK_AUTO_INIT_BROWSER=1`），所以正常情况下
  机器重启后不需要任何人工干预。
- **补跑也要浏览器就绪**：当日补发队列同样只在浏览器可用时才取走，
  否则「补发过一次」会被白白记账。

任务状态落盘在 `data/state.json`，重启后自动恢复；`python backend.py` 和
`uvicorn backend:app` 两种启动方式行为一致（恢复与调度挂在应用 lifespan 上）。

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端框架 | Vue 3 (Composition API) |
| UI 组件库 | Element Plus |
| 状态管理 | Pinia |
| 构建工具 | Vite |
| 路由 | Vue Router |
| HTTP 客户端 | Axios |
| 后端框架 | FastAPI + Uvicorn |
| 浏览器自动化 | Selenium（驱动可见 Chrome） |
| 定时调度 | 内置 ticker + 发送队列（不依赖第三方调度库） |
| 状态持久化 | JSON 文件（`data/state.json`，原子写入 + 0600 权限） |
| 纯逻辑层 | `spark_core.py`（只用标准库，可脱离浏览器单测） |

---

## 项目结构

```
.
├── src/                        # 前端页面与状态管理
├── public/                     # 前端静态资源
├── backend.py                  # FastAPI + Selenium 后端（HTTP 路由与浏览器操作）
├── spark_core.py               # 纯逻辑：时间/文案池/补跑判定/去重/密码/token（stdlib-only）
├── state_store.py              # 状态持久化（原子落盘）
├── notifier.py                 # 消息推送（stdlib-only）
├── tests/                      # 单元测试（unittest，无需第三方依赖即可运行）
├── deploy/                     # systemd 单元与部署说明（含宝塔面板部署文档）
├── Dockerfile / docker-compose.yml
├── requirements.txt            # 后端依赖（锁定版本）
├── requirements-dev.txt        # 开发/测试依赖（pytest、ruff）
├── start-backend.ps1           # Windows 启动脚本
├── start-backend.sh            # Linux 启动脚本（缺 DISPLAY 时自动用 xvfb-run）
├── LOCAL_RUN.md                # 本地运行补充说明
├── dist/                       # 前端生产构建产物
├── vite.config.js              # Vite 配置（含 API 代理）
└── package.json
```

---

## 快速开始

### 环境要求
- Node.js 18+
- Python 3.10+
- Google Chrome
- Chromedriver（Selenium 4.6+ 会自动下载匹配版本；也可用 `CHROMEDRIVER_PATH` 显式指定）

### Windows 启动

```powershell
.\start-backend.ps1
npm install
npm run dev
```

打开 `http://localhost:5173`。

> `start-backend.ps1` 会先检查 Python 是否真的可用，并检查 `.venv\Scripts\python.exe` 是否存在。
> 如果目录里的 `.venv` 是从 Linux 拷过来的（只有 `bin/` 没有 `Scripts/`），脚本会明确提示你删除重建，
> 而不是抛一句看不懂的错误。

### Linux 启动

```bash
chmod +x ./start-backend.sh
./start-backend.sh
npm install
npm run dev
```

### Docker Compose

```bash
docker compose up -d
```

镜像里用 Xvfb 提供虚拟显示，`data/`、`logs/`、`chrome-profile/` 都映射到宿主机。
**注意**：容器内看不到真实屏幕，扫码登录需要在宿主机上先完成登录并挂载 `chrome-profile/`，
或者自行接 VNC。详见 `deploy/README.md`。

### 宝塔面板（Linux 服务器）

用宝塔 / aaPanel 管理服务器的话，完整步骤见 [`deploy/baota-deploy.md`](deploy/baota-deploy.md)。
（要点：后端用「进程守护管理器」常驻，宝塔站点只反代 `/api/` 与 `/healthz`，
服务器时区必须是 `Asia/Shanghai`，且只需一份实例。）

### 生产构建

```bash
npm run build
```

产物输出到 `dist/` 目录，可部署至任意静态服务器。

---

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PORT` | `9844` | 后端监听端口（只绑 `127.0.0.1`） |
| `SPARK_DATA_DIR` | `./data` | 状态目录（`state.json`），容器/测试可指到别处 |
| `SPARK_LOG_DIR` | `./logs` | 日志与失败截图目录（`app.log`、`shots/`） |
| `CHROME_BINARY` | 自动探测 | Chrome 可执行文件路径 |
| `CHROMEDRIVER_PATH` | 自动探测 | Chromedriver 路径，找不到则用 Selenium Manager |
| `CHROME_PROFILE_DIR` | `./chrome-profile` | 浏览器用户目录（登录态在这里，属于敏感数据） |
| `DISPLAY` | 自动尝试 `:91`/`:99`/`:1` | Linux 显示环境 |
| `SPARK_SHOW_BROWSER` | `1` | 设为 `0` 走 headless（**登录/验证就没法人工完成了**） |
| `SPARK_AUTO_INIT_BROWSER` | `1` | 启动时若已有定时任务，自动拉起浏览器（无人值守的关键） |
| `SPARK_JITTER_MINUTES` | `40` | 随机时间窗大小，0 = 整点发 |
| `SPARK_CATCHUP_GRACE_MINUTES` | `360` | 错过当天时间点后多久内还补跑，0 = 不补跑 |
| `SPARK_RETRY_AFTER_MINUTES` | `45` | 当日失败后隔多久补发一次，0 = 关闭补发 |
| `SPARK_TOKEN_TTL_HOURS` | `72` | 面板登录状态空闲过期时间，0 = 不过期 |
| `SPARK_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | 允许跨域的来源（逗号分隔） |
| `SPARK_TRUSTED_PROXIES` | `127.0.0.1,::1` | 只有来自这些地址才信任 `X-Real-IP`/`X-Forwarded-For` |
| `SPARK_USER_AGENT` | Chrome/110 串（沿用原值） | 浏览器 UA |
| `SPARK_LOCK_WAIT_SECONDS` | `15` | 浏览器忙时请求最多等多久，超时返回 409 |
| `SPARK_SEND_TIMEOUT` | `15` | 单条消息发送确认预算（秒） |
| `SPARK_SEND_POLL` | `0.2` | 确认轮询间隔（秒） |
| `SPARK_SEND_GRACE` | `1.0` | 新气泡需要持续干净多久才算成功（秒） |
| `SPARK_IMAGE_MAX_MB` | `5` | 签图大小上限（MB），超过就整条不发 |
| `SPARK_IMAGE_TIMEOUT` | `10` | 下载签图超时（秒） |
| `SPARK_IMAGE_UPLOAD_TIMEOUT` | `20` | 图片上传后等预览出现的最长时间（秒） |
| `SPARK_IMAGE_SEND_TIMEOUT` | `25` | 图片消息送达确认预算（秒，比纯文字长） |
| `SPARK_WENCHANG_URL` | `https://v2.xxapi.cn/api/wenchangdijunrandom` | 文昌帝君灵签接口（换供应商只改这里） |
| `SPARK_IMAGE_KEEP_DAYS` | `30` | 签图缓存保留天数 |
| `SPARK_IMAGE_MAX_FILES` | `300` | 签图缓存文件数上限 |
| `SPARK_SHOT_KEEP_DAYS` | `7` | 失败截图保留天数 |
| `SPARK_SHOT_MAX_FILES` | `200` | 失败截图数量上限 |

---

## API 代理配置

后端只监听 `127.0.0.1:9844`，**必须**通过反向代理对外提供服务（开发态由 Vite 代理）。

```js
        location / {
            index index.php index.html;
            try_files $uri $uri/ /index.html;
            autoindex  off;
            .......
          }
        location /api/ {
            proxy_pass http://127.0.0.1:9844/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_connect_timeout 60s;
            proxy_read_timeout 60s;
        }
        location = /healthz {
            proxy_pass http://127.0.0.1:9844/healthz;
            access_log off;
        }
```

> 建议同时配 HTTPS 与访问控制（例如只允许自己的 IP 访问面板）。
> 登录失败节流依赖 `X-Real-IP`，请务必像上面这样用 `$remote_addr` **覆盖**该头，
> 而不要直接透传客户端传来的值。

---

## 测试与开发

单元测试用标准库 `unittest` 编写，只依赖标准库（不需要 Chrome，也不需要装后端依赖）：

```bash
python -m unittest discover -s tests -t . -v
```

装了开发依赖后也可以用 pytest 与 ruff：

```bash
pip install -r requirements-dev.txt
python -m pytest -q
ruff check .
```

`spark_core.py` 里的逻辑（时间归一化、随机窗口、补跑判定、当日去重、文案池渲染、
密码哈希与时序安全的 token 表）都覆盖了单测，CI 见 `.github/workflows/ci.yml`。

---

## 安全与风险

- **改掉默认密码**：内置默认口令 `admin / 123456` 是公开信息。
- **不要把 9844 端口暴露到公网**：后端信任本机反代，直接暴露会绕过所有节流。
- **`data/state.json` 与 `chrome-profile/` 都是敏感数据**：
  前者含密码哈希、推送 token、好友名与发送记账，后者等于你的登录态。
  两者都已写进 `.gitignore`，**切勿提交或分享**。
- 密码使用 PBKDF2-HMAC-SHA256（加盐、20 万次迭代）存储；旧的裸 SHA-256 哈希会在登录成功时自动升级。
- 面板登录状态默认空闲 72 小时过期，改密码会立即失效全部会话。
- 消息正文、面板密码、手机号、短信验证码一律走 **POST 请求体**，不会进 nginx 访问日志。
- 风控与登录失效会被识别并**停止本轮发送**（继续硬发只会招来更严厉的限制）。
- 抖音页面结构变化会让选择器失效，本项目只能尽力适配；失败截图与信息日志是排查的第一手材料。
- **发图属于新增的行为特征**：灵签图文会在同一次会话里发「图片 + 文字」两条消息，
  比纯文字更像自动化操作。请保持本项目原有的低频前提（本人账号、少量好友、每天一条）。
- **灵签内容来自第三方接口**（默认 `v2.xxapi.cn`）：签文与签图由对方提供，其服务器会看到你的出口 IP。
  接口返回视为不可信输入——图片会先校验 Content-Type、文件魔数（JPG/PNG/GIF/WEBP）与大小上限再落盘，
  指向本机/内网的图片地址一律拒绝（防止面板被当成内网探测工具）。

---

## 页面路由

| 路径 | 页面 | 说明 |
|------|------|------|
| `/login` | 登录页 | 管理员账户登录 |
| `/home` | 首页 | 状态看板、快速操作 |
| `/friends` | 好友列表 | 查看好友、发送消息、批量建任务 |
| `/tasks` | 定时任务 | 添加/修改/删除任务 |
| `/settings` | 设置 | 登录管理、验证、密码修改、通知 |
| `/logs` | 信息日志 | 运行日志检索 |

---

## License

MIT
