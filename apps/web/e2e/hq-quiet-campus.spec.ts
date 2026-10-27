import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { campusFixture, mockHq, presenceItem } from './support/hqCampusApi';
import { mockFlow } from './support/hqFlowApi';
import { sceneReady } from './support/hqScene';
import { useSavedLocale } from './support/workspaceApi';

// The quiet campus (docs/ANUM_MINE_HQ_BILLBOARDS.md "The board", docs/ANUM_MINE_HQ_LABELS.md "Quiet campus"): the 3D boards carry the live details,
// the page labels are a few small chips and status dots, and the full card shows only for the focused, hovered or selected building.
// The API is mocked at http://localhost:8000; every name and number is plainly fake. With QUIET_SHOTS set the screenshots land there.
test.describe.configure({ timeout: 300_000 });
test.use({ deviceScaleFactor: 1, isMobile: false, hasTouch: false, viewport: { width: 1440, height: 900 } });
const SHOTS = process.env.QUIET_SHOTS;
const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
const hq = (page: Page) => page.locator('.hqLive');
const wrap = (page: Page) => hq(page).locator('.hqSceneWrap');
const shot = async (page: Page, name: string) => { if (SHOTS) { mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: `${SHOTS}/${name}.png` }); } };
const noOverflow = async (page: Page) => expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);

const zeroState = { working: 0, queued: 0, meeting: 0, waiting_approval: 0, blocked: 0, idle: 0, paused: 0, unknown: 0 };
/** Green Concrete: four agents, mostly paused (the owner's own campus), next to the starter campus: thirteen buildings and this one. */
const green = { ...campusFixture.buildings[3], workspace_id: 'workspace_green', name: 'Green Concrete', kind: null, profile_revision: null, agents: { total: 4, by_state: { ...zeroState, idle: 1, paused: 3 } } };
/** The campus read as of now: a slow runner must not make the fixture's own verified time older than the 90 seconds after which a board says Stale. */
const freshCampus = () => {
  const at = new Date().toISOString();
  return { ...campusFixture, generated_at: at, buildings: [...campusFixture.buildings, green].map((b) => ({ ...b, presence_as_of: at, presence_verified_at: b.agents ? at : null })) };
};

type OpenOptions = { locale?: 'en' | 'ar'; night?: boolean; stream?: 'live' | 'unsupported' | 'down'; presence?: 'api' | 'missing'; flow?: boolean; size?: readonly [number, number] };
async function open(page: Page, { locale = 'en', night = false, stream = 'live', presence = 'api', flow = true, size = [1440, 900] }: OpenOptions = {}) {
  await page.addInitScript((n) => { try { localStorage.setItem('anum.hq.light', n ? 'night' : 'day'); } catch { /* storage blocked */ } }, night);
  await useSavedLocale(page, locale);
  const mock = await mockHq(page, { campus: 'api', layout: 'starter', commerce: 'connected', meetings: 'live', presence, stream });
  await page.route('http://localhost:8000/api/v1/office/campus', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(freshCampus()) }));
  const flowMock = await mockFlow(page);
  if (!flow) flowMock.failWith(500);
  await page.setViewportSize({ width: size[0], height: size[1] });
  await page.goto('/#office-preview');
  await sceneReady(page);
  await expect(wrap(page)).toHaveAttribute('data-billboards', '14', { timeout: 90_000 });
  await expect(wrap(page)).not.toHaveAttribute('data-board-version', '0', { timeout: 60_000 });
  return mock;
}

/** The text on every board right now, one line per board. */
const boardLines = async (page: Page): Promise<string[]> => ((await wrap(page).getAttribute('data-board-texts')) ?? '').split('\n');
const boardOf = async (page: Page, name: string): Promise<string> => (await boardLines(page)).find((l) => l.startsWith(name)) ?? '';
const rects = (page: Page) => page.evaluate(() => {
  const box = (el: Element) => { const r = el.getBoundingClientRect(); return { x0: r.left, y0: r.top, x1: r.right, y1: r.bottom, name: (el.getAttribute('aria-label') ?? el.className).toString().slice(0, 40), dot: (el as HTMLElement).dataset?.density === 'dot' }; };
  const shown = (el: HTMLElement) => el.style.display !== 'none' && el.dataset.hidden !== 'crowded' && el.getBoundingClientRect().width > 0;
  const cards = [...document.querySelectorAll<HTMLElement>('.hqLive .hqLabels:not(.hqLabelsExtra) .hqLabelAnchor')].filter(shown).map((el) => box(el.querySelector('.hqBuildingCard') ?? el));
  const panels = [...document.querySelectorAll('.hqLive .hqDetail, .hqLive .hqOverlayRight, .hqLive .hqOverlayBottomLeft, .hqLive .hqOverlayBottomRight, .hqLive .hqOverlayBottom > *')].map(box).filter((r) => r.x1 - r.x0 > 14 && r.y1 - r.y0 > 14);
  return { cards, panels };
});
const hit = (a: { x0: number; y0: number; x1: number; y1: number }, b: { x0: number; y0: number; x1: number; y1: number }) => a.x0 < b.x1 - 1 && a.x1 > b.x0 + 1 && a.y0 < b.y1 - 1 && a.y1 > b.y0 + 1;

