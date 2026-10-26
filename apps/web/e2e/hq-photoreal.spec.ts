import { expect, test, type Page } from '@playwright/test';
import { mockHq } from './support/hqCampusApi';

// The photographic look (docs/ANUM_MINE_HQ_PHOTOREAL.md): quality tiers, the effects that cost nothing per frame (baked ambient
// occlusion, contact discs) and the pin that makes the top tier reachable on a real GPU. CI runs a software rasteriser, which must
// stay on the lowest tier by itself: only a pin raises it, and tier 4 is only checked at its start (the first frame of the post
// stack takes minutes on SwiftShader, so no CI loop runs it). The API is mocked; every name is plainly fake.
const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
// Each test builds a whole campus on a software rasteriser: allow what the other campus specs get on a loaded runner.
test.describe.configure({ timeout: 240_000 });
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

async function open(page: Page, pin: string | null, fxOff?: string) {
  await page.addInitScript(([q, off]) => {
    try {
      localStorage.setItem('anum.hq.light', 'day');
      if (q) localStorage.setItem('anum.hq.quality', q);
      if (off) localStorage.setItem('anum.hq.fxoff', off);
    } catch { /* storage blocked */ }
  }, [pin, fxOff ?? null]);
  await mockHq(page, { campus: 'api', layout: 'starter', commerce: 'connected', meetings: 'live' });
  await page.goto('/#office-preview');
  await expect(canvas(page)).toBeVisible({ timeout: 60_000 });
  await expect(canvas(page)).toHaveAttribute('data-engine', /three/, { timeout: 180_000 });
}

const tier = (page: Page) => canvas(page).getAttribute('data-tier');
const fx = async (page: Page) => (await canvas(page).getAttribute('data-fx')) ?? '';

for (const [w, h] of SIZES) {
  test(`campus draws a non-blank frame on the software tier with baked occlusion at ${w}x${h}`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await open(page, null);
    // A software rasteriser starts at tier 0 on its own: no shadows, no post stack, no photographic effects.
    await expect.poll(() => tier(page), { timeout: 180_000 }).toBe('0');
    await expect.poll(() => fx(page), { timeout: 180_000 }).toContain('ao');
    expect(await fx(page)).not.toMatch(/gtao|dof|shafts|agx|pbr|cascade|lights/);
    await expect.poll(async () => Number(await canvas(page).getAttribute('data-triangles')), { timeout: 180_000 }).toBeGreaterThan(40_000);
    await expect.poll(() => distinctColours(page), { timeout: 180_000 }).toBeGreaterThan(60);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
  });
}

test('the quality pin sets the tier; a pinned software rasteriser gets baked occlusion but no campus detail; a bad pin is ignored', async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 600 });
  await open(page, '3');
  await expect.poll(() => tier(page), { timeout: 180_000 }).toBe('3');
  await expect.poll(() => fx(page), { timeout: 180_000 }).toContain('ao');
  expect(await canvas(page).getAttribute('data-shadow')).toBe('2048');
  // Roof detail and contact discs are for hardware: a software rasteriser never draws them, pinned or not.
  expect(await fx(page)).not.toMatch(/contact|gtao|pbr|agx/);
  const withAo = Number(await canvas(page).getAttribute('data-triangles'));

  const second = await page.context().newPage();
  await second.setViewportSize({ width: 800, height: 600 });
  await open(second, '3', 'ao');
  await expect.poll(() => tier(second), { timeout: 180_000 }).toBe('3');
  expect(await fx(second)).not.toMatch(/ao/);
  // Baked occlusion cuts the wide walls, so the same scene has more triangles with it.
  expect(Number(await canvas(second).getAttribute('data-triangles'))).toBeLessThan(withAo);
  await second.close();

  const bad = await page.context().newPage();
  await bad.setViewportSize({ width: 800, height: 600 });
  await open(bad, 'banana');
  await expect.poll(() => tier(bad), { timeout: 180_000 }).toBe('0');
  await bad.close();
});

test('pin 4 asks for the photographic extension on the ultra tier (checked at its start: the chunk loads, the pin is not read as a bad value)', async ({ page }) => {
  test.setTimeout(180_000);
  await page.setViewportSize({ width: 390, height: 844 });
  const chunks: string[] = [];
  page.on('request', (r) => { if (/hqPhotoFx/.test(r.url())) chunks.push(r.url()); });
  await open(page, '4');
  // The extension is not a registered tier: the picture is the ultra tier (3) until its first frame has compiled, then reports 4.
  await expect.poll(async () => ['3', '4'].includes((await tier(page)) ?? ''), { timeout: 180_000 }).toBe(true);
  await expect.poll(() => chunks.length, { timeout: 120_000 }).toBeGreaterThan(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
});
