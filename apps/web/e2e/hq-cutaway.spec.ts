import { expect, test, type Page } from '@playwright/test';
import { mockHq } from './support/hqCampusApi';
import { useSavedLocale } from './support/workspaceApi';

// The cutaway section of the building view (docs/ANUM_MINE_HQ_CUTAWAY.md): open by default with every floor drawn, the walls facing the
// camera and the roof taken away, a Cutaway/Closed toggle, 90 degree rotate snaps that re-pick the cut walls, and the same on a phone.
// The API is mocked; every name is plainly fake.
test.describe.configure({ timeout: 240_000 });
const hq = (page: Page) => page.locator('.hqLive');
const canvas = (page: Page) => hq(page).locator('canvas.hqCanvas');
const wrap = (page: Page) => hq(page).locator('.hqSceneWrap');

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

async function openBuilding(page: Page, name = 'ANUM 360 Commerce', light: 'day' | 'night' = 'night') {
  await page.addInitScript((l) => { try { localStorage.setItem('anum.hq.light', l); } catch { /* storage blocked */ } }, light);
  await mockHq(page, { campus: 'api', layout: 'starter-full', commerce: 'connected', meetings: 'live' });
  await page.goto('/#office-preview');
  await expect(canvas(page)).toHaveAttribute('data-engine', /three/, { timeout: 90_000 });
  // Buildings are entered from the list, whose Open button is reachable in every language and at every size.
  await hq(page).getByRole('button', { name: /^(List|قائمة)$/ }).click();
  await hq(page).locator('.hqListBuilding', { has: page.getByRole('heading', { level: 3, name, exact: true }) }).locator('.hqListRow > button').click();
  await expect(wrap(page)).toBeVisible();
  await expect.poll(async () => Number(await canvas(page).getAttribute('data-triangles')), { timeout: 60_000 }).toBeGreaterThan(15_000);
}

const noOverflow = async (page: Page) => expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);

test('the building opens as a cutaway: all floors, the front walls and the roof removed, the scene drawn', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  await openBuilding(page);
  await expect(hq(page).getByRole('button', { name: 'Cutaway', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(hq(page).getByRole('button', { name: 'Closed', exact: true })).toHaveAttribute('aria-pressed', 'false');
  await expect(wrap(page)).toHaveAttribute('data-facade', 'cutaway');
  await expect(wrap(page)).toHaveAttribute('data-roof-open', 'true');
  await expect(wrap(page)).toHaveAttribute('data-cut-sides', 'south east');
  await expect(wrap(page)).toHaveAttribute('data-stack', 'true');
  await expect(hq(page).getByRole('checkbox', { name: /All floors/ })).toBeChecked();
  await expect.poll(() => distinctColours(page), { timeout: 60_000 }).toBeGreaterThan(40);
  await noOverflow(page);
});

test('Closed puts the walls and the roof back, says so, and Cutaway takes them away again', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  await openBuilding(page);
  const closed = hq(page).getByRole('button', { name: 'Closed', exact: true });
  await closed.focus();
  await page.keyboard.press('Enter');
  await expect(closed).toHaveAttribute('aria-pressed', 'true');
  await expect(wrap(page)).toHaveAttribute('data-facade', 'closed');
  await expect(wrap(page)).toHaveAttribute('data-roof-open', 'false');
  await expect(wrap(page)).toHaveAttribute('data-cut-sides', '');
  await expect(page.getByTestId('hq-facade-live')).toContainText('Closed view');
  await expect.poll(() => distinctColours(page), { timeout: 60_000 }).toBeGreaterThan(30);
  await hq(page).getByRole('button', { name: 'Cutaway', exact: true }).click();
  await expect(wrap(page)).toHaveAttribute('data-facade', 'cutaway');
  await expect(page.getByTestId('hq-facade-live')).toContainText('Cutaway view');
});

test('rotating snaps 90 degrees and re-picks the cut walls; four turns come back', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  await openBuilding(page);
  const yaw = async () => Number(await wrap(page).getAttribute('data-camera-yaw'));
  const sides = ['east north', 'north west', 'south west', 'south east'];
  const yaws = [125, 215, 305, 35];
  for (let i = 0; i < 4; i++) {
    await hq(page).getByRole('button', { name: 'Rotate right' }).click();
    await expect.poll(yaw, { timeout: 5000 }).toBeCloseTo(yaws[i], 0);
    expect([...(await wrap(page).getAttribute('data-cut-sides'))!.split(' ')].sort().join(' ')).toBe(sides[i].split(' ').sort().join(' '));
  }
  await hq(page).getByRole('button', { name: 'Rotate left' }).click();
  await expect.poll(yaw, { timeout: 5000 }).toBeCloseTo(305, 0);
  await expect(canvas(page)).toBeVisible();
});

