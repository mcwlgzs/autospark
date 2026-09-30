import axios from 'axios'
import { ElMessage } from 'element-plus'

// 创建 axios 实例
// 超时给到 60s：后端接口是在驱动真实浏览器（点会话、等输入框渲染、提交验证后等抖音响应），
// 单次操作几秒到十几秒很正常，10s 会导致「前端报失败、消息其实已经发出去」。
const api = axios.create({
  baseURL: '/api',  // 通过 vite 代理到后端
  timeout: 60000
})

// 打上「拦截器已经提示过」的标记：调用方 catch 里再弹一条笼统文案，
// 会让用户一次失败看到两条提示，而且第二条反而更没信息量。
const markReported = (error) => {
  if (error && typeof error === 'object') error.__reported = true
  return error
}

// 调用方 catch 的兜底提示：拦截器已弹过真实原因时不再叠加
export const fallbackError = (error, message) => {
  if (error && error.__reported) return
  ElMessage.error(message)
}

// 面板会话失效：清本地凭据 + 同步 Pinia + 回登录页。
// 三处都要做，缺一处就会出现「localStorage 清了但内存里还认为已登录」，
// 于是路由守卫放行、后续每个请求再撞一次 401。
// 动态 import 是为了避免与 stores/user.js 形成循环依赖。
const handleSessionExpired = () => {
  ElMessage.error('登录已过期，请重新登录')
  localStorage.removeItem('token')
  localStorage.removeItem('douyin_token')
  import('../stores/user').then(({ useUserStore }) => {
    try {
      useUserStore().logout()
    } catch (e) {
      // Pinia 还没初始化（极端情况）时忽略：localStorage 已经清干净了
    }
  })
  import('../router').then(({ default: router }) => {
    router.push('/login')
  }).catch(() => {
    window.location.replace('/login')
  })
}

