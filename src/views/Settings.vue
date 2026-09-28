<template>
  <div class="settings-container">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>账户配置</span>
        </div>
      </template>

      <div class="account-section">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="登录状态">
            <el-tag :type="loginStatus ? 'success' : 'danger'">
              {{ loginStatus ? (username ? '已登录: ' + username : '已登录') : '未登录' }}
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>

        <div class="login-action">
          <el-button type="primary" :icon="Key" @click="handleLogin" :loading="loginLoading" :disabled="loginStatus">
            扫码登录
          </el-button>
          <el-button type="primary" :icon="Message" @click="phoneDialogVisible = true" :disabled="loginStatus">
            验证码登录
          </el-button>
          <el-button type="warning" :icon="WarnTriangleFilled" @click="openVerifyDialog">
            身份验证
          </el-button>
          <el-button :icon="Edit" @click="manualDialogVisible = true" :disabled="loginStatus">
            手动登录
          </el-button>
          <el-button :icon="Refresh" @click="handleRefreshStatus" :loading="refreshStatusLoading">
            刷新状态
          </el-button>
          <el-button :icon="Document" @click="cookieDialogVisible = true">
            获取Base64Cookie
          </el-button>
          <el-button :icon="SwitchButton" type="danger" @click="handleDieLogin">
            强制退出登录
          </el-button>
        </div>
      </div>
    </el-card>

    <!-- 手动登录弹窗 -->
    <el-dialog v-model="manualDialogVisible" title="手动登录" width="500px" destroy-on-close>
      <el-form :model="manualForm" label-width="100px">
        <el-form-item label="Base64Cookie">
          <el-input
            v-model="manualForm.cookie"
            type="textarea"
            :rows="6"
            placeholder="请输入登录Base64Cookie"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="manualDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleManualLogin" :loading="manualLoading">
          验证登录
        </el-button>
      </template>
    </el-dialog>

    <!-- 获取Cookie弹窗 -->
    <el-dialog v-model="cookieDialogVisible" title="获取Base64Cookie" width="400px" destroy-on-close>
      <el-form :model="cookieForm" label-width="100px">
        <el-form-item label="确认密码">
          <el-input
            v-model="cookieForm.password"
            type="password"
            placeholder="请输入密码确认"
            @keyup.enter="handleGetCookie"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="cookieDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleGetCookie" :loading="cookieLoading">
          获取Cookie
        </el-button>
      </template>
    </el-dialog>

    <!-- 验证码登录弹窗 -->
    <el-dialog v-model="phoneDialogVisible" title="验证码登录" width="400px" destroy-on-close>
      <el-form :model="phoneForm" label-width="80px">
        <el-form-item label="手机号">
          <div style="display: flex; gap: 8px;">
            <el-input
              v-model="phoneForm.areacode"
              placeholder="+86"
              style="width: 70px; flex-shrink: 0;"
              @keyup.enter="handleSendCode"
            />
            <el-input
              v-model="phoneForm.phone"
              placeholder="请输入手机号"
              style="flex: 1"
              @keyup.enter="handleSendCode"
            />
          </div>
        </el-form-item>
        <el-form-item label="验证码">
          <div style="display: flex; gap: 10px;">
            <el-input
              v-model="phoneForm.code"
              placeholder="请输入验证码"
              style="flex: 1"
              @keyup.enter="handlePhoneLogin"
            />
            <el-button @click="handleSendCode" :disabled="codeCountdown > 0" :loading="codeLoading">
              {{ codeCountdown > 0 ? `${codeCountdown}s` : '发送验证码' }}
            </el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="phoneDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handlePhoneLogin" :loading="phoneLoading">
          登录
        </el-button>
      </template>
    </el-dialog>

    <!-- 二维码弹窗 -->
    <el-dialog v-model="qrDialogVisible" title="抖音扫码登录" width="350px" destroy-on-close @closed="stopQrTimers">
      <div class="qrcode-container">
        <div v-if="qrcodeUrl" class="qrcode-wrapper">
          <img :src="qrcodeUrl" alt="登录二维码" class="qrcode-img" />
          <p class="qrcode-hint">请使用抖音App扫码登录</p>
        </div>
        <div v-else-if="loading" class="loading-wrapper">
          <el-icon class="is-loading"><Loading /></el-icon>
          <p>正在加载二维码...</p>
        </div>
        <div v-else class="error-wrapper">
          <p>获取二维码失败，请重试</p>
        </div>
      </div>
      <div class="qrcode-actions">
        <el-button :icon="Refresh" @click="handleRefreshCode" :loading="refreshLoading" size="small">
          刷新验证码
        </el-button>
        <el-button :icon="View" @click="handleCheckLogin" :loading="checkLoading" size="small">
          获取登录状态
        </el-button>
      </div>
    </el-dialog>

    <!-- 身份验证（二次验证）弹窗 -->
    <el-dialog
      v-model="verifyDialogVisible"
      title="抖音身份验证（二次验证）"
      width="560px"
      destroy-on-close
      @closed="stopVerifyPolling"
    >
      <template v-if="verifyState && verifyState.active">
        <div class="verify-head">
          <div class="verify-head-main">
            <span>当前账号：{{ verifyState.account || '（未识别）' }}</span>
            <el-tag v-if="verifyState.masked_phone" type="info" size="small" effect="plain">
              {{ verifyState.masked_phone }}
            </el-tag>
          </div>
          <div class="verify-head-sub">
            短信验证码发往账号绑定手机号，与登录页填写的号码无关
          </div>
        </div>

        <div class="verify-step">
          <span class="verify-step-no">1</span>
          <span class="verify-step-title">选择验证方式</span>
        </div>
        <div class="verify-methods">
          <button
            v-for="opt in verifyOptionList"
            :key="opt.key"
            type="button"
            class="verify-method"
            :class="{ 'is-active': verifyState.method === opt.key }"
            :disabled="!!verifyLoading"
            @click="handleSelectVerify(opt.key)"
          >
            <el-icon v-if="verifyState.method === opt.key" class="verify-method-check"><Select /></el-icon>
            <el-icon class="verify-method-icon" :class="{ 'is-loading': verifyLoading === opt.key }"><component :is="opt.icon" /></el-icon>
            <span class="verify-method-label">{{ opt.label }}</span>
            <span class="verify-method-desc">{{ opt.desc }}</span>
          </button>
        </div>

        <div class="verify-step">
          <span class="verify-step-no">2</span>
          <span class="verify-step-title">完成验证</span>
          <el-button
            v-if="verifyState.method"
            link
            type="primary"
            size="small"
            :loading="verifyLoading === 'back'"
            @click="handleBackVerifyMethods"
          >
            换一种方式
          </el-button>
        </div>

        <!-- 短信验证码 -->
        <div v-if="verifyState.method === 'sms'" class="verify-panel">
          <div class="verify-row">
            <el-input
              v-model="verifyCode"
              size="large"
              maxlength="8"
              placeholder="请输入手机收到的验证码"
              @keyup.enter="handleVerifyCode"
            >
              <template #prepend>验证码</template>
            </el-input>
            <el-button type="primary" size="large" :loading="verifyLoading === 'code'" @click="handleVerifyCode">
              提交
            </el-button>
          </div>
          <div class="verify-panel-foot">
            <span>没收到？</span>
            <el-button link type="primary" :loading="verifyLoading === 'sms_send'" @click="handleResendCode">
              重新发送
            </el-button>
          </div>
        </div>

        <!-- 登录密码 -->
        <div v-else-if="verifyState.method === 'password'" class="verify-panel">
          <div class="verify-row">
            <el-input
              v-model="verifyPassword"
              type="password"
              show-password
              size="large"
              placeholder="请输入账号登录密码"
              @keyup.enter="handleVerifyPassword"
            >
              <template #prepend>密码</template>
            </el-input>
            <el-button type="primary" size="large" :loading="verifyLoading === 'password'" @click="handleVerifyPassword">
              提交
            </el-button>
          </div>
          <p class="verify-hint">密码只写入服务器浏览器完成本次验证，不落盘、不记录</p>
        </div>

        <!-- 刷脸二维码 -->
        <div v-else-if="verifyState.method === 'face'" class="verify-panel verify-qr">
          <img v-if="verifyState.qr" :src="verifyState.qr" alt="刷脸验证二维码" />
          <el-empty v-else description="二维码生成中…" :image-size="60" />
          <p class="verify-qr-hint">
            用手机上的抖音扫码；识别不上就长按保存这张图，再用相册识别（每 5 秒自动刷新）
          </p>
        </div>

        <div v-else class="verify-panel verify-placeholder">
          先在上面选一种验证方式，这里会出现对应的操作
        </div>

        <el-collapse class="verify-debug">
          <el-collapse-item title="排查信息（页面文字 / 完整截图）" name="debug">
            <pre v-if="verifyState.text" class="verify-text">{{ verifyState.text }}</pre>
            <img
              v-if="verifyShot"
              class="verify-shot"
              :src="`data:image/png;base64,${verifyShot}`"
              alt="验证页面截图"
            />
          </el-collapse-item>
        </el-collapse>
      </template>
      <el-empty v-else description="当前没有身份验证请求" :image-size="80" />

      <template #footer>
        <el-button :icon="Refresh" :loading="verifyLoading === 'state'" @click="refreshVerifyState(true)">
          刷新状态
        </el-button>
        <el-button type="primary" @click="verifyDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>

  <!-- 修改密码弹窗 -->
  <el-dialog v-model="passwordDialogVisible" title="修改密码" width="400px" destroy-on-close>
    <el-form :model="passwordForm" label-width="90px">
      <el-form-item label="原密码">
        <el-input v-model="passwordForm.old_password" type="password" placeholder="请输入原密码" show-password />
      </el-form-item>
      <el-form-item label="新密码">
        <el-input v-model="passwordForm.new_password" type="password" placeholder="请输入新密码" show-password />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="passwordDialogVisible = false">取消</el-button>
      <el-button type="primary" @click="handleChangePassword" :loading="passwordLoading">
        确认修改
      </el-button>
    </template>
  </el-dialog>

  <!-- 后台配置卡片 -->
  <el-card style="margin-top: 20px">
    <template #header>
      <div class="card-header">
        <span>后台配置</span>
      </div>
    </template>
    <div class="config-row">
      <div class="config-item">
        <span class="config-label">上次登录IP：</span>
        <span class="config-value">{{ lastLoginIP }}</span>
      </div>
      <el-button type="primary" :icon="Lock" @click="passwordDialogVisible = true">
        修改密码
      </el-button>
    </div>
  </el-card>

  <!-- 消息通知卡片 -->
  <el-card style="margin-top: 20px">
    <template #header>
      <div class="card-header">
        <span>消息通知</span>
      </div>
    </template>
    <div class="notify-desc">
      定时任务失败、抖音风控、登录失效时会推送到手机（对接 showdoc 推送服务，也兼容任意同类 webhook）。
      推送地址里含凭据，只保存在本机，不会写进日志。下面还可以再配一条邮箱通道，两边互不影响。
    </div>
    <div class="notify-form">
      <div class="config-item">
        <span class="config-label">推送地址：</span>
        <el-input
          v-model="notifyForm.url"
          :placeholder="notifyForm.url_set ? '已配置（留空表示不修改）' : 'https://push.showdoc.com.cn/server/api/push/你的token'"
          show-password
          clearable
          style="max-width: 520px"
        />
        <el-button v-if="notifyForm.url_set" @click="handleClearNotifyUrl">清空地址</el-button>
      </div>
      <div class="config-item">
        <span class="config-label">开启通知：</span>
        <el-switch v-model="notifyForm.enabled" />
        <span class="config-label" style="margin-left: 24px">成功也通知：</span>
        <el-switch v-model="notifyForm.on_success" :disabled="!notifyForm.enabled" />
        <span class="notify-hint">（关闭时只在失败/风控/登录失效时推送）</span>
      </div>
      <div class="config-item">
        <el-button type="primary" :icon="Bell" @click="handleSaveNotify" :loading="notifySaving">
          保存
        </el-button>
        <el-button :icon="Promotion" @click="handleTestNotify" :loading="notifyTesting">
          发送测试推送
        </el-button>
        <span v-if="notifyForm.url_masked" class="config-label">当前地址：{{ notifyForm.url_masked }}</span>
      </div>

      <el-divider content-position="left">邮箱通知</el-divider>
      <div class="notify-desc">
        用你自己的邮箱发信（QQ 邮箱 / 163 / Gmail / 企业邮局都行）。授权码只保存在本机，
        接口不会回传明文，日志里也只留打码后的地址。
      </div>
      <div class="config-item">
        <span class="config-label">开启邮箱：</span>
        <el-switch v-model="notifyForm.email_enabled" />
        <span class="notify-hint">（需先开启上面的总开关）</span>
        <el-tag v-if="notifyForm.email_configured" type="success" size="small" effect="plain">已配置</el-tag>
        <el-tag v-else type="info" size="small" effect="plain">未配置</el-tag>
      </div>
      <div class="config-item">
        <span class="config-label">SMTP 服务器：</span>
        <el-input
          v-model="notifyForm.email_host"
          :placeholder="notifyForm.email_host_set ? '已配置（留空表示不修改）' : 'smtp.qq.com'"
          clearable
          style="max-width: 340px"
        />
        <span class="config-label">端口：</span>
        <el-input-number v-model="notifyForm.email_port" :min="1" :max="65535" controls-position="right" style="width: 130px" />
        <span class="config-label">SSL：</span>
        <el-switch v-model="notifyForm.email_ssl" />
      </div>
      <div class="config-item">
        <span class="config-label">登录账号：</span>
        <el-input
          v-model="notifyForm.email_username"
          :placeholder="notifyForm.email_username_set ? '已配置（留空表示不修改）' : '一般是完整邮箱地址'"
          clearable
          style="max-width: 340px"
        />
        <span class="config-label">授权码：</span>
        <el-input
          v-model="notifyForm.email_password"
          type="password"
          show-password
          clearable
          :placeholder="notifyForm.email_password_set ? '已配置（留空表示不修改）' : '邮箱授权码 / 应用专用密码'"
          style="max-width: 300px"
        />
      </div>
      <div class="config-item">
        <span class="config-label">发件邮箱：</span>
        <el-input
          v-model="notifyForm.email_from"
          :placeholder="notifyForm.email_from_set ? '已配置（留空表示不修改）' : '留空则用登录账号'"
          clearable
          style="max-width: 340px"
        />
        <span class="config-label">收件邮箱：</span>
        <el-input
          v-model="notifyForm.email_to"
          :placeholder="notifyForm.email_to_set ? '已配置（留空表示不修改）' : '通知发到哪个邮箱'"
          clearable
          style="max-width: 300px"
        />
        <el-button v-if="notifyForm.email_to_set" @click="handleClearEmailTo">清空收件人</el-button>
      </div>
      <div class="config-item">
        <el-tag v-if="notifyForm.email_to_masked" type="info" size="small" effect="plain">
          收件邮箱：{{ notifyForm.email_to_masked }}
        </el-tag>
        <el-tag v-if="notifyForm.email_from_masked" type="info" size="small" effect="plain">
          发件邮箱：{{ notifyForm.email_from_masked }}
        </el-tag>
      </div>
      <div class="config-item">
        <el-button type="primary" :icon="Bell" @click="handleSaveNotify" :loading="notifySaving">
          保存
        </el-button>
        <el-button :icon="Promotion" @click="handleTestEmail" :loading="emailTesting">
          发送测试邮件
        </el-button>
        <span class="notify-hint">（测试用的是上面表单里填的值，不必先保存）</span>
      </div>
    </div>
  </el-card>

  <!-- 调试区域卡片 -->
  <el-card style="margin-top: 20px">
    <template #header>
      <div class="card-header">
        <span>调试功能</span>
      </div>
    </template>
    <div class="debug-row">
      <el-button type="primary" :icon="Picture" @click="handleGetScreenshot" :loading="screenshotLoading">
        获取浏览器页面截图
      </el-button>
      <el-button type="warning" :icon="WarnTriangleFilled" @click="handleForceLogin">
        强制登录状态
      </el-button>
    </div>
    <el-dialog v-model="screenshotPreviewVisible" title="浏览器截图" width="600px" destroy-on-close>
      <img :src="screenshotUrl" alt="浏览器截图" class="screenshot-img" />
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted, onActivated, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Key, Refresh, View, Loading, Edit, Lock, Document, SwitchButton, Picture, Message, WarnTriangleFilled, Camera, Select, Bell, Promotion } from '@element-plus/icons-vue'
import { getInitStatus, getLoginStatus, initBrowser, getLoginPng, login, getUsername, changePassword, getLastLoginIP, getFriendsList, getCooker, logout, pnglogin, getScrlk, dieLogin, sendVerifyCode, submitVerifyCode, forceLogin, getVerifyState, selectVerifyOption, submitVerifyPanelCode, submitVerifyPassword, backVerifyMethods, fallbackError, getNotifyConfig, setNotifyConfig, testNotify } from '../api/douyin'
import { loginStatus, hasLoaded, setLoginStatus, setFriendsList } from '../stores/browser'
import { useUserStore } from '../stores/user'

