import { expect, test, type Page } from '@playwright/test';
import { mockHq } from './support/hqCampusApi';
import { sceneSettled } from './support/hqScene';

// Opening a room on another floor and the people at their posts (docs/ANUM_MINE_HQ_PEOPLE_CHARACTERS.md,
// "People at their posts" and "Opening a room on any floor"). The API is mocked at http://localhost:8000 like the
// other specs; every name below is plainly fake. The fixture has two floors: Ground (Reception, Customer Care,
// Meeting Room and more) and First (Sales and Marketing, Finance, Executive Office).
const hq = (page: Page) => page.locator('.hqLive');
const live = (page: Page) => page.getByTestId('hq-nav-live');
const rail = (page: Page) => hq(page).getByRole('navigation', { name: 'Floors of Al Noor Fleet HQ' });
const cards = (page: Page) => hq(page).locator('.hqLabels');
const canvas = (page: Page) => hq(page).locator('canvas.hqCanvas');
test.use({ deviceScaleFactor: 1, isMobile: false, hasTouch: false });
test.describe.configure({ timeout: 240_000 });

async function openBuilding(page: Page, options: Parameters<typeof mockHq>[1] = { campus: 'api', meetings: 'live' }, size = { width: 1440, height: 900 }) {
  await page.addInitScript(() => { try { localStorage.setItem('anum.hq.light', 'day'); } catch { /* storage blocked */ } });
  await mockHq(page, options);
  await page.setViewportSize(size);
  await page.goto('/#office-preview');
  await expect(cards(page).locator('.hqBuildingCard').first()).toBeAttached({ timeout: 60_000 });
  await cards(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click();
  await hq(page).getByRole('button', { name: 'Open Al Noor Fleet HQ' }).click();
  await expect(hq(page).getByRole('button', { name: 'Back to campus' })).toBeVisible();
  await expect(rail(page).getByRole('button', { name: /^Ground floor\./ })).toHaveAttribute('aria-current', 'true', { timeout: 30_000 });
}

test('a room picked in a list on another floor switches to that floor first, then opens and frames the room', async ({ page }) => {
  await openBuilding(page);
  // The list view lists every floor's rooms; Finance is on the First floor while the Ground floor is open.
  await hq(page).getByRole('group', { name: 'View' }).getByRole('button', { name: 'List' }).click();
  await hq(page).getByRole('button', { name: /^Finance/ }).click();
  await expect(rail(page).getByRole('button', { name: /^First floor\./ })).toHaveAttribute('aria-current', 'true', { timeout: 30_000 });
  await expect(page.getByTestId('hq-floor-live')).toContainText('First floor');
  await expect(hq(page).getByRole('complementary', { name: 'Room Finance' })).toBeVisible();
  // The camera frames the room on its floor and says so.
  await expect(live(page)).toContainText('Now showing Finance.', { timeout: 30_000 });
  await sceneSettled(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
});

test('the keyboard path: a room of another floor opens with Enter and the floor rail and the room panel follow', async ({ page }) => {
  await openBuilding(page);
  await hq(page).getByRole('group', { name: 'View' }).getByRole('button', { name: 'List' }).click();
  const sales = hq(page).getByRole('button', { name: /^Sales and Marketing/ });
  await sales.focus();
  await page.keyboard.press('Enter');
  await expect(rail(page).getByRole('button', { name: /^First floor\./ })).toHaveAttribute('aria-current', 'true', { timeout: 30_000 });
  await expect(hq(page).getByRole('complementary', { name: 'Room Sales and Marketing' })).toBeVisible();
  await expect(live(page)).toContainText('Now showing Sales and Marketing.', { timeout: 30_000 });
  // Back down: a Ground floor room from the list returns to the Ground floor.
  await hq(page).getByRole('group', { name: 'View' }).getByRole('button', { name: 'List' }).click();
  await hq(page).getByRole('button', { name: /^Customer Care/ }).press('Enter');
  await expect(rail(page).getByRole('button', { name: /^Ground floor\./ })).toHaveAttribute('aria-current', 'true', { timeout: 30_000 });
  await expect(hq(page).getByRole('complementary', { name: 'Room Customer Care' })).toBeVisible();
});

// The fixture's live meeting lists one stored participant, agent_care, whose own desk is in Customer Care on the Ground floor and who
// has no chosen look. The figures on the canvas carry their look codes (`data-figure-looks`, build first).
const figures = async (page: Page) => ({
  count: Number(await canvas(page).getAttribute('data-model-figures')),
  looks: ((await canvas(page).getAttribute('data-figure-looks')) ?? '').split(' ').filter(Boolean),
});

test('a live meeting held in a room seats its stored participant there (neutral, no look set) instead of at the desk; no room, no attendee', async ({ browser }) => {
  const read = async (meetingRoomId: string | null) => {
    const page = await browser.newPage({ deviceScaleFactor: 1 });
    await openBuilding(page, { campus: 'api', meetings: 'live', meetingRoomId });
    await sceneSettled(page);
    await expect.poll(async () => (await figures(page)).count, { timeout: 60_000 }).toBeGreaterThan(0);
    const out = await figures(page);
    await page.close();
    return out;
  };
  const free = await read(null);
  const held = await read('room_meet');
  // One person, one figure: the same number of people, the participant moved from the desk to the meeting table.
  expect(held.count).toBe(free.count);
  expect(held.count).toBe(held.looks.length);
  const neutral = (looks: string[]) => looks.filter((l) => l.startsWith('neutral/')).length;
  // The attendee has no chosen look, so the meeting draws the neutral build; at the desk the id's default build is used.
  const moved = free.looks.filter((l) => !held.looks.includes(l));
  expect(moved.length).toBeLessThanOrEqual(1);
  if (moved.length === 1) expect(neutral(held.looks)).toBe(neutral(free.looks) + (moved[0].startsWith('neutral/') ? 0 : 1));
  expect(neutral(held.looks)).toBeGreaterThanOrEqual(1);
});
