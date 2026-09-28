<template>
  <div class="tasks-container">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>定时任务管理</span>
          <div class="header-buttons">
            <el-button v-if="!selectionMode" type="primary" :icon="Refresh" @click="refreshAll" :loading="loading">
              刷新
            </el-button>
            <el-button v-if="!selectionMode" type="success" :icon="Plus" @click="openAddDialog">
              添加任务
            </el-button>
            <template v-if="selectionMode">
              <el-button type="primary" :icon="Refresh" @click="refreshAll" :loading="loading">
                刷新
              </el-button>
              <el-button type="danger" :icon="Delete" @click="handleBatchDelete" :loading="batchDeleteLoading">
                删除 ({{ selectedTasks.length }})
              </el-button>
              <el-button :icon="Close" @click="cancelSelection">
                取消
              </el-button>
            </template>
            <el-button v-if="!selectionMode && taskList.length > 0" :icon="Tickets" @click="selectionMode = true">
              多选
            </el-button>
          </div>
        </div>
      </template>

      <!-- 任务列表 -->
      <el-table
        v-loading="loading"
        :data="taskList"
        stripe
        style="width: 100%"
        @selection-change="handleSelectionChange"
      >
        <el-table-column v-if="selectionMode" type="selection" width="50" />
        <el-table-column type="index" label="序号" :width="selectionMode ? 80 : 60" />
        <el-table-column prop="name" label="好友" min-width="120" />
        <el-table-column prop="time" label="执行时间" width="100" />
        <el-table-column prop="next_run" label="下次执行" min-width="160" />
        <el-table-column label="灵签" width="90">
          <template #default="{ row }">
            <!-- 老任务没有 sign 字段（后端也按 false 处理），这里只认严格的 true，
                 避免把 undefined 显示成灵签任务让用户误判 -->
            <el-tag v-if="row.sign === true" type="danger" effect="dark" size="small">灵签</el-tag>
            <span v-else class="not-sign">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="280" fixed="right">
          <template #default="{ row }">
            <el-button type="warning" size="small" @click="openEditDialog(row)">
              修改时间
            </el-button>
            <el-button size="small" :loading="testingId === row.task_id" @click="handleTestSend(row)">
              试发
            </el-button>
            <el-button type="danger" size="small" @click="handleDelete(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && taskList.length === 0" description="暂无定时任务">
        <el-button type="primary" @click="openAddDialog">添加第一个任务</el-button>
      </el-empty>
    </el-card>

    <!-- 添加/编辑对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogTitle"
      width="500px"
      destroy-on-close
    >
      <el-form :model="taskForm" label-width="80px">
        <el-form-item label="好友">
          <el-select
            v-model="taskForm.name"
            placeholder="选择好友"
            filterable
            :disabled="dialogMode === 'edit'"
            style="width: 100%"
          >
            <el-option
              v-for="friend in availableFriends"
              :key="friend.name"
              :label="friend.name"
              :value="friend.name"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="执行时间">
          <el-time-picker
            v-model="taskForm.time"
            format="HH:mm"
            value-format="HH:mm"
            placeholder="选择时间"
            style="width: 100%"
          />
          <div class="field-hint">
            实际发送时间 = 这个时间再随机推迟 0~{{ jitterMinutes }} 分钟（固定整点是机器特征）；
            机器重启错过当天时间点后会补跑。
          </div>
        </el-form-item>
        <el-form-item label="消息内容">
          <el-input
            v-model="taskForm.text"
            type="textarea"
            :rows="3"
            placeholder="留空 = 每天发送前现取一条名言"
          />
          <div class="field-hint">
            一行一条即为文案池，发送时随机挑一条；支持 {date}、{weekday} 占位符。
            编辑时留空会让它恢复成每日名言。
          </div>
        </el-form-item>
        <el-form-item label="灵签">
          <el-switch v-model="taskForm.sign" active-text="每天发送文昌帝君灵签（图文）" />
          <div class="field-hint">
            勾选后这条任务每天发送文昌帝君灵签签图；消息内容留空则正文用当天签文。
            关掉开关会把这条任务改回普通文字任务。
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSubmit" :loading="submitLoading">
          {{ dialogMode === 'add' ? '添加' : '修改' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Delete, Close, Tickets } from '@element-plus/icons-vue'
import { getTaskList, addTask, delTask, editTask, getFriendsList, testTask, fallbackError } from '../api/douyin'
import { friendsList as storeFriendsList, setFriendsList } from '../stores/browser'

const loading = ref(false)
const taskList = ref([])
const isFirstLoad = ref(true)

const selectionMode = ref(false)
const selectedTasks = ref([])
const batchDeleteLoading = ref(false)

const dialogVisible = ref(false)
const dialogMode = ref('add')
const submitLoading = ref(false)
// 后端下发的随机窗口分钟数（用于在界面上解释「为什么不是整点发」）
const jitterMinutes = ref(40)
const taskForm = ref({
  name: '',
  time: '',
  text: '',
  // 灵签任务开关：对应后端 sign 布尔字段，新增任务默认关（普通文字任务）
  sign: false
})

const dialogTitle = computed(() => dialogMode.value === 'add' ? '添加定时任务' : '修改任务')

const availableFriends = computed(() => {
  // 过滤掉已有任务的好友
  const existingNames = taskList.value.map(t => t.name)
  return storeFriendsList.value.filter(f => !existingNames.includes(f.name))
})

// 页面加载时获取任务列表（首次加载才请求接口）
onMounted(async () => {
  if (isFirstLoad.value) {
    // 首次加载：请求接口并缓存
    await refreshAll()
    isFirstLoad.value = false
  } else {
    // 非首次加载：从缓存恢复
    const cached = localStorage.getItem('douyin_tasks')
    if (cached) {
      taskList.value = JSON.parse(cached)
    }
  }
})

// 刷新按钮 - 同时请求任务列表和好友列表
const refreshAll = async () => {
  loading.value = true
  try {
    // 用 allSettled：好友列表拿不到（例如页面没加载好，后端回 404）时，
    // 任务列表这次请求其实是成功的，不该被一起丢掉。
    const [tasksResult, friendsResult] = await Promise.allSettled([
      getTaskList(),
      getFriendsList()
    ])

    // 更新任务列表
    const tasksRes = tasksResult.status === 'fulfilled' ? tasksResult.value : null
    if (tasksRes && tasksRes.code === 200) {
      const tasks = tasksRes.data.tasks || []
      taskList.value = tasks
      if (tasksRes.data.jitter_minutes !== undefined) {
        jitterMinutes.value = tasksRes.data.jitter_minutes
      }
      localStorage.setItem('douyin_tasks', JSON.stringify(tasks))
    }

    // 更新好友列表到 store
    const friendsRes = friendsResult.status === 'fulfilled' ? friendsResult.value : null
    if (friendsRes && friendsRes.code === 200) {
      const list = friendsRes.data.list || {}
      const formattedList = Object.entries(list).map(([name, [avatar, fire]]) => ({
        name,
        avatar,
        fire
      }))
      setFriendsList(formattedList)
    }

    // 两个都失败才算刷新失败；只失败一个时把原因说出来，不要笼统报「刷新失败」
    if (!tasksRes && !friendsRes) {
      ElMessage.error('刷新失败')
    } else if (!tasksRes) {
      ElMessage.error('任务列表刷新失败')
    }
  } finally {
    loading.value = false
  }
}

const openAddDialog = () => {
  dialogMode.value = 'add'
  taskForm.value = {
    name: '',
    time: '',
    text: '',
    sign: false
  }
  dialogVisible.value = true
}

const openEditDialog = (task) => {
  dialogMode.value = 'edit'
  taskForm.value = {
    name: task.name,
    time: task.time,
    // 回填当前文案：旧版本编辑弹窗不显示内容字段，用户只改个时间，
    // 后端就把自定义文案换成随机名言了
    text: task.text || '',
    // 按任务当前状态回填灵签开关：老任务没有这个字段（undefined）要当成 false，
    // 提交时也只是显式写回 false，不会把老任务意外升级成灵签任务
    sign: task.sign === true
  }
  dialogVisible.value = true
}

const handleSubmit = async () => {
  if (!taskForm.value.name && dialogMode.value === 'add') {
    ElMessage.warning('请选择好友')
    return
  }
  if (!taskForm.value.time) {
    ElMessage.warning('请选择时间')
    return
  }

  submitLoading.value = true
  try {
    if (dialogMode.value === 'add') {
      await addTask(taskForm.value.time, taskForm.value.name, taskForm.value.text || null, {
        sign: taskForm.value.sign
      })
      ElMessage.success('添加成功')
    } else {
      // 把文案一起提交：留空表示恢复「每日名言」
      // sign 显式传：编辑时必须能把灵签任务关回普通任务（省略字段后端会保持原值）
      await editTask(taskForm.value.name, taskForm.value.time, taskForm.value.text || '', {
        sign: taskForm.value.sign
      })
      ElMessage.success('修改成功')
    }
    dialogVisible.value = false
    // 使用刷新方法更新任务列表
    await refreshAll()
  } catch (error) {
    ElMessage.error(dialogMode.value === 'add' ? '添加失败' : '修改失败')
  } finally {
    submitLoading.value = false
  }
}

const handleDelete = async (task) => {
  try {
    await ElMessageBox.confirm(`确定要删除 ${task.name} 的定时任务吗？`, '提示', {
      type: 'warning'
    })
    await delTask(task.task_id)
    ElMessage.success('删除成功')
    await refreshAll()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败')
    }
  }
}

