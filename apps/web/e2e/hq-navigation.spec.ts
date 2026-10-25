import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { campusFixture, mockHq } from './support/hqCampusApi';
import { useSavedLocale } from './support/workspaceApi';

// ANUM MINE headquarters navigation layer (docs/ANUM_MINE_HQ_EXTENSION_POINTS.md, "Navigation layer"):
// the floor rail, the quick-view strip, the campus cards, the floor plan with people and the quick moves.
// The API is mocked at http://localhost:8000 like the other specs; every name below is plainly fake.
const hq = (page: Page) => page.locator('.hqLive');
const live = (page: Page) => page.getByTestId('hq-nav-live');
const strip = (page: Page) => hq(page).getByRole('navigation', { name: 'Quick views' });
const rail = (page: Page) => hq(page).getByRole('navigation', { name: 'Floors of Al Noor Fleet HQ' });
const cards = (page: Page) => hq(page).locator('.hqLabels');
const SHOTS = process.env.HQ_NAV_SHOTS;
// The scene draws in software on CI and in this sandbox: give every test room.
test.describe.configure({ timeout: 240_000 });

async function openOffice(page: Page, size = { width: 1440, height: 900 }, options: Parameters<typeof mockHq>[1] = { campus: 'api', commerce: 'connected', meetings: 'live' }) {
  await page.addInitScript(() => { try { localStorage.setItem('anum.hq.light', 'day'); localStorage.setItem('anum.hq.quality', '0'); } catch { /* storage blocked */ } });
  await mockHq(page, options);
  await page.setViewportSize(size);
  await page.goto('/#office-preview');
  await expect(hq(page).getByRole('heading', { level: 1 })).toBeVisible();
  await expect(hq(page).locator('canvas.hqCanvas')).toBeVisible();
  // The campus read, the first draw and the lazy panels share a busy machine in CI: wait for the cards once, with room.
  await expect(cards(page).locator('.hqBuildingCard').first()).toBeAttached({ timeout: 60_000 });
}

async function enterFleetBuilding(page: Page) {
  await cards(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click();
  await hq(page).getByRole('button', { name: 'Open Al Noor Fleet HQ' }).click();
  await expect(hq(page).getByRole('button', { name: 'Back to campus' })).toBeVisible();
  // The panels are a lazy chunk and the presence read follows: wait for them once, with room for a busy machine.
  if ((page.viewportSize()?.width ?? 0) >= 1280) await expect(hq(page).locator('.hqPlan')).toBeVisible({ timeout: 30_000 });
  if ((page.viewportSize()?.width ?? 0) >= 900) await expect(hq(page).locator('.hqRailItem').first()).toBeVisible({ timeout: 30_000 });
}

const noOverflow = async (page: Page) => expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);

