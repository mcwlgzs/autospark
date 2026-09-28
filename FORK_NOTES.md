# Fork notes

This repository packages the server-tested visible Chrome version of TikTokAutoSparkWeb.

Original upstream: `DkoBot/TikTokAutoSparkWeb`.

## 与上游的差异（基础）

- Uses Chrome/Selenium instead of the original fixed Windows Edge driver path.
- Runs the browser visibly by default so Douyin QR login and SMS verification can be completed manually.
- Supports `CHROME_BINARY`, `CHROMEDRIVER_PATH`, `CHROME_PROFILE_DIR`, and `PORT` environment variables.
- Adds `requirements.txt`, `start-backend.ps1`, `start-backend.sh`, and `LOCAL_RUN.md`.
- Keeps browser profile, local drivers, virtual environments, build output, and passwords out of git.

## 稳定性与可靠性改造

### 调度（原 `schedule` 库已被替换）

旧实现用 `schedule.every().day.at(...)` 注册任务，有三个硬伤：

1. 进程不在那一分钟就永久跳过（重启 / 崩溃 / 升级都会漏发，而漏一天火花就断了）；
2. 调度循环 `while True: schedule.run_pending()` 没有异常隔离，一次异常就把线程
   打死，而且没有任何日志 —— 面板照旧显示任务列表，实际再也不会发；
3. `schedule` 内部无锁，请求线程增删任务会与调度线程并发改 job 列表。

现在改成后端内置 ticker（每 5 秒）+ 发送队列 + 独立 worker 线程：

- **随机时间窗**：实际发送 = 设定时间 + 当天固定的随机偏移（`SPARK_JITTER_MINUTES`，默认 40 分钟）。
  偏移用 `(任务ID, 日期)` 做种子，所以同一天稳定、跨天变化 —— 既不像机器那么规律，
  也不会因为每次重算而无限推迟。
- **错过补跑**：记录每个任务当天的 `last_run_date`，开机后只要还在宽限窗口内
  （`SPARK_CATCHUP_GRACE_MINUTES`，默认 6 小时）就补发当天这一条。
- **当日失败补发**：发送失败且属于临时性故障时，约 45 分钟后
  （`SPARK_RETRY_AFTER_MINUTES`）只对失败的好友补发一次，每天最多一次；
  命中风控 / 登录失效则取消补发（再发只会更糟）。
- **异常隔离**：ticker 与 worker 的每一轮都各自兜异常，任何单次失败都不会让调度停摆。
- **浏览器未就绪不烧额度**：到点时若浏览器还没初始化，任务会等待（日志每 10 分钟提醒一次），
  不写 `last_run_date`、不入队。旧思路下如果直接发出去，只会被记成一次失败，
  等用户几分钟后点了「初始化浏览器」，今天的机会已经没了 —— 火花照样断。
- **开机自动初始化浏览器**：启动时如果已经有定时任务，后台线程自动把 Chrome 拉起来
  （`SPARK_AUTO_INIT_BROWSER=0` 可关闭）。这是「机器重启后不用管」的前提，
  否则必须有人打开面板点一次初始化。
- 任务恢复与调度启动改挂到 FastAPI lifespan，`python backend.py` 与 `uvicorn backend:app`
  行为一致（旧实现只在 `__main__` 里恢复任务，用 uvicorn 启动会全部丢失）。

### 文案

旧实现是「建任务时取一次名言并固定」，于是每天给同一个人发的都是同一句话。
现在 `text` 是**文案池**：一行一条，发送时随机挑一条，支持 `{date}` / `{weekday}` 占位符；
留空则每次发送前现取一条。设置页与任务弹窗都能编辑，改时间不会再顺手把自定义文案覆盖掉。

### 发送记账去重

记账键从「日期 + 好友 + 文案哈希」改成「日期 + 好友」。旧键只要文案一变
（改任务文案、或做每日随机文案）就会绕过当天去重，同一天给同一个人发两次。

### 其他

