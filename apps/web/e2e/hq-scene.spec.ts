import { expect, test, type Page } from '@playwright/test';
import { openHq } from './support/hqApi';

// F04 / F14 browser verification of the authenticated headquarters (HeadquartersLive).
// The sandbox Chromium renders WebGL through SwiftShader (software), see docs/ANUM_MINE_HQ_BASELINE_AUDIT.md.

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
  page.on('console', (message) => { if (message.type() === 'error') problems.push(`console: ${message.text()}`); });
  return problems;
}

test('WebGL: the scene draws a real frame from saved rooms and no fallback is shown', async ({ page }) => {
  const problems = watchConsole(page);
  await openHq(page);
  const canvas = page.locator('canvas.hqCanvas');
  await expect(canvas).toBeVisible();
  await expect(page.locator('.hqGpuFallback')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Select Alpha Room 1/ })).toBeVisible();
  await expect.poll(() => distinctColours(page, 'canvas.hqCanvas'), { timeout: 10_000 }).toBeGreaterThan(5);
  expect(problems).toEqual([]);
});

test.describe('room inspector (hidden below 768 px: issue register H-04)', () => {
test.use({ viewport: { width: 1280, height: 800 } });
test('no WebGL: the accessible floor renderer takes over and rooms stay usable', async ({ page }) => {
  const problems = watchConsole(page);
  await openHq(page, { noWebgl: true });
  await expect(page.locator('.hqGpuFallback')).toBeVisible();
  await expect(page.locator('.hqImmersiveStage')).toHaveCount(0);
  await page.getByRole('button', { name: /Alpha Room 2/ }).click();
  await expect(page.locator('.hqRoomDetails h2')).toHaveText('Alpha Room 2');
  expect(problems).toEqual([]);
});

});

test('GPU context loss switches to the fallback instead of a blank scene', async ({ page }) => {
  await openHq(page);
  await expect(page.locator('canvas.hqCanvas')).toBeVisible();
  await page.evaluate(() => {
    const canvas = document.querySelector('canvas.hqCanvas')!;
    canvas.dispatchEvent(new Event('webglcontextlost', { cancelable: true }));
  });
  await expect(page.locator('.hqGpuFallback')).toBeVisible();
  await expect(page.getByRole('status').filter({ hasText: /GPU context lost/ })).toBeVisible();
});

test('reduced motion: nothing in the headquarters keeps animating', async ({ page }) => {
  await openHq(page, { reducedMotion: true });
  await expect(page.locator('.hqLive')).toBeVisible();
  await expect(page.locator('canvas.hqCanvas')).toBeVisible();
  await page.waitForTimeout(500);
  const running = await page.evaluate(() => document.getAnimations()
    .filter((a) => a.playState === 'running' && (a.effect as KeyframeEffect | null)?.target?.closest?.('.hqLive'))
    .map((a) => (a as CSSAnimation).animationName ?? 'transition'));
  expect(running).toEqual([]);
});

test.describe('keyboard journey needs the inspector (H-04)', () => {
test.use({ viewport: { width: 1280, height: 800 } });
test('keyboard only: rooms can be chosen and the workstation panel opened and closed', async ({ page }) => {
  await openHq(page);
  await expect(page.getByRole('button', { name: /Alpha Room 1 office/ })).toBeVisible();
  const search = page.getByRole('textbox', { name: 'Find office room' });
  await search.focus();
  for (let i = 0; i < 5; i += 1) await page.keyboard.press('Tab'); // All, Assigned, Meetings, room 1, room 2
  await page.keyboard.press('Enter');
  await expect(page.locator('.hqRoomDetails h2')).toHaveText('Alpha Room 2');
  const ops = page.getByRole('button', { name: 'Live workstations' });
  await ops.focus();
  await expect(ops).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog', { name: 'Live Employee Workstations' })).toBeVisible();
  await page.getByRole('button', { name: 'Close office panel' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog', { name: 'Live Employee Workstations' })).toHaveCount(0);
});
});

test('the page never scrolls sideways at the current viewport', async ({ page }) => {
  await openHq(page);
  await expect(page.locator('.hqLive')).toBeVisible();
  const { sw, cw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
  expect(sw).toBeLessThanOrEqual(cw);
});
