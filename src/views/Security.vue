<template>
  <div class="security-container">
    <el-card v-loading="loading">
      <template #header>
        <div class="card-header">
          <span>安全中心</span>
          <el-button :icon="Refresh" @click="load" :loading="loading">
            重新检查
          </el-button>
        </div>
      </template>

      <template v-if="data">
        <!-- 安全评分 -->
        <div class="score-bar">
          <el-progress
            type="circle"
            :percentage="data.score"
            :width="112"
            :stroke-width="8"
            :color="scoreColor"
          >
            <div class="score-value">{{ data.score }}</div>
            <div class="score-label">{{ levelLabel }}</div>
          </el-progress>

          <div class="score-side">
            <div class="score-title">
              安全评分 {{ data.score }} / 100 · {{ totalChecks }} 项检查
            </div>
            <div class="score-sub">检查时间：{{ data.generated_at }}</div>
            <div class="score-tags">
              <el-tag type="success" effect="plain">正常 {{ data.counts.ok }}</el-tag>
              <el-tag type="info" effect="plain">提示 {{ data.counts.info }}</el-tag>
              <el-tag type="warning" effect="plain">注意 {{ data.counts.warn }}</el-tag>
              <el-tag type="danger" effect="plain">风险 {{ data.counts.risk }}</el-tag>
            </div>
          </div>
        </div>

        <!-- 需要处理的项 -->
        <el-alert
          v-if="attentionChecks.length"
          type="warning"
          :closable="false"
          show-icon
          class="attention-alert"
        >
          <template #title>有 {{ attentionChecks.length }} 项需要你处理</template>
          <div v-for="item in attentionChecks" :key="item.id" class="attention-item">
            <strong>{{ item.title }}</strong>：{{ item.detail }}
          </div>
        </el-alert>
        <el-alert
          v-else
          title="所有检查项都正常"
          type="success"
          :closable="false"
          show-icon
          class="attention-alert"
        />

        <!-- 分组检查项 -->
        <div v-for="group in visibleGroups" :key="group.name" class="check-group">
          <div class="group-title">{{ group.name }}</div>

          <div
            v-for="item in group.items"
            :key="item.id"
            class="check-row"
            :class="`is-${item.status}`"
          >
            <el-icon class="check-icon" :class="`is-${item.status}`">
              <component :is="STATUS_META[item.status].icon" />
            </el-icon>
            <div class="check-body">
              <div class="check-head">
                <span class="check-title">{{ item.title }}</span>
                <el-tag
                  :type="STATUS_META[item.status].type"
                  size="small"
                  effect="plain"
                >
                  {{ STATUS_META[item.status].label }}
                </el-tag>
              </div>
              <div class="check-detail">{{ item.detail }}</div>
              <div v-if="item.hint" class="check-hint">{{ item.hint }}</div>
            </div>
          </div>
        </div>

        <!-- 运行概况 -->
        <div class="group-title">运行概况</div>
        <el-descriptions :column="2" border size="small" class="facts">
          <el-descriptions-item label="后端监听">
            {{ data.facts.bind_host }}（{{ data.facts.scheme }}）
          </el-descriptions-item>
          <el-descriptions-item label="本次来源 IP">
            {{ data.facts.client_ip }}
          </el-descriptions-item>
          <el-descriptions-item label="面板有效会话">
            {{ data.facts.sessions }} 个
          </el-descriptions-item>
          <el-descriptions-item label="定时任务">
            {{ data.facts.tasks }} 个
          </el-descriptions-item>
          <el-descriptions-item label="浏览器">
            {{ yesNo(data.facts.browser_ready) }}
          </el-descriptions-item>
          <el-descriptions-item label="浏览器版本">
            {{ data.facts.browser_version || '未启动' }}
          </el-descriptions-item>
          <el-descriptions-item label="自动化引擎">
            Selenium {{ data.facts.selenium || '未知' }}
          </el-descriptions-item>
          <el-descriptions-item label="Playwright">
            {{ data.facts.playwright || '未安装' }}
          </el-descriptions-item>
          <el-descriptions-item label="抖音登录态">
            {{ yesNo(data.facts.logged_in) }}
          </el-descriptions-item>
          <el-descriptions-item label="反检测注入">
            {{ yesNo(data.facts.stealth_injected) }}
          </el-descriptions-item>
          <el-descriptions-item label="视口 / 缩放">
            {{ data.facts.viewport || '未读取' }}<span v-if="data.facts.dpr"> @ {{ data.facts.dpr }}x</span>
          </el-descriptions-item>
          <el-descriptions-item label="时区">
            {{ data.facts.timezone || '未读取' }}
          </el-descriptions-item>
          <el-descriptions-item label="WebGL 显卡" :span="2">
            {{ data.facts.webgl || '未读取' }}
          </el-descriptions-item>
          <el-descriptions-item label="今日发送">
            {{ data.facts.sent_today }} 条
          </el-descriptions-item>
        </el-descriptions>

        <!-- 危险操作 -->
        <div class="group-title">危险操作</div>
        <div class="danger-row">
          <div class="danger-text">
            <div class="danger-title">吊销全部面板会话</div>
            <div class="danger-desc">
              让所有已登录的面板会话立刻失效（包括你现在用的这一个，执行后会掉线、要求重新登录）。
              怀疑密码泄漏、或在别人电脑上登录过面板时用它。为防误触，需要再输一次面板密码。
            </div>
          </div>
          <el-button
            type="danger"
            :icon="Lock"
            :loading="revoking"
            @click="handleRevoke"
          >
            吊销全部会话
          </el-button>
        </div>
      </template>

      <el-empty v-else-if="!loading" description="还没读到安全状态，点右上角「重新检查」试试" />
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Refresh,
  Lock,
  CircleCheckFilled,
  WarningFilled,
  CircleCloseFilled,
  InfoFilled
} from '@element-plus/icons-vue'
import { getSecurityOverview, revokeAllSessions, fallbackError } from '../api/douyin'
import { useUserStore } from '../stores/user'

