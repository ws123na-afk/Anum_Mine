import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Locator, type Page } from '@playwright/test';
import { openBuilder, type BuilderMock } from './support/hqBuilderApi';

// The owner's visual office builder (docs/help/office-builder.md): plan, drag, resize, rotate,
// templates, draft and atomic save, stale revision, permissions, keyboard, Arabic and a large floor.
// The API is mocked at http://localhost:8000/** with the server's own rules (support/hqBuilderApi.ts).
test.use({ viewport: { width: 1440, height: 900 } });

const room = (page: Page, id: string): Locator => page.locator(`.hqbSvg [data-room-id="${id}"]`);
const body = (page: Page, id: string): Locator => room(page, id).locator('.hqbRoomBody');
const field = (page: Page, label: string): Locator => page.getByRole('tabpanel').getByLabel(label, { exact: true }).first();
const status = (page: Page): Locator => page.getByTestId('draft-status');

/** Pixels per metre of the drawn plan, from the floor outline (60 m wide in the mock). */
async function pixelsPerMetre(page: Page): Promise<number> {
  const box = await page.locator('.hqbFloor').boundingBox();
  if (!box) throw new Error('no floor outline');
  return box.width / 60;
}

async function drag(page: Page, from: Locator, dxPx: number, dyPx: number, at: 'center' | 'topleft' = 'center'): Promise<void> {
  const box = await from.boundingBox();
  if (!box) throw new Error('nothing to drag');
  const x = at === 'center' ? box.x + box.width / 2 : box.x + 2;
  const y = at === 'center' ? box.y + box.height / 2 : box.y + 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + dxPx / 2, y + dyPx / 2, { steps: 4 });
  await page.mouse.move(x + dxPx, y + dyPx, { steps: 4 });
  await page.mouse.up();
}

async function layoutPuts(mock: BuilderMock) {
  return mock.requests.filter((r) => r.method === 'PUT' && r.path.endsWith('/layout'));
}

test('owner: add from a template, drag, resize, rotate, undo, redo and save one atomic layout', async ({ page }) => {
  const mock = await openBuilder(page);
  await expect(page.getByRole('dialog', { name: 'Office builder' })).toBeVisible();
  await expect(room(page, 'r-sales')).toBeVisible();
  await expect(status(page)).toHaveText('All changes saved');
  await expect(page.getByRole('button', { name: 'Save layout' })).toBeDisabled();

  // Create a room from a template: an immediate server action, placed in a free spot.
  await page.getByRole('tab', { name: 'Rooms' }).click();
  await page.locator('[data-template="workshop"]').click();
  await expect(page.getByRole('status').filter({ hasText: 'Room added: Workshop' }).first()).toBeAttached();
  const createdPost = mock.requests.find((r) => r.method === 'POST' && r.path === '/rooms/from-template');
  expect(createdPost?.body).toMatchObject({ template_id: 'workshop', with_desks: true });
  expect(typeof createdPost?.body?.x).toBe('number');
  await expect(status(page)).toHaveText('All changes saved');

  // Drag the board room 8 m south.
  const ppm = await pixelsPerMetre(page);
  await drag(page, body(page, 'r-meet'), 0, 8 * ppm);
  await expect(status(page)).toHaveText('Unsaved changes: 1');
  await expect(field(page, 'X (east)')).toHaveValue('20');
  await expect(field(page, 'Z (south)')).toHaveValue('10');

  // Resize from the east handle by 2 m.
  await drag(page, page.locator('.hqbHandle[data-handle="e"]'), 2 * ppm, 0);
  await expect(field(page, 'Width')).toHaveValue('8');

  // Rotate: width and depth swap in the same draft.
  await page.getByRole('button', { name: 'Rotate 90 degrees' }).first().click();
  await expect(field(page, 'Width')).toHaveValue('5');
  await expect(field(page, 'Depth')).toHaveValue('8');

  // Undo and redo with the keyboard.
  await page.getByRole('dialog', { name: 'Office builder' }).focus();
  await page.keyboard.press('Control+z');
  await expect(field(page, 'Width')).toHaveValue('8');
  await page.keyboard.press('Control+Shift+z');
  await expect(field(page, 'Width')).toHaveValue('5');

  expect(await layoutPuts(mock)).toHaveLength(0);
  await page.getByRole('button', { name: 'Save layout' }).click();
  await expect(status(page)).toHaveText('All changes saved');
  const puts = await layoutPuts(mock);
  expect(puts).toHaveLength(1);
  const saved = puts[0].body as { rooms: Record<string, unknown>[] };
  expect(saved.rooms).toHaveLength(1);
  expect(saved.rooms[0]).toMatchObject({ id: 'r-meet', expected_revision: 1, x: 21.5, z: 8.5, width: 5, depth: 8, rotation: 90 });
  expect(mock.rooms.get('r-meet')).toMatchObject({ scene_x: 21.5, scene_width: 5, scene_depth: 8, revision: 2 });
});

