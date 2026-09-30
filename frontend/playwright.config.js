import { defineConfig, devices } from '@playwright/test';

// Browser tests run against a mocked backend (see tests/e2e): no API keys, network or paid calls.
// PW_CHROMIUM_PATH / PW_CHROMIUM_ARGS optionally point at a preinstalled Chromium (for sandboxes
// that cannot download Playwright's browser). Normal runs use `npx playwright install chromium`.
const executablePath = process.env.PW_CHROMIUM_PATH;

export default defineConfig({
  testDir: 'tests/e2e',
  timeout: 30000,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:5199',
    acceptDownloads: true,
    ...(executablePath && { launchOptions: { executablePath, args: JSON.parse(process.env.PW_CHROMIUM_ARGS || '[]') } }),
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'npx vite --host 127.0.0.1 --port 5199 --strictPort',
    url: 'http://127.0.0.1:5199',
    reuseExistingServer: false,
    timeout: 60000,
  },
});