test('campus to building to floor to room through the strip, the rail, the plan and the cards', async ({ page }) => {
  await openOffice(page);
  // The strip lists the campus aerial and one stop per building, taken from the campus data.
  await expect(strip(page)).toBeVisible({ timeout: 60_000 });
  for (const name of ['Campus (aerial)', 'Executive HQ', 'Al Noor Fleet HQ', 'Tyre Pulse HQ', 'ANUM360 Commerce', 'Personal Office']) {
    await expect(strip(page).getByRole('button', { name: `Quick view: ${name}.` })).toBeVisible();
  }
  // A building the person cannot open is listed, locked and disabled.
  await expect(strip(page).getByRole('button', { name: /^Quick view: Legal and Compliance\. Not available to you/ })).toBeDisabled();
  await expect(strip(page).getByRole('button', { name: /^Quick view: Campus \(aerial\)/ })).toHaveAttribute('aria-current', 'true');

  // A quick view flies the camera, selects the building and says so for screen readers.
  await strip(page).getByRole('button', { name: /^Quick view: Executive HQ\./ }).click();
  await expect(live(page)).toContainText(/Moving to Executive HQ|Now showing Executive HQ/);
  await expect(live(page)).toContainText('Now showing Executive HQ.', { timeout: 30_000 });
  // The card may sit out of view once the camera is close; it is still the selected one.
  await expect(cards(page).locator('.hqBuildingCard[aria-label^="Executive HQ."]')).toHaveAttribute('aria-pressed', 'true');
  await expect(strip(page).getByRole('button', { name: /^Quick view: Executive HQ\./ })).toHaveAttribute('aria-current', 'true');

  // Pressing the current stop again opens the building, like the label card does.
  await strip(page).getByRole('button', { name: /^Quick view: Al Noor Fleet HQ\./ }).click();
  await strip(page).getByRole('button', { name: /^Quick view: Al Noor Fleet HQ\./ }).click();
  await expect(hq(page).getByRole('button', { name: 'Back to campus' })).toBeVisible();

  // The rail lists the floors, top floor first, and opens one.
  await expect(rail(page).locator('.hqRailItem')).toHaveCount(2, { timeout: 30_000 });
  await expect(rail(page).locator('.hqRailItem').first()).toHaveAccessibleName(/^First floor\./);
  await expect(rail(page).getByRole('button', { name: /^Ground floor\./ })).toHaveAttribute('aria-current', 'true');
  await rail(page).getByRole('button', { name: /^First floor\./ }).click();
  await expect(rail(page).getByRole('button', { name: /^First floor\./ })).toHaveAttribute('aria-current', 'true');
  await expect(live(page)).toContainText('Floor First floor is open.');
  await expect(cards(page).locator('.hqRoomCard', { hasText: 'Finance' })).toBeAttached();

  // The plan lists the saved rooms; a room opens its panel and the camera moves to it.
  const plan = hq(page).getByRole('region', { name: 'Floor plan of First floor' });
  await expect(plan.getByRole('button', { name: /^On the plan: Finance, Office\./ })).toBeVisible();
  await plan.getByRole('button', { name: /^On the plan: Finance, Office\./ }).click();
  await expect(hq(page).getByRole('complementary', { name: 'Room Finance' })).toBeVisible();
  await expect(live(page)).toContainText('Now showing Finance.', { timeout: 30_000 });

  // A room stop in the strip, on another floor, switches the floor and flies there.
  await strip(page).getByRole('button', { name: /^Quick view: Tyre Analysis\./ }).click();
  await expect(rail(page).getByRole('button', { name: /^Ground floor\./ })).toHaveAttribute('aria-current', 'true');
  await expect(hq(page).getByRole('complementary', { name: 'Room Tyre Analysis' })).toBeVisible();
  await expect(live(page)).toContainText('Now showing Tyre Analysis.', { timeout: 30_000 });
  await noOverflow(page);
});

test('the people list shows only assigned agents with their real state', async ({ page }) => {
  await openOffice(page);
  await enterFleetBuilding(page);
  const people = hq(page).getByRole('region', { name: 'People at their posts' });
  await expect(people.getByRole('button', { name: /^Open Fleet Planner\. Working\./ })).toBeVisible({ timeout: 30_000 });
  await expect(people.getByRole('button', { name: /^Open WhatsApp Concierge\. In meetings\./ })).toBeVisible();
  // Another person's agent keeps its desk and loses its name.
  await expect(people.getByRole('button', { name: /^Open Private agent\./ })).toBeDisabled();
  // Desks without an assignment are not people.
  await expect(people.getByRole('listitem')).toHaveCount(3);
  // An agent opens the workstation panel.
  await people.getByRole('button', { name: /^Open Fleet Planner/ }).click();
  await expect(hq(page).getByRole('complementary', { name: 'Agent Fleet Planner' })).toBeVisible();
  // The floor above has an assignment but no presence record: it says so instead of guessing.
  await rail(page).getByRole('button', { name: /^First floor\./ }).click();
  await expect(people.getByRole('button', { name: /^Open Lead Finder\./ })).toContainText('Idle');
});

