import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { historyFixture, mockHistory, record, type HistoryMock, type HistoryMockOptions } from './support/hqHistoryApi';

// History CSV export for room views, agent views and the "Other" type (docs/ANUM_MINE_HQ_HISTORY_PANEL.md,
// matrix row 10.9), and the request links of a record (row 10.4). The mocked audit export returns JSON rows;
// the panel keeps the rows of the view and saves them as CSV. API mocked at http://localhost:8000/**.
const registry = hqRegistry(TENANT_A, { rooms: 4, desks: 4, agentIds: ['agent_fleet', 'agent_leads', 'agent_care', 'agent_invoice'] });
let history: HistoryMock;
const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;

function records() {
  const all = historyFixture();
  all.push(record('office.room.update', 'Alpha-room-0', 100, { resource_kind: 'rooms', prompt: 'private words that must not be exported', note: 'kept' }));
  all.push(record('auth.session.revoke', 'session_example', 90, {}));
  return all.sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp) || b.id.localeCompare(a.id));
}

async function open(page: Page, options: Parameters<typeof openHq>[1] = {}, mock: HistoryMockOptions = {}) {
  await openHq(page, { registry, noWebgl: true, ...options, beforeGoto: async (p) => { history = await mockHistory(p, { records: records(), ...mock }); } });
  await expect(page.locator('.hqLive')).toBeVisible();
}
async function openRoomPanel(page: Page) {
  const hq = page.locator('.hqLive');
  await hq.getByRole('button', { name: /Alpha Room 1/ }).click();
  await hq.getByRole('button', { name: 'Room history' }).click();
  const panel = page.getByTestId('hq-history');
  await expect(panel.locator('.hqHistRow').first()).toBeVisible({ timeout: 15_000 });
  return panel;
}
async function noOverflow(page: Page) {
  const wide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(wide).toBeLessThanOrEqual(0);
  const panelWide = await page.getByTestId('hq-history').evaluate((el) => el.scrollWidth - el.clientWidth);
  expect(panelWide).toBeLessThanOrEqual(0);
}

