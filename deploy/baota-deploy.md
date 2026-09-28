# 宝塔面板部署（Debian / Ubuntu 优先）

面向「用宝塔面板把面板挂到一台 Linux 服务器上长期无人值守」的场景。
如果你是纯 SSH 部署，看 `deploy/README.md`；本地开发看 `LOCAL_RUN.md`。

> 一句话结论：**后端用「进程守护管理器（Supervisor）」常驻，前端 dist 交给宝塔站点，
> nginx 只反代 `/api/` 和 `/healthz`，9844 端口绝不对外。**
> 后端自带定时调度，**不需要**再建宝塔计划任务去定时发送。

---

## 0. 环境要求

| 项 | 建议 |
| --- | --- |
| 系统 | **Ubuntu 22.04 / Debian 12**（CentOS 7 的 yum 源已 EOL，装 Chromium 很折腾） |
| 内存 | 2G 起。Chromium 常驻约 500MB~1G；1G 内存请先在宝塔里加 2G swap |
| 时区 | **必须 `Asia/Shanghai`**，否则「每天 22:00」会变成北京时间早上 6 点 |
| Python | 3.10+（推荐 3.11），可来自系统，也可用宝塔的 Python 项目管理器 |
| 浏览器 | `chromium` + `chromium-driver`（两者大版本必须一致） |

时区确认与修正：

```bash
timedatectl                      # 看 Time zone 是不是 Asia/Shanghai
timedatectl set-timezone Asia/Shanghai
```

---

## 1. 装系统依赖（Chromium + 驱动 + 虚拟显示 + 中文字体）

```bash
apt update
apt install -y chromium chromium-driver xvfb fonts-noto-cjk
```

四样东西各有用途，缺一个都会以很难懂的方式失败：

- `chromium` / `chromium-driver`：浏览器和驱动。**Selenium 与浏览器版本强耦合**，
  升级时必须两个一起升（`apt install chromium chromium-driver` 一起执行即可）。
- `xvfb`：服务器没有桌面，靠它提供一块虚拟屏幕。本项目用的是「可见」Chrome，
  没有显示环境时后端会直接回 `初始化失败: 未找到可用的 DISPLAY/Xvfb 环境`。
- `fonts-noto-cjk`：缺了它页面上的中文全是方框，好友昵称和验证提示根本没法看。

装完立刻验证（这一步别省）：

```bash
/usr/bin/chromium --version
/usr/bin/chromedriver --version     # 大版本号要和上面一致
xvfb-run --help >/dev/null && echo xvfb-ok
```

**Ubuntu 的坑**：`apt install chromium-browser` 装的是 snap 包装器，
在服务器上经常起不来（报 `Failed to connect to bus` 之类）。
请用 `chromium` 这个包名（Debian/Ubuntu 22.04+ 的 universe 源里有真包），
或者去 Google 下 `.deb` 装 `google-chrome-stable`，
并把 `CHROME_BINARY` 指到 `/opt/google/chrome/chrome`、
`CHROMEDRIVER_PATH` 指到与之版本匹配的驱动。

---

## 2. 放代码并准备可写目录

宝塔 → 文件 → 在 `/www/wwwroot` 下新建 `spark-web`，把项目传上去
（或用宝塔的 Git 部署功能拉仓库）。

```bash
cd /www/wwwroot/spark-web
mkdir -p data logs chrome-profile
```

- `data/`：`state.json`（登录态、定时任务、发送记账）
- `logs/`：`app.log`、`backend.log`、`shots/`（失败截图）
- `chrome-profile/`：Chrome 用户目录，**扫码一次就能长期复用登录态**

这三个目录是全部身家，删了就要重新扫码。`.gitignore` 已经把它们排除在仓库外。

### 2.1 从 Windows 打包上传（白名单，别整个文件夹拖上去）

`node_modules/`（装的是 Windows 原生二进制，Linux 上根本用不了）、`.venv/`、
`.git/`、各种缓存目录都不该上传；而 `dist/`、`data/`、`chrome-profile/`
是**必须**带上的。用下面这条白名单命令打包，产出的 zip 通常只有十几 MB：

