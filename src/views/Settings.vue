<template>
  <div class="settings-container">
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

  <!-- 女朋友模式卡片 -->
  <el-card style="margin-top: 20px">
    <template #header>
      <div class="card-header">
        <span>女朋友模式</span>
      </div>
    </template>
    <div class="notify-desc">
      早安 / 午安 / 晚安问候：发送时按当时时段自动写一句，带上当天真实天气、农历和相识天数。
      天气用和风天气（QWeather）的个人开发者专属域名 + API Key，两项凭据只保存在本机。
    </div>
    <el-form :model="girlfriendForm" label-width="90px" class="girlfriend-form">
      <el-form-item label="启用">
        <el-switch v-model="girlfriendForm.enabled" />
        <span class="notify-hint">（关掉后仍可在任务/发送里选「女朋友模式」，届时只走兜底文案）</span>
      </el-form-item>
      <el-form-item label="API Host">
        <el-input
          v-model="girlfriendForm.host"
          placeholder="https://xxxxx.qweatherapi.com"
          clearable
          style="max-width: 420px"
        />
      </el-form-item>
      <el-form-item label="API Key">
        <el-input
          v-model="girlfriendForm.key"
          type="password"
          show-password
          clearable
          :placeholder="girlfriendForm.key_set ? '已配置（留空表示不修改）' : '和风天气控制台里的 API Key'"
          style="max-width: 420px"
        />
      </el-form-item>
      <el-form-item label="城市">
        <el-input
          v-model="girlfriendForm.city"
          placeholder="城市名或 LocationID，例如 北京 / 101010100"
          clearable
          style="max-width: 320px"
        />
      </el-form-item>
      <el-form-item label="相识日期">
        <el-date-picker
          v-model="girlfriendForm.meet_date"
          type="date"
          format="YYYY-MM-DD"
          value-format="YYYY-MM-DD"
          placeholder="选择相识的那一天"
        />
        <span v-if="girlfriendForm.meet_days !== ''" class="notify-hint">
          （已相识 {{ girlfriendForm.meet_days }} 天）
        </span>
      </el-form-item>
      <el-form-item label="她的昵称">
        <el-input
          v-model="girlfriendForm.her_name"
          placeholder="问候语里怎么称呼她，例如 宝宝"
          clearable
          style="max-width: 320px"
        />
      </el-form-item>
      <el-form-item label="我的昵称">
        <el-input
          v-model="girlfriendForm.my_name"
          placeholder="她怎么称呼你，例如 猪猪"
          clearable
          style="max-width: 320px"
        />
      </el-form-item>
    </el-form>
    <div class="notify-desc">
      Host 填「控制台 → 设置」里那个形如 <code>https://xxxxx.qweatherapi.com</code> 的专属域名
      （不是公共的 api.qweather.com）；Key 就是同一个页面上的 API Key。新版接口用请求头认证，
      所以只需要这两项，不用再填用户名或 JWT。
    </div>
    <div class="gf-actions">
      <el-button type="primary" :icon="Bell" @click="handleSaveGirlfriend" :loading="gfSaving">
        保存配置
      </el-button>
      <el-button :icon="Promotion" @click="handleTestWeather" :loading="gfWeatherLoading">
        测试天气
      </el-button>
      <el-button :icon="Sunny" @click="handlePreviewGirlfriend('morning')" :loading="gfPreviewLoading">
        预览早安
      </el-button>
      <el-button :icon="Sunny" @click="handlePreviewGirlfriend('noon')" :loading="gfPreviewLoading">
        预览午安
      </el-button>
      <el-button :icon="Moon" @click="handlePreviewGirlfriend('night')" :loading="gfPreviewLoading">
        预览晚安
      </el-button>
    </div>
    <div v-if="girlfriendForm.city_resolved" class="notify-hint" style="margin-top: 10px">
      已解析城市：{{ girlfriendForm.city_resolved_text || girlfriendForm.city }}
      <template v-if="girlfriendForm.tz">（时区 {{ girlfriendForm.tz }}）</template>
      <template v-if="girlfriendForm.lat !== '' && girlfriendForm.lon !== ''">
        （{{ girlfriendForm.lat }}, {{ girlfriendForm.lon }}）
      </template>
    </div>

    <!-- 天气 / 问候预览：接口返回的 text 是多行文本，必须用 pre-wrap 保留换行，
         ElMessage 会把换行挤成一行（而且塞对象进去会弹「绿色但没有文字」的空通知） -->
    <el-dialog v-model="gfPreviewVisible" :title="gfPreviewTitle" width="560px" destroy-on-close>
      <pre class="gf-preview-text">{{ gfPreviewText }}</pre>
    </el-dialog>
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
import { ref, onMounted, onActivated } from 'vue'
import { ElMessage } from 'element-plus'
import { Lock, Picture, WarnTriangleFilled, Bell, Promotion, Sunny, Moon } from '@element-plus/icons-vue'
import { getInitStatus, changePassword, getLastLoginIP, getScrlk, forceLogin, fallbackError, getNotifyConfig, setNotifyConfig, testNotify, getGirlfriendConfig, saveGirlfriendConfig, getGirlfriendWeather, previewGirlfriend } from '../api/douyin'
import { hasLoaded } from '../stores/browser'
import { useUserStore } from '../stores/user'

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

