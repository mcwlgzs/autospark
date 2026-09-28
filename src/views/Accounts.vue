<template>
  <div class="accounts-container">
    <el-card class="header-card">
      <div class="header">
        <h2>抖音账号管理</h2>
        <el-button type="primary" @click="showAddDialog">
          <el-icon><Plus /></el-icon>
          添加账号
        </el-button>
      </div>
    </el-card>

    <!-- 账号列表 -->
    <el-card v-loading="loading" class="accounts-card">
      <el-empty v-if="accounts.length === 0" description="暂无账号，请添加抖音账号">
        <el-button type="primary" @click="showAddDialog">立即添加</el-button>
      </el-empty>

      <div v-else class="accounts-grid">
        <el-card
          v-for="account in accounts"
          :key="account.id"
          :class="['account-item', { 'default-account': account.is_default }]"
          shadow="hover"
        >
          <div class="account-header">
            <el-avatar :size="60" :src="account.avatar || defaultAvatar" />
            <div class="account-info">
              <div class="nickname">
                {{ account.nickname || '未命名账号' }}
                <el-tag v-if="account.is_default" type="success" size="small">默认</el-tag>
                <el-tag v-if="account.status === 0" type="danger" size="small">已禁用</el-tag>
              </div>
              <div class="account-meta">
                <span v-if="account.unique_id">@{{ account.unique_id }}</span>
                <span v-else class="text-muted">未设置抖音号</span>
              </div>
              <div class="cookie-status">
                <el-tag
                  :type="getCookieStatusType(account.cookie_status)"
                  size="small"
                  effect="plain"
                >
                  {{ getCookieStatusText(account.cookie_status) }}
                </el-tag>
                <span v-if="account.cookie_expire" class="expire-time">
                  过期时间: {{ formatTime(account.cookie_expire) }}
                </span>
              </div>
            </div>
          </div>

          <div class="account-stats">
            <div class="stat-item">
              <div class="stat-value">{{ account.task_count || 0 }}</div>
              <div class="stat-label">关联任务</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ account.friend_count || 0 }}</div>
              <div class="stat-label">好友数量</div>
            </div>
          </div>

          <div class="account-actions">
            <el-button
              v-if="!account.is_default"
              size="small"
              @click="setDefault(account.id)"
            >
              设为默认
            </el-button>
            <el-button size="small" @click="showEditDialog(account)">
              编辑
            </el-button>
            <el-button
              size="small"
              type="primary"
              @click="loginAccount(account.id)"
            >
              重新登录
            </el-button>
            <el-button
              v-if="accounts.length > 1"
              size="small"
              type="danger"
              @click="deleteAccount(account.id)"
            >
              删除
            </el-button>
          </div>

          <div v-if="account.remark" class="account-remark">
            <el-icon><Document /></el-icon>
            {{ account.remark }}
          </div>
        </el-card>
      </div>
    </el-card>

    <!-- 添加/编辑对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogTitle"
      width="500px"
      @close="resetForm"
    >
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-width="100px"
      >
        <el-form-item label="账号昵称" prop="nickname">
          <el-input v-model="form.nickname" placeholder="请输入账号昵称" />
        </el-form-item>

        <el-form-item label="抖音号" prop="unique_id">
          <el-input v-model="form.unique_id" placeholder="请输入抖音号（可选）" />
        </el-form-item>

        <el-form-item label="备注">
          <el-input
            v-model="form.remark"
            type="textarea"
            :rows="3"
            placeholder="请输入备注信息（可选）"
          />
        </el-form-item>

        <el-form-item label="设为默认">
          <el-switch v-model="form.is_default" />
        </el-form-item>

        <el-form-item label="状态">
          <el-radio-group v-model="form.status">
            <el-radio :label="1">启用</el-radio>
            <el-radio :label="0">禁用</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submitForm">确定</el-button>
      </template>
    </el-dialog>

    <!-- 登录对话框 -->
    <el-dialog
      v-model="loginDialogVisible"
      title="账号登录"
      width="600px"
    >
      <div class="login-tips">
        <el-alert
          type="info"
          :closable="false"
          show-icon
        >
          <p>请按照以下步骤完成登录：</p>
          <ol>
            <li>点击「初始化浏览器」按钮</li>
            <li>在弹出的浏览器中扫码登录抖音</li>
            <li>登录成功后点击「确认登录」</li>
          </ol>
        </el-alert>
      </div>

      <div class="login-actions">
        <el-button type="primary" @click="initBrowser" :loading="browserInitializing">
          初始化浏览器
        </el-button>
        <el-button type="success" @click="confirmLogin" :disabled="!browserInitialized">
          确认登录
        </el-button>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Document } from '@element-plus/icons-vue'
import {
  getAccountsList,
  createAccount,
  updateAccount,
  deleteAccountApi,
  setDefaultAccount,
  initBrowser,
  getLoginStatus
} from '../api/douyin'

const loading = ref(false)
const accounts = ref([])
const dialogVisible = ref(false)
const dialogTitle = ref('添加账号')
const formRef = ref(null)
const editingId = ref(null)
const defaultAvatar = 'https://via.placeholder.com/60'

// 登录相关
const loginDialogVisible = ref(false)
const browserInitializing = ref(false)
const browserInitialized = ref(false)
const currentLoginAccountId = ref(null)

const form = ref({
  nickname: '',
  unique_id: '',
  remark: '',
  is_default: false,
  status: 1
})

const rules = {
  nickname: [
    { required: true, message: '请输入账号昵称', trigger: 'blur' }
  ]
}