test('reduced motion turns at once', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1366, height: 768 });
  await openBuilding(page);
  await hq(page).getByRole('button', { name: 'Rotate right' }).click();
  expect(Number(await wrap(page).getAttribute('data-camera-yaw'))).toBeCloseTo(125, 0);
});

test('the floor overview looks straight down: the floors above are out of it and the roof is open even in Closed', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  await openBuilding(page);
  await hq(page).getByRole('button', { name: 'Closed', exact: true }).click();
  await hq(page).getByRole('button', { name: /whole floor from above/ }).click();
  await expect(wrap(page)).toHaveAttribute('data-stack', 'false');
  await expect(wrap(page)).toHaveAttribute('data-roof-open', 'true');
  await hq(page).locator('.hqStage').focus();
  await page.keyboard.press('Escape');
  await expect(wrap(page)).toHaveAttribute('data-stack', 'true');
  await expect(wrap(page)).toHaveAttribute('data-facade', 'closed');
});

test('All floors off draws the open floor alone, and the floor rail still moves between floors', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  await openBuilding(page);
  const all = hq(page).getByRole('checkbox', { name: /All floors/ });
  const stacked = Number(await canvas(page).getAttribute('data-triangles'));
  await all.uncheck();
  await expect(wrap(page)).toHaveAttribute('data-stack', 'false');
  await expect.poll(async () => Number(await canvas(page).getAttribute('data-triangles')), { timeout: 30_000 }).toBeLessThan(stacked * 0.8);
  await page.keyboard.press('Escape');
  await hq(page).getByRole('button', { name: 'Floor above' }).last().click();
  await expect(page.getByTestId('hq-floor-count')).toHaveText('2 / 3');
  await all.check();
  await expect(wrap(page)).toHaveAttribute('data-stack', 'true');
});

for (const [w, h] of [[1440, 900], [1024, 768], [768, 1024], [390, 844]] as const) {
  test(`the cutaway fits and draws at ${w}x${h}`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await openBuilding(page, 'ANUM HQ', 'day');
    await expect.poll(() => distinctColours(page), { timeout: 60_000 }).toBeGreaterThan(40);
    await noOverflow(page);
    const stats = await canvas(page).evaluate((el) => ({ ...(el as HTMLCanvasElement).dataset }));
    expect(Number(stats.drawMs ?? 0)).toBeLessThan(2500);
    const toggle = hq(page).getByRole('button', { name: 'Cutaway', exact: true });
    await expect(toggle).toBeVisible();
    const box = (await toggle.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(w);
  });
}

test('in Arabic the toggle is labelled, the layout is right to left and the 3D scene is not mirrored', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await page.setViewportSize({ width: 1366, height: 768 });
  await openBuilding(page, 'ANUM 360 Commerce');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(hq(page).getByRole('button', { name: 'مقطع', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(hq(page).getByRole('button', { name: 'مغلق', exact: true })).toBeVisible();
  await expect(wrap(page)).toHaveAttribute('data-cut-sides', 'south east');
  await hq(page).getByRole('button', { name: 'مغلق', exact: true }).click();
  await expect(page.getByTestId('hq-facade-live')).toContainText('العرض المغلق');
  await noOverflow(page);
});
