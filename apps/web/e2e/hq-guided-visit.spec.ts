import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { mockHq } from './support/hqCampusApi';
import { sceneReady } from './support/hqScene';
import { useSavedLocale } from './support/workspaceApi';

// The guided visit of the headquarters (docs/ANUM_MINE_EXECUTIVE_PA.md#guided-tour): every stop is a room from
// GET /executive-pa/tour (the buildings, floors and rooms the person can access), described from the registry
// only, in the page language, with no staff and no example text. The API is mocked at http://localhost:8000.
const hq = (page: Page) => page.locator('.hqLive');
const room = (page: Page, name: string) => hq(page).getByRole('complementary', { name: `Room ${name}` });
const visit = (page: Page) => page.getByRole('dialog', { name: /Your guided office visit|جولتك الإرشادية في المكتب/ });
test.use({ deviceScaleFactor: 1, isMobile: false, hasTouch: false });
test.describe.configure({ timeout: 240_000 });

const stop = (room_id: string, room_name: string, floor_id: string, floor_name: string, level: number, room_kind: string, capacity: number, desks: number) =>
  ({ building_id: 'bld_hq', building_name: 'Al Noor Fleet HQ', floor_id, floor_name, level, room_id, room_name, room_kind, capacity, desks });
const STOPS = [stop('room_reception', 'Reception', 'flr_0', 'Ground floor', 0, 'reception', 3, 0), stop('room_care', 'Customer Care', 'flr_0', 'Ground floor', 0, 'office', 6, 2), stop('room_fin', 'Finance', 'flr_1', 'First floor', 1, 'office', 4, 1)];

type Tour = 'ready' | 'empty' | 'not_connected' | 'failing';
async function openVisit(page: Page, tour: Tour, size = { width: 1440, height: 900 }) {
  await page.addInitScript(() => { try { localStorage.setItem('anum.hq.light', 'day'); } catch { /* storage blocked */ } });
  await mockHq(page, { campus: 'api', commerce: 'connected', meetings: 'live' });
  const seen = { tourReads: 0 };
  await page.route('http://localhost:8000/api/v1/executive-pa/tour', async (route) => {
    seen.tourReads += 1;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (tour === 'failing') return json({ error: { code: 'unavailable', message: 'The registry is down' } }, 503);
    if (tour === 'empty') return json({ status: 'empty', stops: [] });
    if (tour === 'not_connected') return json({ status: 'not_connected', stops: [] });
    return json({ status: 'ready', stops: STOPS });
  });
  await page.setViewportSize(size);
  await page.goto('/#office-preview');
  await expect(hq(page).getByRole('heading', { level: 1 })).toBeVisible();
  await sceneReady(page);
  await expect(hq(page).locator('.hqBuildingCard').first()).toBeAttached({ timeout: 60_000 });
  await hq(page).getByRole('button', { name: /^(Office tools|أدوات المكتب)/ }).click();
  await page.getByRole('button', { name: /^(Guided visit|جولة إرشادية)$/ }).click();
  await expect(visit(page)).toBeVisible();
  return seen;
}
// The scene's label cards are placed on the next drawn frame after a resize, so the width settles a moment after `setViewportSize`: wait for it
// to settle (a persistent overflow still fails) instead of reading it in the middle of the move.
const noOverflow = async (page: Page) => expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), { timeout: 15_000, intervals: [100, 250, 500, 1000] }).toBeLessThanOrEqual(0);

test('the visit walks the real rooms only, describes them from the registry and moves the view', async ({ page }) => {
  await openVisit(page, 'ready');
  const v = visit(page);
  await expect(v.getByRole('listitem')).toHaveCount(3);
  for (const name of ['Reception', 'Customer Care', 'Finance']) await expect(v.getByRole('button', { name: new RegExp(name) })).toBeVisible();
  // Nothing is invented: no example roles, no "illustrative" staff line, no demo disclaimer.
  await expect(v).not.toContainText(/illustrative|example roles|Preview mode|Sales Office/i);
  await expect(v).toContainText('Every stop is a room stored in the office registry.');
  await v.getByRole('button', { name: 'Begin visit' }).click();
  await expect(v).toContainText('Welcome to Reception. Ground floor, Al Noor Fleet HQ. Type: reception. Desks: 0. Capacity: 3.');
  await expect(room(page, 'Reception')).toBeVisible({ timeout: 30_000 });
  await v.getByRole('button', { name: 'Next stop' }).click();
  await expect(v).toContainText('Welcome to Customer Care. Ground floor, Al Noor Fleet HQ. Type: office. Desks: 2. Capacity: 6.');
  await expect(room(page, 'Customer Care')).toBeVisible({ timeout: 30_000 });
  // A stop on another floor switches the floor.
  await v.getByRole('button', { name: /Finance/ }).click();
  await expect(room(page, 'Finance')).toBeVisible({ timeout: 30_000 });
  await v.getByRole('button', { name: 'Show visit history (4)' }).click();
  await expect(v.getByText('Moved to Finance')).toBeVisible();
  // Finance is the last stop, so the main button now finishes the visit.
  await v.getByRole('button', { name: 'Finish visit' }).click();
  await expect(v).toContainText('That was the last stop.');
  await expect(v.getByRole('button', { name: 'Restart' })).toBeVisible();
  await noOverflow(page);
});