- 失败截图按「保留 7 天 + 最多 200 张」自动清理；截图文件名带微秒，不再互相覆盖。
- `GetScrlk` 直接用 WebDriver 的 base64 截图，不再往进程当前工作目录写 `temp.png`。
- 浏览器初始化不再负责启动调度；退出时通过 lifespan 关闭 Chrome，避免残留进程。
- `Douyin.friends_xpath_list` 从类属性改为实例属性且每次刷新重建
  （旧实现只增不减，会话重排后会指向别的会话）。
- `AiqingGongyu_text()` 补上 5 秒超时与本地兜底文案（原来是同步路由里无超时的第三方外呼，
  第三方挂住会占死 FastAPI 线程池额度）。
- 浏览器锁改为「等不到就返回 409」而不是无限排队：一次发送最长可达 60 秒，
  旧写法会让按秒轮询的前端把线程池额度占满，连登录接口都拿不到线程，
  表现为「面板忽然登录不上」。
- 删掉了命令行时代遗留的 `Get_Cooke()`（内含死循环 + `driver.close()` + `exit()`，
  在 Web 服务里被误调用会直接结束进程）。

## 安全改造

- `/Api/ChangePassword` 从 GET + query 改为 POST + 请求体：旧写法把新旧密码明文写进
  nginx access log（同文件里 `GetCooker` 早就因此改成 POST，这里当时漏改）。
- 消息正文、好友昵称、手机号、短信验证码（`/Api/Send`、`/Time/add`、
  `/Api/LoginPhone`、`/Api/LoginPhoneInput`、`/Api/Verify/Code`、`/Api/Send/Check`）
  一并从 query 挪到请求体。
- 密码哈希从**无盐 SHA-256** 换成 PBKDF2-HMAC-SHA256（随机盐、20 万次迭代）；
  历史遗留哈希在登录成功时透明升级，不会把任何人锁在门外。
- 面板 token 表改为带签发时间 + 空闲过期（`SPARK_TOKEN_TTL_HOURS`，默认 72 小时）
  + 可一键全部失效：改密码会立刻踢掉所有会话（旧实现是永不过期的内存 set，改密码也踢不掉）。
- CORS 不再用 `allow_origins=["*"] + allow_credentials=True`（矛盾配置，等于对全网开放），
  改为按 `SPARK_CORS_ORIGINS` 白名单且不带 credentials。
- 登录节流只采信来自可信反代的 `X-Real-IP` / `X-Forwarded-For`
  （`SPARK_TRUSTED_PROXIES`）；直连端口时伪造这两个头就能无限重置失败计数的漏洞已封。
- `/Api/login` 的 Cookie 解析改用 `json.loads`，不再用「全文替换 true/false 再 literal_eval」
  （后者会把 cookie 值里的 `true`/`false` 子串静默改坏）。
- `state.json` 落盘设置 0600 权限；落盘失败不再静默吞掉，接口会明确返回保存失败。
- 去掉了 Chrome 启动参数里的 `--disable-web-security`（同源页面用不上，白白削弱沙箱）。
- 新增免鉴权 `/healthz`（只回状态摘要，不含凭据）。

## 工程化

- `requirements.txt` 锁定到实测版本（Selenium 与 Chrome 版本强耦合，浮动版本会让服务直接起不来）；
  新增 `requirements-dev.txt`。
- 抽出 `spark_core.py`：时间归一化、文案池渲染、补跑判定、去重判定、错误分级、
  密码哈希、token 表等纯逻辑集中在这里，**只依赖标准库**，因此可以脱离 Chrome
  和第三方依赖直接单测。
- 新增 `tests/`（标准库 `unittest` 编写，pytest 也能收集）、`pyproject.toml`（pytest + ruff）、
  `.github/workflows/ci.yml`。
- 新增 `Dockerfile`、`docker-compose.yml`、`.dockerignore`、`deploy/spark-web.service`
  与 `deploy/README.md`。
- `start-backend.ps1` / `start-backend.sh` 会先验证 Python 与虚拟环境是否真的可用：
  Windows 上只有 Microsoft Store 占位别名（`python -c` 退出码 9009）、
  或 `.venv` 是从 Linux 拷来的（有 `bin/` 没有 `Scripts/`）时，都会给出中文提示而不是抛莫名错误。

## 审计后的修复（对照 GitHub 同类项目）