for (const [w, h] of SIZES) {
  test(`calm at ${w}x${h}: chips are minimal and few, no card overlaps another or a panel, every building keeps its button, no axe violations`, async ({ page }) => {
    await open(page, { size: [w, h] });
    await noOverflow(page);
    const buttons = hq(page).locator('.hqBuildingCard');
    // Thirteen buildings of the starter campus and Green Concrete: every one has its button, whatever it looks like.
    expect(await buttons.count()).toBe(14);
    // Chips are few; the rest are status dots; the old full card with kind chips, avatars and counts is gone.
    const chips = await hq(page).locator('.hqBuildingCard[data-density="compact"]').count();
    expect(chips).toBeGreaterThan(0);
    expect(chips).toBeLessThanOrEqual(w < 700 ? 2 : 4);
    expect(await hq(page).locator('.hqBuildingCard[data-density="full"]').count()).toBe(0);
    expect(await hq(page).locator('.hqBuildingCard .hqCardTags, .hqBuildingCard .hqAvatarStack, .hqBuildingCard small:not(.hqWorkLine)').count()).toBe(0);
    // No unfocused billboard card is in the page: the boards carry the details.
    await expect(hq(page).locator('[data-testid="hq-billboard"]')).toHaveCount(0);
    // The accessible names are unchanged: name, then the presence summary.
    await expect(hq(page).getByRole('button', { name: /^Green Concrete\. 4 agents, 0 working/ })).toBeAttached();
    await expect(hq(page).getByRole('button', { name: /^Executive HQ\. 5 agents, 2 working/ })).toBeAttached();
    // Nothing overlaps: no two shown cards, and no card under a panel or a control.
    const { cards, panels } = await rects(page);
    expect(cards.length).toBeGreaterThan(0);
    for (let i = 0; i < cards.length; i++) {
      // On a scene narrower than 641 px no card is left out (docs/ANUM_MINE_HQ_LABELS.md): two status dots with no free spot may touch there.
      // A chip never overlaps anything, and on wider scenes nothing does.
      for (let j = i + 1; j < cards.length; j++) if (w >= 641 || !(cards[i].dot && cards[j].dot)) expect(hit(cards[i], cards[j]), `${cards[i].name} covers ${cards[j].name}`).toBe(false);
      for (const p of panels) expect(hit(cards[i], p), `${cards[i].name} is under ${p.name}`).toBe(false);
    }
    const results = await new AxeBuilder({ page }).include('.hqLive').withTags(['wcag2a', 'wcag2aa']).analyze();
    expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
    await shot(page, `calm-en-${w}x${h}`);
  });
}

test('the full card is for the focused building only: hover, keyboard focus and selection', async ({ page }) => {
  await open(page);
  const billboards = hq(page).locator('[data-testid="hq-billboard"]');
  await expect(billboards).toHaveCount(0);
  // Hover: that building's billboard card (full) and no other.
  const green = hq(page).getByRole('button', { name: /^Green Concrete\./ });
  await green.hover();
  await expect(billboards).toHaveCount(1);
  await expect(billboards.first()).toHaveAttribute('data-building', 'workspace_green');
  await expect(billboards.first()).toHaveAttribute('data-focused', 'true');
  await expect(billboards.first().locator('[data-field="presence"] .hqBbTotal')).toContainText('4 agents');
  await page.mouse.move(5, 5);
  await expect(billboards).toHaveCount(0);
  // Selection: the detail panel and the billboard card, still a single full card.
  await green.click();
  await expect(hq(page).locator('.hqDetail')).toContainText('Green Concrete');
  await expect(billboards).toHaveCount(1);
  await page.keyboard.press('Escape');
  // Keyboard: focusing a chip's button is enough to reach it (every building has a button in the tab order).
  const executive = hq(page).getByRole('button', { name: /^Executive HQ\./ });
  await executive.focus();
  await executive.press('Enter');
  await expect(hq(page).locator('.hqDetail')).toContainText('Executive HQ');
});