const loginLoading = ref(false)
const refreshLoading = ref(false)
const checkLoading = ref(false)
const refreshStatusLoading = ref(false)
const qrDialogVisible = ref(false)
const qrcodeUrl = ref('')
const loading = ref(false)
const manualDialogVisible = ref(false)
const manualLoading = ref(false)
const manualForm = ref({
  cookie: ''
})
const username = ref(localStorage.getItem('douyin_username') || '')
const usernameLoaded = ref(localStorage.getItem('douyin_username_loaded') === '1')
const notifyForm = ref({
  enabled: false,
  on_success: false,
  url: '',
  url_set: false,
  url_masked: '',
  email_enabled: false,
  email_configured: false,
  email_host: '',
  email_port: 465,
  email_ssl: true,
  email_username: '',
  email_password: '',
  email_from: '',
  email_to: '',
  email_host_set: false,
  email_username_set: false,
  email_password_set: false,
  email_from_set: false,
  email_to_set: false,
  email_from_masked: '',
  email_to_masked: ''
})
const notifySaving = ref(false)
const notifyTesting = ref(false)
const emailTesting = ref(false)

const passwordDialogVisible = ref(false)
const passwordLoading = ref(false)
const passwordForm = ref({
  old_password: '',
  new_password: ''
})
const lastLoginIP = ref(localStorage.getItem('douyin_last_login_ip') || '加载中...')
const settingsLoaded = ref(localStorage.getItem('douyin_settings_loaded') === '1')
const cookieDialogVisible = ref(false)
const cookieLoading = ref(false)
const cookieForm = ref({
  password: ''
})
const screenshotLoading = ref(false)
const screenshotUrl = ref('')
const screenshotPreviewVisible = ref(false)
const phoneDialogVisible = ref(false)
const phoneLoading = ref(false)
const codeLoading = ref(false)
const codeCountdown = ref(0)
let codeTimer = null
const phoneForm = ref({
  areacode: '+86',
  phone: '',
  code: ''
})