test('the 2D and 3D switch reuses the top-down view', async ({ page }) => {
  await openOffice(page);
  await enterFleetBuilding(page);
  const mode = hq(page).getByRole('group', { name: 'Plan view' });
  await mode.getByRole('button', { name: '2D' }).click();
  await expect(mode.getByRole('button', { name: '2D' })).toHaveAttribute('aria-pressed', 'true');
  await expect(hq(page).getByRole('group', { name: 'View', exact: true }).getByRole('button', { name: /Top/ })).toHaveAttribute('aria-pressed', 'true');
  await mode.getByRole('button', { name: '3D' }).click();
  await expect(hq(page).getByRole('group', { name: 'View', exact: true }).getByRole('button', { name: /3D/ })).toHaveAttribute('aria-pressed', 'true');
});

test('keyboard: arrows move between quick views and floors, Enter opens', async ({ page }) => {
  await openOffice(page);
  const first = strip(page).getByRole('button').first();
  await first.focus();
  await page.keyboard.press('ArrowRight');
  await expect(strip(page).getByRole('button').nth(1)).toBeFocused();
  await page.keyboard.press('End');
  await expect(strip(page).getByRole('button').last()).toBeFocused();
  await page.keyboard.press('Home');
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('Enter');
  await expect(live(page)).toContainText('Now showing Executive HQ.', { timeout: 30_000 });
  await strip(page).getByRole('button', { name: /^Quick view: Al Noor Fleet HQ\./ }).click();
  await strip(page).getByRole('button', { name: /^Quick view: Al Noor Fleet HQ\./ }).click();
  await expect(rail(page)).toBeVisible();
  await rail(page).locator('.hqRailItem').first().focus();
  await page.keyboard.press('ArrowDown');
  await expect(rail(page).locator('.hqRailItem').nth(1)).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(rail(page).locator('.hqRailItem').nth(1)).toHaveAttribute('aria-current', 'true');
});

test('reduced motion jumps the camera and says so', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await openOffice(page);
  await strip(page).getByRole('button', { name: /^Quick view: Executive HQ\./ }).click();
  await expect(live(page)).toHaveText('Showing Executive HQ.');
});

test('the rail and the strip collapse, and the choice is remembered', async ({ page }) => {
  await openOffice(page);
  await enterFleetBuilding(page);
  await hq(page).getByRole('button', { name: 'Collapse the floor list' }).click();
  await expect(rail(page)).toHaveAttribute('data-slim', 'true');
  await hq(page).getByRole('button', { name: 'Collapse the quick views' }).click();
  await expect(strip(page)).toHaveCount(0);
  await page.reload();
  await expect(hq(page).locator('canvas.hqCanvas')).toBeVisible();
  await expect(strip(page)).toHaveCount(0);
  await hq(page).getByRole('button', { name: 'Show the quick views' }).click();
  await expect(strip(page)).toBeVisible();
});

test('many buildings: cards shrink by rule and none disappears', async ({ page }) => {
  await page.addInitScript(() => { try { localStorage.setItem('anum.hq.light', 'day'); localStorage.setItem('anum.hq.quality', '0'); } catch { /* storage blocked */ } });
  await mockHq(page, { campus: 'api', commerce: 'none', meetings: 'none' });
  const many = { ...campusFixture, buildings: [...campusFixture.buildings, ...Array.from({ length: 10 }, (_, i) => ({ ...campusFixture.buildings[3], workspace_id: `workspace_extra_${i}`, name: `Extra Building ${i}`, accessible: true }))] };
  await page.route('http://localhost:8000/api/v1/office/campus', (route) => route.fulfill({ json: many }));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/#office-preview');
  await expect(hq(page).locator('canvas.hqCanvas')).toBeVisible();
  const count = (d: string) => cards(page).locator(`.hqBuildingCard[data-density="${d}"]`).count();
  await expect.poll(async () => (await count('full')) + (await count('compact')) + (await count('dot')), { timeout: 60_000 }).toBe(16);
  expect(await count('full')).toBe(6);
  expect(await count('compact')).toBe(8);
  expect(await count('dot')).toBe(2);
  // A dot card still has its full name for assistive technology.
  await expect(cards(page).locator('.hqBuildingCard[data-density="dot"]').first()).toHaveAttribute('aria-label', /agents|Presence unavailable/);
});

