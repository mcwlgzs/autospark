<template>
  <div class="layout-container">
    <!-- 移动端遮罩层 -->
    <div
      v-if="isMobile && sidebarVisible"
      class="sidebar-overlay"
      @click="closeSidebar"
    ></div>

    <!-- 侧边栏 -->
    <aside
      :width="isCollapsed ? '64px' : '220px'"
      class="sidebar"
      :class="{
        'sidebar-collapsed': isCollapsed && !isMobile,
        'sidebar-mobile': isMobile,
        'sidebar-mobile-visible': sidebarVisible
      }"
    >
      <div class="logo">
        <el-icon v-if="isCollapsed && !isMobile"><ChromeFilled /></el-icon>
        <span v-else-if="!isCollapsed || (isMobile && sidebarVisible)" class="logo-text">抖音火花助手</span>
        <el-tooltip :content="browserStatus ? '浏览器已初始化' : '浏览器未初始化'" placement="right">
          <span class="status-dot" :class="browserStatus ? 'success' : 'error'"></span>
        </el-tooltip>
      </div>

      <el-menu
        :default-active="activeMenu"
        :collapse="isCollapsed && !isMobile"
        :collapse-transition="false"
        class="sidebar-menu"
        @select="handleMenuSelect"
      >
        <el-menu-item
          v-for="item in menuList"
          :key="item.path"
          :index="item.path"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <template #title>
            <span>{{ item.title }}</span>
          </template>
        </el-menu-item>
      </el-menu>

      <!-- 底部链接 -->
      <div class="sidebar-footer">
        <a
          href="https://github.com/mcwlgzs/autospark"
          target="_blank"
          class="github-link"
          :title="isCollapsed && !isMobile ? 'GitHub 项目' : ''"
        >
          <svg class="github-icon" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
            <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z"/>
          </svg>
          <span v-if="!isCollapsed || isMobile" class="github-text">GitHub 项目</span>
        </a>
      </div>
    </aside>

    <!-- 主体区域 -->
    <div class="main-wrapper">
      <!-- 顶部导航 -->
      <header class="header">
        <div class="header-left">
          <el-icon class="collapse-btn" @click="toggleSidebar">
            <Fold v-if="!isCollapsed || isMobile" />
            <Expand v-else />
          </el-icon>
          <el-breadcrumb separator="/">
            <el-breadcrumb-item :to="{ path: '/home' }">首页</el-breadcrumb-item>
            <el-breadcrumb-item v-if="activeMenu !== '/home'">
              {{ currentMenuTitle }}
            </el-breadcrumb-item>
          </el-breadcrumb>
        </div>

        <div class="header-right">
          <el-dropdown @command="handleCommand">
            <span class="user-info">
              <el-avatar :size="32" :icon="UserFilled" />
              <span class="username">{{ userStore.userInfo.username || 'Admin' }}</span>
              <el-icon><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="logout">
                  <el-icon><SwitchButton /></el-icon>
                  退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <!-- 内容区域 -->
      <main class="main-content">
        <!-- 仍在使用内置默认密码时必须显眼提示：后端此时会拦住发消息、建任务、
             改通知配置等敏感动作（防止面板被他人接管），不说明的话用户只会看到一堆 403 -->
        <el-alert
          v-if="defaultPassword"
          class="default-password-alert"
          type="warning"
          :closable="false"
          show-icon
          title="当前仍在使用内置默认密码（123456）"
        >
          <template #default>
            为保证面板不被他人接管，发消息、创建定时任务、绑定抖音账号、修改通知配置等操作已被暂停。
            <el-button link type="primary" @click="goChangePassword">立即修改密码</el-button>
          </template>
        </el-alert>
        <router-view />
      </main>
    </div>

    <!-- 悬浮的浏览器截图：定时任务跑起来以后浏览器是一个独立窗口，光看面板
         不知道它在干什么（是不是卡在验证码、是不是已经登录失效）。PC 上常驻
         右下角一个圆按钮：点一下截一张当时的画面，再点一下收起（按钮就是开关，
         不用去够浮窗里的关闭）。不做实时刷新（一秒一帧既费资源，人也不盯着一只
         看），手机上不显示。 -->
    <template v-if="!isMobile">
      <el-tooltip :content="screenVisible ? '收起浏览器截图' : '截一张浏览器画面'" placement="left">
        <button
          class="screen-fab"
          :class="{ 'screen-fab-busy': screenLoading, 'screen-fab-active': screenVisible }"
          @click="toggleScreen"
        >
          <el-icon><Close v-if="screenVisible" /><Monitor v-else /></el-icon>
        </button>
      </el-tooltip>

      <section
        v-show="screenVisible"
        class="screen-float"
        :style="{
          left: screenPos.x + 'px',
          top: screenPos.y + 'px',
          width: screenSize.w + 'px',
          height: screenSize.h + 'px'
        }"
      >
        <header class="screen-head" @mousedown="startScreenDrag">
          <span class="screen-title">浏览器截图</span>
          <span class="screen-badge" :class="screenStatusClass">{{ screenStatusText }}</span>
          <span class="screen-spacer"></span>
          <el-button link size="small" :loading="screenLoading" @click="refreshScreen">重新截图</el-button>
          <el-button link size="small" @click="cycleScreenSize">缩放</el-button>
          <el-button link size="small" @click="closeScreen">关闭</el-button>
        </header>
        <div class="screen-body">
          <img
            v-if="screenSrc"
            :src="screenSrc"
            :class="{ 'screen-stale': screenStale }"
            alt="浏览器画面"
            draggable="false"
          />
          <div v-else class="screen-empty">{{ screenError || '正在获取画面…' }}</div>
        </div>
        <footer class="screen-foot" :title="screenFootText">{{ screenFootText }}</footer>
      </section>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessageBox, ElTooltip } from 'element-plus'
