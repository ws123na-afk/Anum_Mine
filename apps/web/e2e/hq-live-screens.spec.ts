import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { mockHq, type HqMockOptions } from './support/hqCampusApi';
import { flowItems, mockFlow, type FlowMock } from './support/hqFlowApi';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { mockProvisioning } from './support/hqProvisioningApi';
import { FX, greenRegistry, mockGreenWork, newState } from './support/hqGreenConcreteApi';
import { useSavedLocale } from './support/workspaceApi';
import { sceneReady } from './support/hqScene';

// The 3D screens in the rooms (docs/ANUM_MINE_HQ_LIVE_SCREENS.md): the Operations wall and the desk monitors show what is running, drawn on
// the meshes. The API is mocked at http://localhost:8000; every name and number is plainly fake. The page reports what the screens say in
// `data-screen-texts` on the stage wrapper (read only, for QA).
test.describe.configure({ timeout: 240_000 });
const SHOTS = process.env.HQ_SCREEN_SHOTS;
const hq = (page: Page) => page.locator('.hqLive');
const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
const noOverflow = async (page: Page) => expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
const shot = async (page: Page, name: string) => { if (SHOTS) { mkdirSync(SHOTS, { recursive: true }); await page.locator('canvas.hqCanvas').screenshot({ path: `${SHOTS}/${name}.png` }); } };
const texts = (page: Page) => page.locator('.hqSceneWrap').getAttribute('data-screen-texts').then((v) => v ?? '');

async function open(page: Page, options: HqMockOptions = {}, { reducedMotion = false }: { reducedMotion?: boolean } = {}): Promise<FlowMock> {
  await page.addInitScript(() => { try { localStorage.setItem('anum.hq.light', 'day'); } catch { /* storage blocked */ } });
  if (reducedMotion) await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockHq(page, { layout: 'campus', campus: 'missing', commerce: 'connected', tyre: 'connected', meetings: 'none', ...options });
  const flow = await mockFlow(page);
  await page.goto('/#office-preview');
  await expect(hq(page).getByRole('heading', { level: 1 })).toBeVisible();
  return flow;
}

async function enter(page: Page, name: string) {
  await hq(page).getByRole('button', { name: 'List', exact: true }).click();
  await hq(page).locator('.hqListBuilding', { has: page.getByRole('heading', { level: 3, name, exact: true }) }).locator('.hqListRow > button').click();
  await expect(hq(page).getByRole('heading', { level: 2, name })).toBeVisible();
  await sceneReady(page);
}

/** One line of what is drawn on the wall (`wall:<cell>`), or '' while there is none. */
const wallLine = async (page: Page) => (await texts(page)).split('\n').find((l) => l.startsWith('wall:')) ?? '';
const monitorLines = async (page: Page) => (await texts(page)).split('\n').filter((l) => l.startsWith('monitor:'));

/** QA captures only: the canvas with no page chrome over it. */
async function bareStage(page: Page) {
  await page.addStyleTag({ content: '.hqLabels, .hqSceneWrap > :not(.hqStage), .hqPanel, .hqLive aside, .hqBottomPanels, .hqFlowDock { visibility: hidden !important; }' });
}

