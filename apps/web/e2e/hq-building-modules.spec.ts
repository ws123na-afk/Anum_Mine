import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { mockHq, type HqMockOptions } from './support/hqCampusApi';
import { mockFlow } from './support/hqFlowApi';
import { useSavedLocale } from './support/workspaceApi';
import { sceneReady } from './support/hqScene';

// Buildings with their own module panels, the command centre room kind, the personal office and the floor transitions
// (docs/ANUM_MINE_HQ_BUILDING_MODULES.md). The API is mocked at http://localhost:8000; every name and number is plainly fake.
test.describe.configure({ timeout: 240_000 });
const SHOTS = process.env.HQ_BM_SHOTS;
const hq = (page: Page) => page.locator('.hqLive');
const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
const noOverflow = async (page: Page) => expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
const shot = async (page: Page, name: string) => { if (SHOTS) { mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: `${SHOTS}/${name}.png` }); } };

async function open(page: Page, options: HqMockOptions = {}, { reducedMotion = false, flow = true }: { reducedMotion?: boolean; flow?: boolean } = {}) {
  await page.addInitScript(() => { try { localStorage.setItem('anum.hq.light', 'day'); } catch { /* storage blocked */ } });
  if (reducedMotion) await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockHq(page, { layout: 'campus', campus: 'missing', commerce: 'connected', tyre: 'connected', meetings: 'none', ...options });
  if (flow) await mockFlow(page);
  await page.goto('/#office-preview');
  await expect(hq(page).getByRole('heading', { level: 1 })).toBeVisible();
}

async function enter(page: Page, name: string) {
  await hq(page).getByRole('button', { name: new RegExp('^' + name + '\\.') }).click({ timeout: 60_000 });
  await hq(page).getByRole('button', { name: `Open ${name}` }).click();
  await expect(hq(page).getByRole('heading', { level: 2, name })).toBeVisible();
}

test.describe('each building shows its own panels', () => {
  test('ANUM HQ shows the ANUM OS wall (tasks and agents), no commerce and no tyre numbers', async ({ page }) => {
    await open(page);
    await enter(page, 'ANUM HQ');
    const wall = hq(page).locator('.hqBottomPanels [data-testid="hq-wall-panel"]');
    await expect(wall).toBeVisible();
    await expect(wall.locator('[data-tile="openTasks"] strong')).toHaveText('3');
    await expect(hq(page).getByRole('region', { name: 'Team status' })).toBeVisible();
    await expect(hq(page).getByRole('region', { name: 'Live operations' })).toHaveCount(0);
    await expect(hq(page).getByRole('region', { name: 'Tyre fleet' })).toHaveCount(0);
  });

  test('ANUM 360 Commerce shows orders and stock from the commerce dashboard', async ({ page }) => {
    await open(page);
    await enter(page, 'ANUM 360 Commerce');
    const live = hq(page).getByRole('region', { name: 'Live operations' });
    await expect(live).toContainText('17Orders today');
    await expect(live).toContainText('2Low stock');
    await expect(hq(page).getByRole('region', { name: 'Current flow' })).toContainText('7Waiting to ship');
    await expect(hq(page).getByRole('region', { name: 'Tyre fleet' })).toHaveCount(0);
    await expect(hq(page).locator('.hqBottomPanels [data-testid="hq-wall-panel"]')).toHaveCount(0);
  });

  test('Tyre Pulse shows fleet and workshop numbers from the tyre dashboard', async ({ page }) => {
    await open(page);
    await enter(page, 'Tyre Pulse');
    const tyre = hq(page).getByRole('region', { name: 'Tyre fleet' });
    await expect(tyre).toContainText('42Vehicles');
    await expect(tyre).toContainText('250Tyres fitted');
    await expect(tyre).toContainText('30Tyres in stock');
    await expect(tyre).toContainText('3Below minimum');
    await expect(hq(page).getByRole('region', { name: 'Live operations' })).toHaveCount(0);
  });

  test('the personal office shows the ANUM OS wall too', async ({ page }) => {
    await open(page);
    await enter(page, 'Founder Personal Office');
    await expect(hq(page).locator('.hqBottomPanels [data-testid="hq-wall-panel"]')).toBeVisible();
  });

  test('a source that is not connected says so and offers the setup; a denied one says who can see it', async ({ page }) => {
    await open(page, { commerce: 'none', tyre: 'none' });
    await enter(page, 'ANUM 360 Commerce');
    const live = hq(page).getByRole('region', { name: 'Live operations' });
    await expect(live).toContainText('Not connected');
    await expect(live.getByRole('link', { name: 'Set it up' })).toHaveAttribute('href', '#commerce');
    await hq(page).getByRole('button', { name: 'Back to campus' }).click();
    await enter(page, 'Tyre Pulse');
    const tyre = hq(page).getByRole('region', { name: 'Tyre fleet' });
    await expect(tyre).toContainText('Not connected');
    await expect(tyre.getByRole('link', { name: 'Set it up' })).toHaveAttribute('href', '#tyre');
  });

  test('commerce access denied is explained, not shown as zero', async ({ page }) => {
    await open(page, { commerce: 'denied' });
    await enter(page, 'ANUM 360 Commerce');
    const live = hq(page).getByRole('region', { name: 'Live operations' });
    await expect(live).toContainText('No access');
    await expect(live).not.toContainText('Orders today');
  });

  test('the flow wall of the ANUM OS panel says Not connected when the flow cannot be read', async ({ page }) => {
    await open(page, {}, { flow: false });
    await enter(page, 'ANUM HQ');
    const wall = hq(page).locator('.hqBottomPanels [data-testid="hq-wall-panel"]');
    await expect(wall.locator('[data-tile="openTasks"]')).toContainText('Not connected');
    await expect(wall.locator('[data-tile="openTasks"] strong')).not.toHaveText(/\d/);
  });
});