```powershell
# 在项目所在目录（spark-web 的上一级）执行
$items = @(
  'backend.py','spark_core.py','state_store.py','notifier.py',
  'requirements.txt','start-backend.sh','index.html',
  'package.json','package-lock.json','vite.config.js',
  'src','public','dist','deploy',
  'data','chrome-profile'      # 登录态：带了就不用在服务器上重新扫码
)
Compress-Archive -Path ($items | ForEach-Object { ".\spark-web\$_" }) `
                 -DestinationPath .\spark-web-upload.zip -Force
```

上传后在服务器上解包（宝塔 → 文件 → 上传 → 解压到 `/www/wwwroot/spark-web`），
然后**必须补一次可执行权限和换行符检查** —— Windows 传过去的 `start-backend.sh`
经常丢掉 x 位，而被 CRLF 破坏过的脚本会报
`/bin/bash^M: bad interpreter: No such file or directory`：

```bash
cd /www/wwwroot/spark-web
chmod +x start-backend.sh
file start-backend.sh        # 应显示 "ASCII text"，出现 "with CRLF line terminators" 就跑：
sed -i 's/\r$//' start-backend.sh
mkdir -p data logs chrome-profile
```

> 走 Git 部署（宝塔 Git 拉仓库）的话不用管这些：`.gitattributes` 已经把这几个
> 脚本锁成 LF。但 `data/`、`chrome-profile/` 在 `.gitignore` 里，
> **必须另外单独传一次**，否则服务器上是空的，要重新扫码登录。

---

## 3. 准备前端产物

`dist/` 也在 `.gitignore` 里，所以服务器上要么自己构建，要么从本地传上去。

**做法 A（推荐，快）**：本地 `npm ci && npm run build`，
把生成的 `dist/` 整个目录上传到 `/www/wwwroot/spark-web/dist`。

**做法 B（服务器上构建）**：宝塔 → 软件商店 → Node 版本管理器，装 Node 18+，然后：

```bash
cd /www/wwwroot/spark-web
npm ci
npm run build
```

---

## 4. 准备 Python 并先手动跑通一次

宝塔 → 软件商店 → **Python 项目管理器** → 安装 3.11。
它的解释器一般在 `/www/server/pyporject_evn/<版本>/bin/python3`（用 `ls` 确认实际路径）。

我们特意给启动脚本留了 `SPARK_PYTHON` 口子，所以**面板里的 Python 可以直接用，
不需要改脚本、也不用去动系统 PATH**：

```bash
cd /www/wwwroot/spark-web

# 用系统的 python3（先确认 python3 -V >= 3.10）
bash start-backend.sh

