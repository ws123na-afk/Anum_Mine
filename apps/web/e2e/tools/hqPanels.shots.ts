// Local screenshot runner for the HQ page layout (not part of CI). Usage: see playwright.hq.shots.config.ts.
//   HQ_PORT=4401 PANELS_OUT=<dir> PANELS_PREFIX=before pnpm exec playwright test -c playwright.hq.shots.config.ts e2e/tools/hqPanels.shots.ts --project=desktop
import { expect, test, type Page } from '@playwright/test';
import { openHq } from '../support/hqApi';
import { mockFlow } from '../support/hqFlowApi';

const OUT = process.env.PANELS_OUT ?? '/tmp/panels';
const PREFIX = process.env.PANELS_PREFIX ?? 'shot';
const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
const hq = (page: Page) => page.locator('.hqLive');
test.use({ deviceScaleFactor: 1, isMobile: false, hasTouch: false });
test.describe.configure({ timeout: 300_000 });

for (const locale of ['en', 'ar'] as const) {
  for (const [w, h] of SIZES) {
    for (const level of ['campus', 'building'] as const) {
      test(`${PREFIX} ${locale} ${level} ${w}x${h}`, async ({ page }) => {
        await page.setViewportSize({ width: w, height: h });
        await openHq(page, { locale, stream: 'live', beforeGoto: async (p) => { await mockFlow(p); } });
        await expect(hq(page).locator('canvas.hqCanvas')).toBeVisible({ timeout: 60_000 });
        await expect(hq(page).locator('.hqBuildingCard').first()).toBeAttached({ timeout: 60_000 });
        if (level === 'building') {
          await hq(page).locator('.hqLabels').getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click();
          await hq(page).locator('.hqDetail button.hqPrimary').click();
          await expect(hq(page).locator('.hqNav[data-level="building"]')).toBeVisible({ timeout: 30_000 });
          await page.waitForTimeout(2500);
        }
        await page.waitForTimeout(3000);
        await page.screenshot({ path: `${OUT}/${PREFIX}-${locale}-${level}-${w}x${h}.png`, fullPage: true });
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        console.log(`${PREFIX} ${locale} ${level} ${w}x${h} overflowX=${overflow}`);
      });
    }
  }
}

for (const [w, h] of [[1440, 900], [390, 844]] as const) {
  test(`${PREFIX} en room ${w}x${h}`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await openHq(page, { stream: 'live', beforeGoto: async (p) => { await mockFlow(p); } });
    await expect(hq(page).locator('.hqBuildingCard').first()).toBeAttached({ timeout: 60_000 });
    await hq(page).locator('.hqLabels').getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click();
    await hq(page).locator('.hqDetail button.hqPrimary').click();
    await expect(hq(page).locator('.hqNav[data-level="building"]')).toBeVisible({ timeout: 30_000 });
    await hq(page).locator('.hqLabels').getByRole('button', { name: /Customer Care|Alpha Room/ }).first().click();
    await expect(hq(page).locator('.hqDetailDock')).toBeVisible({ timeout: 30_000 });
    await page.waitForTimeout(2500);
    await page.screenshot({ path: `${OUT}/${PREFIX}-en-room-${w}x${h}.png`, fullPage: true });
  });
}