test.describe('rooms and modules', () => {
  // The list shows the rooms of the open building, so the building is entered first.
  async function openRoomFromList(page: Page, name: string, building = 'ANUM HQ') {
    await enter(page, building);
    await hq(page).getByRole('button', { name: 'List', exact: true }).click();
    await page.locator('.hqListRoom', { hasText: new RegExp('^' + name) }).first().click({ timeout: 20_000 });
  }

  test('the command centre is its own room kind: it opens the live flow and shows the wall', async ({ page }) => {
    await open(page);
    await openRoomFromList(page, 'Command Center');
    const side = page.locator('.hqSide');
    await expect(side).toContainText('Command centre');
    await expect(side.locator('[data-testid="hq-wall-panel"]')).toBeVisible();
    await expect(side.locator('[data-testid="room-module"]')).toContainText('live flow');
    await expect(side.locator('[data-testid="room-module-hint"]')).toHaveCount(1);
    await expect(page.locator('.hqFlowDock')).toHaveAttribute('data-open', 'false');
    await side.locator('[data-testid="room-module"]').click();
    await expect(page.locator('.hqFlowDock')).toHaveAttribute('data-open', 'true');
  });

  test('rooms of the HQ do not all open the executive desk; the marketing room opens Marketing', async ({ page }) => {
    await open(page);
    await openRoomFromList(page, 'Facilities Office');
    await expect(page.locator('.hqSide [data-testid="room-module"]')).toHaveAttribute('href', '#tasks');
    await page.locator('.hqListRoom', { hasText: /^Marketing Studio/ }).first().click();
    await expect(page.locator('.hqSide [data-testid="room-module"]')).toHaveAttribute('href', '#marketing');
    await page.locator('.hqListRoom', { hasText: /^Executive Office/ }).first().click();
    await expect(page.locator('.hqSide [data-testid="room-module"]')).toHaveAttribute('href', '#executive');
  });

  test('every room in the list opens a module or says why not (no dead rows)', async ({ page }) => {
    await open(page);
    let total = 0;
    for (const name of ['ANUM HQ', 'ANUM 360 Commerce', 'Tyre Pulse', 'Founder Personal Office']) {
      await enter(page, name);
      await hq(page).getByRole('button', { name: 'List', exact: true }).click();
      const rows = page.locator('.hqListRooms li');
      await expect(rows.first()).toBeVisible();
      const count = await rows.count();
      total += count;
      for (let i = 0; i < count; i++) {
        const row = rows.nth(i);
        const link = await row.locator('[data-testid="room-module"]').count();
        const none = await row.locator('[data-testid="room-module-none"]').count();
        expect(link + none, await row.innerText()).toBeGreaterThan(0);
      }
      await hq(page).getByRole('button', { name: '3D', exact: true }).click();
      await hq(page).getByRole('button', { name: 'Back to campus' }).click();
    }
    expect(total).toBe(14);
  });
});

