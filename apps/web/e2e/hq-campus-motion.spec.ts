import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { mockHq } from './support/hqCampusApi';
import { flowItems, mockFlow, type FlowMock } from './support/hqFlowApi';

// Campus motion (docs/ANUM_MINE_HQ_CAMPUS_MOTION.md). Layer 1 is scenery: loops and turning wheels that claim nothing. Layer 2 is work motion, started
// only by a stored record: a Tyre Pulse alert or inspection, a task that starts in an office, a rise in working agents. Every work motion says why
// ("Because: ...") and ends; with the feed off the building says "Not connected" and nothing moves. The API is mocked and every name is fake.
const OUT = process.env.MOTION_DIR;
if (OUT) mkdirSync(OUT, { recursive: true });
const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
const hq = (page: Page) => page.locator('.hqLive');
const canvas = (page: Page) => hq(page).locator('canvas.hqCanvas');
/** alert, inspection (the source stores a time) and inspection_day (dates only) answer once with the earlier state, then with the new record. standing: an alert that is already there in the first answer and never changes. */
type Tyre = 'alert' | 'standing' | 'inspection' | 'inspection_day' | 'quiet' | 'none' | 'denied';

/** Only for local frame captures: hold the Tyre Pulse answer so the camera can be placed first. */
const TYRE_DELAY_MS = Number(process.env.MOTION_TYRE_DELAY_MS ?? 0);
const SECRET = 'fake-sn-0103 on fake-plate-01';
const PRIVATE = 'Private note about a customer';

type TyreMock = { fire: () => Promise<void> };
async function tyreRoutes(page: Page, mode: Tyre): Promise<TyreMock> {
  const now = new Date();
  const today = now.toISOString().slice(0, 10);
  const yesterday = new Date(now.getTime() - 24 * 3_600_000).toISOString().slice(0, 10);
  // The first answer is the baseline; only what changed after it is an event (a later alert time, a later inspection).
  let fired = false;
  let servedAfterFire = 0;
  let served = 0;
  const alertAt = new Date(now.getTime() - 4 * 60_000).toISOString();
  const json = (route: import('@playwright/test').Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route(/http:\/\/localhost:8000\/api\/v1\/tyre\/(setup|alerts|dashboard)(\?.*)?$/, async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (TYRE_DELAY_MS && path.endsWith('/setup')) await new Promise((r) => setTimeout(r, TYRE_DELAY_MS));
    if (mode === 'denied') return json(route, { detail: 'You cannot read Tyre Pulse.' }, 403);
    if (path.endsWith('/setup')) {
      return json(route, { connection: mode === 'none' ? null : { kind: 'supabase_postgres', label: 'TyrePulse', status: 'ok' }, mapping: null, mapping_problems: [], assumptions: {}, discovery: null, sync: mode === 'none' ? null : { synced_at: now.toISOString() }, fields: {}, presets: [], can_manage: true, can_operate: true, delivery_channels: [] });
    }
    const alert = (fired && mode === 'alert') || mode === 'standing';
    if (path.endsWith('/alerts')) {
      const items = alert ? [{ id: 'alert_1', key: 'low_tread:t1', kind: 'low_tread', severity: 'critical', subject_type: 'tyre', subject_id: 't1', subject_label: SECRET, message: PRIVATE, value: {}, status: 'open', first_seen_at: alertAt, last_seen_at: now.toISOString(), resolved_at: null, acknowledged_by: null, acknowledged_at: null }] : [];
      return json(route, { items, total: items.length, limit: 50, offset: 0, counts: {} });
    }
    const timed = mode === 'inspection';
    const inspected = (mode === 'inspection' || mode === 'inspection_day') && fired;
    const operations = {
      as_of: today, inspections_total: 4,
      last_inspection: mode === 'inspection' || mode === 'inspection_day' ? (inspected ? today : yesterday) : null,
      last_inspection_at: timed ? (inspected ? new Date(now.getTime() - 2 * 60_000).toISOString() : `${yesterday}T09:00:00Z`) : null,
    };
    served += 1;
    if (fired) servedAfterFire += 1;
    return json(route, {
      summary: { as_of: today, currency: 'AED', vehicles: 6, tyres_fitted: 24, tyres_in_stock: 2, tyres_removed_last_30_days: 2, tyres_with_readings: 23, average_tread_mm: 10, below_minimum: 1, in_warning_band: 1, pressure_checked: 23, pressure_compliant_pct: 91, under_inflated: 1, over_inflated: 1, overdue_inspections: 1, due_next_30_days: 2, due_next_90_days: 2, cost_per_1000_km: null, cost_per_hour: null, min_tread_mm: 3 },
      operations, synced_at: now.toISOString(), alert_counts: {}, top_alerts: [], due_soon: [], due_by_month: {},
      latest_alert_at: alert ? alertAt : null,
    });
  });
  return {
    // The record lands at the source: wait for the baseline answer, then the page reads again (it does when the tab is shown again).
    fire: async () => {
      await expect.poll(() => served, { timeout: 60_000 }).toBeGreaterThan(0);
      fired = true;
      await expect.poll(async () => {
        if (servedAfterFire === 0) await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
        return servedAfterFire;
      }, { timeout: 30_000, intervals: [300, 500, 1000] }).toBeGreaterThan(0);
    },
  };
}

