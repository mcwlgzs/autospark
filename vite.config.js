import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import path from 'path'

export default defineConfig({
  plugins: [
    vue(),
    // Element Plus 按需引入（官方推荐方式）。
    // 之前 main.js 是 `app.use(ElementPlus)` + 注册全部图标 + 引入整份 index.css，
    // 于是不管实际用到几个组件，整个组件库都会进主包：实测产物里那个 1.1 MB 的
    // vendor chunk 和 344 KB 的 index.css 基本都是这么来的。
    // 这两个插件按模板里真实出现的标签自动 import 组件与样式（含 ElMessage 这类
    // 直接写在 script 里的调用），因此 32 个 el-* 标签之外的代码不再进包。
    AutoImport({ resolvers: [ElementPlusResolver()] }),
    Components({ resolvers: [ElementPlusResolver()] })
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src')
    }
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:9844',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, '')
      }
    }
  },
  build: {
    rollupOptions: {
      output: {
        // 把体积大且很少变动的依赖单独切出来：改业务代码时这些 chunk 的 hash 不变，
        // 浏览器可以直接用缓存，不必重新下载。
        //
        // 注意这里必须用**函数**形式：Vite 8 底层换成了 rolldown，它只接受
        // manualChunks(id) => name，传对象会直接构建失败
        // （报 "Expected Function but received Object"）。
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('element-plus') || id.includes('@element-plus')) return 'vendor-element'
          if (id.includes('/vue/') || id.includes('vue-router') || id.includes('pinia')) return 'vendor-vue'
          return undefined
        }
      }
    }
  }
})