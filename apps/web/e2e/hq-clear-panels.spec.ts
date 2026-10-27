import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { mockFlow, type FlowMock } from './support/hqFlowApi';
import { mockHq } from './support/hqCampusApi';

// The page layout and the side panels of the headquarters (docs/ANUM_MINE_HQ_PAGE_LAYOUT.md): panels dock beside or under the scene and
// never over it, Total agents is whole, the live flow is a thin strip that leaves the quick views alone, every panel says whether it is
// live and when it was last confirmed, and the page reads in Arabic. The API is mocked at http://localhost:8000; every name is plainly fake.
test.use({ deviceScaleFactor: 1, isMobile: false, hasTouch: false });
test.describe.configure({ timeout: 240_000 });

const hq = (page: Page) => page.locator('.hqLive');
const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
let flow: FlowMock;

async function open(page: Page, size: { width: number; height: number }, options: Parameters<typeof openHq>[1] = {}) {
  await page.setViewportSize(size);
  const registry = hqRegistry(TENANT_A, { rooms: 4, desks: 4, agentIds: ['agent_fleet', 'agent_leads', 'agent_care', 'agent_invoice'] });
  const mock = await openHq(page, { registry, stream: 'live', ...options, beforeGoto: async (p) => { flow = await mockFlow(p); } });
  await expect(hq(page).getByRole('heading', { level: 1 })).toBeVisible();
  await expect(hq(page).locator('canvas.hqCanvas')).toBeVisible({ timeout: 60_000 });
  await expect(hq(page).locator('.hqBuildingCard').first()).toBeAttached({ timeout: 60_000 });
  return mock;
}
async function enterBuilding(page: Page) {
  await hq(page).locator('.hqLabels').getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click({ timeout: 60_000 });
  await hq(page).getByRole('button', { name: 'Open Al Noor Fleet HQ' }).click();
  await expect(hq(page).getByRole('button', { name: 'Back to campus' })).toBeVisible({ timeout: 30_000 });
}

type Box = { x: number; y: number; w: number; h: number };
const box = (page: Page, selector: string): Promise<Box | null> => page.evaluate((sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return r.width === 0 && r.height === 0 ? null : { x: r.left, y: r.top, w: r.width, h: r.height };
}, selector);
const overlaps = (a: Box, b: Box) => a.x < b.x + b.w - 1 && b.x < a.x + a.w - 1 && a.y < b.y + b.h - 1 && b.y < a.y + a.h - 1;
const noOverflow = async (page: Page) => expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);

/** No panel of the page sits on the stage; the status row and the quick views do not touch each other. */
async function assertClear(page: Page) {
  const stage = await box(page, '.hqLive .hqStage');
  expect(stage, 'the stage is drawn').not.toBeNull();
  for (const sel of ['.hqNavSide', '.hqStatusRow', '.hqStatusStrip', '.hqStrip', '.hqDetailDock', '.hqNavDockBelow', '.hqNavRail']) {
    const other = await box(page, `.hqLive ${sel}`);
    if (other) expect(overlaps(stage!, other), `${sel} must not overlap the stage`).toBe(false);
  }
  const strip = await box(page, '.hqLive .hqStrip');
  const row = await box(page, '.hqLive .hqStatusRow');
  if (strip && row) expect(overlaps(strip, row), 'the flow bar must not cover the quick views').toBe(false);
  await noOverflow(page);
}

for (const [width, height] of SIZES) {
  test(`campus ${width}x${height}: panels beside or under the scene, Total agents whole, every building card usable`, async ({ page }) => {
    await open(page, { width, height });
    await expect(hq(page).locator('.hqStatusRow')).toBeVisible({ timeout: 30_000 });
    await expect(hq(page).locator('.hqFlowDock')).toBeVisible();
    // Total agents: a docked panel in the side column when there is one, else a strip under the scene; never both, never clipped.
    const panel = hq(page).getByTestId('hq-status-panel');
    const strip = hq(page).getByTestId('hq-status-strip');
    await expect(panel.or(strip)).toBeVisible({ timeout: 30_000 });
    const target = (await panel.count()) ? '.hqLive [data-testid="hq-status-panel"]' : '.hqLive [data-testid="hq-status-strip"]';
    const b = (await box(page, target))!;
    expect(b.x).toBeGreaterThanOrEqual(0);
    expect(b.x + b.w).toBeLessThanOrEqual(width + 1);
    if (await panel.count()) {
      const side = (await box(page, '.hqLive .hqNavSide'))!;
      expect(b.x).toBeGreaterThanOrEqual(side.x - 1);
      expect(b.x + b.w).toBeLessThanOrEqual(side.x + side.w + 1);
    }
    await expect(hq(page).getByText('Total agents').first()).toBeVisible();
    await assertClear(page);
    // Every building card stays visible and clickable on the default view (the panels took nothing from them).
    const cards = hq(page).locator('.hqLabels .hqBuildingCard');
    await expect.poll(() => cards.count(), { timeout: 30_000 }).toBeGreaterThan(0);
    for (const card of await cards.all()) await expect(card).toBeVisible();
    await cards.first().click({ trial: true });
    // The scene itself keeps most of the width on a wide screen: the side column is at most a third of it.
    const stageBox = (await box(page, '.hqLive .hqStage'))!;
    if (width >= 1280) expect(stageBox.w).toBeGreaterThan(width * 0.5);
    expect(stageBox.h).toBeGreaterThan(height >= 800 ? 400 : 330);
  });
}

