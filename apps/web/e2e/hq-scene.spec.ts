import { expect, test, type Page } from '@playwright/test';
import { openHq } from './support/hqApi';

// F04 / F14 browser verification of the authenticated headquarters (HeadquartersLive).
// The sandbox Chromium renders WebGL through SwiftShader (software), see docs/ANUM_MINE_HQ_BASELINE_AUDIT.md.
const hq = (page: Page) => page.locator('.hqLive');

/** Counts distinct colours in a screenshot of the element: a cleared canvas has one, a drawn scene many. */
async function distinctColours(page: Page, selector: string): Promise<number> {
  const png = await page.locator(selector).screenshot();
  return page.evaluate(async (b64) => {
    const image = new Image();
    image.src = `data:image/png;base64,${b64}`;
    await image.decode();
    const canvas = document.createElement('canvas');
    canvas.width = image.width; canvas.height = image.height;
    const ctx = canvas.getContext('2d')!;
    ctx.drawImage(image, 0, 0);
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    const seen = new Set<number>();
    for (let i = 0; i < data.length; i += 4 * 7) seen.add((data[i] << 16) | (data[i + 1] << 8) | data[i + 2]);
    return seen.size;
  }, png.toString('base64'));
}

function watchConsole(page: Page): string[] {
  const problems: string[] = [];
  page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`));
  // A missing optional resource in the mocked app is not a scene error; scripts and WebGL errors are.
  page.on('console', (message) => { if (message.type() === 'error' && !/Failed to load resource/.test(message.text())) problems.push(`console: ${message.text()}`); });
  return problems;
}

async function enterBuilding(page: Page) {
  await hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click();
  await hq(page).getByRole('button', { name: 'Open Al Noor Fleet HQ' }).click();
  await expect(hq(page).getByRole('heading', { level: 2, name: 'Al Noor Fleet HQ' })).toBeVisible();
}

test('WebGL: the scene draws a real frame from saved rooms and no fallback is shown', async ({ page }) => {
  const problems = watchConsole(page);
  await openHq(page);
  const canvas = page.locator('canvas.hqCanvas');
  await expect(canvas).toBeVisible();
  await expect(hq(page).getByText(/3D is not available in this browser/)).toHaveCount(0);
  await expect(hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ })).toBeAttached();
  await expect.poll(() => distinctColours(page, 'canvas.hqCanvas'), { timeout: 60_000 }).toBeGreaterThan(5);
  await enterBuilding(page);
  await expect(hq(page).getByRole('button', { name: /^Alpha Room 1/ })).toBeVisible();
  await expect.poll(() => distinctColours(page, 'canvas.hqCanvas'), { timeout: 60_000 }).toBeGreaterThan(5);
  expect(problems).toEqual([]);
});

test.describe('room panel and list at desktop size', () => {
  test.use({ viewport: { width: 1280, height: 800 } });
  test('no WebGL: the list and the 2D floor plan take over and rooms stay usable', async ({ page }) => {
    // three.js logs its own "Error creating WebGL context" when the browser refuses a context: that is the case under test.
    const problems = watchConsole(page);
    await openHq(page, { noWebgl: true });
    await expect(hq(page).getByText(/3D is not available in this browser/)).toBeVisible();
    await expect(hq(page).locator('canvas.hqCanvas')).toHaveCount(0);
    const list = hq(page).getByRole('region', { name: 'All buildings and rooms' });
    await expect(list).toBeVisible();
    await list.getByRole('button', { name: /Alpha Room 2/ }).click();
    await expect(hq(page).getByRole('complementary', { name: 'Room Alpha Room 2' })).toBeVisible();
    expect(problems.filter((p) => !/Error creating WebGL context/.test(p))).toEqual([]);
  });
});

test('GPU context loss is announced and, if the GPU does not come back, the list takes over instead of a blank scene', async ({ page }) => {
  await openHq(page);
  const canvas = page.locator('canvas.hqCanvas');
  await expect(canvas).toBeVisible();
  await expect(canvas).toHaveAttribute('data-engine', /three/, { timeout: 60_000 });
  await page.evaluate(() => {
    const el = document.querySelector('canvas.hqCanvas') as HTMLCanvasElement;
    const gl = (el.getContext('webgl2') ?? el.getContext('webgl')) as WebGL2RenderingContext;
    (gl.getExtension('WEBGL_lose_context') as { loseContext(): void }).loseContext();
  });
  await expect(hq(page).getByText('Restoring the 3D view...')).toBeVisible();
  await expect(hq(page).getByText(/3D is not available in this browser/)).toBeVisible({ timeout: 15_000 });
  await expect(hq(page).getByRole('region', { name: 'All buildings and rooms' })).toBeVisible();
});

test('reduced motion: nothing in the headquarters keeps animating', async ({ page }) => {
  await openHq(page, { reducedMotion: true });
  await expect(hq(page)).toBeVisible();
  await expect(page.locator('canvas.hqCanvas')).toBeVisible();
  await expect(hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ })).toBeAttached();
  await page.waitForTimeout(500);
  const running = await page.evaluate(() => document.getAnimations()
    .filter((a) => a.playState === 'running' && (a.effect as KeyframeEffect | null)?.target?.closest?.('.hqLive'))
    .map((a) => (a as CSSAnimation).animationName ?? 'transition'));
  expect(running).toEqual([]);
});

test.describe('keyboard journey', () => {
  test.use({ viewport: { width: 1280, height: 800 } });
  test('keyboard only: a building and a room can be opened, and the workstation panel opened and closed', async ({ page }) => {
    await openHq(page);
    const card = hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ });
    await expect(card).toBeVisible();
    await card.focus();
    await page.keyboard.press('Enter'); // select
    await hq(page).getByRole('button', { name: 'Open Al Noor Fleet HQ' }).focus();
    await page.keyboard.press('Enter'); // open
    const room = hq(page).getByRole('button', { name: /^Alpha Room 2/ });
    await expect(room).toBeVisible();
    await room.focus();
    await page.keyboard.press('Enter');
    await expect(hq(page).getByRole('complementary', { name: 'Room Alpha Room 2' })).toBeVisible();
    const tools = hq(page).getByRole('button', { name: 'Office tools' });
    await tools.focus();
    await expect(tools).toBeFocused();
    await page.keyboard.press('Enter');
    await hq(page).getByRole('button', { name: 'Live workstations' }).focus();
    await page.keyboard.press('Enter');
    const dialog = hq(page).getByRole('dialog', { name: 'Office tools' });
    await expect(dialog).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
  });
});

test('the page never scrolls sideways at the current viewport', async ({ page }) => {
  await openHq(page);
  await expect(hq(page)).toBeVisible();
  const { sw, cw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
  expect(sw).toBeLessThanOrEqual(cw);
});
