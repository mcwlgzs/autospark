# 浏览器自动化层 · 脆弱性与正确性审计（只读，未改动任何文件）

审计对象：`backend.py`（4111 行，UTF-8 中文），行号一律为 `backend.py:N`。
范围：1–1520（helpers / XPath / JS 常量 / `class Douyin`）、2500–3690（浏览器路由）。静态阅读，未运行任何代码。

---

## 1. Selector 清单

**高脆弱 = 依赖构建期散列类名或全局宽泛匹配；中 = 依赖当前文案/属性；低 = 结构化锚点（id / data-e2e / data-slate-editor）。**

### 登录面板 / 登录态
| 行 | 选择器 | 评级 | 理由 |
|---|---|---|---|
| 2183, 2677, 2712, 3207 | `//*[@id="douyin_login_comp_flat_panel"]` / `/picture` | 低 | 抖音登录组件固定 id；`/picture` 只是「面板还在」的存在性探针 |
| 2169–2172 | `#douyin_login_comp_flat_panel`(JS 变量名 `box`) | 低 | 同上 |
| 902–917 | `data-slate-editor="true"` | 低 | Slate 框架属性，非抖音散列类名 |
| 2211–2212, 2226 | 文案 `扫码登录/登录抖音/手机号登录/Log in to Douyin/Scan to Log In` | 中 | 文案 A/B 或语言切换即失效 |

### QR 码
| 行 | 选择器 | 评级 | 理由 |
|---|---|---|---|
| 2737, 2758, 2759, 2775–2777 | `#animate_qrcode_container`（CSS + XPath） | 中 | id 可控但属登录组件内部；`Refresh` / `刷新` 文案（2776–2777）随语言变 |
| 678–686 | 验证面板 `//*[contains(@class,"second_verify_panel_new")]//canvas|//img`、`uc_verification_component` | 中 | 散列类名，但用 `contains` 前缀 + canvas/img 双重兜底，冗余较多 |
| 2789–2798 | `_qr_expired()` 文案 `expired/过期/失效/refresh/刷新` | 中 | 纯文案判据 |

### 区号 / 手机号 / 验证码 / 提交（登录页）
| 行 | 选择器 | 评级 | 理由 |
|---|---|---|---|
| 208–215 | `web-login-area-code-input`、`aria-label="国家/地区"`、**`//input[@role="combobox"]`** | 高 | 第 3 条是全局第一条 combobox，任何下拉/搜索框都会命中，`_find_first` 取第一个「可见且可点」的元素，可能把区号写进搜索框 |
| 334–337 | `#select-ul li`（CSS，固定等待 8s） | 中 | 下拉容器 id 硬编码 |
| 217–223 | `//*[@id="normal-input"]`、`@placeholder="Phone number"`、`手机号/手机`、`inputmode=tel` | 中 | 「手机」子串匹配会把「手机号登录」等文本节点附近输入框一并纳入 |
| 225–230 | `douyin_login_comp_button_input_id`、`//*[normalize-space()="Send code"]`、`获取验证码/发送验证码` | 中 | 文案精确等值；英文页/新文案即失配 |
| 232–237 | `//*[@id="button-input"]`、`Enter code`、`验证码/短信` | 中 | 幂等 id，但 `#button-input` 在验证面板里也存在（3219–3235 的注释已确认），跨作用域冲突 |
| 239–244 | `douyin_login_comp_btn_id`、`登录/Log in/下一步` | 中 | 精确文案 |
| 246–249 | `second_verify_panel_new`、`uc_verification_component_layout` | 中 | 散列类名，`contains` 前缀缓解 |
| 251–255 | `Use Phone/手机号登录/验证码登录` | 中 | 文案 |

