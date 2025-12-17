import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/healthz': 'http://localhost:8000',
      '/metrics': 'http://localhost:8000',
      '/readyz': 'http://localhost:8000',
      '/twilio': 'http://localhost:8000',
      '/v1': 'http://localhost:8000',
    },
  },
})
