import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { useSavedLocale } from './support/workspaceApi';

// Where the model spend goes (docs/model-gateway.md#spend-breakdown): grouping, scope, period,
// measure, the chart and table, Load more, the CSV export, the recorded-since note, the empty
// state, the error state and the owner's alert percent on the budget card.
const now = '2026-10-18T10:00:00.000Z';
type Json = Record<string, unknown>;
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });

const row = (key: string, calls: number, cost: number, label: string | null = null, unpriced = 0): Json =>
  ({ key, label, calls, input_tokens: calls * 300, output_tokens: calls * 200, total_tokens: calls * 500, estimated_cost_usd: cost, unpriced_calls: unpriced });

async function mockApi(page: Page, options: { fail?: boolean; empty?: boolean } = {}) {
  const calls: { method: string; path: string; query: Record<string, string>; body: unknown }[] = [];
  let budgets: Json = {
    period_start: '2026-10-01', resets_on: '2026-11-01', can_manage_tenant_budget: false,
    tenant: { budget: null, usage: { input_tokens: 1200, output_tokens: 800, estimated_cost_usd: 1.5, calls: 4, unpriced_calls: 0 }, total_tokens: 2000, exceeded: false },
    workspace: { budget: { scope: 'workspace', monthly_cost_limit_usd: 20, monthly_token_limit: null, alert_percent: 30, updated_at: now, updated_by: 'user_local' }, usage: { input_tokens: 1200, output_tokens: 800, estimated_cost_usd: 1.5, calls: 4, unpriced_calls: 0 }, total_tokens: 2000, exceeded: false },
  };
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname, method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    calls.push({ method, path, query: Object.fromEntries(url.searchParams), body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals') return json([]);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'owner', active: true, created_at: now, updated_at: now });
    if (path === '/api/v1/agent-meetings/budget') return json({ limit_usd: 50, default_limit_usd: 50, is_default: true, used_usd: 1, remaining_usd: 49, percent: 2, turns: 1, exceeded: false, period_start: '2026-10-01', resets_on: '2026-11-01', updated_by: null, updated_at: null, message: null, can_manage: true });
    if (path === '/api/v1/model-budgets' && method === 'GET') return json(budgets);
    if (path === '/api/v1/model-budgets/workspace' && method === 'PUT') {
      budgets = { ...budgets, workspace: { ...(budgets.workspace as Json), budget: { scope: 'workspace', ...(body as Json), updated_at: now, updated_by: 'user_local' } } };
      return json(budgets);
    }
    if (path === '/api/v1/model-budgets/usage') {
      if (options.fail) return json(envelope('internal_error', 'The database is not reachable'), 500);
      const group = url.searchParams.get('group_by') ?? 'day', scope = url.searchParams.get('scope') ?? 'workspace';
      const offset = Number(url.searchParams.get('offset') ?? 0);
      if (url.searchParams.get('format') === 'csv') return route.fulfill({ status: 200, contentType: 'text/csv; charset=utf-8', headers: { 'Content-Disposition': 'attachment; filename="usage.csv"' }, body: `${group},label,calls\n` });
      let rows: Json[] = [], count = 0, recorded: string | null = null;
      if (!options.empty) {
        if (group === 'day') { rows = [row('2026-10-03', 2, 0.9), row('2026-10-04', 2, 0.6, null, 1)]; recorded = '2026-10-02'; }
        else if (group === 'month') rows = [row('2026-09-01', 7, 3.1), row('2026-10-01', 4, 1.5)];
        else if (group === 'agent') rows = [row('assistant', 3, 1.1, 'ANUM assistant'), row('agent_fleet', 1, 0.4, 'Fleet planner')];
        else if (group === 'user') rows = scope === 'tenant' ? [row('user_local', 4, 1.5), row('user_other', 2, 0.7)] : [row('user_local', 4, 1.5)];
        else { const all = [[row('tasks', 2, 0.8)], [row('voice', 1, 0.4)], [row('email', 1, 0.3)]]; rows = all[Math.min(2, offset)] ?? []; }
        count = group === 'feature' ? 3 : rows.length;
      }
      return json({ scope, group_by: group, from_day: url.searchParams.get('from'), to_day: url.searchParams.get('to'), rows, total: { input_tokens: 1200, output_tokens: 800, estimated_cost_usd: 1.5, calls: 4, unpriced_calls: 1 }, count, limit: 50, offset, truncated: offset + rows.length < count, recorded_since: recorded });
    }
    return route.abort();
  });
  return calls;
}

