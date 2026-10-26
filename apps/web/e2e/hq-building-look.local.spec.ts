import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { mockHq } from './support/hqCampusApi';

const OUT = process.env.LOOK_DIR ?? '/tmp/claude-0/-home-user/61f032fb-c00f-5793-80f2-ca9f0fc13fd1/scratchpad/look-before';
const Q = process.env.LOOK_Q;
const hq = (page: Page) => page.locator('.hqLive');
mkdirSync(OUT, { recursive: true });

async function open(page: Page, layout: 'full' | 'starter', light: 'day' | 'night') {
  await page.addInitScript(([l, q]) => { try { localStorage.setItem('anum.hq.light', l!); if (q) localStorage.setItem('anum.hq.quality', q); } catch { /* */ } }, [light, Q]);
  await mockHq(page, { campus: 'api', layout, commerce: 'connected', meetings: 'live' });
  await page.goto('/#office-preview');
  await expect(hq(page).getByRole('heading', { level: 1 })).toBeVisible();
  await expect(page.locator('canvas.hqCanvas')).toHaveAttribute('data-engine', /three/, { timeout: 90_000 });
  await page.waitForTimeout(5000);
}
const canvasShot = async (page: Page, name: string) => { await page.addStyleTag({ content: '.hqStage > :not(canvas){visibility:hidden !important}' }); await page.locator('canvas.hqCanvas').screenshot({ path: `${OUT}/${name}.png` }); };

for (const light of ['day', 'night'] as const) {
  test(`campus ${light}`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page, 'starter', light);
    await canvasShot(page, `campus-${light}`);
    const stops = page.locator('.hqStop');
    const n = await stops.count();
    const want = (process.env.LOOK_STOPS ?? '1,3').split(',').map(Number).filter((i) => i < n);
    for (const i of want) {
      await stops.nth(i).click({ force: true });
      await page.waitForTimeout(4500);
      await canvasShot(page, `close${i}-${light}`);
    }
  });
  test(`building ${light}`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page, 'full', light);
    await hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click({ force: true });
    await hq(page).getByRole('button', { name: 'Open Al Noor Fleet HQ' }).click();
    await expect(hq(page).getByRole('heading', { level: 2, name: 'Al Noor Fleet HQ' })).toBeVisible();
    await page.waitForTimeout(6000);
    await canvasShot(page, `building-${light}`);
  });
}
