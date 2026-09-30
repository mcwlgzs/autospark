# 抖音火花助手（单用户版）

本机自用的抖音火花助手：登录一次抖音账号，之后由后端按「每天随机时间」自动给指定好友发消息，保持火花不断。

- **后端**：`backend.py`（FastAPI + Selenium 驱动真实 Chrome），默认监听 `127.0.0.1:9844`
- **面板**：`src/`（Vue 3 + Element Plus + Vite），默认跑在 `http://localhost:5173`

---

## 快速开始（Windows）

```powershell
# 1. 启动后端（脚本会自动建 .venv、装依赖、拉起 Chrome）
.\start-backend.ps1

# 2. 另开一个终端，启动面板
npm install
npm run dev
```

然后浏览器打开 **http://localhost:5173**。

默认账号 `admin` / `123456` —— **首次登录后必须先到「设置」页改密码**，
否则后端会拒绝一切浏览器相关操作（绑定账号、发消息、建定时任务），
这是防止面板被别人接管的有意设计。

Linux / macOS 用 `./start-backend.sh`，行为一致。

### 首次使用顺序

1. 首页点「**初始化浏览器**」（会弹出一个 Chrome 窗口，登录抖音必须用它）
2. 设置页**扫码登录**抖音（需要短信 / 刷脸 / 密码二次验证时，按页面提示走）
3. 回首页**刷新好友列表**
4. 勾选好友 → 批量创建**定时任务**（时间可留空，后端会在随机窗口内挑时刻）
5. 先点「**发送预检**」确认能打开会话，再手动发一条试水
6. 到「**信息日志**」确认没有异常

---

## 功能

| 功能 | 说明 |
|---|---|
| 定时发送 | 每个好友一条任务，每天在「目标时间 ± 随机窗口」内发送 |
| 随机窗口 | 默认 ±40 分钟（`SPARK_JITTER_MINUTES`），避免整点机械化 |
| 错过补跑 | 关机 / 崩溃错过的任务，在宽限期内补发（默认 360 分钟） |
| 失败补发 | 发送失败进当日补发队列，默认 45 分钟后重试 |
| 发送预检 | 只打开会话不发消息，用来确认好友在线、页面正常 |
| 签名图 / 图片 | 支持把签名或图片一起发出去 |
| 通知 | 支持 Webhook 推送和邮件（成功 / 失败可选） |
| 防封控 | 发送间隔冷却、发送前风控探测、命中风控自动暂停 |
| 日志 | 面板内可翻页查看 / 清空，失败自动留截图 |

---

## 环境要求

- **Python 3.10+**
- **Chrome 浏览器**（本机实测 Chrome 152.0.7977.84 + selenium 4.49.0；升级 Chrome 后请回归测试）
- **Node.js 18+**（只用来跑面板，后端不需要）

---

## 项目结构

```
spark-web/
├── backend.py            # FastAPI 后端：44 条路由 + 调度线程 + 发送队列
├── spark_core.py         # 纯逻辑层：密码哈希、时间计算、消息模板、风控判定
├── state_store.py        # 原子写 JSON 状态文件（data/state.json）
├── notifier.py           # Webhook / 邮件通知
├── selenium_stealth.py   # 反自动化检测
├── health_check.py       # 环境自检（python health_check.py）
├── start-backend.ps1     # Windows 后端启动脚本
├── start-backend.sh      # Linux / macOS 后端启动脚本
├── start.sh / start.bat  # 上面两个脚本的快捷入口
│
├── src/                  # 面板源码（Vue 3）
│   ├── views/            # Home / Accounts / Friends / Tasks / Settings / Logs / History / Security / Login
│   ├── components/       # DouyinLogin.vue（抖音账号页里的登录/验证区）
│   ├── api/douyin.js     # 后端接口封装
│   ├── stores/           # pinia 状态
│   └── router/
├── dist/                 # npm run build 的产物（nginx 直接托管这个目录）
├── deploy/               # 部署说明、systemd 单元
├── docs/                 # 补充文档
├── tests/                # pytest 用例
├── data/                 # state.json（运行时生成，别提交）
├── logs/                 # app.log、失败截图（运行时生成）
└── chrome-profile/       # 抖音登录态（运行时生成，**不要提交、不要进镜像**）
```

---

## 配置

所有配置走**环境变量**。代码里没有调用 `load_dotenv`，`.env` 文件不会自动读取，
请用 systemd 的 `EnvironmentFile=`、docker 的 `--env-file`，或 shell 里 `set -a; . ./.env; set +a` 导入。
完整清单见 [.env.example](.env.example)，常用的几个：

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | `9844` | 后端端口 |
| `SPARK_DATA_DIR` | `./data` | 状态文件目录 |
| `SPARK_SHOW_BROWSER` | `1` | `0` = 无头（只在登录态已就绪时用，扫码/短信验证必须 `1`） |
| `SPARK_JITTER_MINUTES` | `40` | 随机时间窗（分钟） |
| `SPARK_CATCHUP_GRACE_MINUTES` | `360` | 错过补跑宽限（分钟） |
| `SPARK_RETRY_AFTER_MINUTES` | `45` | 失败补发间隔（分钟） |
| `SPARK_TOKEN_TTL_HOURS` | `72` | 面板登录令牌有效期 |
| `CHROME_PROFILE_DIR` | `./chrome-profile` | 抖音登录态目录 |
| `CHROME_BINARY` / `CHROMEDRIVER_PATH` | 自动探测 | Chrome / chromedriver 路径 |