test('cards carry real tags and counts only', async ({ page }) => {
  await openOffice(page);
  const exec = cards(page).locator('.hqBuildingCard[aria-label^="Executive HQ."]');
  await expect(exec).toContainText('Executive');
  await expect(exec).toContainText('3 floors');
  await expect(exec).toContainText('5 agents, 2 working');
  // An unclassified building shows no type tag, and a building without presence says so, with no invented count.
  const personal = cards(page).locator('.hqBuildingCard[aria-label^="Personal Office."]');
  await expect(personal).not.toContainText('Business');
  await expect(cards(page).locator('.hqBuildingCard[aria-label^="Tyre Pulse HQ."]')).toContainText('Presence unavailable');
});

test('quick moves run existing navigation and suggest only real places', async ({ page }) => {
  await openOffice(page);
  const moves = hq(page).getByRole('region', { name: 'Quick moves' });
  await expect(moves.getByRole('button', { name: 'Take me to Executive HQ' })).toBeVisible();
  await expect(moves.getByRole('button', { name: 'Take me to Legal and Compliance' })).toHaveCount(0);
  await moves.getByRole('button', { name: 'Take me to Executive HQ' }).click();
  await moves.getByRole('button', { name: 'Take me to my office' }).click();
  await expect(hq(page).getByRole('button', { name: 'Back to campus' })).toBeVisible();
  await hq(page).getByRole('tab', { name: 'Quick moves' }).click();
  await expect(hq(page).getByRole('button', { name: 'Take me to Reception' })).toBeVisible();
  await hq(page).getByRole('button', { name: 'Take me to Reception' }).click();
  await expect(hq(page).getByRole('complementary', { name: 'Room Reception' })).toBeVisible();
  await hq(page).getByRole('button', { name: 'Show active meetings' }).click();
  await expect(page.getByRole('dialog', { name: 'Office tools' })).toBeVisible();
});

test('Arabic: right to left, translated labels, no overflow', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await openOffice(page);
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(hq(page).getByRole('navigation', { name: 'مشاهد سريعة' })).toBeVisible();
  await noOverflow(page);
  await hq(page).getByRole('navigation', { name: 'مشاهد سريعة' }).getByRole('button').nth(2).click();
  await hq(page).getByRole('navigation', { name: 'مشاهد سريعة' }).getByRole('button').nth(2).click();
  await expect(hq(page).getByRole('navigation', { name: /^طوابق/ })).toBeVisible({ timeout: 30_000 });
  await expect(hq(page).getByRole('region', { name: 'الأشخاص في مواقعهم' })).toBeVisible({ timeout: 30_000 });
  await noOverflow(page);
  // The rail sits on the inline start side: the right in Arabic.
  const railBox = await hq(page).getByRole('navigation', { name: /^طوابق/ }).boundingBox();
  const sideBox = await hq(page).getByRole('complementary', { name: 'مخطط الطابق والأشخاص والتنقلات السريعة' }).boundingBox();
  expect(railBox && sideBox && railBox.x > sideBox.x).toBe(true);
});

