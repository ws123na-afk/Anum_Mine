import AxeBuilder from '@axe-core/playwright';
import { expect as baseExpect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { openHq } from './support/hqApi';
import { mockFlow } from './support/hqFlowApi';
import { mockProvisioning } from './support/hqProvisioningApi';
import { FX, greenRegistry, mockGreenAction, mockGreenWork, newState, person, type GreenState } from './support/hqGreenConcreteApi';

const expect = baseExpect.configure({ timeout: 20_000 });

// Green Concrete (docs/ANUM_MINE_GREEN_CONCRETE.md): the owner's company with a command center, L shaped tables, a day and a night shift
// and a manager at the back. The platform's own records (presence, tasks) say who is doing what; the room shows it at the right desk,
// and a person off shift is never lit. The API is mocked at http://localhost:8000/**; the page clock is pinned. HQ_SHOTS_DIR keeps screenshots.
test.use({ actionTimeout: 30_000, deviceScaleFactor: 1, isMobile: false, hasTouch: false });
test.describe.configure({ timeout: 180_000 });

// Dubai is UTC+4: 12:00 in Dubai is 08:00 UTC (day shift), 23:30 in Dubai is 19:30 UTC (night shift).
const NOON = new Date(Date.UTC(2026, 9, 26, 8, 0, 0));
const NIGHT = new Date(Date.UTC(2026, 9, 26, 19, 30, 0));
const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
const hq = (page: Page) => page.locator('.hqLive');
const DAY_ORDER = ['Raed', 'Hazrat', 'Mai', 'Shahzeb', 'Ijaz', 'Badea', 'Ahmed Elbendari'];
const NIGHT_ORDER = ['Ali Zeehan', 'Amjad', 'Rana', 'Ahmed Elbendari'];

async function shot(page: Page, name: string) {
  const dir = process.env.HQ_SHOTS_DIR;
  if (!dir) return;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: join(dir, `${name}.png`) });
}

async function noOverflow(page: Page, root: string) {
  const problems = await page.evaluate((selector) => {
    const out: string[] = [];
    if (document.documentElement.scrollWidth > window.innerWidth) out.push(`page scrolls sideways (${document.documentElement.scrollWidth} > ${window.innerWidth})`);
    const scope = document.querySelector<HTMLElement>(selector);
    if (!scope) return [...out, `no ${selector}`];
    const box = scope.getBoundingClientRect();
    if (box.left < -1 || box.right > window.innerWidth + 1) out.push('the panel leaves the screen sideways');
    for (const el of scope.querySelectorAll<HTMLElement>('strong, small, h2, h3, button, th, td')) {
      if (!el.offsetParent || el.closest('.hqGcTableWrap') || el.getBoundingClientRect().width <= 2) continue;
      const style = getComputedStyle(el);
      if ((style.overflow !== 'visible' || style.overflowX !== 'visible') && style.textOverflow !== 'ellipsis' && el.scrollWidth > el.clientWidth + 1) out.push(`clipped: ${el.textContent?.trim().slice(0, 40)}`);
    }
    return out;
  }, root);
  expect(problems).toEqual([]);
}

