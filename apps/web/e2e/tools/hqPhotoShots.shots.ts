import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { mockHq } from '../support/hqCampusApi';

// Screenshot tool, not a CI spec (its name does not match the default test pattern): renders the campus and a building
// at 1440x900 for the before/after comparison in docs/ANUM_MINE_HQ_PHOTOREAL.md.
//   LOOK_DIR=<dir> LOOK_Q=4 pnpm exec playwright test -c playwright.hq.shots.config.ts
const OUT = process.env.LOOK_DIR ?? 'test-results/photoreal';
const Q = process.env.LOOK_Q;
const OFF = process.env.LOOK_OFF;
const SIZE = (process.env.LOOK_SIZE ?? '1440x900').split('x').map(Number);
const LIGHTS = (process.env.LOOK_LIGHTS ?? 'day,dusk,night').split(',');
const hq = (page: Page) => page.locator('.hqLive');
mkdirSync(OUT, { recursive: true });

async function open(page: Page, layout: 'full' | 'starter', light: string) {
  await page.addInitScript(([l, q, off]) => { try { localStorage.setItem('anum.hq.light', l === 'dusk' ? 'night' : l!); localStorage.setItem('anum.hq.preset', l === 'dusk' ? 'dusk' : 'night'); if (q) localStorage.setItem('anum.hq.quality', q); if (off) localStorage.setItem('anum.hq.fxoff', off); } catch { /* storage blocked */ } }, [light, Q, OFF]);
  await mockHq(page, { campus: 'api', layout, commerce: 'connected', meetings: 'live' });
  await page.goto('/#office-preview');
  await expect(hq(page).getByRole('heading', { level: 1 })).toBeVisible();
  await expect(page.locator('canvas.hqCanvas')).toHaveAttribute('data-engine', /three/, { timeout: 120_000 });
  await page.waitForTimeout(Number(process.env.LOOK_WAIT ?? 6000));
  await settle(page);
}

/** Tier 4 on software GL needs minutes for the first frame (shader compile): wait until the post stack reports it ran. */
async function settle(page: Page) {
  if (Q !== '4') return;
  await expect.poll(() => page.locator('canvas.hqCanvas').getAttribute('data-fx'), { timeout: 420_000, intervals: [3000] }).toContain('agx');
  await page.waitForTimeout(4000);
}
const shot = async (page: Page, name: string) => {
  await page.addStyleTag({ content: '.hqLive :is(.hqOverlay,.hqPanel,.hqLabels,.hqNotice,.hqNote,.hqStats,.hqSideInline,.hqMenuWrap,.hqGhost,.hqTotalsInline,.hqHead){visibility:hidden !important} .hqLive [role=status],.hqLive [role=alert],.hqLive [aria-live]{visibility:hidden !important}' });
  await page.locator('canvas.hqCanvas').screenshot({ path: `${OUT}/${name}.png` });
};

for (const light of LIGHTS) {
  test(`campus ${light}`, async ({ page }) => {
    test.setTimeout(900_000);
    await page.setViewportSize({ width: SIZE[0], height: SIZE[1] });
    await open(page, 'starter', light);
    await shot(page, `campus-${light}`);
    const stops = page.locator('.hqStop');
    const n = await stops.count();
    const i = Math.min(n - 1, Number(process.env.LOOK_STOP ?? 1));
    if (i >= 0) {
      await stops.nth(i).click({ force: true });
      await page.waitForTimeout(Q === '4' ? 20_000 : 5500);
      await shot(page, `close-${light}`);
    }
  });
  test(`building ${light}`, async ({ page }) => {
    test.setTimeout(900_000);
    await page.setViewportSize({ width: SIZE[0], height: SIZE[1] });
    await open(page, 'full', light);
    await hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click({ force: true });
    await hq(page).getByRole('button', { name: 'Open Al Noor Fleet HQ' }).click();
    await expect(hq(page).getByRole('heading', { level: 2, name: 'Al Noor Fleet HQ' })).toBeVisible();
    await page.waitForTimeout(Q === '4' ? 25_000 : 7000);
    await shot(page, `room-${light}`);
  });
}