import { useUserStore } from '../stores/user'
import { logout, getHome, getBrowserScreen } from '../api/douyin'
import { browserStatus } from '../stores/browser'
import {
  Fold,
  Expand,
  UserFilled,
  ArrowDown,
  SwitchButton,
  House,
  User,
  Clock,
  ChromeFilled,
  Setting,
  Document,
  Lock,
  ChatDotRound,
  Postcard,
  Monitor,
  Close
} from '@element-plus/icons-vue'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const isCollapsed = ref(false)
const isMobile = ref(false)
const sidebarVisible = ref(false)
// 是否还在用内置默认密码。后端 /Home 会回这个字段（也用于登录页提示），
// 这里显式提示一下，否则用户只会看到「发消息」「建任务」被拒的 403。
const defaultPassword = ref(false)

const goChangePassword = () => {
  router.push('/settings')
}

const loadSetupState = async () => {
  try {
    const res = await getHome()
    defaultPassword.value = !!res.must_change_password
  } catch (error) {
    // 提示性质的信息，取不到就不提示，不打扰用户
  }
}

const checkMobile = () => {
  isMobile.value = window.innerWidth < 768
  if (isMobile.value) {
    isCollapsed.value = true
    sidebarVisible.value = false
  } else {
    sidebarVisible.value = true
  }
}

const toggleSidebar = () => {
  if (isMobile.value) {
    sidebarVisible.value = !sidebarVisible.value
  } else {
    isCollapsed.value = !isCollapsed.value
  }
}

const closeSidebar = () => {
  if (isMobile.value) {
    sidebarVisible.value = false
  }
}

onMounted(() => {
  checkMobile()
  window.addEventListener('resize', checkMobile)
  loadSetupState()
})

onUnmounted(() => {
  window.removeEventListener('resize', checkMobile)
})

watch(isCollapsed, (val) => {
  if (!isMobile.value && window.innerWidth < 768) {
    isCollapsed.value = true
  }
})