test('the selected building opens its panel beside or under the scene, never over it', async ({ page }) => {
  await open(page, { width: 1440, height: 900 });
  await hq(page).locator('.hqLabels').getByRole('button', { name: /^Executive HQ\./ }).click();
  const detail = hq(page).locator('.hqDetail');
  await expect(detail).toBeVisible();
  await expect(detail).toContainText('Executive HQ');
  await assertClear(page);
  const d = (await box(page, '.hqLive .hqDetail'))!;
  const stage = (await box(page, '.hqLive .hqStage'))!;
  expect(overlaps(d, stage)).toBe(false);
  await detail.getByRole('button', { name: /Close|×/ }).click();
  await expect(detail).toHaveCount(0);
});

test('inside a building the room panel docks in the side column and the scene stays clear', async ({ page }) => {
  await open(page, { width: 1440, height: 900 });
  await enterBuilding(page);
  await hq(page).locator('.hqLabels').getByRole('button', { name: /Customer Care|Room|Alpha/ }).first().click();
  await expect(hq(page).locator('.hqDetailDock')).toBeVisible({ timeout: 30_000 });
  await assertClear(page);
  const dock = (await box(page, '.hqLive .hqDetailDock'))!;
  const side = (await box(page, '.hqLive .hqNavSide'))!;
  expect(dock.x).toBeGreaterThanOrEqual(side.x - 1);
  // Nothing but the scene's own controls hangs over the stage: no wall board, no operations or flow strips (they are docked under the scene), no floating room panel.
  await expect(hq(page).locator('.hqSceneWrap [data-testid="hq-wall-scene"], .hqSceneWrap .hqOpsStrip, .hqSceneWrap .hqFlowStrip, .hqSceneWrap .hqSideOverlay')).toHaveCount(0);
  await expect(hq(page).getByRole('region', { name: 'Live numbers for this building' })).toBeVisible();
});

test('narrow screens: the room panel sits under the scene, stage first', async ({ page }) => {
  await open(page, { width: 390, height: 844 });
  await enterBuilding(page);
  await hq(page).locator('.hqLabels').getByRole('button', { name: /Customer Care|Room|Alpha/ }).first().click();
  await expect(hq(page).locator('.hqDetailDock')).toBeVisible({ timeout: 30_000 });
  const stage = (await box(page, '.hqLive .hqStage'))!;
  const dock = (await box(page, '.hqLive .hqDetailDock'))!;
  expect(dock.y).toBeGreaterThanOrEqual(stage.y + stage.h - 1);
  await assertClear(page);
});

test('every panel says whether it is live and when it was last confirmed; a dropped feed is shown and recovers', async ({ page }) => {
  let down = false;
  const mock = await open(page, { width: 1440, height: 900 });
  await page.route('http://localhost:8000/api/v1/office/presence**', (route) => (down ? route.abort('connectionrefused') : route.fallback()));
  const status = hq(page).getByTestId('hq-status-live');
  const flowChip = hq(page).getByTestId('hq-flow-live');
  await expect(status).toHaveAttribute('data-live', 'live', { timeout: 30_000 });
  // The real time of the last confirmation is shown (hh:mm:ss), never invented.
  await expect(status).toContainText(/Verified \d{2}:\d{2}:\d{2}/);
  await expect(flowChip).toContainText(/Updated \d{2}:\d{2}:\d{2}/, { timeout: 30_000 });
  await expect(flowChip).toHaveAttribute('data-live', /live|polling/);
  // The flow feed fails: the bar says Not connected (not Live) and offers a retry in its sheet.
  flow.failWith(500);
  await hq(page).getByRole('button', { name: /Live flow/ }).first().click();
  await hq(page).getByRole('button', { name: /Reload|Retry|Try again/ }).first().click({ timeout: 30_000 }).catch(() => undefined);
  await expect(flowChip).toHaveAttribute('data-live', 'offline', { timeout: 30_000 });
  await expect(hq(page).getByTestId('hq-panels-live')).toContainText(/not connected/i, { timeout: 30_000 });
  flow.failWith(null);
  await hq(page).getByRole('button', { name: /Reload|Retry|Try again/ }).first().click({ timeout: 30_000 }).catch(() => undefined);
  await expect(flowChip).not.toHaveAttribute('data-live', 'offline', { timeout: 40_000 });
  // The presence feed drops, then recovers.
  down = true;
  await mock.stream.deliver().catch(() => undefined);
  await expect(status).not.toHaveAttribute('data-live', 'live', { timeout: 60_000 });
  await expect(hq(page).getByTestId('hq-live-link')).not.toHaveText('Live', { timeout: 60_000 });
  down = false;
  await expect(status).toHaveAttribute('data-live', /live|polling/, { timeout: 90_000 });
});