type OpenOptions = { tyre?: Tyre; ambient?: 0 | 1 | 2; locale?: 'en' | 'ar'; reduced?: boolean; flow?: boolean; night?: boolean };
async function open(page: Page, o: OpenOptions = {}): Promise<{ flow: FlowMock | null; tyre: TyreMock }> {
  await page.addInitScript(([ambient, locale, night]) => {
    try {
      localStorage.setItem('anum.hq.light', night ? 'night' : 'day');
      if (night) localStorage.setItem('anum.hq.preset', 'night');
      if (ambient !== null) localStorage.setItem('anum.hq.ambient', String(ambient));
      if (locale) localStorage.setItem('anum.locale', locale);
    } catch { /* storage blocked */ }
  }, [o.ambient ?? null, o.locale ?? null, o.night ?? false] as const);
  if (o.reduced) await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockHq(page, { campus: 'api', layout: 'starter-full', commerce: 'connected', meetings: 'live', tyre: 'connected' });
  const tyre = await tyreRoutes(page, o.tyre ?? 'quiet');
  const flow = o.flow ? await mockFlow(page, []) : null;
  await page.goto('/#office-preview');
  await expect(canvas(page)).toBeVisible();
  await expect(canvas(page)).toHaveAttribute('data-engine', /three/, { timeout: 60_000 });
  await expect.poll(async () => Number(await canvas(page).getAttribute('data-triangles')), { timeout: 60_000 }).toBeGreaterThan(40_000);
  if ((o.tyre === 'alert' || o.tyre === 'inspection' || o.tyre === 'inspection_day')) await tyre.fire();
  return { flow, tyre };
}

const card = (page: Page, name: RegExp | string) => hq(page).locator('button.hqBuildingCard').filter({ hasText: name });
const tyreCard = (page: Page) => hq(page).getByRole('button', { name: /^Tyre Pulse\./ });
/** Selecting a building frames it, so its card is placed in the open (a card behind another one is hidden, as for every building). */
const focusTyre = (page: Page) => tyreCard(page).dispatchEvent('click');
const live = (page: Page) => page.getByTestId('hq-work-live');
const rigs = async (page: Page) => Number((await canvas(page).getAttribute('data-work-rigs')) ?? '0');

async function shot(page: Page, name: string) {
  if (!OUT) return;
  await canvas(page).screenshot({ path: `${OUT}/${name}.png` });
}
const png = async (page: Page) => (await canvas(page).screenshot()).toString('base64');

test.describe('layer 1: ambient scenery', () => {
  test('vehicles loop on the roads and the scene moves between two frames when motion is allowed', async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page, { ambient: 1 });
    await expect.poll(async () => Number(await canvas(page).getAttribute('data-scenery-vehicles')), { timeout: 30_000 }).toBeGreaterThanOrEqual(16);
    const clock = Number(await canvas(page).getAttribute('data-scenery-clock'));
    const a = await png(page);
    await page.waitForTimeout(2500);
    expect(Number(await canvas(page).getAttribute('data-scenery-clock'))).toBeGreaterThan(clock + 1);
    expect(await png(page)).not.toBe(a);
    await shot(page, 'ambient-2500ms');
    // Nothing in the scenery claims work: there is no work motion and no Because line.
    await expect(hq(page).locator('[data-work]')).toHaveCount(0);
    expect(await rigs(page)).toBe(0);
  });

  test('reduced motion freezes the loops', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page, { ambient: 2, reduced: true });
    await page.waitForTimeout(1500);
    expect(await canvas(page).getAttribute('data-scenery-level')).toBe('0');
    const clock = await canvas(page).getAttribute('data-scenery-clock');
    await page.waitForTimeout(1500);
    expect(await canvas(page).getAttribute('data-scenery-clock')).toBe(clock);
  });

  test('headlights glow at night', async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page, { ambient: 1, night: true });
    await expect.poll(async () => Number(await canvas(page).getAttribute('data-scenery-vehicles')), { timeout: 30_000 }).toBeGreaterThanOrEqual(16);
    await page.waitForTimeout(1500);
    await shot(page, 'ambient-night');
  });
});

