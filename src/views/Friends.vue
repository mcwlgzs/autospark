<template>
  <div class="friends-container">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>好友列表</span>
          <div class="header-actions">
            <el-button v-if="!selectionMode" type="primary" :icon="Refresh" @click="loadFriends" :loading="loading">
              刷新
            </el-button>
            <el-button v-if="!selectionMode" type="success" :icon="Tickets" @click="selectionMode = true">
              多选
            </el-button>
            <template v-if="selectionMode">
              <el-button type="primary" :icon="Refresh" @click="loadFriends" :loading="loading">
                刷新
              </el-button>
              <el-button type="success" :icon="Check" @click="openBatchTaskDialog">
                创建定时任务 ({{ selectedFriends.length }})
              </el-button>
              <el-button :icon="Close" @click="cancelSelection">
                取消
              </el-button>
            </template>
          </div>
        </div>
      </template>

      <!-- 搜索框 -->
      <div class="search-bar">
        <el-input
          v-model="searchKeyword"
          placeholder="搜索好友..."
          :prefix-icon="Search"
          clearable
          style="max-width: 220px"
        />
        <el-select v-model="fireFilter" placeholder="火花筛选" clearable style="width: 140px; margin-left: 10px">
          <el-option label="全部" value="all" />
          <el-option label="有火花" value="has" />
          <el-option label="无火花" value="none" />
        </el-select>
      </div>

      <!-- 好友列表 -->
      <el-table
        v-loading="loading"
        :data="filteredFriends"
        stripe
        style="width: 100%"
        @selection-change="handleSelectionChange"
      >
        <el-table-column v-if="selectionMode" type="selection" width="50" />
        <el-table-column type="index" label="序号" :width="selectionMode ? 80 : 60" />
        <el-table-column label="头像" width="80">
          <template #default="{ row }">
            <el-avatar :size="40" :src="row.avatar">
              <el-icon><User /></el-icon>
            </el-avatar>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="昵称" min-width="120" />
        <el-table-column prop="fire" label="火花天数" width="100">
          <template #default="{ row }">
            <el-tag v-if="isFireActive(row.fire)" type="warning">{{ row.fire }}🔥</el-tag>
            <el-tag v-else type="info">无火花</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-dropdown @command="(cmd) => handleCommand(cmd, row)" trigger="click">
              <el-button type="primary" size="small">
                操作<el-icon class="el-icon--right"><ArrowDown /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="send">发送消息</el-dropdown-item>
                  <el-dropdown-item command="create">创建任务</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && filteredFriends.length === 0" description="暂无好友数据" />
    </el-card>

    <!-- 发送消息对话框 -->
    <el-dialog v-model="sendDialogVisible" title="发送消息" width="500px" destroy-on-close>
      <el-form :model="sendForm" label-width="80px">
        <el-form-item label="好友">
          <el-input v-model="sendForm.name" disabled />
        </el-form-item>
        <el-form-item label="消息内容">
          <el-input
            v-model="sendForm.text"
            type="textarea"
            :rows="4"
            :placeholder="sendForm.source === 'hitokoto'
              ? '可留空：发送时现取一句一言，取不到才用这里的文字'
              : (sendForm.source === 'girlfriend'
                ? '可留空：发送时按当时时段自动写问候，天气取不到才用这里的文字'
                : (sendForm.wenchangSign ? '可留空，留空则用当天签文文字' : '请输入消息内容'))"
          />
        </el-form-item>
        <el-form-item label="内容来源">
          <el-radio-group v-model="sendForm.source">
            <el-radio value="text">用上面写的内容</el-radio>
            <el-radio value="hitokoto">一言接口（发送时现取一句）</el-radio>
            <el-radio value="girlfriend">女朋友模式（天气问候）</el-radio>
          </el-radio-group>
          <div v-if="sendForm.source === 'girlfriend'" class="field-hint">
            发送时按当时时段自动写早安/午安/晚安，带当天真实天气、农历和相识天数；
            天气取不到时用上面的消息内容兜底（在「设置 → 女朋友模式」里配置天气接口）。
          </div>
          <el-button
            v-if="sendForm.source === 'hitokoto' || sendForm.source === 'girlfriend'"
            link
            type="primary"
            :loading="sendForm.source === 'girlfriend' ? girlfriendLoading : hitokotoLoading"
            @click="sendForm.source === 'girlfriend' ? handlePreviewGirlfriend() : handlePreviewHitokoto()"
          >
            取一条试试
          </el-button>
          <div class="field-hint">
            一言（https://v1.hitokoto.cn/?c=k，哲学分类）每次随机给一句短句并带上出处，
            格式是『正文』—— 「来源 作者」；选它之后上面的消息内容只当兜底，
            接口取不到时才用写下的文字。
          </div>
        </el-form-item>
        <el-form-item label="灵签">
          <el-switch v-model="sendForm.wenchangSign" active-text="附带文昌帝君灵签图片" />
          <div class="field-hint">
            勾选后由后端现取一条当天的文昌帝君灵签，发送签图；消息内容留空时正文用当天签文，填了就保留你写的文字（签图照发）。
          </div>
        </el-form-item>
        <el-form-item v-if="sendForm.wenchangSign" label="试发内容">
          <el-radio-group v-model="sendForm.imageOnly">
            <el-radio :value="false">签图 + 签文文字</el-radio>
            <el-radio :value="true">只发签图</el-radio>
          </el-radio-group>
          <div class="field-hint">
            这里是真实发送，用来验证签图到底能不能发出去；成功会占用对方今天的发送记录，
            今天的定时任务不会再重复发一条。想先确认能不能打开会话，点「预检（不发送）」。
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="sendDialogVisible = false">取消</el-button>
        <el-button :icon="Search" @click="handleCheck" :loading="checkLoading">
          预检（不发送）
        </el-button>
        <el-button type="primary" @click="handleSend" :loading="sendLoading">发送</el-button>
      </template>
    </el-dialog>

    <!-- 创建定时任务对话框 -->
    <el-dialog v-model="taskDialogVisible" title="创建定时任务" width="500px" destroy-on-close>
      <el-form :model="taskForm" label-width="80px">
        <el-form-item label="好友">
          <el-input v-model="taskForm.name" disabled />
        </el-form-item>
        <el-form-item label="执行时间">
          <el-time-picker
            v-model="taskForm.time"
            format="HH:mm"
            value-format="HH:mm"
            placeholder="选择时间"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="消息内容">
          <el-input
            v-model="taskForm.text"
            type="textarea"
            :rows="3"
            placeholder="留空将使用每日名言"
          />
        </el-form-item>
        <el-form-item label="灵签">
          <el-switch v-model="taskForm.sign" active-text="每天发送文昌帝君灵签图文" />
          <div class="field-hint">
            勾选后这个任务每天发送灵签签图；消息内容留空则用当天签文文字。
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="taskDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleCreateTask" :loading="taskLoading">创建</el-button>
      </template>
    </el-dialog>

    <!-- 批量创建定时任务对话框 -->
    <el-dialog v-model="batchTaskDialogVisible" title="批量创建定时任务" width="600px" destroy-on-close>
      <div class="selected-friends">
        <span class="label">已选好友 ({{ selectedFriends.length }})：</span>
        <el-tag v-for="f in selectedFriends" :key="f.name" style="margin: 4px 4px 4px 0">
          {{ f.name }}
        </el-tag>
        <span v-if="selectedFriends.length === 0" class="empty-hint">未选择任何好友</span>
      </div>
      <el-divider />
      <el-form :model="batchTaskForm" label-width="80px">
        <el-form-item label="执行时间">
          <el-time-picker
            v-model="batchTaskForm.time"
            format="HH:mm"
            value-format="HH:mm"
            placeholder="选择时间"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="消息内容">
          <el-input
            v-model="batchTaskForm.text"
            type="textarea"
            :rows="3"
            placeholder="留空将使用每日名言"
          />
        </el-form-item>
        <el-form-item label="灵签">
          <el-switch v-model="batchTaskForm.sign" active-text="每天发送文昌帝君灵签图文" />
          <div class="field-hint">
            对本次批量创建的每个好友都生效：勾选后每天发送灵签签图，消息内容留空则用当天签文文字。
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="batchTaskDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleBatchCreateTask" :loading="batchTaskLoading">
          批量创建 ({{ selectedFriends.length }})
        </el-button>
      </template>
    </el-dialog>
    <!-- 女朋友模式预览：接口返回的 text 是多行正文，用 pre-wrap 保留换行 -->
    <el-dialog v-model="girlfriendPreviewVisible" :title="girlfriendPreviewTitle" width="560px" destroy-on-close>
      <pre class="preview-text">{{ girlfriendPreviewText }}</pre>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onActivated } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Search, User, ArrowDown, Tickets, Check, Close } from '@element-plus/icons-vue'