### 二次验证方式列表 / 回退
| 行 | 选择器 | 评级 | 理由 |
|---|---|---|---|
| 458–466 | `//*[contains(@class,"list_item")][1]` + `normalize-space()` 文案匹配 | 中 | 类名是 `list_item` 前缀（散列后缀，如 661 的 `list_item-ZI1VMT`） |
| 494–498 | `选择其他验证方式/其他验证方式/无法验证通过` | 中 | 文案 |
| 508–515 | `返回/上一步/重新选择验证方式/取消` | 中 | 文案，且「取消」在面板内匹配 |
| 557, 661 | `//*[contains(@class,"list_item")]`、`[class*="list_item-ZI1VMT"]` | **高** | 661 是**完整散列类名**；557 会统计页面上任意 `list_item`——若登录页也用同一组件库前缀，`_verify_method_list_visible()`(569–586) 会把普通登录页判成「验证方式列表页」 |
| 3225–3240, 3256–3263, 3248–3254 | 面板内 `input[type=tel|text|number]`、`[not(@type="password")]`、`type="password"`、`确认/确定/提交/验证/下一步/完成` | 中 | 排除 password 的注释（3220–3228, 3256–3258）已经踩过一次坑 |
| 678–686 | 同上 QR 组 | 中 | |

### 聊天会话列表行 / 好友行 / 火花 / 头像
| 行 | 选择器 | 评级 | 理由 |
|---|---|---|---|
| **1367, 1371, 1374–1377** | `//div[@class="conversationConversationListwrapper"]/div/div/div` 及 `div[{n+1}]/div[1]/div[2]/div[1]/div[1]`、`.../div[1]/div/span/img`、`.../div[2]/div[1]/div/div` | **高（最脆弱）** | 1) 散列类名 `conversationConversationListwrapper` 用 **`@class=` 精确等值**，抖音加一个额外类名（很常见）即全列表失配；2) 行定位是**纯位置索引链**，列表虚拟化/行内多一层 div 即整体错位——错位不会报错，只会「返回空/点错人」 |
| 2193, 2194, 2195 | `contains(@class,"conversationConversationListwrapper")`、`data-e2e="im-list"`、`im-container` | 中 | 这里用了 `contains`，与 1367 的精确匹配**不一致**：登录检测说「已登录」，`Updara_FrinderList` 却读到 0 行（1408–1409 只报「没读到会话列表」） |
| 1388 | 火花数 `.../div[1]/div[2]/div[1]/div[2]/div[1]/div/div` + `.text` | **高** | 结构变化 → 空字符串（1389–1390 静默吞掉），UI 显示 0 火花但不会报错 |
| 1381–1385 | 头像 `.../div[1]/div[1]/div/span/img`、`.../div/div/img` | 中 | 有二级兜底 |

### 聊天编辑器 / 发送按钮 / 气泡
| 行 | 选择器 | 评级 | 理由 |
|---|---|---|---|
| 904–917 | `data-slate-editor="true"`、`data-e2e="message-input"/chat-input"`、`contenteditable`、`textarea` | 低（第 1/5/6/10/11/12 条） | 行为特征兜底很好 |
| 906, 907, 911, 912 | `messageEditorimChatEditorContainer`、`messageMsgInput`、`imChatEditor`、`messageEditor` | **高** | 构建期拼接类名（899–903 注释自述该类名已失效过一次） |
| 908, 913, 916 | `data-placeholder="发送消息"`、`//textarea[not(@type="hidden")]` | 中 | 第 12 条是**全局第一个 textarea**（可能是搜索框） |
| 922–928 | `messageMsgInputpublishBtn`、`e2e-send-msg-btn`、`data-e2e="send-msg-btn"`、`//*[normalize-space()="发送"]` | 中（散列）/低（e2e） | 第 1、4 条依赖散列类名；919–921 注释警告左侧同款 svg 是表情按钮，一旦顺序变就会点到表情 |
| 1019–1029 | `.messageMessageListlist`、`[class*="messageMessageListlist"]`、`[class*="messageMessageListwrapper"]`、`[class*="MessageList"]`、`[class*="messageBox"]/[class*="MessageBox"]/[class*="msgBox"]` | **高** | 送达判定的**唯一**依据（见 §4）。`[class*="MessageList"]`（1020）是全页第一个 MessageList，多会话/侧栏预览存在时会取错容器 |
| 1211, 1216 | `RightPanelHeadertitle`、`data-slate-editor="true"` | 中 / 低 | 头部标题是散列类名；它是「防发错人」的第一判据 |
| 1218 | `.conversationConversationListwrapper`（JS 里排除列表内同名文本） | **高** | 同名散列类名，改一处漏一处 |