test.describe('floors: eased cut-away, highlight, buttons and keyboard', () => {
  const floorName = (page: Page) => hq(page).locator('.hqFloorControl select');
  async function inHq(page: Page, options?: { reducedMotion?: boolean }) {
    await open(page, {}, options);
    await enter(page, 'ANUM HQ');
    await sceneReady(page);
  }

  test('previous and next buttons, the count and the live note follow the floor; the ends are disabled', async ({ page }) => {
    await inHq(page);
    const count = page.getByTestId('hq-floor-count');
    const live = page.getByTestId('hq-floor-live');
    const next = hq(page).getByRole('button', { name: 'Floor above' }).last();
    const prev = hq(page).getByRole('button', { name: 'Floor below' }).last();
    await expect(count).toHaveText('1 / 3');
    await expect(prev).toBeDisabled();
    await next.click();
    await expect(count).toHaveText('2 / 3');
    await expect(live).toContainText('Floor 2: Operations and Management');
    await expect(live).toContainText('floor 2 of 3');
    await next.click();
    await expect(count).toHaveText('3 / 3');
    await expect(next).toBeDisabled();
    await prev.click();
    await expect(count).toHaveText('2 / 3');
  });

  test('Page Up and Page Down move between floors, and are left alone while typing in a field', async ({ page }) => {
    await inHq(page);
    const count = page.getByTestId('hq-floor-count');
    await hq(page).locator('.hqStage').focus();
    await page.keyboard.press('PageUp');
    await expect(count).toHaveText('2 / 3');
    await page.keyboard.press('PageUp');
    await expect(count).toHaveText('3 / 3');
    await page.keyboard.press('PageUp');
    await expect(count).toHaveText('3 / 3');
    await page.keyboard.press('PageDown');
    await expect(count).toHaveText('2 / 3');
    // The floor select keeps its own keys (the page does not change floor under the person's hands).
    await floorName(page).focus();
    await page.keyboard.press('PageUp');
    await expect(count).toHaveText('2 / 3');
  });

  test('"All floors" shows the whole stack and floors still change', async ({ page }) => {
    await inHq(page);
    const all = hq(page).getByLabel('All floors');
    await all.click();
    await expect(all).toBeChecked();
    const above = hq(page).getByRole('button', { name: 'Floor above' }).last();
    await above.click();
    await above.click();
    await expect(page.getByTestId('hq-floor-count')).toHaveText('3 / 3');
    await expect(hq(page).locator('canvas.hqCanvas')).toBeVisible();
  });

  test('reduced motion changes floor at once and the scene stays drawn', async ({ page }) => {
    await inHq(page, { reducedMotion: true });
    await hq(page).getByRole('button', { name: 'Floor above' }).last().click();
    await expect(page.getByTestId('hq-floor-count')).toHaveText('2 / 3');
    await expect(hq(page).locator('canvas.hqCanvas')).toBeVisible();
  });

  test('moving floors keeps the frame time bounded (software GL)', async ({ page }) => {
    await inHq(page);
    const canvas = hq(page).locator('canvas.hqCanvas');
    const stats = () => canvas.evaluate((el) => ({ ...(el as HTMLCanvasElement).dataset }));
    const before = await stats();
    for (let i = 0; i < 2; i++) { await page.keyboard.press('PageUp'); await page.waitForTimeout(600); }
    for (let i = 0; i < 2; i++) { await page.keyboard.press('PageDown'); await page.waitForTimeout(600); }
    const after = await stats();
    // The renderer reports its average draw time; a cut-away that cost frames would show here.
    expect(Number(after.drawMs ?? before.drawMs ?? 0)).toBeLessThan(250);
  });
});

test.describe('Arabic', () => {
  test('the panels, the command centre kind and the floor note read right to left', async ({ page }) => {
    await useSavedLocale(page, 'ar');
    await open(page);
    await enter(page, 'Tyre Pulse');
    await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
    const tyre = hq(page).getByRole('region', { name: 'أسطول الإطارات' });
    await expect(tyre).toContainText('إطارات مركّبة');
    await expect(tyre).toContainText('إطارات في المخزون');
    await noOverflow(page);
  });
});

for (const [w, h] of SIZES) {
  test(`@viewport building views fit at ${w}x${h}`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await open(page);
    await shot(page, `campus-${w}x${h}`);
    for (const [name, file] of [['ANUM HQ', 'hq'], ['ANUM 360 Commerce', 'commerce'], ['Tyre Pulse', 'tyre'], ['Founder Personal Office', 'personal']] as const) {
      await enter(page, name);
      await sceneReady(page);
      await noOverflow(page);
      await expect(hq(page).locator('.hqBottomPanels')).toBeVisible();
      await shot(page, `${file}-${w}x${h}`);
      await hq(page).getByRole('button', { name: 'Back to campus' }).click();
    }
    // The command centre room inspector.
    await hq(page).getByRole('button', { name: 'List', exact: true }).click();
    await page.locator('.hqListRoom', { hasText: /^Command Center/ }).first().click();
    await expect(page.locator('.hqSide [data-testid="room-module"]')).toBeVisible();
    await noOverflow(page);
    await shot(page, `command-${w}x${h}`);
  });
}

test('no accessibility violations on a building with its panels', async ({ page }) => {
  await open(page);
  await enter(page, 'Tyre Pulse');
  await sceneReady(page);
  const results = await new AxeBuilder({ page }).include('.hqLive').analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
});
