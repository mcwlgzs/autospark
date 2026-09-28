<template>
  <!-- 中文语言包必须挂在 el-config-provider 上：
       以前是 app.use(ElementPlus, { locale: zhCn })，改成按需引入之后那个全局配置没了，
       分页器、日期选择器这类组件会退回英文（“Go to”“Total”）。 -->
  <el-config-provider :locale="zhCn">
    <router-view />
  </el-config-provider>
</template>

<script setup>
import { onMounted } from 'vue'
// el-config-provider 由 unplugin-vue-components 自动引入；语言包要显式 import
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { useUserStore } from './stores/user'

const userStore = useUserStore()

onMounted(() => {
  userStore.restoreSession()
})
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html, body, #app {
  width: 100%;
  height: 100%;
}
</style>