### JS 里出现的其它 DOM 字符串
`VERIFY_QR_SRC_JS` 688–699、`CHAT_EDITOR_TEXT_JS` 930–935、`_verify_panel_text` 371、`_login_error_text` 400–401、`_verify_account` 653（`name-eQ7kOH`，**完整散列类名**）、`CHAT_OPEN_NAME_JS` 1209–1233、`CHAT_INSERT_JS` 1238–1260、`CHAT_ENTER_JS` 1262–1271、`RISK_DETECT_JS` 2228–2244、`PROFILE_FETCH_JS` 3001–3016、`VERIFY_BUTTON_READY_JS` 3286–3304（依赖 React Fiber `__reactFiber$`）、`VERIFY_REACT_CLICK_JS` 3306–3318、`REACT_HANDLER_JS` 3323–3351（依赖 `__reactProps$`）。

**明确点名的高危散列类名**：`conversationConversationListwrapper`（1367/1374–1377/1218，且用精确 `@class=`）、`conversationConversationItemwrapper`（1286 注释）、`list_item-ZI1VMT`（661）、`name-eQ7kOH`（653）、`messageEditorimChatEditorContainer` / `messageMsgInputpublishBtn` / `messageMsgInput`（906/907/923）、`RightPanelHeadertitle`（1211）。`messageBox`/`MessageBox`/`msgBox` 只要抖音换成 `msgBoxWrapper` 之类，1029 行 `raw` 直接为空 → `total=0` → 永远「未确认」。

---

## 2. Fallback 纪律

**有多级兜底（好）**：编辑器 `_chat_editor`(942–955, 12 条含 contenteditable/textarea)、发送触发 `Send_Frinder`(1453–1470: react-onclick → dom-click → enter-event)、点开会话 `_open_conversation`(1297–1311: react-mousedown → click → JS click)、按钮点击 `_click_first_xpath`(195–201)/`_try_click`(269–282)、QR 图 `_verify_qr_image`(749–772: JS dataURL → HTTP 拉取 → 元素截图)、验证方式入口 `_enter_verify_method`(632–647: 面板条目 → 「其他验证方式」列表页)、区号 `LOGIN_AREA_CODE_XPATHS`(208–215, 6 条)。

**单点（无兜底）**：
- **送达判定**：`CHAT_OUTGOING_PROBE_JS` 的 4 条列表选择器（1019–1020）与 `messageBox` 组（1029）——全部失配即永久 `nolist`；`_chat_probe`(1075–1081) 本身无重试。
- **好友列表**：`Updara_FrinderList` 只有一条 1367，且 `@class=` 精确等值；行/头像/火花全靠位置链（1374–1377）。
- `#select-ul li`(337) 单条。
- `VERIFY_PASSWORD_INPUT_XPATHS`(3256–3263) 首条 `//input[@type="password"]` 全局匹配。
- `_visible_list_items`(557) 单条 `list_item`。

**该用 `find_elements` 却用 `find_element`（会抛）**：
`1292`（会话行）、`1378`（每行必抛点，且位置链天生易空）、`1381`/`1384`（头像，靠 `except:` 兜）、`1388`（火花）、`1512`（登录初始化，`except: pass`）、`2677`/`2712`/`3207`（登录面板探针，靠 `NoSuchElementException` 做**控制流**——元素结构一变就会走进「登录成功」分支，2677→2681、3207→3209 都是把「找不到」当作成功证据）。
反过来，`_find_first`(180–192) 用 `find_elements` 是正确写法。

---

## 3. 等待 / 同步质量（24 处 sleep + 5 个轮询器）

