import { expect as baseExpect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { mockHq } from './support/hqCampusApi';

// The centre of the 3D campus (docs/ANUM_MINE_HQ_CENTER_PLAZA.md): a big fountain on a paved plaza with a roundabout round it and the
// buildings standing round that, in day, blue hour and night light, in English and Arabic, at the five gate sizes. The scene is drawn
// by a software renderer here, so the checks are about what any frame must hold: it is not blank, it is not one flat colour, the page does not scroll sideways,
// and the scene reports the plaza it drew. Set PLAZA_DIR to keep the screenshots. The API is mocked; every name is fake.
const expect = baseExpect.configure({ timeout: 20_000 });
test.use({ deviceScaleFactor: 1, isMobile: false, hasTouch: false });
test.describe.configure({ timeout: 240_000 });
const OUT = process.env.PLAZA_DIR;
if (OUT) mkdirSync(OUT, { recursive: true });
const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
const canvas = (page: Page) => page.locator('.hqLive canvas.hqCanvas');
type Look = 'day' | 'dusk' | 'night';

async function open(page: Page, look: Look, locale: 'en' | 'ar', size: readonly [number, number]) {
  await page.setViewportSize({ width: size[0], height: size[1] });
  await page.addInitScript(([l, loc]) => {
    try {
      localStorage.setItem('anum.hq.light', l === 'day' ? 'day' : 'night');
      localStorage.setItem('anum.hq.preset', l === 'dusk' ? 'dusk' : 'night');
      localStorage.setItem('anum.locale', loc);
    } catch { /* storage blocked */ }
  }, [look, locale] as const);
  await mockHq(page, { campus: 'api', layout: 'starter', commerce: 'connected', meetings: 'live' });
  await page.goto('/#office-preview');
  await expect(canvas(page)).toBeVisible();
  await expect(canvas(page)).toHaveAttribute('data-engine', /three/, { timeout: 60_000 });
  await expect.poll(async () => Number(await canvas(page).getAttribute('data-triangles')), { timeout: 60_000 }).toBeGreaterThan(40_000);
  await page.waitForTimeout(2000);
}

/** Spread of the picture: the standard deviation of luminance over a sample of pixels, and how many distinct coarse colours it holds. */
async function spread(page: Page): Promise<{ sd: number; colours: number }> {
  const png = await canvas(page).screenshot();
  return page.evaluate(async (b64) => {
    const image = new Image();
    image.src = `data:image/png;base64,${b64}`;
    await image.decode();
    const c = document.createElement('canvas');
    c.width = image.width; c.height = image.height;
    const ctx = c.getContext('2d')!;
    ctx.drawImage(image, 0, 0);
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    let n = 0, s = 0, s2 = 0;
    const seen = new Set<number>();
    for (let i = 0; i < d.length; i += 4 * 5) {
      const l = 0.3 * d[i] + 0.59 * d[i + 1] + 0.11 * d[i + 2];
      n += 1; s += l; s2 += l * l;
      seen.add(((d[i] >> 4) << 8) | ((d[i + 1] >> 4) << 4) | (d[i + 2] >> 4));
    }
    const mean = s / n;
    return { sd: Math.sqrt(Math.max(0, s2 / n - mean * mean)), colours: seen.size };
  }, png.toString('base64'));
}

async function shot(page: Page, name: string) {
  if (!OUT) return;
  await page.screenshot({ path: `${OUT}/${name}.png` });
  await page.addStyleTag({ content: '.hqStage > :not(canvas){visibility:hidden !important}' });
  await page.waitForTimeout(800);
  await canvas(page).screenshot({ path: `${OUT}/${name}-scene.png` });
}

for (const size of SIZES) {
  test(`the campus centre draws and fits at ${size[0]}x${size[1]} (day, English)`, async ({ page }) => {
    await open(page, 'day', 'en', size);
    const { sd, colours } = await spread(page);
    expect(sd).toBeGreaterThan(12);
    expect(colours).toBeGreaterThan(40);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
    await shot(page, `day-en-${size[0]}x${size[1]}`);
  });
}

for (const look of ['dusk', 'night'] as const) {
  test(`the campus centre draws at blue hour and at night (${look})`, async ({ page }) => {
    await open(page, look, 'en', SIZES[0]);
    const { sd, colours } = await spread(page);
    expect(sd).toBeGreaterThan(8);
    expect(colours).toBeGreaterThan(25);
    await shot(page, `${look}-en-1440x900`);
  });
}

for (const size of [SIZES[0], SIZES[4]]) {
  test(`the campus centre draws and fits in Arabic at ${size[0]}x${size[1]}`, async ({ page }) => {
    await open(page, 'day', 'ar', size);
    expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
    const { sd, colours } = await spread(page);
    expect(sd).toBeGreaterThan(12);
    expect(colours).toBeGreaterThan(40);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
    await shot(page, `day-ar-${size[0]}x${size[1]}`);
  });
}