import { sendMessage, sendWenchangSign, addTask, getFriendsList, checkSend, previewHitokoto, previewGirlfriend, fallbackError } from '../api/douyin'
import { friendsList, setFriendsList } from '../stores/browser'

const loading = ref(false)
const searchKeyword = ref('')
const fireFilter = ref('')

// 判断是否有火花：数值>0 或文本非空非"0"
const isFireActive = (fire) => {
  if (!fire) return false
  const n = Number(fire)
  return !isNaN(n) ? n > 0 : true
}

const sendDialogVisible = ref(false)
const sendLoading = ref(false)
const sendForm = ref({
  name: '',
  text: '',
  // 正文来源：'text' = 用上面写的内容；'hitokoto' = 发送时现取一句一言（写下的只当兜底）
  source: 'text',
  // 灵签开关：勾选后走「签图 + 签文」通道，正文允许留空
  wenchangSign: false,
  // 勾了灵签之后还能选「只发签图」：不附任何文字，用来单独验证图片这条路
  imageOnly: false
})

const taskDialogVisible = ref(false)
const taskLoading = ref(false)
const taskForm = ref({
  name: '',
  time: '',
  text: '',
  // 单条任务的灵签开关（对应后端 /Time/add 的 sign 布尔字段）
  sign: false
})

