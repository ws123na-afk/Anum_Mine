import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { historyFixture, mockHistory, record, type HistoryMock, type HistoryMockOptions, type HistoryRecord } from './support/hqHistoryApi';

// "Show history" on a task, meeting or approval screen (matrix row 10.4) and the JSON export with the server's
// room or agent filter (row 10.9). docs/ANUM_MINE_HQ_HISTORY_PANEL.md. The API is mocked at http://localhost:8000;
// ids and names are plainly fake. The History panel is a lazy chunk: it must not load before the click.
const registry = hqRegistry(TENANT_A, { rooms: 4, desks: 4, agentIds: ['agent_fleet', 'agent_leads', 'agent_care', 'agent_invoice'] });
const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
let history: HistoryMock;

/** task_plan and ap_1 (the desk work fixture) and meeting_live (the live meeting) with their stored records. */
function records(): HistoryRecord[] {
  const all = historyFixture();
  all.push(record('task.created', 'task_plan', 600, {}, { correlation_id: 'corr_plan_create' }));
  all.push(record('task.started', 'task_plan', 500, {}, { correlation_id: 'corr_plan_create' }));
  all.push(record('agent_run.started', 'run_1', 499, { task_id: 'task_plan' }, { correlation_id: 'corr_plan_create' }));
  all.push(record('task.retried', 'task_plan', 300, {}, { correlation_id: 'corr_plan_retry' }));
  all.push(record('approval.requested', 'ap_1', 200, { task_id: 'task_plan' }, { correlation_id: 'corr_ap' }));
  all.push(record('agent_meeting.create', 'meeting_live', 150, { room_id: 'Alpha-room-0' }, { correlation_id: '' }));
  return all.sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp) || b.id.localeCompare(a.id));
}

async function open(page: Page, options: Parameters<typeof openHq>[1] = {}, mock: HistoryMockOptions = {}) {
  await openHq(page, { registry, noWebgl: true, meetingRoomId: 'Alpha-room-0', ...options, beforeGoto: async (p) => { history = await mockHistory(p, { records: records(), ...mock }); } });
  await expect(page.locator('.hqLive')).toBeVisible();
}
/** Records every script the page fetches, so a test can prove the History chunk loads only on the click. */
function scripts(page: Page): string[] {
  const seen: string[] = [];
  page.on('request', (r) => { if (r.resourceType() === 'script') seen.push(r.url()); });
  return seen;
}
const historyChunk = (urls: string[]) => urls.some((u) => /HqHistoryPanel-[^/]+\.js$/.test(u));
async function openTask(page: Page, taskId: string) {
  await page.evaluate((id) => window.dispatchEvent(new CustomEvent('anum:hq-open-task', { detail: id })), taskId);
  const side = page.getByRole('complementary', { name: 'Task details' });
  await expect(side.getByRole('heading', { name: 'Plan October tyre rotation' })).toBeVisible();
  return side;
}
async function routeTask(page: Page) {
  await page.route(/http:\/\/localhost:8000\/api\/v1\/tasks\/task_plan(\?.*)?$/, (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
    id: 'task_plan', title: 'Plan October tyre rotation', prompt: 'Rotate the tyres.', status: 'running', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation',
    created_at: '2026-10-20T09:00:00Z', updated_at: '2026-10-20T10:00:00Z', agent_id: 'agent_fleet',
  }) }));
  await page.route(/http:\/\/localhost:8000\/api\/v1\/tasks\/task_plan\/runs(\?.*)?$/, (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));
}
async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
  expect(await page.getByTestId('hq-history').evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
}

