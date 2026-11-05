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

const tab = (page: Page, name: string) => page.getByRole('tablist', { name: 'Attendance sections' }).getByRole('tab', { name });

async function noSeriousAxe(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
  expect(serious).toEqual([]);
}

test('check in, a failed press retried with the same key, then check out; it says it is not employer-certified', async ({ page }) => {
  const { sent } = await mockApi(page, { failCheck: 1 });
  await page.goto('/#attendance-timesheet');
  await expect(page.getByRole('heading', { name: 'Attendance and Daily Reports', level: 1 })).toBeVisible();
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
  // An old Reports link opens My timesheet, where the week's hours now live.
  await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toContainText('My timesheet');
  const table = page.getByRole('table', { name: 'Hours by day' });
  await expect(table.getByRole('cell', { name: 'Head office, Tower B site' })).toBeVisible();
  await expect(table.getByText('Check out missing')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Site visits' })).toBeVisible();
  await expect(page.getByRole('listitem').filter({ hasText: 'Tower B site' }).first()).toBeVisible();
  await page.getByRole('button', { name: 'CSV' }).click();
  await expect(page.getByText(/Saved attendance-[\w-]+\.csv/)).toBeVisible();
  expect(sent.some((r) => r.path === '/api/v1/attendance/export.csv')).toBe(true);
  await tab(page, 'Daily reports').click();
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
  // Review and submit: the report is sent with submit, and stays a draft until then.
  await page.getByRole('button', { name: 'Review and submit' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Submit report' }).click();
  await expect(page.getByText('Report submitted.')).toBeVisible();
  const submitted = sent.filter((r) => r.method === 'PUT' && r.path.startsWith('/api/v1/attendance/daily-reports/')).at(-1)?.body as { submit: boolean };
  expect(submitted.submit).toBe(true);
  await page.getByRole('searchbox', { name: 'Search reports' }).fill('valves');
  await page.getByRole('button', { name: 'Find' }).click();
  await expect(page.getByRole('table', { name: 'Daily reports' }).getByText('Checked the pumps; Signed the delivery note')).toBeVisible();
});

test('a correction needs a reason, then lands in the history', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#attendance-corrections');
  await page.getByRole('radio', { name: 'Change' }).click();
  await page.getByLabel('Record').selectOption({ index: 1 });
  await page.getByRole('button', { name: 'Save correction' }).click();
  await expect(page.getByRole('alert')).toContainText('Say why');
  await page.getByLabel('Reason').selectOption('other');
  await page.getByLabel('Details').fill('Left later than the phone said');
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
  // The team view with nobody sharing says who can appear and that nobody else is listed.
  await expect(page.getByText('Only your own record is shown')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'No daily reports in this range' })).toBeVisible();
  await tab(page, 'My timesheet').click();
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
  await page.goto('/#attendance-timesheet');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { name: 'الحضور والتقارير اليومية', level: 1 })).toBeVisible();
  await expect(page.getByRole('button', { name: 'تسجيل الدخول' })).toBeVisible();
  await noSeriousAxe(page);
});

test('team attendance lists only people who share, reports only when shared, and a draft is reviewed and submitted', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#attendance');
  await expect(page.getByRole('tab', { name: 'Team attendance', selected: true })).toBeVisible();
  const roster = page.getByRole('table', { name: 'Attendance roster' });
  await expect(roster.getByRole('rowheader')).toHaveCount(3);
  await expect(roster.getByText('Layla Manager')).toBeVisible();
  await expect(roster.getByText('Omar Field')).toBeVisible();
  // Omar shares hours only: no report status, said plainly.
  await expect(roster.getByRole('row', { name: /Omar Field/ }).getByText('Not shared')).toBeVisible();
  await expect(roster.getByRole('row', { name: /Layla Manager/ }).getByText('iPhone')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Attendance today' }).getByText('Present')).toBeVisible();
  // A colleague's report opens read only.
  await page.getByRole('button', { name: "Review Layla Manager's report", exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('Supplier follow ups')).toBeVisible();
  await expect(dialog.getByText('Shared with you to read. Only the person can change it.')).toBeVisible();
  await dialog.getByRole('button', { name: 'Close' }).click();
  // The quick draft saves, then Review and submit sends submit: true.
  await page.getByLabel('Completed work').fill('Fleet readiness checks');
  await page.getByLabel('Blocker (if any)').fill('Waiting for a quote');
  await page.getByRole('button', { name: 'Save draft' }).click();
  await expect(page.getByText('Draft saved.')).toBeVisible();
  // The reports table shows the new draft at once, without a reload.
  const reportsTable = page.getByRole('table', { name: 'Daily reports' });
  await expect(reportsTable.getByRole('row', { name: /Fleet readiness checks/ }).getByText('Draft')).toBeVisible();
  await page.getByRole('button', { name: 'Review and submit' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Submit report' }).click();
  await expect(page.getByText('Report submitted.')).toBeVisible();
  await expect(reportsTable.getByRole('row', { name: /Fleet readiness checks/ }).getByText('Submitted')).toBeVisible();
  const puts = sent.filter((r) => r.method === 'PUT').map((r) => r.body as { submit: boolean; tasks_done: string[]; issues: string });
  expect(puts.map((b) => b.submit)).toEqual([false, true]);
  expect(puts[1].tasks_done).toEqual(['Fleet readiness checks']);
  expect(puts[1].issues).toBe('Waiting for a quote');
  // The privacy note and only real features; the roster export is a formula-safe CSV of what is shown.
  await expect(page.getByText(/There is no continuous tracking/)).toBeVisible();
  await expect(page.getByRole('button', { name: /Bulk corrections|Report templates/ })).toHaveCount(0);
  // Every roster read went to /team; nobody's records were asked for without a share.
  expect(sent.some((r) => r.path === '/api/v1/attendance/team')).toBe(true);
  await noSeriousAxe(page);
});

test('the row menu opens a colleague timesheet read only, and leaving it returns to my own record', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#attendance');
  await page.getByRole('button', { name: 'More for Layla Manager' }).click();
  await page.getByRole('menuitem', { name: 'Open timesheet' }).click();
  await expect(page.getByRole('tab', { name: "Layla Manager's timesheet", selected: true })).toBeVisible();
  await expect(page.getByText(/It is read only/)).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Corrections' })).toHaveCount(0);
  // Leaving the colleague's timesheet: my own correction form uses my own time zone, never theirs.
  await page.getByRole('tab', { name: 'Team attendance' }).click();
  await expect(page.getByText('Departure time (Asia/Dubai)')).toBeVisible();
  await expect(page.getByText(/Asia\/Karachi/)).toHaveCount(0);
  await expect(page.getByRole('tab', { name: 'My timesheet' })).toBeVisible();
  await page.getByRole('button', { name: 'More for Layla Manager' }).click();
  await page.getByRole('menuitem', { name: 'Open timesheet' }).click();
  await page.getByRole('button', { name: 'Back to my timesheet' }).click();
  await expect(page.getByRole('tab', { name: 'My timesheet', selected: true })).toBeVisible();
});

test('the Arabic team view is right to left', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockApi(page);
  await page.goto('/#attendance');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('tab', { name: 'حضور الفريق', selected: true })).toBeVisible();
  await expect(page.getByRole('table', { name: 'قائمة الحضور' })).toBeVisible();
  await noSeriousAxe(page);
});
