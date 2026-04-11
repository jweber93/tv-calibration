import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  // Fail the build on CI if test.only() is accidentally committed
  forbidOnly: !!process.env.CI,
  // Retry once on CI to reduce flakiness from timing
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI
    ? [['html', { open: 'never' }], ['github']]
    : 'html',

  use: {
    baseURL: 'http://localhost:4173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },

  // Start the Vite preview server before running tests.
  // The frontend must be built first: npm run build
  webServer: {
    command: 'npm run preview',
    port: 4173,
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },

  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],

  // Visual snapshot baseline directory.
  // Generate baselines locally: npm run test:e2e:update
  // Commit the snapshots/ directory so CI can diff against them.
  snapshotDir: './tests/snapshots',
  updateSnapshots: 'none',
});