// 请求拦截器
api.interceptors.request.use(
  config => {
    const token = localStorage.getItem('token') || localStorage.getItem('douyin_token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  error => Promise.reject(error)
)

// 响应拦截器
api.interceptors.response.use(
  response => {
    const contentType = response.headers['content-type']
    if (contentType && contentType.includes('application/json')) {
      const res = response.data
      if (res && res.code !== undefined && res.code == 401) {
        handleSessionExpired()
        return Promise.reject(markReported(new Error(res.data || '未授权')))
      }
      if (res && res.code !== undefined && res.code != 200 && res.code != '200' && res.code != 202 && res.code != '202') {
        ElMessage.error(res.data || res.msg || res.message || '请求失败')
        return Promise.reject(markReported(new Error(res.data || '请求失败')))
      }
      return res
    }
    return response
  },
  error => {
    // 处理网络错误或服务器错误
    if (error.response) {
      // 服务器返回了错误状态码
      const status = error.response.status
      const data = error.response.data

      if (status === 401) {
        handleSessionExpired()
        // 必须 reject：之前这里裸 return，调用方 await 拿到的是 undefined，
        // 接着访问 res.code 会抛 TypeError，报错信息完全对不上真实原因。
        return Promise.reject(markReported(error))
      } else if (status === 500) {
        ElMessage.error('服务器内部错误')
      } else if (status === 404) {
        ElMessage.error('请求的资源不存在')
      } else {
        // 尝试从响应中提取错误信息
        const msg = data?.data || data?.msg || data?.message || error.message || `请求失败 (${status})`
        ElMessage.error(msg)
      }
    } else if (error.request) {
      // 请求已发送但没有收到响应
      ElMessage.error('无法连接到服务器，请检查后端服务是否启动')
    } else {
      ElMessage.error(error.message || '网络错误')
    }
    return Promise.reject(markReported(error))
  }
)

// 初始化浏览器
export const initBrowser = () => api.get('/Api/Init')

// 获取初始化状态
export const getInitStatus = () => api.get('/Api/GetInit')

// 获取登录状态
export const getLoginStatus = () => api.get('/Api/GetLogin')

// 扫码登录确认
export const pnglogin = () => api.get('/Api/Pnglogin')

// 获取浏览器页面截图
export const getScrlk = () => api.get('/Api/GetScrlk')

// Gzip压缩后base64编码
const encodeGzipBase64 = async (str) => {
  const bytes = new TextEncoder().encode(str)
  const cs = new CompressionStream('gzip')
  const writer = cs.writable.getWriter()
  writer.write(bytes)
  writer.close()
  const reader = cs.readable.getReader()
  const chunks = []
  let result = new Uint8Array(0)
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    const newResult = new Uint8Array(result.length + value.length)
    newResult.set(result, 0)
    newResult.set(value, result.length)
    result = newResult
  }
  return btoa(String.fromCharCode(...result))
}

// 登录
export const login = async (cookie) => {
  const jsonStr = JSON.stringify(cookie)
  const encoder = new TextEncoder()
  const inputData = encoder.encode(jsonStr)
  const cs = new CompressionStream('gzip')
  const writer = cs.writable.getWriter()
  writer.write(inputData)
  writer.close()
  const output = await new Response(cs.readable).arrayBuffer()
  const base64 = btoa(String.fromCharCode(...new Uint8Array(output)))
  return api.post('/Api/login', { cooke: base64, gzip_flag: true })
}

// 获取二维码
export const getLoginPng = (refresh = 0) =>
  api.get('/Api/login/Init/GetLoginPng', { params: { refresh } })

// 强制退出登录
export const dieLogin = () => api.get('/Api/DieLogin')

// 获取Cookie
// 面板密码走请求体（原来放 query string 会明文进访问日志）
export const getCooker = (password) => api.post('/Api/login/Init/GetCooker', { password })

// 发送验证码（手机号属于隐私，走请求体而不是 URL）
export const sendVerifyCode = (areacode, phone) => api.post('/Api/LoginPhone', { areacode, phone })

// 提交验证码
// 提交验证码（短信验证码属于凭据，走请求体，不进 URL / access log）
export const submitVerifyCode = (code) => api.post('/Api/LoginPhoneInput', { code })

// 退出登录（后端只接受 POST：GET 会 405，token 不会被吊销）
export const logout = () => api.post('/Api/logout')

// 获取好友列表
export const getFriendsList = () => api.get('/Api/GetFriendsList', { timeout: 30000 })

// 发送消息
// 消息正文和好友昵称都算隐私，走请求体：放 query string 会明文进 nginx access log
// options 是给后续新增能力（灵签 sign、图片 image_url）留的扩展位：老调用点不传时
// 展开空对象，请求体一个字段都不多，行为与以前完全一致，不用回头改所有调用点。
export const sendMessage = (name, text, options = {}) => api.post('/Api/Send', { name, text, ...options })

// 发送一条文昌帝君灵签（签图 + 签文文字）
// 单独包一层而不是让每个页面自己拼 { sign: 'wenchang' }：这个魔法字符串一旦后端改名，
// 只改这里一处即可；text 留空时后端会用当天签文自动补正文。
// options 可带 image_only: true —— 只发签图，不附任何文字（面板上的「只发图」）。
export const sendWenchangSign = (name, text = '', options = {}) =>
  sendMessage(name, text, { sign: 'wenchang', ...options })

// 添加定时任务（text 留空 = 每天发送前现取一条文案）
// options 里可带 sign: true —— 该任务每天发送文昌帝君灵签图文
// options 里可带 source: 'hitokoto' —— 正文改成每次发送前现取一句一言
export const addTask = (time, name, text, options = {}) => api.post('/Time/add', { time, name, text, ...options })

// 删除定时任务
export const delTask = (task_id) => api.post('/Time/del', { task_id })

// 修改定时任务（text 传 undefined 表示不改文案）
// options.sign 是布尔值：后端只在显式传了才覆盖原值，所以「把灵签任务改回普通任务」
// 必须传 sign: false，不能靠省略字段（省略等于保持原样，关不掉）。
// options.source 同理（'text' / 'hitokoto'）：显式传才覆盖，省略等于保持原值。
export const editTask = (name, new_time, text, options = {}) => api.post('/Time/edit', { name, new_time, text, ...options })

// 获取任务列表
export const getTaskList = () => api.get('/Time/getlist')

// 立即试发一条定时任务（**真发一条**）：按任务当前配置马上发一次，用来验证
// 「这条任务现在到底能不能发出去」，不用等到设定时间。
// options 可带 image_only: true（只发签图，仅对灵签任务有意义）。
export const testTask = (task_id, options = {}) => api.post('/Time/test', { task_id, ...options })

// 预览一句一言（只取回来显示给用户看，不发送任何消息）
// 用途：任务选了「一言接口」时，让用户先看看这个接口现在能不能用、取到的是什么。
// 接口：成功 { code: 200, data: { text } }，取不到 { code: 400, data: '原因' }。
export const previewHitokoto = () => api.get('/Api/Hitokoto/Preview')

// ===== 女朋友模式（早安 / 午安 / 晚安 + 和风天气）=====
// 配置里的 host / key 是凭据（和风天气的专属域名 + API Key），只保存在本机。
// 接口调用方读的是 res.code / res.data —— 响应拦截器已经把响应体解包，没有 res.status；
// 成功时 res.data 是**对象**，失败时 res.data 是中文原因**字符串**，两种都要处理。
export const getGirlfriendConfig = () => api.get('/Api/Girlfriend/Config')

// body 为部分字段：省略 = 保持后端已保存的值（key 留空即不修改凭据）
export const saveGirlfriendConfig = (data) => api.post('/Api/Girlfriend/Config', data)

// refresh=1 强制绕过缓存，用于「测试天气」当场验证 host/key 是否可用
export const getGirlfriendWeather = (refresh = 0) => api.get('/Api/Girlfriend/Weather', { params: { refresh } })

// period: auto / morning / noon / night —— 只取回来显示，不发送任何消息
export const previewGirlfriend = (period = 'auto') => api.get('/Api/Girlfriend/Preview', { params: { period } })

// 获取用户名
export const getUsername = () => api.get('/Api/GetUsername')

// 修改密码（改完所有已登录会话都会失效，需要重新登录）
export const changePassword = (old_password, new_password) =>
  api.post('/Api/ChangePassword', { old_password, new_password })

// 获取上次登录IP
export const getLastLoginIP = () => api.get('/Api/GetLastLoginIP')

// 强制登录状态
export const forceLogin = () => api.get('/Api/LoginDebug')

// 获取项目启动时间
export const getHome = () => api.get('/Home')

// ===== 抖音身份验证（二次验证）=====
// 获取当前身份验证面板状态
export const getVerifyState = (shot = 0) => api.get('/Api/Verify/State', { params: { shot } })

// 选择验证方式：sms=接收短信验证码 / face=手机刷脸验证 / password=验证登录密码 / sms_send=发送短信验证
export const selectVerifyOption = (option, shot = 0) =>
  api.get('/Api/Verify/Select', { params: { option, shot } })

// 提交短信验证码（身份验证面板）
// 提交身份验证面板的短信验证码（凭据，走请求体）
export const submitVerifyPanelCode = (code) => api.post('/Api/Verify/Code', { code })

// 提交账号登录密码完成验证（密码走请求体，不进 URL）
export const submitVerifyPassword = (password) => api.post('/Api/Verify/Password', { password })

// 回退到验证方式列表（上一次验证失败后换一种验证方式）
export const backVerifyMethods = (shot = 0) => api.get('/Api/Verify/Back', { params: { shot } })

// ===== 发送预检（Dry Run，不发送任何消息）=====
export const checkSend = (name) => api.post('/Api/Send/Check', { name })

// ===== 消息通知 =====
export const getNotifyConfig = () => api.get('/Api/Notify/Get')
// url 传 undefined 表示不修改已保存的地址，传 '' 表示清空
export const setNotifyConfig = (payload) => api.post('/Api/Notify/Set', payload)
// channel：'push' 只测推送 / 'email' 只测邮件 / 不传则测已配置的通道
// email：可带上还没保存的邮箱设置（密码留空则用已保存的那份）
export const testNotify = (payload = {}) => api.post('/Api/Notify/Test', payload)

// ===== 信息日志 =====
// 读取程序运行日志（浏览器/登录/验证/发消息/定时任务），最新在前
export const getLogs = (params = {}) => api.get('/Api/Logs', { params })

// 清空信息日志（后端只接受 POST：GET 会 405）
export const clearLogs = () => api.post('/Api/Logs/Clear')

// ===== 安全中心 =====
// 一次拿回「面板 / 账号 / 数据 / 运行时」四组巡检结果和安全评分（只读，不改任何状态）
export const getSecurityOverview = () => api.get('/Api/Security/Overview')

// 吊销全部面板会话（包括当前这一个，调用完会直接掉线）。
// 后端要求再输一次面板密码，所以密码必须走请求体 —— 放进 query 会写进 nginx 访问日志。
export const revokeAllSessions = (password) => api.post('/Api/Security/RevokeAll', { password })

// ===== 消息记录 =====
// 发送记录列表（分页 + 按状态/日期/关键词筛选）。
// 数据就是后端的发送记账（state.json 的 send_history），只读。
// params: { page, size, status, keyword, days }
export const getHistoryList = (params = {}) => api.get('/Api/History/List', { params })

// 清空消息记录（台账）。scope='old'（默认）只清今天以前的记录；
// scope='all' 连今天一起清 —— 今天的记录是防重复发送的依据，清掉后同一天
// 可能重复发给同一个好友，所以前端必须对 all 做明确警告。
export const clearHistory = (scope = 'old') => api.post('/Api/History/Clear', { scope })

// ===== 抖音账号 =====
// 单用户版只有一个抖音账号 —— 就是 chrome-profile/ 里的那一份登录态，
// 所以这里是「账号信息」而不是「账号管理」：没有增删改账号这回事。
// 返回 Cookie 状态与到期时间、好友数、今日已发、备注、添加时间等真实运行状态。
export const getAccountInfo = () => api.get('/Api/Account/Info')

// 保存账号备注（纯本地标记，最长 100 字，后端会校验长度）
export const setAccountNote = (note) => api.post('/Api/Account/Note', { note })

// 注：这里原来还有一整套「账号管理」接口（/Api/Accounts/List|Create|Update|Delete|
// SetDefault|Detail|Switch），但 backend.py 从来没有这些路由 —— 单用户版本只认
// chrome-profile 里那一份抖音登录态，不存在「多账号」。现在改成上面的真实实现，
// 不要再照旧文档把那一套加回来。

export default api