轮询器：`_wait_send_result`(414–432, 0.5s)、`_wait_for_any`(518–526, 0.5s)、`_wait_until`(529–540, 0.5s)、`_wait_chat_editor`(958–967, 0.5s)、`_wait_message_sent`(979–996, 0.5s)、`_confirm_message_delivered`(1165–1198, 0.2s)、`_wait_verify_outcome`(3504–3514, 0.5s)、`_ensure_fresh_qr_src`(2850–2851, 0.6s)。**全项目没有一处 `WebDriverWait`/`expected_conditions`。**

| 行 | 时长 | 真正在等什么 | 应否改显式等待 |
|---|---|---|---|
| 337–340 | 0.3s 轮询、上限 8s | **已是轮询** `#select-ul li`，但无「下拉关闭」判据 | 保留，补 UI 关闭条件（中） |
| 358 | 1s 固定 | 区号回填生效 | **是**（低风险） |
| 418 | 0.5s 轮询 | 短信按钮出现「60s」倒计时 | 可保留 |
| 526, 540 | 0.5s 轮询 | 元素出现 / 谓词成立 | 保留 |
| 837 | **2s 固定** | 点「接收短信验证码」后步骤切换 | **是**（高） |
| 840 | **2s 固定** | 点「发送短信验证」后下发 | **是**（高） |
| 967 | 0.5s 轮询 | 输入框渲染 | 保留 |
| 990 | 0.5s 轮询 | 输入框被清空 | 保留（判据弱，见 §4） |
| 1186 | 0.5s 固定 | 转圈消失后重试标记是否挂上 | 可接受（本来就靠它兜） |
| 1198 | 0.2s 轮询 | 气泡终态 | 保留 |
| 2375 | 随机 | 人情化停顿（`human_pause`） | 非同步用途，保留 |
| 2699 | 1s 固定 | 扫码登录状态刷新 | **是**（中） |
| 2826 | 0.8s 固定 | hover 后刷新控件出现 | **是**（中） |
| 2851 | 0.6s 轮询、上限 12s | 新二维码 src 出现 | 保留 |
| 2879 | 1s 固定 | 切回扫码标签 | **是**（中） |
| 3154 | 1s 固定 | 点「手机号登录」后表单切换 | **是**（中） |
| 3168 | 0.5s 固定 | 手机号写入后 React 状态同步 | **是**（中） |
| 3203 | **3s 固定** | 提交验证码后登录完成 | **是**（高） |
| 3453 | 0.5s × 最多 12 | React 按钮 ready（有判据） | 保留 |
| 3460 | 0.5s × 最多 6 | 重灌值后 ready | 保留 |
| 3508 | 0.5s 轮询 | 验证结果三选一 | 保留 |
| 3571 | 1s 固定 | 选完验证方式后步骤切换 | **是**（中） |
| 3576 | **2s 固定** | 短信下发 | **是**（高） |
| 1841 | 随机 1–15s | 定时任务错峰（设计如此） | 保留 |

**单次发送最坏延迟**（`Send_Frinder` 1394–1495）：会话行点击候选等待 8+3+2=13s（1309, `_wait_until`）→ 编辑器 `_wait_chat_editor(10)` → paced 停顿 0.3–1.2s(1426) + 0.2–0.8s(1436) → 首次确认 `SEND_CONFIRM_TIMEOUT=15s`(1006/1441) → 3 个兜底触发器各 8s(1465) → 兜底 `_wait_message_sent(5)`(1474)。**最坏 ≈ 13+10+2+15+24+5 ≈ 69s**；再加 `Updara_FrinderList()`(1407，无等待、无轮询，列表没渲染完就直接报「没读到会话列表」)与锁外 `human_pause(1,15)`(1841)。**持锁时长即 UI 轮询的 409 窗口**（`BROWSER_LOCK_WAIT_SECONDS=15`，2345/2363）：发送期间前端所有 `@serialized` 轮询（截图/好友列表/验证状态）必然 409，最长约 1 分钟。

---

## 4. 发送确认逻辑（最高价值）

**判定链**：`Send_Frinder` 1427 写入内容 → 1432 `_chat_probe(text,'tag')` 给**当前所有气泡**打 `data-spark-seen` 并返回 `matches` 作为基线 → 1438 `send_keys(ENTER)` → 1441 `_confirm_message_delivered(text, before_matches)` 每 0.2s 调 `_chat_probe(text,'check')`(1166)，直到 15s 预算用尽(1165)。