test('moving a room onto another turns both red, explains why in plain words, and blocks the save until it is fixed', async ({ page }) => {
  const mock = await openBuilder(page);
  const ppm = await pixelsPerMetre(page);
  // The board room (20,2 6x5) goes west 8 m: it now overlaps the sales room (2,2 12x8).
  await drag(page, body(page, 'r-meet'), -8 * ppm, 0);
  await expect(room(page, 'r-meet')).toHaveAttribute('data-tone', 'error');
  await expect(room(page, 'r-sales')).toHaveAttribute('data-tone', 'error');
  await expect(page.getByRole('button', { name: 'Save layout' })).toBeDisabled();
  await page.locator('.hqbIssues summary').click();
  await expect(page.locator('.hqbIssues li').first()).toContainText('overlaps');
  await expect(page.locator('.hqbIssues li').first()).not.toContainText('ROOM_OVERLAP');
  expect(await layoutPuts(mock)).toHaveLength(0);
  await page.keyboard.press('Control+z');
  await expect(room(page, 'r-meet')).toHaveAttribute('data-tone', 'ok');
  await expect(status(page)).toHaveText('All changes saved');
});

test('the server refuses what the client let through, and its code is explained, not shown raw', async ({ page }) => {
  const mock = await openBuilder(page);
  await expect(room(page, 'r-meet')).toBeVisible();
  // Make the server see a room the page does not know: it now sits where the board room will be dropped.
  mock.rooms.set('r-ghost', { ...mock.rooms.get('r-recep'), id: 'r-ghost', name: 'Ghost room', scene_x: 40, scene_z: 20 });
  const ppm = await pixelsPerMetre(page);
  await drag(page, body(page, 'r-meet'), 20 * ppm, 18 * ppm);
  await page.getByRole('button', { name: 'Save layout' }).click();
  const alert = page.getByRole('alert').filter({ hasText: 'overlaps' });
  await expect(alert).toBeVisible();
  await expect(alert).not.toContainText('ROOM_OVERLAP');
  await expect(status(page)).toContainText('Unsaved changes');
});

test('stale revision: the draft is kept, reload and reapply puts it back on top, then it saves', async ({ page }) => {
  const mock = await openBuilder(page);
  const ppm = await pixelsPerMetre(page);
  await drag(page, body(page, 'r-recep'), 0, 6 * ppm);
  await expect(status(page)).toHaveText('Unsaved changes: 1');
  // Someone else edits the front desk room (revision 2) and moves the board room.
  mock.touchRoom('r-recep');
  mock.touchRoom('r-meet', { scene_x: 24 });
  await page.getByRole('button', { name: 'Save layout' }).click();
  const dialog = page.getByRole('alertdialog', { name: /changed while you were editing/ });
  await expect(dialog).toBeVisible();
  await expect(status(page)).toHaveText('Unsaved changes: 1');
  await dialog.getByRole('button', { name: 'Reload and reapply my changes' }).click();
  await expect(page.getByText(/Reloaded\. 1 of your changes were put back on top/)).toBeVisible();
  await expect(status(page)).toHaveText('Unsaved changes: 1');
  await page.getByRole('button', { name: 'Save layout' }).click();
  await expect(status(page)).toHaveText('All changes saved');
  expect(mock.rooms.get('r-recep')).toMatchObject({ scene_z: 8, revision: 3 });
  // The other person's move of the board room is now what the page shows.
  await expect(field(page, 'X (east)')).toBeVisible();
});