const selectionMode = ref(false)
const selectedFriends = ref([])
const batchTaskDialogVisible = ref(false)
const batchTaskLoading = ref(false)
const batchTaskForm = ref({
  time: '',
  text: '',
  sign: false
})

const filteredFriends = computed(() => {
  let list = friendsList.value

  if (searchKeyword.value) {
    const keyword = searchKeyword.value.toLowerCase()
    list = list.filter(f => f.name.toLowerCase().includes(keyword))
  }

  if (fireFilter.value && fireFilter.value !== 'all') {
    list = list.filter(f => {
      if (fireFilter.value === 'has') return isFireActive(f.fire)
      if (fireFilter.value === 'none') return !isFireActive(f.fire)
      return false
    })
  }

  return list
})

// 刷新按钮 - 请求 API 获取最新数据
const loadFriends = async () => {
  if (loading.value) return
  loading.value = true
  try {
    const data = await getFriendsList()
    if (data.code == 401) {
      // 面板未授权：正常情况下拦截器已经拦掉并跳 /login 了，这里只作为兜底
      window.location.replace('/login')
      loading.value = false
      return
    }
    if (data.code === 200) {
      const list = data.data.list || {}
      const formattedList = Object.entries(list).map(([name, [avatar, fire]]) => ({
        name,
        avatar,
        fire
      }))
      setFriendsList(formattedList)
      return
    }
    ElMessage.error(data.data || '加载好友列表失败')
  } catch (error) {
    // 面板会话失效（面板未登录）由 api/douyin.js 的拦截器统一处理：它已经弹过
    // 「登录已过期」并把用户带回 /login。这里再去问「是否前往登录抖音」会一次失败
    // 弹两个框，而且会把用户骗到 /settings（真正需要的是重新登录面板）。
    if (error && error.__reported) {
      return
    }
    const message = error?.message || ''
    // 剩下的才是「抖音账号」层面的问题：面板登录着，但后端读不到好友
    if (message.includes('未登录') || message.includes('未初始化')) {
      ElMessageBox.confirm('您还未登录抖音账号，是否前往登录？', '提示', {
        confirmButtonText: '前往登录',
        cancelButtonText: '取消',
        type: 'warning'
      }).then(() => {
        window.location.href = '/settings'
      }).catch(() => {})
    } else {
      ElMessage.error(message || '加载好友列表失败')
    }
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadFriends()
})