interface Opened { state: GreenState; action?: boolean }
/** Opens the headquarters of Green Concrete at a pinned time, in the list view with the Command Center panel open. */
async function openRoom(page: Page, options: { at?: Date; state?: GreenState; locale?: 'en' | 'ar'; viewport?: readonly [number, number]; building?: Record<string, unknown> } = {}): Promise<Opened> {
  const at = options.at ?? NOON;
  if (options.viewport) await page.setViewportSize({ width: options.viewport[0], height: options.viewport[1] });
  await page.clock.setFixedTime(at);
  const state = options.state ?? newState(() => at.getTime());
  state.clock = () => at.getTime();
  state.created = true;
  await openHq(page, {
    registry: greenRegistry(options.building), locale: options.locale, noWebgl: true,
    beforeGoto: async (p) => { await mockFlow(p); await mockProvisioning(p); await mockGreenWork(p, state, options.building); },
  });
  await expect(hq(page)).toBeVisible();
  await hq(page).getByRole('button', { name: /^(List|قائمة)$/ }).click();
  await hq(page).locator('.hqListRoom').filter({ hasText: /Command Center|مركز القيادة/ }).first().click();
  await expect(hq(page).getByTestId('shift-bar')).toBeVisible();
  await hq(page).locator('.hqSide').first().scrollIntoViewIfNeeded();
  return { state };
}
const rows = (page: Page) => hq(page).locator('.hqSide [data-testid="room-desk"], .hqSide .hqVacant');
const names = async (page: Page) => (await hq(page).locator('.hqSide [data-testid="room-desk"] strong').allTextContents()).map((n) => n.trim());
const rowOf = (page: Page, name: string) => hq(page).locator('.hqSide [data-testid="room-desk"]').filter({ hasText: name });

test('day shift: the room shows the six day people left to right along the L, then the manager', async ({ page }) => {
  await openRoom(page);
  expect(await names(page)).toEqual(DAY_ORDER);
  await expect(hq(page).getByTestId('shift-showing')).toContainText('Day shift');
  await expect(hq(page).getByTestId('shift-showing')).toContainText('on shift now');
  await expect(hq(page).getByTestId('shift-showing')).toContainText('07:00 to 19:00');
  // The hours are defaults: the room says so, it never presents them as a fact.
  await expect(hq(page).getByTestId('shift-default-note')).toContainText('not yet confirmed');
  await expect(rowOf(page, 'Raed')).toContainText('Business analyst');
  await expect(rowOf(page, 'Hazrat')).toContainText('Data monitoring');
  await expect(rowOf(page, 'Shahzeb')).toContainText('Tyre planning engineer');
  await expect(rowOf(page, 'Ijaz')).toContainText('Data collector');
  await expect(rowOf(page, 'Ahmed Elbendari')).toContainText('PMV manager');
  await expect(rowOf(page, 'Ahmed Elbendari').getByTestId('desk-shift')).toContainText('Manager seat, always on');
  await expect(rowOf(page, 'Raed').getByTestId('desk-shift')).toContainText('Day shift, 07:00 to 19:00');
  // Nobody is invented: the room panel counts the seats of the shown shift.
  await expect(hq(page).locator('.hqSide .hqStats')).toContainText('7');
  await shot(page, 'green-day-room-panel');
});

test('the toggle shows the night shift, whose people are off shift, not absent', async ({ page }) => {
  await openRoom(page);
  await hq(page).getByTestId('shift-toggle').click();
  expect(await names(page)).toEqual(NIGHT_ORDER);
  await expect(hq(page).getByTestId('shift-showing')).toContainText('Night shift');
  await expect(hq(page).getByTestId('shift-showing')).toContainText('off shift now');
  for (const n of ['Ali Zeehan', 'Amjad', 'Rana']) {
    await expect(rowOf(page, n).getByTestId('desk-offshift')).toHaveText('Off shift');
    await expect(rowOf(page, n)).not.toContainText('Idle');
    await expect(rowOf(page, n)).not.toContainText(/absent/i);
  }
  await expect(rowOf(page, 'Amjad').getByTestId('desk-shift')).toContainText('Night shift, 19:00 to 07:00');
  // The manager is always on.
  await expect(rowOf(page, 'Ahmed Elbendari').getByTestId('desk-offshift')).toHaveCount(0);
  await shot(page, 'green-day-toggled-to-night');
  await hq(page).getByTestId('shift-toggle').click();
  expect(await names(page)).toEqual(DAY_ORDER);
});