// 试发中的任务 ID：用来只给被点的那一行转圈，避免整张表都跟着禁用
const testingId = ref('')

// 立即试发：真发一条，用来当场验证这条任务（尤其是灵签图文）现在到底能不能发出去。
// 定时任务只会在设定时间 + 随机窗口触发，没有这个按钮就只能干等一天。
// 代价是：试发成功会占用该好友今天的发送记录 —— 所以先让用户确认，并把后果写在弹窗里。
const handleTestSend = async (task) => {
  const what = task.sign ? '文昌帝君灵签（签图 + 签文）' : '任务里的文案'
  try {
    await ElMessageBox.confirm(
      `会立刻给「${task.name}」真实发送一条${what}，用来验证这条任务现在能不能发出去。\n` +
      '试发成功会占用他今天的发送记录，今天的定时任务将不再重复发一条。',
      '立即试发',
      { type: 'warning', confirmButtonText: '确认试发', cancelButtonText: '取消' }
    )
  } catch (error) {
    return  // 用户点了取消
  }
  testingId.value = task.task_id
  try {
    const res = await testTask(task.task_id)
    ElMessage.success(res?.data || '试发成功')
    await refreshAll()
  } catch (error) {
    fallbackError(error, '试发失败')
  } finally {
    testingId.value = ''
  }
}

