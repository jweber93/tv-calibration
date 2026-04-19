import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const reportDir = path.resolve(__dirname, '../../../test-artifacts/report-layout');
const htmlPath = path.join(reportDir, 'sample-calibration-report.html');

test('sample calibration report layout stays stable', async ({ page }) => {
  const html = await readFile(htmlPath, 'utf8');

  await page.emulateMedia({ media: 'screen' });
  await page.setContent(html, { waitUntil: 'load' });
  await page.addStyleTag({
    content: `
      * {
        font-family: Arial, sans-serif !important;
      }
    `,
  });
  await page.evaluate(async () => {
    if (document.fonts) {
      await document.fonts.ready;
    }
  });

  await expect(page.locator('body')).toHaveScreenshot('sample-calibration-report.png');
});
