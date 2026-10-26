import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { mockHq } from './support/hqCampusApi';

// The exterior look and the ambient motion of the 3D campus (docs/ANUM_MINE_HQ_BUILDING_LOOK.md, docs/ANUM_MINE_HQ_SCENERY_MOTION.md).
// It checks that day, blue hour and night each draw a real, different frame, that scenery motion is off by default on a
// software renderer and under reduced motion, and that it runs (vehicles, scene clock) when it is switched on.
// With LOOK_DIR set it also writes the screenshots used for the before and after comparison. The API is mocked, every name is fake.
const OUT = process.env.LOOK_DIR;
if (OUT) mkdirSync(OUT, { recursive: true });
const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
const hq = (page: Page) => page.locator('.hqLive');
const canvas = (page: Page) => hq(page).locator('canvas.hqCanvas');
type Look = 'day' | 'dusk' | 'night';

async function open(page: Page, look: Look, opts: { ambient?: 0 | 1 | 2; layout?: 'starter' | 'full' } = {}) {
  await page.addInitScript(([l, a]) => {
    try {
      localStorage.setItem('anum.hq.light', l === 'day' ? 'day' : 'night');
      localStorage.setItem('anum.hq.preset', l === 'dusk' ? 'dusk' : 'night');
      if (a !== null) localStorage.setItem('anum.hq.ambient', String(a));
    } catch { /* storage blocked */ }
  }, [look, opts.ambient ?? null] as const);
  await mockHq(page, { campus: 'api', layout: opts.layout ?? 'starter', commerce: 'connected', meetings: 'live' });
  await page.goto('/#office-preview');
  await expect(canvas(page)).toBeVisible();
  await expect(canvas(page)).toHaveAttribute('data-engine', /three/, { timeout: 60_000 });
  await expect.poll(async () => Number(await canvas(page).getAttribute('data-triangles')), { timeout: 60_000 }).toBeGreaterThan(40_000);
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

test('day, blue hour and night each draw a different frame', async ({ page }) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  const means: Record<string, [number, number, number]> = {};
  for (const look of ['day', 'dusk', 'night'] as const) {
    const p = await page.context().newPage();
    await p.setViewportSize({ width: 1440, height: 900 });
    await open(p, look);
    await p.waitForTimeout(2500);
    means[look] = await meanColour(p);
    await shot(p, `campus-${look}`);
    await p.close();
  }
  const lum = (c: [number, number, number]) => 0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2];
  expect(lum(means.day)).toBeGreaterThan(lum(means.dusk));
  expect(lum(means.dusk)).toBeGreaterThan(lum(means.night) - 2);
  // The blue hour is bluer than the day, and not the same picture as the night.
  expect(means.dusk[2] / (means.dusk[0] + 1)).toBeGreaterThan(means.day[2] / (means.day[0] + 1) - 0.05);
  expect(Math.abs(means.dusk[0] - means.night[0]) + Math.abs(means.dusk[1] - means.night[1]) + Math.abs(means.dusk[2] - means.night[2])).toBeGreaterThan(4);
});

test('scenery motion is off by default on a software renderer', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page, 'day');
  await page.waitForTimeout(2500);
  expect(await canvas(page).getAttribute('data-scenery-level')).toBe('0');
  const a = await canvas(page).getAttribute('data-scenery-clock');
  await page.waitForTimeout(1500);
  expect(await canvas(page).getAttribute('data-scenery-clock')).toBe(a);
});

test('scenery motion runs when switched on: traffic is placed and the scene clock advances', async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page, 'dusk', { ambient: 1 });
  await expect.poll(async () => Number(await canvas(page).getAttribute('data-scenery-vehicles')), { timeout: 30_000 }).toBeGreaterThan(5);
  await expect.poll(async () => Number(await canvas(page).getAttribute('data-scenery-frames')), { timeout: 60_000 }).toBeGreaterThan(5);
  const a = Number(await canvas(page).getAttribute('data-scenery-clock'));
  await expect.poll(async () => Number(await canvas(page).getAttribute('data-scenery-clock')), { timeout: 60_000 }).toBeGreaterThan(a + 0.4);
  await shot(page, 'campus-dusk-moving');
});

test('reduced motion freezes scenery motion even when it is switched on', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page, 'dusk', { ambient: 2 });
  await page.waitForTimeout(2500);
  expect(await canvas(page).getAttribute('data-scenery-level')).toBe('0');
  const a = await canvas(page).getAttribute('data-scenery-clock');
  await page.waitForTimeout(1500);
  expect(await canvas(page).getAttribute('data-scenery-clock')).toBe(a);
});

for (const [w, h] of SIZES) {
  test(`dusk campus fits ${w}x${h} without sideways scroll`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await open(page, 'dusk');
    await page.waitForTimeout(1500);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
    await shot(page, `campus-dusk-${w}x${h}`);
  });
}
