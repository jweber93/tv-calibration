import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/report',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI
    ? [['html', { open: 'never' }], ['github']]
    : 'html',
  use: {
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    viewport: { width: 1440, height: 2200 },
    colorScheme: 'light',
    deviceScaleFactor: 1,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  snapshotDir: './tests/snapshots/report',
  snapshotPathTemplate: '{snapshotDir}/{testFilePath}/{arg}{ext}',
  updateSnapshots: 'none',
});
