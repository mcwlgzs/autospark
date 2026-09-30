<template>
  <div class="history-container">
    <el-card>
      <template #header>
        <div class="card-header">
          <div class="card-title">
            <span>消息记录</span>
            <el-tag type="info" effect="plain" size="small">{{ statText }}</el-tag>
          </div>
          <div class="card-actions">
            <el-dropdown trigger="click" :disabled="clearing" @command="handleClearCommand">
              <el-button :icon="Delete" :loading="clearing">
                清空<el-icon class="el-icon--right"><ArrowDown /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="old">清空今天以前</el-dropdown-item>
                  <el-dropdown-item command="all" divided>清空全部（含今天）</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
            <el-button :icon="Refresh" circle :loading="loading" title="刷新" @click="load" />
          </div>
        </div>
      </template>

      <el-alert
        v-if="retryNote"
        class="retry-note"
        type="warning"
        :closable="false"
        show-icon
        :title="retryNote"
      />

      <el-form class="filter-bar" inline @submit.prevent>
        <el-form-item label="结果">
          <el-select
            v-model="filters.status"
            placeholder="全部"
            clearable
            style="width: 140px"
            @change="handleSearch"
          >
            <el-option label="成功" value="success" />
            <el-option label="失败" value="failed" />
            <el-option label="结果未确认" value="unknown" />
          </el-select>
        </el-form-item>
        <el-form-item label="时间">
          <el-select v-model="filters.days" style="width: 130px" @change="handleSearch">
            <el-option label="全部" :value="0" />
            <el-option label="今天" :value="1" />
            <el-option label="近 3 天" :value="3" />
            <el-option label="近 7 天" :value="7" />
          </el-select>
        </el-form-item>
        <el-form-item label="关键词">
          <el-input
            v-model="filters.keyword"
            placeholder="好友昵称 / 消息内容"
            clearable
            style="width: 220px"
            @keyup.enter="handleSearch"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="handleSearch">查询</el-button>
          <el-button :icon="RefreshLeft" @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>

      <el-table v-loading="loading" :data="rows" stripe style="width: 100%">
        <el-table-column prop="at" label="发送时间" width="170" />
        <el-table-column label="好友" width="140">
          <template #default="{ row }">
            <span :class="{ muted: !row.name }">{{ row.name || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.kind_text" type="info" effect="plain" size="small">
              {{ row.kind_text }}
            </el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="消息内容" min-width="240" :show-overflow-tooltip="cellTooltip">
          <template #default="{ row }">
            <span :class="{ muted: !row.text }">{{ row.text || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="结果" width="120">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" effect="light" size="small">
              {{ row.status_text }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="说明" min-width="200" :show-overflow-tooltip="cellTooltip">
          <template #default="{ row }">
            <span :class="{ muted: !row.detail }">{{ row.detail || '—' }}</span>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty :description="loading ? '正在读取…' : '还没有发送记录'" />
        </template>
      </el-table>

      <el-pagination
        v-if="total > 0"
        class="pager"
        :current-page="page"
        :page-size="size"
        :page-sizes="[10, 20, 50, 100]"
        :total="total"
        layout="total, sizes, prev, pager, next"
        @current-change="handlePageChange"
        @size-change="handleSizeChange"
      />

      <p class="footnote">
        记录就是后端那份「发送记账」，同时也是防重复发送的依据：一天一个好友只留一条，
        所以这里看到的条数 = 实际发出去的次数。只保留最近 {{ keepDays }} 天，
        更早的会在下次发送时自动清掉。
        <template v-if="oldest">现有记录从 {{ oldest }} 到 {{ newest }}。</template>
        清空只针对台账显示，发送状态不受影响。
      </p>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, RefreshLeft, Search, Delete, ArrowDown } from '@element-plus/icons-vue'
import { getHistoryList, clearHistory, fallbackError } from '../api/douyin'

// 表格单元格的悬浮提示配置：交给全局样式 .spark-cell-tip 收成一个固定大小的小框，
// 不然一条女朋友问候/一整段签文会把提示框撑得铺满半个屏幕（用户 2026-09-28 反馈）。
// enterable + showArrow:false 的理由写在 src/style.css 那段注释里。
const cellTooltip = { popperClass: 'spark-cell-tip', showArrow: false, enterable: true }

const loading = ref(false)
const clearing = ref(false)
const rows = ref([])
const total = ref(0)
const page = ref(1)
const size = ref(20)
const stats = ref({ success: 0, failed: 0, unknown: 0 })
const keepDays = ref(7)
const oldest = ref('')
const newest = ref('')
const retry = ref({})
const manualRetries = ref([])

const filters = reactive({ status: '', days: 0, keyword: '' })

const statText = computed(() => {
  const s = stats.value || {}
  return `共 ${total.value} 条 · 成功 ${s.success || 0} · 失败 ${s.failed || 0} · 未确认 ${s.unknown || 0}`
})

// 补发队列和「人工重发」标记本身不是发送记录，但它们解释了
// 「为什么今天还会有一次发送」，放在表格上方更符合排查顺序。
const retryNote = computed(() => {
  const r = retry.value || {}
  const parts = []
  if (r.date) {
    parts.push(
      r.done
        ? `${r.date} 的当日补发已完成`
        : `${r.date} 安排了当日补发${r.due_at ? '（计划 ' + r.due_at + '）' : ''}`
    )
  }
  if (r.names && r.names.length) parts.push('待补发：' + r.names.join('、'))
  if (manualRetries.value.length) parts.push('标记人工重发：' + manualRetries.value.join('、'))
  return parts.join('；')
})

const statusTagType = (status) => {
  if (status === 'success') return 'success'
  if (status === 'failed') return 'danger'
  return 'warning'
}

const load = async () => {
  loading.value = true
  try {
    const res = await getHistoryList({
      page: page.value,
      size: size.value,
      status: filters.status || undefined,
      keyword: filters.keyword || undefined,
      days: filters.days || 0
    })
    // 注意：src/api/douyin.js 的响应拦截器返回的是响应体 {code, data}，
    // 不是 axios 的 Response —— 所以读 res.code / res.data，没有 res.status。
    if (res.code == 200) {
      const payload = res.data || {}
      rows.value = payload.list || []
      total.value = payload.total || 0
      page.value = payload.page || 1
      size.value = payload.size || size.value
      stats.value = payload.stats || { success: 0, failed: 0, unknown: 0 }
      keepDays.value = payload.keep_days || 7
      oldest.value = payload.oldest || ''
      newest.value = payload.newest || ''
      retry.value = payload.retry || {}
      manualRetries.value = payload.manual_retries || []
    } else {
      ElMessage.error(res.data || '读取消息记录失败')
    }
  } catch (error) {
    fallbackError(error, '读取消息记录失败')
  } finally {
    loading.value = false
  }
}

const handleSearch = () => {
  page.value = 1
  load()
}

const handleReset = () => {
  filters.status = ''
  filters.days = 0
  filters.keyword = ''
  page.value = 1
  load()
}

const handlePageChange = (value) => {
  page.value = value
  load()
}

const handleSizeChange = (value) => {
  size.value = value
  page.value = 1
  load()
}

// 今天的条数单独问一次后端：它不在列表返回值里，而确认框要说清「保留几条」。
// 数不出来就退回「今天的记录会保留」这种不写数字的说法，不能因此拦着用户清空。
const countToday = async () => {
  try {
    const res = await getHistoryList({ page: 1, size: 1, days: 1 })
    if (res.code == 200) return (res.data || {}).total || 0
  } catch (error) {
    // 数不出来不影响清空本身，忽略
  }
  return null
}

const doClear = async (scope) => {
  clearing.value = true
  try {
    const res = await clearHistory(scope)
    if (res.code == 200) {
      const removed = (res.data || {}).removed || 0
      ElMessage.success(`已清空 ${removed} 条记录`)
      page.value = 1
      await load()
    } else {
      ElMessage.error(res.data || '清空消息记录失败')
    }
  } catch (error) {
    fallbackError(error, '清空消息记录失败')
  } finally {
    clearing.value = false
  }
}

const handleClearCommand = async (scope) => {
  if (scope === 'all') {
    try {
      await ElMessageBox.confirm(
        '今天的记录是防重复发送的依据，清掉后同一天可能重复发给同一个好友。确定要清空全部记录吗？',
        '清空全部（含今天）',
        { type: 'warning', confirmButtonText: '清空全部', cancelButtonText: '取消' }
      )
    } catch (error) {
      return
    }
    await doClear('all')
    return
  }

  const todayCount = await countToday()
  const keepText = todayCount === null ? '今天的记录会保留' : `今天的 ${todayCount} 条会保留`
  try {
    await ElMessageBox.confirm(
      `只清除今天以前的记录；${keepText} —— 它们是防重复发送的依据。`,
      '清空今天以前的记录',
      { type: 'warning', confirmButtonText: '清空', cancelButtonText: '取消' }
    )
  } catch (error) {
    return
  }
  await doClear('old')
}

onMounted(load)
</script>

<style scoped>
.history-container {
  padding: 0;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.card-title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 600;
}

.card-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.retry-note {
  margin-bottom: 14px;
}

.filter-bar {
  margin-bottom: 6px;
}

.filter-bar :deep(.el-form-item) {
  margin-bottom: 12px;
}

.pager {
  margin-top: 16px;
  justify-content: flex-end;
}

.footnote {
  margin: 14px 0 0;
  color: #909399;
  font-size: 12px;
  line-height: 1.7;
}

.muted {
  color: #c0c4cc;
}

@media (max-width: 768px) {
  .card-header {
    flex-wrap: wrap;
    gap: 8px;
  }

  .filter-bar :deep(.el-form-item) {
    display: block;
    margin-right: 0;
  }

  .filter-bar :deep(.el-input),
  .filter-bar :deep(.el-select) {
    width: 100% !important;
  }

  .pager {
    justify-content: center;
  }
}
</style>
