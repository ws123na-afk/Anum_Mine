import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { campusFixture, mockHq } from './support/hqCampusApi';
import { flowItems, mockFlow } from './support/hqFlowApi';
import { sceneReady } from './support/hqScene';
import { useSavedLocale } from './support/workspaceApi';

// The building billboards (docs/ANUM_MINE_HQ_BILLBOARDS.md): a board in front of every building and a card with what the building does and
// its real work details. The API is mocked at http://localhost:8000; every name and number is plainly fake. With BB_SHOTS set the
// screenshots land there (the stage only, so the board and the card can be inspected).
test.describe.configure({ timeout: 240_000 });
test.use({ viewport: { width: 1440, height: 900 } });
const SHOTS = process.env.BB_SHOTS;
const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
const hq = (page: Page) => page.locator('.hqLive');
const HQ_KEY = 'workspace_foundation~campus-01-hq';
const card = (page: Page, key = HQ_KEY) => page.locator(`[data-testid="hq-billboard"][data-building="${key}"]`);
const noOverflow = async (page: Page) => expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
const shot = async (page: Page, name: string) => { if (SHOTS) { mkdirSync(SHOTS, { recursive: true }); await hq(page).screenshot({ path: `${SHOTS}/${name}.png` }); } };

/** The open workspace's four registry buildings plus one other workspace (no registry of its own, no presence): five buildings. */
const campus5 = { ...campusFixture, buildings: [campusFixture.buildings[0], campusFixture.buildings[2]] };

/** Three open tasks placed in ANUM HQ (one waiting for approval, one blocked), one in Tyre Pulse. */
function items() {
  const base = flowItems();
  const where = (id: string) => base.find((i) => i.key === id)!;
  const move = (key: string, building: string) => ({ ...where(key), location: { ...where(key).location!, building_id: building } });
  return [move('task:t_fail', 'campus-01-hq'), move('task:t_send', 'campus-01-hq'), move('task:t_plan', 'campus-01-hq'), where('lead:l_acme')] as ReturnType<typeof flowItems>;
}

async function open(page: Page, { flow = true, night = false, locale = 'en' }: { flow?: boolean; night?: boolean; locale?: 'en' | 'ar' } = {}) {
  await page.addInitScript((n) => { try { localStorage.setItem('anum.hq.light', n ? 'night' : 'day'); } catch { /* storage blocked */ } }, night);
  await useSavedLocale(page, locale);
  await mockHq(page, { layout: 'campus', campus: 'api', commerce: 'connected', tyre: 'connected', meetings: 'none' });
  await page.route('http://localhost:8000/api/v1/office/campus', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(campus5) }));
  const mock = await mockFlow(page, items());
  if (!flow) mock.failWith(500);
  await page.goto('/#office-preview');
  await sceneReady(page);
  await expect(hq(page).locator('.hqSceneWrap')).toHaveAttribute('data-billboards', '5', { timeout: 60_000 });
  return mock;
}

async function select(page: Page, name: RegExp) {
  const button = hq(page).locator('.hqBuildingCard', { hasText: name }).first();
  await button.focus();
  await page.keyboard.press('Enter');
  await expect(card(page)).toHaveAttribute('data-selected', 'true');
  await page.waitForTimeout(1500);
}

test('a billboard is drawn for every building, the scene is not blank, and the card shows the real record values', async ({ page }) => {
  await open(page);
  const cards = hq(page).locator('[data-testid="hq-billboard"]');
  await expect(cards).toHaveCount(5);
  expect(new Set(await cards.evaluateAll((els) => els.map((e) => e.getAttribute('data-building')))).size).toBe(5);
  // Not blank: the canvas has many distinct colours.
  const png = await hq(page).locator('canvas.hqCanvas').screenshot();
  const spread = await page.evaluate(async (b64) => {
    const image = new Image(); image.src = `data:image/png;base64,${b64}`; await image.decode();
    const c = document.createElement('canvas'); c.width = image.width; c.height = image.height;
    const ctx = c.getContext('2d')!; ctx.drawImage(image, 0, 0);
    const d = ctx.getImageData(0, 0, c.width, c.height).data; const seen = new Set<number>();
    for (let i = 0; i < d.length; i += 4 * 11) seen.add(((d[i] >> 4) << 8) | ((d[i + 1] >> 4) << 4) | (d[i + 2] >> 4));
    return seen.size;
  }, png.toString('base64'));
  expect(spread).toBeGreaterThan(40);
  await select(page, /ANUM HQ/);
  const hqCard = card(page);
  await expect(hqCard).toBeVisible();
  await expect(hqCard.locator('.hqBbHead strong')).toHaveText('ANUM HQ');
  // A registry building has no owner-set type, so it says so instead of guessing one.
  await expect(hqCard.locator('[data-field="purpose"] dd')).toContainText('not set yet');
  await expect(hqCard.locator('[data-field="floors"] dd')).toHaveText('3 floors · 7 rooms');
  await expect(hqCard.locator('[data-field="departments"] dd')).toContainText('Marketing');
  for (const room of ['Reception', 'Meeting Room A', 'Command Center', '+2 more']) await expect(hqCard.locator('[data-field="roomNames"] dd')).toContainText(room);
  await expect(hqCard.locator('[data-field="work"] [data-part="open"] b')).toHaveText('3');
  await expect(hqCard.locator('[data-field="work"] [data-part="waiting"] b')).toHaveText('1');
  await expect(hqCard.locator('[data-field="work"] [data-part="blocked"] b')).toHaveText('1');
  await expect(hqCard.locator('[data-field="presenceTime"]')).toContainText(/Verified|Stale|not reported/);
  await expect(hqCard.getByRole('button', { name: 'Open ANUM HQ' })).toBeVisible();
  // Nothing from a task, an agent or a customer appears.
  const text = await hqCard.innerText();
  for (const secret of ['Reorder wiper', 'Send fleet quote', 'Plan October', 'Acme', 'Fleet Planner', 'Invoice Checker']) expect(text).not.toContain(secret);
  await shot(page, 'selected-1440');
});