test('panels fold and open again, the choice is kept, and Hide side panels gives the scene the whole width', async ({ page }) => {
  await open(page, { width: 1440, height: 900 });
  const fold = hq(page).getByRole('button', { name: 'Fold Total agents' });
  await expect(fold).toHaveAttribute('aria-expanded', 'true', { timeout: 30_000 });
  await fold.click();
  const open_ = hq(page).getByRole('button', { name: 'Open Total agents' });
  await expect(open_).toHaveAttribute('aria-expanded', 'false');
  await expect(hq(page).getByTestId('hq-status-panel')).toBeHidden();
  // The header stays, with its live status.
  await expect(hq(page).getByTestId('hq-status-live')).toBeVisible();
  await page.reload();
  await expect(hq(page).getByRole('button', { name: 'Open Total agents' })).toBeVisible({ timeout: 60_000 });
  await hq(page).getByRole('button', { name: 'Open Total agents' }).click();
  await expect(hq(page).getByTestId('hq-status-panel')).toBeVisible();
  const before = (await box(page, '.hqLive .hqStage'))!;
  await hq(page).getByRole('button', { name: 'Hide side panels' }).click();
  await expect(hq(page).getByTestId('hq-status-strip')).toBeVisible();
  const after = (await box(page, '.hqLive .hqStage'))!;
  expect(after.w).toBeGreaterThan(before.w + 200);
  await assertClear(page);
  await hq(page).getByRole('button', { name: 'Show side panels' }).click();
  await expect(hq(page).getByTestId('hq-status-panel')).toBeVisible();
});

test('the campus map is a corner button, closed by default, and opens without covering the controls', async ({ page }) => {
  await open(page, { width: 1440, height: 900 });
  const toggle = hq(page).getByTestId('hq-map-toggle');
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await expect(hq(page).locator('.hqMinimap')).toHaveCount(0);
  await toggle.click();
  await expect(hq(page).locator('.hqMinimap')).toBeVisible();
  const map = (await box(page, '.hqLive .hqOverlayMap'))!;
  const rail = (await box(page, '.hqLive .hqOverlayRight'))!;
  expect(overlaps(map, rail)).toBe(false);
  await toggle.click();
  await expect(hq(page).locator('.hqMinimap')).toHaveCount(0);
});

test('states: the campus is refused, and the panel says who can open it', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openHq(page, { stream: 'live' });
  // A second mock layer is not needed: the campus endpoint answers 403 here.
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockHq(page, { campus: 'denied', commerce: 'none', meetings: 'none' });
  await page.reload();
  const panel = hq(page).getByTestId('hq-status-panel');
  await expect(panel).toContainText('Ask a workspace owner', { timeout: 60_000 });
  await noOverflow(page);
});

for (const [width, height] of [[1440, 900], [390, 844]] as const) {
  test(`Arabic ${width}x${height}: right to left, panels clear of the scene, no overflow`, async ({ page }) => {
    await open(page, { width, height }, { locale: 'ar' });
    await expect(hq(page).getByTestId('hq-status-live')).toBeVisible({ timeout: 60_000 });
    expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
    await expect(hq(page).getByTestId('hq-status-live')).toContainText(/مباشر|يتحقق|جارٍ|غير متصل|الاتصال/);
    await assertClear(page);
    const live = hq(page).getByTestId('hq-flow-live');
    await expect(live).toContainText(/\d{2}:\d{2}:\d{2}/, { timeout: 30_000 });
  });
}

test('keyboard and screen reader basics: landmarks, polite live regions, drawer focus', async ({ page }) => {
  await open(page, { width: 1440, height: 900 });
  await expect(hq(page).getByRole('complementary', { name: 'Floor plan, people and quick moves' })).toBeVisible({ timeout: 30_000 });
  await expect(hq(page).getByTestId('hq-panels-live')).toHaveAttribute('aria-live', 'polite');
  await expect(hq(page).getByRole('region', { name: 'Total agents' })).toBeVisible();
  await expect(hq(page).getByRole('region', { name: 'Quick moves' })).toBeVisible();
  // A drawer takes focus when it opens and gives it back when it closes.
  await page.setViewportSize({ width: 1024, height: 768 });
  const opener = hq(page).getByRole('button', { name: /Quick moves/ }).first();
  await expect(opener).toBeVisible({ timeout: 30_000 });
  await opener.focus();
  await opener.click();
  const close = hq(page).locator('.hqDrawer').getByRole('button', { name: /Close/ });
  await expect(close).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(hq(page).locator('.hqDrawer')).toHaveCount(0);
  await expect(opener).toBeFocused();
});

for (const [width, height] of [[1440, 900], [390, 844]] as const) {
  test(`axe ${width}x${height}: the panels have no violations`, async ({ page }) => {
    await open(page, { width, height });
    await expect(hq(page).getByTestId('hq-status-live')).toBeVisible({ timeout: 60_000 });
    const results = await new AxeBuilder({ page }).include('.hqLive').exclude('canvas').exclude('.hqLabels').disableRules(['color-contrast']).analyze();
    expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  });
}