---

## 接口一览

后端 44 条路由，全部挂在同一个端口上。除 `/healthz` 外都要
`Authorization: Bearer <token>`（token 由 `POST /Api/Login/Admin` 发放）。

- **面板块**：`POST /Api/Login/Admin`、`POST /Api/logout`、`POST /Api/ChangePassword`、`GET /Home`
- **浏览器**：`GET /Api/Init`、`GET /Api/GetInit`、`GET /Api/DieLogin`
- **抖音登录**：`/Api/Pnglogin`、`/Api/GetLogin`、`/Api/login/Init/GetLoginPng`、
  `/Api/login/Init/GetCooker`、`/Api/LoginPhone`、`/Api/LoginPhoneInput`、
  `/Api/Verify/{State,Select,Back,Code,Password}`
- **好友与发送**：`GET /Api/GetFriendsList`、`POST /Api/Send`、`POST /Api/Send/Check`
- **定时任务**：`POST /Time/add`、`POST /Time/del`、`POST /Time/edit`、`POST /Time/test`、`GET /Time/getlist`
- **通知**：`GET /Api/Notify/Get`、`POST /Api/Notify/Set`、`POST /Api/Notify/Test`
- **抖音账号**：`GET /Api/Account/Info`、`POST /Api/Account/Note`（备注，最长 100 字）
- **消息记录**：`GET /Api/History/List`（分页 + 结果/天数/关键词筛选）、
  `POST /Api/History/Clear`（`scope=old` 只清今天以前，`all` 连今天一起清）
- **内容来源**：`GET /Api/Hitokoto/Preview`（取一条一言，给任务表单「取一条试试」用）
- **安全中心**：`GET /Api/Security/Overview`、`POST /Api/Security/RevokeAll`（吊销全部面板会话，要重输面板密码）
- **日志与健康**：`GET /Api/Logs`、`POST /Api/Logs/Clear`、`GET /healthz`（免鉴权）

面板开发时 Vite 会把 `/api` 前缀代理到 `http://localhost:9844`（见 `vite.config.js`）。

---

## 测试

```bash
python -m pytest tests -q          # 247 个用例
python health_check.py             # 环境自检
```

---

## 忘记面板密码怎么办

面板**故意不提供**「忘记密码」自助入口 —— 那等于给在线爆破开了后门。
重置只能在服务器（或你自己的电脑）上、对 `data/state.json` 动手：

```bash
python reset_password.py --show            # 只读：先看现在是什么状态
python reset_password.py                   # 交互式输入新密码（输两遍，不回显）
python reset_password.py --password 你的新密码   # 一步到位（至少 8 位，不能纯数字）
python reset_password.py --allow-weak --password 123456   # 恢复成内置默认密码
```

它会先把 `state.json` 备份成 `state.json.bak-<时间戳>`，写完**立刻回读校验**，
其余字段（定时任务、发送记账、通知配置）原样保留。
`--show` 会告诉你当前密码是「从没设置过（默认 `123456` 可用）」还是
「改过、是自定义密码」，也能识别旧版无盐 SHA-256 哈希。

> 面板登录失败时的提示里会带上这条命令 —— 忘记密码时不用去翻文档。

---

## 部署

对外提供服务时**不要**直接把后端暴露到公网：后端只监听 `127.0.0.1`，
用 nginx 托管 `dist/` 并把 `/api/` 反代到 `9844`。
完整步骤（含 systemd、Docker、certbot）见 [deploy/README.md](deploy/README.md)。

宝塔面板用户可以直接跑一键脚本（幂等，**先用 `--dry-run` 看它要干什么**）：

```bash
sudo bash deploy/baota-install.sh --dry-run
sudo bash deploy/baota-install.sh
```

系统依赖、Chrome、虚拟环境、前端产物、守护配置它都能代劳，
剩下建站 / SSL / 开机自启要在面板里点；手动版步骤见
[deploy/baota-deploy.md](deploy/baota-deploy.md)。

**要备份的东西只有三样**：`data/`、`logs/`、`chrome-profile/`。
其中 `chrome-profile/` 就是抖音登录态，泄露等于账号被接管。

---

## 注意事项

- Chrome 窗口是**故意可见**的：扫码、短信、刷脸验证都需要人工在窗口里完成。
  无头模式只适合「登录态已经完全就绪」的场景。
- 定时任务的时间带随机偏移，所以「22:00 的任务」实际触发时间会在 21:20–22:40 之间，这是防止行为机械化的设计。
- 本工具只应作用于你自己的账号和你自己的好友。请遵守抖音的用户协议；
  因使用本工具导致的封号、限流等后果由使用者自负。
- 默认密码没改之前，后端会拒绝所有敏感操作并在日志里持续告警。
- 面板只有一个固定账号 `admin`（用户名不是秘密，登录页已预填），密码哈希存在
  `data/state.json` 里。忘记了就跑 `python reset_password.py`（见上一节）。

---

## 许可证

MIT