onActivated(async () => {
  await loadFriends()
})

const openSendDialog = (friend) => {
  sendForm.value = {
    name: friend.name,
    text: '',
    source: 'text',
    // 每次打开都归零：弹窗虽然 destroy-on-close，但表单状态在组件里，
    // 不重置会让「上次勾了灵签 / 上次选了只发图 / 上次选了一言」顺延到下一个好友
    wenchangSign: false,
    imageOnly: false
  }
  sendDialogVisible.value = true
}

const checkLoading = ref(false)

// 预检：只验证登录、定位好友、确认能打开会话，不发送任何消息
const handleCheck = async () => {
  if (!sendForm.value.name) {
    ElMessage.warning('请先选择好友')
    return
  }
  checkLoading.value = true
  try {
    const res = await checkSend(sendForm.value.name)
    if (res.code === 200) ElMessage.success(res.data)
    else ElMessage.error(res.data || '预检未通过')
  } catch (error) {
    fallbackError(error, '预检失败')
  } finally {
    checkLoading.value = false
  }
}

const hitokotoLoading = ref(false)

// 女朋友模式预览：多行正文用弹窗展示（不复用一言那条 ElMessage 路径）
const girlfriendLoading = ref(false)
const girlfriendPreviewVisible = ref(false)
const girlfriendPreviewTitle = ref('女朋友模式预览')
const girlfriendPreviewText = ref('')

// 只取一句给用户看看，不发出去（对应后端 GET /Api/Hitokoto/Preview）
const handlePreviewHitokoto = async () => {
  hitokotoLoading.value = true
  try {
    const res = await previewHitokoto()
    // 后端成功时返回的是 {'code':200,'data':{'text':'…'}} —— data 是**对象**不是字符串。
    // 直接把 data 丢给 ElMessage 会弹出一个「绿色但什么都没有」的通知（用户 m02903 报的）。
    const detail = res?.data
    if (detail && typeof detail === 'object' && detail.text) {
      ElMessage.success(detail.text)
    } else {
      ElMessage.error(typeof detail === 'string' && detail
        ? detail
        : '一言接口暂时取不到内容，请稍后再试')
    }
  } catch (error) {
    fallbackError(error, '一言接口暂时取不到内容，请稍后再试')
  } finally {
    hitokotoLoading.value = false
  }
}

// 「取一条试试」的女朋友模式分支（对应后端 GET /Api/Girlfriend/Preview?period=auto）。
// 成功时 res.data 是对象（{period, period_text, text}），失败时是中文原因字符串。
const handlePreviewGirlfriend = async () => {
  girlfriendLoading.value = true
  try {
    const res = await previewGirlfriend('auto')
    const detail = res?.data
    if (detail && typeof detail === 'object' && detail.text) {
      girlfriendPreviewTitle.value = detail.period_text
        ? `女朋友模式预览（${detail.period_text}）`
        : '女朋友模式预览'
      girlfriendPreviewText.value = detail.text
      girlfriendPreviewVisible.value = true
    } else {
      ElMessage.error(typeof detail === 'string' && detail
        ? detail
        : '女朋友模式暂时取不到内容，请稍后再试')
    }
  } catch (error) {
    fallbackError(error, '女朋友模式暂时取不到内容，请稍后再试')
  } finally {
    girlfriendLoading.value = false
  }
}

