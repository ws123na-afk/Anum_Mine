import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockHq } from './support/hqCampusApi';
import { cameraOptions, officeMenuItem, openStatus, showFloorList } from './support/hqScene';

// The decluttered headquarters (owner, 2 November 2026: "too many things showing, so messy"): labels that never stack (one compact tag per
// floor at its own edge, only the open floor's tag while zoomed in or with one floor), one floor control, one status chip, one Office tools
// menu, one camera group, and a stage that takes most of a laptop screen. Spec: docs/ANUM_MINE_HQ_LABELS.md ("Declutter") and
// docs/ANUM_MINE_HQ_PAGE_LAYOUT.md ("Header"). The API is mocked at http://localhost:8000; the starter campus fixture is plainly fake.
test.use({ deviceScaleFactor: 1, isMobile: false, hasTouch: false });
test.describe.configure({ timeout: 300_000 });

const hq = (page: Page) => page.locator('.hqLive');
const SIZES = [[1920, 1080], [1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;

async function openCampus(page: Page, size: { width: number; height: number }) {
  await page.setViewportSize(size);
  await page.addInitScript(() => { try { localStorage.setItem('anum.hq.light', 'day'); } catch { /* storage blocked */ } });
  await mockHq(page, { campus: 'api', layout: 'starter-full', meetings: 'live' });
  await page.goto('/#office-preview');
  await expect(hq(page).locator('canvas.hqCanvas')).toHaveAttribute('data-engine', /three/, { timeout: 90_000 });
  await expect(hq(page).locator('.hqBuildingCard').first()).toBeAttached({ timeout: 60_000 });
}

/** ANUM HQ of the starter campus: Basement, Floor 1 to 3 and the Rooftop, entered from the list (reachable at every size). */
async function enterHq(page: Page) {
  await hq(page).getByRole('button', { name: 'List', exact: true }).click();
  await hq(page).locator('.hqListBuilding', { has: page.getByRole('heading', { level: 3, name: 'ANUM HQ', exact: true }) }).locator('.hqListRow > button').click();
  await expect(hq(page).locator('.hqSceneWrap')).toBeVisible({ timeout: 60_000 });
  await expect(hq(page).getByRole('group', { name: 'Floors of this building' })).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1500);
}

type Shown = { id: string; floorTag: boolean; selected: boolean; name: string; r: { x0: number; x1: number; y0: number; y1: number } };
/** The label cards a person can see: drawn, not crowded, not fading out, with their boxes. */
const shownLabels = (page: Page): Promise<Shown[]> => page.evaluate(() => {
  const out: Shown[] = [];
  document.querySelectorAll<HTMLElement>('.hqLive .hqLabels > .hqLabelAnchor').forEach((el, i) => {
    if (el.style.display === 'none' || el.dataset.hidden === 'crowded' || el.dataset.fade === 'out') return;
    const card = el.firstElementChild as HTMLElement | null;
    if (!card) return;
    const b = card.getBoundingClientRect();
    if (b.width === 0 || b.height === 0) return;
    out.push({ id: String(i), floorTag: card.classList.contains('hqFloorTag'), selected: card.dataset.selected === 'true', name: card.getAttribute('aria-label') ?? '', r: { x0: b.left, x1: b.right, y0: b.top, y1: b.bottom } });
  });
  return out;
});

async function expectNoOverlap(page: Page, where: string) {
  await page.waitForTimeout(1200); // the placement follows the camera every frame; let a moving camera stop
  const shown = await shownLabels(page);
  for (let i = 0; i < shown.length; i++) for (let j = i + 1; j < shown.length; j++) {
    const a = shown[i].r, b = shown[j].r;
    const overlap = a.x0 < b.x1 - 2 && a.x1 > b.x0 + 2 && a.y0 < b.y1 - 2 && a.y1 > b.y0 + 2;
    expect(overlap, `${where}: "${shown[i].name}" and "${shown[j].name}" overlap`).toBe(false);
  }
  return shown;
}

async function pickFloor(page: Page, name: RegExp) {
  await hq(page).getByRole('group', { name: 'Floors of this building' }).getByRole('button', { name }).click();
  await page.waitForTimeout(1200);
}

async function zoomIn(page: Page, times = 3) {
  const zoom = hq(page).locator('.hqDock').getByRole('button', { name: 'Zoom in' });
  for (let i = 0; i < times; i++) await zoom.click();
}

const share = (page: Page) => page.evaluate(() => {
  const r = document.querySelector('.hqLive .hqStage')!.getBoundingClientRect();
  return (r.width * r.height) / (window.innerWidth * window.innerHeight);
});

for (const [width, height] of SIZES) {
  test(`labels never overlap at ${width}x${height}: campus, a building with every floor, zoomed in, and one floor`, async ({ page }) => {
    await openCampus(page, { width, height });
    await expectNoOverlap(page, 'campus');
    await enterHq(page);
    await expect(hq(page).getByRole('checkbox', { name: /All floors/ })).toBeChecked();
    // Every floor: at most one compact tag per floor, each at its own floor's edge, none on another label.
    let shown = await expectNoOverlap(page, 'all floors');
    expect(shown.filter((l) => l.floorTag).length).toBeLessThanOrEqual(5);
    // Zoomed in on the Rooftop like the owner's picture: only the open floor's tag may show.
    await pickFloor(page, /Rooftop/);
    await zoomIn(page);
    shown = await expectNoOverlap(page, 'all floors, zoomed in');
    const zoomedTags = shown.filter((l) => l.floorTag);
    expect(zoomedTags.length, 'only the open floor is tagged while zoomed in').toBeLessThanOrEqual(1);
    for (const tag of zoomedTags) { expect(tag.selected).toBe(true); expect(tag.name).toMatch(/^Rooftop/); }
    // One floor (All floors off), zoomed in: only that floor's tag exists at all.
    await hq(page).getByRole('checkbox', { name: /All floors/ }).uncheck();
    await zoomIn(page, 1);
    shown = await expectNoOverlap(page, 'one floor, zoomed in');
    await expect(hq(page).locator('.hqLabels .hqFloorTag')).toHaveCount(1);
    for (const tag of shown.filter((l) => l.floorTag)) expect(tag.name).toMatch(/^Rooftop/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  });
}

test('with one floor selected only that floor has a tag, and it follows the floor', async ({ page }) => {
  await openCampus(page, { width: 1440, height: 900 });
  await enterHq(page);
  await hq(page).getByRole('checkbox', { name: /All floors/ }).uncheck();
  for (const floor of [/^Floor 2/, /^Basement/, /^Rooftop/]) {
    await pickFloor(page, floor);
    const tags = hq(page).locator('.hqLabels .hqFloorTag');
    await expect(tags).toHaveCount(1);
    await expect(tags.first()).toHaveAttribute('aria-label', floor);
    await expect(tags.first()).toHaveAttribute('aria-pressed', 'true');
    for (const tag of (await shownLabels(page)).filter((l) => l.floorTag)) expect(tag.name).toMatch(floor);
  }
});

for (const [width, height, campusMin, buildingMin] of [[1366, 768, 0.42, 0.38], [1920, 1080, 0.45, 0.42]] as const) {
  test(`the stage takes most of a ${width}x${height} screen (campus at least ${campusMin}, a building at least ${buildingMin} of the viewport)`, async ({ page }) => {
    await openCampus(page, { width, height });
    expect(await share(page)).toBeGreaterThanOrEqual(campusMin);
    await enterHq(page);
    expect(await share(page)).toBeGreaterThanOrEqual(buildingMin);
    // One floor control: the floor list with thumbnails is not docked by default, the selector in the scene is.
    await expect(hq(page).locator('.hqNavRail')).toHaveCount(0);
    await expect(hq(page).getByRole('group', { name: 'Floors of this building' })).toBeVisible();
    // One camera group: no corner column next to the bar.
    await expect(hq(page).locator('.hqSceneWrap .hqTools')).toHaveCount(0);
    await expect(hq(page).locator('.hqSceneWrap [role="toolbar"]')).toHaveCount(1);
  });
}

test('every moved control is still reachable, by keyboard too, and none is a dead button', async ({ page }) => {
  await openCampus(page, { width: 1440, height: 900 });
  // The one status chip: Enter opens its panel with the people link, the office data read and the graphics tier; Escape closes it.
  const chip = hq(page).getByTestId('hq-status-chip');
  await expect(chip).toHaveAccessibleName(/^Office status: .+\. Show the details$/);
  await chip.focus();
  await page.keyboard.press('Enter');
  await expect(chip).toHaveAttribute('aria-expanded', 'true');
  await expect(hq(page).getByTestId('hq-live-link')).toBeVisible();
  await expect(hq(page).getByTestId('hq-data-status')).toBeVisible();
  await expect(hq(page).getByTestId('hq-gpu-chip')).toBeVisible({ timeout: 60_000 });
  await page.keyboard.press('Escape');
  await expect(chip).toHaveAttribute('aria-expanded', 'false');
  await expect(chip).toBeFocused();
  // Office tools holds the rare items: nothing docked at 1440 means no Hide side panels (never a dead switch); the concept preview is there.
  await expect(await officeMenuItem(page, 'Hide side panels')).toHaveCount(0);
  const concept = await officeMenuItem(page, 'Concept preview');
  await concept.click();
  await expect(page.locator('.anumOfficeTour')).toBeVisible();
  await page.getByRole('button', { name: 'Authenticated office' }).click();
  await expect(hq(page).locator('canvas.hqCanvas')).toBeVisible({ timeout: 60_000 });
  // The camera group on the campus: zoom, reset, the campus map, previous and next building, then Face north and the light under More.
  const bar = hq(page).getByRole('toolbar', { name: 'Scene controls' });
  for (const name of ['Zoom in', 'Zoom out', 'Reset view', 'Rotate left', 'Rotate right', 'Full screen']) await expect(bar.getByRole('button', { name, exact: true })).toBeVisible();
  await expect(bar.getByTestId('hq-map-toggle')).toBeVisible();
  const more = bar.getByRole('button', { name: 'More camera options' });
  await more.focus();
  await page.keyboard.press('Enter');
  for (const name of ['Face north', 'Day', 'Blue hour', 'Night']) await expect(bar.getByRole('button', { name, exact: true })).toBeVisible();
  await bar.getByRole('button', { name: 'Night', exact: true }).click();
  await expect(hq(page).locator('.hqStage')).toHaveAttribute('data-night', 'true');
  await page.keyboard.press('Escape');
  await expect(more).toHaveAttribute('aria-expanded', 'false');
  await expect(more).toBeFocused();
  // Inside a building: the floor arrows are in the floor selector, the floor list docks from the Floors button and goes again.
  await enterHq(page);
  const count = page.getByTestId('hq-floor-count');
  const floors = hq(page).getByRole('group', { name: 'Floors of this building' });
  const before = await count.textContent();
  await floors.getByRole('button', { name: 'Floor below' }).click();
  await expect(count).not.toHaveText(before ?? '');
  await floors.getByRole('button', { name: 'Floor above' }).click();
  await expect(count).toHaveText(before ?? '');
  await showFloorList(page);
  await expect(hq(page).getByRole('navigation', { name: 'Floors of ANUM HQ' })).toBeVisible();
  // With the list docked the fold switch exists and works.
  await expect(await officeMenuItem(page, 'Hide side panels')).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Escape');
  await hq(page).getByTestId('hq-floors-toggle').click();
  await expect(hq(page).getByRole('navigation', { name: 'Floors of ANUM HQ' })).toHaveCount(0);
  // The building's camera group carries the same options.
  await cameraOptions(page);
  await expect(hq(page).locator('.hqDock').getByRole('button', { name: 'Face north' })).toBeVisible();
  const results = await new AxeBuilder({ page }).include('.hqLive').exclude('canvas').exclude('.hqLabels').disableRules(['color-contrast']).analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
});

test('on a phone the floors are a drawer from the Floors button, and the status chip still says the state', async ({ page }) => {
  await openCampus(page, { width: 390, height: 844 });
  await openStatus(page);
  await expect(hq(page).getByTestId('hq-live-link')).toBeVisible();
  await page.keyboard.press('Escape');
  await enterHq(page);
  await hq(page).getByTestId('hq-floors-toggle').click();
  const drawer = page.getByRole('dialog', { name: 'Floors' });
  await expect(drawer).toBeVisible();
  await drawer.getByRole('button', { name: /^Floor 1/ }).click();
  await expect(drawer).toHaveCount(0);
  await expect(hq(page).getByRole('group', { name: 'Floors of this building' }).getByRole('button', { name: /^Floor 1/ })).toHaveAttribute('aria-pressed', 'true');
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
});

test('the camera group fades while the scene is idle and comes back on a move; labels fade, never pop, unless motion is reduced', async ({ page }) => {
  await openCampus(page, { width: 1366, height: 768 });
  const bar = hq(page).getByRole('toolbar', { name: 'Scene controls' });
  await page.mouse.move(2, 2);
  await expect(bar).toHaveAttribute('data-idle', 'true', { timeout: 10_000 });
  await expect.poll(() => bar.evaluate((el) => Number(getComputedStyle(el).opacity))).toBeLessThan(0.6);
  const stage = (await hq(page).locator('.hqStage').boundingBox())!;
  await page.mouse.move(stage.x + stage.width / 2, stage.y + stage.height / 3);
  await expect(bar).toHaveAttribute('data-idle', 'false');
  await expect.poll(() => bar.evaluate((el) => Number(getComputedStyle(el).opacity))).toBe(1);
  // Keyboard focus keeps it whole even when idle.
  await bar.getByRole('button', { name: 'Zoom in' }).focus();
  await page.waitForTimeout(3500);
  await expect.poll(() => bar.evaluate((el) => Number(getComputedStyle(el).opacity))).toBe(1);
  const fade = await hq(page).locator('.hqLabels > .hqLabelAnchor').first().evaluate((el) => getComputedStyle(el).transitionDuration);
  expect(fade).not.toBe('0s');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const still = await hq(page).locator('.hqLabels > .hqLabelAnchor').first().evaluate((el) => getComputedStyle(el).transitionDuration);
  expect(still).toBe('0s');
});
