import { createApp } from 'vue'
import { createPinia } from 'pinia'

// Element Plus 的「函数式」组件（ElMessage 顶部弹条、ElMessageBox 确认框）
// 不是写在模板里的 <el-xxx> 标签，而是代码里直接调用的 API。
// vite.config.js 里的 unplugin-vue-components 只处理两件事：模板里的标签，
// 以及由它自己注入的 import；各页面里手写的
// `import { ElMessage } from 'element-plus'` 它不认，也就不会连带引入样式 ——
// 结果弹出来的是一个完全没有样式的白框，正是「怎么没有通知样式」的原因。
// 这里显式补上官方 style/css 入口（内部会一并引入 base / badge 等依赖），
// 路径与按需插件注入的完全一致，不会把整个组件库的 CSS 拉进包里。
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'

import router from './router'
import App from './App.vue'
// 放在 Element Plus 样式之后：保证项目自己的样式能盖住组件库默认值
import './style.css'

const app = createApp(App)

// Pinia
app.use(createPinia())

// 路由
app.use(router)

// Element Plus 不再在这里全量注册：改由 vite.config.js 里的
// unplugin-vue-components + ElementPlusResolver 按模板里真实用到的标签按需引入。
// 全局注册省事，但代价是整个组件库（含全部图标）无条件进主包 ——
// 实测那一个 chunk 就有 1.1 MB，而后端只监听 127.0.0.1，面板常常是走内网/反代打开的。
// 图标同理：以前 `for (const [k,v] of Object.entries(ElementPlusIconsVue))` 把
// 上千个图标全注册了一遍，实际上各页面只 import 了自己用得上的那几个。

app.mount('#app')