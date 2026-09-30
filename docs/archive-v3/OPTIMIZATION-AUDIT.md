# 优化审计报告（对照 GitHub 同类项目）

审计日期：2026-09-20
审计对象：本仓库（`TikTokAutoSparkWeb-visible-chrome`，upstream `DkoBot/TikTokAutoSparkWeb`）
方法：三路并行 —— ① GitHub 同类项目与自动化栈调研；② FastAPI/Selenium 现代实践调研；③ 本仓库浏览器层静态审计。
标注约定：**[已核实]** = 本次直接读了源码/原始来源；**[推断]** = 基于经验的判断，未逐条实测。

---

## 一、结论先说

**这个仓库的工程化程度已经明显高于同类项目**（定时补跑、去重记账、PBKDF2、token 过期、
纯逻辑层单测、Docker/systemd、CI），对照的 9 个同类项目里没有一个做到这个程度。

但有一个**致命问题**：产品的核心承诺是「每位好友每天恰好一条」，而当前代码在**选择器失效时会静默记成"发送成功"**，
然后因为当天去重直接跳过，火花断掉且面板显示一切正常。
这比"发失败"严重得多——失败至少会通知你。

优先级最高的事不是换框架，而是**把"发送成功"的判定改严**。

---

## 二、同类项目现状（对照参考）