// ===== 二维码登录：自动续期 + 自动检测扫码结果 =====
let qrRefreshTimer = null
let qrPollTimer = null

// 静默请求：轮询时不经过 axios 拦截器，避免失败时反复弹提示
const silentGet = async (path) => {
  try {
    const token = localStorage.getItem('token') || localStorage.getItem('douyin_token')
    const headers = token ? { Authorization: `Bearer ${token}` } : {}
    const resp = await fetch(`/api${path}`, { headers })
    if (!resp.ok) return null
    return await resp.json()
  } catch (error) {
    return null
  }
}

const stopQrTimers = () => {
  if (qrRefreshTimer) {
    clearInterval(qrRefreshTimer)
    qrRefreshTimer = null
  }
  if (qrPollTimer) {
    clearInterval(qrPollTimer)
    qrPollTimer = null
  }
}

const handleScanSuccess = async () => {
  stopQrTimers()
  qrDialogVisible.value = false
  loginStatus.value = true
  setLoginStatus(true)
  username.value = ''
  usernameLoaded.value = false
  localStorage.removeItem('douyin_username')
  localStorage.removeItem('douyin_username_loaded')
  ElMessage.success('扫码登录成功')
  await fetchUsername()
  await fetchFriendsList()
}

const startQrTimers = () => {
  stopQrTimers()
  // 每 4 秒检查一次扫码结果（/Api/GetLogin 恒定返回 200，不会打扰用户）
  qrPollTimer = setInterval(async () => {
    if (!qrDialogVisible.value) {
      stopQrTimers()
      return
    }
    const res = await silentGet('/Api/GetLogin')
    if (res && res.code == 200 && res.data === 'Yes') {
      await handleScanSuccess()
    }
  }, 4000)
  // 每 15 秒续期一次二维码（后端仅在过期时才真正刷新）
  qrRefreshTimer = setInterval(async () => {
    if (!qrDialogVisible.value) {
      stopQrTimers()
      return
    }
    const res = await silentGet('/Api/login/Init/GetLoginPng')
    if (!res) return
    if (res.status === 'already_logged_in') {
      await handleScanSuccess()
      return
    }
    if (res.code == 200 && res.data) {
      qrcodeUrl.value = res.data
    }
  }, 15000)
}