const router = useRouter()
const userStore = useUserStore()

const loading = ref(false)
const revoking = ref(false)
const data = ref(null)

// 后端返回的 status 只有这四种，图标/颜色/文案都在这里映射，模板里不再写 if
const STATUS_META = {
  ok: { icon: CircleCheckFilled, type: 'success', label: '正常' },
  info: { icon: InfoFilled, type: 'info', label: '提示' },
  warn: { icon: WarningFilled, type: 'warning', label: '注意' },
  risk: { icon: CircleCloseFilled, type: 'danger', label: '风险' }
}

// 固定展示顺序（后端可能新增分组，没列到的排到最后）
const GROUP_ORDER = ['面板', '账号', '防封号', '数据', '运行时']

const visibleGroups = computed(() => {
  if (!data.value) return []
  const groups = new Map()
  data.value.checks.forEach((item) => {
    if (!groups.has(item.group)) groups.set(item.group, [])
    groups.get(item.group).push(item)
  })
  const rank = (name) => {
    const index = GROUP_ORDER.indexOf(name)
    return index === -1 ? GROUP_ORDER.length : index
  }
  return [...groups.keys()]
    .sort((a, b) => rank(a) - rank(b))
    .map((name) => ({ name, items: groups.get(name) }))
})

const totalChecks = computed(() => (data.value ? data.value.checks.length : 0))

const attentionChecks = computed(() =>
  data.value
    ? data.value.checks.filter((item) => item.status === 'risk' || item.status === 'warn')
    : []
)

const scoreColor = computed(() => {
  const score = data.value ? data.value.score : 0
  if (score >= 85) return '#67c23a'
  if (score >= 60) return '#e6a23c'
  return '#f56c6c'
})

const levelLabel = computed(() => {
  const level = data.value ? data.value.level : ''
  if (level === 'good') return '良好'
  if (level === 'warn') return '需注意'
  return '有风险'
})

const yesNo = (value) => (value ? '是' : '否')

