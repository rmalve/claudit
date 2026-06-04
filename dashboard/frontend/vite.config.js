import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // Must match dashboard/start.py API_PORT (default 8001). Off 8000 to avoid
      // colliding with other local FastAPI apps (e.g. an onboarded project's webapp).
      '/api': 'http://localhost:8001',
    },
  },
})
