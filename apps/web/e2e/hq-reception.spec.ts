import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { fakeVisit, mockReception, type MockVisit, type ReceptionMode } from './support/hqReceptionApi';

// The reception visitor register (docs/ANUM_MINE_HQ_RECEPTION.md, owner decision O4): a real register with every state,
// owner-only clicks, and a 3D reception room that shows only the stored count of checked-in visitors. Names are plainly fake.
test.use({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, isMobile: false, hasTouch: false });
test.describe.configure({ timeout: 240_000 });

const hq = (page: Page) => page.locator('.hqLive');
const panel = (page: Page) => page.getByTestId('hq-reception');
const stamp = '2026-10-01T08:00:00Z';
const rec = (id: string, extra: Record<string, unknown>) => ({ id, revision: 1, tenant_id: TENANT_A.tenantId, workspace_id: TENANT_A.workspaceId, created_at: stamp, updated_at: stamp, ...extra });

function frontOffice() {
  const base = hqRegistry(TENANT_A, { rooms: 0, desks: 0 });
  const bid = base.buildings[0].id;
  const floors = [rec('Alpha-floor-0', { building_id: bid, level: 0, name: 'Ground floor: Front office' })];
  const room = (id: string, name: string, kind: string, x: number, dept: string | null) =>
    rec(id, { floor_id: 'Alpha-floor-0', name, kind, capacity: 6, scene_x: x, scene_z: 0, scene_width: 8, scene_depth: 6, ...(dept ? { team_department: dept } : {}) });
  const rooms = [room('Alpha-finance', 'Alpha Finance', 'office', 0, 'finance'), room('Alpha-lobby', 'Alpha Lobby', 'reception', 10, null)];
  const desks = [rec('Alpha-desk-0', { room_id: 'Alpha-finance', label: 'Alpha Desk 1', agent_id: 'agent_fleet' })];
  return { buildings: base.buildings, floors, rooms, desks } as unknown as ReturnType<typeof hqRegistry>;
}

async function open(page: Page, options: { mode?: ReceptionMode; visits?: MockVisit[]; locale?: 'en' | 'ar'; enter?: boolean } = {}) {
  let reception: Awaited<ReturnType<typeof mockReception>> | null = null;
  await openHq(page, { registry: frontOffice(), locale: options.locale, beforeGoto: async (p) => { reception = await mockReception(p, { mode: options.mode, visits: options.visits }); } });
  await expect(hq(page).getByRole('heading', { level: 1 })).toBeVisible();
  if (options.enter) {
    await expect(hq(page).locator('canvas.hqCanvas')).toBeVisible();
    await hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).dispatchEvent('click', undefined, { timeout: 60_000 });
    await hq(page).getByRole('button', { name: /^(Open|فتح) ⁨?Al Noor Fleet HQ⁩?$/ }).click();
    await expect(hq(page).getByRole('heading', { level: 2, name: 'Al Noor Fleet HQ' })).toBeVisible();
  }
  return reception as unknown as Awaited<ReturnType<typeof mockReception>>;
}

async function openFromTools(page: Page, label = 'Reception: visitors') {
  await hq(page).getByRole('button', { name: /Office tools|أدوات المكتب/ }).click();
  await page.getByRole('button', { name: label }).click();
  // The panel is a lazy chunk fetched on its first open: allow for a slow network or a busy machine.
  await expect(panel(page)).toBeVisible({ timeout: 15_000 });
}