onUnmounted(stopQrTimers)

// ===== 抖音身份验证（二次验证）=====
const verifyDialogVisible = ref(false)
const verifyState = ref(null)
const verifyLoading = ref('')
const verifyCode = ref('')
const verifyPassword = ref('')
const verifyShot = ref('')
let verifyPollTimer = null

// 三种验证方式（「发送短信验证」是动作不是方式，移到短信面板里做「重新发送」）
const verifyOptionList = [
  { key: 'sms', label: '短信验证码', desc: '发到绑定手机', icon: Message },
  { key: 'face', label: '手机刷脸', desc: '抖音扫码完成', icon: Camera },
  { key: 'password', label: '登录密码', desc: '用账号密码验证', icon: Lock }
]

const stopVerifyPolling = () => {
  if (verifyPollTimer) {
    clearInterval(verifyPollTimer)
    verifyPollTimer = null
  }
}

const applyVerifyState = async (state) => {
  if (!state) return
  verifyState.value = state
  if (state.screenshot) verifyShot.value = state.screenshot
  if (state.logged_in) {
    stopVerifyPolling()
    verifyDialogVisible.value = false
    await handleScanSuccess()
  }
}

const refreshVerifyState = async (withShot = false) => {
  verifyLoading.value = 'state'
  try {
    const res = await getVerifyState(withShot ? 1 : 0)
    await applyVerifyState(res && res.data)
  } catch (error) {
    // 拦截器已提示，静默
  } finally {
    verifyLoading.value = ''
  }
}

const openVerifyDialog = async () => {
  verifyDialogVisible.value = true
  stopVerifyPolling()
  await refreshVerifyState(true)
  verifyPollTimer = setInterval(() => {
    if (!verifyDialogVisible.value) {
      stopVerifyPolling()
      return
    }
    refreshVerifyState(false)
  }, 5000)
}

const handleSelectVerify = async (key) => {
  verifyLoading.value = key
  try {
    const res = await selectVerifyOption(key, key === 'face' ? 1 : 0)
    await applyVerifyState(res && res.state)
    if (res) ElMessage.success(res.data || '已执行')
  } catch (error) {
    fallbackError(error, '操作失败，可点「重新选择验证方式」后再试')
  } finally {
    verifyLoading.value = ''
  }
}

// 回退到验证方式列表：验证失败 / 想换一种验证方式时使用
const handleBackVerifyMethods = async () => {
  verifyLoading.value = 'back'
  try {
    const res = await backVerifyMethods(1)
    await applyVerifyState(res && res.state)
    ElMessage.success((res && res.data) || '已回到验证方式列表')
  } catch (error) {
    fallbackError(error, '回退失败，请刷新状态后重试')
  } finally {
    verifyLoading.value = ''
  }
}

