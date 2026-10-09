import { resolve } from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      // trial.html is the standalone "Try SiltProof yourself" page
      // (src/features/judge-trial); index.html is the investigation.
      input: {
        main: resolve(__dirname, 'index.html'),
        trial: resolve(__dirname, 'trial.html'),
      },
    },
  },
})
