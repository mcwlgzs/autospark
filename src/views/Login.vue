<template>
  <div class="login-container">
    <div class="login-box">
      <div class="login-header">
        <h1>抖音火花助手</h1>
        <p>后台管理系统</p>
      </div>

      <el-form
        ref="loginFormRef"
        :model="loginForm"
        :rules="loginRules"
        class="login-form"
        @keyup.enter="handleLogin"
      >
        <el-form-item prop="username">
          <el-input
            v-model="loginForm.username"
            placeholder="请输入用户名"
            size="large"
            :prefix-icon="User"
          />
        </el-form-item>

        <el-form-item prop="password">
          <el-input
            v-model="loginForm.password"
            type="password"
            placeholder="请输入密码"
            size="large"
            :prefix-icon="Lock"
            show-password
          />
        </el-form-item>

        <el-form-item>
          <el-button
            type="primary"
            size="large"
            :loading="loading"
            class="login-button"
            @click="handleLogin"
          >
            {{ loading ? '登录中...' : '登 录' }}
          </el-button>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Lock } from '@element-plus/icons-vue'
import { useUserStore } from '../stores/user'

const router = useRouter()
const userStore = useUserStore()

const loginFormRef = ref(null)
const loading = ref(false)

const loginForm = reactive({
  // 后端只认 admin 这一个账号（backend.py 的 admin_login 里是硬编码判断，
  // 用户名写错了同样只回一句「登录失败」，看不出区别）。
  // 留空会让人以为要填一个自己注册过的用户名，白白卡在这里。预填好，
  // 仍然可以改。
  username: 'admin',
  password: ''
})

const loginRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' }
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 1, message: '密码不能为空', trigger: 'blur' }
  ]
}

const handleLogin = async () => {
  if (!loginFormRef.value) return

  await loginFormRef.value.validate(async (valid) => {
    if (valid) {
      loading.value = true
      try {
        const result = await userStore.login(loginForm.username, loginForm.password)
        if (result.success) {
          if (userStore.mustChangePassword) {
            // 默认密码状态下后端会拦住发消息/建任务/绑定账号等操作，
            // 所以这里必须明确告诉用户第一步该干什么
            ElMessage.warning('当前仍是内置默认密码，请先在「设置」页修改密码，之后才能发消息和创建任务')
          } else {
            ElMessage.success(result.message)
          }
          router.push(userStore.mustChangePassword ? '/settings' : '/')
        } else {
          ElMessage.error(result.message)
        }
      } catch (error) {
        ElMessage.error('登录失败')
      } finally {
        loading.value = false
      }
    }
  })
}
</script>

<style scoped>
.login-container {
  width: 100%;
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: #1a1a1a;
  /* 背景图上叠一层深色渐变：原图中间偏亮，而卡片是浅色半透明玻璃 + 白字，
     实测「抖音火花助手」标题和副标题几乎看不清。压暗之后白字对比度才够，
     同时保留雨滴玻璃的质感（不是简单把图换成纯色）。 */
  background-image:
    linear-gradient(rgba(12, 16, 24, 0.55), rgba(12, 16, 24, 0.72)),
    url('/drops_drips_glass_141451_1280x720.jpg');
  background-size: cover;
  background-position: center;
  background-repeat: no-repeat;
}

.login-box {
  width: 90%;
  max-width: 400px;
  padding: 40px 30px;
  /* 卡片本身也提高不透明度并加一层深色底：纯白 0.15 在亮背景上等于没有底色，
     blur 也压不住背景的高频细节（雨滴），文字会被噪点吃掉。 */
  background: rgba(20, 26, 38, 0.62);
  backdrop-filter: blur(20px) saturate(120%);
  -webkit-backdrop-filter: blur(20px) saturate(120%);
  border-radius: 16px;
  border: 1px solid rgba(255, 255, 255, 0.22);
  box-shadow: 0 25px 60px rgba(0, 0, 0, 0.55), 0 8px 20px rgba(0, 0, 0, 0.35);
}

.login-header {
  text-align: center;
  margin-bottom: 40px;
}

.login-header h1 {
  font-size: 28px;
  color: #fff;
  margin-bottom: 10px;
  text-shadow: 0 2px 10px rgba(0, 0, 0, 0.6);
}

.login-header p {
  font-size: 14px;
  color: rgba(255, 255, 255, 0.88);
  text-shadow: 0 1px 6px rgba(0, 0, 0, 0.55);
}

.login-form {
  margin-bottom: 20px;
}

.login-form :deep(.el-input__wrapper) {
  padding: 12px 16px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.login-form :deep(.el-input__inner) {
  font-size: 16px;
  color: #333;
}

.login-form :deep(.el-form-item__error) {
  color: #ff6b6b;
}

.login-button {
  width: 100%;
  height: 48px;
  font-size: 16px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.2);
  border: 1px solid rgba(255, 255, 255, 0.3);
  color: #fff;
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2), inset 0 1px 0 rgba(255, 255, 255, 0.2);
  transition: all 0.3s ease;
}

.login-button:hover {
  background: rgba(255, 255, 255, 0.3);
  border-color: rgba(255, 255, 255, 0.5);
  transform: translateY(-2px);
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.3);
}

.login-footer {
  text-align: center;
  color: rgba(255, 255, 255, 0.6);
  font-size: 12px;
}

/* 响应式适配 */
@media (max-width: 480px) {
  .login-box {
    width: 95%;
    padding: 30px 20px;
    border-radius: 12px;
    backdrop-filter: blur(15px);
    -webkit-backdrop-filter: blur(15px);
  }

  .login-header h1 {
    font-size: 24px;
  }

  .login-form :deep(.el-input__wrapper) {
    background: rgba(255, 255, 255, 0.95);
  }
}
</style>