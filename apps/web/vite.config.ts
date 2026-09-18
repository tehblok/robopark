import react from '@vitejs/plugin-react'
import { realpathSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { configDefaults, defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    fs: {
      // Worktrees may share an installed dependency directory via a symlink.
      allow: [
        fileURLToPath(new URL('.', import.meta.url)),
        realpathSync(fileURLToPath(new URL('./node_modules', import.meta.url))),
      ],
    },
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  test: {
    environment: 'jsdom',
    exclude: [...configDefaults.exclude, 'e2e/**/*.spec.ts', 'scripts/**/*.test.mjs'],
    setupFiles: ['./src/test/setup.ts'],
  },
})