**`CHAT_OUTGOING_PROBE_JS`(1017–1072)** 在 JS 内完成全部判断：找消息列表（1019–1025，取第一个匹配容器）→ 去重到最外层气泡（1029–1041，靠祖先类名含 `messageBox/MessageBox/msgBox`）→ `check` 模式下用 `norm(innerText).indexOf(target)` 做**子串**匹配（1052），统计 `matches` 与「未带 TAG 的 `untagged`」（1054）→ 只对**最后一个未标记气泡** `newest` 判状态：文案「发送失败」→`failed`（1058）；`[class*="SendStatusretry"]/[class*="sendFailed"]/[aria-label*="重试"]` →`failed`（1059–1060, 1068）；`[class*="spin"]/[data-icon="spin"]/[class*="sending"]` →`pending`（1061, 1069）；否则 `clean`（1070）。

**Python 侧**：`is_new = untagged>=1 且 matches > before_matches`(1173–1174)。`pending` → 记 `clean_since=None`(1179–1181)；`clean` 且此前出现过 `pending` → 睡 0.5s 复查，非 failed/pending 即 `success`(1182–1191)；否则要求连续干净 ≥ `SEND_INITIAL_CLEAN_GRACE=1.0s`(1196) 才 `success`。超时返回 `unconfirmed`(1199)。

### 假阳性（报成功但没发出去）
1. **`clean` 判据本身不构成送达证据**：1029–1041 只要气泡元素存在、文本匹配、且**内部没有** `spin`/`retry` 类名，就返回 `clean`。抖音把转圈换成别的类名（散列类名改造）就永久 `clean` → 1 秒后 `success`(1196–1197)。转圈图标是 `svg`/`img`（无类名）时同样漏判。
2. **列表选择器命中错误容器**：1020 的 `[class*="MessageList"]` 是宽泛子串，侧栏预览/多面板时可能选中**不含刚发送气泡**的容器；只要该容器里恰好有一条文本子串匹配且未打标的气泡，就 `is_new` 成立。
3. **`nolist` 分支把「输入框清空」当成功**（1472–1483）：只要判成读不到列表 + `_wait_message_sent` 读到空(1474)，就 `return TrueString(True)`(1483)，日志仅记为「未确认送达」。而 1477 的 `cleared` 判据来自 `_chat_editor_text`(970–976)——元素被 React 换成新节点、`execute_script` 抛异常返回 `None` 时**不会**误判（976 已区分 None 与空串），但 `current != text`(994) 分支会把「内容被任何原因改动」也算作已提交。此时若抖音后台其实没收到（键盘事件被吞、编辑器失焦），**接口报成功、送达为假**。
4. **`state='failed'` 的漏判组合**：`failed` 只在 `is_new` 为真时才会返回(1176–1178)。若新气泡出现但 `untagged` 已被计入 0（例如 React 复用了被我们打过标记的 DOM 节点——`setAttribute` 只改 DOM，React 重渲染旧节点时会**保留**该属性，1066 的注释承认了这一点），则 `is_new` 永假 → 即使气泡翻红也只会落到 `unconfirmed`，不会报 `failed`（方向安全，但错误分类会误导用户）。
5. **`_save_failure_shot` 之前无二次确认**：1486 截图与日志用的是同一次已经过期的 probe 结果。

