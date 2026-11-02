import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { mockHq } from './support/hqCampusApi';

// Local render check (not in CI): every building should stand on the ground (docs/ANUM_MINE_HQ_BUILDING_LOOK.md#standing-on-the-ground).
// Screenshots of the campus and of three building kinds, Closed and Cutaway, at the five sizes of the responsive gate.
// GROUND_DIR (output folder), GROUND_SIZES (for example 1440x900,390x844), GROUND_BUILDINGS (comma separated names).
const OUT = process.env.GROUND_DIR ?? '/tmp/grounded-shots';
mkdirSync(OUT, { recursive: true });
const SIZES = (process.env.GROUND_SIZES ?? '1440x900,1366x768,1024x768,768x1024,390x844').split(',').map((s) => s.split('x').map(Number) as [number, number]);
const BUILDINGS = (process.env.GROUND_BUILDINGS ?? 'Knowledge & AI,Tyre Pulse,Personal Office').split(',');
const hq = (page: Page) => page.locator('.hqLive');
const tag = (s: string) => s.replace(/\W+/g, '-') + (process.env.GROUND_TOP === '1' ? '-top' : '') + (process.env.GROUND_FULL === '1' ? '-full' : '');

async function open(page: Page, w: number, h: number) {
  await page.setViewportSize({ width: w, height: h });
  await page.addInitScript(() => { try { localStorage.setItem('anum.hq.light', 'day'); } catch { /* */ } });
  await mockHq(page, { campus: 'api', layout: 'starter-full', commerce: 'connected', meetings: 'live' });
  await page.goto('/#office-preview');
  await expect(page.locator('canvas.hqCanvas')).toHaveAttribute('data-engine', /three/, { timeout: 120_000 });
}

for (const [w, h] of SIZES) {
  test(`campus ${w}x${h}`, async ({ page }) => {
    test.setTimeout(180_000);
    await open(page, w, h);
    await page.waitForTimeout(5000);
    await page.screenshot({ path: `${OUT}/campus-${w}x${h}.png` });
  });
  for (const name of BUILDINGS) {
    test(`${name} ${w}x${h}`, async ({ page }) => {
      test.setTimeout(240_000);
      await open(page, w, h);
      // Entered from the list, whose Open button is reachable at every size.
      await hq(page).getByRole('button', { name: /^List$/ }).click();
      await hq(page).locator('.hqListBuilding', { has: page.getByRole('heading', { level: 3, name, exact: true }) }).locator('.hqListRow > button').first().click();
      await expect(hq(page).locator('.hqSceneWrap')).toBeVisible();
      const hide = hq(page).getByRole('button', { name: /Hide side panels/ });
      if (await hide.count()) await hide.first().click({ timeout: 4000 }).catch(() => undefined);
      // GROUND_TOP=1 opens the top floor, like the owner's screenshot (Floor 2 of 3 in the cutaway).
      if (process.env.GROUND_TOP === '1') {
        const floors = hq(page).locator('.hqFloorButtons button');
        if (await floors.count()) await floors.last().click();
      }
      // GROUND_FULL=1 takes the stage to full screen, closer to the owner's screenshot.
      if (process.env.GROUND_FULL === '1') await hq(page).getByRole('button', { name: /Full screen/ }).first().click();
      await page.waitForTimeout(5000);
      await hq(page).locator('.hqSceneWrap').scrollIntoViewIfNeeded();
      await page.screenshot({ path: `${OUT}/${tag(name)}-${w}x${h}-cutaway.png` });
      await hq(page).getByTestId('hq-facade-closed').click();
      await page.waitForTimeout(2500);
      await page.screenshot({ path: `${OUT}/${tag(name)}-${w}x${h}-closed.png` });
    });
  }
}
