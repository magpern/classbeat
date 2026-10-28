import { defineConfig, devices } from '@playwright/test'

// Reads the worktree's .env (WEB_PORT) when present; real environment variables win.
try {
  process.loadEnvFile(new URL('../.env', import.meta.url))
} catch {
  // no .env (CI creates one; E2E_BASE_URL can be set instead)
}

const baseURL = process.env.E2E_BASE_URL ?? `http://127.0.0.1:${process.env.WEB_PORT ?? '8000'}`

export default defineConfig({
  testDir: './tests',
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL,
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