test.describe('layer 2: work motion from records', () => {
  test('a Tyre Pulse alert drives a tyre hauler into the Tyre Pulse yard, says why, shows no private text, and then rests', async ({ page }) => {
    test.setTimeout(180_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page, { tyre: 'alert' });
    await focusTyre(page);
    const line = tyreCard(page).locator('.hqWorkLine[data-work="tyre_alert"]');
    await expect(line).toBeVisible({ timeout: 30_000 });
    await expect(line).toHaveText(/^Because: Tyre Pulse alert raised \(low tread\), \d\d:\d\d$/);
    await expect(live(page)).toContainText('Tyre Pulse: Because: Tyre Pulse alert raised (low tread)');
    await expect(tyreCard(page)).toHaveAttribute('title', /One animation per event type, not a count\./);
    await expect.poll(() => rigs(page), { timeout: 15_000 }).toBe(1);
    const body = await hq(page).innerText();
    expect(body).not.toContain(SECRET);
    expect(body).not.toContain(PRIVATE);
    await expect(hq(page).locator('button.hqBuildingCard[data-work]')).toHaveCount(1); // only the Tyre Pulse building
    await page.waitForTimeout(1500);
    await shot(page, 'alert-approach');
    const moving = await png(page);
    await page.waitForTimeout(3000);
    expect(await png(page)).not.toBe(moving); // it is moving: two frames differ while nothing else on the scene does
    await page.waitForTimeout(2500);
    await shot(page, 'alert-parked');
    // Bounded: the vehicle leaves, then the marker goes, and the scene rests.
    await expect.poll(() => rigs(page), { timeout: 40_000 }).toBe(0);
    await expect(line).toHaveCount(0, { timeout: 20_000 });
    const rest = await png(page);
    await page.waitForTimeout(1500);
    expect(await png(page)).toBe(rest);
  });

  test('a Tyre Pulse inspection rolls a tyre out of the workshop onto the rack and back', async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page, { tyre: 'inspection' });
    await focusTyre(page);
    const line = tyreCard(page).locator('.hqWorkLine[data-work="tyre_inspection"]');
    await expect(line).toBeVisible({ timeout: 30_000 });
    // The source stores the time of the inspection, so the line says it.
    await expect(line).toHaveText(/^Because: Tyre Pulse inspection recorded, \d\d:\d\d$/);
    await expect.poll(() => rigs(page), { timeout: 15_000 }).toBe(1);
    await page.waitForTimeout(1200);
    await shot(page, 'inspection-rolling');
    await expect.poll(() => rigs(page), { timeout: 30_000 }).toBe(0);
  });

  test('a source that keeps inspection dates only shows the day, never a made-up time', async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page, { tyre: 'inspection_day' });
    await focusTyre(page);
    const line = tyreCard(page).locator('.hqWorkLine[data-work="tyre_inspection"]');
    await expect(line).toBeVisible({ timeout: 30_000 });
    await expect(line).toHaveText(/^Because: Tyre Pulse inspection recorded, [A-Z][a-z]{2} \d{1,2}$/);
    await expect.poll(() => rigs(page), { timeout: 15_000 }).toBe(1);
  });

  test('the first Tyre Pulse answer is a baseline: an alert already there is not replayed, and an unchanged answer is not an event', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const { tyre } = await open(page, { tyre: 'standing' });
    await tyre.fire(); // a second, unchanged answer: still nothing to show
    await page.waitForTimeout(3500);
    await expect(hq(page).locator('[data-work], [data-feed]')).toHaveCount(0);
    expect(await rigs(page)).toBe(0);
  });

  test('with the Tyre Pulse feed off the building says Not connected and nothing moves', async ({ page }) => {
    for (const mode of ['none', 'denied'] as const) {
      const p = await page.context().newPage();
      await p.setViewportSize({ width: 1440, height: 900 });
      await open(p, { tyre: mode });
      await expect(tyreCard(p).locator('.hqWorkLine[data-feed]')).toHaveText('Tyre Pulse: Not connected', { timeout: 30_000 });
      await p.waitForTimeout(2000);
      await expect(hq(p).locator('[data-work]')).toHaveCount(0);
      expect(await rigs(p)).toBe(0);
      await p.close();
    }
  });

  test('a connected feed with no record in the last day shows rest: no line, no motion', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page, { tyre: 'quiet' });
    await page.waitForTimeout(3500);
    await expect(hq(page).locator('[data-work], [data-feed]')).toHaveCount(0);
    expect(await rigs(page)).toBe(0);
  });

  test('a task that starts in an office sends a truck to that building only, with the reason on its card', async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    const { flow } = await open(page, { tyre: 'quiet', flow: true });
    await page.waitForTimeout(2500); // the first answer replays nothing
    await expect(hq(page).locator('[data-work]')).toHaveCount(0);
    const started = new Date().toISOString();
    const at = flowItems()[3];
    flow!.set([{ ...at, key: 'task:t_new', id: 't_new', title: 'Private task title', stage: 'in_office', since: started, location: { ...at.location!, building_id: 'campus-02-commerce' } } as never]);
    flow!.emit('task.updated');
    const started$ = hq(page).locator('button.hqBuildingCard[data-work="task_started"]');
    await expect(started$).toHaveCount(1, { timeout: 20_000 });
    await expect(started$).toHaveAttribute('aria-label', /^ANUM 360 Commerce\./);
    await started$.dispatchEvent('click');
    const line = started$.locator('.hqWorkLine');
    await expect(line).toBeVisible({ timeout: 20_000 });
    await expect(line).toHaveText(/^Because: a task run started, \d\d:\d\d$/);
    await expect(live(page)).toContainText('ANUM 360 Commerce: Because: a task run started');
    // The card and its tooltip carry no task title (the flow dock lists real titles, as before).
    expect(await started$.innerText()).not.toContain('Private task title');
    expect((await started$.getAttribute('aria-label')) ?? '').not.toContain('Private task title');
    await expect(hq(page).locator('button.hqBuildingCard[data-work]')).toHaveCount(1);
    await expect.poll(() => rigs(page), { timeout: 10_000 }).toBe(1);
    await page.waitForTimeout(2500);
    await shot(page, 'task-truck');
    await expect.poll(() => rigs(page), { timeout: 40_000 }).toBe(0);
  });

  test('reduced motion shows a static marker with the same Because line and draws nothing', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page, { tyre: 'alert', reduced: true });
    await focusTyre(page);
    const line = tyreCard(page).locator('.hqWorkLine[data-work="tyre_alert"]');
    await expect(line).toHaveText(/^Because: Tyre Pulse alert raised \(low tread\), \d\d:\d\d$/, { timeout: 30_000 });
    await page.waitForTimeout(2500);
    expect(await rigs(page)).toBe(0);
    await expect(line).toBeVisible();
  });

  test('Arabic: the line, the legend and the off note are Arabic and keep the time', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page, { tyre: 'alert', locale: 'ar' });
    const line = hq(page).locator('.hqWorkLine[data-work="tyre_alert"]');
    await expect(line).toContainText('السبب', { timeout: 30_000 });
    await expect(line).toContainText('نقص عمق الكفر');
    expect(await line.innerText()).not.toMatch(/\{\w+\}/);
    await expect(hq(page).locator('button.hqBuildingCard[data-work="tyre_alert"]')).toHaveAttribute('title', /حركة واحدة لكل نوع حدث/);
  });
});

test.describe('layout and accessibility', () => {
  for (const [w, h] of SIZES) {
    test(`${w}x${h}: no horizontal overflow, the line is readable, no serious accessibility violation`, async ({ page }) => {
      test.setTimeout(120_000);
      await page.setViewportSize({ width: w, height: h });
      await open(page, { tyre: 'alert' });
      const line = hq(page).locator('.hqWorkLine[data-work="tyre_alert"]').first();
      if (w >= 600) {
        await focusTyre(page);
        await expect(line).toBeVisible({ timeout: 30_000 });
      } else {
        // On a phone the stage is small and a selected building's panel covers it: the reason is in the card's name and in the live region.
        await expect(line).toBeAttached({ timeout: 30_000 });
        await expect(hq(page).locator('button.hqBuildingCard[data-work="tyre_alert"]')).toHaveAttribute('aria-label', /Because: Tyre Pulse alert raised \(low tread\)/);
      }
      await expect(live(page)).toContainText('Because: Tyre Pulse alert raised (low tread)');
      await page.waitForTimeout(1500);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      await shot(page, `view-${w}x${h}`);
      const results = await new AxeBuilder({ page }).include('.hqLive').analyze();
      expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
    });
  }
});
