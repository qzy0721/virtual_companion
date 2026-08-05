import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  base: '/app/',
  build: {
    rollupOptions: {
      input: {
        app: fileURLToPath(new URL('./index.html', import.meta.url)),
        roleManager: fileURLToPath(new URL('./role-manager.html', import.meta.url)),
        settings: fileURLToPath(new URL('./settings.html', import.meta.url)),
        pet: fileURLToPath(new URL('./pet.html', import.meta.url)),
      },
    },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
      '/tts_audio': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