test('owner: empty register, add a walk-in, check out, and the reception room shows the real count', async ({ page }) => {
  const reception = await open(page, { enter: true });
  const lobby = hq(page).locator('.hqBuilding .hqRoomCard', { hasText: 'Alpha Lobby' });
  await expect(lobby).toHaveAccessibleName(/^Alpha Lobby\. No visitors checked in · Reception/);
  await openFromTools(page);
  await expect(page.getByTestId('reception-empty')).toContainText('No visitor is expected today');
  await page.getByTestId('reception-empty').getByRole('button', { name: 'Add a visitor' }).click();
  const form = page.getByTestId('reception-form');
  await form.getByRole('button', { name: 'Save visitor' }).click();
  await expect(form.getByText('Check the marked fields.')).toBeVisible();
  await expect(form.getByLabel('Visitor name')).toHaveAttribute('aria-invalid', 'true');
  await form.getByLabel('Visitor name').fill('Visitor Example Walkin');
  await form.getByLabel('Purpose of the visit').fill('Example interview');
  await form.getByLabel('Room (optional)').selectOption('Alpha-lobby');
  await form.getByLabel('Already here: check in now').check();
  await form.getByRole('button', { name: 'Save visitor' }).click();
  await expect(page.getByTestId('reception-note')).toHaveText('Visitor Example Walkin was added.');
  const row = page.locator('.hqRecRow', { hasText: 'Visitor Example Walkin' });
  await expect(row).toHaveAttribute('data-status', 'checked_in');
  await expect(row).toContainText('Room: Alpha Lobby');
  const create = reception.calls.find((c) => c.method === 'POST');
  expect(create?.body).toMatchObject({ visitor_name: 'Visitor Example Walkin', room_id: 'Alpha-lobby', arrived: true, input_mode: 'click' });
  await expect(lobby).toHaveAccessibleName(/^Alpha Lobby\. 1 visitor checked in · Reception/);
  await row.getByRole('button', { name: 'Check out' }).click();
  await expect(page.getByTestId('reception-note')).toHaveText('Visitor Example Walkin is checked out.');
  await expect(row).toHaveAttribute('data-status', 'checked_out');
  expect(reception.calls.find((c) => c.path.endsWith('/check-out'))?.body).toEqual({ expected_revision: 1, input_mode: 'click' });
  await expect(lobby).toHaveAccessibleName(/^Alpha Lobby\. No visitors checked in · Reception/);
  // Escape closes the panel and gives focus back.
  await page.keyboard.press('Escape');
  await expect(panel(page)).toHaveCount(0);
});

test('owner: check in, cancel, edit and delete with a confirmation', async ({ page }) => {
  const reception = await open(page, { visits: [fakeVisit(1), fakeVisit(2), fakeVisit(3)] });
  await openFromTools(page);
  const row = (n: number) => page.locator('.hqRecRow', { hasText: `Visitor Example ${n}` });
  await expect(page.getByRole('button', { name: 'Expected (3)' })).toBeVisible();
  await row(1).getByRole('button', { name: 'Check in' }).click();
  await expect(row(1)).toHaveAttribute('data-status', 'checked_in');
  await expect(page.getByRole('button', { name: 'Checked in (1)' })).toBeVisible();
  await row(2).getByRole('button', { name: 'Cancel visit' }).click();
  await expect(row(2)).toHaveAttribute('data-status', 'cancelled');
  await row(3).getByRole('button', { name: 'Edit' }).click();
  const form = page.getByTestId('reception-form');
  await expect(form.getByLabel('Visitor name')).toHaveValue('Visitor Example 3');
  await form.getByLabel('Company (optional)').fill('Example Holdings');
  await form.getByRole('button', { name: 'Save changes' }).click();
  await expect(row(3)).toContainText('Example Holdings');
  expect(reception.calls.find((c) => c.method === 'PATCH')?.body).toMatchObject({ company: 'Example Holdings', expected_revision: 1, input_mode: 'click' });
  await row(3).getByRole('button', { name: 'Delete' }).click();
  await expect(row(3).getByRole('alertdialog')).toContainText('Delete the record of Visitor Example 3?');
  await row(3).getByRole('button', { name: 'Keep' }).click();
  await row(3).getByRole('button', { name: 'Delete' }).click();
  await row(3).getByRole('button', { name: 'Delete record' }).click();
  await expect(page.getByTestId('reception-note')).toHaveText('The record of Visitor Example 3 is deleted.');
  await expect(row(3)).toHaveCount(0);
  expect(reception.calls.find((c) => c.method === 'DELETE')?.path).toContain('expected_revision=2&input_mode=click');
  // Status filters narrow the list.
  await page.getByRole('button', { name: 'Cancelled (1)' }).click();
  await expect(page.locator('.hqRecRow')).toHaveCount(1);
});

test('a stale revision is explained and the list reloads', async ({ page }) => {
  const reception = await open(page, { visits: [fakeVisit(1)] });
  await openFromTools(page);
  await expect(page.locator('.hqRecRow')).toHaveAttribute('data-status', 'expected'); // the page holds revision 1
  reception.visits[0].revision = 5; // someone else changed it
  await page.locator('.hqRecRow').getByRole('button', { name: 'Check in' }).click();
  await expect(page.getByTestId('reception-note')).toContainText('Someone changed this visit a moment ago');
  await page.locator('.hqRecRow').getByRole('button', { name: 'Check in' }).click();
  await expect(page.locator('.hqRecRow')).toHaveAttribute('data-status', 'checked_in');
});

