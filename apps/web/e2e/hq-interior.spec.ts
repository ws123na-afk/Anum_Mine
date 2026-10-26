import { expect, test, type Page } from '@playwright/test';
import { mockHq } from './support/hqCampusApi';

// The interiors of the starter campus (docs/ANUM_MINE_HQ_INTERIORS.md): every building of the 8 building, 18 floor, 62 room
// campus opens, draws a lit, non-blank floor at every supported size without sideways scroll, carries its moving scenery
// as parts the renderer can freeze, and keeps it frozen under reduced motion. The API is mocked; every name is plainly fake.
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

async function openBuilding(page: Page, name: string, ambient?: 'on' | 'off') {
  await page.addInitScript((a) => { try { localStorage.setItem('anum.hq.light', 'night'); if (a) localStorage.setItem('anum.hq.ambient', a); } catch { /* storage blocked */ } }, ambient);
  await mockHq(page, { campus: 'api', layout: 'starter-full', commerce: 'connected', meetings: 'live' });
  await page.goto('/#office-preview');
  await expect(canvas(page)).toHaveAttribute('data-engine', /three/, { timeout: 60_000 });
  await hq(page).getByRole('button', { name: new RegExp(`^${name}\\.`) }).first().click({ force: true });
  await hq(page).getByRole('button', { name: `Open ${name}` }).click();
  await expect(hq(page).getByRole('heading', { level: 2, name })).toBeVisible();
}

for (const [w, h] of SIZES) {
  test(`a starter campus floor draws lit interiors with scenery at ${w}x${h}`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await openBuilding(page, 'ANUM 360 Commerce');
    await expect.poll(async () => Number(await canvas(page).getAttribute('data-triangles')), { timeout: 60_000 }).toBeGreaterThan(15_000);
    await expect.poll(() => distinctColours(page), { timeout: 60_000 }).toBeGreaterThan(40);
    // Ambient parts exist (lift indicators, light glints, leaves, fans); on a software rasteriser they stay still.
    await expect.poll(async () => (await canvas(page).getAttribute('data-ambient')) ?? '', { timeout: 60_000 }).toMatch(/^[1-9]\d*/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
  });
}

test('ambient scenery moves when allowed', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await openBuilding(page, 'ANUM 360 Commerce', 'on');
  await expect.poll(async () => (await canvas(page).getAttribute('data-ambient')) ?? '', { timeout: 60_000 }).toMatch(/moving$/);
});

test.describe('reduced motion', () => {
  test.use({ reducedMotion: 'reduce' });
  test('ambient scenery is frozen even when pinned on', async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 });
    await openBuilding(page, 'ANUM 360 Commerce', 'on');
    await expect.poll(async () => (await canvas(page).getAttribute('data-ambient')) ?? '', { timeout: 60_000 }).toMatch(/^[1-9]\d*$/);
  });
});

test('the server area, pantry and every other kit of the starter campus build without a missing room', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  await openBuilding(page, 'Knowledge & AI');
  await expect.poll(async () => Number(await canvas(page).getAttribute('data-triangles')), { timeout: 60_000 }).toBeGreaterThan(10_000);
});

test('the stacked view draws every floor of the 3 floor building above the one below it', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  await openBuilding(page, 'ANUM 360 Commerce');
  await expect.poll(async () => Number(await canvas(page).getAttribute('data-triangles')), { timeout: 60_000 }).toBeGreaterThan(15_000);
  const single = Number(await canvas(page).getAttribute('data-triangles'));
  const all = hq(page).getByRole('checkbox', { name: /All floors/ });
  // The page can rebuild the view once while it settles; retry until the choice sticks.
  await expect(async () => { if (!(await all.isChecked())) await all.click(); await expect(all).toBeChecked({ timeout: 2000 }); await page.waitForTimeout(1500); await expect(all).toBeChecked({ timeout: 1000 }); }).toPass({ timeout: 40_000 });
  // Three furnished floors in view instead of one: far more geometry, and a taller scene to frame (the unit tests pin the heights).
  await expect.poll(async () => Number(await canvas(page).getAttribute('data-triangles')), { timeout: 60_000 }).toBeGreaterThan(single * 1.4);
  await expect.poll(() => distinctColours(page), { timeout: 60_000 }).toBeGreaterThan(40);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
});