// 顺序 = 侧边栏顺序。设置放最后：日常用的是前几项，配置类的东西挪到最下面不挡路。
const menuList = [
  { path: '/home', title: '首页', icon: House },
  { path: '/accounts', title: '抖音账号', icon: Postcard },
  { path: '/friends', title: '好友列表', icon: User },
  { path: '/tasks', title: '定时任务', icon: Clock },
  { path: '/logs', title: '信息日志', icon: Document },
  { path: '/history', title: '消息记录', icon: ChatDotRound },
  { path: '/security', title: '安全中心', icon: Lock },
  { path: '/settings', title: '设置', icon: Setting }
]

const activeMenu = computed(() => route.path)

const currentMenuTitle = computed(() => {
  const menu = menuList.find(item => item.path === activeMenu.value)
  return menu ? menu.title : ''
})

const handleMenuSelect = (path) => {
  router.push(path)
  if (isMobile.value) {
    sidebarVisible.value = false
  }
}

const handleCommand = (command) => {
  if (command === 'logout') {
    ElMessageBox.confirm('确定要退出登录吗？', '提示', {
      type: 'warning'
    }).then(async () => {
      try {
        await logout()
      } catch (e) {}
      userStore.logout()
      router.push('/login')
    }).catch(() => {})
  }
}

// ==================== 悬浮的浏览器截图 ====================
// 定时任务/手动发送时浏览器是一个独立的窗口，面板上看不到它在做什么：卡在验证码、
// 登录失效、还是根本没动，都只能靠猜。这里做一个常驻右下角的小浮窗：点开时截一张，
// 需要时点「重新截图」再截一张（不做实时轮询 —— 一秒一帧对后端和浏览器都是白耗）。
// 截图走 /Api/Browser/Screen（CDP JPEG，实测一帧 5KB 上下），比 /Api/GetScrlk
// 的整窗 PNG 小三个数量级。浏览器正忙时后端会回上一帧并带 stale，画面压暗一点
// 并在脚注写明「上一帧」，比整块空白强。
const screenVisible = ref(false)
const screenSrc = ref('')
const screenLoading = ref(false)
const screenError = ref('')
const screenUrl = ref('')
const screenStale = ref(false)
const screenLoggedIn = ref(false)
const screenTakenAt = ref('')
const screenSize = ref({ w: 460, h: 380 })
// 初始位置：贴着右下角，但底部要给那颗圆按钮留一条出来 —— 浮窗压住按钮的话
// 就没法「再点一次收起」了（按钮 z-index 也高一层做兜底）
const screenPos = ref({
  x: Math.max(12, window.innerWidth - 460 - 24),
  y: Math.max(12, window.innerHeight - 380 - 86)
})
let screenDrag = null

const SCREEN_SIZES = [
  { w: 460, h: 380 },
  { w: 760, h: 600 },
  { w: 1180, h: 820 }
]

const screenStatusText = computed(() => {
  if (screenError.value) return '不可用'
  if (!screenSrc.value) return '连接中'
  return screenLoggedIn.value ? '已登录' : '未登录'
})

const screenStatusClass = computed(() => {
  if (screenError.value) return 'screen-badge-bad'
  return screenLoggedIn.value ? 'screen-badge-ok' : 'screen-badge-warn'
})

const screenFootText = computed(() => {
  const parts = []
  if (screenStale.value) parts.push('上一帧（浏览器正忙）')
  if (screenTakenAt.value) parts.push(screenTakenAt.value)
  parts.push(screenUrl.value || '—')
  return parts.join(' · ')
})

const fetchScreen = async () => {
  if (screenLoading.value) return
  screenLoading.value = true
  try {
    const res = await getBrowserScreen()
    const detail = res?.data
    if (detail && typeof detail === 'object' && detail.image) {
      screenSrc.value = `data:${detail.mime || 'image/jpeg'};base64,${detail.image}`
      screenUrl.value = detail.url || ''
      screenLoggedIn.value = !!detail.logged_in
      screenStale.value = !!res?.stale
      screenTakenAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
      screenError.value = ''
    } else {
      // 后端在这里回的都是字符串（浏览器未初始化 / 正忙 409 / 截图失败）
      screenError.value = (typeof detail === 'string' && detail) || '取不到浏览器画面'
    }
  } catch (error) {
    screenError.value = error?.message || '取不到浏览器画面'
  } finally {
    screenLoading.value = false
  }
}

