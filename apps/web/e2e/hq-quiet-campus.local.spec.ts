import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { campusFixture, mockHq } from './support/hqCampusApi';
import { mockFlow } from './support/hqFlowApi';
import { sceneReady } from './support/hqScene';
import { useSavedLocale } from './support/workspaceApi';

// Local render check for the quiet campus and the live boards (not run in CI: screenshots for the owner review, no pixel assertions).
// QUIET_DIR: where the screenshots go; QUIET_W / QUIET_H: viewport; QUIET_LANG: en | ar; QUIET_LIGHT: day | night; QUIET_BUILDING: a building to select and frame;
// QUIET_ZOOM: how many keyboard zoom steps after framing; QUIET_HIDE=1: hide the side panels (the owner's wide stage); QUIET_TAG: file name part.
const OUT = process.env.QUIET_DIR ?? '/tmp/quiet-shots';
mkdirSync(OUT, { recursive: true });
const hq = (page: Page) => page.locator('.hqLive');
const W = Number(process.env.QUIET_W ?? 1440), H = Number(process.env.QUIET_H ?? 900);
const LANG = (process.env.QUIET_LANG ?? 'en') as 'en' | 'ar';
const LIGHT = process.env.QUIET_LIGHT ?? 'day';
const zeroState = { working: 0, queued: 0, meeting: 0, waiting_approval: 0, blocked: 0, idle: 0, paused: 0, unknown: 0 };
const green = { ...campusFixture.buildings[3], workspace_id: 'workspace_green', name: 'Green Concrete', kind: null, profile_revision: null, agents: { total: 4, by_state: { ...zeroState, idle: 1, paused: 3 } } };
const campus14 = { ...campusFixture, buildings: [...campusFixture.buildings, green] };

test.use({ deviceScaleFactor: 1, isMobile: false, hasTouch: false });
test(`quiet campus ${LANG} ${LIGHT} ${W}x${H}`, async ({ page }) => {
  test.setTimeout(300_000);
  await page.setViewportSize({ width: W, height: H });
  await page.addInitScript((l) => { try { localStorage.setItem('anum.hq.light', l); } catch { /* storage blocked */ } }, LIGHT);
  await useSavedLocale(page, LANG);
  await mockHq(page, { campus: 'api', layout: 'starter', commerce: 'connected', meetings: 'live', presence: 'api', stream: 'live' });
  await page.route('http://localhost:8000/api/v1/office/campus', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(campus14) }));
  await mockFlow(page);
  await page.goto('/#office-preview');
  await sceneReady(page);
  await expect(hq(page).locator('.hqSceneWrap')).not.toHaveAttribute('data-board-version', '0', { timeout: 90_000 });
  if (process.env.QUIET_HIDE === '1') { await hq(page).getByRole('button', { name: /Hide side panels|إخفاء اللوحات الجانبية/ }).click({ timeout: 15000 }).catch(() => undefined); await page.waitForTimeout(1500); }
  const building = process.env.QUIET_BUILDING;
  if (building) {
    await hq(page).locator('.hqBuildingCard', { hasText: new RegExp(building) }).first().focus();
    await page.keyboard.press('Enter');
    await page.waitForTimeout(Number(process.env.QUIET_FLY ?? 2500));
    await hq(page).locator('.hqStage').focus();
    for (let i = 0; i < Number(process.env.QUIET_ZOOM ?? 0); i++) await page.keyboard.press('+');
  }
  await page.waitForTimeout(3500);
  if (process.env.QUIET_BARE === '1') {
    await page.addStyleTag({ content: '.hqStage > :not(canvas){visibility:hidden !important}' });
    await page.waitForTimeout(1500);
    await hq(page).locator('canvas.hqCanvas').screenshot({ path: `${OUT}/${process.env.QUIET_TAG ?? 'quiet'}-bare-${LANG}-${LIGHT}-${W}x${H}.png` });
    return;
  }
  await page.screenshot({ path: `${OUT}/${process.env.QUIET_TAG ?? 'quiet'}-${LANG}-${LIGHT}-${W}x${H}.png` });
  const lines = await hq(page).locator('.hqSceneWrap').getAttribute('data-board-texts');
  console.log((lines ?? '').split('\n').slice(0, 14).join('\n'));
});