// 重新发送短信验证码
const handleResendCode = async () => {
  verifyLoading.value = 'sms_send'
  try {
    const res = await selectVerifyOption('sms_send', 0)
    await applyVerifyState(res && res.state)
    ElMessage.success((res && res.data) || '验证码已重新发送')
  } catch (error) {
    fallbackError(error, '重新发送失败，请稍后再试')
  } finally {
    verifyLoading.value = ''
  }
}

const handleVerifyCode = async () => {
  if (!verifyCode.value.trim()) {
    ElMessage.warning('请输入短信验证码')
    return
  }
  verifyLoading.value = 'code'
  try {
    const res = await submitVerifyPanelCode(verifyCode.value.trim())
    verifyCode.value = ''
    await applyVerifyState(res && res.state)
    ElMessage.success((res && res.data) || '提交成功')
  } catch (error) {
    fallbackError(error, '验证码未通过，可重试或点「重新选择验证方式」换一种方式')
  } finally {
    verifyLoading.value = ''
  }
}

const handleVerifyPassword = async () => {
  if (!verifyPassword.value) {
    ElMessage.warning('请输入账号登录密码')
    return
  }
  verifyLoading.value = 'password'
  try {
    const res = await submitVerifyPassword(verifyPassword.value)
    verifyPassword.value = ''
    await applyVerifyState(res && res.state)
    ElMessage.success((res && res.data) || '提交成功')
  } catch (error) {
    fallbackError(error, '密码未通过，可重试或点「重新选择验证方式」换一种方式')
  } finally {
    verifyLoading.value = ''
  }
}

onUnmounted(stopVerifyPolling)

const fetchLastLoginIP = async () => {
  try {
    const res = await getLastLoginIP()
    if (res.code == 200 || res.code == '200') {
      lastLoginIP.value = res.data || '无'
      localStorage.setItem('douyin_last_login_ip', lastLoginIP.value)
    }
  } catch (error) {
    lastLoginIP.value = '获取失败'
  }
}

const fetchUsername = async () => {
  try {
    const res = await getUsername()
    if (res.code == 200 || res.code == '200') {
      username.value = res.data
      usernameLoaded.value = true
      localStorage.setItem('douyin_username', res.data)
      localStorage.setItem('douyin_username_loaded', '1')
    }
  } catch (error) {
    // 获取失败不提示，静默处理
  }
}

const checkLoginStatus = async () => {
  try {
    const res = await getLoginStatus()
    loginStatus.value = res.data === 'Yes'
    setLoginStatus(loginStatus.value)
    if (loginStatus.value && !usernameLoaded.value) {
      await fetchUsername()
    }
  } catch (error) {
    loginStatus.value = false
    setLoginStatus(false)
  }
}

const handleRefreshStatus = async () => {
  refreshStatusLoading.value = true
  try {
    usernameLoaded.value = false
    localStorage.removeItem('douyin_username')
    localStorage.removeItem('douyin_username_loaded')
    await checkLoginStatus()
    await fetchLastLoginIP()
    ElMessage.success(loginStatus.value ? '已登录' : '未登录')
  } finally {
    refreshStatusLoading.value = false
  }
}

const handleCheckLogin = async () => {
  checkLoading.value = true
  try {
    const res = await pnglogin()
    if (res.code == 202 || res.code == '202' || res.data === 'two_factor_required') {
      loginStatus.value = false
      setLoginStatus(false)
      qrDialogVisible.value = false
      stopQrTimers()
      await openVerifyDialog()
      ElMessage.warning('扫码已通过，请完成身份验证（二次验证）')
      return
    }
    loginStatus.value = res.code == 200 || res.code == '200'
    setLoginStatus(loginStatus.value)
    if (loginStatus.value) {
      ElMessage.success('登录成功，扫码登录窗口将关闭')
      qrDialogVisible.value = false
      username.value = ''
      usernameLoaded.value = false
      localStorage.removeItem('douyin_username')
      localStorage.removeItem('douyin_username_loaded')
      await fetchUsername()
      // 登录成功后请求好友列表
      await fetchFriendsList()
    } else {
      ElMessage.warning('未登录，请继续扫码')
    }
  } catch (error) {
    ElMessage.error('扫码登录失败，请重试')
  } finally {
    checkLoading.value = false
  }
}

const fetchFriendsList = async () => {
  try {
    const res = await getFriendsList()
    if (res.code === 200) {
      const list = res.data.list || {}
      const formattedList = Object.entries(list).map(([name, [avatar, fire]]) => ({
        name,
        avatar,
        fire
      }))
      setFriendsList(formattedList)
    }
  } catch (error) {
    // 获取失败静默处理
  }
}

const syncSettingsStatus = async ({ resetUsername = false } = {}) => {
  if (resetUsername) {
    username.value = ''
    usernameLoaded.value = false
    localStorage.removeItem('douyin_username')
    localStorage.removeItem('douyin_username_loaded')
  }
  await checkLoginStatus()
  await fetchLastLoginIP()
  if (loginStatus.value) {
    await fetchUsername()
    await fetchFriendsList()
  }
}

const handleRefreshCode = async () => {
  refreshLoading.value = true
  try {
    await syncSettingsStatus()
    if (loginStatus.value) {
      qrDialogVisible.value = false
      ElMessage.success('当前浏览器已登录，无需刷新二维码')
      return
    }
    // 先初始化浏览器
    await initBrowser()
    // 强制换一张新二维码（refresh=1）
    const res = await getLoginPng(1)
    if (res.status === 'already_logged_in') {
      await syncSettingsStatus({ resetUsername: true })
      qrDialogVisible.value = false
      ElMessage.success('当前浏览器已登录，无需扫码')
    } else if (res.data) {
      const changed = res.data !== qrcodeUrl.value
      qrcodeUrl.value = res.data
      qrDialogVisible.value = true
      ElMessage.success(changed ? '已刷新二维码' : '二维码仍有效，暂时不用刷新（过期后会自动换新）')
      startQrTimers()
    } else {
      ElMessage.error('获取二维码失败')
    }
  } catch (error) {
    ElMessage.error('刷新失败，请确保浏览器已初始化')
  } finally {
    refreshLoading.value = false
  }
}

