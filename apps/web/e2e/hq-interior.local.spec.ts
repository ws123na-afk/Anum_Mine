import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { mockHq } from './support/hqCampusApi';

// Local render check for the interior assets (not run in CI: no assertions on pixels, only screenshots for the owner review).
const OUT = process.env.INT_DIR ?? '/tmp/int-shots';
mkdirSync(OUT, { recursive: true });
const hq = (page: Page) => page.locator('.hqLive');
const BUILDING = process.env.INT_BUILDING ?? 'ANUM 360 Commerce';
const FLOORS = (process.env.INT_FLOORS ?? '0,1').split(',').map(Number);
const LIGHT = (process.env.INT_LIGHT ?? 'night') as 'day' | 'night';
const W = Number(process.env.INT_W ?? 1440), H = Number(process.env.INT_H ?? 900);

test(`interior ${BUILDING}`, async ({ page }) => {
  test.setTimeout(180_000);
  await page.setViewportSize({ width: W, height: H });
  await page.addInitScript((l) => { try { localStorage.setItem('anum.hq.light', l); } catch { /* */ } }, LIGHT);
  await mockHq(page, { campus: 'api', layout: 'campus', commerce: 'connected', meetings: 'live' });
  await page.goto('/#office-preview');
  await expect(page.locator('canvas.hqCanvas')).toHaveAttribute('data-engine', /three/, { timeout: 120_000 });
  await hq(page).getByRole('button', { name: new RegExp(`^${BUILDING}\\.`) }).first().click({ force: true });
  await hq(page).getByRole('button', { name: `Open ${BUILDING}` }).click();
  await expect(hq(page).getByRole('heading', { level: 2, name: BUILDING })).toBeVisible();
  if (process.env.INT_HIDE !== '0') { const hide = hq(page).getByRole('button', { name: /Hide side panels/ }); if (await hide.count()) await hide.click({ timeout: 4000 }).catch(() => undefined); }
  await page.waitForTimeout(2500);
  if (process.env.INT_FULL === '1') await hq(page).getByRole('button', { name: 'Full screen' }).click();
  await page.waitForTimeout(1500);
  if (process.env.INT_ALL === '1') { await hq(page).getByRole('checkbox', { name: /All floors/ }).check(); await page.waitForTimeout(2500); }
  const rail = hq(page).getByRole('navigation', { name: `Floors of ${BUILDING}` });
  const buttons = rail.getByRole('button');
  for (const f of FLOORS) {
    if (await buttons.count()) await buttons.nth(f).click();
    await page.waitForTimeout(4500);
    await page.locator('canvas.hqCanvas').screenshot({ path: `${OUT}/${BUILDING.replace(/\W+/g, '-')}-f${f}-${LIGHT}-${W}.png` });
    for (const room of (process.env.INT_ROOMS ?? '').split(',').filter(Boolean)) {
      await hq(page).getByRole('button', { name: new RegExp(`^${room}`) }).first().click({ force: true });
      await page.waitForTimeout(1500);
      const close = hq(page).getByRole('button', { name: 'Close', exact: true });
      if (await close.count()) await close.first().click().catch(() => undefined);
      for (let z = 0; z < Number(process.env.INT_ZOOM ?? 3); z++) { await hq(page).getByRole('button', { name: /Zoom in/ }).first().click(); await page.waitForTimeout(400); }
      await page.waitForTimeout(3000);
      await page.locator('canvas.hqCanvas').screenshot({ path: `${OUT}/room-${room.replace(/\W+/g, '-')}-${LIGHT}-${W}.png` });
      await hq(page).getByRole('button', { name: /Reset|Recentre|Home/ }).first().click().catch(() => undefined);
      await page.waitForTimeout(1500);
    }
  }
});