// 加载账号列表
const loadAccounts = async () => {
  loading.value = true
  try {
    const res = await getAccountsList()
    accounts.value = res.data || []
  } catch (error) {
    ElMessage.error('加载账号列表失败')
  } finally {
    loading.value = false
  }
}

// 显示添加对话框
const showAddDialog = () => {
  dialogTitle.value = '添加账号'
  editingId.value = null
  dialogVisible.value = true
}

// 显示编辑对话框
const showEditDialog = (account) => {
  dialogTitle.value = '编辑账号'
  editingId.value = account.id
  form.value = {
    nickname: account.nickname,
    unique_id: account.unique_id || '',
    remark: account.remark || '',
    is_default: account.is_default === 1,
    status: account.status
  }
  dialogVisible.value = true
}

// 提交表单
const submitForm = async () => {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  try {
    const data = {
      ...form.value,
      is_default: form.value.is_default ? 1 : 0
    }

    if (editingId.value) {
      await updateAccount(editingId.value, data)
      ElMessage.success('更新成功')
    } else {
      await createAccount(data)
      ElMessage.success('添加成功')
    }

    dialogVisible.value = false
    loadAccounts()
  } catch (error) {
    ElMessage.error(editingId.value ? '更新失败' : '添加失败')
  }
}

// 重置表单
const resetForm = () => {
  form.value = {
    nickname: '',
    unique_id: '',
    remark: '',
    is_default: false,
    status: 1
  }
  editingId.value = null
}

// 设置默认账号
const setDefault = async (id) => {
  try {
    await setDefaultAccount(id)
    ElMessage.success('已设为默认账号')
    loadAccounts()
  } catch (error) {
    ElMessage.error('设置失败')
  }
}

// 删除账号
const deleteAccount = async (id) => {
  try {
    await ElMessageBox.confirm(
      '删除账号将同时删除该账号的所有关联数据（任务、好友等），是否继续？',
      '警告',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    await deleteAccountApi(id)
    ElMessage.success('删除成功')
    loadAccounts()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败')
    }
  }
}

// 登录账号
const loginAccount = (id) => {
  currentLoginAccountId.value = id
  loginDialogVisible.value = true
  browserInitialized.value = false
}

// 初始化浏览器
const handleInitBrowser = async () => {
  browserInitializing.value = true
  try {
    await initBrowser()
    browserInitialized.value = true
    ElMessage.success('浏览器已启动，请在浏览器中完成登录')
  } catch (error) {
    ElMessage.error('初始化浏览器失败')
  } finally {
    browserInitializing.value = false
  }
}

// 确认登录
const confirmLogin = async () => {
  try {
    const res = await getLoginStatus()
    if (res.data && res.data.is_logged_in) {
      // 更新账号cookie信息
      // TODO: 调用更新账号cookie的API
      ElMessage.success('登录成功')
      loginDialogVisible.value = false
      loadAccounts()
    } else {
      ElMessage.warning('请先在浏览器中完成登录')
    }
  } catch (error) {
    ElMessage.error('确认登录失败')
  }
}

// Cookie状态
const getCookieStatusType = (status) => {
  const map = {
    0: 'danger',
    1: 'success',
    2: 'warning'
  }
  return map[status] || 'info'
}

const getCookieStatusText = (status) => {
  const map = {
    0: '无效',
    1: '有效',
    2: '待刷新'
  }
  return map[status] || '未知'
}

// 格式化时间
const formatTime = (time) => {
  if (!time) return '-'
  const date = new Date(time)
  return date.toLocaleString('zh-CN')
}

onMounted(() => {
  loadAccounts()
})
</script>

<style scoped>
.accounts-container {
  padding: 20px;
}

.header-card {
  margin-bottom: 20px;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header h2 {
  margin: 0;
  font-size: 20px;
}

.accounts-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
  gap: 20px;
}

.account-item {
  position: relative;
  transition: all 0.3s;
}

.account-item:hover {
  transform: translateY(-5px);
}

.default-account {
  border: 2px solid var(--el-color-success);
}

.account-header {
  display: flex;
  gap: 15px;
  margin-bottom: 20px;
}

.account-info {
  flex: 1;
  min-width: 0;
}

.nickname {
  font-size: 18px;
  font-weight: bold;
  margin-bottom: 5px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.account-meta {
  color: #606266;
  font-size: 14px;
  margin-bottom: 5px;
}

.text-muted {
  color: #909399;
}

.cookie-status {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 5px;
}

.expire-time {
  font-size: 12px;
  color: #909399;
}

.account-stats {
  display: flex;
  justify-content: space-around;
  padding: 15px 0;
  border-top: 1px solid #eee;
  border-bottom: 1px solid #eee;
  margin-bottom: 15px;
}

.stat-item {
  text-align: center;
}

.stat-value {
  font-size: 24px;
  font-weight: bold;
  color: var(--el-color-primary);
}

.stat-label {
  font-size: 12px;
  color: #909399;
  margin-top: 5px;
}

.account-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.account-actions .el-button {
  flex: 1;
  min-width: 80px;
}

.account-remark {
  margin-top: 15px;
  padding: 10px;
  background: #f5f7fa;
  border-radius: 4px;
  font-size: 13px;
  color: #606266;
  display: flex;
  align-items: center;
  gap: 5px;
}

.login-tips {
  margin-bottom: 20px;
}

.login-tips ol {
  margin: 10px 0 0 0;
  padding-left: 20px;
}

.login-tips li {
  margin: 5px 0;
}

.login-actions {
  display: flex;
  gap: 10px;
  justify-content: center;
}
</style>
