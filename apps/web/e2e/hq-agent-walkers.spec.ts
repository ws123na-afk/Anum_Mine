import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { mockHq } from './support/hqCampusApi';

// Agent walkers (docs/ANUM_MINE_HQ_AGENT_WALKERS.md). A figure of an agent appears only when a stored record says the agent moved between buildings:
// a live meeting with a room in another building that started or ended with the agent as participant, or a presence answer that puts the agent's
// desk in another building. No record, no figure. The API is mocked and every name is fake.
const OUT = process.env.WALKERS_DIR;
if (OUT) mkdirSync(OUT, { recursive: true });
const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
const SECRET = 'Q3 layoffs plan for the Tyre Pulse team';
const hq = (page: Page) => page.locator('.hqLive');
const canvas = (page: Page) => hq(page).locator('canvas.hqCanvas');
const layer = (page: Page) => page.getByTestId('hq-walkers');
const live = (page: Page) => page.getByTestId('hq-walkers-live');
const walkers = async (page: Page) => Number((await layer(page).getAttribute('data-walkers').catch(() => '0')) ?? '0');
const figures = async (page: Page) => Number((await canvas(page).getAttribute('data-walkers')) ?? '0');

type World = {
  meeting: null | { id: string; roomId: string; startedAt: string; agents: string[]; status: 'active' | 'finished' | 'paused'; endedAt: string | null };
  desks: Record<string, string>;
  states: Record<string, string>;
  failing: boolean;
};
const AGENTS: Record<string, { name: string; desk: string }> = {
  agent_fleet: { name: 'Fleet Planner', desk: 'desk_c1' },
  agent_leads: { name: 'Lead Finder', desk: 'desk_m1' },
  agent_care: { name: 'WhatsApp Concierge', desk: 'desk_s1' },
  agent_invoice: { name: 'Invoice Checker', desk: 'desk_t1' },
};
const newWorld = (): World => ({ meeting: null, desks: Object.fromEntries(Object.entries(AGENTS).map(([id, a]) => [id, a.desk])), states: { agent_fleet: 'working', agent_leads: 'idle', agent_care: 'idle', agent_invoice: 'idle' }, failing: false });

async function open(page: Page, world: World, o: { locale?: 'en' | 'ar'; reduced?: boolean } = {}) {
  await page.addInitScript(([locale]) => {
    try { localStorage.setItem('anum.hq.light', 'day'); localStorage.setItem('anum.hq.ambient', '0'); if (locale) localStorage.setItem('anum.locale', locale); } catch { /* storage blocked */ }
  }, [o.locale ?? null] as const);
  if (o.reduced) await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockHq(page, { campus: 'api', layout: 'campus', commerce: 'connected', meetings: 'none', tyre: 'connected' });
  await page.route('http://localhost:8000/**', async (route) => {
    const url = new URL(route.request().url());
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    const now = new Date().toISOString();
    if (url.pathname === '/api/v1/office/presence') {
      const items = Object.entries(AGENTS).map(([id, a]) => ({
        key: id, agent_id: id, agent_name: a.name, desk_id: world.desks[id], state: world.states[id] ?? 'idle',
        provenance: { kind: 'task', run_id: null, task_id: null, approval_id: null, meeting_id: null, reason: null }, task: { id: 't', title: SECRET }, last_verified_at: now, stale: false,
      }));
      return json({ version: 1, workspace_id: 'workspace_foundation', as_of: now, cursor: null, totals: {}, items });
    }
    if (url.pathname === '/api/v1/agent-meetings') {
      if (world.failing) return json({ error: { code: 'unavailable', message: 'Meetings are not reachable.', correlation_id: 'c', details: [] } }, 503);
      const m = world.meeting;
      return json({ items: m && m.status === 'active' ? [{ id: m.id, title: SECRET, status: 'active', revision: 1, room_id: m.roomId, started_at: m.startedAt }] : [], next_cursor: null });
    }
    const detail = /^\/api\/v1\/agent-meetings\/([^/]+)$/.exec(url.pathname);
    if (detail && world.meeting && detail[1] === world.meeting.id) {
      const m = world.meeting;
      return json({ id: m.id, title: SECRET, status: m.status, revision: 2, room_id: m.roomId, started_at: m.startedAt, ended_at: m.endedAt, participants: m.agents.map((a, i) => ({ id: `p${i}`, session_id: m.id, actor_id: a, kind: 'agent' })) });
    }
    return route.fallback();
  });
  await page.goto('/#office-preview');
  await expect(canvas(page)).toBeVisible({ timeout: 60_000 });
  await expect(canvas(page)).toHaveAttribute('data-engine', /three/, { timeout: 60_000 });
  await expect.poll(async () => Number(await canvas(page).getAttribute('data-triangles')), { timeout: 60_000 }).toBeGreaterThan(20_000);
  await expect(layer(page)).toBeAttached({ timeout: 90_000 });
}

