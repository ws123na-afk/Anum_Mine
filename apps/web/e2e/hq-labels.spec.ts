import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { mockFlow, flowItems } from './support/hqFlowApi';

// The label layer and scene chrome of a building (docs/ANUM_MINE_HQ_LABELS.md): room cards with a subtitle from the registry,
// agent cards only for real seated agents with their real state, density, the stacked view's depth test, the floor selector,
// the Back to campus card and the strips with their "Not connected" states. The API is mocked; every name is plainly fake.
test.use({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, isMobile: false, hasTouch: false });
test.describe.configure({ timeout: 240_000 });

const hq = (page: Page) => page.locator('.hqLive');
const stamp = '2026-10-01T08:00:00Z';
const rec = (id: string, extra: Record<string, unknown>) => ({ id, revision: 1, tenant_id: TENANT_A.tenantId, workspace_id: TENANT_A.workspaceId, created_at: stamp, updated_at: stamp, ...extra });

/** Three floors: two offices and a reception on the ground floor, a meeting room above, an office on top. */
function threeFloors(seated: readonly string[] = ['agent_fleet', 'agent_leads', 'agent_care']) {
  const base = hqRegistry(TENANT_A, { rooms: 0, desks: 0 });
  const bid = base.buildings[0].id;
  const floors = [0, 1, 2].map((level) => rec(`Alpha-floor-${level}`, { building_id: bid, level, name: ['Ground floor: Front office', 'Floor 1: Meetings', 'Floor 2: Studio'][level] }));
  const room = (id: string, floor: number, name: string, kind: string, x: number, z: number, dept: string | null) =>
    rec(id, { floor_id: floors[floor].id, name, kind, capacity: 6, scene_x: x, scene_z: z, scene_width: 8, scene_depth: 6, ...(dept ? { team_department: dept } : {}) });
  const rooms = [
    room('Alpha-finance', 0, 'Alpha Finance', 'office', 0, 0, 'finance'), room('Alpha-sales', 0, 'Alpha Sales', 'office', 10, 0, 'sales'), room('Alpha-lobby', 0, 'Alpha Lobby', 'reception', 20, 0, null),
    room('Alpha-board', 1, 'Alpha Boardroom', 'meeting', 0, 0, null), room('Alpha-studio', 2, 'Alpha Studio', 'office', 0, 0, 'marketing'),
  ];
  const desks = seated.map((agent, i) => rec(`Alpha-desk-${i}`, { room_id: rooms[i % 2].id, label: `Alpha Desk ${i + 1}`, agent_id: agent }));
  desks.push(rec('Alpha-desk-empty', { room_id: rooms[0].id, label: 'Alpha Empty Desk', agent_id: null }));
  return { buildings: base.buildings, floors, rooms, desks };
}

async function open(page: Page, options: Parameters<typeof openHq>[1] & { flowFail?: number | null; flowSources?: Record<string, string>; stacked?: boolean } = {}) {
  const mock = await openHq(page, {
    registry: threeFloors() as unknown as ReturnType<typeof hqRegistry>, ...options,
    beforeGoto: async (p) => { const flow = await mockFlow(p); if (options.flowSources) flow.set(flowItems(), options.flowSources); if (options.flowFail !== undefined) flow.failWith(options.flowFail); },
  });
  await expect(hq(page).getByRole('heading', { level: 1 })).toBeVisible();
  await expect(hq(page).locator('canvas.hqCanvas')).toBeVisible();
  await hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).dispatchEvent('click', undefined, { timeout: 60_000 });
  await hq(page).getByRole('button', { name: /^(Open|فتح) \u2068?Al Noor Fleet HQ\u2069?$/ }).click();
  await expect(hq(page).getByRole('heading', { level: 2, name: 'Al Noor Fleet HQ' })).toBeVisible();
  // The section view opens with every floor (docs/ANUM_MINE_HQ_CUTAWAY.md); these tests read one floor unless they ask for the stack.
  if (options.stacked !== true) await hq(page).locator('.hqCheck input[type="checkbox"]').first().uncheck();
  return mock;
}
const roomCards = (page: Page) => hq(page).locator('.hqBuilding .hqRoomCard');
const visibleAnchors = (page: Page) => hq(page).locator('.hqLabelAnchor:not([style*="display: none"])');

test('every room of the open floor has a card with its name and a subtitle from the registry, and a press opens the room panel', async ({ page }) => {
  await open(page);
  await expect(roomCards(page)).toHaveCount(3);
  const finance = roomCards(page).filter({ hasText: 'Alpha Finance' });
  await expect(finance).toHaveAccessibleName(/^Alpha Finance\. Office · \d+ desks? · Finance(\. \d+ working)?\. Opens the room panel\.$/);
  await expect(roomCards(page).filter({ hasText: 'Alpha Lobby' })).toHaveAccessibleName(/^Alpha Lobby\. Reception\./);
  // Only the rooms of this floor: the floors above are cut away and have no cards.
  await expect(hq(page).locator('.hqRoomCard', { hasText: 'Alpha Boardroom' })).toHaveCount(0);
  await finance.focus();
  await page.keyboard.press('Enter');
  await expect(hq(page).getByRole('complementary', { name: 'Room Alpha Finance' })).toBeVisible();
  await expect(finance).toHaveAttribute('aria-pressed', 'true');
});