test('a task opens its first request on click; the panel chunk loads only then; another request can be picked', async ({ page }) => {
  const loaded = scripts(page);
  await open(page, {}, {});
  await routeTask(page);
  const side = await openTask(page, 'task_plan');
  expect(historyChunk(loaded)).toBe(false);
  await side.getByRole('button', { name: /^Show history/ }).click();
  // The task side panel steps aside so the History panel is not covered.
  await expect(side).toHaveCount(0);
  const panel = page.getByTestId('hq-history');
  await expect(panel.locator('.hqHistScope')).toContainText('Task: Plan October tyre rotation');
  await expect(panel.locator('.hqHistScope')).toContainText('One request: corr_plan_create');
  expect(historyChunk(loaded)).toBe(true);
  // The item was found by its exact target, never by a text search.
  expect(history.requests.some((u) => u.searchParams.get('target') === 'task_plan' && !u.searchParams.get('q'))).toBe(true);
  await expect(panel.locator('.hqHistRow')).toHaveCount(3);
  const requests = panel.getByTestId('hq-history-requests');
  await expect(requests).toContainText('This Task is named in 2 requests.');
  await requests.getByRole('button', { name: /task\.retried/ }).click();
  await expect(panel.locator('.hqHistScope')).toContainText('One request: corr_plan_retry');
  await expect(panel.locator('.hqHistRow')).toHaveCount(1);
  await expect(requests.getByRole('button', { name: /task\.retried/ })).toHaveAttribute('aria-pressed', 'true');
  // The request can be exported as before.
  await expect(panel.getByRole('button', { name: 'Export these records (CSV)' })).toBeEnabled();
  await expect(panel.getByRole('button', { name: 'Export as JSON' })).toBeEnabled();
});

test('an approval on the agent card opens its request; a meeting with no correlation id says so honestly', async ({ page }) => {
  await open(page);
  const hq = page.locator('.hqLive');
  await hq.getByRole('button', { name: /Alpha Room 1/ }).click();
  // The live meeting held in the room: its record has no correlation id, so no request is opened.
  const meeting = page.locator('[data-testid="room-meeting"]').first();
  await expect(meeting).toBeVisible();
  await meeting.scrollIntoViewIfNeeded();
  await page.locator('.hqSide').first().screenshot({ path: 'test-results/history-links-meeting.png' });
  await meeting.getByRole('button', { name: /^Show history/ }).click();
  const panel = page.getByTestId('hq-history');
  await expect(panel.getByTestId('hq-history-record-none')).toContainText('No history recorded for this yet.');
  await expect(panel.getByTestId('hq-history-record-none')).toContainText('Stored records that name this Meeting: 1.');
  await panel.getByRole('button', { name: 'Show records that name this Meeting' }).click();
  await expect(panel.locator('.hqHistRow')).toHaveCount(1);
  await panel.getByRole('button', { name: 'Close history' }).click();

  // The agent's workstation card lists the pending approval.
  await hq.locator('.hqSide').getByRole('button', { name: /Fleet Planner|agent_fleet/ }).first().click();
  const approvals = page.locator('.hqWork');
  await expect(approvals.getByText('send_email')).toBeVisible();
  await page.screenshot({ path: 'test-results/history-links-approval.png' });
  await approvals.locator('li', { hasText: 'send_email' }).getByRole('button', { name: /^Show history/ }).click();
  await expect(panel.locator('.hqHistScope')).toContainText('Approval: send_email');
  // ap_1 has two stored requests: the oldest opens first (the history fixture's approval), the other can be picked.
  const requests = panel.getByTestId('hq-history-requests');
  await expect(requests).toContainText('This Approval is named in 2 requests.');
  await expect(panel.locator('.hqHistScope')).not.toContainText('corr_ap');
  await requests.getByRole('button', { name: /approval\.requested/ }).click();
  await expect(panel.locator('.hqHistScope')).toContainText('One request: corr_ap');
  await expect(panel.locator('.hqHistRow')).toHaveCount(1);
});

test('an item with no stored record says no history is recorded yet and offers no export', async ({ page }) => {
  await open(page, {}, { records: historyFixture() });
  await routeTask(page);
  const side = await openTask(page, 'task_plan');
  await side.getByRole('button', { name: /^Show history/ }).click();
  const panel = page.getByTestId('hq-history');
  await expect(panel.getByTestId('hq-history-record-none')).toContainText('No history recorded for this yet.');
  await expect(panel.getByTestId('hq-history-record-none')).toContainText('No stored audit record names this Task.');
  await expect(panel.getByRole('button', { name: /Export/ })).toHaveCount(0);
});