test('a feed that is off shows Not connected, never a zero', async ({ page }) => {
  await open(page, { flow: false });
  // Tyre Pulse HQ is another workspace without presence: every live value is Not connected.
  const other = card(page, 'workspace_tyre');
  await other.locator('.hqBbHead').focus();
  await expect(other.locator('[data-field="presence"] dd')).toHaveText('Not connected');
  await expect(other.locator('[data-field="work"] dd')).toHaveText('Not connected');
  await expect(other.locator('[data-field="roomNames"] dd')).toHaveText('Not connected');
  await expect(other.locator('[data-field="presenceTime"]')).toHaveCount(0);
  // The open workspace's flow failed: its open work is Not connected too, while its registry facts still show.
  await select(page, /ANUM HQ/);
  await expect(card(page).locator('[data-field="work"] dd')).toHaveText('Not connected');
  await expect(card(page).locator('[data-field="work"] [data-part]')).toHaveCount(0);
  await expect(card(page).locator('[data-field="roomNames"] dd')).toContainText('Reception');
  await shot(page, 'feed-off');
});

test('Arabic: the card reads right to left with the Arabic texts', async ({ page }) => {
  await open(page, { locale: 'ar' });
  await select(page, /ANUM HQ/);
  const c = card(page);
  await expect(c).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
  await expect(c.locator('[data-field="purpose"] dt')).toHaveText('ما يفعله المبنى');
  await expect(c.locator('[data-field="work"] [data-part="open"]')).toContainText('مهام مفتوحة');
  await expect(c.getByRole('group')).toHaveCount(0);
  await expect(c).toHaveAttribute('aria-label', /لوحة إعلانات/);
  await noOverflow(page);
  await shot(page, 'arabic-1440');
});

/** The 3D canvas alone (cards and panels hidden) so the boards themselves can be inspected. */
async function bare(page: Page, name: string) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  await page.addStyleTag({ content: '.hqStage > :not(canvas){visibility:hidden !important}' });
  await page.waitForTimeout(1200);
  await hq(page).locator('canvas.hqCanvas').screenshot({ path: `${SHOTS}/${name}.png` });
}

test('day and night: the boards stay in the drawn scene (campus and framed on a building)', async ({ page }) => {
  test.setTimeout(480_000);
  // Two scenes always (the campus by day, a framed building by night); with BB_SHOTS set, more views for the screenshot review.
  const all: Array<[string, boolean, boolean, RegExp]> = [['bare-day-overview', false, false, /ANUM HQ/], ['bare-night-commerce', true, true, /ANUM 360/], ['bare-day-hq', false, true, /ANUM HQ/], ['bare-night-overview', true, false, /ANUM HQ/], ['bare-day-commerce', false, true, /ANUM 360/], ['bare-night-tyre', true, true, /^Tyre Pulse\./]];
  const scenes = SHOTS ? all : all.slice(0, 2);
  for (const [name, night, framed, which] of scenes) {
    const p = await page.context().newPage();
    await p.setViewportSize({ width: 1440, height: 900 });
    await open(p, { night });
    if (framed) {
      await hq(p).locator('.hqBuildingCard', { hasText: which }).first().focus();
      await p.keyboard.press('Enter');
      await p.waitForTimeout(1500);
      await hq(p).locator('.hqStage').focus();
      for (let i = 0; i < 3; i++) await p.keyboard.press('+');
      await p.waitForTimeout(1500);
    }
    expect(Number(await hq(p).locator('canvas.hqCanvas').getAttribute('data-triangles'))).toBeGreaterThan(40_000);
    await bare(p, name);
    await p.close();
  }
});

for (const [w, h] of SIZES) {
  test(`billboards fit ${w}x${h}: no sideways scroll, card inside the stage, controls stay reachable, no axe violations`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await open(page);
    await select(page, /ANUM HQ/);
    await noOverflow(page);
    const stage = (await hq(page).locator('.hqSceneWrap').boundingBox())!;
    const box = (await card(page).boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(stage.x - 1);
    expect(box.x + box.width).toBeLessThanOrEqual(stage.x + stage.width + 1);
    // The card is under the camera controls: the element at the centre of each control is never the card.
    for (const control of await hq(page).locator('.hqOverlayRight button, .hqOverlayBottom button').all()) {
      if (!(await control.isVisible())) continue;
      const b = (await control.boundingBox())!;
      const covered = await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.closest('.hqBillboard') != null, [b.x + b.width / 2, b.y + b.height / 2]);
      expect(covered).toBe(false);
    }
    const results = await new AxeBuilder({ page }).include('.hqLive').withTags(['wcag2a', 'wcag2aa']).analyze();
    expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
    await shot(page, `selected-${w}x${h}`);
  });
}