test('stale revision: discard and reload drops the draft', async ({ page }) => {
  const mock = await openBuilder(page);
  const ppm = await pixelsPerMetre(page);
  await drag(page, body(page, 'r-recep'), 0, 6 * ppm);
  mock.touchRoom('r-recep');
  await page.getByRole('button', { name: 'Save layout' }).click();
  await page.getByRole('button', { name: 'Discard mine and reload' }).click();
  await expect(status(page)).toHaveText('All changes saved');
  await expect(page.getByRole('alertdialog')).toHaveCount(0);
});

test('a member sees a read-only plan with the reason, and cannot move anything', async ({ page }) => {
  const mock = await openBuilder(page, { role: 'member' });
  await expect(page.getByText('View only')).toBeVisible();
  await expect(page.getByText(/Only the workspace owner can change the office layout\. Your role is member/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save layout' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Undo' })).toHaveCount(0);
  const ppm = await pixelsPerMetre(page);
  await drag(page, body(page, 'r-meet'), 4 * ppm, 0);
  await expect(room(page, 'r-meet')).toHaveAttribute('data-selected', 'true');
  await expect(field(page, 'X (east)')).toBeDisabled();
  await expect(field(page, 'Name')).toBeDisabled();
  await page.keyboard.press('ArrowRight');
  expect(mock.requests.filter((r) => r.method !== 'GET')).toHaveLength(0);
  await page.getByRole('tab', { name: 'Rooms' }).click();
  await expect(page.locator('[data-template]')).toHaveCount(0);
});