test('work given to Raed shows at his desk with the real record and its time; the others stay as they are', async ({ page }) => {
  const state = newState();
  state.work.raed = { state: 'working', title: 'Check the tyre data feed' };
  state.work.hazrat = { state: 'queued', title: 'Review the weekly sensor sample' };
  state.work.shahzeb = { state: 'waiting_approval', title: 'Approve the tyre rotation plan' };
  await openRoom(page, { state });
  await expect(rowOf(page, 'Raed').locator('.hqPill')).toHaveText('Working');
  await expect(rowOf(page, 'Hazrat').locator('.hqPill')).toHaveText('Queued');
  await expect(rowOf(page, 'Shahzeb').locator('.hqPill')).toHaveText('Waiting for approval');
  await expect(rowOf(page, 'Mai').locator('.hqPill')).toHaveText('Idle');
  await rowOf(page, 'Raed').click();
  const panel = hq(page).getByRole('complementary', { name: /Raed/ });
  await expect(panel.getByTestId('agent-shift')).toContainText('On shift now');
  await expect(panel).toContainText('Verified at');
  await expect(panel).toContainText('Check the tyre data feed');
  await expect(panel.getByTestId('agent-state-help')).toContainText('A run is executing a task');
  await shot(page, 'green-work-raed');
});

test('work given to a night person during the day: off shift, work waiting, the screen is not lit', async ({ page }) => {
  const state = newState();
  state.work.amjad = { state: 'queued', title: 'Collect the depot readings' };
  await openRoom(page, { state });
  await hq(page).getByTestId('shift-toggle').click();
  const amjad = rowOf(page, 'Amjad');
  await expect(amjad.getByTestId('desk-offshift')).toHaveText('Off shift, work waiting');
  await expect(amjad.getByTestId('desk-offshift')).toHaveAttribute('data-waiting', 'true');
  await expect(amjad.getByTestId('desk-shift')).toContainText('Collect the depot readings');
  await expect(rowOf(page, 'Rana').getByTestId('desk-offshift')).toHaveText('Off shift');
  await amjad.click();
  const panel = hq(page).getByRole('complementary', { name: /Amjad/ });
  await expect(panel.getByTestId('agent-shift')).toContainText('Off shift, work waiting');
  await expect(panel.getByTestId('desk-offshift')).toHaveText('Off shift, work waiting');
  await expect(panel).toContainText('Collect the depot readings');
  await shot(page, 'green-offshift-work-waiting');
});

test('at night the night people show and the day people go off shift', async ({ page }) => {
  await openRoom(page);
  expect(await names(page)).toEqual(DAY_ORDER);
  // The clock moves to 23:30 in Dubai; the page reads it again when it gets focus (and every 15 seconds).
  await page.clock.setFixedTime(NIGHT);
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(hq(page).getByTestId('shift-showing')).toContainText('Night shift');
  await expect(hq(page).getByTestId('shift-showing')).toContainText('on shift now');
  expect(await names(page)).toEqual(NIGHT_ORDER);
  await expect(rowOf(page, 'Ali Zeehan').getByTestId('desk-offshift')).toHaveCount(0);
  await shot(page, 'green-night-room-panel');
  await hq(page).getByTestId('shift-toggle').click();
  expect(await names(page)).toEqual(DAY_ORDER);
  for (const n of ['Raed', 'Hazrat', 'Mai', 'Shahzeb', 'Ijaz', 'Badea']) await expect(rowOf(page, n).getByTestId('desk-offshift')).toHaveText('Off shift');
});

test('opened at night the room starts with the night shift, and edited hours move the change', async ({ page }) => {
  await openRoom(page, { at: NIGHT });
  expect(await names(page)).toEqual(NIGHT_ORDER);
  await expect(hq(page).getByTestId('shift-bar')).toHaveAttribute('data-active', 'night');
});

test('the hours saved on the building replace the defaults and are shown as the owner\'s', async ({ page }) => {
  // 12:00 in Dubai with the day shift ending at 11:00: the night shift is in force.
  await openRoom(page, { building: { shift_day_start: '05:00', shift_night_start: '11:00' } });
  await expect(hq(page).getByTestId('shift-bar')).toHaveAttribute('data-active', 'night');
  await expect(hq(page).getByTestId('shift-showing')).toContainText('11:00 to 05:00');
  await expect(hq(page).getByTestId('shift-default-note')).toHaveCount(0);
});

