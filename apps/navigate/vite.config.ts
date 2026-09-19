import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Relative asset paths, so dist/ can be hosted at any URL path.
export default defineConfig({
  base: './',
  plugins: [react()],
})
