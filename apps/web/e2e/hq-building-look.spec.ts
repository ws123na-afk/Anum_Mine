import { expect, test, type Page } from '@playwright/test';
import { mockHq } from './support/hqCampusApi';

// The look of the 3D campus and building (docs/ANUM_MINE_HQ_BUILDING_LOOK.md): at every supported size the scene draws a
// real, non-blank frame with the expected amount of geometry (campus: gardens, trees, globe plaza; building: the
// furnished floors) and the page does not scroll sideways. The API is mocked; every name is plainly fake.
const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
const hq = (page: Page) => page.locator('.hqLive');
const canvas = (page: Page) => hq(page).locator('canvas.hqCanvas');

async function distinctColours(page: Page): Promise<number> {
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
    const seen = new Set<number>();
    for (let i = 0; i < data.length; i += 4 * 5) seen.add(((data[i] >> 3) << 10) | ((data[i + 1] >> 3) << 5) | (data[i + 2] >> 3));
    return seen.size;
  }, png.toString('base64'));
}

async function open(page: Page, layout: 'starter' | 'full', light: 'day' | 'night') {
  await page.addInitScript((l) => { try { localStorage.setItem('anum.hq.light', l); } catch { /* storage blocked */ } }, light);
  await mockHq(page, { campus: 'api', layout, commerce: 'connected', meetings: 'live' });
  await page.goto('/#office-preview');
  await expect(canvas(page)).toBeVisible();
  await expect(canvas(page)).toHaveAttribute('data-engine', /three/, { timeout: 60_000 });
}

for (const [w, h] of SIZES) {
  test(`campus draws gardens, trees and the globe plaza at ${w}x${h}`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await open(page, 'starter', 'day');
    // A starter campus (8 buildings) with gardens: far more geometry than the bare buildings alone.
    await expect.poll(async () => Number(await canvas(page).getAttribute('data-triangles')), { timeout: 60_000 }).toBeGreaterThan(40_000);
    await expect.poll(() => distinctColours(page), { timeout: 60_000 }).toBeGreaterThan(60);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
  });
}

for (const [w, h] of SIZES) {
  test(`building floor draws a lit, furnished scene at ${w}x${h} at night`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await open(page, 'full', 'night');
    await hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click({ force: true });
    await hq(page).getByRole('button', { name: 'Open Al Noor Fleet HQ' }).click();
    await expect(hq(page).getByRole('heading', { level: 2, name: 'Al Noor Fleet HQ' })).toBeVisible();
    await expect.poll(async () => Number(await canvas(page).getAttribute('data-triangles')), { timeout: 60_000 }).toBeGreaterThan(20_000);
    await expect.poll(() => distinctColours(page), { timeout: 60_000 }).toBeGreaterThan(40);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
  });
}
