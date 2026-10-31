import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'

const DEFAULT_API_TARGET = 'http://localhost:8000'

// Where the dev server forwards /api. Override with API_PROXY_TARGET (shell or frontend/.env).
function apiProxyTarget(value: string | undefined): string {
  if (!value) return DEFAULT_API_TARGET
  let url: URL
  try {
    url = new URL(value)
  } catch {
    throw new Error(`API_PROXY_TARGET must be an http(s) URL, got "${value}"`)
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error(`API_PROXY_TARGET must be an http(s) URL, got "${value}"`)
  }
  return value
}

// The Spotify logo files are never inlined as data: URIs: the CSP allows images from 'self' only.
// Every other asset keeps Vite's default rule (undefined).
export function inlineLimit(file: string): boolean | undefined {
  return file.replaceAll(String.fromCharCode(92), '/').includes('/src/assets/brand/') ? false : undefined
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react(), tailwindcss()],
    build: { assetsInlineLimit: inlineLimit },
    server: {
      proxy: {
        '/api': { target: apiProxyTarget(env.API_PROXY_TARGET) },
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      testTimeout: 15000,
      unstubGlobals: true,
      restoreMocks: true,
    },
  }
})
