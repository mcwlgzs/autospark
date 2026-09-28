import { defineStore } from 'pinia'
import axios from 'axios'
import { ref, computed } from 'vue'

export const useUserStore = defineStore('user', () => {
  // localStorage 里的内容可能不是合法 JSON（手动改过、被别的脚本写坏）。
  // 直接 JSON.parse 会抛在模块初始化阶段，整个 SPA 白屏且无法自愈 —— 兜底成 {}。
  const readUserInfo = () => {
    try {
      const parsed = JSON.parse(localStorage.getItem('userInfo') || '{}')
      return parsed && typeof parsed === 'object' ? parsed : {}
    } catch (e) {
      localStorage.removeItem('userInfo')
      return {}
    }
  }

  // 状态
  const token = ref(localStorage.getItem('token') || '')
  const userInfo = ref(readUserInfo())
  // 「还在用内置默认密码」标记：登录响应里带回来的，用于登录后提示改密码
  const mustChangePassword = ref(false)

  // 计算属性
  const isLoggedIn = computed(() => !!token.value)

  // 登录
  async function login(username, password) {
    try {
      // 密码走请求体：放 query string 会明文写进 nginx 访问日志
      const res = await axios.post('/api/Api/Login/Admin', { username, password })
      if (res.data.code == 200 || res.data.code == '200') {
        const newToken = res.data.data
        token.value = newToken
        userInfo.value = { username, loginTime: new Date().toISOString() }
        localStorage.setItem('token', newToken)
        localStorage.setItem('userInfo', JSON.stringify(userInfo.value))
        // 后端会告诉我们「是否还在用内置默认密码」：这种情况下敏感操作会被拦，
        // 登录后必须显眼提示一次，否则用户只会遇到一堆看不懂的 403。
        mustChangePassword.value = !!res.data.must_change_password
        // 注意：这里不需要手动设置 axios 默认请求头。
        // 业务请求走的是 src/api/douyin.js 里 axios.create() 出来的实例，
        // 它的 Authorization 由请求拦截器从 localStorage 现取（见 douyin.js 请求拦截器）。
        // 旧代码在这里写 axios.defaults.headers.common 其实对此实例不生效 ——
        // 实例创建时就已拷贝了默认头，之后改 defaults 影响不到它，纯属误导。
        return { success: true, message: '登录成功' }
      } else {
        return { success: false, message: res.data.data || '登录失败' }
      }
    } catch (error) {
      const msg = error.response?.data?.data || error.message || '登录失败'
      return { success: false, message: msg }
    }
  }

  // 登出
  function logout() {
    token.value = ''
    userInfo.value = {}
    localStorage.removeItem('token')
    localStorage.removeItem('userInfo')
    // 拦截器同时认 douyin_token 这个旧键名，登出时一并清掉，避免「登出后仍带着旧 token」
    localStorage.removeItem('douyin_token')
  }

  // 恢复登录状态（token 由 douyin.js 的请求拦截器按需读取，这里只需确认本地有值）
  function restoreSession() {
    if (token.value) {
      userInfo.value = readUserInfo()
    }
  }

  return {
    token,
    userInfo,
    mustChangePassword,
    isLoggedIn,
    login,
    logout,
    restoreSession
  }
})