/** Picks a room (once), hides the page chrome for the capture, then moves the stage camera with the wheel (dolly) and the keys (q and e turn it). */
async function closeUp(page: Page, room: string | null, wheel: number, keys: string, shotName: string, inside = false) {
  if (room) {
    const hide = hq(page).getByRole('button', { name: /Hide side panels/ });
    if (await hide.count()) await hide.click({ timeout: 4000 }).catch(() => undefined);
    await hq(page).getByRole('button', { name: new RegExp(`^${room}`) }).first().click({ force: true });
    await page.waitForTimeout(1200);
    const full = hq(page).getByRole('button', { name: 'Full screen' });
    if (await full.count()) await full.click().catch(() => undefined);
    if (inside) await hq(page).getByRole('button', { name: 'Go inside the selected room', exact: true }).first().click({ timeout: 8000 }).catch(() => undefined);
    await page.waitForTimeout(1500);
    if (SHOTS) await bareStage(page);
  }
  const box = (await page.locator('canvas.hqCanvas').boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  for (let i = 0; i < wheel; i += 1) { await page.mouse.wheel(0, wheel > 0 ? -250 : 250); await page.waitForTimeout(150); }
  await page.locator('.hqStage').focus();
  for (const k of keys) { await page.keyboard.press(k); await page.waitForTimeout(250); }
  await page.waitForTimeout(3500);
  if (shotName) await shot(page, shotName);
}

test('the Tyre Pulse wall shows the real numbers, the flow and the tyre figures, and follows an update', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const flow = await open(page);
  await enter(page, 'Tyre Pulse');
  await expect.poll(() => wallLine(page), { timeout: 90_000 }).toContain('Open tasks 3, Blocked work 1, Pending approvals 1, Running now 1');
  await expect.poll(() => wallLine(page), { timeout: 60_000 }).toContain('Vehicles 42, Tyres fitted 250, Tyres in stock 30, Below minimum 3, Overdue inspections 6, Due in 30 days 11');
  const line = await wallLine(page);
  expect(line).toMatch(/Task · In an office · \d+m/);
  expect(line).toMatch(/Updated /);
  expect(line).not.toContain('Plan October tyre rotation');
  expect(line).not.toContain('Invoice Checker');
  if (SHOTS) {
    await shot(page, 'tyre-overview');
    await closeUp(page, 'Fleet', 0, '', 'tyre-inside-a', true);
    await closeUp(page, null, 0, 'eeeeee', 'tyre-inside-b');
    await closeUp(page, null, 0, 'qqqqqqqqqqqq', 'tyre-inside-c');
  }
  // An update from the server changes the drawn wall without a rebuild: one open task fewer.
  const before = await wallLine(page);
  flow.set(flowItems().filter((i) => i.key !== 'task:t_plan'));
  flow.emit('task.updated');
  await expect.poll(() => wallLine(page), { timeout: 60_000 }).not.toBe(before);
  expect(await wallLine(page)).toContain('Open tasks 2');
});

test('a Tyre Pulse building without the tyre feed says so on its wall and shows no tyre number', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  await open(page, { tyre: 'none' });
  await enter(page, 'Tyre Pulse');
  await expect.poll(() => wallLine(page), { timeout: 90_000 }).toContain('Tyre Pulse: Not connected');
  expect(await wallLine(page)).not.toContain('Vehicles');
});

test('the wall is Arabic and right to left in the Arabic page', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page);
  await hq(page).getByRole('button', { name: 'قائمة', exact: true }).click();
  await hq(page).locator('.hqListBuilding', { has: page.getByRole('heading', { level: 3, name: 'Tyre Pulse', exact: true }) }).locator('.hqListRow > button').click();
  await sceneReady(page);
  await expect.poll(() => wallLine(page), { timeout: 90_000 }).toContain('مهام مفتوحة');
  expect(await wallLine(page)).toContain('مهام مفتوحة');
});

test.describe('desk monitors', () => {
  const registry = hqRegistry(TENANT_A, { rooms: 4, desks: 4, agentIds: ['agent_fleet', 'agent_leads', 'agent_care', 'agent_invoice'] });
  async function openAlNoor(page: Page, options: Parameters<typeof openHq>[1] = {}) {
    await openHq(page, { registry, presence: 'api', ...options, beforeGoto: async (p) => { await mockFlow(p); } });
    await hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click();
    await hq(page).getByRole('button', { name: 'Open Al Noor Fleet HQ' }).click();
    await expect(hq(page).getByRole('heading', { level: 2, name: 'Al Noor Fleet HQ' })).toBeVisible();
    await sceneReady(page);
  }

  test('a working agent shows running with the real elapsed time, the others are dim', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openAlNoor(page);
    await expect.poll(async () => (await monitorLines(page)).length, { timeout: 90_000 }).toBeGreaterThan(0);
    const lines = (await monitorLines(page)).join('\n');
    expect(lines).toMatch(/Working \/ Task run \/ Running \d+m \/ Verified .* \/ scan/);
    expect(lines).toMatch(/Queued \/ Task queued/);
    expect(lines).not.toMatch(/Plan October tyre rotation|Fleet Planner|Invoice Checker|Send fleet quote/);
    // Only a working or meeting agent scans; every other monitor is a still face.
    for (const l of (await monitorLines(page))) if (!/^monitor:\d+\|(near|far)\|(Working|In meetings) /.test(l)) expect(l).not.toContain('scan');
  });

  test('reduced motion keeps the content (the scan line stands still)', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openAlNoor(page, { reducedMotion: true });
    await expect.poll(async () => (await monitorLines(page)).join('\n'), { timeout: 90_000 }).toMatch(/Working \/ Task run \/ Running/);
  });

});