const openScreen = () => {
  screenVisible.value = true
  fetchScreen()
}

const closeScreen = () => {
  screenVisible.value = false
}

// 圆按钮是个开关：点一下截一张并打开，再点一下收起（不用去够浮窗里那个「关闭」）
const toggleScreen = () => {
  if (screenVisible.value) {
    closeScreen()
  } else {
    openScreen()
  }
}

const refreshScreen = () => {
  fetchScreen()
}

// 三档尺寸循环：面板上一般是「瞄一眼」，需要看清验证码时再放大
const cycleScreenSize = () => {
  const index = SCREEN_SIZES.findIndex((item) => item.w === screenSize.value.w)
  const next = SCREEN_SIZES[(index + 1) % SCREEN_SIZES.length]
  screenSize.value = { ...next }
  // 放大后可能超出右下角，拽回可视范围
  screenPos.value = {
    x: Math.min(screenPos.value.x, Math.max(12, window.innerWidth - next.w - 12)),
    y: Math.min(screenPos.value.y, Math.max(12, window.innerHeight - next.h - 12))
  }
}

const onScreenDrag = (event) => {
  if (!screenDrag) return
  const x = event.clientX - screenDrag.dx
  const y = event.clientY - screenDrag.dy
  screenPos.value = {
    // 允许往左/上拖出去一点（但标题栏必须留在屏幕里，否则拽不回来）
    x: Math.min(Math.max(x, 160 - screenSize.value.w), window.innerWidth - 160),
    y: Math.min(Math.max(y, 0), window.innerHeight - 48)
  }
}

const stopScreenDrag = () => {
  screenDrag = null
  window.removeEventListener('mousemove', onScreenDrag)
  window.removeEventListener('mouseup', stopScreenDrag)
}

const startScreenDrag = (event) => {
  // 标题栏上的按钮不该触发拖动
  if (event.target.closest && event.target.closest('button')) return
  screenDrag = {
    dx: event.clientX - screenPos.value.x,
    dy: event.clientY - screenPos.value.y
  }
  window.addEventListener('mousemove', onScreenDrag)
  window.addEventListener('mouseup', stopScreenDrag)
}
</script>

<style scoped>
.layout-container {
  width: 100%;
  height: 100vh;
  display: flex;
}

.sidebar-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 998;
}

.sidebar {
  background: #304156;
  transition: width 0.3s, transform 0.3s;
  overflow: hidden;
  flex-shrink: 0;
  position: relative;
  display: flex;
  flex-direction: column;
}

.sidebar-collapsed {
  width: 64px !important;
}

.sidebar-mobile {
  position: fixed;
  left: 0;
  top: 0;
  height: 100vh;
  z-index: 999;
  transform: translateX(-100%);
  width: 220px !important;
}

.sidebar-mobile-visible {
  transform: translateX(0);
}

.logo {
  height: 60px;
  line-height: 60px;
  text-align: center;
  color: #fff;
  font-size: 18px;
  font-weight: bold;
  background: #263445;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  position: relative;
}

.logo-text {
  flex-shrink: 0;
}

.status-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
  transition: all 0.3s;
}

.status-dot.success {
  background: #67c23a;
  box-shadow: 0 0 6px rgba(103, 194, 58, 0.6);
}

.status-dot.error {
  background: #f56c6c;
  box-shadow: 0 0 6px rgba(245, 108, 108, 0.6);
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0% { opacity: 1; }
  50% { opacity: 0.5; }
  100% { opacity: 1; }
}

.sidebar-menu {
  border-right: none;
  background: #304156;
  flex: 1;
}

.sidebar-menu:not(.el-menu--collapse) {
  width: 220px;
}

.sidebar-menu :deep(.el-menu-item) {
  color: #bfcbd9;
}