const startMeeting = (world: World, agents = ['agent_care']) => {
  world.meeting = { id: 'meeting_walk', roomId: 'campus-01-hq-meeting-a', startedAt: new Date(Date.now() - 60_000).toISOString(), agents, status: 'active', endedAt: null };
  for (const a of agents) world.states[a] = 'meeting';
};
const hqCard = (page: Page) => hq(page).locator('button.hqBuildingCard').filter({ hasText: /^ANUM HQ/ });
async function shot(page: Page, name: string) { if (OUT) await hq(page).locator('.hqSceneWrap').screenshot({ path: `${OUT}/${name}.png` }); }

test.describe('record-backed walkers', () => {
  test('no record, no walker: a meeting that is already live when the page opens, an agent that is only working, a paused agent and a private one draw nobody', async ({ page }) => {
    test.setTimeout(180_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    const world = newWorld();
    startMeeting(world);                     // live before the first answer: the past is not replayed
    world.states.agent_fleet = 'working';    // working, with nothing that says where it went
    await open(page, world);
    await page.waitForTimeout(4000);
    expect(await walkers(page)).toBe(0);
    expect(await figures(page)).toBe(0);
    await expect(hq(page).locator('[data-walk], .hqWalkerPin, .hqWalkerChip')).toHaveCount(0);
  });

  test('a meeting that starts in another building walks its participant there, says why, shows no private text, and ends', async ({ page }) => {
    test.setTimeout(180_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    const world = newWorld();
    await open(page, world);
    await page.waitForTimeout(1500);
    expect(await walkers(page)).toBe(0);
    startMeeting(world);
    await hqCard(page).dispatchEvent('click');
    const line = hqCard(page).locator('.hqWalkLine[data-walk="meeting_started"]');
    await expect(line).toBeVisible({ timeout: 40_000 });
    await expect(line).toHaveText(/^Because: meeting started \d\d:\d\d in Meeting Room A$/);
    await expect(live(page)).toContainText('WhatsApp Concierge is on the way to ANUM HQ. Because: meeting started');
    await expect.poll(() => figures(page), { timeout: 15_000 }).toBe(1);
    expect(await walkers(page)).toBe(1);
    await expect(layer(page).locator('.hqWalkerChip')).toHaveText('1 on the move');
    const pin = layer(page).locator('.hqWalkerPin');
    await expect(pin).toHaveCount(1);
    await expect(pin).toHaveAttribute('aria-label', /^WhatsApp Concierge: Because: meeting started \d\d:\d\d in Meeting Room A$/);
    await pin.dispatchEvent('click');
    await expect(pin.locator('.hqWalkerTip')).toContainText('Because: meeting started');
    const body = await hq(page).innerText();
    expect(body).not.toContain(SECRET);
    expect((await hqCard(page).getAttribute('aria-label')) ?? '').not.toContain(SECRET);
    expect(await live(page).innerText()).not.toContain(SECRET);
    // Frames: it moves.
    await page.waitForTimeout(1500);
    await shot(page, 'start-a');
    // It moves: the target over the figure follows it from frame to frame (the stage reports the camera every frame).
    const at = () => pin.evaluate((el) => (el as HTMLElement).style.transform);
    const before = await at();
    await page.waitForTimeout(3000);
    await shot(page, 'start-b');
    expect(await at()).not.toBe(before);
    // Bounded: the figure is gone within 25 seconds of its start (plus the answer delay), its line a few seconds later.
    await expect.poll(() => figures(page), { timeout: 45_000 }).toBe(0);
    await expect(line).toHaveCount(0, { timeout: 20_000 });
    expect(await walkers(page)).toBe(0);
  });

  test('a meeting that is over sends the participant back; a paused one does not', async ({ page }) => {
    test.setTimeout(240_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    const world = newWorld();
    startMeeting(world);
    await open(page, world);
    await page.waitForTimeout(1500);
    world.meeting!.status = 'paused';        // gone from the live list, but not over
    await page.waitForTimeout(13_000);
    expect(await walkers(page)).toBe(0);
    world.meeting!.status = 'active';
    await page.waitForTimeout(12_000);       // live again: it was live before, so this is no new start
    expect(await walkers(page)).toBe(0);
    world.meeting!.status = 'finished';
    world.meeting!.endedAt = new Date().toISOString();
    world.states.agent_care = 'idle';
    const card = hq(page).locator('button.hqBuildingCard').filter({ hasText: /^ANUM 360 Commerce/ });
    await card.dispatchEvent('click');
    const line = card.locator('.hqWalkLine[data-walk="meeting_ended"]');
    await expect(line).toBeVisible({ timeout: 40_000 });
    await expect(line).toHaveText(/^Because: meeting in Meeting Room A ended \d\d:\d\d$/);
    await expect.poll(() => figures(page), { timeout: 15_000 }).toBe(1);
    await shot(page, 'ended');
  });

  test('a desk that moves to another building in the presence answer walks the agent there', async ({ page }) => {
    test.setTimeout(180_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    const world = newWorld();
    await open(page, world);
    await page.waitForTimeout(1500);
    world.desks.agent_invoice = 'desk_c2';   // from Tyre Pulse to ANUM HQ
    await hqCard(page).dispatchEvent('click');
    const line = hqCard(page).locator('.hqWalkLine[data-walk="desk_moved"]');
    await expect(line).toBeVisible({ timeout: 40_000 });
    await expect(line).toHaveText(/^Because: moved to ANUM HQ, verified \d\d:\d\d$/);
    await expect.poll(() => figures(page), { timeout: 15_000 }).toBe(1);
  });

  test('paused agents are never drawn', async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    const world = newWorld();
    world.states.agent_care = 'paused';      // paused from the first answer: the meeting names it, the presence says paused
    await open(page, world);
    await page.waitForTimeout(1500);
    startMeeting(world);
    world.states.agent_care = 'paused';
    await page.waitForTimeout(14_000);
    expect(await walkers(page)).toBe(0);
    expect(await figures(page)).toBe(0);
  });

  test('with the meeting feed unreadable nothing walks and the open building says Not connected', async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    const world = newWorld();
    world.failing = true;
    await open(page, world);
    await expect(layer(page)).toHaveAttribute('data-walkers-feed', 'off', { timeout: 30_000 });
    await expect(layer(page).locator('.hqWalkerChip')).toHaveText('Agents on the move: Not connected');
    await expect(hq(page).locator('.hqWalkLine[data-walkers-off]')).toHaveCount(1);
    await expect(live(page)).toContainText('Agents on the move: Not connected');
    expect(await figures(page)).toBe(0);
    expect(await walkers(page)).toBe(0);
  });

  test('reduced motion: no walking, a static marker at the destination with the same Because line', async ({ page }) => {
    test.setTimeout(180_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    const world = newWorld();
    await open(page, world, { reduced: true });
    await page.waitForTimeout(1500);
    startMeeting(world);
    await hqCard(page).dispatchEvent('click');
    const line = hqCard(page).locator('.hqWalkLine[data-walk="meeting_started"]');
    await expect(line).toBeVisible({ timeout: 40_000 });
    await expect(line).toHaveText(/^Because: meeting started \d\d:\d\d in Meeting Room A$/);
    const pin = layer(page).locator('.hqWalkerPin');
    await expect(pin).toHaveAttribute('data-animate', 'false');
    await expect(live(page)).toContainText('WhatsApp Concierge arrived at ANUM HQ');
    await page.waitForTimeout(8000);                                           // still there well past an animated walk's line (6 s)
    await expect(line).toBeVisible();
    await expect(pin).toHaveAttribute('data-animate', 'false');
    await shot(page, 'reduced-marker');
    // Nothing moves: the figure stands at the door, so its target does not move while the camera is still, and one figure is drawn.
    const at = await pin.evaluate((el) => (el as HTMLElement).style.transform);
    await page.waitForTimeout(3000);
    expect(await pin.evaluate((el) => (el as HTMLElement).style.transform)).toBe(at);
    expect(await figures(page)).toBe(1);
  });

  test('Arabic: right to left, the Because line and the live region are Arabic', async ({ page }) => {
    test.setTimeout(180_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    const world = newWorld();
    await open(page, world, { locale: 'ar' });
    await page.waitForTimeout(1500);
    startMeeting(world);
    const line = hq(page).locator('.hqWalkLine[data-walk="meeting_started"]');
    await expect(line).toContainText('السبب: بدأ اجتماع', { timeout: 40_000 });
    expect(await line.innerText()).not.toMatch(/\{\w+\}/);
    expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
    await expect(live(page)).toContainText('في الطريق إلى');
    await expect(layer(page).locator('.hqWalkerChip')).toContainText('في الطريق');
    await shot(page, 'arabic');
  });
});

test.describe('layout and accessibility', () => {
  for (const [w, h] of SIZES) {
    test(`${w}x${h}: no horizontal overflow, every building card stays visible and clickable, no serious accessibility violation`, async ({ page }) => {
      test.setTimeout(180_000);
      await page.setViewportSize({ width: w, height: h });
      const world = newWorld();
      await open(page, world);
      await page.waitForTimeout(1500);
      startMeeting(world);
      await expect(layer(page)).toHaveAttribute('data-walkers', '1', { timeout: 40_000 });
      await page.waitForTimeout(2500);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      const cards = hq(page).locator('button.hqBuildingCard');
      expect(await cards.count()).toBeGreaterThanOrEqual(4);
      for (const c of await cards.all()) {
        if (await c.isVisible()) { const box = await c.boundingBox(); expect(box && box.width > 8 && box.height > 8).toBeTruthy(); }
      }
      await hqCard(page).dispatchEvent('click');
      await expect(hqCard(page)).toHaveAttribute('aria-pressed', 'true');
      await shot(page, `view-${w}x${h}`);
      const results = await new AxeBuilder({ page }).include('.hqLive').analyze();
      expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
    });
  }
});