test('without WebGL the list and the 2D plan take over, and the rail still works', async ({ page }) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (kind: string, ...rest: unknown[]) {
      if (kind === 'webgl' || kind === 'webgl2' || kind === 'experimental-webgl') return null;
      return (original as (...a: unknown[]) => unknown).call(this, kind, ...rest) as never;
    } as typeof original;
    try { localStorage.setItem('anum.hq.light', 'day'); } catch { /* storage blocked */ }
  });
  await mockHq(page, { campus: 'api', commerce: 'connected', meetings: 'live' });
  await page.goto('/#office-preview');
  await expect(hq(page).getByRole('heading', { name: 'Executive HQ' }).first()).toBeVisible();
  await expect(strip(page)).toHaveCount(0);
  await hq(page).getByRole('button', { name: /^Open Al Noor Fleet HQ/ }).first().click();
  // The list is the accessible view; the 2D plan has the rail and the people beside it.
  await hq(page).getByRole('group', { name: 'View', exact: true }).getByRole('button', { name: /Top/ }).click();
  await expect(rail(page)).toBeVisible();
  await rail(page).getByRole('button', { name: /^First floor\./ }).click();
  await expect(rail(page).getByRole('button', { name: /^First floor\./ })).toHaveAttribute('aria-current', 'true');
  await expect(hq(page).getByRole('group', { name: 'Plan view' }).getByRole('button', { name: '3D' })).toBeDisabled();
});