### 假阴性（发出去了却报失败）
1. **`_chat_probe` 的 `except Exception: return {}`(1079–1080) 被当作终态**：`_confirm_message_delivered` 1168–1170 一见空 dict 就立刻 `nolist`，不重试。发送后页面正在重渲染、执行脚本瞬时失败，就直接走兜底路径；若此时输入框**已清空** → `_wait_message_sent` 为 True → 1477–1483 报成功（变成假阳性）；若输入框已被 React 销毁 → 1474 读到 None → 1468 `_editor_still_has` 为假 → 1486 报「发送状态未确认」，**消息实际已送达**，而 `_history_mark(name,text,'unknown')`(1877) 会阻止当天补发（"未确认"→status unknown）。
2. **`clean` 的 1.0s 门槛**：首次干净必须**跨至少一次 0.2s 轮询**且 `now - clean_since >= 1.0`(1196)，在抖音慢渲染场景下会白等 15s 后转 `unconfirmed`。
3. **误判 `failed`**：1058 对 `newest.innerText` 做 `/发送失败/` 正则——**朋友发来的消息里包含「发送失败」四个字**（或自己回复里含该词）时，只要它未打标且文本子串匹配我们的内容，就报 failed(1177–1178)。
4. **文本子串匹配**：1052 用 `indexOf(target)`，目标文案被截断/包含在旧消息中时，`matches` 的增量可被**对方新消息**顶上去 → 既可能假阴性也可能假阳性。
5. **成功太早**：`success` 在「1 秒干净」或「转圈消失后 0.5s 复查干净」时立即返回(1191/1197)，而抖音的重试标记可能更晚上挂 → 系统报成功、稍后气泡变红。

**结论**：核心承诺「每位好友每天恰好一条」在这条链上有两个破口——(a) 成功判定不校验气泡归属（自己/对方）、不校验发送方向；(b) `nolist` 兜底把「编辑器清空」升级为成功。两者都会让 `send_history` 写入 `success`(1868/2982)，当天不再重发。

---

## 5. 错误处理异味与顺序问题

**会吞掉真实浏览器故障的 `except: pass`（浏览器层）**：
`199`（真实 click 失败，静默改 JS click）、`292/296/300`（`_set_value` 的 click/clear/send_keys——**输入失败**被吞，302/314 靠读回值兜）、`312`（原生 setter 注入失败）、`332/353/356`（区号点击）、`425`（`_wait_send_result` 里读按钮文案失败 → 走到 `unknown`）、`478/481`（点验证方式）、`707/721`（QR 元素探测）、`947/953`（编辑器候选元素）、`1079`（**`_chat_probe`：把脚本异常等同于「读不到列表」**）、`1278`（`_open_chat_is` 返回 False，等于「没打开」）、`1306`（点开会话三种方式全部失败也被 `continue` 吞掉，只留 `tried` 列表 1311）、`1324/1330/1347`（写输入框三次失败）、`1439`（`send_keys(ENTER)` 异常被吞——**回车没发出去也不会记录**）、`1463`（`trigger()` 失败被吞）、`1515`（`LoginInit` 点击）、`2174`（`_driver_alive`）、`2189/2202/2208/2218`（登录态判定）、`2253/2259`（风控检测）、`2272`（`detect_login_lost`：读 body 失败 → `body=''` → **跳过标记检查**，可能误判为已登录）、`2744/2750/2767/2784/2794/2810/2814/2828`（QR 相关）、`3048`（资料接口）、`3360/3370/3379/3386/3406/3429`（React handler / readiness / 请求计数）。
**裸 `except:`**：`1383`、`1389`（头像/火花，`UserFriendsInfo.avatar` 可能为未定义值；`fire=''` 静默）、`1515`。

**状态先改、浏览器动作后做（顺序 bug）**：
- `1859`：`_history_mark(name, content, 'unknown')` 在 `Douyin.Send_Frinder`(1861) **之前**写入；`_history_mark`(2398–2401) 走 `STATE.set`，而 `state_store.StateStore.set`（state_store.py:67–70）每次都同步 `save()` 落盘 → 发送前就落盘一条记录。若发送抛错，1863 再写一次 unknown。
- `2969`：手动 `/Api/Send` 同样是先 `_history_mark`(2969) 后 `Send_Frinder`(2971)。**交错即双发**：worker 在 1861 的 `_confirm_message_delivered` 超时即将返回 `unconfirmed`(1199)（消息其实已送达、气泡已出现）时，手动请求（未持锁，见 §6）在 2965 读到的是 1859 写入的 `unknown`——`guard_decision`(spark_core, 2416) 对 manual 作用域只做 90s 冷却，而该记录刚刚写入（同一秒）→ 冷却**命中**并拦下（安全方向）；但反过来，若手动请求先于 1859 进入，则两次发送会重叠执行，最终产生两条同内容消息。**结论：防重复依赖「记账早于发送」与「冷却」的组合，而不是原子检查，不是可靠保证。**
- `1420–1441`：写入编辑器(1427) 与打标记(1432) 之间没有任何确认，编辑器重渲染会让标记基线失真。
- `2011–2016`：`check_due_tasks` 先写 `last_run_date=today` 再入队（有注释说明是有意的），但 `_persist_tasks`(2016) 在锁内、`_enqueue_send`(2023) 在锁外，中间异常会留下「记了今天但从未入队」的任务（静默漏发一天）。