test('the boards carry the live details; a presence update changes the board text in place, and Live shows only while the stream is connected', async ({ page }) => {
  const mock = await open(page);
  const lines = await boardLines(page);
  expect(lines.length).toBe(14);
  const gc = await boardOf(page, 'Green Concrete');
  // What the building does, the live counts with the verified time, and open work: all from records, nothing else.
  expect(gc).toContain('Green Concrete');
  expect(gc).toContain('0 working');
  expect(gc).toContain('4 agents, Idle 1, Paused 3');
  expect(gc).toMatch(/(Verified|Stale: last verified) \d/);
  expect(gc).toContain('Open work: Not connected');
  for (const secret of ['Fleet Planner', 'Outreach Writer', 'Plan October', 'Acme']) expect(lines.join('\n')).not.toContain(secret);
  // The stream is up: the Live tick is on the boards.
  await expect(hq(page).getByTestId('hq-live-link')).toHaveAttribute('data-link', 'live', { timeout: 30_000 });
  await expect.poll(async () => (await boardLines(page)).filter((l) => l.endsWith('Live')).length, { timeout: 30_000 }).toBeGreaterThan(0);
  // A presence update arrives: a working agent goes idle. The board of its building is drawn again (not the scene), within a few seconds.
  const before = Number(await wrap(page).getAttribute('data-board-version'));
  const beforeText = (await boardLines(page)).join('\n');
  const stage = hq(page).locator('canvas.hqCanvas');
  const triangles = await stage.getAttribute('data-triangles');
  await mock.stream.deliver({ changed: [presenceItem('agent_fleet', 'Fleet Planner', 'idle', { desk_id: 'desk_3' })] });
  await expect.poll(async () => Number(await wrap(page).getAttribute('data-board-version')), { timeout: 30_000 }).toBeGreaterThan(before);
  expect((await boardLines(page)).join('\n')).not.toBe(beforeText);
  // The scene was not rebuilt for it.
  expect(await stage.getAttribute('data-triangles')).toBe(triangles);
});

test('a building without presence says Not connected, never a zero, and no board shows Live without a stream', async ({ page }) => {
  await open(page, { presence: 'missing', stream: 'unsupported', flow: false });
  const lines = await boardLines(page);
  // Tyre Pulse HQ and Legal and Compliance report no presence in the campus read: Not connected, no verified time, no Live tick.
  for (const name of ['Tyre Pulse HQ', 'Legal and Compliance']) {
    const line = await boardOf(page, name);
    expect(line).toContain('Not connected');
    expect(line).not.toMatch(/Verified|Live|\b0 working|No agents/);
  }
  expect((await boardOf(page, 'Tyre Pulse HQ'))).toContain('Open work: Not connected');
  expect(lines.filter((l) => /\| Live$/.test(l)).length).toBe(0);
});

test('a stream that is down: the numbers stay with their verified time and no Live tick is shown', async ({ page }) => {
  await open(page, { stream: 'down' });
  await expect(hq(page).getByTestId('hq-live-link')).not.toHaveAttribute('data-link', 'live');
  expect((await boardLines(page)).filter((l) => /\| Live$/.test(l)).length).toBe(0);
});

test('Arabic: the boards and the chips read right to left with the Arabic texts', async ({ page }) => {
  await open(page, { locale: 'ar' });
  expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
  const lines = await boardLines(page);
  expect(lines.some((l) => /يعملون/.test(l))).toBe(true);
  expect(lines.join('\n')).not.toMatch(/\bworking\b/);
  await expect(hq(page).locator('.hqBuildingCard').first()).toBeAttached();
  expect(await hq(page).locator('.hqBuildingCard .hqCardTags, .hqBuildingCard .hqAvatarStack, .hqBuildingCard small:not(.hqWorkLine)').count()).toBe(0);
  await noOverflow(page);
  await shot(page, 'calm-ar-1440x900');
});

test('at night the boards glow and are still readable (screenshots with QUIET_SHOTS)', async ({ page }) => {
  await open(page, { night: true });
  await page.waitForTimeout(1500);
  await shot(page, 'calm-night-en-1440x900');
  const lines = await boardLines(page);
  expect(lines.length).toBe(14);
});

test('a quiet campus still reaches every building through the list view', async ({ page }) => {
  await open(page);
  await hq(page).getByRole('group', { name: /View/ }).getByRole('button', { name: /List/ }).click();
  await expect(hq(page).locator('.hqListBuilding')).toHaveCount(14, { timeout: 30_000 });
});
