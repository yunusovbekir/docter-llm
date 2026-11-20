import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': `http://127.0.0.1:${process.env.DOCTER_PORT ?? '8000'}`,
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/msw.ts'],
  },
})