---

## 6. 并发

真正取锁的只有 3 处：`serialized` 装饰器(2362–2368, `RLock.acquire(timeout=15)`)、worker 发送路径 `with browser_lock`(1857)、`ensure_browser_ready`(2507)。**`_scheduler_loop`(2063–2087) 与 `check_due_tasks` 不持锁**：2071 直接调 `check_due_tasks()`，其中 `_browser_ready_for_send()`(1997→1960–1965) 调 `_driver_alive()`(2168–2175) 执行 `execute_script` **在锁外**；worker 线程只在 1857 起持锁，1849 `send_precheck()`（`detect_risk_control`→`execute_script`）与 1853 `send_guard`、1869–1882 的收尾都在锁外。答案是：**调度/worker 路径部分持锁（仅 1857–1866 的发送区间），就绪性探测与预检完全不持锁。**

**确认：未加 `@serialized` 的浏览器路由**（已逐行核对装饰器）：`/Api/Init`(2581–2582)、`/Api/GetInit`(2598–2599)、`/Api/login`(2642–2643)、`/Api/login/Init/GetLoginPng`(2863–2864)、`/Api/login/Init/GetCooker`(2891–2892)、`/Api/LoginPhone`(3128–3129)、`/Api/LoginPhoneInput`(3183–3184)、`/Api/LoginDebug`(3674–3675)。**与你给的清单完全一致，无遗漏。** 另外 `/Time/add`(3721–3722) 虽未装饰，但 3745 调 `douyin.Find_Friends` → `Updara_FrinderList()`（1366，纯 `find_element(s)` DOM 读）——**也是一条未持锁的浏览器路由**，应计入清单。`class Douyin` 的方法（`Updara_FrinderList` 1366、`Send_Frinder` 1394、`Find_Friends` 1497、`LoginInit` 1510、`PrintfFrinder` 1359）**全部不自持锁**，完全依赖调用方。

**会真的坏掉的交错**：
- `/Api/login`(2642) 在 worker 持锁发送中执行 `driver.add_cookie`(2672) + `driver.refresh()`(2675)：页面重载 → 1407 建立的会话行与 1427 写入的编辑器全部失效 → 1432/1441 的 probe 得到空 dict → `nolist` → 走 1472–1483，可能把「没发出去」报成成功，也可能把「已发出」报成未确认。
- `/Api/LoginPhone`(3128) 与 `@serialized` 的 `/Api/Verify/Code`(3612) 并发操作**同一块二次验证 DOM**：3129 的 `_wait_send_result()` 会轮询 15s(414)，期间用户从面板提交验证码，两边都改输入框/点提交 → 相互覆盖，双方结论都不可信。
- `/Api/GetInit`(2598) / `/Api/GetCooker`(2891) 在锁外读 `driver`，与 `_reset_browser_state()`(2116–2127，会 `driver.quit()`) 并发 → `InvalidSessionIdException`/`WebDriverException`（`_driver_alive` 2174 吞掉、`get_cookies` 2911 不吞，直接 500）。
- `/Api/Init`(2587) 在锁外读 `init`/`_driver_alive()`，两条并发请求可同时进入 `ensure_browser_ready()`：第二者在 2507 拿到锁后于 2508 看到 `init=True` 直接返回，但**第一者的 `driver` 赋值(2519) 尚未发生**，第二者仍返回 200「init Repeated」——真实的「初始化成功但浏览器不存在」竞态。
- `/Api/LoginDebug`(3675–3687) 无锁把 `Login_is_bool` 置 True(3684)，绕过 2733/2524 的 `_detect_logged_in_state()`，会让 2910/3079 的导出/查询以为已登录。