test('agent cards exist only for seated agents, carry the presence state, and a desk with nobody has none', async ({ page }) => {
  await open(page);
  const cards = hq(page).locator('.hqBuilding .hqAgentCard');
  await expect(cards).toHaveCount(3);
  await expect(cards.filter({ hasText: 'Fleet Planner' })).toContainText('Working');
  await expect(cards.filter({ hasText: 'WhatsApp Concierge' })).toContainText('In meetings');
  await expect(cards.filter({ hasText: 'Lead Finder' })).toContainText('Idle');
  await expect(cards.filter({ hasText: 'Fleet Planner' })).toHaveAccessibleName(/^Fleet Planner, .+, Working\. Opens the agent panel\.$/);
  await expect(hq(page).locator('.hqAgentCard', { hasText: 'Alpha Empty Desk' })).toHaveCount(0);
});

test('no agents seated: no agent cards, and the strips say so without a made-up number', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await open(page, { registry: threeFloors([]) as unknown as ReturnType<typeof hqRegistry> });
  await expect(roomCards(page)).toHaveCount(3);
  await expect(hq(page).locator('.hqBuilding .hqAgentCard')).toHaveCount(0);
});

test('the density rule: cards never overlap, and a crowded view uses the smaller densities', async ({ page }) => {
  await open(page);
  // A scene narrower than 641 px squeezes cards onto their anchors (a phone); the no-overlap rule is for a scene wide enough to follow it.
  await hq(page).getByRole('button', { name: /Hide side panels/ }).click();
  await page.waitForTimeout(800);
  await page.waitForTimeout(1500);
  const result = await page.evaluate(() => {
    const out: { id: string; d: string | undefined; r: { x0: number; x1: number; y0: number; y1: number } }[] = [];
    document.querySelectorAll<HTMLElement>('.hqLive .hqLabelAnchor').forEach((el, i) => {
      if (el.style.display === 'none' || el.dataset.hidden === 'crowded') return;
      const b = (el.firstElementChild as HTMLElement).getBoundingClientRect();
      out.push({ id: String(i), d: el.dataset.density, r: { x0: b.left, x1: b.right, y0: b.top, y1: b.bottom } });
    });
    return out;
  });
  expect(result.length).toBeGreaterThan(0);
  for (const a of result) expect(['full', 'compact', 'dot']).toContain(a.d);
  for (let i = 0; i < result.length; i++) for (let j = i + 1; j < result.length; j++) {
    const a = result[i].r, b = result[j].r;
    const overlap = a.x0 < b.x1 - 3 && a.x1 > b.x0 + 3 && a.y0 < b.y1 - 3 && a.y1 > b.y0 + 3;
    expect(overlap, `cards ${i} and ${j} overlap`).toBe(false);
  }
});

test('single floor: only that floor is labelled; the stacked view tags every floor and keeps the open floor labelled', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await open(page);
  await hq(page).getByRole('button', { name: /Hide side panels/ }).click();
  const selector = hq(page).getByRole('group', { name: 'Floors of this building' });
  await expect(selector.getByRole('button')).toHaveCount(4); // three floors and the stack switch
  await expect(selector.getByRole('button', { name: /^Ground floor: Front office$/ })).toHaveAttribute('aria-pressed', 'true');
  await selector.getByRole('button', { name: /^Floor 1: Meetings$/ }).click();
  await expect(hq(page).locator('.hqRoomCard', { hasText: 'Alpha Boardroom' })).toHaveCount(1);
  await expect(roomCards(page)).toHaveCount(1);
  await selector.getByRole('button', { name: 'Stack the floors' }).click();
  // The stack tags every floor; the room cards stay on the open floor (the other floors are reached by their tag).
  await expect(hq(page).locator('.hqFloorTag')).toHaveCount(3);
  await expect(roomCards(page)).toHaveCount(1);
  await page.waitForTimeout(2000);
  await expect(hq(page).locator('.hqFloorTag[data-selected="true"]')).toBeVisible();
  await expect(hq(page).locator('.hqRoomCard', { hasText: 'Alpha Boardroom' })).toBeVisible();
  await hq(page).locator('.hqSegment').getByRole('button', { name: '3D' }).click();
  await page.waitForTimeout(1500);
  await hq(page).locator('.hqFloorTag', { hasText: 'Floor 2' }).evaluate((el) => (el as HTMLElement).click());
  await expect(selector.getByRole('button', { name: /^Floor 2: Studio$/ })).toHaveAttribute('aria-pressed', 'true');
});