| 项目 | ★ | 最后更新 | 技术栈 | 值得借鉴的点 |
|---|---|---|---|---|
| [DkoBot/TikTokAutoSparkWeb](https://github.com/DkoBot/TikTokAutoSparkWeb)（我们的上游） | 90 | 2026-04-23 | Vue3 + FastAPI + Selenium | **已停滞约 5 个月**，2 个 open issue，MIT 未归档 |
| [halfwaystudent/douyin-sparkflow](https://github.com/halfwaystudent/douyin-sparkflow) | 501 | 2026-09-19 | Playwright + FastAPI + Docker + noVNC | **同类最强**：私信走私有协议；`statusCode` 发送状态枚举；稳定身份寻址 |
| [2061360308/DouYinSparkFlow](https://github.com/2061360308/DouYinSparkFlow) | 379 | 2026-09-20 | Playwright + chrome-headless-shell | 已从创作者中心私信迁到 `www.douyin.com/chat`；README 明确"GH Actions 会被踢下线" |
| [unmev/douyin-auto-fire](https://github.com/unmev/douyin-auto-fire) | 323 | 2026-09-12 | GitHub Actions | 无服务端，纯定时 |
| [bling-yshs/douyin-auto-spark](https://github.com/bling-yshs/douyin-auto-spark) | — | — | Playwright + TS | 描述直接写着**【已被风控失效】**，README 里 GH Actions「基本不可用」 |
| [dr-190/ScriptCat-Douyin-Fire-Helper](https://github.com/dr-190/ScriptCat-Douyin-Fire-Helper) | 121 | 2026-08-25 | 油猴脚本（跑在用户真实浏览器里） | **反检测最强**：没有 driver，就是本人浏览器 + 外部调度回调 |
| [cv-cat/DouYin_Spider](https://github.com/cv-cat/DouYin_Spider) | 3101 | 2026-09-19 | 私有 API（WebSocket 收发私信） | 私信 API 路径 |
| [HRuiCcc/dy-xuhuohua](https://github.com/HRuiCcc/dy-xuhuohua) | 11 | 2026-09-08 | **FastAPI + Playwright + APScheduler** | 架构上离我们最近的一个 |

同源 fork：`shouwei-Byte/…-visible-chrome`（6★，就是本仓库）、`Aprivity/…-Linux`(2★)、`bxiaoan/…-docker`(1★)、`DaVinci-hu/…`(0★)。
**结论：这个赛道没有赢家，上游已停滞，我们完全有条件做成最好用的那个。**

### 行业共识（[已核实：各项目 README/源码]）

1. **反检测排序**：脚本注入真实浏览器 > 本地可见浏览器 > Docker/无头 > GitHub Actions。
   `bling-yshs` 被风控失效、`2061360308` 报告 Actions 会话被踢 —— 我们"本机可见 Chrome"的选择是**正确**的。
2. **不要用无头/Actions 跑**：我们默认 `SPARK_SHOW_BROWSER=1` 也是**正确**的。
3. **发送结果要有明确状态码**，不能靠"按钮点了就算发了"（`douyin-sparkflow` 的 `statusCode` 枚举：
   `0 Succeeded / 1 UserNotInConversation / 2 CheckConversationNotPass / 3 CheckMessageNotPass / 5 UserHasBeenBlock`）。
4. **寻址要用稳定身份**（`secUid`/`peerUserId`），昵称只能当最后手段，重名要报 `ambiguous_target` 而不是猜
   —— `protocol_sender.mjs` 的 `protocol_targets_cache` [已核实]。

---

## 三、浏览器自动化栈：是否该换掉 Selenium

| 方案 | ★ | 维护状态 | 迁移成本 | 关键优势 |
|---|---|---|---|---|
| Selenium 4.49（现状） | — | 活跃 | — | 现状；**chromedriver 与 Chrome 版本强耦合**（我们 requirements.txt 里专门写了这条风险） |
| [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright) | 4,640 | 2026-09-13 | **低** | Playwright 的 drop-in 替代，仍接受 `xpath=`；自动等待；自带浏览器无需 driver；补掉 `Runtime.enable` 等自动化特征 |
| [DrissionPage](https://github.com/g1879/DrissionPage) | 12,467 | 2026-09-14 | 中 | 不用 webdriver、不用匹配 driver；**可接管已打开的浏览器**；自带自动等待/重试 |
| [nodriver](https://github.com/ultrafunkamsterdam/nodriver) | 4,757 | 2026-05-13 | 高 | 无 driver；但 **AGPL-3.0**，对要分发的 fork 是许可风险 |
| [undetected-chromedriver](https://github.com/ultrafunkamsterdam/undetected-chromedriver) | 12,847 | **2025-07-05** | — | **事实停维护**（issues 已关闭，1141 个未处理）——不要选 |
| [Camoufox](https://github.com/daijro/camoufox) | 12,023 | 2026-09-14 | 高 | C++ 层指纹伪装；但作者自己写"维护断档一年…不建议生产使用" |

**建议（[推断]）**：**不急着换**。真正让我们痛的不是 Selenium 本身，而是：
① 选择器全写死（换任何框架都救不了）；② 发送确认逻辑不成立（跟框架无关）。
先把这两件事修好。**若之后要换，选 patchright**——迁移成本最低且能彻底摆脱 chromedriver 版本矩阵。

另外，私有协议（`imapi.douyin.com` + `frontier-im.douyin.com/ws/v2`，`douyin-sparkflow` 的
`protocol_sender.mjs` [已核实]）确实存在且被使用，但它需要逆向抖音自己的 webpack 包、注入
`identity_security_token`，且没有任何官方许可。**没有任何一手证据证明它一定封号，但同样没有证据证明它安全**——
建议**只作为可插拔的第二种后端**（放在现有调度之后，默认关闭 + dry-run），不要替换 DOM 方案。

---

## 四、必须修的问题（按 严重度 × 触发概率 排序）

> **状态（第 0/1 步已落地）**：**P0-1、P0-2、P0-3①、P1-6 已修复并加了回归测试**
> （另修掉一个隐藏缺陷：`unknown` 被误记成 `failed`）。详见 `FORK_NOTES.md`
> 的「审计后的修复」一节。下面保留原始审计描述，作为未完成项（P0-3②、P1-4、P1-5、P1-7）
> 的依据。

### P0-1　选择器失效时会静默记成"发送成功"【已核实，本轮最严重】

`backend.py:1019-1027` → `1077-1081` → `1168-1172` → `1472-1483` → `1868`

链路：消息列表选择器全失效 → JS 返回 `{list:false}` → `_chat_probe` 返回该 dict →
`_confirm_message_delivered` 走 `nolist` → `Send_Frinder` 在 `1474` 发现"输入框被清空"
→ **返回 `TrueString(True)`（成功）** → `run_scheduled_send:1868` 记 `success`。

结果：**当天这个人不会再发（被去重拦住），面板显示成功，火花断掉且你毫不知情。**
这是把"最坏情况"伪装成"最好情况"，与项目自己的设计意图（`1473` 注释说"如实说明未确认"）相反。

**修法**：`nolist` 分支绝不返回成功。改为返回 `False` 并注明"读不到消息列表，无法确认"，
由上层记 `unknown`（`unknown` 当天不会重发，安全方向正确）。

### P0-2　`/Api/LoginPhoneInput` 把"找不到元素"当成"登录成功"【已核实】

`backend.py:3206-3211`：找不到 `#douyin_login_comp_flat_panel/picture` 就 `Login_is_bool = True`。

页面只要改版/慢一拍，就会**谎报登录成功**，随后所有发送都会失败（或更糟：发到错误状态）。
**修法**：改为**正向证据**——用 `_detect_logged_in_state()`（`2178`）判定，
即"会话列表或 `sessionid` cookie 存在"才算成功；找不到否定证据 ≠ 肯定证据。

### P0-3　`@serialized` 漏了 9 个会碰浏览器的路由【已核实】

已挂 `@serialized`：`PngLogin / GetLogin / GetFriendsList / Send / GetUsername / GetScrlk / DieLogin / Verify*(5) / SendCheck`。
**漏掉的**：

| 行 | 路由 | 后果 |
|---|---|---|
| 2582 | `/Api/Init` | 两个并发 Init 都可能通过检查，双双返回"已初始化"而实际没有浏览器 |
| 2599 | `/Api/GetInit` | 可能踩到 `_reset_browser_state()` 的 `driver.quit()` → 500 |
| 2643 | `/Api/login` | 发送进行中写入 cookie + `refresh()`，会把会话列表/输入框冲掉 |
| 2864 | `/Api/login/Init/GetLoginPng` | 前端**每 1–2 秒轮询**，与发送并发读 DOM |
| 2892 | `/Api/login/Init/GetCooker` | 读 `driver.get_cookies()` 时可能正被 quit |
| 3129 | `/Api/LoginPhone` | 与已加锁的 `/Api/Verify/Code` 抢同一个 2FA DOM |
| 3184 | `/Api/LoginPhoneInput` | 同上 |
| 3675 | `/Api/LoginDebug` | 无锁改写 `Login_is_bool` |
| **3722** | **`/Time/add`** | 调 `douyin.Find_Friends()` → DOM 抓取，**完全无锁**（这条容易漏） |

调度侧同样无锁：`_scheduler_loop:2063-2087` 不加锁，`_browser_ready_for_send():1997` →
`_driver_alive():2172` 会执行 `execute_script`，`send_precheck():1849` → `2252` 也是。
**只有 `1857-1866` 这一小段持锁**。

Selenium 官方明确说明 WebDriver 非线程安全，跨线程使用是"undefined behaviour"
（[ThreadGuard 文档](https://www.selenium.dev/documentation/webdriver/support_features/thread_guard/)，
Java 绑定，页面更新于 2026-09-16）；Python 绑定的线程安全问题**至今仍在修**
（[PR #17682](https://github.com/SeleniumHQ/selenium/pull/17682)，2026-06 仍 open，描述为
"N threads making blocking busy-wait calls against unlocked dicts"）。

**修法（两步）**：
① 立刻：给上表 9 个路由加 `@serialized`（`/Api/LoginPhone` 那种要等 15 秒的，拆成"触发"+"轮询状态"两个短操作）；
② 正确解：让**唯一一个 worker 线程**独占 `driver`，其他所有路径（含调度器）只投递命令并带超时等待结果。
即把现有的 `_send_queue` 从"只管发送"升级为"所有浏览器操作的唯一入口"。

### P1-4　发送确认不区分「自己的气泡」和「对方的气泡」【已核实】

`CHAT_OUTGOING_PROBE_JS:1050-1056` 只做 `innerText.indexOf(target)` 子串匹配，
**没有任何方向判定**。所以：
- 好友恰好发来一条包含同样文字的消息 → 被当成"我们发出的新气泡" → 报成功（可能压根没发出去）；
- `1061` 的 `PENDING` 类名（`spin`/`Spin`/`sending`）一旦改版，所有气泡都判 `clean`，
  `1196` 等满 1 秒就报成功。

**修法**：JS 里同时返回气泡的**方位**（例如气泡节点的左右偏移 / `data-*` 方向属性 / 是否在
"自己"容器内），`is_new` 必须同时满足"未标记 + 计数增加 + **方向为我方**"；
方向无法判定时返回 `unconfirmed`，不要猜。

### P1-5　好友重名会被静默覆盖【已核实】

`1367-1392`：`self.friends_xpath_list[friends_text] = new_xpath`，
昵称做 key，**重名直接后者覆盖前者**，同一个人的两条会话只留一条，
且 `1410` 的 `get(name)` 可能打开的是**另一个同名人**的会话。项目已经写了
`_open_conversation` 的名字校验（`1209-1218`）作为安全阀，但根因没解决。

**修法**：参照 `douyin-sparkflow` 的做法——缓存的 key 用稳定身份（会话 ID / `secUid`），
昵称只做显示；发现重名时**拒绝建任务并提示**（`ambiguous_target`），不要猜。

### P1-6　唯一的"活跃度"检查会把调度线程卡住【已核实】

`_browser_ready_for_send():1963` → `_driver_alive():2172` 执行
`execute_script('return document.readyState')`。当 Chrome 渲染进程卡死（有模态框/页面忙）时，
这个 HTTP 调用会一直挂到 Selenium 自己的超时。而它跑在 **ticker 线程**上 —— 调度直接停摆，
且没有任何日志。

**修法**：调度器不做同步 WebDriver 调用。改成"worker 心跳"：worker 每轮更新一个时间戳，
调度器只看 `now - heartbeat < N`，以及"浏览器进程是否还在"。

### P1-7　一次发送最长 ≈69 秒，远超 15 秒锁等待 → 面板全线 409【已核实】

链路：`1309` 打开会话最多 8+3+2=13s → `1420` 等输入框 10s → `1426/1436` 随机停顿 →
`1441` 首次确认 15s → `1465` 三次兜底触发 ×8s → `1474` 再等 5s。
而 `BROWSER_LOCK_WAIT_SECONDS=15`，于是**发送期间所有 `@serialized` 轮询（截图/好友列表/验证状态）
全部返回 409**，前端表现为"面板卡死/截图刷不出来"。

**修法**：把"发送"和"查询"分开——查询类接口不持同一把锁（或改成读写分离：
发送期间允许只读截图，禁止 DOM 交互）。

---

## 五、值得直接抄过来的做法

1. **显式发送状态枚举**（`douyin-sparkflow` [已核实]）：把 `TrueString(True/False)` 换成
   `SENT / FAILED_RETRYABLE / FAILED_PERMANENT / UNCONFIRMED / BLOCKED_BY_RISK`，
   每种状态对应明确的"要不要重发"。这是 P0-1/P1-4 的根治办法。
2. **Dry-run 模式**：我们已有 `/Api/Send/Check` 预检，但只验"能不能打开会话"。
   建议扩成"完整跑一遍直到输入框、不按回车"，并在设置页提供开关。
3. **随机化的风控规避参数**（`config.example.json` [已核实]）：
   账号启动延迟 15–60s、消息间隔 25–70s、**打乱发送顺序**（`shuffleTargets`）、
   文案变体（我们已有文案池 ✓）。我们目前缺的是**打乱顺序**——按任务 ID 顺序发送本身就是特征。
4. **会话保活策略**（`persistentBrowserProfiles`）：`seedCookiesWhenEmpty` /
   `syncStoredCookiesBeforeRun` / `refreshStoredCookiesAfterLogin`。我们已有 profile 持久化，
   可以补上"发送前同步一次 cookie、登录后回写"。
5. **前端按需引入 Element Plus**（[官方文档](https://element-plus.org/en-US/guide/quickstart.html)明确
   推荐用 `unplugin-vue-components` + `unplugin-auto-import`，"全量引入"是"不在意包体积"时的选项）。
   实测我们的产物：**`dist/assets/axios-*.js` 1.1 MB + `index.css` 344 KB**，
   而 `main.js:3-6` 正是**全量引入 + 注册全部图标**。改成按需引入通常能砍掉一半以上。

---

## 六、其它可优化项（低风险、收益明确）

| 项 | 位置 | 说明 |
|---|---|---|
| 日志接口每次全量读盘 | `1597-1641` `read_app_log` | 每次请求读完整 2MB 文件 + 逐行 `json.loads`；Logs 页每 5 秒轮询一次。改成从文件**尾部倒读**，够一页就停 |
| 日志"触发即重写" | `1540-1548` `_trim_app_log` | 超限后每次追加都会重写整个文件（不断在 2MB 上下抖动）。改成"重写后留出大块余量"（如留 2000 行后允许再涨到 2MB） |
| 裸 `except:` | `1383 / 1389 / 1515` | 吞掉真实异常（头像、火花数、登录初始化）。至少改成 `except Exception` + 记日志 |
| 静默失败 | `1079`（探针）、`1439`（`send_keys(ENTER)` 失败被忽略，等于没发也没记账） | 改成 `log_event('warn', ...)` |
| 火花数缺失显示为 0 | `1388-1390` | 抓不到时置 `''`，无法区分"0 天"和"没抓到" |
| 环境变量无校验 | `64-68` `_env_int` | `SPARK_JITTER_MINUTES` 若配置成 >1440 会跨天，当天直接跳过。加个上界检查 |
| 页面标题 | `index.html:7` | `<title>admin</title>` → 应改成"抖音火花助手" |
| 无 `manualChunks` | `vite.config.js` | Element Plus 全量进主包；配合按需引入后再做手动分包 |
| 测试只覆盖纯逻辑层 | `tests/` | `spark_core` 覆盖很好（约 120 个用例），但调度/路由/发送确认**零覆盖**。P0-1 这类 bug 正是缺测试导致的——建议给 `_confirm_message_delivered` 的判定分支补纯逻辑化单测 |
| ruff 对 backend.py 几乎全关 | `pyproject.toml:63` | `["E","W","I","UP","B","F841","F401"]` 全放宽，等于这个文件没有 lint。可逐步收紧 |

---

## 七、不建议做的事

- **不要为了"现代化"重写**。3,300 行的 `backend.py` 确实该拆（FastAPI 官方推荐
  `routers/` + `lifespan` 注入 + `pydantic-settings`，见
  [Bigger Applications](https://fastapi.tiangolo.com/tutorial/bigger-applications/)），
  但拆分本身不产生任何用户价值，且会与 P0 修复互相冲突。**先修 P0，再拆。**
- **不要引入 APScheduler**。我们手写的 ticker 已经覆盖了它最关键的语义（补跑窗口、当天去重），
  而 APScheduler 4.x **至今仍是 alpha**（4.0.0a6，2025-04-27），3.11.3 才是 stable
  （[PyPI](https://pypi.org/project/APScheduler/)）。为一个已经能工作的调度器引入 alpha 依赖不划算。
- **不要把 JSON 状态换成 SQLite**。当前写量极小（每天几十条），`os.replace` 原子写已经完全够用；
  换成 SQLite 反而要处理 WAL 单写者、`SQLITE_BUSY`、checkpoint 等新问题
  （[WAL 文档](https://www.sqlite.org/wal.html)）。**除非**开始需要历史查询/多账号。
- **不要走无头 / GitHub Actions**。同类项目已经用血泪证明这条路被风控盯得最紧。
- **不要把私有协议当主路径**。可以做，但必须默认关闭 + dry-run + 明确风险提示。

---

## 八、建议的执行顺序

| 阶段 | 内容 | 产出 |
|---|---|---|
| **第 0 步（今天）** | 修 P0-1（nolist 不报成功）+ P0-2（正向证据判定登录） | 两处小改动，立刻消除"静默漏发" |
| **第 1 步（本周）** | P0-3 的 ①：给 9 个路由补 `@serialized`；P1-6 心跳化 | 消除大部分并发崩溃 |
| **第 2 步** | P1-4 方向判定 + 引入显式发送状态枚举；给判定逻辑补单测 | "每天恰好一条"真正成立 |
| **第 3 步** | P1-5 稳定身份寻址；P1-7 锁策略拆分 | 重名安全 + 面板不再 409 |
| **第 4 步** | 前端：Element Plus 按需引入 + `manualChunks` + 标题 | 首屏体积大幅下降 |
| **第 5 步（可选）** | 浏览器 worker 单点化（把 `_send_queue` 升级为唯一入口）；再考虑拆 `routers/` | 架构级收敛 |
| **第 6 步（评估）** | patchright 迁移可行性验证 | 摆脱 chromedriver 版本耦合 |

---

## 附：本报告的证据来源

- 本仓库源码：`backend.py`（逐行引用，行号已核对）、`spark_core.py`、`state_store.py`、
  `notifier.py`、`src/**`、`vite.config.js`、`pyproject.toml`、`Dockerfile`、`.github/workflows/ci.yml`
- 同类项目：GitHub REST search API + 各项目 README/源码文件（见第二节表格链接）
- 官方文档：Selenium ThreadGuard / FastAPI Bigger Applications / Element Plus Quick Start /
  APScheduler PyPI / SQLite WAL / OWASP CSRF Cheat Sheet

未核实项（不要当作结论使用）：抖音登录页是否也渲染 `list_item-*` 类名；
`messageBox` 外层节点是否一定带状态图标；`#button-input` 在登录页与验证面板同时存在时的真实文档顺序。

> 本报告只做审计，**未修改任何现有代码文件**。配套的浏览器层细粒度审计见 `AUDIT-browser-layer.md`。

---

## 附二：怎么在这台机器上跑测试（踩过的坑）

**Windows 侧跑不了**：`python` 是 Microsoft Store 的占位别名（退出码 9009），
而仓库里的 `.venv` 是从**宝塔服务器**（`/www/wwwroot/tiktok-spark/…`）拷过来的 Linux venv
（`pyvenv.cfg` 里写着 `home = /usr/bin`、`version = 3.11.2`），在 Windows 上完全不可用。
另外 `git show HEAD:backend.py` 之类的操作会因为 CRLF 归一化把中文弄坏，不要用它来取"原始版本"。

**可用的方法**（WSL Ubuntu + 干净的 Linux venv）：

```bash
# 一次性准备（WSL 里，需 root；本机 WSL 已经是 root）
apt-get install -y python3-pip python3-venv
python3 -m venv /home/aictro/venvs/spark-test
/home/aictro/venvs/spark-test/bin/python -m pip install 'pytest>=8,<9' 'ruff>=0.6,<1'

# 跑测试：把仓库拷进 WSL 再跑。直接跑 /mnt/c 会很慢，
# 而且 tests/__pycache__ 里的旧 .pyc 会在收集阶段盖掉新源码。
V=/home/aictro/venvs/spark-test/bin
rm -rf /tmp/work && mkdir -p /tmp/work
cp -r /mnt/c/Users/aictro/Desktop/spark-web/. /tmp/work/
cd /tmp/work && rm -rf .venv node_modules dist chrome-profile data logs
find . -name __pycache__ -type d -prune -exec rm -rf {} +
PYTHONPATH=$PWD $V/python -m pytest -q
$V/python -m ruff check .
```

当前状态：**136 passed，ruff 干净**。

### 一个必须保留的习惯：验证测试本身有效

新加的回归用例要**先把修复改回去、确认它真的会变红**，否则很容易写出
"永远绿"的假测试 —— 本轮就发生过：第一版针对最严重缺陷的用例只调了
`_chat_probe`，而缺陷其实在**调用方**那几行，改回去它依然全绿。
凡是"防回归"的用例，都值得这样验一遍。