test('small screens: the floors and the side panel open as drawers', async ({ page }) => {
  await openOffice(page, { width: 390, height: 844 });
  await noOverflow(page);
  await enterFleetBuilding(page);
  await noOverflow(page);
  await hq(page).getByRole('button', { name: 'Floors', exact: true }).click();
  const drawer = page.getByRole('dialog', { name: 'Floors' });
  await expect(drawer).toBeVisible();
  await drawer.getByRole('button', { name: /^First floor\./ }).click();
  await expect(drawer).toHaveCount(0);
  await hq(page).getByRole('button', { name: 'Plan and people', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Plan and people' }).getByRole('region', { name: 'People at their posts' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: 'Plan and people' })).toHaveCount(0);
  await noOverflow(page);
});

for (const locale of ['en', 'ar'] as const) {
  for (const theme of ['light', 'dark'] as const) {
    test(`axe on the navigation layer: ${locale}, ${theme} theme, campus and building`, async ({ page }) => {
      await page.addInitScript(() => { try { localStorage.setItem('anum.hq.light', 'day'); localStorage.setItem('anum.hq.quality', '0'); } catch { /* storage blocked */ } });
      await useSavedLocale(page, locale);
      await page.addInitScript((value) => { try { localStorage.setItem('anum.theme', value); } catch { /* storage blocked */ } }, theme);
      await mockHq(page, { campus: 'api', commerce: 'connected', meetings: 'live' });
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto('/#office-preview');
      await expect(hq(page).locator('canvas.hqCanvas')).toBeVisible();
      const scan = async () => (await new AxeBuilder({ page }).include('.hqNav').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze()).violations
        .filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.slice(0, 4).map((n) => n.target.join(' ')).join(' | ')}`);
      expect(await scan()).toEqual([]);
      await cards(page).locator('.hqBuildingCard', { hasText: 'Al Noor Fleet HQ' }).click();
      await hq(page).locator('.hqDetail button.hqPrimary').click();
      await expect(hq(page).locator('.hqPlan')).toBeVisible({ timeout: 30_000 });
      await page.waitForTimeout(500);
      expect(await scan()).toEqual([]);
    });
  }
}

test('a registry with eight buildings is eight buildings on the campus, next to the other workspaces', async ({ page }) => {
  await openOffice(page, { width: 1440, height: 900 }, { campus: 'api', layout: 'starter', commerce: 'connected', meetings: 'live' });
  const card = (name: string) => cards(page).locator(`.hqBuildingCard[aria-label^="${name}."]`);
  // Eight registry buildings of the open workspace and five other workspaces: thirteen buildings, none merged.
  await expect(cards(page).locator('.hqBuildingCard')).toHaveCount(13);
  for (const name of ['ANUM HQ', 'ANUM 360 Commerce', 'Tyre Pulse Workshop', 'Corporate Services', 'Knowledge and AI', 'New Ventures', 'Founder Personal Office', 'Career and Employment']) await expect(card(name)).toBeAttached();
  // The workspace's own single building is gone (its registry buildings stand for it); other workspaces keep theirs.
  await expect(card('Al Noor Fleet HQ')).toHaveCount(0);
  for (const name of ['Executive HQ', 'Tyre Pulse HQ', 'ANUM360 Commerce', 'Personal Office']) await expect(card(name)).toBeAttached();
  // People come from desk assignments and presence; a building without assignments says so, a registry building names its workspace.
  await expect(card('ANUM HQ')).toHaveAttribute('aria-label', /^ANUM HQ\. 6 agents, 1 working, 1 in meetings/);
  await expect(card('Corporate Services')).toHaveAttribute('aria-label', /^Corporate Services\. No agents/);
  await expect(card('ANUM HQ')).toContainText('Al Noor Fleet HQ');
  await expect(card('ANUM HQ')).not.toContainText('Business');
  // More than ten buildings: the density rule applies and no card disappears.
  const full = await cards(page).locator('.hqBuildingCard[data-density="full"]').count();
  expect(full).toBeLessThanOrEqual(6);
  // The strip lists the same buildings; opening one reads its own floors and rooms.
  for (const name of ['ANUM HQ', 'Tyre Pulse Workshop', 'Career and Employment', 'Executive HQ']) await expect(strip(page).getByRole('button', { name: `Quick view: ${name}.` })).toBeVisible();
  await strip(page).getByRole('button', { name: /^Quick view: Tyre Pulse Workshop\./ }).click();
  await strip(page).getByRole('button', { name: /^Quick view: Tyre Pulse Workshop\./ }).click();
  await expect(hq(page).getByRole('button', { name: 'Back to campus' })).toBeVisible();
  const floors = hq(page).getByRole('navigation', { name: 'Floors of Tyre Pulse Workshop' });
  await expect(floors.locator('.hqRailItem')).toHaveCount(1);
  await expect(strip(page).getByRole('button', { name: /^Quick view: Tyre Pulse Workshop Lobby\./ })).toBeVisible();
  await expect(strip(page).getByRole('button', { name: /^Quick view: Tyre Pulse Workshop\./ })).toHaveAttribute('aria-current', 'true');
  await expect(hq(page).getByRole('region', { name: 'People at their posts' })).toContainText('No one is assigned on this floor.');
  // Another registry building of the same workspace opens without switching workspace.
  await strip(page).getByRole('button', { name: /^Quick view: ANUM HQ\./ }).click();
  await expect(hq(page).getByRole('navigation', { name: 'Floors of ANUM HQ' }).locator('.hqRailItem')).toHaveCount(2);
  await expect(hq(page).getByRole('region', { name: 'People at their posts' })).toContainText('Fleet Planner');
  // The assistant's move by name reaches a registry building too.
  await hq(page).getByRole('button', { name: 'Back to campus' }).click();
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('anum:hq-navigate', { detail: { building: 'Career and Employment' } })));
  await expect(hq(page).getByRole('navigation', { name: 'Floors of Career and Employment' })).toBeVisible();
});

for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const) {
  test(`screenshots at ${width}x${height}`, async ({ page }) => {
    test.skip(!SHOTS, 'set HQ_NAV_SHOTS to a folder to write the screenshots');
    await openOffice(page, { width, height }, { campus: 'api', layout: 'starter', commerce: 'connected', meetings: 'live' });
    await page.waitForTimeout(5000);
    await page.screenshot({ path: `${SHOTS}/campus-${width}x${height}.png` });
    await noOverflow(page);
    const hqStop = strip(page).getByRole('button', { name: /^Quick view: ANUM HQ\./ });
    await hqStop.click();
    await hqStop.click();
    await expect(hq(page).getByRole('button', { name: 'Back to campus' })).toBeVisible();
    await page.waitForTimeout(5000);
    await page.screenshot({ path: `${SHOTS}/building-${width}x${height}.png` });
    await noOverflow(page);
  });
}
