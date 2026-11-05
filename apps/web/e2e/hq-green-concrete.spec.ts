import AxeBuilder from '@axe-core/playwright';
import { expect as baseExpect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { openHq } from './support/hqApi';
import { openBuilder } from './support/hqBuilderApi';
import { mockStarterCampus } from './support/hqStarterCampusApi';
import { mockFlow } from './support/hqFlowApi';
import { mockProvisioning } from './support/hqProvisioningApi';
import { FX, greenRegistry, mockGreenAction, mockGreenWork, newState, olderDesks, person, type GreenState } from './support/hqGreenConcreteApi';

const expect = baseExpect.configure({ timeout: 20_000 });

// Green Concrete (docs/ANUM_MINE_GREEN_CONCRETE.md): the owner's company with a command center, one table with a seat for every person
// and a manager desk at the back. Each person has their own working hours (the owner's answer of 28 October 2026; no night shift). The
// platform's own records (presence, tasks) say who is doing what; the room shows it at the right desk, and a person outside their hours is
// never lit and has no figure. The API is mocked at http://localhost:8000/**; the page clock is pinned. HQ_SHOTS_DIR keeps screenshots.
test.use({ actionTimeout: 30_000, deviceScaleFactor: 1, isMobile: false, hasTouch: false });
test.describe.configure({ timeout: 180_000 });

// Dubai is UTC+4 all year: a local hour h is UTC h - 4.
const dubai = (h: number, m = 0) => new Date(Date.UTC(2026, 9, 26, h - 4, m, 0));
const NOON = dubai(12);
const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
const hq = (page: Page) => page.locator('.hqLive');
const ALL = ['Raed', 'Hazrat', 'Mai', 'Shahzeb', 'Ijaz', 'Badea', 'Ali Zeehan', 'Amjad', 'Rana', 'Ahmed Elbendari'];
const MORNING = ['Ijaz', 'Ali Zeehan', 'Rana'];
const OFFICE = ['Raed', 'Hazrat', 'Mai', 'Shahzeb', 'Badea', 'Ahmed Elbendari'];
// Who is inside their own hours at each pinned time (06:00 to 14:00, 08:00 to 17:00, Amjad 13:00 to 22:00).
const ON_AT: ReadonlyArray<readonly [string, Date, readonly string[]]> = [
  ['07:00', dubai(7), MORNING],
  ['12:00', dubai(12), [...MORNING, ...OFFICE]],
  ['16:00', dubai(16), [...OFFICE, 'Amjad']],
  ['18:30', dubai(18, 30), ['Amjad']],
  ['21:00', dubai(21), ['Amjad']],
];

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
    for (const el of scope.querySelectorAll<HTMLElement>('strong, small, h2, h3, button, th, td, li')) {
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
async function openRoom(page: Page, options: { at?: Date; state?: GreenState; locale?: 'en' | 'ar'; viewport?: readonly [number, number]; building?: Record<string, unknown>; desks?: Record<string, unknown>[] } = {}): Promise<Opened> {
  const at = options.at ?? NOON;
  if (options.viewport) await page.setViewportSize({ width: options.viewport[0], height: options.viewport[1] });
  await page.clock.setFixedTime(at);
  const state = options.state ?? newState(() => at.getTime());
  state.clock = () => at.getTime();
  state.created = true;
  await openHq(page, {
    registry: greenRegistry(options.building, options.desks), locale: options.locale, noWebgl: true,
    beforeGoto: async (p) => { await mockFlow(p); await mockProvisioning(p); await mockGreenWork(p, state, options.building, options.desks); },
  });
  await expect(hq(page)).toBeVisible();
  await hq(page).getByRole('button', { name: /^(List|قائمة)$/ }).click();
  await hq(page).locator('.hqListRoom').filter({ hasText: /Command Center|مركز القيادة/ }).first().click();
  await expect(hq(page).getByTestId('shift-bar')).toBeVisible();
  await hq(page).locator('.hqSide').first().scrollIntoViewIfNeeded().catch(() => undefined);
  return { state };
}
const names = async (page: Page) => (await hq(page).locator('.hqSide [data-testid="room-desk"] strong').allTextContents()).map((n) => n.trim());
const rowOf = (page: Page, name: string) => hq(page).locator('.hqSide [data-testid="room-desk"]').filter({ hasText: name });
const wallStates = (page: Page) => hq(page).getByRole('region', { name: 'Operations wall' }).getByRole('list', { name: 'Agents by state' });

test('the room lists every person at their own seat, in seat order, with their own working hours', async ({ page }) => {
  await openRoom(page);
  expect(await names(page)).toEqual(ALL);
  await expect(hq(page).getByTestId('shift-showing')).toContainText('9 of 10 on shift now');
  await expect(hq(page).getByTestId('shift-showing')).toContainText('Asia/Dubai');
  const windows = hq(page).getByTestId('shift-window');
  await expect(windows).toHaveCount(3);
  await expect(windows.nth(0)).toContainText('06:00 to 14:00');
  await expect(windows.nth(0)).toContainText('People: 3');
  await expect(windows.nth(1)).toContainText('08:00 to 17:00');
  await expect(windows.nth(1)).toContainText('People: 6');
  await expect(windows.nth(2)).toContainText('13:00 to 22:00');
  await expect(windows.nth(2)).toHaveAttribute('data-on', 'false');
  // The hours are the owner's: no "not confirmed" note, and the room says where they are changed.
  await expect(hq(page).getByTestId('shift-default-note')).toHaveCount(0);
  await expect(hq(page).getByTestId('shift-edit')).toContainText('in the builder');
  await expect(hq(page).getByTestId('shift-toggle')).toHaveCount(0);
  await expect(rowOf(page, 'Raed')).toContainText('Business analyst');
  await expect(rowOf(page, 'Raed')).toContainText('Seat 1');
  await expect(rowOf(page, 'Raed').getByTestId('desk-shift')).toHaveText('Works 08:00 to 17:00');
  await expect(rowOf(page, 'Ijaz').getByTestId('desk-shift')).toHaveText('Works 06:00 to 14:00');
  await expect(rowOf(page, 'Amjad').getByTestId('desk-shift')).toHaveText('Works 13:00 to 22:00');
  await expect(rowOf(page, 'Ali Zeehan')).toContainText('Seat 7');
  await expect(rowOf(page, 'Ahmed Elbendari')).toContainText('PMV manager');
  await expect(rowOf(page, 'Ahmed Elbendari')).toContainText('Manager desk');
  await expect(rowOf(page, 'Ahmed Elbendari').getByTestId('desk-shift')).toHaveText('Works 08:00 to 17:00');
  // Nobody is invented: the room panel counts its ten desks.
  await expect(hq(page).locator('.hqSide .hqStats')).toContainText('10');
  await shot(page, 'green-noon-room-panel');
});

for (const [label, at, on] of ON_AT) {
  test(`at ${label} in Dubai exactly the people inside their hours are on shift, and every count agrees`, async ({ page }) => {
    await openRoom(page, { at });
    expect(await names(page)).toEqual(ALL);
    await expect(hq(page).getByTestId('shift-showing')).toContainText(`${on.length} of 10 on shift now`);
    for (const n of ALL) {
      const off = rowOf(page, n).getByTestId('desk-offshift');
      if (on.includes(n)) {
        await expect(off).toHaveCount(0);
        await expect(rowOf(page, n).locator('.hqPill')).toHaveText('Idle');
      } else {
        await expect(off).toHaveText('Off shift');
        await expect(rowOf(page, n)).not.toContainText(/absent/i);
      }
    }
    await expect(wallStates(page)).toContainText(`Off shift ${10 - on.length}`);
    await expect(wallStates(page)).toContainText(`Idle ${on.length}`);
    await shot(page, `green-room-${label.replace(':', '')}`);
  });
}

test('the clock moving from 12:00 to 16:00 sends the morning people off shift and brings Amjad on', async ({ page }) => {
  await openRoom(page);
  await expect(rowOf(page, 'Ijaz').getByTestId('desk-offshift')).toHaveCount(0);
  await expect(rowOf(page, 'Amjad').getByTestId('desk-offshift')).toHaveText('Off shift');
  // The page reads the clock again when it gets focus (and every 15 seconds).
  await page.clock.setFixedTime(dubai(16));
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(hq(page).getByTestId('shift-showing')).toContainText('7 of 10 on shift now');
  for (const n of MORNING) await expect(rowOf(page, n).getByTestId('desk-offshift')).toHaveText('Off shift');
  await expect(rowOf(page, 'Amjad').getByTestId('desk-offshift')).toHaveCount(0);
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
  await expect(panel.getByTestId('agent-shift')).toContainText('Works 08:00 to 17:00');
  await expect(panel.getByTestId('agent-shift')).toContainText('On shift now');
  await expect(panel).toContainText('Verified at');
  await expect(panel).toContainText('Check the tyre data feed');
  await expect(panel.getByTestId('agent-state-help')).toContainText('A run is executing a task');
  await shot(page, 'green-work-raed');
});

test('work given to Amjad before his hours: off shift, work waiting, the screen is not lit', async ({ page }) => {
  const state = newState();
  state.work.amjad = { state: 'queued', title: 'Collect the depot readings' };
  await openRoom(page, { state });
  const amjad = rowOf(page, 'Amjad');
  await expect(amjad.getByTestId('desk-offshift')).toHaveText('Off shift, work waiting');
  await expect(amjad.getByTestId('desk-offshift')).toHaveAttribute('data-waiting', 'true');
  await expect(amjad.getByTestId('desk-shift')).toContainText('Collect the depot readings');
  await expect(rowOf(page, 'Rana').getByTestId('desk-offshift')).toHaveCount(0);
  await amjad.click();
  const panel = hq(page).getByRole('complementary', { name: /Amjad/ });
  await expect(panel.getByTestId('agent-shift')).toContainText('Off shift, work waiting');
  await expect(panel.getByTestId('desk-offshift')).toHaveText('Off shift, work waiting');
  await expect(panel).toContainText('Collect the depot readings');
  await shot(page, 'green-offshift-work-waiting');
});

test('hours the owner edits on a seat move that person, including a window over midnight', async ({ page }) => {
  const desks = FX.desks.map((d) => (d.id === person('raed').desk_id ? { ...d, work_start: '22:00', work_end: '06:00' } : d));
  await openRoom(page, { desks, at: dubai(23) });
  await expect(rowOf(page, 'Raed').getByTestId('desk-shift')).toHaveText('Works 22:00 to 06:00');
  await expect(rowOf(page, 'Raed').getByTestId('desk-offshift')).toHaveCount(0);
  await expect(hq(page).getByTestId('shift-showing')).toContainText('1 of 10 on shift now');
  await expect(wallStates(page)).toContainText('Off shift 9');
});

test('an earlier build with day and night seats keeps working: the shift in force sits at the shared tables, with the default note', async ({ page }) => {
  await openRoom(page, { desks: olderDesks() });
  expect(await names(page)).toEqual(['Raed', 'Hazrat', 'Mai', 'Shahzeb', 'Ijaz', 'Badea', 'Ahmed Elbendari']);
  await expect(rowOf(page, 'Raed').getByTestId('desk-shift')).toHaveText('Day shift, 07:00 to 19:00');
  await expect(hq(page).getByTestId('shift-default-note')).toContainText('not yet confirmed');
  await page.clock.setFixedTime(dubai(23, 30));
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  // At night the night people take the shared places; the day seats nobody shares stay, off shift.
  await expect.poll(() => names(page)).toEqual(['Shahzeb', 'Ijaz', 'Badea', 'Ali Zeehan', 'Amjad', 'Rana', 'Ahmed Elbendari']);
  await expect(rowOf(page, 'Amjad').getByTestId('desk-shift')).toHaveText('Night shift, 19:00 to 07:00');
  await expect(rowOf(page, 'Shahzeb').getByTestId('desk-offshift')).toHaveText('Off shift');
  await expect(rowOf(page, 'Ahmed Elbendari').getByTestId('desk-shift')).toHaveText('Manager seat, always on');
});

for (const size of SIZES) {
  test(`the room panel and the working hours fit at ${size[0]}x${size[1]}`, async ({ page }) => {
    await openRoom(page, { viewport: size });
    await noOverflow(page, '.hqSide');
    await shot(page, `green-room-panel-en-${size[0]}x${size[1]}`);
  });
}

test('Arabic: right to left, roles and hours read in Arabic', async ({ page }) => {
  await openRoom(page, { locale: 'ar' });
  expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
  await expect(hq(page).getByTestId('shift-showing')).toContainText('9 من 10 في الوردية الآن');
  await expect(rowOf(page, 'Raed')).toContainText('محلل أعمال');
  await expect(rowOf(page, 'Raed')).toContainText('المقعد 1');
  await expect(rowOf(page, 'Raed').getByTestId('desk-shift')).toContainText('يعمل من 08:00 إلى 17:00');
  await expect(rowOf(page, 'Amjad').getByTestId('desk-offshift')).toHaveText('خارج الوردية');
  await expect(hq(page).getByTestId('shift-edit')).toContainText('المنشئ');
  await noOverflow(page, '.hqSide');
  await shot(page, 'green-room-panel-ar');
});

for (const locale of ['en', 'ar'] as const) {
  test(`axe: the room panel with the working hours (${locale})`, async ({ page }) => {
    await openRoom(page, { locale });
    await page.waitForTimeout(400);
    const results = await new AxeBuilder({ page }).include('.hqLive').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
    expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  });
}

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

test('Add Green Concrete: the preview lists everything with each person\'s hours, and a click creates it', async ({ page }) => {
  const state = await openAction(page);
  await expect(dialog(page).getByTestId('gc-totals')).toContainText('1 building, 1 floor, 1 room, 10 desks, 10 paused agents');
  const hours = dialog(page).getByTestId('gc-hours');
  await expect(hours).toContainText('06:00 to 14:00: Ijaz, Ali Zeehan, Rana');
  await expect(hours).toContainText('08:00 to 17:00: Raed, Hazrat, Mai, Shahzeb, Badea, Ahmed Elbendari');
  await expect(hours).toContainText('13:00 to 22:00: Amjad');
  await expect(hours).toContainText('There is no night shift');
  await expect(dialog(page)).not.toContainText('defaults you must confirm');
  await expect(dialog(page).getByTestId('gc-older')).toHaveCount(0);
  await expect(dialog(page)).toContainText('starts paused');
  await expect(dialog(page)).toContainText('No gender, pronoun or background is guessed');
  const table = dialog(page).getByTestId('gc-people');
  await expect(table.locator('tbody tr')).toHaveCount(10);
  const raed = table.locator('tr[data-person="raed"]');
  await expect(raed).toContainText('Business analyst');
  await expect(raed).toContainText('08:00 to 17:00');
  await expect(raed).toContainText('Seat 1');
  await expect(table.locator('tr[data-person="ali-zeehan"]')).toContainText('Seat 7');
  await expect(table.locator('tr[data-person="ali-zeehan"]')).toContainText('06:00 to 14:00');
  await expect(table.locator('tr[data-person="amjad"]')).toContainText('13:00 to 22:00');
  await expect(table).not.toContainText(/Night shift|Day shift/);
  await expect(table.locator('tr[data-person="ahmed-elbendari"]')).toContainText('Manager desk');
  await expect(table.locator('tr[data-person="ahmed-elbendari"]')).toContainText('PMV manager');
  await expect(dialog(page).getByRole('img', { name: /nine seats/ })).toBeVisible();
  // Every person has a numbered place of their own on the plan.
  await expect(dialog(page).locator('.hqGcMap .seat')).toHaveCount(10);
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

test('Add Green Concrete is also reachable from Office tools, Set up my campus', async ({ page }) => {
  await page.clock.setFixedTime(NOON);
  const state = newState(() => NOON.getTime());
  await openHq(page, {
    registry: greenRegistry(), noWebgl: true,
    beforeGoto: async (p) => { await mockFlow(p); await mockProvisioning(p); await mockStarterCampus(p); await mockGreenWork(p, state); await mockGreenAction(p, state, {}); },
  });
  await hq(page).getByRole('button', { name: /Office tools/ }).click();
  await hq(page).getByRole('button', { name: 'Set up my campus' }).click();
  await page.getByTestId('sc-green-open').click();
  await expect(dialog(page)).toBeVisible();
  await expect(dialog(page).getByTestId('gc-people').locator('tbody tr')).toHaveCount(10);
});

test('Add Green Concrete: already set up says so and offers nothing to click', async ({ page }) => {
  await openAction(page, { created: true });
  await expect(dialog(page).getByTestId('gc-totals')).toContainText('already set up');
  await expect(dialog(page).getByTestId('gc-confirm')).toBeDisabled();
  await expect(dialog(page).getByTestId('gc-older')).toHaveCount(0);
});

test('Add Green Concrete: an earlier build is never moved; the dialog says how to give each person their hours', async ({ page }) => {
  await openAction(page, { created: true, older: true });
  await expect(dialog(page).getByTestId('gc-totals')).toContainText('already set up');
  const older = dialog(page).getByTestId('gc-older');
  await expect(older).toContainText('10 seats of an earlier Green Concrete build');
  await expect(older).toContainText('nobody there is moved');
  await expect(older).toContainText('Manage spaces');
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

for (const locale of ['en', 'ar'] as const) {
  test(`axe: the Add Green Concrete dialog (${locale})`, async ({ page }) => {
    await openAction(page, { locale });
    const results = await new AxeBuilder({ page }).include('.hqGcDialog').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
    expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  });
}

test('builder: the working hours of a seat are saved, a bad hour is refused, and clearing them is allowed', async ({ page }) => {
  const mock = await openBuilder(page);
  await expect(page.getByRole('dialog', { name: 'Office builder' })).toBeVisible();
  // On a phone the tabs live in the details sheet, which opens when a room is selected (the builder's own design), so select a room first.
  await page.locator('.hqbSvg [data-room-id="r-sales"] .hqbRoomBody').click();
  await page.getByRole('tab', { name: 'Room', exact: true }).click();
  await page.getByRole('button', { name: /Desk 2/ }).click();
  const seat = page.getByTestId('hqb-seat');
  await expect(seat).toContainText('An end before the start runs over midnight');
  await page.getByTestId('hqb-seat-start').fill('7am');
  await page.getByTestId('hqb-seat-end').fill('14:00');
  await expect(seat.getByRole('alert')).toContainText('Use hours like 08:00');
  await expect(page.getByTestId('hqb-seat-save')).toBeDisabled();
  await page.getByTestId('hqb-seat-start').fill('14:00');
  await expect(seat.getByRole('alert')).toContainText('Start and end must differ');
  await expect(page.getByTestId('hqb-seat-save')).toBeDisabled();
  await page.getByTestId('hqb-seat-start').fill('06:00');
  await page.getByTestId('hqb-seat-order').fill('2');
  await page.getByTestId('hqb-seat-save').click();
  await expect.poll(() => mock.requests.find((r) => r.method === 'PATCH' && r.path === '/desks/d-2')?.body).toMatchObject({ work_start: '06:00', work_end: '14:00', seat_order: 2 });
  await expect(page.getByTestId('hqb-seat-start')).toHaveValue('06:00');
  // Clearing both is a seat with no hours; one alone is refused.
  await page.getByTestId('hqb-seat-end').fill('');
  await expect(page.getByTestId('hqb-seat-save')).toBeDisabled();
  await page.getByTestId('hqb-seat-start').fill('');
  await page.getByTestId('hqb-seat-save').click();
  await expect.poll(() => mock.requests.filter((r) => r.method === 'PATCH' && r.path === '/desks/d-2').at(-1)?.body).toMatchObject({ work_start: null, work_end: null });
  // Without older day or night seats the building has no day and night hours to set: the Floor tab says hours are per seat.
  await page.getByRole('tab', { name: 'Floor' }).click();
  await expect(page.getByTestId('hqb-shift-hours')).toHaveCount(0);
  await expect(page.getByTestId('hqb-hours-per-seat')).toContainText('per person');
});

test('builder: an older night seat says where its hours come from, and its own hours replace the shift', async ({ page }) => {
  const mock = await openBuilder(page, { olderSeat: true });
  await page.locator('.hqbSvg [data-room-id="r-sales"] .hqbRoomBody').click();
  await page.getByRole('tab', { name: 'Floor' }).click();
  const hours = page.getByTestId('hqb-shift-hours');
  await expect(hours).toContainText('Day and night hours (older seats)');
  await expect(hours).toContainText('(1 in this building)');
  await page.getByTestId('hqb-day-start').fill('7am');
  await expect(hours).toContainText('Use hours like 07:00');
  await expect(page.getByRole('button', { name: 'Save building' })).toBeDisabled();
  await page.getByTestId('hqb-day-start').fill('06:30');
  await page.getByTestId('hqb-night-start').fill('18:30');
  await page.getByRole('button', { name: 'Save building' }).click();
  await expect.poll(() => mock.requests.find((r) => r.method === 'PATCH' && r.path.startsWith('/buildings/'))?.body).toMatchObject({ shift_day_start: '06:30', shift_night_start: '18:30' });
  await page.locator('.hqbSvg [data-room-id="r-sales"] .hqbRoomBody').click();
  await page.getByRole('tab', { name: 'Room', exact: true }).click();
  await page.getByRole('button', { name: /Desk 2/ }).click();
  await expect(page.getByTestId('hqb-seat-older')).toContainText('Older Night shift seat');
  await page.getByTestId('hqb-seat-start').fill('13:00');
  await page.getByTestId('hqb-seat-end').fill('22:00');
  await page.getByTestId('hqb-seat-save').click();
  await expect.poll(() => mock.requests.find((r) => r.method === 'PATCH' && r.path === '/desks/d-2')?.body).toMatchObject({ work_start: '13:00', work_end: '22:00', shift: null });
  await expect(page.getByTestId('hqb-seat-older')).toHaveCount(0);
});

test('builder: seats of one table group are drawn as tables on the plan, and a copied room keeps its seats', async ({ page }) => {
  const mock = await openBuilder(page);
  await page.locator('.hqbSvg [data-room-id="r-sales"] .hqbRoomBody').click();
  await page.getByRole('tab', { name: 'Room', exact: true }).click();
  await expect(page.locator('.hqbTables rect')).toHaveCount(0);
  await page.getByRole('button', { name: /Desk 2/ }).click();
  await page.getByLabel('Table group').fill('l-table');
  await page.getByLabel('Looks at').selectOption('west');
  await page.getByTestId('hqb-seat-save').click();
  await expect.poll(() => mock.requests.find((r) => r.method === 'PATCH' && r.path === '/desks/d-2')?.body).toMatchObject({ table_group: 'l-table', facing: 'west' });
  await expect(page.locator('.hqbTables rect')).toHaveCount(1);
  await page.getByRole('checkbox', { name: /Also copy the desks/ }).check();
  await page.getByRole('button', { name: 'Copy room' }).click();
  await expect.poll(() => [...mock.desks.values()].filter((d) => d.table_group === 'l-table').length).toBe(2);
});

test('the facts the spec relies on are the planner fixture', () => {
  expect(FX.people.map((p) => p.name)).toEqual(['Raed', 'Hazrat', 'Mai', 'Shahzeb', 'Ijaz', 'Badea', 'Ali Zeehan', 'Amjad', 'Rana', 'Ahmed Elbendari']);
  expect([person('raed').work_start, person('raed').work_end]).toEqual(['08:00', '17:00']);
  expect([person('amjad').work_start, person('amjad').work_end]).toEqual(['13:00', '22:00']);
  expect(FX.desks.every((d) => d.shift === null)).toBe(true);
});

test('the Operations wall counts the people outside their hours as Off shift, and its total stays every agent', async ({ page }) => {
  await openRoom(page);
  const wall = hq(page).getByRole('region', { name: 'Operations wall' });
  await expect(wall.getByRole('list', { name: 'Agents by state' })).toContainText('Off shift 1');
  await expect(wall.locator('[data-tile="agents"] .hqWallValue')).toHaveText('10');
  await shot(page, 'green-noon-wall-off-shift');
});