test('paging and search over a long register', async ({ page }) => {
  await open(page, { visits: Array.from({ length: 31 }, (_, i) => fakeVisit(i + 1)) });
  await openFromTools(page);
  await expect(page.locator('.hqRecRow')).toHaveCount(25);
  await expect(page.locator('.hqRecCount')).toHaveText('Showing 25 of 31 visitors');
  await page.getByRole('button', { name: 'Load more visitors' }).click();
  await expect(page.locator('.hqRecRow')).toHaveCount(31);
  await expect(panel(page).getByText('End of the list for these filters.')).toBeVisible();
  await panel(page).getByRole('searchbox').fill('Visitor Example 3');
  await expect(page.locator('.hqRecRow')).toHaveCount(3); // 3, 30, 31
  await panel(page).getByRole('searchbox').fill('nobody here');
  await expect(page.getByTestId('reception-empty')).toContainText('No visitor matches these filters.');
  await page.getByRole('button', { name: 'Clear the filters' }).click();
  await expect(page.locator('.hqRecRow')).toHaveCount(25);
});

test('a viewer reads the register and is told who can change it', async ({ page }) => {
  await open(page, { mode: 'viewer', visits: [fakeVisit(1)] });
  await openFromTools(page);
  await expect(page.getByTestId('reception-owner-only')).toContainText('Only a workspace owner can add');
  await expect(page.getByTestId('reception-add')).toHaveCount(0);
  await expect(page.locator('.hqRecRow').getByRole('button')).toHaveCount(0);
});

test('denied and failed reads say what happened, and Try again recovers', async ({ page }) => {
  await open(page, { mode: 'denied' });
  await openFromTools(page);
  await expect(page.getByTestId('reception-denied')).toContainText('You cannot read the visitor register');
  await page.keyboard.press('Escape');
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  const reception = await open(page, { mode: 'failing', visits: [fakeVisit(1)] });
  reception.mode = 'owner';
  await openFromTools(page);
  await expect(page.getByTestId('reception-error')).toContainText('The visitor register could not be read');
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.locator('.hqRecRow')).toHaveCount(1);
});

test('the reception room in the list opens the register with that room, and the room panel shows the count', async ({ page }) => {
  await open(page, { visits: [fakeVisit(1, { status: 'checked_in', checked_in_at: new Date().toISOString(), room_id: 'Alpha-lobby' })] });
  await hq(page).getByRole('button', { name: 'List', exact: true }).click();
  await hq(page).locator('.hqListRoom', { hasText: 'Alpha Lobby' }).click();
  await expect(page.locator('.hqSide [data-testid="room-visitors"]')).toHaveText('1 visitor checked in');
  await page.locator('.hqSide [data-testid="room-module"]').click();
  await expect(panel(page)).toBeVisible();
  await panel(page).getByTestId('reception-add').click();
  await expect(page.getByTestId('reception-form').getByLabel('Room (optional)')).toHaveValue('Alpha-lobby');
  const results = await new AxeBuilder({ page }).include('[data-testid="hq-reception"]').analyze();
  expect(results.violations.map((v) => v.id)).toEqual([]);
});

test('Arabic: the register reads right to left with Arabic words', async ({ page }) => {
  await open(page, { locale: 'ar', visits: [fakeVisit(1)] });
  await openFromTools(page, 'الاستقبال: الزوار');
  await expect(panel(page).getByRole('heading', { level: 2 })).toHaveText('الاستقبال');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.locator('.hqRecRow').getByRole('button', { name: 'تسجيل الدخول' })).toBeVisible();
  const box = await panel(page).boundingBox();
  expect(box && box.x).toBeLessThan(10); // the drawer opens from the left in Arabic
  // The expected time follows the app language, and the chosen time is written in Arabic next to the field.
  await panel(page).getByTestId('reception-add').click();
  const expected = page.getByTestId('reception-form').locator('input[type="datetime-local"]');
  await expect(expected).toHaveAttribute('lang', 'ar');
  await expect(expected).toHaveAttribute('dir', 'rtl');
  await expected.fill('2026-10-28T09:30');
  const shown = page.getByTestId('reception-form').getByTestId('hq-date-shown');
  await expect(shown).toContainText('28 أكتوبر 2026');
  await expect(shown).toContainText('9:30');
});