test('a reader without history access is told who can read it', async ({ page }) => {
  await open(page, {}, { mode: 'denied' });
  await routeTask(page);
  const side = await openTask(page, 'task_plan');
  await side.getByRole('button', { name: /^Show history/ }).click();
  await expect(page.getByTestId('hq-history').getByRole('alert')).toContainText('Only workspace owners and organisation administrators can.');
});

test('a room view exports JSON narrowed on the server by the room and its desks', async ({ page }) => {
  await open(page);
  const hq = page.locator('.hqLive');
  await hq.getByRole('button', { name: /Alpha Room 1/ }).click();
  await hq.getByRole('button', { name: 'Room history' }).click();
  const panel = page.getByTestId('hq-history');
  await expect(panel.locator('.hqHistRow').first()).toBeVisible({ timeout: 15_000 });
  const download = page.waitForEvent('download');
  await panel.getByRole('button', { name: 'Export as JSON' }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^history-room-\d{4}-\d{2}-\d{2}\.json$/);
  const rows = JSON.parse(readFileSync(await file.path(), 'utf8')) as { action: string; target: string }[];
  expect(rows.some((r) => r.action === 'office.room.create')).toBe(true);
  expect(rows.some((r) => r.action === 'office.desk.assignment')).toBe(true);
  expect(rows.some((r) => r.action === 'task.updated')).toBe(false);
  const sent = history.exports[history.exports.length - 1];
  expect(sent.searchParams.get('format')).toBe('json');
  expect(sent.searchParams.getAll('mention')).toContain('Alpha-room-0');
  expect(sent.searchParams.getAll('mention').length).toBeGreaterThan(1);
  await expect(panel.getByText(/^Saved history-room-.*\.json with \d+ records\.$/)).toBeVisible();
});

test('the whole office exports JSON straight from the audit export', async ({ page }) => {
  await open(page);
  await page.locator('.hqLive').getByRole('button', { name: 'Office tools' }).click();
  await page.locator('.hqLive').getByRole('button', { name: 'History', exact: true }).click();
  const panel = page.getByTestId('hq-history');
  await expect(panel.locator('.hqHistRow').first()).toBeVisible();
  const download = page.waitForEvent('download');
  await panel.getByRole('button', { name: 'Export as JSON' }).click();
  await download;
  const sent = history.exports[history.exports.length - 1];
  expect(sent.searchParams.get('format')).toBe('json');
  expect(sent.searchParams.getAll('mention')).toEqual([]);
});

for (const locale of ['en', 'ar'] as const) {
  for (const [width, height] of SIZES) {
    test(`${locale}: Show history from a task fits without overflow at ${width}x${height}`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      await open(page, { locale });
      await routeTask(page);
      await page.evaluate(() => window.dispatchEvent(new CustomEvent('anum:hq-open-task', { detail: 'task_plan' })));
      const side = page.locator('.hqAsSide');
      await expect(side.getByRole('heading', { name: 'Plan October tyre rotation' })).toBeVisible();
      const link = side.getByTestId('hq-show-history');
      await expect(link).toHaveText(locale === 'ar' ? 'عرض السجل' : 'Show history');
      await page.screenshot({ path: `test-results/history-links-task-${locale}-${width}x${height}.png` });
      await link.click();
      const panel = page.getByTestId('hq-history');
      await expect(panel.getByTestId('hq-history-requests')).toBeVisible();
      await expect(panel.locator('.hqHistRow')).toHaveCount(3);
      if (locale === 'ar') await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
      await expect(panel.getByRole('button', { name: locale === 'ar' ? 'تصدير بصيغة JSON' : 'Export as JSON' })).toBeVisible();
      await noOverflow(page);
      await page.screenshot({ path: `test-results/history-links-panel-${locale}-${width}x${height}.png` });
    });
  }
}