const handleSelectionChange = (rows) => {
  selectedTasks.value = rows
}

const cancelSelection = () => {
  selectionMode.value = false
  selectedTasks.value = []
}

const handleBatchDelete = async () => {
  if (selectedTasks.value.length === 0) {
    ElMessage.warning('请先选择要删除的任务')
    return
  }
  try {
    await ElMessageBox.confirm(`确定要删除选中的 ${selectedTasks.value.length} 个任务吗？`, '批量删除', {
      type: 'warning'
    })
    batchDeleteLoading.value = true
    let success = 0
    let failed = 0
    for (const task of selectedTasks.value) {
      try {
        await delTask(task.task_id)
        success++
      } catch {
        failed++
      }
    }
    if (failed === 0) {
      ElMessage.success(`批量删除成功，共 ${success} 个`)
    } else {
      ElMessage.warning(`完成：成功 ${success} 个，失败 ${failed} 个`)
    }
    cancelSelection()
    await refreshAll()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('批量删除失败')
    }
  } finally {
    batchDeleteLoading.value = false
  }
}
</script>

<style scoped>
.tasks-container {
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-buttons {
  display: flex;
  gap: 10px;
}

/* 表单字段下方的解释文案：说明随机窗口 / 文案池这些容易被误解的行为 */
.field-hint {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: #909399;
}

/* 非灵签任务的占位符：用浅色破折号，突出真正的灵签标签 */
.not-sign {
  color: #c0c4cc;
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

  :deep(.el-dialog) {
    width: 95% !important;
  }
}
</style>