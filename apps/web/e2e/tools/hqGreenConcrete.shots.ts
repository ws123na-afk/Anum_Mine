import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { openHq } from '../support/hqApi';
import { mockFlow } from '../support/hqFlowApi';
import { mockProvisioning } from '../support/hqProvisioningApi';
import { greenRegistry, mockGreenWork, newState, person } from '../support/hqGreenConcreteApi';

// Local screenshot runner for the Green Concrete command center (not part of CI). Usage: see playwright.hq.shots.config.ts.
const OUT = process.env.GC_DIR ?? '/tmp/gc-shots';
mkdirSync(OUT, { recursive: true });
const W = Number(process.env.GC_W ?? 1440), H = Number(process.env.GC_H ?? 900);
const SCENE = process.env.GC_SCENE ?? 'day';
const DUBAI_NOON = new Date(Date.UTC(2026, 9, 26, 8, 0, 0));
const DUBAI_NIGHT = new Date(Date.UTC(2026, 9, 26, 19, 30, 0));
const hq = (page: Page) => page.locator('.hqLive');

test(`green concrete ${SCENE}`, async ({ page }) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: W, height: H });
  const at = SCENE.startsWith('night') ? DUBAI_NIGHT : DUBAI_NOON;
  await page.clock.setFixedTime(at);
  const state = newState(() => at.getTime());
  if (SCENE === 'work') {
    state.work.raed = { state: 'working', title: 'Check the tyre data feed' };
    state.work.hazrat = { state: 'queued', title: 'Review the weekly sensor sample' };
    state.work.shahzeb = { state: 'waiting_approval', title: 'Approve the tyre rotation plan' };
  }
  if (SCENE === 'offwork') state.work.amjad = { state: 'queued', title: 'Collect the depot readings' };
  await openHq(page, {
    registry: greenRegistry(), locale: process.env.GC_LOCALE === 'ar' ? 'ar' : 'en',
    beforeGoto: async (p) => { if (SCENE.startsWith('night')) await p.addInitScript(() => { try { localStorage.setItem('anum.hq.light', 'night'); } catch { /* */ } }); await mockFlow(p); await mockProvisioning(p); await mockGreenWork(p, state); },
  });
  await expect(page.locator('canvas.hqCanvas')).toHaveAttribute('data-engine', /three/, { timeout: 120_000 });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: `${OUT}/${SCENE}-0-campus-${W}.png` });
  const card = hq(page).locator('.hqBuildingCard').first();
  await card.click({ force: true });
  await hq(page).getByRole('button', { name: /^(Open |فتح |افتح )/ }).first().click();
  await page.waitForTimeout(4000);
  await page.screenshot({ path: `${OUT}/${SCENE}-1-building-${W}.png` });
  await hq(page).locator('.hqRoomCard').first().click({ force: true });
  await page.waitForTimeout(1500);
  const inside = hq(page).getByRole('button', { name: /Go inside the selected room|الدخول/ }).first();
  if (process.env.GC_INSIDE === '1' && await inside.count()) await inside.click();
  await page.waitForTimeout(3000);
  await page.screenshot({ path: `${OUT}/${SCENE}-2-room-${W}.png` });
  if (process.env.GC_TOGGLE === '1') { await hq(page).getByTestId('shift-toggle').click(); await page.waitForTimeout(2500); await page.screenshot({ path: `${OUT}/${SCENE}-2b-toggled-${W}.png` }); }
  if (process.env.GC_AGENT) { await hq(page).locator(`[data-testid="room-desk"]`).filter({ hasText: process.env.GC_AGENT }).first().click(); await page.waitForTimeout(1500); await page.screenshot({ path: `${OUT}/${SCENE}-2c-agent-${W}.png` }); }
  await page.locator('.hqSide .hqPanelHead .hqIconButton').first().click({ timeout: 4000 }).catch(() => undefined);
  const hide = hq(page).getByRole('button', { name: /Hide side panels|إخفاء/ });
  if (await hide.count()) await hide.click({ timeout: 4000 }).catch(() => undefined);
  for (let z = 0; z < Number(process.env.GC_ZOOM ?? 3); z++) { await hq(page).getByRole('button', { name: /Zoom in|تكبير/ }).first().click({ timeout: 4000 }).catch(() => undefined); await page.waitForTimeout(400); }
  await page.waitForTimeout(3500);
  await page.locator('canvas.hqCanvas').screenshot({ path: `${OUT}/${SCENE}-3-canvas-${W}.png` });
  await hq(page).getByRole('button', { name: /^(Top|من الأعلى)/ }).first().click({ timeout: 4000 }).catch(() => undefined);
  await page.waitForTimeout(3500);
  await page.locator('canvas.hqCanvas').screenshot({ path: `${OUT}/${SCENE}-4-top-${W}.png` });
  await page.screenshot({ path: `${OUT}/${SCENE}-3-full-${W}.png` });
});