.sidebar-menu :deep(.el-menu-item:hover),
.sidebar-menu :deep(.el-menu-item.is-active) {
  background: #263445 !important;
  color: #409eff;
}

.sidebar-footer {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  padding: 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
}

.github-link {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 10px;
  color: #bfcbd9;
  text-decoration: none;
  border-radius: 6px;
  transition: all 0.3s;
  font-size: 13px;
}

.github-link:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
}

.github-icon {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  fill: currentColor;
}

.github-text {
  white-space: nowrap;
}

.main-wrapper {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
}

.header {
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);
  height: 60px;
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.collapse-btn {
  font-size: 20px;
  cursor: pointer;
  color: #666;
}

.collapse-btn:hover {
  color: #409eff;
}

.header-right {
  display: flex;
  align-items: center;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 8px;
  border-radius: 4px;
}

.user-info:hover {
  background: #f5f7fa;
}

.username {
  font-size: 14px;
  color: #333;
}

.main-content {
  background: #f0f2f5;
  padding: 20px;
  overflow-y: auto;
  flex: 1;
}

/* 默认密码提示条：贴住内容区顶部，与下方的页面卡片留出间距 */
.default-password-alert {
  margin-bottom: 16px;
}

/* ==================== 悬浮的浏览器画面 ==================== */
.screen-fab {
  position: fixed;
  right: 24px;
  bottom: 24px;
  /* 比浮窗（2100）高一层：浮窗万一被拖到按钮上，按钮仍然点得到（点一下就能收起） */
  z-index: 2200;
  width: 48px;
  height: 48px;
  border: none;
  border-radius: 50%;
  background: #409eff;
  color: #fff;
  font-size: 22px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 6px 18px rgba(64, 158, 255, 0.35);
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}

.screen-fab:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 22px rgba(64, 158, 255, 0.45);
}

/* 浮窗已经打开：按钮转成灰底「收起」态，和图标一起表示再点一下就关 */
.screen-fab-active {
  background: #909399;
  box-shadow: 0 6px 18px rgba(144, 147, 153, 0.35);
}

.screen-fab-active:hover {
  box-shadow: 0 8px 22px rgba(144, 147, 153, 0.45);
}

/* 取画面中：按钮轻微呼吸，提示「正在连」 */
.screen-fab-busy {
  animation: screen-pulse 1.4s ease-in-out infinite;
}

@keyframes screen-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.65; }
}

.screen-float {
  position: fixed;
  z-index: 2100;
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 10px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.22);
  overflow: hidden;
}

.screen-head {
  flex: 0 0 34px;
  height: 34px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 6px 0 12px;
  background: #f5f7fa;
  border-bottom: 1px solid #e4e7ed;
  cursor: move;
  user-select: none;
}

.screen-title {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}

.screen-spacer {
  flex: 1;
}

.screen-badge {
  font-size: 11px;
  line-height: 16px;
  padding: 1px 6px;
  border-radius: 8px;
}

.screen-badge-ok {
  color: #529b2e;
  background: #e1f3d8;
}

.screen-badge-warn {
  color: #b88230;
  background: #faecd8;
}

.screen-badge-bad {
  color: #c45656;
  background: #fde2e2;
}

.screen-body {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #1f1f1f;
}

.screen-body img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  transition: opacity 0.2s ease;
}

/* 浏览器正忙时后端回的是上一帧（stale），压暗一点提醒「这不是最新的」 */
.screen-stale {
  opacity: 0.55;
}

.screen-empty {
  padding: 16px;
  font-size: 13px;
  line-height: 1.6;
  color: #c0c4cc;
  text-align: center;
}

.screen-foot {
  flex: 0 0 24px;
  padding: 0 10px;
  font-size: 11px;
  line-height: 24px;
  color: #909399;
  background: #fafafa;
  border-top: 1px solid #ebeef5;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 响应式适配 */
@media (max-width: 768px) {
  .username {
    display: none;
  }

  .main-content {
    padding: 12px;
  }
}
</style>