test.describe('the Live mark', () => {
  test('shows only while the presence stream is connected', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page, { stream: 'unsupported', presence: 'api' });
    await enter(page, 'Tyre Pulse');
    await expect.poll(() => wallLine(page).then((l) => l.includes('Updated ')), { timeout: 90_000 }).toBe(true);
    expect(await wallLine(page)).not.toMatch(/ Live$/);
  });

  test('shows when the stream is live', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page, { stream: 'live', presence: 'api' });
    await enter(page, 'Tyre Pulse');
    await expect.poll(() => wallLine(page), { timeout: 90_000 }).toMatch(/ Live$/);
  });
});

test.describe('Green Concrete command center', () => {
  async function openGreen(page: Page, work: Record<string, { state: 'working' | 'idle'; title?: string }>) {
    const at = new Date(Date.UTC(2026, 9, 26, 8, 0, 0));
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.clock.setFixedTime(at);
    const state = newState(() => at.getTime());
    state.work = work;
    await openHq(page, { registry: greenRegistry(), beforeGoto: async (p) => { await mockFlow(p); await mockProvisioning(p); await mockGreenWork(p, state); } });
    await hq(page).getByRole('button', { name: 'List', exact: true }).click();
    await hq(page).locator('.hqListBuilding', { has: page.getByRole('heading', { level: 3, name: String(FX.building.name), exact: true }) }).locator('.hqListRow > button').click();
    await sceneReady(page);
  }

  test('the day shift monitor of a working person runs, an idle one is still, and no title or name is drawn', async ({ page }) => {
    await openGreen(page, { raed: { state: 'working', title: 'ZZSECRET plan' } });
    await expect.poll(async () => (await monitorLines(page)).join('\n'), { timeout: 90_000 }).toMatch(/Working \/ Task run/);
    const lines = (await monitorLines(page)).join('\n');
    expect(lines).toMatch(/Idle/);
    expect(lines).not.toMatch(/ZZSECRET|Raed|Hazrat|Shahzeb/);
    // The command center's video wall carries the same real numbers (a room of kind command always has one).
    await expect.poll(() => wallLine(page), { timeout: 60_000 }).toContain('Open tasks');
    if (SHOTS) await shot(page, 'green-room');
  });

  test('the night shift people, off shift now, show a dark Off shift monitor', async ({ page }) => {
    await openGreen(page, {});
    await hq(page).getByRole('button', { name: new RegExp(`^${String(FX.room.name)}`) }).first().click({ force: true });
    await hq(page).getByTestId('shift-toggle').click();
    await expect.poll(async () => (await monitorLines(page)).join('\n'), { timeout: 90_000 }).toMatch(/Off shift/);
    expect((await monitorLines(page)).join('\n')).not.toMatch(/scan/);
  });
});

test.describe('layout', () => {
  for (const [w, h] of SIZES) {
    test(`the screens do not disturb the page at ${w}x${h}`, async ({ page }) => {
      await page.setViewportSize({ width: w, height: h });
      await open(page);
      await enter(page, 'Tyre Pulse');
      await expect.poll(() => wallLine(page), { timeout: 90_000 }).toContain('Open tasks');
      await noOverflow(page);
      const box = (await page.locator('canvas.hqCanvas').boundingBox())!;
      expect(box.width).toBeGreaterThan(200);
      expect(box.height).toBeGreaterThan(120);
      await shot(page, `tyre-${w}x${h}`);
      const results = await new AxeBuilder({ page }).include('.hqHeadTools').analyze();
      expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([]);
    });
  }
});