const handleManualLogin = async () => {
  if (!manualForm.value.cookie.trim()) {
    ElMessage.warning('请输入Base64Cookie')
    return
  }
  manualLoading.value = true
  try {
    const res = await login(manualForm.value.cookie)
    if (res.data === 'ok') {
      ElMessage.success('登录成功')
      loginStatus.value = true
      setLoginStatus(true)
      manualDialogVisible.value = false
      username.value = ''
      usernameLoaded.value = false
      localStorage.removeItem('douyin_username')
      localStorage.removeItem('douyin_username_loaded')
      await fetchUsername()
      await fetchFriendsList()
    } else {
      ElMessage.error('登录失败，Cookie无效')
    }
  } catch (error) {
    ElMessage.error('登录失败，请检查Cookie')
  } finally {
    manualLoading.value = false
  }
}

const handleChangePassword = async () => {
  if (!passwordForm.value.old_password) {
    ElMessage.warning('请输入原密码')
    return
  }
  if (!passwordForm.value.new_password) {
    ElMessage.warning('请输入新密码')
    return
  }
  if (passwordForm.value.new_password.length < 8) {
    ElMessage.warning('新密码至少 8 位')
    return
  }
  if (/^\d+$/.test(passwordForm.value.new_password)) {
    ElMessage.warning('新密码不能是纯数字')
    return
  }
  if (passwordForm.value.old_password === passwordForm.value.new_password) {
    ElMessage.warning('新密码不能与原密码相同')
    return
  }
  passwordLoading.value = true
  try {
    const res = await changePassword(passwordForm.value.old_password, passwordForm.value.new_password)
    if (res.code == 200 || res.code == '200') {
      passwordDialogVisible.value = false
      passwordForm.value.old_password = ''
      passwordForm.value.new_password = ''
      // 后端在改密后会失效全部已签发的登录状态（这是刻意的安全设计），
      // 所以这里直接把用户带回登录页，而不是留在一个所有请求都会 401 的界面上。
      ElMessage.success('密码修改成功，请用新密码重新登录')
      const userStore = useUserStore()
      userStore.logout()
      setTimeout(() => window.location.replace('/login'), 1200)
    } else {
      ElMessage.error(res.data || '修改失败')
    }
  } catch (error) {
    fallbackError(error, '修改失败')
  } finally {
    passwordLoading.value = false
  }
}

const handleGetCookie = async () => {
  if (!cookieForm.value.password) {
    ElMessage.warning('请输入密码')
    return
  }
  cookieLoading.value = true
  try {
    const res = await getCooker(cookieForm.value.password)
    if (res.code == 200) {
      const textArea = document.createElement('textarea')
      textArea.value = res.data.cooke
      textArea.style.position = 'fixed'
      textArea.style.left = '-9999px'
      document.body.appendChild(textArea)
      textArea.select()
      try {
        document.execCommand('copy')
        ElMessage.success('Cookie已复制到剪贴板')
        cookieDialogVisible.value = false
        cookieForm.value.password = ''
      } catch {
        ElMessage.error('复制失败，请检查浏览器剪贴板权限后重试')
      } finally {
        document.body.removeChild(textArea)
      }
    } else {
      ElMessage.error(res.data || '获取失败，密码错误')
    }
  } catch (error) {
    ElMessage.error('获取失败')
  } finally {
    cookieLoading.value = false
  }
}

const handleLogout = async () => {
  try {
    await logout()
    setLoginStatus(false)
    localStorage.removeItem('douyin_token')
    ElMessage.success('已退出登录')
  } catch (error) {
    ElMessage.error('退出失败')
  }
}

const handleDieLogin = async () => {
  try {
    await dieLogin()
    setLoginStatus(false)
    localStorage.removeItem('douyin_token')
    ElMessage.success('已强制退出登录')
  } catch (error) {
    ElMessage.error('强制退出失败')
  }
}

const handleSendCode = async () => {
  if (!phoneForm.value.phone) {
    ElMessage.warning('请输入手机号')
    return
  }
  codeLoading.value = true
  try {
    const res = await sendVerifyCode(phoneForm.value.areacode, phoneForm.value.phone)
    if (res.code == 200) {
      ElMessage.success('验证码发送成功')
      codeCountdown.value = 60
      // 句柄存起来，组件卸载时统一清掉；否则离开设置页后这个定时器还在跑
      if (codeTimer) clearInterval(codeTimer)
      codeTimer = setInterval(() => {
        codeCountdown.value--
        if (codeCountdown.value <= 0) {
          clearInterval(codeTimer)
          codeTimer = null
        }
      }, 1000)
    } else {
      ElMessage.error(res.data || '验证码发送失败')
    }
  } catch (error) {
    ElMessage.error('验证码发送失败，请确保浏览器已初始化')
  } finally {
    codeLoading.value = false
  }
}

const handlePhoneLogin = async () => {
  if (!phoneForm.value.phone) {
    ElMessage.warning('请输入手机号')
    return
  }
  if (!phoneForm.value.code) {
    ElMessage.warning('请输入验证码')
    return
  }
  phoneLoading.value = true
  try {
    const res = await submitVerifyCode(phoneForm.value.code)
    if (res.code == 200) {
      ElMessage.success('登录成功')
      phoneDialogVisible.value = false
      setLoginStatus(true)
      username.value = ''
      usernameLoaded.value = false
      localStorage.removeItem('douyin_username')
      localStorage.removeItem('douyin_username_loaded')
      await fetchUsername()
    } else {
      ElMessage.error(res.data || '登录失败')
    }
  } catch (error) {
    ElMessage.error('登录失败，请重试')
  } finally {
    phoneLoading.value = false
  }
}

const handleGetScreenshot = async () => {
  screenshotLoading.value = true
  screenshotUrl.value = ''
  try {
    const res = await getScrlk()
    if (res.code == 200) {
      screenshotUrl.value = 'data:image/png;base64,' + res.data
      screenshotPreviewVisible.value = true
    } else {
      ElMessage.error(res.data || '获取截图失败')
    }
  } catch (error) {
    ElMessage.error('获取截图失败，请确保已登录')
  } finally {
    screenshotLoading.value = false
  }
}

