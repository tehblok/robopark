import react from '@vitejs/plugin-react'
import { realpathSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { configDefaults, defineConfig } from 'vitest/config'

function devApiTarget(): string {
  const raw = process.env.ROBOPARK_DEV_API_TARGET ?? 'http://127.0.0.1:8000'
  let url: URL
  try {
    url = new URL(raw)
  } catch {
    throw new Error('ROBOPARK_DEV_API_TARGET must be a loopback HTTP URL')
  }
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
    || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('ROBOPARK_DEV_API_TARGET must be a loopback HTTP URL')
  }
  return url.origin
}

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
        target: devApiTarget(),
        changeOrigin: true,
        ws: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  build: {
    rollupOptions: {
      input: {
        app: fileURLToPath(new URL('./index.html', import.meta.url)),
        terminal: fileURLToPath(new URL('./terminal.html', import.meta.url)),
      },
      output: {
        entryFileNames: chunk => chunk.name === 'terminal'
          ? 'assets/terminal/[name]-[hash].js' : 'assets/[name]-[hash].js',
        assetFileNames: asset => asset.names.some(name => name.includes('terminal'))
          ? 'assets/terminal/[name]-[hash][extname]' : 'assets/[name]-[hash][extname]',
      },
    },
  },
  test: {
    environment: 'jsdom',
    exclude: [...configDefaults.exclude, 'e2e/**/*.spec.ts', 'e2e-production/**/*.spec.ts', 'scripts/**/*.test.mjs'],
    setupFiles: ['./src/test/setup.ts'],
  },
})