完整审计见 `OPTIMIZATION-AUDIT.md`（结论与优先级）和 `AUDIT-browser-layer.md`（浏览器层逐行清单）。
本节记录**本轮已经落地的改动**。

### P0-1 读不到消息列表不再报「发送成功」（最严重）

原来的判定链：消息列表选择器一旦失效 → JS 返回 `{list:false}` → `_confirm_message_delivered`
返回 `nolist` → `Send_Frinder` 发现「输入框被清空」→ **返回发送成功** → 上层写入当天记账。

后果是最坏的一种：**这个人当天被去重跳过，面板显示成功，火花断了却没有任何失败通知。**
失败至少还会通知你，这个连通知都没有。

现在 `nolist` 一律按「未确认」处理：不判成功、保留现场截图、记 `unknown`
（`unknown` 当天不重发，安全方向正确），并明确提示去抖音里确认一下。

### P0-2 登录成功必须有正面证据

`/Api/LoginPhoneInput` 原来用「找不到 `douyin_login_comp_flat_panel/picture`」证明登录成功。
找不到否定证据 ≠ 肯定证据：页面改版、跳转中、元素没渲染完都会让它**谎报登录成功**，
之后所有发送都会失败。现在改为由 `_detect_logged_in_state()` 给出正面证据
（会话列表已渲染、或拿到 `sessionid`），并在等待窗口内重试几次再下结论。

### P0-3 补齐 `@serialized`（并发）

审计发现 **9 个会读写 `driver` 的路由漏了这把锁**，其中两个特别危险：

- `/Api/login/Init/GetLoginPng`：前端每 4 秒轮询一次，会在发送过程中读同一份 DOM；
- `/Time/add`：会调 `douyin.Find_Friends()` 抓会话列表，完全裸奔。

而 WebDriver 跨线程使用是 undefined behavior（Selenium 官方文档与
ThreadGuard 的说明都写明了）。另外 `_scheduler_loop` 也会在 ticker 线程里直接
`execute_script` 探测存活，同样没有保护 —— 见下一条。

`serialized` 现在支持 `@serialized(wait=N)`：慢接口（发短信验证码要等抖音响应）
给足预算，被按秒轮询的接口（`GetInit`、`GetLoginPng`）给短预算，忙就立刻回 409，
不占着 AnyIO 线程池。

### P1-6 存活探测改为心跳，避免卡死调度线程

`_driver_alive()` 会执行一次 `execute_script`，那是真实的 HTTP 调用；
Chrome 卡住时它会一直挂着，而它被 ticker 线程调用 —— **整个调度跟着停摆且没有任何日志**。
现在真实探测只在调度线程每轮 tick 开头做一次（短超时锁，浏览器正忙就不打扰），
其他地方只读心跳。`/healthz` 也改成只读心跳：它免鉴权、而且被 Docker HEALTHCHECK
每 30 秒打一次，绝不该去戳一个可能已经卡死的会话。

### 顺带修掉的隐藏缺陷：`unknown` 被记成 `failed`

记账时判定「结果不确定」用的是 `'未确认' in reason`，而实际文案是「**未能**确认…」。
匹配不上就会把"结果不确定"记成 `failed`，当天再补发一条 —— 而那条消息可能已经送达了。
现在这条规则收进 `spark_core.send_status_from_reason()`，
用 `UNCONFIRMED_MARKERS` 明确列出所有"不确定"的说法，两个调用点都改成走它。

### 测试

- 新增回归用例覆盖上面每一条，并且**逐个验证过它们真的能失败**：
  把对应修复改回去，用例必须变红（`nolist` 2 条、登录 1 条、锁 1 条、状态归类 1 条）。
  这一步是必要的 —— 第一版 `nolist` 用例只调了 `_chat_probe`，
  缺陷改回去它依然全绿，等于没测。
- 修掉一个**依赖墙上时钟的偶发失败**：`test_task_fires_once_at_planned_time`
  原来用「now + 31 分钟」表示"到点之后"，而任务时刻是 HH:MM 精度、
  还会叠加随机窗口 —— 实测 23:29 触发时两者会落在**同一秒**，用例随机变红。
  现在改成从 `_task_view()` 读框架自己算出的 `next_run` 再断言，任何时刻都成立。

### 前端