const handleForceLogin = async () => {
  try {
    const res = await forceLogin()
    if (res.code == 200) {
      ElMessage.success(res.data || '强制登录状态成功')
    } else {
      ElMessage.error(res.data || '强制登录状态失败')
    }
  } catch (error) {
    ElMessage.error('强制登录状态失败')
  }
}

const handleLogin = async () => {
  loginLoading.value = true
  loading.value = true
  qrcodeUrl.value = ''

  try {
    await syncSettingsStatus()
    if (loginStatus.value) {
      qrDialogVisible.value = false
      ElMessage.success('当前浏览器已登录，无需扫码')
      return
    }
    qrDialogVisible.value = true
    // 先初始化浏览器
    await initBrowser()
    // 获取二维码
    const res = await getLoginPng()
    if (res.status === 'already_logged_in') {
      await syncSettingsStatus({ resetUsername: true })
      qrDialogVisible.value = false
      ElMessage.success('当前浏览器已登录，无需扫码')
    } else if (res.data) {
      qrcodeUrl.value = res.data
      ElMessage.success('请使用抖音App扫码登录')
      startQrTimers()
    } else {
      ElMessage.error('获取二维码失败')
      qrDialogVisible.value = false
    }
  } catch (error) {
    ElMessage.error('登录初始化失败，请确保浏览器已启动')
    qrDialogVisible.value = false
  } finally {
    loginLoading.value = false
    loading.value = false
  }
}

// ===== 消息通知配置 =====
const loadNotifyConfig = async () => {
  try {
    const res = await getNotifyConfig()
    if (res.code === 200 && res.data) {
      const data = res.data
      // 邮箱字段单独一层（后端刻意不把邮箱的 enabled 和总开关平铺在一起）
      const mail = data.email || {}
      notifyForm.value = {
        enabled: !!data.enabled,
        on_success: !!data.on_success,
        url: '',
        url_set: !!data.url_set,
        url_masked: data.url_masked || '',
        email_enabled: !!mail.enabled,
        email_configured: !!mail.configured,
        // 凭据类字段后端不回传（连打码都不给），只能用「是否已设置」提示；
        // 地址做掩码，够用户确认配的是哪个邮箱，又不会完整回显。
        email_host: '',
        email_port: mail.port || 465,
        email_ssl: mail.ssl !== false,
        email_username: '',
        email_password: '',
        email_from: '',
        email_to: '',
        email_host_set: !!mail.host,
        email_username_set: !!mail.username_set,
        email_password_set: !!mail.password_set,
        email_from_set: !!mail.from_set,
        email_to_set: !!mail.to_masked,
        email_from_masked: mail.from_masked || '',
        email_to_masked: mail.to_masked || ''
      }
    }
  } catch (error) {
    fallbackError(error, '读取通知配置失败')
  }
}

const saveNotify = async (payload) => {
  notifySaving.value = true
  try {
    const res = await setNotifyConfig(payload)
    if (res.code === 200) {
      ElMessage.success('通知配置已保存')
      await loadNotifyConfig()
    } else {
      ElMessage.error(res.data || '保存失败')
    }
  } catch (error) {
    fallbackError(error, '保存失败')
  } finally {
    notifySaving.value = false
  }
}

// 留空 = 不改动已保存的值，所以只把真正填了内容的字段发出去
const buildEmailPayload = () => {
  const form = notifyForm.value
  const email = { enabled: form.email_enabled }
  const text = (value) => String(value == null ? '' : value).trim()
  if (text(form.email_host)) email.host = text(form.email_host)
  if (text(form.email_username)) email.username = text(form.email_username)
  if (text(form.email_password)) email.password = text(form.email_password)
  if (text(form.email_from)) email.from = text(form.email_from)
  if (text(form.email_to)) email.to = text(form.email_to)
  if (form.email_port) email.port = Number(form.email_port)
  email.ssl = !!form.email_ssl
  return email
}

const handleSaveNotify = () => {
  // 地址留空 = 不修改已保存的地址（避免每次保存都要重新粘贴 token）
  const payload = {
    enabled: notifyForm.value.enabled,
    on_success: notifyForm.value.on_success,
    email: buildEmailPayload()
  }
  const url = (notifyForm.value.url || '').trim()
  if (url) payload.url = url
  else if (!notifyForm.value.url_set) payload.url = ''
  return saveNotify(payload)
}

const handleClearNotifyUrl = () => saveNotify({
  enabled: notifyForm.value.enabled,
  on_success: notifyForm.value.on_success,
  url: '',
  email: buildEmailPayload()
})

// 清空收件人：saveNotify 里空串 = 删除，所以这里显式传 ''
const handleClearEmailTo = () => saveNotify({
  enabled: notifyForm.value.enabled,
  on_success: notifyForm.value.on_success,
  email: { ...buildEmailPayload(), to: '' }
})

const handleTestNotify = async () => {
  notifyTesting.value = true
  try {
    const res = await testNotify({ channel: 'push' })
    if (res.code === 200) ElMessage.success(res.data || '推送成功')
    else ElMessage.error(res.data || '推送失败')
  } catch (error) {
    fallbackError(error, '推送失败')
  } finally {
    notifyTesting.value = false
  }
}

const handleTestEmail = async () => {
  const form = notifyForm.value
  if (!String(form.email_host || '').trim() && !form.email_host_set) {
    ElMessage.warning('请先填写 SMTP 服务器')
    return
  }
  if (form.email_enabled && !String(form.email_to || '').trim() && !form.email_to_set) {
    ElMessage.warning('请先填写收件邮箱')
    return
  }
  emailTesting.value = true
  try {
    // 表单里的值优先；没填的字段由后端用已保存的配置兜底，所以不必先保存
    const res = await testNotify({ channel: 'email', email: buildEmailPayload() })
    if (res.code === 200) ElMessage.success(res.data || '测试邮件已发出')
    else ElMessage.error(res.data || '测试邮件发送失败')
  } catch (error) {
    fallbackError(error, '测试邮件发送失败')
  } finally {
    emailTesting.value = false
  }
}