for (const [width, height] of SIZES) {
  test(`room view export keeps only the room's records and no secret text at ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await open(page);
    const panel = await openRoomPanel(page);
    await expect(panel.getByText('exports every stored record of this workspace that matches it', { exact: false })).toBeVisible();
    const download = page.waitForEvent('download');
    await panel.getByRole('button', { name: 'Export these records (CSV)' }).click();
    const file = await download;
    expect(file.suggestedFilename()).toMatch(/^history-room-\d{4}-\d{2}-\d{2}\.csv$/);
    const csv = readFileSync(await file.path(), 'utf8');
    expect(csv.split('\r\n')[0]).toBe('id,created_at,actor,action,target,outcome,correlation_id,tenant_id,workspace_id,metadata');
    expect(csv).toContain('office.room.create');
    expect(csv).toContain('office.desk.assignment'); // names a desk of the room
    expect(csv).not.toContain('task.updated');
    expect(csv).not.toContain('private words that must not be exported');
    expect(csv).toContain('kept');
    expect(history.exports[0].searchParams.get('format')).toBe('json');
    await expect(panel.getByText(/^Saved history-room-.*with \d+ records\.$/)).toBeVisible();
    await noOverflow(page);
    await page.screenshot({ path: `test-results/history-export-room-${width}x${height}.png` });
  });
}

test('the Other type exports only records outside every named type', async ({ page }) => {
  await open(page);
  await page.locator('.hqLive').getByRole('button', { name: 'Office tools' }).click();
  await page.locator('.hqLive').getByRole('button', { name: 'History', exact: true }).click();
  const panel = page.getByTestId('hq-history');
  await panel.getByRole('button', { name: /^Filters/ }).click();
  await panel.getByLabel('Event type').selectOption('other');
  await panel.getByRole('button', { name: 'Apply filters' }).click();
  const download = page.waitForEvent('download');
  await panel.getByRole('button', { name: 'Export these records (CSV)' }).click();
  const csv = readFileSync(await (await download).path(), 'utf8');
  expect(csv).toContain('auth.session.revoke');
  expect(csv).not.toContain('office.room');
  expect(csv).not.toContain('task.updated');
});

test('an agent view exports the records that name the agent', async ({ page }) => {
  await open(page);
  const hq = page.locator('.hqLive');
  await hq.getByRole('button', { name: 'Office tools' }).click();
  await hq.getByRole('button', { name: 'History', exact: true }).click();
  const panel = page.getByTestId('hq-history');
  await panel.getByRole('button', { name: /^Filters/ }).click();
  const agentSelect = panel.locator('select').nth(2);
  await agentSelect.selectOption({ index: 1 });
  await panel.getByRole('button', { name: 'Apply filters' }).click();
  await expect(panel.locator('.hqHistScope')).toContainText('Agent');
  const download = page.waitForEvent('download');
  await panel.getByRole('button', { name: 'Export these records (CSV)' }).click();
  const csv = readFileSync(await (await download).path(), 'utf8');
  expect(csv.split('\r\n').length).toBeGreaterThan(1);
  expect(csv).not.toContain('auth.session.revoke');
});

test('a viewer who may not export is told who can, and nothing is requested', async ({ page }) => {
  await open(page, {}, { canExport: false });
  const panel = await openRoomPanel(page);
  await expect(panel.getByRole('button', { name: 'Export these records (CSV)' })).toBeDisabled();
  await expect(panel.getByText('Ask a workspace owner for the export permission.')).toBeVisible();
  expect(history.exports).toHaveLength(0);
});

test('a refused export (permission unknown beforehand) explains who can do it', async ({ page }) => {
  await open(page, {}, { exportDenied: true });
  const panel = await openRoomPanel(page);
  await panel.getByRole('button', { name: 'Export these records (CSV)' }).click();
  await expect(panel.getByRole('alert')).toContainText('Ask a workspace owner for the export permission.');
});

test('a view with no matching stored record saves no file and says so', async ({ page }) => {
  await open(page);
  const hq = page.locator('.hqLive');
  await hq.getByRole('button', { name: /Alpha Room 2/ }).click();
  await hq.getByRole('button', { name: 'Room history' }).click();
  const panel = page.getByTestId('hq-history');
  await panel.getByRole('button', { name: /^Filters/ }).click();
  await panel.getByLabel('Search', { exact: false }).first().fill('no-such-text');
  await panel.getByRole('button', { name: 'Apply filters' }).click();
  await panel.getByRole('button', { name: 'Export these records (CSV)' }).click();
  await expect(panel.getByText('No stored record matches this view, so no file was saved.')).toBeVisible();
});

test('a record links to its whole request, and its sources to the records that name them', async ({ page }) => {
  const all = records();
  const shared = 'corr_shared';
  all.filter((r) => r.action === 'approval.approved' || r.action === 'task.updated' && r.target === 'task_1').forEach((r) => { r.correlation_id = shared; });
  await open(page, {}, { records: all });
  await page.locator('.hqLive').getByRole('button', { name: 'Office tools' }).click();
  await page.locator('.hqLive').getByRole('button', { name: 'History', exact: true }).click();
  const panel = page.getByTestId('hq-history');
  await panel.getByRole('button', { name: /Approval granted/ }).first().click();
  await panel.getByRole('button', { name: 'Show records of the same request' }).click();
  await expect(panel.getByText('2 records share this correlation id.')).toBeVisible();
  await panel.getByRole('button', { name: 'Open this request as the timeline' }).click();
  await expect(panel.locator('.hqHistScope')).toContainText(shared);
  await expect(panel.locator('.hqHistRow')).toHaveCount(2);
  await expect(panel.getByRole('button', { name: 'Export these records (CSV)' })).toBeEnabled();
  expect(history.requests.some((u) => u.pathname.includes(`/correlations/${shared}`))).toBe(true);
  // From a source: the stored records that name the task.
  await panel.getByRole('button', { name: 'Show the whole office' }).click();
  await panel.getByRole('button', { name: /Approval granted/ }).first().click();
  await panel.getByRole('button', { name: 'Show records that name this Task' }).click();
  await expect(panel.getByRole('button', { name: /Task updated/ }).first()).toBeVisible();
  expect(history.requests.some((u) => u.searchParams.get('q') === 'task_1')).toBe(true);
});

test('a record without a correlation id says no other record can be tied to it', async ({ page }) => {
  const all = records();
  all.find((r) => r.action === 'auth.session.revoke')!.correlation_id = '';
  await open(page, {}, { records: all });
  await page.locator('.hqLive').getByRole('button', { name: 'Office tools' }).click();
  await page.locator('.hqLive').getByRole('button', { name: 'History', exact: true }).click();
  const panel = page.getByTestId('hq-history');
  await panel.getByRole('button', { name: /^Filters/ }).click();
  await panel.getByLabel('Search', { exact: false }).first().fill('session_example');
  await panel.getByRole('button', { name: 'Apply filters' }).click();
  await panel.locator('.hqHistRow').first().click();
  await expect(panel.getByText('This record has no correlation id')).toBeVisible();
});

for (const [width, height] of SIZES) {
  test(`Arabic: export note, button and counts read right to left without overflow at ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await open(page, { locale: 'ar' });
    const hq = page.locator('.hqLive');
    await hq.getByRole('button', { name: /Alpha Room 1/ }).click();
    await hq.getByRole('button', { name: 'سجل الغرفة' }).click();
    const panel = page.getByTestId('hq-history');
    await expect(panel.locator('.hqHistRow').first()).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
    await expect(panel.getByRole('button', { name: 'تصدير هذه السجلات (CSV)' })).toBeEnabled();
    await expect(panel.getByText('يصدر هذا العرض كل سجل مخزن', { exact: false })).toBeVisible();
    await noOverflow(page);
    await panel.getByRole('button', { name: 'تصدير هذه السجلات (CSV)' }).click();
    await expect(panel.getByText(/تم حفظ .*history-room-.*ويحتوي/)).toBeVisible();
    await noOverflow(page);
    await page.screenshot({ path: `test-results/history-export-ar-${width}x${height}.png` });
  });
}