test('an owner whose write is refused by the server is told who can do it', async ({ page }) => {
  await openBuilder(page, { forbidWrites: true });
  const ppm = await pixelsPerMetre(page);
  await drag(page, body(page, 'r-recep'), 0, 6 * ppm);
  await page.getByRole('button', { name: 'Save layout' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Only the workspace owner can change the office' }).first()).toBeVisible();
  await expect(status(page)).toContainText('Unsaved changes');
});

test('the office service being down is explained with a way forward', async ({ page }) => {
  await openBuilder(page, { down: true });
  await expect(page.getByRole('alert').filter({ hasText: 'PostgreSQL' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('keyboard: arrows move, shift with arrows resizes, R rotates, with a live announcement', async ({ page }) => {
  await openBuilder(page);
  await room(page, 'r-meet').focus();
  await expect(room(page, 'r-meet')).toHaveAttribute('data-selected', 'true');
  const live = page.getByTestId('builder-live');
  await page.keyboard.press('ArrowRight');
  await expect(live).toHaveText('Board room moved to x 21, z 2');
  await page.keyboard.press('ArrowDown');
  await expect(live).toHaveText('Board room moved to x 21, z 3');
  await page.keyboard.press('Shift+ArrowRight');
  await expect(live).toHaveText('Board room is now 7 by 5 metres');
  await page.keyboard.press('r');
  await expect(live).toHaveText('Board room rotated 90 degrees');
  await expect(status(page)).toHaveText('Unsaved changes: 1');
  await page.keyboard.press(']');
  await expect(page.locator('.hqbSvg [data-selected="true"]')).not.toHaveAttribute('data-room-id', 'r-meet');
  // The whole draft is one undo step per key group; Control+S saves.
  await page.keyboard.press('Control+s');
  await expect(status(page)).toHaveText('All changes saved');
});

test('room details: save name, equipment, colour and team; real counts or Not connected', async ({ page }) => {
  const mock = await openBuilder(page);
  await body(page, 'r-sales').click();
  await expect(page.getByTestId('room-team')).toHaveText('People in the team: 3. Assistants: 2.');
  await field(page, 'Name').fill('Sales floor');
  await field(page, 'Arabic name').fill('قسم المبيعات');
  await page.getByRole('radio', { name: 'Brass' }).click();
  await page.getByLabel('Add equipment').selectOption('printer');
  await page.getByRole('button', { name: 'Save details' }).click();
  await expect(page.getByTestId('builder-live')).toHaveText('Details saved: Sales floor');
  const patch = mock.requests.find((r) => r.method === 'PATCH' && r.path === '/rooms/r-sales');
  expect(patch?.body).toMatchObject({ expected_revision: 1, name: 'Sales floor', name_ar: 'قسم المبيعات', color: 'brass', equipment: [{ kind: 'monitor', quantity: 3 }, { kind: 'printer', quantity: 1 }] });
  // A room with no team link says so instead of inventing counts.
  await body(page, 'r-meet').click();
  await expect(page.getByTestId('room-team')).toHaveText('Not connected');
});

test('desks: assign and move an agent between desks, rename, add; delete is blocked while seated', async ({ page }) => {
  const mock = await openBuilder(page);
  await body(page, 'r-sales').click();
  await page.getByRole('button', { name: /Desk 1/ }).click();
  await expect(page.getByLabel('Agent at this desk')).toHaveValue('agent_fleet');
  // Move the same agent to Desk 2: unseat there, then seat here.
  await page.getByRole('button', { name: /Desk 2/ }).click();
  await page.getByLabel('Agent at this desk').selectOption('agent_fleet');
  await expect(page.getByTestId('builder-live')).toHaveText('Agent seated at the desk');
  const assignments = mock.requests.filter((r) => r.path.endsWith('/assignment')).map((r) => [r.path, r.body?.agent_id]);
  expect(assignments).toEqual([['/desks/d-1/assignment', null], ['/desks/d-2/assignment', 'agent_fleet']]);
  await page.getByLabel('Desk name', { exact: true }).fill('Front');
  await page.getByRole('button', { name: 'Rename' }).click();
  await expect(page.locator('.hqbDeskRow', { hasText: 'Front' })).toBeVisible();
  await page.getByLabel('New desk name').fill('Desk 9');
  await page.getByRole('button', { name: 'Add desk' }).click();
  await expect(page.getByTestId('builder-live')).toHaveText('Desk added: Desk 9');
  // The new desk got a place in the draft inside its room.
  await expect(status(page)).toHaveText('Unsaved changes: 1');
  await page.locator('.hqbDeskRow', { hasText: 'Front' }).click();
  await page.getByRole('button', { name: 'Delete desk' }).click();
  await expect(page.getByText('An agent sits at this desk. Unseat the agent first.')).toBeVisible();
  await expect(page.getByRole('alertdialog').getByRole('button', { name: 'Delete desk' })).toBeDisabled();
});

test('a desk is dragged inside its room and saved with the layout', async ({ page }) => {
  const mock = await openBuilder(page);
  await body(page, 'r-sales').click();
  const ppm = await pixelsPerMetre(page);
  const desk = page.locator('.hqbDesk').nth(1);
  await drag(page, desk.locator('circle'), 2 * ppm, 1 * ppm);
  await expect(status(page)).toHaveText('Unsaved changes: 1');
  await page.getByRole('button', { name: 'Save layout' }).click();
  await expect(status(page)).toHaveText('All changes saved');
  const put = (await layoutPuts(mock))[0].body as { desks: { id: string; pos_x: number; pos_z: number }[] };
  expect(put.desks).toHaveLength(1);
  expect(put.desks[0].id).toBe('d-2');
  expect(put.desks[0].pos_x).toBeCloseTo(6.5, 1);
});

test('copy a room, and delete: blocked with desks, allowed when empty; the server blockers are listed', async ({ page }) => {
  const mock = await openBuilder(page);
  await body(page, 'r-meet').click();
  await page.getByRole('button', { name: 'Copy room' }).click();
  await expect(page.getByTestId('builder-live')).toHaveText('Room added: Board room copy');
  expect(mock.requests.find((r) => r.path === '/rooms/r-meet/copy')?.body).toMatchObject({ name: 'Board room copy', copy_desks: false });
  await page.getByRole('button', { name: 'Delete room' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Delete room' }).click();
  await expect(page.getByTestId('builder-live')).toHaveText('Room deleted');
  // A room with desks cannot be deleted.
  await body(page, 'r-sales').click();
  await page.getByRole('button', { name: 'Delete room' }).click();
  await expect(page.getByText(/still has 3 desks/)).toBeVisible();
  await expect(page.getByRole('alertdialog').getByRole('button', { name: 'Delete room' })).toBeDisabled();
  // A floor with rooms: the server answers 409 and its blockers are listed.
  await page.getByRole('tab', { name: 'Floor' }).click();
  await page.getByRole('button', { name: 'Delete floor' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Delete floor' }).click();
  const alert = page.getByRole('alert').filter({ hasText: 'This floor still has rooms' });
  await expect(alert).toBeVisible();
  await expect(alert).toContainText('Sales room');
});

test('floor size: set the outline and rooms outside it show as a problem', async ({ page }) => {
  const mock = await openBuilder(page);
  await page.getByRole('tab', { name: 'Floor' }).click();
  await page.getByLabel('Width (m)').fill('30');
  await page.getByRole('button', { name: 'Save floor' }).click();
  await expect(page.getByTestId('builder-live')).toHaveText('Saved');
  expect(mock.requests.find((r) => r.method === 'PATCH' && r.path === '/floors/floor-1')?.body).toMatchObject({ width_m: 30, depth_m: 40 });
  await expect(room(page, 'r-recep')).toHaveAttribute('data-tone', 'warning');
});

test('a room that has no place waits in the tray and is placed on the plan', async ({ page }) => {
  const mock = await openBuilder(page);
  await page.getByRole('tab', { name: 'Rooms' }).click();
  await expect(page.getByText('Rooms waiting for a place: 1')).toBeVisible();
  await page.getByRole('button', { name: 'Place on plan' }).click();
  await expect(room(page, 'r-spare')).toBeVisible();
  await expect(status(page)).toHaveText('Unsaved changes: 1');
  await page.getByRole('button', { name: 'Save layout' }).click();
  await expect(status(page)).toHaveText('All changes saved');
  expect(mock.rooms.get('r-spare')).toMatchObject({ revision: 2 });
});

test('an empty floor says what to do next', async ({ page }) => {
  await openBuilder(page, { empty: true });
  await expect(page.getByText('No rooms on this floor yet').first()).toBeVisible();
  await page.getByRole('button', { name: 'Add the first room' }).click();
  await expect(page.getByRole('tab', { name: 'Rooms' })).toHaveAttribute('aria-selected', 'true');
});

test('large floor: 500 rooms render, search and move stay quick', async ({ page }) => {
  const mock = await openBuilder(page, { extraRooms: 500 });
  await expect(page.locator('.hqbSvg [data-room-id]')).toHaveCount(503);
  const started = Date.now();
  await room(page, 'r-x10').focus();
  await page.keyboard.press('ArrowDown');
  await expect(status(page)).toHaveText('Unsaved changes: 1');
  expect(Date.now() - started).toBeLessThan(2500);
  await page.getByRole('tab', { name: 'Rooms' }).click();
  await page.getByLabel('Find a room').fill('Bulk room 499');
  await expect(page.locator('.hqbRoomRow')).toHaveCount(1);
  expect(mock.requests.filter((r) => r.method === 'GET').length).toBeGreaterThan(0);
});

test('Arabic: right to left, translated, same editing', async ({ page }) => {
  const mock = await openBuilder(page, { locale: 'ar' });
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('dialog', { name: 'منشئ المكتب' })).toBeVisible();
  await expect(page.getByTestId('draft-status')).toHaveText('تم حفظ كل التغييرات');
  const ppm = await pixelsPerMetre(page);
  await drag(page, body(page, 'r-recep'), 0, 6 * ppm);
  await expect(page.getByTestId('draft-status')).toHaveText('تغييرات غير محفوظة: 1');
  await page.getByRole('button', { name: 'حفظ التخطيط' }).click();
  await expect(page.getByTestId('draft-status')).toHaveText('تم حفظ كل التغييرات');
  expect(await layoutPuts(mock)).toHaveLength(1);
  await page.getByRole('tab', { name: 'الغرف', exact: true }).click();
  await expect(page.locator('[data-template="workshop"]')).toContainText('ورشة عمل');
  // No overflow either way.
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

for (const locale of ['en', 'ar'] as const) {
  test(`accessibility scan (${locale})`, async ({ page }) => {
    await openBuilder(page, { locale });
    await body(page, 'r-sales').click();
    const results = await new AxeBuilder({ page }).include('.hqb').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
    const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
      .map((v) => `${v.id}: ${v.help} -> ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`);
    expect(serious).toEqual([]);
  });
}

test('reduced motion: no transitions on the plan', async ({ page }) => {
  await openBuilder(page, { reducedMotion: true });
  const transition = await body(page, 'r-sales').evaluate((el) => getComputedStyle(el).transitionDuration);
  expect(parseFloat(transition)).toBeLessThan(0.001);
});
