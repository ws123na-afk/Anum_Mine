import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { ATTENDANCE_PEOPLE, attendanceReply, attendanceState } from './support/attendanceApi';
import { useSavedLocale } from './support/workspaceApi';

// Work attendance (docs/attendance.md#web): check in and out on Today with a retry that is never counted
// twice, hours and site visits on Reports with the CSV export, a daily report with an AI draft from the
// person's own entries, a correction with its required reason, settings (consent, pause, sites, sharing),
// Arabic right to left, and the empty, error and permission-denied states. Runs on desktop and phone.
async function mockApi(page: Page, options: Parameters<typeof attendanceState>[0] & { role?: 'owner' | 'member' | 'viewer'; failCheck?: number } = {}) {
  const role = options.role ?? 'member';
  const state = attendanceState(options);
  const sent: { method: string; path: string; body: unknown }[] = [];
  let failures = options.failCheck ?? 0;
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role, active: true });
    if (path === '/api/v1/workspaces/current') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', archived_at: null, archived_by: null });
    if (path === '/api/v1/me/workspace-memberships') return json([{ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', role, active: true }]);
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals' || path === '/api/v1/conversations') return json([]);
    if (path === '/api/v1/team/people') return json(ATTENDANCE_PEOPLE);
    const body = request.postData() ? request.postDataJSON() : null;
    if (path.startsWith('/api/v1/attendance')) sent.push({ method: request.method(), path, body });
    if (path === '/api/v1/attendance/check' && failures > 0) { failures -= 1; return route.abort('failed'); }
    if (path.startsWith('/api/v1/attendance/export.')) return route.fulfill({ status: 200, contentType: 'text/csv', headers: { 'content-disposition': 'attachment; filename="attendance-week.csv"' }, body: 'date,time_zone\n' });
    const reply = attendanceReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { state, sent };
}

const tab = (page: Page, name: string) => page.getByRole('navigation', { name: 'Attendance sections' }).getByRole('button', { name });

async function noSeriousAxe(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
  expect(serious).toEqual([]);
}

test('check in, a failed press retried with the same key, then check out; it says it is not employer-certified', async ({ page }) => {
  const { sent } = await mockApi(page, { failCheck: 1 });
  await page.goto('/#attendance');
  await expect(page.getByRole('heading', { name: 'Attendance', level: 1 })).toBeVisible();
  await expect(page.getByText('A personal work log kept by you. It is not certified by an employer.')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Not checked in' })).toBeVisible();
  await page.getByRole('button', { name: 'Check in' }).click();
  await expect(page.getByText(/Couldn't record it/)).toBeVisible();
  await page.getByRole('button', { name: 'Check in' }).click();
  await expect(page.getByText(/Arrived recorded at/)).toBeVisible();
  const checks = sent.filter((r) => r.path === '/api/v1/attendance/check').map((r) => (r.body as { idempotency_key: string }).idempotency_key);
  expect(checks.length).toBe(2);
  expect(new Set(checks).size).toBe(1);
  await expect(page.getByRole('heading', { name: 'At Head office' })).toBeVisible();
  await page.getByRole('button', { name: 'Check out' }).click();
  await expect(page.getByText(/Left recorded at/)).toBeVisible();
  await noSeriousAxe(page);
});

test('reports show week hours, site visits and flags, export CSV, and tabs are deep links', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#attendance-reports');
  await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toContainText('Reports');
  const table = page.getByRole('table', { name: 'Hours by day' });
  await expect(table.getByRole('cell', { name: 'Head office, Tower B site' })).toBeVisible();
  await expect(table.getByText('Check out missing')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Site visits' })).toBeVisible();
  await expect(page.getByText('Tower B site').first()).toBeVisible();
  await page.getByRole('button', { name: 'CSV' }).click();
  await expect(page.getByText(/Saved attendance-[\w-]+\.csv/)).toBeVisible();
  expect(sent.some((r) => r.path === '/api/v1/attendance/export.csv')).toBe(true);
  await tab(page, 'Daily report').click();
  await expect(page).toHaveURL(/#attendance-daily$/);
  await page.goBack();
  await expect(page).toHaveURL(/#attendance-reports$/);
  await noSeriousAxe(page);
});

test('a daily report is written, an AI draft from own entries is added, and earlier reports are searched', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#attendance-daily');
  await expect(page.getByText('No report for this day yet. Write one below.')).toBeVisible();
  await page.getByRole('button', { name: 'Save report' }).click();
  await expect(page.getByRole('alert')).toContainText('Write at least one task');
  await page.getByLabel('Project').fill('Tower B');
  await page.getByLabel('Tasks done').fill('Poured the slab\nChecked the pumps');
  await page.getByLabel('Priority').selectOption('urgent');
  await page.getByRole('button', { name: 'Draft day summary' }).click();
  await expect(page.getByRole('region', { name: 'AI draft' })).toContainText('Valves still need ordering');
  await page.getByRole('button', { name: 'Add to notes' }).click();
  await page.getByRole('button', { name: 'Save report' }).click();
  await expect(page.getByText(/^Report for .* saved\.$/)).toBeVisible();
  const saved = sent.find((r) => r.method === 'PUT' && r.path.startsWith('/api/v1/attendance/daily-reports/'))?.body as { tasks_done: string[]; drafted_with_ai: boolean; priority: string };
  expect(saved.tasks_done).toEqual(['Poured the slab', 'Checked the pumps']);
  expect(saved.drafted_with_ai).toBe(true);
  expect(saved.priority).toBe('urgent');
  await page.getByRole('searchbox', { name: 'Search reports' }).fill('valves');
  await page.getByRole('button', { name: 'Find' }).click();
  await expect(page.getByRole('heading', { name: 'Earlier reports' })).toBeVisible();
  await expect(page.getByText('Tower B').first()).toBeVisible();
});

test('a correction needs a reason, then lands in the history', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#attendance-corrections');
  await page.getByRole('radio', { name: 'Change' }).click();
  await page.getByLabel('Record').selectOption({ index: 1 });
  await page.getByRole('button', { name: 'Save correction' }).click();
  await expect(page.getByRole('alert')).toContainText('Say why');
  await page.getByLabel('Reason').fill('Left later than the phone said');
  await page.getByRole('button', { name: 'Save correction' }).click();
  await expect(page.getByText('Correction saved. Your hours are updated.')).toBeVisible();
  const body = sent.find((r) => r.method === 'POST' && r.path === '/api/v1/attendance/corrections')?.body as { action: string; event_id: string; reason: string };
  expect(body.action).toBe('change');
  expect(body.event_id).toMatch(/^att_evt_/);
  await expect(page.getByRole('region', { name: 'Correction history' }).getByText('Left later than the phone said')).toBeVisible();
});

test('settings: consent and pause, add a site, share with a colleague and read the Shortcut steps', async ({ page }) => {
  const { sent } = await mockApi(page, { empty: true });
  await page.goto('/#attendance-settings');
  await page.getByRole('button', { name: 'Turn on location check in' }).click();
  await expect(page.getByText('Location check in is on.').first()).toBeVisible();
  await page.getByRole('button', { name: 'Pause' }).click();
  await expect(page.getByText(/Paused\. Nothing is recorded/)).toBeVisible();
  await page.getByLabel('Name', { exact: true }).fill('Head office');
  await page.getByRole('button', { name: 'Add site' }).click();
  await expect(page.getByRole('region', { name: 'Workplace and sites' }).getByText('Head office')).toBeVisible();
  await page.getByLabel('Share with').selectOption('user_manager');
  await page.getByRole('button', { name: 'Share', exact: true }).click();
  await expect(page.getByText('Layla Manager')).toBeVisible();
  expect(sent.find((r) => r.path === '/api/v1/attendance/shares' && r.method === 'POST')?.body).toEqual({ viewer_user_id: 'user_manager', include_reports: false });
  await expect(page.getByRole('heading', { name: /iPhone Shortcuts/ })).toBeVisible();
  // No coordinates are ever asked for or sent.
  expect(JSON.stringify(sent)).not.toMatch(/latitude|longitude/);
  await noSeriousAxe(page);
});

test('empty, error and permission-denied states explain what to do', async ({ page }) => {
  await mockApi(page, { empty: true });
  await page.goto('/#attendance');
  await expect(page.getByText('Add your workplace first, then check in there.')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'No records today' })).toBeVisible();
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockApi(page, { failToday: true });
  await page.reload();
  await expect(page.getByRole('alert')).toContainText("Couldn't read your attendance");
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockApi(page, { denied: true, role: 'viewer' });
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Attendance is not available for your role' })).toBeVisible();
});

test('Arabic is right to left with the same flow', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockApi(page);
  await page.goto('/#attendance');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { name: 'الحضور', level: 1 })).toBeVisible();
  await expect(page.getByRole('button', { name: 'تسجيل الدخول' })).toBeVisible();
  await noSeriousAxe(page);
});
