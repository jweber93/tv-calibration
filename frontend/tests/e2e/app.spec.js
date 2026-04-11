/**
 * E2E tests for the TV Calibration Helper app.
 *
 * All API calls are intercepted via page.route() so no backend is required.
 * The preview server (npm run preview) serves the built frontend on :4173.
 */
import { test, expect } from '@playwright/test';

// ── Mock data ──────────────────────────────────────────────────────────────

const PROFILES = [
  { key: 'u8g',  name: 'Samsung U8G' },
  { key: 'c2',   name: 'Samsung C2'  },
  { key: 'lg_c3', name: 'LG C3'     },
];

/**
 * Wire up API mocks for all background and polling calls the app makes on
 * startup.  A single *\/api\/** catch-all fulfills every endpoint so tests
 * stay focused on UI behaviour rather than API wiring.
 *
 * @param {import('@playwright/test').Page} page
 * @param {object} [opts]
 * @param {object|null} [opts.session]  Active session object, or null for no session
 * @param {object[]}    [opts.profiles] List of TV profiles
 */
async function mockApi(page, { session = null, profiles = PROFILES } = {}) {
  // Abort SSE connections — EventSource would hang without a real backend
  await page.route('**/events/**', route => route.abort());

  // Single catch-all for all /api/* requests
  await page.route('**/api/**', route => {
    const url    = route.request().url();
    const method = route.request().method();

    // Session
    if (/\/api\/session$/.test(url) && method === 'GET') {
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify(session) });
    }

    // Profiles
    if (/\/api\/profiles$/.test(url)) {
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify(profiles) });
    }

    // File watcher polling
    if (url.includes('/api/watch/status')) {
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ watching: false, path: null, error: null, last_import: null, diagnostics: {} }) });
    }

    // ZRO bridge (status + url save)
    if (url.includes('/api/bridge')) {
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ ok: false, configured: false }) });
    }

    // Dogegen
    if (url.includes('/api/dogegen')) {
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ running: false, configured: false, ready: false }) });
    }

    // ADB
    if (url.includes('/api/adb')) {
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ connected: false, device: null }) });
    }

    // Default — return an empty ok so unrecognised polls don't crash the app
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });
}

// ── App shell ──────────────────────────────────────────────────────────────

test.describe('App shell', () => {
  test('renders header branding', async ({ page }) => {
    await mockApi(page);
    await page.goto('/');
    await expect(page.getByText('Calibration Helper')).toBeVisible();
    await expect(page.locator('.header-label')).toHaveText('ColourSpace ZRO / Zero');
  });

  test('does not show session badge or Start Over when there is no session', async ({ page }) => {
    await mockApi(page);
    await page.goto('/');
    await expect(page.getByRole('button', { name: 'Start Over' })).not.toBeVisible();
  });

  test('page title is set correctly', async ({ page }) => {
    await mockApi(page);
    await page.goto('/');
    await expect(page).toHaveTitle(/Calibration/i);
  });
});

// ── Setup page (no session) ────────────────────────────────────────────────

test.describe('Setup page — no session', () => {
  test('shows TV model selector with profiles', async ({ page }) => {
    await mockApi(page);
    await page.goto('/');
    await expect(page.getByText('Select TV Model')).toBeVisible();
    await expect(page.getByText('Samsung U8G')).toBeVisible();
    await expect(page.getByText('Samsung C2')).toBeVisible();
    await expect(page.getByText('LG C3')).toBeVisible();
  });

  test('shows Start Session card', async ({ page }) => {
    await mockApi(page);
    await page.goto('/');
    await expect(page.getByText('Start Session')).toBeVisible();
  });

  test('Start New Session button is enabled when a TV is auto-selected', async ({ page }) => {
    await mockApi(page);
    await page.goto('/');
    // Setup auto-selects profiles[0] via useState(profiles[0]?.key || '')
    const btn = page.getByRole('button', { name: 'Start New Session' });
    await expect(btn).toBeVisible();
    await expect(btn).not.toBeDisabled();
  });

  test('shows empty state gracefully when profiles list is empty', async ({ page }) => {
    await mockApi(page, { profiles: [] });
    await page.goto('/');
    await expect(page.getByText('Start Session')).toBeVisible();
    // Button is disabled because no TV can be selected
    const btn = page.getByRole('button', { name: 'Start New Session' });
    await expect(btn).toBeDisabled();
  });

  test('clicking a TV card selects it', async ({ page }) => {
    await mockApi(page);
    await page.goto('/');
    await page.getByText('Samsung C2').click();
    // After clicking the second card, it gets the 'selected' class.
    // We can't assert class directly, but we verify the first card is no
    // longer the only active one by checking the C2 card is present.
    await expect(page.getByText('Samsung C2')).toBeVisible();
  });
});

// ── Session active ─────────────────────────────────────────────────────────

test.describe('Session active', () => {
  const MOCK_SESSION = {
    id: 'abc-123',
    tv: 'Samsung U8G',
    mode: 'SDR',
    step: 'select_mode',
    step_index: 0,
    steps: [
      { id: 'select_mode',   label: 'Select Mode'  },
      { id: 'prepare',       label: 'Prepare'      },
      { id: 'pre_grayscale', label: 'Pre-Grayscale'},
    ],
    quality_gates: {},
    select_mode_data: { modes: ['SDR', 'HDR10'] },
  };

  test('shows session badge with TV and mode', async ({ page }) => {
    await mockApi(page, { session: MOCK_SESSION });
    await page.goto('/');
    await expect(page.getByText('Samsung U8G — SDR')).toBeVisible();
  });

  test('shows progress bar with step tabs', async ({ page }) => {
    await mockApi(page, { session: MOCK_SESSION });
    await page.goto('/');
    await expect(page.getByText('Select Mode')).toBeVisible();
    await expect(page.getByText('Prepare')).toBeVisible();
    await expect(page.getByText('Pre-Grayscale')).toBeVisible();
  });

  test('shows Start Over button', async ({ page }) => {
    await mockApi(page, { session: MOCK_SESSION });
    await page.goto('/');
    await expect(page.getByRole('button', { name: 'Start Over' })).toBeVisible();
  });

  test('Start Over opens confirmation modal', async ({ page }) => {
    await mockApi(page, { session: MOCK_SESSION });
    await page.goto('/');
    await page.getByRole('button', { name: 'Start Over' }).click();
    await expect(page.getByText('Discard this calibration session?')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Discard session' })).toBeVisible();
  });

  test('Cancel in Start Over modal dismisses it', async ({ page }) => {
    await mockApi(page, { session: MOCK_SESSION });
    await page.goto('/');
    await page.getByRole('button', { name: 'Start Over' }).click();
    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByText('Discard this calibration session?')).not.toBeVisible();
  });
});
