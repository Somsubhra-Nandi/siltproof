import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Component tests run in jsdom. MapLibre needs WebGL, which jsdom has not
// got, so src/__tests__/setup.ts stubs the map; everything else is the real
// component tree.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/__tests__/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