# 或者用宝塔装的 Python
SPARK_PYTHON=/www/server/pyporject_evn/3.11/bin/python3 bash start-backend.sh
```

首次会自动建 `.venv` 并装依赖（要能访问 PyPI，装不上就先配宝塔的 pip 镜像源）。
看到下面这几行就算成功：

```text
[spark-web] 使用 Python 3.11.x（/www/.../bin/python3）
[spark-web] 当前没有 $DISPLAY，改用 xvfb-run -a 在虚拟显示里启动（适合服务器 / systemd）。
...
服务已启动（版本 1.1.0，退出请使用 Ctrl-C，会自动关闭浏览器）
Uvicorn running on http://127.0.0.1:9844
```

`Ctrl+C` 退出，接下来交给守护进程。

---

## 5. 常驻守护（三种方式，**只选一种**）

> ⚠️ 同一个 `chrome-profile/` 与 `state.json` 只能有一个实例在用。
> 同时开两份会出现两个 Chrome 抢同一个用户目录、以及同一条消息被发两次。

### 方式 A：进程守护管理器 / Supervisor（推荐）

宝塔 → 软件商店 → 安装「进程守护管理器」（旧版叫 Supervisor 管理器），添加守护进程：

| 字段 | 值 |
| --- | --- |
| 名称 | `spark-web` |
| 运行目录 | `/www/wwwroot/spark-web` |
| 启动用户 | `root`（或 `www`，但要先把项目目录 `chown -R www:www`） |
| 进程数量 | **1** |
| 启动命令 | 见下 |

```bash
SPARK_PYTHON=/www/server/pyporject_evn/3.11/bin/python3 \
CHROME_BINARY=/usr/bin/chromium \
CHROMEDRIVER_PATH=/usr/bin/chromedriver \
bash /www/wwwroot/spark-web/start-backend.sh
```

勾选「开机自启」。`start-backend.sh` 在检测不到 `$DISPLAY` 时会自己套一层 `xvfb-run -a`，
所以这里不用手写 Xvfb。

显式指定 `CHROME_BINARY` / `CHROMEDRIVER_PATH` 是有意的：
容器和部分发行版里 Selenium 会尝试联网下载匹配的驱动（`selenium-manager`），
服务器没有外网出口时就会卡在这里失败。

### 方式 B：Python 项目管理器

新版面板支持「自定义启动命令」时，把方式 A 的那条命令原样填进去即可（端口 9844、开机自启）。

如果面板只让你选「启动文件」而不给自定义命令，那它**不会自动套 Xvfb**，
需要你自己先起一个虚拟屏，或者干脆换成方式 A/C。

### 方式 C：systemd（面板不参与，SSH 操作）

用仓库里现成的单元文件，步骤见 `deploy/spark-web.service` 顶部注释。
它同样用 `xvfb-run` 包了一层，并且退出时会真正关掉 Chrome 进程。

### 常用环境变量（填到启动命令前面，或写进启动脚本顶部 `export`）

| 变量 | 建议 | 说明 |
| --- | --- | --- |
| `SPARK_JITTER_MINUTES` | `40` | 计划时间后再随机推迟 0~40 分钟（降低机器特征） |
| `SPARK_CATCHUP_GRACE_MINUTES` | `360` | 错过当天时间点后 6 小时内补跑 |
| `SPARK_RETRY_AFTER_MINUTES` | `45` | 失败后 45 分钟只对失败好友补发一次 |
| `SPARK_TOKEN_TTL_HOURS` | `72` | 面板登录状态空闲多久失效 |
| `SPARK_AUTO_INIT_BROWSER` | `1` | 启动时若有任务就自动拉起浏览器（无人值守靠它） |
| `TZ` | `Asia/Shanghai` | 决定「22:00」到底指几点 |

后端默认监听 `127.0.0.1:9844`，不要试图改它的监听地址 —— 对外只走 nginx。

---

## 6. 建站 + 反向代理（最容易出错的一步）

### 6.1 建站

宝塔 → 网站 → 添加站点：

- 域名：`spark.example.com`
- 根目录：`/www/wwwroot/spark-web/dist`
- PHP 版本：**纯静态**
- 伪静态：选 **vue** 模板（宝塔自带，作用是 SPA 回退到 `index.html`，
  否则刷新 `/friends` 这类前端路由会 404）

### 6.2 手改配置文件加反向代理

网站设置 → 配置文件，在 `server { ... }` 里加上：

```nginx
    # 后端路由是 /Api/... 、/Time/...，而前端 axios 的 baseURL 是 /api，
    # 所以这里必须把 /api 前缀剥掉 —— proxy_pass 结尾那个 "/" 绝对不能省：
    #   /api/Api/Logs  ->  http://127.0.0.1:9844/Api/Logs
    # 少了它就会变成 /api/Api/Logs，后端全部 404。
    location /api/ {
        proxy_pass http://127.0.0.1:9844/;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # 初始化浏览器 / 扫码登录可能要几十秒，别让 nginx 先超时
        proxy_read_timeout 180s;
        proxy_send_timeout 180s;
    }

    # 免鉴权探活接口，方便宝塔监控或外部拨测
    location = /healthz {
        proxy_pass http://127.0.0.1:9844/healthz;
        access_log off;
    }
