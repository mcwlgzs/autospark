<template>
  <div class="account-container">
    <el-card>
      <template #header>
        <div class="card-header">
          <div class="card-title">
            <span>抖音账号</span>
            <el-tag :type="cookieTagType" effect="plain" size="small">{{ cookieText }}</el-tag>
          </div>
          <el-button :icon="Refresh" circle :loading="loading" title="刷新" @click="load" />
        </div>
      </template>

      <el-alert
        v-if="info && !info.logged_in"
        class="tip"
        type="warning"
        :closable="false"
        show-icon
        title="抖音未登录：这里的状态来自真实浏览器，登录后才会出现 Cookie 与到期时间。"
        description="到「设置」页扫码登录，或点「初始化浏览器」唤起登录窗口。"
      />

      <el-table v-loading="loading" :data="rows" stripe style="width: 100%">
        <el-table-column label="账号" min-width="180">
          <template #default="{ row }">
            <div class="account-name">
              <span :class="{ muted: !row.nickname }">{{ row.nickname || '当前登录的抖音账号' }}</span>
              <el-tag :type="row.logged_in ? 'success' : 'info'" effect="light" size="small">
                {{ row.logged_in ? '已登录' : '未登录' }}
              </el-tag>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="Cookie 状态" width="150">
          <template #default="{ row }">
            <el-tag :type="tagTypeOf(row.cookie_status)" effect="light" size="small">
              {{ row.cookie_text }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="好友数" width="110">
          <template #default="{ row }">
            <template v-if="row.friend_count_known">
              {{ row.friend_count }} 位
            </template>
            <el-tooltip v-else content="好友数只读内存里已抓到的列表，不会为刷新一个数字去开一次好友列表。到「好友列表」页刷新一次即可。" placement="top">
              <span class="muted">未获取</span>
            </el-tooltip>
          </template>
        </el-table-column>

        <el-table-column label="今日已发" width="110">
          <template #default="{ row }">
            <span :class="{ muted: !row.sent_today }">{{ row.sent_today }} 条</span>
          </template>
        </el-table-column>

        <el-table-column label="Cookie 到期" width="190">
          <template #default="{ row }">
            <div v-if="row.cookie_expire">
              <div>{{ row.cookie_expire }}</div>
              <div class="sub" :class="{ warn: row.cookie_ahead_hours !== null && row.cookie_ahead_hours < 24 }">
                {{ aheadText(row.cookie_ahead_hours) }}
              </div>
            </div>
            <el-tooltip v-else content="抖音没有给凭证写到期时间（由服务端控制），真失效时后端会暂停发送并记日志。" placement="top">
              <span class="muted">—</span>
            </el-tooltip>
          </template>
        </el-table-column>

        <el-table-column label="备注" min-width="160" :show-overflow-tooltip="cellTooltip">
          <template #default="{ row }">
            <span :class="{ muted: !row.note }">{{ row.note || '未填写' }}</span>
          </template>
        </el-table-column>

        <el-table-column label="添加时间" width="180">
          <template #default="{ row }">
            <el-tooltip v-if="row.added_at" content="面板第一次确认真实登录成功的时间，不是文件创建时间。" placement="top">
              <span>{{ row.added_at }}</span>
            </el-tooltip>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="110" fixed="right">
          <template #default>
            <el-button link type="primary" @click="editNote">编辑备注</el-button>
          </template>
        </el-table-column>

        <template #empty>
          <el-empty :description="loading ? '正在读取…' : '没有读到账号信息'" />
        </template>
      </el-table>

      <div class="group-title">运行概况</div>
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item label="自动化引擎">{{ facts.engine || '—' }}</el-descriptions-item>
        <el-descriptions-item label="反检测注入">
          {{ facts.stealth_injected ? '已注入' : '未注入' }}
        </el-descriptions-item>
        <el-descriptions-item label="登录凭证">
          {{ facts.cookie_count ? facts.cookie_count + ' 个 Cookie' : '无' }}
          <span v-if="facts.cookie_names && facts.cookie_names.length" class="sub">
            （{{ facts.cookie_names.join('、') }}）
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="登录态目录">{{ facts.profile_dir || '—' }}</el-descriptions-item>
      </el-descriptions>

      <p class="footnote">
        本程序是单用户版：只登录一个抖音账号，就是这里显示的这一个（登录态存在
        <code>chrome-profile/</code> 目录里，重启后端会自动恢复）。所以这里只有一行，
        没有「添加账号 / 删除账号」这一类操作 —— 要换账号就用下面的登录区退出登录后重新扫码。
        备注和添加时间存在本地 <code>data/state.json</code>，Cookie 状态、好友数、今日已发都是实时读出来的。
      </p>
    </el-card>

    <DouyinLogin />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { getAccountInfo, setAccountNote, fallbackError } from '../api/douyin'
import DouyinLogin from '../components/DouyinLogin.vue'

// 表格单元格的悬浮提示配置：交给全局样式 .spark-cell-tip 收成一个固定大小的小框。
const cellTooltip = { popperClass: 'spark-cell-tip', showArrow: false, enterable: true }

const loading = ref(false)
const info = ref(null)

const rows = computed(() => (info.value ? [info.value] : []))
const facts = computed(() => info.value || {})
const cookieText = computed(() => (info.value ? info.value.cookie_text : '读取中…'))
const cookieTagType = computed(() => tagTypeOf(info.value ? info.value.cookie_status : 'unknown'))

// Cookie 状态到标签颜色：只有「有效」是绿的，快到期是黄的，真出问题是红的。
function tagTypeOf(status) {
  if (status === 'valid') return 'success'
  if (status === 'expiring') return 'warning'
  if (status === 'expired' || status === 'missing') return 'danger'
  return 'info'
}

const aheadText = (hours) => {
  if (hours === null || hours === undefined) return ''
  if (hours < 0) return `已过期 ${Math.abs(hours).toFixed(1)} 小时`
  if (hours < 48) return `约 ${hours.toFixed(1)} 小时后到期`
  return `约 ${Math.floor(hours / 24)} 天后到期`
}

const load = async () => {
  loading.value = true
  try {
    // 拦截器返回的是响应体 {code, data}，所以读 res.code / res.data（没有 res.status）。
    const res = await getAccountInfo()
    if (res.code == 200) {
      info.value = res.data || null
    } else {
      ElMessage.error(res.data || '读取账号信息失败')
    }
  } catch (error) {
    fallbackError(error, '读取账号信息失败')
  } finally {
    loading.value = false
  }
}

const editNote = async () => {
  const current = (info.value && info.value.note) || ''
  const max = (info.value && info.value.note_max) || 100
  try {
    const { value } = await ElMessageBox.prompt('备注只存在本地，不影响登录和发送。', '编辑账号备注', {
      inputValue: current,
      inputPlaceholder: `最多 ${max} 个字，留空表示不写备注`,
      inputValidator: (text) => (text || '').length <= max || `最多 ${max} 个字`,
      confirmButtonText: '保存',
      cancelButtonText: '取消'
    })
    const res = await setAccountNote(value || '')
    if (res.code == 200) {
      ElMessage.success('备注已保存')
      await load()
    } else {
      ElMessage.error(res.data || '保存备注失败')
    }
  } catch (error) {
    // 点取消不是错误，不要弹提示
    if (error !== 'cancel' && error !== 'close') fallbackError(error, '保存备注失败')
  }
}

onMounted(load)
</script>

<style scoped>
.account-container {
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

.tip {
  margin-bottom: 14px;
}

.account-name {
  display: flex;
  align-items: center;
  gap: 8px;
}

.group-title {
  margin: 18px 0 10px;
  font-weight: 600;
  color: #303133;
}

.sub {
  color: #909399;
  font-size: 12px;
}

.sub.warn {
  color: #e6a23c;
}

.muted {
  color: #c0c4cc;
}

.footnote {
  margin: 16px 0 0;
  color: #909399;
  font-size: 12px;
  line-height: 1.7;
}

.footnote code {
  background: #f5f7fa;
  padding: 1px 4px;
  border-radius: 3px;
}

@media (max-width: 768px) {
  .card-header {
    flex-wrap: wrap;
    gap: 8px;
  }
}
</style>