// ===== 女朋友模式配置 =====
// key_set / lat / lon / tz / city_resolved / meet_days 都是后端回的派生或只读字段，
// 只用来回显，不参与提交。
const girlfriendForm = ref({
  enabled: false,
  host: '',
  key: '',
  city: '',
  meet_date: '',
  her_name: '',
  my_name: '',
  lat: '',
  lon: '',
  tz: '',
  city_resolved: '',
  // 面板上「已解析城市：」要显示的是城市名（如「义乌（浙江省 金华）」），
  // 后端单独给 city_resolved_text；只拿布尔会显示成 'true'。
  city_resolved_text: '',
  meet_days: '',
  key_set: false
})
const gfSaving = ref(false)
const gfWeatherLoading = ref(false)
const gfPreviewLoading = ref(false)
const gfPreviewVisible = ref(false)
const gfPreviewTitle = ref('女朋友模式预览')
const gfPreviewText = ref('')

const passwordDialogVisible = ref(false)
const passwordLoading = ref(false)
const passwordForm = ref({
  old_password: '',
  new_password: ''
})
const lastLoginIP = ref(localStorage.getItem('douyin_last_login_ip') || '加载中...')
const settingsLoaded = ref(localStorage.getItem('douyin_settings_loaded') === '1')
const screenshotLoading = ref(false)
const screenshotUrl = ref('')
const screenshotPreviewVisible = ref(false)

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

// ===== 女朋友模式：配置读写 / 天气测试 / 问候预览 =====
// 后端出错时 res.data 是中文原因**字符串**（不是对象），成功时才是对象；
// 这里统一抹平成字符串，避免把对象丢给 ElMessage（那会弹出「绿色但没有文字」的空通知）。
const replyText = (res, fallback) => {
  const data = res && res.data
  if (typeof data === 'string' && data.trim()) return data
  if (data && typeof data === 'object') {
    if (typeof data.text === 'string' && data.text.trim()) return data.text
    if (typeof data.msg === 'string' && data.msg.trim()) return data.msg
  }
  return fallback
}

// 天气 / 问候预览共用同一个弹窗：接口返回的 text 是多行文本，用 pre-wrap 保留换行
const openGfPreview = (title, text) => {
  gfPreviewTitle.value = title
  gfPreviewText.value = text
  gfPreviewVisible.value = true
}

const loadGirlfriendConfig = async () => {
  try {
    const res = await getGirlfriendConfig()
    // 拦截器已解包：调用方读 res.code / res.data，没有 res.status
    if (res.code == 200 && res.data && typeof res.data === 'object') {
      const data = res.data
      girlfriendForm.value = {
        enabled: !!data.enabled,
        host: data.host || '',
        // Key 是凭据：后端可能不回明文，留空即「不修改已保存的那份」
        key: typeof data.key === 'string' ? data.key : '',
        city: data.city || '',
        meet_date: data.meet_date || '',
        her_name: data.her_name || '',
        my_name: data.my_name || '',
        lat: data.lat == null ? '' : data.lat,
        lon: data.lon == null ? '' : data.lon,
        tz: data.tz || '',
        city_resolved: data.city_resolved || '',
        city_resolved_text: data.city_resolved_text || '',
        meet_days: data.meet_days == null ? '' : data.meet_days,
        key_set: !!(data.key || data.key_set)
      }
    }
  } catch (error) {
    // 后端路由还没上线时这里是 404：拦截器已经弹过真实原因，页面照常渲染空表单，
    // 不要白屏，也不要再叠一条笼统的提示。
    fallbackError(error, '读取女朋友模式配置失败')
  }
}