- **Element Plus 改为按需引入**（官方推荐的 `unplugin-vue-components` +
  `ElementPlusResolver`）。原来 `main.js` 是 `app.use(ElementPlus)` + 注册全部图标 +
  引入整份 `index.css`，实测产物里那个 1.1 MB 的 chunk 和 344 KB 的 CSS 基本都是这么来的。
  实测：JS 1404 KB → 631 KB，CSS 344 KB → 196 KB。
- 按需引入会丢掉原来的全局语言包配置，所以用 `<el-config-provider :locale="zhCn">`
  包住路由出口（否则分页器会退回英文）。
- `manualChunks` 用**函数**写法：Vite 8 底层是 rolldown，传对象会直接构建失败。
- `index.html` 标题从 `admin` 改成「抖音火花助手」，`lang` 改成 `zh-CN`。

## UI 走查（截图后修的）

为了不只凭源码评价界面，本轮把面板真正跑起来逐页截图看过。
工具留在 `.home/` 里（`.home/` 本来就在 `.gitignore`，不进版本库）：

- `.home/ui-mock-server.mjs` —— 用 Node 起一个假后端，**响应体直接从 `backend.py` 源码里
  抽出来求值**，所以不会和真实接口的形状漂移；同时伺服 `dist/`。
- `.home/capture-ui.mjs` —— 用本机已装的 Chrome（`channel: 'chrome'`，不用下浏览器）
  逐页截图，并收集 console / 网络错误。

```powershell
npm run build
node .home\ui-mock-server.mjs 5199
node .home\capture-ui.mjs http://127.0.0.1:5199 .home/shots
```

第一次截图就跑出 6 个页面的真实样子，并暴露了下面这几个**只靠读代码不容易发现**的问题：

### 修掉的

1. **版本号写死且是错的**：`Home.vue` 硬编码 `v1.0.0`，而 `backend.py` 里 `VERSION = '1.1.0'` ——
   面板一直在报一个不存在的版本。现在从免鉴权的 `/healthz` 读 `data.version`，取不到显示 `--`。
2. **「API 地址 / 前端端口」写死**：硬编码 `http://127.0.0.1:9844` 和 `5173`。
   Docker 换端口、nginx 反代、换域名之后这两行全是错的；而且后端本来就只监听
   `127.0.0.1`、从不直接对外，标成"API 地址"也容易误导。现在合成一行「面板地址」，
   取 `window.location.origin`。
3. **状态自相矛盾**：首页卡片显示「已初始化」+ 绿点，同一屏的"系统信息"却显示「待初始化」
   （它是 `browserStatus && loginStatus` 合成的一句）。现在拆成「浏览器状态」「抖音登录」
   两个独立标签 —— 浏览器就绪但没登录，本来就不是"待初始化"。
4. **登录页几乎看不清**：玻璃卡片是 `rgba(255,255,255,0.15)`，压在明亮的雨滴照片上，
   白色标题和副标题的对比度不够（截图里基本糊成一片）。现在给背景叠一层深色渐变、
   卡片换成深色半透明底并提高 blur 与描边，文字加投影 —— 保留了原来的质感，
   但标题清晰可读。

### 看过但**没有**改的（记录在此，供后续判断）

- 侧边栏底部「GitHub 项目」和设置页「调试功能」区块在 950px 高视口下贴着/超出底边。
  实际浏览器里可以滚，属于布局取舍，不确定是否刻意，所以没动。
- 好友头像在 mock 里是占位图（我用的抖音图床假 URL 加载不出来），无法据此判断线上表现。
  若是真实存在，建议给 `el-avatar` 加 `@error` 回退。
- 「好友数量 / 定时任务」在首页卡片上显示 `0`，而好友列表页有数据 —— 这是 mock 的
  数据形状与首页缓存策略共同造成的，**不是**已确认的缺陷，需要连真实后端复现再判断。

## 新增能力：发送文昌帝君灵签图文

用户要求「能发图片」并指定用文昌帝君灵签接口（`GET /api/wenchangdijunrandom`，
返回 `title` / `poem` / `content` / `pic` 直链）。面板与定时任务现在都能发一张签图
+ 一段签文文字。这不是"顺手加个 upload"：原实现里所有发送路径都假设「消息就是一段文字」，
有三处会直接坏掉，逐条说明为什么这么改。

