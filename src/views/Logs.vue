<template>
  <div class="logs-container">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>信息日志</span>
          <div class="header-buttons">
            <el-switch
              v-model="autoRefresh"
              active-text="自动刷新"
              inline-prompt
              style="margin-right: 12px"
            />
            <el-button :icon="Refresh" @click="loadLogs" :loading="loading">
              刷新
            </el-button>
            <el-button
              type="danger"
              :icon="Delete"
              @click="handleClear"
              :loading="clearing"
              :disabled="total === 0"
            >
              清空
            </el-button>
          </div>
        </div>
      </template>

      <!-- 筛选 -->
      <div class="filter-bar">
        <el-select v-model="filters.level" placeholder="全部级别" clearable style="width: 140px" @change="reload">
          <el-option label="成功" value="success" />
          <el-option label="信息" value="info" />
          <el-option label="警告" value="warn" />
          <el-option label="错误" value="error" />
        </el-select>

        <el-select v-model="filters.category" placeholder="全部分类" clearable style="width: 140px" @change="reload">
          <el-option v-for="item in categories" :key="item" :label="item" :value="item" />
        </el-select>

        <el-input
          v-model="filters.keyword"
          placeholder="搜索好友名 / 内容 / 错误原因"
          clearable
          style="width: 280px"
          @keyup.enter="reload"
          @clear="reload"
        >
          <template #append>
            <el-button :icon="Search" @click="reload" />
          </template>
        </el-input>

        <span class="summary">
          共 {{ total }} 条<template v-if="lastUpdated"> · 更新于 {{ lastUpdated }}</template>
        </span>
      </div>

      <!-- 日志表格 -->
      <el-table
        v-loading="loading"
        :data="logs"
        stripe
        style="width: 100%"
        :row-class-name="rowClass"
        empty-text="暂无日志：初始化浏览器、登录、发消息、定时任务等操作记录会显示在这里"
      >
        <el-table-column prop="time" label="时间" width="170" />
        <el-table-column label="级别" width="90">
          <template #default="{ row }">
            <el-tag :type="levelType(row.level)" size="small" effect="dark">
              {{ levelText(row.level) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="category" label="分类" width="100" />
        <el-table-column prop="message" label="内容" min-width="280" show-overflow-tooltip />
        <el-table-column label="详情" min-width="200">
          <template #default="{ row }">
            <span v-if="row.detail" class="detail-text" :title="row.detail">{{ row.detail }}</span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager" v-if="total > 0">
        <el-pagination
          v-model:current-page="page"
          :page-size="size"
          :total="total"
          :pager-count="7"
          layout="prev, pager, next, jumper, sizes"
          :page-sizes="[20, 50, 100, 200]"
          @current-change="loadLogs"
          @size-change="handleSizeChange"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Delete, Search } from '@element-plus/icons-vue'
import { getLogs, clearLogs, fallbackError } from '../api/douyin'

const logs = ref([])
const categories = ref([])
const total = ref(0)
const page = ref(1)
const size = ref(50)
const loading = ref(false)
const clearing = ref(false)
const autoRefresh = ref(true)
const lastUpdated = ref('')

const filters = reactive({
  level: '',
  category: '',
  keyword: ''
})

// 级别到 Element Plus tag 类型的映射
const LEVEL_STYLE = {
  success: { type: 'success', text: '成功' },
  info: { type: 'info', text: '信息' },
  warn: { type: 'warning', text: '警告' },
  error: { type: 'danger', text: '错误' }
}

const levelType = (level) => (LEVEL_STYLE[level] || LEVEL_STYLE.info).type
const levelText = (level) => (LEVEL_STYLE[level] || LEVEL_STYLE.info).text

const rowClass = ({ row }) => (row.level === 'error' ? 'row-error' : '')

let timer = null

const loadLogs = async (silent = false) => {
  if (!silent) loading.value = true
  try {
    const res = await getLogs({
      page: page.value,
      size: size.value,
      level: filters.level || undefined,
      category: filters.category || undefined,
      keyword: filters.keyword || undefined
    })
    if (res.code === 200 && res.data) {
      logs.value = res.data.list || []
      total.value = res.data.total || 0
      categories.value = res.data.categories || []
      lastUpdated.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    } else if (!silent) {
      ElMessage.error(res.data || '读取日志失败')
    }
  } catch (error) {
    fallbackError(error, '读取日志失败')
  } finally {
    loading.value = false
  }
}

// 筛选条件变化后回到第一页，否则会停在空页上
const reload = () => {
  page.value = 1
  loadLogs()
}

const handleSizeChange = () => {
  page.value = 1
  loadLogs()
}

const handleClear = async () => {
  try {
    await ElMessageBox.confirm('确定要清空全部信息日志吗？清空后无法恢复。', '提示', {
      type: 'warning'
    })
  } catch (e) {
    return
  }
  clearing.value = true
  try {
    const res = await clearLogs()
    if (res.code === 200) {
      ElMessage.success('日志已清空')
      page.value = 1
      await loadLogs()
    } else {
      ElMessage.error(res.data || '清空失败')
    }
  } catch (error) {
    fallbackError(error, '清空失败')
  } finally {
    clearing.value = false
  }
}

const startTimer = () => {
  stopTimer()
  timer = setInterval(() => {
    // 后台静默刷新，不打断正在看的筛选/翻页
    if (autoRefresh.value && !loading.value && !clearing.value) loadLogs(true)
  }, 5000)
}

const stopTimer = () => {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

watch(autoRefresh, (val) => {
  if (val) {
    startTimer()
    loadLogs(true)
  } else {
    stopTimer()
  }
})

onMounted(() => {
  loadLogs()
  startTimer()
})

onUnmounted(stopTimer)
</script>

<style scoped>
.logs-container {
  width: 100%;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}

.header-buttons {
  display: flex;
  align-items: center;
}

.filter-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}

.summary {
  color: #909399;
  font-size: 13px;
}

.detail-text {
  color: #606266;
  font-size: 13px;
  word-break: break-all;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.muted {
  color: #c0c4cc;
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

/* 错误行整行标红，方便一眼扫到失败记录 */
:deep(.row-error) {
  --el-table-tr-bg-color: #fef0f0;
}

:deep(.row-error td) {
  color: #f56c6c;
}

@media (max-width: 768px) {
  .filter-bar {
    flex-direction: column;
    align-items: stretch;
  }

  .filter-bar :deep(.el-select),
  .filter-bar :deep(.el-input) {
    width: 100% !important;
  }
}
</style>
