import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    watch: {
      usePolling: true, // Crucial para hot-reload no WSL2/Docker
    },
    host: true,
    strictPort: true,
    port: 5173,
    hmr: {
      clientPort: 5173
    }
  }
})