for (const size of SIZES) {
  test(`the room panel and the shift bar fit at ${size[0]}x${size[1]}, English and Arabic`, async ({ page }) => {
    await openRoom(page, { viewport: size });
    await noOverflow(page, '.hqSide');
    await shot(page, `green-room-panel-en-${size[0]}x${size[1]}`);
  });
}

test('Arabic: right to left, names and roles read in Arabic, the shift is told in Arabic', async ({ page }) => {
  await openRoom(page, { locale: 'ar' });
  expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
  await expect(hq(page).getByTestId('shift-showing')).toContainText('وردية النهار');
  await expect(rowOf(page, 'Raed')).toContainText('محلل أعمال');
  await expect(rowOf(page, 'Raed').getByTestId('desk-shift')).toContainText('وردية النهار');
  await hq(page).getByTestId('shift-toggle').click();
  await expect(rowOf(page, 'Rana').getByTestId('desk-offshift')).toHaveText('خارج الوردية');
  await noOverflow(page, '.hqSide');
  await shot(page, 'green-room-panel-ar');
});

test('axe: the room panel with the shift bar, English and Arabic', async ({ page }) => {
  for (const locale of ['en', 'ar'] as const) {
    await openRoom(page, { locale });
    await page.waitForTimeout(400);
    const results = await new AxeBuilder({ page }).include('.hqLive').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
    expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  }
});

// ---------------------------------------------------------------- the owner's action

async function openAction(page: Page, options: Parameters<typeof mockGreenAction>[2] & { locale?: 'en' | 'ar'; viewport?: readonly [number, number] } = {}) {
  if (options.viewport) await page.setViewportSize({ width: options.viewport[0], height: options.viewport[1] });
  await page.clock.setFixedTime(NOON);
  const state = newState(() => NOON.getTime());
  await openHq(page, {
    registry: greenRegistry(), locale: options.locale, noWebgl: true,
    beforeGoto: async (p) => { await mockFlow(p); await mockProvisioning(p); await mockGreenWork(p, state); await mockGreenAction(p, state, options); },
  });
  state.created = options.created ?? false;
  await expect(hq(page)).toBeVisible();
  await hq(page).getByRole('button', { name: /Office tools|أدوات المكتب/ }).click();
  await hq(page).getByRole('button', { name: /Add Green Concrete|إضافة جرين كونكريت/ }).click();
  await expect(page.getByTestId('gc-dialog')).toBeVisible();
  return state;
}
const dialog = (page: Page) => page.getByTestId('gc-dialog');

test('Add Green Concrete: the preview lists everything, the hours are defaults, and a click creates it', async ({ page }) => {
  const state = await openAction(page);
  await expect(dialog(page).getByTestId('gc-totals')).toContainText('1 building, 1 floor, 1 room, 10 desks, 10 paused agents');
  await expect(dialog(page).getByTestId('gc-hours')).toContainText('day 07:00 to 19:00, night 19:00 to 07:00');
  await expect(dialog(page)).toContainText('defaults you must confirm');
  await expect(dialog(page)).toContainText('starts paused');
  await expect(dialog(page)).toContainText('No gender, pronoun or background is guessed');
  const table = dialog(page).getByTestId('gc-people');
  await expect(table.locator('tbody tr')).toHaveCount(10);
  const raed = table.locator('tr[data-person="raed"]');
  await expect(raed).toContainText('Business analyst');
  await expect(raed).toContainText('Day shift');
  await expect(raed).toContainText('Day shift seat 1');
  await expect(table.locator('tr[data-person="ali-zeehan"]')).toContainText('Night shift seat 1');
  await expect(table.locator('tr[data-person="ahmed-elbendari"]')).toContainText('Manager desk');
  await expect(table.locator('tr[data-person="ahmed-elbendari"]')).toContainText('PMV manager');
  await expect(dialog(page).getByRole('img', { name: /L shaped tables/ })).toBeVisible();
  await shot(page, 'green-preview-dialog');
  await dialog(page).getByTestId('gc-confirm').click();
  await expect(dialog(page).getByTestId('gc-done')).toContainText('Created 1 building, 1 floor, 1 room, 10 desks and 10 paused agents');
  await expect(dialog(page).getByTestId('gc-done')).toContainText('Everyone is paused');
  // The confirm sent exactly what was shown, by a click.
  expect(state.confirms).toHaveLength(1);
  expect(state.confirms[0]).toMatchObject({ input_mode: 'click', timezone: 'Asia/Dubai' });
  expect(String(state.confirms[0].payload_hash)).toMatch(/^[0-9a-f]{64}$/);
  await shot(page, 'green-created-dialog');
});