### 1）送达确认不能靠「气泡里有这段文字」

原判据是 `CHAT_OUTGOING_PROBE_JS` 里「新出现的、方向为自己的气泡，且 innerText 含目标文本」。
图片气泡没有文本，照旧判就是永远 `unconfirmed` —— 而 `_history_mark` 只按（日期 + 好友）
记账，一旦记成未确认，这位好友当天就不会再发。所以新增一套独立的
`CHAT_IMAGE_PROBE_JS`（标记 `data-spark-img-seen`，与文字的 `data-spark-seen` 分开，
互不污染）：判据换成「未标记的气泡里出现了渲染尺寸 ≥80×80 的 `img`/`canvas`」，
`state` 仍走 `failed`（重试标记）/ `pending`（转圈）/ `clean` 三态，
方向（自己发的）判据与文字版共用同一段逻辑。

### 2）图片不能进 state.json，也不能让「一天一条」变成两条

`state.json` 是 JSON 原子写，塞 base64 图片会把它撑爆。所以图片只落盘到
`data/images/<sha1(URL+内容)[:20]>.<ext>`，记账里只写一句摘要
（`image_record_text()` → 正文 + `[图片] 文昌帝君灵签 · 某签`）。
图片和文字是同一次 `Send_Frinder` 调用里的两条消息（图先、文后），路由只调一次
`send_guard` / `_history_mark`，所以「每位好友每天恰好一条」的承诺不受影响。

### 3）上传走 file input 注入，不走剪贴板

选 Selenium 对隐藏 `input[type=file]` 直接 `send_keys(本地路径)`：剪贴板方案要
pywin32 + Pillow，且 headless / 远程桌面下更脆。抖音的 input 是隐藏的，所以
`CHAT_REVEAL_FILE_INPUT_JS` 临时把它显示成 2×2 像素（原 style 存进 `data-spark-style`，
成功后 `CHAT_RESTORE_FILE_INPUT_JS` 还原）；找不到 input 就先点一次图片按钮再重扫。

### 明确的功能取舍（都是刻意的）

- **图片失败不重试**：文字路径失败时会换三种触发方式再确认，图片路径**不**这么做 ——
  图片已经挂在输入区，再按一次回车有把同一张图发两遍的风险，重复发图比晚点发更难解释。
- **定时任务里图片失败退化成纯文字**：定时任务的第一目标是「火花不能断」，
  签文接口挂了或签图下载失败都不该整天不发；退化成文字时把 `未送达：<原因>`
  写进发送明细，通知文案也跟着改（不会把「图没送出」说成「灵签（图文）」）。
- **手动发送时取不到签文就报错，不换随机文案**：用户明确点了「灵签」，
  静默换成一句随机名言是骗人。面板收到 404 提示后可以自己决定是否改发普通文案。
- **图片地址只允许公网 http(s)**：地址来自第三方接口，面板又可能被远程访问，
  这里复用推送地址那套 `private_host_reason()` 挡掉本机/内网地址；
  再校验 Content-Type（可缺省）、文件魔数（JPG/PNG/GIF/WEBP）与大小上限
  （边读边判，避免超大盘撑爆内存）。先写 `.part` 再 `os.replace`。
- **`/Time/edit` 的 sign 只在显式传了才覆盖**：`sign: false` 必须能把一条灵签任务
  改回普通任务，所以「没传」与「传了 false」在接口层是两件事
  （`sign_given = ('sign' in body) or (sign is not None)`）。
- **灵签任务里写了自定义文案时，用户写的优先（签图照发）**：旧写法是
  `content = render_sign_text(sign_data) if sign_data else ''`，于是只要勾了灵签，
  任务里配的文案池就被静默丢掉 —— 和面板上「消息内容留空则用当天签文」的说明正好相反。
  现在统一成：留空 → 当天签文；写了 → 用你写的。`/Time/test` 的试发走同一条规则，
  否则「试发过了」和真正到点发出去的内容可能不是一回事。

### 试发：让这条新链路当场可验证

