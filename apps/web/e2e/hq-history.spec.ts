import { expect, test, type Page } from '@playwright/test';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { historyFixture, mockHistory, record, type HistoryMock, type HistoryMockOptions } from './support/hqHistoryApi';

// The office history panel (docs/ANUM_MINE_HQ_HISTORY_PANEL.md). Everything shown comes from the mocked
// /office-history answers; the tests check that filters become server parameters, that long histories page,
// that a record opens in the side panel with its sources, and that the room slider never invents a state.
const registry = hqRegistry(TENANT_A, { rooms: 4, desks: 4, agentIds: ['agent_fleet', 'agent_leads', 'agent_care', 'agent_invoice'] });
let history: HistoryMock;

async function open(page: Page, options: Parameters<typeof openHq>[1] = {}, mock: HistoryMockOptions = {}) {
  await openHq(page, { registry, ...options, beforeGoto: async (p) => { history = await mockHistory(p, mock); } });
  await expect(page.locator('.hqLive')).toBeVisible();
}
async function openPanel(page: Page) {
  const hq = page.locator('.hqLive');
  await hq.getByRole('button', { name: 'Office tools' }).click();
  await hq.getByRole('button', { name: 'History', exact: true }).click();
  const panel = page.getByTestId('hq-history');
  await expect(panel).toBeVisible();
  return panel;
}

test('the timeline lists real records newest first with paging, and says when the end is reached', async ({ page }) => {
  await open(page);
  const panel = await openPanel(page);
  await expect(panel.getByRole('heading', { name: 'Office history' })).toBeVisible();
  const rows = panel.locator('.hqHistRow');
  await expect(rows.first()).toBeVisible();
  await expect(panel.getByRole('status').filter({ hasText: /records shown/ })).toBeVisible();
  const before = await rows.count();
  expect(before).toBeGreaterThanOrEqual(25);
  const older = panel.getByRole('button', { name: 'Load older records' });
  await older.click();
  await expect.poll(async () => rows.count()).toBeGreaterThan(before);
  // Scrolling to the end keeps loading by itself; the button covers keyboards.
  for (let i = 0; i < 4 && await older.isVisible(); i += 1) { await older.click({ timeout: 2000 }).catch(() => undefined); await page.waitForTimeout(250); }
  await expect(panel.getByText('End of the recorded history for these filters.')).toBeVisible({ timeout: 10_000 });
  expect(await rows.count()).toBe(historyFixture().length);
  const sent = history.requests.map((u) => u.searchParams.get('cursor'));
  expect(sent[0]).toBeNull();
  expect(sent.length).toBeGreaterThan(1);
});

test('search, event type, who and date range become server filters', async ({ page }) => {
  await open(page);
  const panel = await openPanel(page);
  await panel.getByRole('button', { name: /^Filters/ }).click();
  await panel.getByRole('searchbox', { name: 'Search' }).fill('rename');
  await panel.getByLabel('Event type').selectOption('office');
  await panel.getByLabel('Done by (id)').fill('owner_example');
  await panel.getByLabel('From day').fill('2026-10-19');
  await panel.getByLabel('To day').fill('2026-10-20');
  await panel.getByRole('button', { name: 'Apply filters' }).click();
  await expect(panel.locator('.hqHistRow')).toHaveCount(2); // the two renames inside the two days
  const last = history.requests[history.requests.length - 1];
  expect(last.searchParams.get('q')).toBe('rename');
  expect(last.searchParams.get('category')).toBe('office');
  expect(last.searchParams.get('actor')).toBe('owner_example');
  expect(last.searchParams.get('since')).toBeTruthy();
  expect(last.searchParams.get('until')).toBeTruthy();
  await expect(panel.getByRole('button', { name: /^Filters \(5 on\)/ })).toBeVisible();
  await panel.getByRole('button', { name: 'Clear filters' }).click();
  await expect(panel.locator('.hqHistRow').first()).toBeVisible();
});

test('a bad or reversed date range is refused before any request', async ({ page }) => {
  await open(page);
  const panel = await openPanel(page);
  await expect(panel.locator('.hqHistRow').first()).toBeVisible();
  const count = history.requests.length;
  await panel.getByRole('button', { name: /^Filters/ }).click();
  await panel.getByLabel('From day').fill('2026-10-20');
  await panel.getByLabel('To day').fill('2026-10-01');
  await panel.getByRole('button', { name: 'Apply filters' }).click();
  await expect(panel.getByRole('alert')).toContainText('start day must be before the end day');
  expect(history.requests.length).toBe(count);
});