// 只用来去重「首次挂载 vs onActivated」这对同时触发的钩子（见文件末尾）。
// 组件重新挂载时是新的实例，变量自然重置，不会影响再次进入页面时的刷新。
let settingsSynced = false
const syncOnce = async () => {
  if (settingsSynced) return
  settingsSynced = true
  await syncSettingsStatus()
  await loadNotifyConfig()
}

onMounted(async () => {
  await syncOnce()
  localStorage.setItem('douyin_settings_loaded', '1')
})

onActivated(async () => {
  // 只有真的被 keep-alive 缓存后再次激活时才会走到这里；首次挂载已经同步过了
  await syncOnce()
})

onUnmounted(() => {
  if (codeTimer) {
    clearInterval(codeTimer)
    codeTimer = null
  }
})
</script>

<style scoped>
.settings-container {
  width: 100%;
}

.card-header {
  font-weight: 600;
  font-size: 16px;
}

.notify-desc {
  color: #606266;
  font-size: 13px;
  line-height: 1.7;
  margin-bottom: 14px;
}

.notify-form .config-item {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 14px;
}

.notify-hint {
  color: #909399;
  font-size: 12px;
}

/* 邮箱通道与推送之间加一条分隔线，避免两套凭据表单糊在一起 */
.notify-form .el-divider {
  margin: 22px 0 12px;
}

.account-section {
  padding: 10px 0;
}

.login-action {
  margin-top: 30px;
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.qrcode-container {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 300px;
}

.qrcode-wrapper {
  text-align: center;
}

.qrcode-img {
  width: 250px;
  height: 250px;
  border: 1px solid #ddd;
  border-radius: 8px;
}

.qrcode-hint {
  margin-top: 15px;
  color: #666;
  font-size: 14px;
}

.qrcode-actions {
  display: flex;
  justify-content: center;
  gap: 12px;
  margin-top: 20px;
  padding-top: 15px;
  border-top: 1px solid #eee;
}

.verify-tip {
  margin: 0 0 8px;
  font-size: 13px;
  color: #606266;
}

/* 顶部账号信息条 */
.verify-head {
  padding: 10px 12px;
  margin-bottom: 16px;
  background: #f5f7fa;
  border-radius: 8px;
}
.verify-head-main {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}
.verify-head-sub {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
  line-height: 1.5;
}

/* 步骤标题 */
.verify-step {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.verify-step-no {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #409eff;
  color: #fff;
  font-size: 12px;
  line-height: 18px;
  text-align: center;
  flex: none;
}
.verify-step-title {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}
.verify-step .el-button {
  margin-left: auto;
}

/* 验证方式卡片 */
.verify-methods {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  margin-bottom: 18px;
}
.verify-method {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  padding: 12px 6px;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  background: #fff;
  color: #606266;
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s, color 0.2s;
}
.verify-method:hover:not(:disabled) {
  border-color: #409eff;
  color: #409eff;
}
.verify-method.is-active {
  border-color: #409eff;
  background: #ecf5ff;
  color: #409eff;
  box-shadow: inset 0 0 0 1px #409eff;
}
.verify-method:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}
.verify-method-icon {
  font-size: 20px;
}
.verify-method-label {
  font-size: 13px;
  font-weight: 600;
}
.verify-method-desc {
  font-size: 11px;
  color: #909399;
}
.verify-method-check {
  position: absolute;
  top: 6px;
  right: 6px;
  font-size: 13px;
}

/* 当前方式的操作区：只显示一种 */
.verify-panel {
  min-height: 92px;
  padding: 14px;
  border: 1px dashed #dcdfe6;
  border-radius: 8px;
  background: #fafafa;
}
.verify-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.verify-row .el-input {
  flex: 1;
}
.verify-panel-foot {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  margin-top: 10px;
  font-size: 12px;
  color: #909399;
}
.verify-hint {
  margin: 10px 0 0;
  font-size: 12px;
  color: #909399;
}
.verify-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #909399;
  font-size: 13px;
}

/* 刷脸二维码：原图渲染 + 放大，避免整页截图被压缩后手机扫不出 */
.verify-qr {
  text-align: center;
}
.verify-qr img {
  display: block;
  width: 300px;
  max-width: 100%;
  margin: 0 auto;
  /* 二维码规范要求四周留白色静区，没有静区很多手机扫描器会识别失败 */
  padding: 10px;
  box-sizing: content-box;
  image-rendering: pixelated;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  background: #fff;
}
.verify-qr-hint {
  margin: 6px 0 0;
  font-size: 12px;
  color: #909399;
  line-height: 1.5;
}

/* 排查信息默认收起，避免整页截图占满弹窗 */
.verify-debug {
  margin-top: 14px;
}
.verify-shot {
  width: 100%;
  margin-top: 8px;
  border-radius: 6px;
  border: 1px solid #e4e7ed;
}

.verify-text {
  max-height: 220px;
  overflow: auto;
  margin: 0;
  padding: 8px;
  background: #f5f7fa;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}

.loading-wrapper,
.error-wrapper {
  text-align: center;
  color: #999;
}

.loading-wrapper .el-icon {
  font-size: 48px;
  margin-bottom: 10px;
}

.config-row {
  display: flex;
  align-items: center;
  gap: 20px;
  flex-wrap: wrap;
}

.config-item {
  display: flex;
  align-items: center;
  padding: 8px 16px;
  background: #f5f7fa;
  border-radius: 8px;
  border: 1px solid #e4e7ed;
}

.config-label {
  color: #909399;
  font-size: 14px;
}

.config-value {
  color: #303133;
  font-size: 14px;
  font-weight: 500;
}

.debug-row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.screenshot-wrapper {
  margin-top: 16px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  overflow: hidden;
}

.screenshot-img {
  max-width: 100%;
  max-height: 70vh;
  width: auto;
  height: auto;
  display: block;
  margin: 0 auto;
}
</style>