test('states: loading failure with retry, no rooms, registry not connected', async ({ page }) => {
  await openVisit(page, 'failing');
  await expect(visit(page).getByRole('alert')).toContainText('Could not build the visit');
  await expect(visit(page).getByRole('button', { name: 'Retry' })).toBeVisible();
  await page.unrouteAll();
  // Leave the page first so its WebGL context is released; a reload would build a second scene with no API mocks while the next visit starts.
  await page.goto('about:blank');
  await openVisit(page, 'empty');
  await expect(visit(page).getByRole('status').filter({ hasText: 'There are no rooms to visit yet.' })).toBeVisible();
  await expect(visit(page).getByRole('button', { name: 'Begin visit' })).toHaveCount(0);
  await page.unrouteAll();
  // Leave the page first so its WebGL context is released; a reload would build a second scene with no API mocks while the next visit starts.
  await page.goto('about:blank');
  await openVisit(page, 'not_connected');
  await expect(visit(page).getByRole('status').filter({ hasText: 'The office registry is not available on this server.' })).toBeVisible();
});

test('Arabic: the visit and its narration language follow the page, right to left', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await page.addInitScript(() => {
    const spoken: { text: string; lang: string }[] = [];
    (window as unknown as { __spoken: typeof spoken }).__spoken = spoken;
    Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: { speak: (u: SpeechSynthesisUtterance) => spoken.push({ text: u.text, lang: u.lang }), cancel: () => undefined, getVoices: () => [], addEventListener: () => undefined, removeEventListener: () => undefined } });
  });
  await openVisit(page, 'ready');
  const v = visit(page);
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await v.getByRole('button', { name: 'السرد متوقف' }).click();
  await v.getByRole('button', { name: 'بدء الجولة' }).click();
  const plain = async () => (await v.innerText()).replace(/[\u2068\u2069]/g, '');
  await expect.poll(plain).toContain('مرحبًا بك في Reception. Ground floor، Al Noor Fleet HQ. النوع: استقبال. المكاتب: 0. السعة: 3.');
  const spoken = await page.evaluate(() => (window as unknown as { __spoken: { text: string; lang: string }[] }).__spoken);
  expect(spoken.at(-1)).toMatchObject({ lang: 'ar-SA' });
  expect(spoken.at(-1)?.text).toBe('مرحبًا بك في Reception. Ground floor، Al Noor Fleet HQ. النوع: استقبال. المكاتب: 0. السعة: 3.');
  await noOverflow(page);
});

const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const lang of ['en', 'ar'] as const) {
  test(`@gate the visit fits all five sizes in ${lang} without overflow or clipped controls`, async ({ page }) => {
    await useSavedLocale(page, lang);
    await openVisit(page, 'ready');
    const v = visit(page);
    await v.getByRole('button', { name: lang === 'en' ? 'Begin visit' : 'بدء الجولة' }).click();
    const shots = process.env.PA_SHOTS;
    if (shots) mkdirSync(shots, { recursive: true });
    for (const [width, height] of SIZES) {
      await page.setViewportSize({ width, height });
      await expect(v).toBeVisible();
      await noOverflow(page);
      const box = await v.boundingBox();
      expect(box && box.x >= -1 && box.x + box.width <= width + 1).toBe(true);
      const clipped = await v.locator('button').evaluateAll((els) => els.filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && (r.right > window.innerWidth + 1 || r.left < -1); }).length);
      expect(clipped).toBe(0);
      if (shots) await page.screenshot({ path: `${shots}/visit-${lang}-${width}x${height}.png` });
    }
  });
}