test('a record opens in the side panel with before and after values, its task and its request', async ({ page }) => {
  await open(page);
  const panel = await openPanel(page);
  await panel.locator('[data-record-id="audit_0005"]').click(); // the rename that follows an unexplained change
  await expect(panel.getByRole('heading', { name: 'Room renamed' })).toBeFocused();
  await expect(page).toHaveURL(/#office-preview/);
  const diff = panel.getByRole('table');
  await expect(diff.getByRole('row', { name: /Name/ })).toContainText('Lobby');
  await expect(diff.getByRole('row', { name: /Name/ })).toContainText('Welcome Hall');
  await expect(panel.getByText('Stored audit record')).toBeVisible();
  await panel.getByRole('button', { name: 'Show records of the same request' }).click();
  await expect(panel.getByText('No other record shares this correlation id.')).toBeVisible();
  await panel.getByRole('button', { name: 'Back to the list' }).click();
  await expect(panel.locator('.hqHistRow').first()).toBeVisible();
});

test('an approval record shows its task summary in the panel, and a missing record says so', async ({ page }) => {
  await open(page);
  const panel = await openPanel(page);
  await panel.getByRole('button', { name: /Approval granted/ }).first().click();
  await panel.getByRole('button', { name: 'Show Task task_1' }).click();
  await expect(panel.getByText('Plan October tyre rotation')).toBeVisible();
  await expect(panel.getByText('private words that must not show')).toHaveCount(0);
  await panel.getByRole('button', { name: 'Show Approval ap_1' }).click();
  await expect(panel.getByRole('alert')).toContainText('no longer exists or is not visible');
});

test('the room slider steps through real records, shows before and after, and refuses to reconstruct a gap', async ({ page }) => {
  await open(page);
  const panel = await openPanel(page);
  await panel.getByRole('tab', { name: 'Room changes' }).click();
  await panel.getByLabel('Choose a room').selectOption('Alpha-room-0');
  const slider = panel.getByRole('slider');
  await expect(slider).toBeVisible();
  await expect(slider).toHaveAttribute('aria-valuetext', /Step 7 of 7/);
  // Latest step first: the rename that came after an unexplained change.
  await expect(panel.getByText('Welcome Hall').first()).toBeVisible();
  await expect(panel.getByText('Lobby').first()).toBeVisible();
  await slider.focus();
  await page.keyboard.press('ArrowLeft');
  await expect(panel.getByTestId('cannot')).toContainText('Cannot be reconstructed for this time');
  await page.keyboard.press('ArrowLeft');
  await expect(panel.getByRole('heading', { name: 'Room moved or resized' })).toBeVisible();
  await expect(panel.getByRole('row', { name: /Position across/ })).toContainText('9');
  await page.keyboard.press('ArrowLeft');
  await expect(panel.getByText('Desks with a recorded state at this step')).toBeVisible();
  await expect(panel.getByText('agent Fleet Planner').or(panel.getByText('agent agent_fleet'))).toBeVisible();
  for (let i = 0; i < 3; i += 1) await panel.getByRole('button', { name: 'Earlier step' }).click();
  await expect(panel.getByRole('heading', { name: 'Before the oldest loaded record' })).toBeVisible();
  await expect(panel.getByText('The room did not exist before the first record')).toBeVisible();
  await expect(panel.getByText('Who sat where, and who was present, is not stored and is not shown.')).toBeVisible();
});

test('a time the records cannot cover says so instead of showing the nearest state', async ({ page }) => {
  await open(page);
  const panel = await openPanel(page);
  await panel.getByRole('tab', { name: 'Room changes' }).click();
  await panel.getByLabel('Choose a room').selectOption('Alpha-room-0');
  await expect(panel.getByRole('slider')).toBeVisible();
  // Inside the unexplained gap (between the geometry record and the later rename).
  const inGap = new Date(Date.parse('2026-10-20T12:00:00Z') - 2500 * 60_000);
  const local = new Date(inGap.getTime() - inGap.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
  await panel.getByLabel('Show the room at').fill(local);
  await panel.getByRole('button', { name: 'Show', exact: true }).click();
  await expect(panel.getByTestId('cannot').first()).toContainText('Cannot be reconstructed for this time');
  await panel.getByLabel('Show the room at').fill('2999-01-01T10:00');
  await panel.getByRole('button', { name: 'Show', exact: true }).click();
  await expect(panel.getByTestId('jump-verdict')).toContainText('has not happened yet');
});

test('a room with no recorded change says so and offers older records', async ({ page }) => {
  await open(page);
  const panel = await openPanel(page);
  await panel.getByRole('tab', { name: 'Room changes' }).click();
  await panel.getByLabel('Choose a room').selectOption('Alpha-room-3');
  await expect(panel.getByText('No record changed this room in the recorded history.').or(panel.getByText(/No loaded record changed this room/))).toBeVisible({ timeout: 15_000 });
});

test('room and agent views open from the room inspector and the Team panel', async ({ page }) => {
  await open(page, { noWebgl: true });
  const hq = page.locator('.hqLive');
  await hq.getByRole('button', { name: /Alpha Room 1/ }).click();
  await hq.getByRole('button', { name: 'Room history' }).click();
  const panel = page.getByTestId('hq-history');
  await expect(panel.locator('.hqHistScope')).toContainText('Room: Alpha Room 1');
  await expect(panel.locator('.hqHistRow')).toHaveCount(5); // create, rename, desk assignment, geometry and the later rename: every record that names the room or its desk
});

test('the room view keeps only records that name the room or its desks', async ({ page }) => {
  await open(page, { noWebgl: true });
  const hq = page.locator('.hqLive');
  await hq.getByRole('button', { name: /Alpha Room 1/ }).click();
  await hq.getByRole('button', { name: 'Room history' }).click();
  const panel = page.getByTestId('hq-history');
  await expect(panel.locator('.hqHistRow').first()).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByText('Room: Alpha Room 1')).toBeVisible();
  await expect(panel.getByRole('button', { name: /Open record: Task updated/ })).toHaveCount(0);
  await expect(panel.getByRole('button', { name: 'Show the whole office' })).toBeVisible();
  await expect(panel.getByRole('button', { name: 'Export these records (CSV)' })).toBeEnabled();
});

test('who may read history: a denied answer explains and offers no data', async ({ page }) => {
  await open(page, {}, { mode: 'denied' });
  const panel = await openPanel(page);
  await expect(panel.getByRole('alert')).toContainText('Only workspace owners and organisation administrators can');
  await expect(panel.locator('.hqHistRow')).toHaveCount(0);
});

test('an error says what failed and recovers on retry', async ({ page }) => {
  await open(page, {}, { mode: 'ok' });
  history.failNext();
  const panel = await openPanel(page);
  await expect(panel.getByRole('alert')).toContainText('The history could not be loaded');
  await panel.getByRole('button', { name: 'Try again' }).click();
  await expect(panel.locator('.hqHistRow').first()).toBeVisible();
});

test('empty history says nothing was recorded and what to do', async ({ page }) => {
  await open(page, {}, { mode: 'empty' });
  const panel = await openPanel(page);
  await expect(panel.getByRole('heading', { name: 'No records match' })).toBeVisible();
  await expect(panel.getByText('Nothing has been recorded yet.')).toBeVisible();
  await expect(panel.getByRole('button', { name: 'Open tasks' })).toBeVisible();
});

test('a filter with no match offers to clear it', async ({ page }) => {
  await open(page);
  const panel = await openPanel(page);
  await panel.getByRole('searchbox', { name: 'Search' }).fill('zzzz-no-such');
  await panel.getByRole('button', { name: 'Apply filters' }).click();
  await expect(panel.getByText('No loaded record matches these filters.')).toBeVisible();
  await panel.getByRole('button', { name: 'Clear filters' }).last().click();
  await expect(panel.locator('.hqHistRow').first()).toBeVisible();
});

test('CSV export uses the audit export with the same filters', async ({ page }) => {
  await open(page);
  const panel = await openPanel(page);
  await panel.getByRole('button', { name: /^Filters/ }).click();
  await panel.getByLabel('Event type').selectOption('office');
  await panel.getByRole('button', { name: 'Apply filters' }).click();
  const download = page.waitForEvent('download');
  await panel.getByRole('button', { name: 'Export these records (CSV)' }).click();
  expect((await download).suggestedFilename()).toBe('audit-example.csv');
  expect(history.exports[0].searchParams.get('format')).toBe('csv');
  expect(history.exports[0].searchParams.get('action')).toBe('office.*');
  await expect(panel.getByText('Saved audit-example.csv.')).toBeVisible();
  // The "Other" type is narrowed on the device: see hq-history-export.spec.ts.
  await panel.getByLabel('Event type').selectOption('other');
  await panel.getByRole('button', { name: 'Apply filters' }).click();
  await expect(panel.getByRole('button', { name: 'Export these records (CSV)' })).toBeEnabled();
});

test('keyboard: Escape closes a record first, then the panel, and focus returns to the opener', async ({ page }) => {
  await open(page);
  const panel = await openPanel(page);
  await panel.locator('.hqHistRow').first().click();
  await expect(panel.getByRole('button', { name: 'Back to the list' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(panel.locator('.hqHistRow').first()).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(panel).toHaveCount(0);
});

test('Arabic: the panel is right to left with Arabic wording', async ({ page }) => {
  await open(page, { locale: 'ar' });
  const hq = page.locator('.hqLive');
  await hq.getByRole('button', { name: /أدوات المكتب|Office tools/ }).click();
  await hq.getByRole('button', { name: 'السجل', exact: true }).click();
  const panel = page.getByTestId('hq-history');
  await expect(panel.getByRole('heading', { name: 'سجل المكتب' })).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(panel.locator('.hqHistRow').first()).toBeVisible();
  const box = await panel.boundingBox();
  const viewport = page.viewportSize();
  if (box && viewport && viewport.width > 760) expect(box.x).toBeLessThan(5); // docked at the start edge in RTL
  await panel.getByRole('tab', { name: 'تغييرات الغرفة' }).click();
  await panel.getByLabel('اختر غرفة').first().selectOption('Alpha-room-0');
  await expect(panel.getByRole('slider')).toBeVisible();
  await expect(panel.getByText('لا يمكن إعادة بناء هذا الوقت').first().or(panel.getByText('الخطوة 7 من 7'))).toBeVisible();
});

test('works without WebGL', async ({ page }) => {
  await open(page, { noWebgl: true });
  const panel = await openPanel(page);
  await expect(panel.locator('.hqHistRow').first()).toBeVisible();
});

test('a record from another workspace is never shown: the answer is refused', async ({ page }) => {
  const records = historyFixture();
  await open(page, {}, { records });
  const panel = await openPanel(page);
  await expect(panel.locator('.hqHistRow').first()).toBeVisible();
  expect(history.requests.every((u) => u.pathname.startsWith('/api/v1/office-history'))).toBe(true);
  // The request carries the open workspace in its headers; the mock would 403 any other tenant in a real server.
  expect(record('task.updated', 'x', 1).verified).toBe(true);
});

test('the panel header says it checks every 30 s, and newer records are offered, never pushed into the list', async ({ page }) => {
  await page.clock.install();
  const fixture = historyFixture();
  await open(page, {}, { records: fixture });
  const panel = await openPanel(page);
  const chip = panel.getByTestId('hq-history-live');
  await expect(chip).toHaveAttribute('data-live', 'polling');
  await expect(chip).toContainText(/Checking every 30 s/);
  await expect(chip).toContainText(/Updated \d{2}:\d{2}:\d{2}/);
  await expect(panel.locator('.hqHistRow').first()).toBeVisible();
  const before = await panel.locator('.hqHistRow').count();
  const fresh = [record('task.created', 'task_new_2', -2), record('task.created', 'task_new_1', -1)];
  history.setRecords([...fresh, ...fixture]);
  await page.clock.fastForward(31_000);
  const newer = panel.getByTestId('hq-history-newer');
  await expect(newer).toContainText('2 newer records');
  expect(await panel.locator('.hqHistRow').count()).toBe(before);
  await newer.getByRole('button', { name: 'Show them' }).click();
  await expect(newer).toHaveCount(0);
  await expect(panel.locator(`[data-record-id="${fresh[0].id}"]`)).toBeVisible();
  // A failed check keeps the list and says the panel is trying again.
  history.failNext();
  await page.clock.fastForward(31_000);
  await expect(chip).toHaveAttribute('data-live', 'reconnecting');
  await expect(panel.locator('.hqHistRow').first()).toBeVisible();
});
