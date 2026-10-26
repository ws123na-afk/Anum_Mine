import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { mockHq } from './support/hqCampusApi';

// The glass shows the real floors (docs/ANUM_MINE_HQ_GLASS_PLAN.md): the campus draws its registry buildings from their floor plans and the
// glass shader looks into the plan atlas. The API is mocked and every name is fake. With LOOK_DIR set it writes the screenshots.
const OUT = process.env.LOOK_DIR;
if (OUT) mkdirSync(OUT, { recursive: true });
const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
const hq = (page: Page) => page.locator('.hqLive');
const canvas = (page: Page) => hq(page).locator('canvas.hqCanvas');
type Look = 'day' | 'dusk' | 'night';

async function open(page: Page, look: Look, opts: { ambient?: 0 | 1 | 2; layout?: 'starter-full' | 'campus' | 'starter' } = {}) {
  await page.addInitScript(([l, a]) => {
    try {
      localStorage.setItem('anum.hq.light', l === 'day' ? 'day' : 'night');
      localStorage.setItem('anum.hq.preset', l === 'dusk' ? 'dusk' : 'night');
      if (a !== null) localStorage.setItem('anum.hq.ambient', String(a));
    } catch { /* storage blocked */ }
  }, [look, opts.ambient ?? null] as const);
  await mockHq(page, { campus: 'api', layout: opts.layout ?? 'starter-full', commerce: 'connected', meetings: 'live' });
  await page.goto('/#office-preview');
  await expect(canvas(page)).toBeVisible();
  await expect(canvas(page)).toHaveAttribute('data-engine', /three/, { timeout: 60_000 });
  await expect.poll(async () => Number(await canvas(page).getAttribute('data-triangles')), { timeout: 60_000 }).toBeGreaterThan(40_000);
}

const planOf = (page: Page) => canvas(page).getAttribute('data-glass-plan');
const wantedOf = (page: Page) => canvas(page).getAttribute('data-glass-plan-wanted');

async function planReady(page: Page) {
  await expect.poll(async () => (await wantedOf(page)) ?? '', { timeout: 60_000 }).not.toBe('');
  await expect.poll(async () => await planOf(page), { timeout: 60_000 }).toBe(await wantedOf(page));
}

async function meanColour(page: Page): Promise<[number, number, number]> {
  const png = await canvas(page).screenshot();
  return page.evaluate(async (b64) => {
    const image = new Image();
    image.src = `data:image/png;base64,${b64}`;
    await image.decode();
    const c = document.createElement('canvas');
    c.width = image.width; c.height = image.height;
    const ctx = c.getContext('2d')!;
    ctx.drawImage(image, 0, 0);
    const data = ctx.getImageData(0, 0, c.width, c.height).data;
    let r = 0, g = 0, b = 0, n = 0;
    for (let i = 0; i < data.length; i += 4 * 7) { r += data[i]; g += data[i + 1]; b += data[i + 2]; n += 1; }
    return [r / n, g / n, b / n] as [number, number, number];
  }, png.toString('base64'));
}

async function shot(page: Page, name: string) {
  if (!OUT) return;
  await page.addStyleTag({ content: '.hqStage > :not(canvas){visibility:hidden !important}' });
  await page.waitForTimeout(1500);
  await canvas(page).screenshot({ path: `${OUT}/${name}.png` });
}

/** Picks a building by its card: the camera flies to frame it (nothing else is clicked). */
async function frameBuilding(page: Page, name: RegExp) {
  await hq(page).getByRole('button', { name }).first().click();
  await page.waitForTimeout(3500);
}

/** Moves in with the zoom button, as a person does. */
async function zoomIn(page: Page, clicks: number) {
  for (let i = 0; i < clicks; i++) { await hq(page).getByRole('button', { name: /zoom in/i }).first().click(); await page.waitForTimeout(250); }
  await page.waitForTimeout(1500);
}

test('the campus draws its buildings from their floor plans and the glass atlas reaches the GPU', async ({ page }) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page, 'dusk');
  await planReady(page);
  expect((await wantedOf(page))!.length).toBeGreaterThan(4);
  const mean = await meanColour(page);
  expect(mean[0] + mean[1] + mean[2]).toBeGreaterThan(40);
  await shot(page, 'plan-campus-dusk');
});

for (const look of ['day', 'night'] as const) {
  test(`${look} campus with plans is a real frame`, async ({ page }) => {
    test.setTimeout(240_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page, look);
    await planReady(page);
    await page.waitForTimeout(1500);
    const mean = await meanColour(page);
    expect(mean[0] + mean[1] + mean[2]).toBeGreaterThan(40);
    await shot(page, `plan-campus-${look}`);
  });
}

for (const look of ['dusk', 'night'] as const) {
  test(`a close view of the headquarters at ${look} (rich path switched on) is a real frame`, async ({ page }) => {
    test.setTimeout(240_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page, look, { ambient: 1 });
    await planReady(page);
    await frameBuilding(page, /^ANUM HQ\./);
    await shot(page, `plan-closeup-hq-${look}-rich`);
    await zoomIn(page, 4);
    await shot(page, `plan-closer-hq-${look}-rich`);
    const mean = await meanColour(page);
    expect(mean[0] + mean[1] + mean[2]).toBeGreaterThan(40);
  });
}

test('changing the layout updates the plan atlas without a reload', async ({ page }) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page, 'dusk');
  await planReady(page);
  const before = await wantedOf(page);
  await page.evaluate(() => { (window as unknown as { __kept: boolean }).__kept = true; });
  // The registry now holds a moved room: the next scene refresh draws a different plan.
  await page.route('http://localhost:8000/api/v1/office-spaces/rooms', async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as { items: Array<Record<string, unknown>>; next_cursor: null };
    body.items = body.items.map((r) => (r.id === 'campus-01-hq-facilities' ? { ...r, scene_x: 5, scene_width: 9 } : r));
    await route.fulfill({ response, body: JSON.stringify(body) });
  });
  await expect.poll(async () => await wantedOf(page), { timeout: 90_000 }).not.toBe(before);
  await expect.poll(async () => await planOf(page), { timeout: 30_000 }).toBe(await wantedOf(page));
  expect(await page.evaluate(() => (window as unknown as { __kept?: boolean }).__kept)).toBe(true);
});

for (const [w, h] of SIZES) {
  test(`dusk campus with plans fits ${w}x${h} without sideways scroll`, async ({ page }) => {
    test.setTimeout(240_000);
    await page.setViewportSize({ width: w, height: h });
    await open(page, 'dusk');
    await planReady(page);
    await page.waitForTimeout(1000);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
    const mean = await meanColour(page);
    expect(mean[0] + mean[1] + mean[2]).toBeGreaterThan(40);
    await shot(page, `plan-campus-dusk-${w}x${h}`);
  });
}