```

保存即可（宝塔会自动 reload）。

**不要**用宝塔的「反向代理」界面直接代理 `/`：
那会把静态前端也一起代理到后端，页面直接打不开。

### 6.3 X-Real-IP 的说明

面板的「登录失败 5 次锁定」是按来源 IP 计的，而后端只采信来自可信反代的
`X-Real-IP`（默认 `127.0.0.1,::1`）。宝塔的 nginx 与后端在同一台机器上，
上面的配置已经用 `$remote_addr` 覆盖了该头，**不用改**。
（如果你在中间又套了一层代理，要把那层地址加进 `SPARK_TRUSTED_PROXIES`。）

---

## 7. HTTPS 与安全

1. 网站设置 → SSL → Let's Encrypt 申请证书 → 打开「强制 HTTPS」。
   面板密码是 POST 明文提交的，没有 TLS 等于裸奔。
2. 宝塔 → 安全（系统防火墙）：只放行 `80`、`443`。
   **不要放行 9844** —— 后端只监听 `127.0.0.1`，放行了也访问不到，
   反而容易让人误改成监听 `0.0.0.0` 把面板直接暴露在公网。
3. 首次登录后立刻改掉默认口令 `admin / 123456`（现在强制 ≥8 位且不能是纯数字）。
   改完密码会强制所有会话重新登录，这是刻意设计。
4. 想再加一层：宝塔「网站 → 访问限制」里配 `allow/deny` 只放自己的 IP，
   或者套一层 Basic Auth（与后端自己的登录互不冲突）。

---

## 8. 备份（宝塔计划任务）

宝塔 → 计划任务 → 备份目录，建议每天一次：

| 备份内容 | 说明 |
| --- | --- |
| `/www/wwwroot/spark-web/data` | `state.json`：定时任务、发送记账、密码哈希、推送配置 |
| `/www/wwwroot/spark-web/chrome-profile` | 登录态主要在这里，丢了要重新扫码 |
| `/www/wwwroot/spark-web/logs` | 可选，排查问题用 |

保留 7 天即可。**不要**把这三个目录传到公开仓库或分享 —— 里面有登录凭据。

> 再强调一次：**不需要**建「定时发送」的宝塔计划任务。
> 后端自带 ticker（随机窗口 + 错过补跑 + 当日补发），
> 而且只应该有一个实例在跑。

---

## 9. 验收清单

```bash
# 1. 后端在跑，且只监听本机
ss -lntp | grep 9844            # 应该是 127.0.0.1:9844，不是 0.0.0.0

# 2. 探活接口
curl -fsS http://127.0.0.1:9844/healthz   # {"code":200,"data":{"status":"ok",...}}

# 3. 经 nginx 的完整链路
curl -fsS https://spark.example.com/healthz
```

浏览器打开 `https://spark.example.com`：

1. 用 `admin / 123456` 登录 → 立刻改密码；
2. 首页点「初始化浏览器」（若已配置 `SPARK_AUTO_INIT_BROWSER=1` 且已有任务，它会自己起）；
3. 设置页完成扫码登录（要二次验证时按提示选短信/刷脸/密码）；
4. 好友列表刷新 → 勾选好友 → 批量创建定时任务；
5. 点一次「发送预检」确认能打开会话，再手动发一条；
6. 到「信息日志」确认无异常，之后就不用管了。

---

## 10. 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| `初始化失败: 未找到可用的 DISPLAY/Xvfb 环境` | 没装 xvfb，或没通过 `start-backend.sh` / `xvfb-run` 启动 |
| `SessionNotCreatedException` | `chromium` 与 `chromium-driver` 版本不一致；两个一起 `apt install` 重装 |
| 页面能打开，但所有接口 404 | `/api` 前缀没剥掉：`proxy_pass http://127.0.0.1:9844/;` 结尾的 `/` 漏了 |
| 登录后过一会儿全部 401 | 会话空闲超过 `SPARK_TOKEN_TTL_HOURS`，或刚改过密码（会清空全部会话）。重新登录即可 |
| 定时任务到点没发消息 | 看「信息日志」：若提示「浏览器还没初始化」，去首页点初始化；重启服务也会自动初始化 |
| 到点了但没发，且日志提示等浏览器 | 同上；确认 `SPARK_AUTO_INIT_BROWSER` 没被设成 0 |
| 发送时间不是整点 | 正常：实际发送 = 设定时间 + 随机窗口（`SPARK_JITTER_MINUTES`） |
| 「22:00」在早上 6 点执行 | 服务器时区不是 `Asia/Shanghai` |
| 同一条消息发了两次 | 检查是否同时跑了两份实例（Supervisor 与 Python 项目管理器都开了） |
| 扫码后仍显示未登录 | 抖音要求二次验证；到设置页完成短信/刷脸/密码验证 |
| 服务器 IP 频繁触发安全验证 | 机房 IP 比家宽更容易被风控：调大随机窗口、减少好友数、别频繁手动测试 |
