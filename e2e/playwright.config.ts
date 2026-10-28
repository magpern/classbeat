import { defineConfig, devices } from '@playwright/test'

// Reads the worktree's .env (WEB_PORT) when present; real environment variables win.
let envLoaded = false
try {
  process.loadEnvFile(new URL('../.env', import.meta.url))
  envLoaded = true
} catch (error) {
  // A missing .env is fine (E2E_BASE_URL can be set instead); an unreadable or malformed one
  // must not silently fall back to port 8000.
  if ((error as NodeJS.ErrnoException).code !== 'ENOENT') {
    throw new Error(`cannot load ../.env: ${(error as Error).message}`)
  }
}

// Node skips malformed .env lines silently, so check the result: with a .env present (and no
// E2E_BASE_URL) WEB_PORT must be set to a port number, else fail instead of using port 8000.
if (!process.env.E2E_BASE_URL && envLoaded && !/^\d{1,5}$/.test(process.env.WEB_PORT ?? '')) {
  throw new Error('../.env is malformed: WEB_PORT is missing or not a port number')
}
const webPort = process.env.WEB_PORT ?? '8000'

const baseURL = process.env.E2E_BASE_URL ?? `http://127.0.0.1:${webPort}`

export default defineConfig({
  testDir: './tests',
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL,
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
