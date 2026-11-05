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
// 21:00 in Dubai: only Amjad (13:00 to 22:00) is inside his hours.
const DUBAI_NIGHT = new Date(Date.UTC(2026, 9, 26, 17, 0, 0));
const hq = (page: Page) => page.locator('.hqLive');
// GC_MIRROR=1 draws the same table mirrored (the legs swap ends and their people look the other way), from the stored desk geometry.
const mirrored = <R extends { desks: Record<string, unknown>[] }>(registry: R): R => (process.env.GC_MIRROR !== '1' ? registry : {
  ...registry,
  desks: registry.desks.map((d) => (d.table_group === 'l-table' ? { ...d, pos_x: Math.round((16 - Number(d.pos_x)) * 100) / 100, facing: d.facing === 'east' ? 'west' : d.facing === 'west' ? 'east' : d.facing } : d)),
});

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
    registry: mirrored(greenRegistry()), locale: process.env.GC_LOCALE === 'ar' ? 'ar' : 'en',
    beforeGoto: async (p) => { if (SCENE.startsWith('night')) await p.addInitScript(() => { try { localStorage.setItem('anum.hq.light', 'night'); } catch { /* */ } }); await mockFlow(p); await mockProvisioning(p); await mockGreenWork(p, state);
      // Registered last, so it answers the desk list first: the same desks as a left L.
      if (process.env.GC_MIRROR === '1') await p.route(/http:\/\/localhost:8000\/api\/v1\/office-spaces\/desks(\?.*)?$/, (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: mirrored(greenRegistry()).desks, next_cursor: null }) }));
    },
  });
  await expect(page.locator('canvas.hqCanvas')).toHaveAttribute('data-engine', /three/, { timeout: 120_000 });
  await page.waitForTimeout(2000);
  const tag = `${SCENE}${process.env.GC_MIRROR === '1' ? '-left' : '-right'}`;
  await page.screenshot({ path: `${OUT}/${tag}-0-campus-${W}.png` });
  const card = hq(page).locator('.hqBuildingCard').first();
  await card.click({ force: true });
  await hq(page).getByRole('button', { name: /^(Open |فتح |افتح )/ }).first().click();
  await page.waitForTimeout(4000);
  await page.screenshot({ path: `${OUT}/${tag}-1-building-${W}.png` });
  await hq(page).locator('.hqRoomCard').first().click({ force: true });
  await page.waitForTimeout(1500);
  const inside = hq(page).getByRole('button', { name: /Go inside the selected room|الدخول/ }).first();
  if (process.env.GC_INSIDE === '1' && await inside.count()) await inside.click();
  await page.waitForTimeout(3000);
  await page.screenshot({ path: `${OUT}/${tag}-2-room-${W}.png` });
  if (process.env.GC_AGENT) { await hq(page).locator(`[data-testid="room-desk"]`).filter({ hasText: process.env.GC_AGENT }).first().click(); await page.waitForTimeout(1500); await page.screenshot({ path: `${OUT}/${tag}-2c-agent-${W}.png` }); }
  await page.locator('.hqSide .hqPanelHead .hqIconButton').first().click({ timeout: 4000 }).catch(() => undefined);
  const hide = hq(page).getByRole('button', { name: /Hide side panels|إخفاء/ });
  if (await hide.count()) await hide.click({ timeout: 4000 }).catch(() => undefined);
  for (let z = 0; z < Number(process.env.GC_ZOOM ?? 3); z++) { await hq(page).getByRole('button', { name: /Zoom in|تكبير/ }).first().click({ timeout: 4000 }).catch(() => undefined); await page.waitForTimeout(400); }
  await page.waitForTimeout(3500);
  await page.locator('canvas.hqCanvas').screenshot({ path: `${OUT}/${tag}-3-canvas-${W}.png` });
  await hq(page).getByRole('button', { name: /^(Top|من الأعلى)/ }).first().click({ timeout: 4000 }).catch(() => undefined);
  await page.waitForTimeout(3500);
  await page.locator('canvas.hqCanvas').screenshot({ path: `${OUT}/${tag}-4-top-${W}.png` });
  await page.screenshot({ path: `${OUT}/${tag}-3-full-${W}.png` });
});
