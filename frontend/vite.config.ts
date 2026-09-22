import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In Docker the backend is a sibling service, not localhost. Override with
// VITE_PROXY_TARGET=http://backend:8000 (see docker-compose.yml).
const proxyTarget = process.env.VITE_PROXY_TARGET ?? 'http://127.0.0.1:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/api': {
        target: proxyTarget,
        changeOrigin: true,
      },
    },
  },
})