const load = async () => {
  loading.value = true
  try {
    const res = await getSecurityOverview()
    // 注意：douyin.js 的响应拦截器返回的是**响应体**（{code, data}），不是 axios 的 Response，
    // 所以这里读 res.code / res.data，没有 res.status、也没有 res.data.code。
    if (res.code == 200) {
      data.value = res.data
    } else {
      ElMessage.error(res.data || '读取安全状态失败')
    }
  } catch (error) {
    fallbackError(error, '读取安全状态失败')
  } finally {
    loading.value = false
  }
}

const handleRevoke = async () => {
  let password = ''
  try {
    // 用 element-plus 自带的密码输入框，不自己造弹窗
    const result = await ElMessageBox.prompt(
      '这会吊销全部面板会话（包括当前这个），执行后需要重新登录。请再输入一次面板密码：',
      '吊销全部会话',
      {
        confirmButtonText: '确认吊销',
        cancelButtonText: '取消',
        inputType: 'password',
        inputPlaceholder: '面板密码',
        inputValidator: (value) => (value ? true : '请输入面板密码'),
        type: 'warning'
      }
    )
    password = result.value
  } catch (error) {
    // 用户取消：ElMessageBox 会 reject，这不是错误
    return
  }

  revoking.value = true
  try {
    const res = await revokeAllSessions(password)
    if (res.code == 200) {
      const revoked = (res.data && res.data.revoked) || 0
      ElMessage.success(`已吊销 ${revoked} 个会话，请重新登录`)
      // 后端已经把所有 token 作废了，这里同步清掉本地登录态再回登录页，
      // 否则下一次请求会先撞 401，弹一句多余的「登录已过期」
      userStore.logout()
      router.push('/login')
    } else {
      ElMessage.error(res.data || '吊销失败')
    }
  } catch (error) {
    fallbackError(error, '吊销失败')
  } finally {
    revoking.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.score-bar {
  display: flex;
  align-items: center;
  gap: 28px;
  flex-wrap: wrap;
}

.score-value {
  font-size: 26px;
  font-weight: 600;
  line-height: 1.1;
  color: #303133;
}

.score-label {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}

.score-side {
  flex: 1;
  min-width: 220px;
}

.score-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}

.score-sub {
  font-size: 13px;
  color: #909399;
  margin: 6px 0 10px;
}

.score-tags {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.attention-alert {
  margin-top: 18px;
}

.attention-item {
  line-height: 1.7;
  font-size: 13px;
}

.check-group {
  margin-top: 22px;
}

.group-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  padding-left: 9px;
  border-left: 3px solid #409eff;
  margin: 22px 0 10px;
}

.check-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 6px;
  background: #fafafa;
  margin-bottom: 8px;
}

.check-row.is-warn {
  background: #fdf6ec;
}

.check-row.is-risk {
  background: #fef0f0;
}

.check-icon {
  font-size: 17px;
  margin-top: 2px;
  flex-shrink: 0;
}

.check-icon.is-ok {
  color: #67c23a;
}

.check-icon.is-info {
  color: #909399;
}

.check-icon.is-warn {
  color: #e6a23c;
}

.check-icon.is-risk {
  color: #f56c6c;
}

.check-body {
  flex: 1;
  min-width: 0;
}

.check-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.check-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.check-detail {
  font-size: 13px;
  color: #606266;
  line-height: 1.6;
  margin-top: 3px;
  word-break: break-all;
}

.check-hint {
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
  margin-top: 3px;
  word-break: break-all;
}

.facts {
  margin-top: 4px;
}

.danger-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  padding: 12px 14px;
  border: 1px solid #fde2e2;
  border-radius: 6px;
  background: #fef0f0;
}

.danger-text {
  flex: 1;
  min-width: 240px;
}

.danger-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.danger-desc {
  font-size: 12px;
  color: #909399;
  line-height: 1.7;
  margin-top: 4px;
}

@media (max-width: 768px) {
  .score-bar {
    gap: 16px;
    justify-content: center;
  }

  .score-side {
    text-align: center;
  }

  .score-tags {
    justify-content: center;
  }

  .danger-row {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
