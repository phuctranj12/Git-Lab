import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

// Dev: proxy API/registry sang backend chạy local (uvicorn :8000) để cookie cùng origin.
const backend = process.env.VITE_BACKEND_URL ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(__dirname, 'src') } },
  server: {
    port: 5173,
    proxy: {
      '/api': backend,
      '/health': backend,
      '/simple': backend,
      '/packages': backend,
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks: {
          antd: ['antd', '@ant-design/icons'],
          markdown: ['react-markdown', 'remark-gfm'],
        },
      },
    },
  },
})