---

## 7. Top 10 排序修复（风险 × 概率 ÷ 成本）

1. **`_confirm_message_delivered` 不校验气泡归属/方向**（backend.py:1173–1197, JS 1050–1070）。最高风险：错误地宣布「已送达」并写入 `success`。修复：JS 里为每个气泡附带方向信号（气泡容器上抖音自身的 `isSelf`/位置判据，或 `messageBox` 的父级对齐类名），`is_new` 额外要求「该气泡属于己方」；判不出方向时返回 `unconfirmed` 而不是 `clean`。
2. **`nolist` 兜底把「输入框清空」升级为成功**（1472–1483）。修复：返回 `TrueString(False, '未确认')`（或让 1483 的返回值带 `unconfirmed` 标记位由 `run_scheduled_send` 记为 unknown），绝不写 `success`。
3. **8 条浏览器路由未加 `@serialized`**（2582, 2599, 2643, 2864, 2892, 3129, 3184, 3675；外加 3722）。修复：给这 9 个函数统一加 `@serialized`（`/Api/GetInit`、`/Api/LoginDebug` 用短超时即可），并把 `/Api/LoginPhone` 的长轮询拆成「触发」与「轮询结果」两个加锁短请求。
4. **`_chat_probe` 把脚本异常降级为「读不到列表」**（1079–1080 被 1168–1170 当终态）。修复：区分「JS 抛异常」（应重试 2–3 次、指数退避）与「脚本正常返回 `list:false`」；仅后者进 `nolist`。
5. **`Updara_FrinderList` 的精确 `@class=` + 位置索引链**（1367, 1374–1377）。修复：改 `contains(@class,"conversationConversationListwrapper")` 或 `@data-e2e="conversation-item"`，并按行内 `data-e2e`/语义节点（头像 img、昵称节点）取字段，不再用 `div[n]/div[m]` 数字链。
6. **送达判定的 PENDING/FAILURE 类名依赖散列前缀**（1059–1061）。修复：把「无转圈」的默认语义反转——只有**明确观测到**干净终态信号（例如气泡旁的状态图标消失且抖音自身的 `[data-e2e]` 状态钩子存在）才判 `clean`，否则保持 `pending`；同时把 `[class*="MessageList"]`(1020) 收紧为前两条精确前缀。
7. **`send_keys(ENTER)` 异常被吞且无二次触发**（1437–1440）。修复：`except` 分支立刻走 1453 的备用触发器（react-onclick / enter-event），而不是先花 15s 等一个不可能出现的气泡。
8. **固定 2–3s 的猜测等待**（837, 840, 3203, 3576）。修复：改为 `_wait_until` 轮询真实条件（面板文案出现「已发送至」、面板 class 变化、登录面板消失），把每处 2–3s 的固定开销换成有判据的 0.2–0.5s 轮询。
9. **`_driver_alive` / `detect_login_lost` 等探针在锁外执行**（2032 注释声称有锁但 1977/1997/1849 实际没有；2168–2175、2266–2278）。修复：把 `_browser_ready_for_send`、`send_precheck` 的调用点包进 `browser_lock`（用非阻塞 `acquire(blocking=False)` 以免 ticker 被阻塞），或改为「只读快照」接口。
10. **`except: pass` 关键点位无日志**（1383, 1389, 1439, 1463, 1515, 1079）。修复：至少 `log_event('warn', '浏览器', ...)`，并给 `UserFriendsInfo.fire` 保留 `None` 与 `''` 的区分，避免「火花 0」被当成事实展示。

**未验证**：`list_item` 前缀是否也出现在抖音登录页（若出现，§1 表 557 行的高危项会在普通登录页误报「验证中」）；`messageBox` 外层是否总带状态图标；`#button-input` 在验证面板与登录页同时存在时的实际文档顺序。