test('Add Green Concrete: already set up says so and offers nothing to click', async ({ page }) => {
  await openAction(page, { created: true });
  await expect(dialog(page).getByTestId('gc-totals')).toContainText('already set up');
  await expect(dialog(page).getByTestId('gc-confirm')).toBeDisabled();
});

test('Add Green Concrete: a person who is not an owner can read but not confirm', async ({ page }) => {
  await openAction(page, { viewer: true });
  await expect(dialog(page)).toContainText('Only a workspace owner can confirm');
  await expect(dialog(page).getByTestId('gc-confirm')).toBeDisabled();
});

test('Add Green Concrete: denied, stale and failed states say what happened and how to go on', async ({ page }) => {
  await openAction(page, { denied: true });
  await expect(dialog(page).getByRole('alert')).toContainText('You cannot read the office');
});
test('Add Green Concrete: a changed plan (409) asks to review again and creates nothing', async ({ page }) => {
  await openAction(page, { failConfirm: 409 });
  await dialog(page).getByTestId('gc-confirm').click();
  await expect(dialog(page).getByRole('alert')).toContainText('changed since you opened');
  await expect(dialog(page).getByRole('button', { name: 'Review again' })).toBeVisible();
});
test('Add Green Concrete: a server failure is shown with its reason', async ({ page }) => {
  await openAction(page, { failConfirm: 500 });
  await dialog(page).getByTestId('gc-confirm').click();
  await expect(dialog(page).getByRole('alert')).toContainText('Nothing was finished');
});

test('Add Green Concrete: keyboard, Escape closes and returns the focus, Arabic reads right to left', async ({ page }) => {
  await openAction(page, { locale: 'ar' });
  await expect(dialog(page)).toContainText('إضافة جرين كونكريت');
  expect(await dialog(page).evaluate((el) => el.parentElement?.getAttribute('dir'))).toBe('rtl');
  await expect(dialog(page).getByTestId('gc-people').locator('tbody tr')).toHaveCount(10);
  await expect(dialog(page).getByTestId('gc-people').locator('tr[data-person="raed"]')).toContainText('محلل أعمال');
  await shot(page, 'green-preview-dialog-ar');
  await page.keyboard.press('Escape');
  await expect(dialog(page)).toHaveCount(0);
});

for (const size of SIZES) {
  test(`Add Green Concrete fits at ${size[0]}x${size[1]} without sideways scrolling`, async ({ page }) => {
    await openAction(page, { viewport: size });
    await noOverflow(page, '.hqGcDialog');
    await shot(page, `green-dialog-${size[0]}x${size[1]}`);
  });
}

test('axe: the Add Green Concrete dialog, English and Arabic', async ({ page }) => {
  for (const locale of ['en', 'ar'] as const) {
    await openAction(page, { locale });
    const results = await new AxeBuilder({ page }).include('.hqGcDialog').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
    expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  }
});

test('the facts the spec relies on are the planner fixture', () => {
  expect(FX.people.map((p) => p.name)).toEqual(['Raed', 'Hazrat', 'Mai', 'Shahzeb', 'Ijaz', 'Badea', 'Ali Zeehan', 'Amjad', 'Rana', 'Ahmed Elbendari']);
  expect(person('raed').shift).toBe('day');
});
