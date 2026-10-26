import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 90000,
  expect: { timeout: 30000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { outputFolder: '../test-results/browser-report', open: 'never' }]],
  outputDir: '../test-results/browser-artifacts',
  use: { baseURL: 'http://127.0.0.1:5173', actionTimeout: 15000, navigationTimeout: 30000, trace: 'retain-on-failure', screenshot: 'only-on-failure', acceptDownloads: true },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    { command: 'python ../backend/scripts/seed_browser_acceptance.py --serve', url: 'http://127.0.0.1:8000/health/ready', timeout: 120000, reuseExistingServer: false },
    { command: 'npm run dev -- --host 127.0.0.1 --port 5173', url: 'http://127.0.0.1:5173', timeout: 60000, reuseExistingServer: false },
  ],
})