test('the spend breakdown groups, pages, exports and keeps the owner alert percent', async ({ page }) => {
  test.setTimeout(90_000);
  const calls = await mockApi(page);
  await page.goto('/#budgets');
  const spend = page.getByRole('region', { name: 'Spend breakdown' });
  await expect(spend.getByRole('heading', { name: 'Where the spend goes' })).toBeVisible();
  // This month by day, as the API recorded it: two days, the total row, the recorded-since note.
  await expect(spend.getByRole('img', { name: 'Estimated cost by Day' })).toBeVisible();
  await expect(spend.getByRole('rowheader', { name: /Oct 3, 2026/ })).toBeVisible();
  await expect(spend.getByRole('row', { name: /^Total/ })).toContainText('$1.50');
  await expect(spend.getByText(/Breakdowns by day, person, agent and feature exist since Oct 2, 2026/)).toBeVisible();
  const first = calls.find((c) => c.path === '/api/v1/model-budgets/usage');
  expect(first?.query.group_by).toBe('day');
  expect(first?.query.scope).toBe('workspace');
  expect(first?.query.from).toMatch(/^\d{4}-\d{2}-01$/);

  // Agents come with their names and ranked bars; the organization scope changes the request.
  await spend.getByRole('radio', { name: 'Agent' }).check();
  await expect(spend.getByRole('rowheader', { name: /Fleet planner/ })).toBeVisible();
  await expect(spend.getByRole('list', { name: 'Estimated cost by Agent' }).getByText('73% of the period')).toBeVisible();
  await spend.getByLabel('Scope').selectOption('tenant');
  await spend.getByRole('radio', { name: 'Person' }).check();
  await expect(spend.getByRole('rowheader', { name: 'user_local (you)' })).toBeVisible();
  await expect(spend.getByRole('rowheader', { name: 'user_other' })).toBeVisible();
  expect(calls.filter((c) => c.path === '/api/v1/model-budgets/usage').at(-1)?.query.scope).toBe('tenant');

  // Tokens as the measure, the last twelve months by month.
  await spend.getByLabel('Measure').selectOption('tokens');
  await spend.getByRole('radio', { name: 'Month' }).check();
  await expect(spend.getByRole('img', { name: 'Tokens by Month' })).toBeVisible();
  await expect(spend.getByRole('rowheader', { name: 'September 2026' })).toBeVisible();
  await spend.getByLabel('Period').selectOption('12m');
  expect(calls.filter((c) => c.path === '/api/v1/model-budgets/usage').at(-1)?.query.from).toMatch(/-01$/);

  // Features page: one group per page, Load more until the count is reached.
  await spend.getByRole('radio', { name: 'Feature' }).check();
  await expect(spend.getByText('Showing 1 of 3 groups.')).toBeVisible();
  await spend.getByRole('button', { name: 'Load more' }).click();
  await expect(spend.getByText('Showing 2 of 3 groups.')).toBeVisible();
  await expect(spend.getByRole('rowheader', { name: 'Voice' })).toBeVisible();
  await spend.getByRole('button', { name: 'Load more' }).click();
  await expect(spend.getByText('Showing 3 of 3 groups.')).toBeVisible();
  await expect(spend.getByRole('button', { name: 'Load more' })).toHaveCount(0);

  // Custom dates are checked before anything is asked.
  await spend.getByLabel('Period').selectOption('custom');
  await spend.getByLabel('From', { exact: true }).fill('2026-10-20');
  await spend.getByLabel('To', { exact: true }).fill('2026-10-01');
  await expect(spend.getByRole('alert')).toContainText('The end date must not be before the start date');
  await spend.getByLabel('To', { exact: true }).fill('2026-10-25');
  await expect(spend.getByRole('alert')).toHaveCount(0);

  // The CSV export asks for the whole range.
  const download = page.waitForEvent('download');
  await spend.getByRole('button', { name: 'Export CSV' }).click();
  expect((await download).suggestedFilename()).toMatch(/^model-usage-tenant-feature-2026-10-20-2026-10-25\.csv$/);
  await expect(spend.getByText('The CSV download started.')).toBeVisible();
  const csv = calls.find((c) => c.path === '/api/v1/model-budgets/usage' && c.query.format === 'csv');
  expect(csv?.query).toMatchObject({ group_by: 'feature', scope: 'tenant', from: '2026-10-20', to: '2026-10-25' });

  // The budget card carries the owner's alert percent and saves it with the limits.
  const workspace = page.getByRole('region', { name: 'This workspace budget' });
  await expect(workspace.getByText(/Owners are also alerted at 30%/)).toBeVisible();
  await expect(workspace.getByLabel('Alert owners at (%)')).toHaveValue('30');
  await workspace.getByLabel('Alert owners at (%)').fill('150');
  await workspace.getByRole('button', { name: 'Save limits' }).click();
  await expect(workspace.getByText(/alert threshold must be a whole number from 1 to 99/)).toBeVisible();
  await workspace.getByLabel('Alert owners at (%)').fill('45');
  await workspace.getByRole('button', { name: 'Save limits' }).click();
  await expect(workspace.getByText('Budget saved.')).toBeVisible();
  expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ monthly_cost_limit_usd: 20, monthly_token_limit: null, alert_percent: 45 });
});

test('an empty period and a failed load each say what happened', async ({ page }) => {
  await mockApi(page, { empty: true });
  await page.goto('/#budgets');
  const spend = page.getByRole('region', { name: 'Spend breakdown' });
  await expect(spend.getByRole('heading', { name: 'No model calls in this period' })).toBeVisible();
  await expect(spend.getByText(/Pick a longer period or the whole organization/)).toBeVisible();
});

test('a failed breakdown shows the API answer with Retry', async ({ page }) => {
  await mockApi(page, { fail: true });
  await page.goto('/#budgets');
  const spend = page.getByRole('region', { name: 'Spend breakdown' });
  await expect(spend.getByRole('alert')).toContainText('The spend breakdown could not be loaded.');
  await expect(spend.getByRole('button', { name: 'Retry' })).toBeVisible();
});

for (const locale of ['en', 'ar'] as const) {
  for (const size of [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 }]) {
    test(`the breakdown fits at ${size.width}x${size.height} in ${locale} with no serious axe finding`, async ({ page }, testInfo) => {
      await page.setViewportSize(size);
      await useSavedLocale(page, locale);
      await mockApi(page);
      await page.goto('/#budgets');
      const spend = page.getByRole('region', { name: locale === 'ar' ? 'تفصيل الإنفاق' : 'Spend breakdown' });
      await expect(spend.getByRole('row', { name: locale === 'ar' ? /^المجموع/ : /^Total/ })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 'overflow').toBeLessThanOrEqual(0);
      const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
      expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
      if (process.env.ANUM_SCREENSHOTS) await page.screenshot({ path: testInfo.outputPath(`budgets-${locale}-${size.width}.png`), fullPage: true });
    });
  }
}