图片这条路能不能走通，取决于抖音当时的 DOM（`input[type=file]` 在不在、上传后
会不会渲染预览、气泡里有没有足够大的图），这些都不是单元测试能覆盖的。而定时任务只在
设定时间 + 0~N 分钟随机窗口触发，想验证就只能干等一天。所以加了两个入口：

- **好友页发送弹窗**：勾了「灵签」之后多一个「试发内容」选择 —— `签图 + 签文文字`
  或 `只发签图`（后者对应 `/Api/Send` 的 `image_only`，后端会把文案清空，
  连用户填的字也不带出去）。旁边写清了「这是真实发送、会占用对方今天的发送记录」。
- **任务页每行「试发」按钮** → `POST /Time/test`：按该任务**当前配置**立刻真发一条
  （灵签任务就是签图 + 当天签文，普通任务留空文案就用 `_resolve_text()` 现取一条语录），
  用于验证「这条任务现在到底能不能发出去」。点之前有确认弹窗，结果（含失败原因）直接回面板。

记账口径是刻意这样定的，三条都写进了接口注释：

1. 用 `scope='manual'` 而不是 `'task'`：试发的目的就是「现在试一下」，
   不能被「今天已经发过」挡住 —— 否则已经收过今天消息的好友根本试不出来；
2. **不写 `last_run_date`**：今天到点的那次任务照常执行，试发不该把当天的额度烧掉；
3. 但试发成功会写进（日期 + 好友）的发送记录，于是那次正常发送会被判成「今天已经发过」
   而跳过。这正是「每位好友每天恰好一条」的本意，所以返回文案里把这句话明确告诉了用户。

顺带把 `/Api/Send` 的发送主体抽成了 `_send_now()`（手动发送与立即试发共用同一段记账逻辑，
差别只有 `scope`、日志分类 `发消息`/`试发` 和成功文案），避免两条路径的记账口径漂移。

### 新增代码与测试

- `spark_core.py`：`image_kind` / `image_payload_error` / `render_sign_text` /
  `sign_summary` / `image_record_text`（纯标准库，不 import selenium）。
- `backend.py`：`fetch_wenchang_sign` / `_bool_flag` / `_prune_dir` / `download_image`、
  图片上传与探测一段、`Douyin._send_image_in_chat`、`Send_Frinder(..., image=)`、
  `_send_now`（手动发送与试发共用）、`/Time/test` 路由。
- 新增 46 个用例（`tests/test_core.py` 的 `TestImagePayload` 4 个 / `TestSignText` 5 个 +
  `tests/test_backend_scheduler.py` 的 `ImageSendTestCase` 37 个）：覆盖内网地址拒绝、
  Content-Type 与魔数、去重落盘与 `.part` 残留、超限、接口 4 种失败形状、
  「上传失败绝不回车」「结果不确定只按一次回车」、灵签开关落盘与显式关闭、
  定时任务退化成纯文字、通知不谎报图文、「没勾灵签时绝不请求灵签接口」、
  灵签任务里自定义文案优先且试发与真发同内容，
  以及只发图与立即试发的记账口径（不写 `last_run_date`、但要写当天记录；
  试发成功后台账会拦住今天那次正常发送；「只发图却没图」直接报错而不是改发文字）。**原有语录通道**也补了回归：
  `AiqingGongyu_text()` 接口成功 / 返回空 / 抛异常三条分支，以及
  `SPARK_REMOTE_QUOTE` 开关只影响「留空文案」（用户自己写了文案时绝不请求第三方接口）。
  全套 `193 tests OK`。

### 未验证的部分（如实说明）

没有连真实抖音页面联调过：`input[type=file]` 的 XPATH 与图片气泡的 DOM 判据
（渲染尺寸 ≥80×80、blur 预览等）都是按抖音 web 版常见结构写的，**首次真机发送需要人工看
一次失败截图**。若选择器失效，`_save_failure_shot('send-image-failed')` 会留下现场，
调整点集中在 `CHAT_FILE_INPUT_XPATHS` / `CHAT_IMAGE_BUTTON_XPATHS` / `CHAT_IMAGE_PROBE_JS`
三处。现在面板上有「试发」按钮，这一步可以随时人工复验（先拿一个不重要的好友试）。
另外按要求，本次没有 `git commit`。