const handleSaveGirlfriend = async () => {
  gfSaving.value = true
  try {
    const form = girlfriendForm.value
    const text = (value) => String(value == null ? '' : value).trim()
    const payload = {
      enabled: !!form.enabled,
      host: text(form.host),
      city: text(form.city),
      meet_date: text(form.meet_date),
      her_name: text(form.her_name),
      my_name: text(form.my_name)
    }
    // 凭据留空 = 不修改已保存的值（与「消息通知」里凭据字段的语义保持一致）
    if (text(form.key)) payload.key = text(form.key)
    const res = await saveGirlfriendConfig(payload)
    if (res.code == 200) {
      ElMessage.success('已保存')
      await loadGirlfriendConfig()
    } else {
      ElMessage.error(replyText(res, '保存失败'))
    }
  } catch (error) {
    fallbackError(error, '保存失败')
  } finally {
    gfSaving.value = false
  }
}

const handleTestWeather = async () => {
  gfWeatherLoading.value = true
  try {
    const res = await getGirlfriendWeather(1)
    if (res.code == 200 && res.data && typeof res.data === 'object') {
      const data = res.data
      openGfPreview('天气实况', data.text || '（接口没有返回 text 字段）')
      // 顺手把后端解析出来的城市 / 时区回显出来：填错城市时这里一眼能看出来
      if (data.city_resolved) girlfriendForm.value.city_resolved = data.city_resolved
      if (data.city_resolved_text) girlfriendForm.value.city_resolved_text = data.city_resolved_text
      if (data.tz) girlfriendForm.value.tz = data.tz
      if (data.lat != null) girlfriendForm.value.lat = data.lat
      if (data.lon != null) girlfriendForm.value.lon = data.lon
    } else {
      ElMessage.error(replyText(res, '天气获取失败'))
    }
  } catch (error) {
    fallbackError(error, '天气获取失败')
  } finally {
    gfWeatherLoading.value = false
  }
}

const gfPeriodName = { morning: '早安', noon: '午安', night: '晚安' }

const handlePreviewGirlfriend = async (period) => {
  gfPreviewLoading.value = true
  try {
    const res = await previewGirlfriend(period)
    if (res.code == 200 && res.data && typeof res.data === 'object') {
      const data = res.data
      openGfPreview(
        data.period_text || `预览${gfPeriodName[period] || ''}`,
        data.text || '（接口没有返回 text 字段）'
      )
    } else {
      ElMessage.error(replyText(res, '预览失败'))
    }
  } catch (error) {
    fallbackError(error, '预览失败')
  } finally {
    gfPreviewLoading.value = false
  }
}

// 只用来去重「首次挂载 vs onActivated」这对同时触发的钩子（见文件末尾）。
// 组件重新挂载时是新的实例，变量自然重置，不会影响再次进入页面时的刷新。
let settingsSynced = false
const syncOnce = async () => {
  if (settingsSynced) return
  settingsSynced = true
  await fetchLastLoginIP()
  await loadNotifyConfig()
  await loadGirlfriendConfig()
}

onMounted(async () => {
  await syncOnce()
  localStorage.setItem('douyin_settings_loaded', '1')
})

onActivated(async () => {
  // 只有真的被 keep-alive 缓存后再次激活时才会走到这里；首次挂载已经同步过了
  await syncOnce()
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

/* 女朋友模式卡片：字段纵向排列 + 一排操作按钮 */
.girlfriend-form .el-form-item {
  margin-bottom: 14px;
}

.gf-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

/* 天气 / 问候预览正文：保留接口返回的多行换行（ElMessage 会把换行挤成一行） */
.gf-preview-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.8;
  color: #303133;
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