test('the Back to campus card names the building with a status dot; its real facts are docked under the scene', async ({ page }) => {
  await open(page);
  const card = hq(page).locator('.hqBackCard');
  await expect(card).toBeVisible({ timeout: 30_000 });
  await expect(card.getByRole('button', { name: 'Back to campus' })).toBeVisible();
  await expect(card.getByRole('heading', { level: 2, name: 'Al Noor Fleet HQ' })).toBeVisible();
  await expect(card.getByRole('img', { name: /Presence is reporting/ })).toBeVisible();
  // The stage carries only scene controls: the facts list is in the building panels under the scene, not on the card.
  await expect(card.getByRole('list', { name: 'Building totals' })).toHaveCount(0);
  const facts = hq(page).locator('.hqBottomPanels').getByRole('list', { name: 'Building totals' });
  await expect(facts).toContainText('3Floors');
  await expect(facts).toContainText('5Rooms');
  await card.getByRole('button', { name: 'Back to campus' }).click();
  await expect(hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ })).toBeVisible();
});

test('pan: the camera bar switches drag-to-pan on and off', async ({ page }) => {
  await open(page);
  const pan = hq(page).getByTestId('hq-pan');
  await expect(pan).toHaveAttribute('aria-pressed', 'false');
  await pan.click();
  await expect(pan).toHaveAttribute('aria-pressed', 'true');
  await pan.click();
  await expect(pan).toHaveAttribute('aria-pressed', 'false');
});

test('wide scene: live operations from presence and the flow strip from /office/flow', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await open(page);
  await hq(page).getByRole('button', { name: /Hide side panels/ }).click();
  const ops = hq(page).getByRole('region', { name: 'Live numbers for this building' });
  await expect(ops).toBeVisible();
  await expect(ops).toContainText('People');
  const flow = hq(page).getByRole('region', { name: 'Work in flight in this building' });
  await expect(flow).toBeVisible();
  await expect(flow.getByRole('listitem').first()).toHaveAccessibleName(/^Arrived: \d+$/);
});

test('flow not connected: the flow strip says so, with no number', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await open(page, { flowFail: 500 });
  await hq(page).getByRole('button', { name: /Hide side panels/ }).click();
  const flow = hq(page).getByRole('region', { name: 'Work in flight in this building' });
  await expect(flow).toContainText('Not connected');
  await expect(flow.getByRole('listitem')).toHaveCount(0);
});

test('presence denied: People is "-" and the facts never invent a count', async ({ page }) => {
  await open(page, { stream: 'denied', presence: 'missing' });
  const facts = hq(page).locator('.hqBottomPanels').getByRole('list', { name: 'Building totals' });
  await expect(facts).toContainText('3Floors');
  await expect(hq(page).locator('.hqBackCard').getByRole('img', { name: /Presence is (reporting|not reporting)/ })).toBeVisible();
});

test('Arabic, right to left: Arabic subtitles, mirrored chrome, no sideways scroll, axe clean', async ({ page }) => {
  await open(page, { locale: 'ar' });
  await expect(hq(page).locator('.hqBackCard')).toBeVisible({ timeout: 30_000 }); // a lazy chunk
  const card = roomCards(page).first();
  await expect(card).toHaveAccessibleName(/مكتب|استقبال/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  const back = await hq(page).locator('.hqBackCard').boundingBox();
  const wrap = await hq(page).locator('.hqSceneWrap').boundingBox();
  expect(back!.x + back!.width / 2).toBeGreaterThan(wrap!.x + wrap!.width / 2);
  const results = await new AxeBuilder({ page }).include('.hqBuilding').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
});

test('English: axe clean over the labels and the chrome, in the day and the night lighting', async ({ page }) => {
  await open(page);
  for (const night of [false, true]) {
    await hq(page).getByRole('group', { name: /light|Lighting/i }).getByRole('button').nth(night ? 1 : 0).click();
    await page.waitForTimeout(800);
    const results = await new AxeBuilder({ page }).include('.hqBuilding').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  }
});

test('reduced motion: the label cards do not animate in', async ({ page }) => {
  await open(page, { reducedMotion: true });
  const name = await roomCards(page).first().evaluate((el) => getComputedStyle(el.parentElement!).animationName);
  expect(name).toBe('none');
});

test('no horizontal overflow and no clipped chrome at the five screen sizes', async ({ page }) => {
  await open(page);
  for (const [w, h] of [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const) {
    await page.setViewportSize({ width: w, height: h });
    await page.waitForTimeout(700);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), `${w}x${h}`).toBeLessThanOrEqual(0);
    const wrap = (await hq(page).locator('.hqBuilding .hqSceneWrap').boundingBox())!;
    for (const sel of ['.hqBackCard', '.hqDock .hqToolbar', '.hqOverlayRight .hqTools']) {
      const el = hq(page).locator(sel).first();
      if (!(await el.isVisible())) continue;
      const b = (await el.boundingBox())!;
      expect(b.x, `${sel} at ${w}`).toBeGreaterThanOrEqual(wrap.x - 1);
      expect(b.x + b.width, `${sel} at ${w}`).toBeLessThanOrEqual(wrap.x + wrap.width + 1);
    }
  }
});