const handleSend = async () => {
  // 勾了灵签、选了一言或选了女朋友模式都放宽正文校验：正文由后端补（当天签文 /
  // 现取的一句一言 / 按当时时段写的问候），这里再拦「请输入消息内容」会让用户
  // 选好了来源却怎么都发不出去。
  const source = sendForm.value.source === 'hitokoto'
    ? 'hitokoto'
    : (sendForm.value.source === 'girlfriend' ? 'girlfriend' : 'text')
  if (!sendForm.value.wenchangSign && source === 'text' && !sendForm.value.text.trim()) {
    ElMessage.warning('请输入消息内容')
    return
  }

  sendLoading.value = true
  try {
    // 只有真的选了非默认来源才展开 source：没选时 options 是空对象，
    // 请求体与加这个功能之前逐字节相同
    const options = source === 'text' ? {} : { source }
    const res = sendForm.value.wenchangSign
      ? await sendWenchangSign(sendForm.value.name, sendForm.value.text, {
          image_only: sendForm.value.imageOnly,
          ...options
        })
      : await sendMessage(sendForm.value.name, sendForm.value.text, options)
    ElMessage.success('发送成功')
    sendDialogVisible.value = false
  } catch (error) {
    fallbackError(error, '发送失败')
  } finally {
    sendLoading.value = false
  }
}

const handleCommand = (command, row) => {
  if (command === 'send') {
    openSendDialog(row)
  } else if (command === 'create') {
    openCreateTaskDialog(row)
  }
}

const openCreateTaskDialog = (friend) => {
  taskForm.value = {
    name: friend.name,
    time: '',
    text: '',
    // 默认普通任务：灵签必须由用户显式勾选，避免误发签图
    sign: false
  }
  taskDialogVisible.value = true
}

const handleCreateTask = async () => {
  if (!taskForm.value.time) {
    ElMessage.warning('请选择时间')
    return
  }

  taskLoading.value = true
  try {
    await addTask(taskForm.value.time, taskForm.value.name, taskForm.value.text || null, {
      sign: taskForm.value.sign
    })
    ElMessage.success('创建成功')
    taskDialogVisible.value = false
  } catch (error) {
    ElMessage.error('创建失败')
  } finally {
    taskLoading.value = false
  }
}

const handleSelectionChange = (rows) => {
  selectedFriends.value = rows
}

const cancelSelection = () => {
  selectionMode.value = false
  selectedFriends.value = []
}

const openBatchTaskDialog = () => {
  if (selectedFriends.value.length === 0) {
    ElMessage.warning('请先选择好友')
    return
  }
  batchTaskForm.value = { time: '', text: '', sign: false }
  batchTaskDialogVisible.value = true
}

const handleBatchCreateTask = async () => {
  if (!batchTaskForm.value.time) {
    ElMessage.warning('请选择时间')
    return
  }

  batchTaskLoading.value = true
  let success = 0
  let failed = 0
  try {
    for (const friend of selectedFriends.value) {
      try {
        await addTask(batchTaskForm.value.time, friend.name, batchTaskForm.value.text || null, {
          sign: batchTaskForm.value.sign
        })
        success++
      } catch {
        failed++
      }
    }
    if (failed === 0) {
      ElMessage.success(`批量创建成功，共 ${success} 个任务`)
    } else {
      ElMessage.warning(`完成：成功 ${success} 个，失败 ${failed} 个`)
    }
    batchTaskDialogVisible.value = false
    cancelSelection()
  } finally {
    batchTaskLoading.value = false
  }
}
</script>

<style scoped>
.friends-container {
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.selected-friends {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  max-height: 120px;
  overflow-y: auto;
}

.selected-friends .label {
  font-weight: 500;
  margin-right: 8px;
  white-space: nowrap;
}

.selected-friends .empty-hint {
  color: #999;
  font-size: 14px;
}

.search-bar {
  margin-bottom: 20px;
}

/* 表单字段下方的解释文案：说明灵签开关会附带什么、正文留空时会发生什么 */
.field-hint {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: #909399;
}

/* 女朋友模式预览正文：保留接口返回的多行换行（ElMessage 会把换行挤成一行） */
.preview-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.8;
  color: #303133;
}

/* 响应式适配 */
@media (max-width: 768px) {
  :deep(.el-table) {
    font-size: 14px;
  }

  :deep(.el-button) {
    padding: 8px 12px;
    font-size: 12px;
  }
}
</style>
