import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { useSavedLocale } from './support/workspaceApi';

// The post-close daily trading reports in the Trading intelligence tab (docs/finance-trading-intel.md#daily-report).
// Read only and SIMULATED: the list with paging, the deep link the report notice opens, a report's sections,
// the Markdown download, loading, empty with a next step, errors with a retry, permission denied, Arabic and
// the responsive gate. The API is mocked at http://localhost:8000/** like the other specs.
const stamp = '2026-10-15T08:00:00Z';
const NO_RULES = { allowed_currencies: [], max_single: [], max_daily_total: [], category_caps: [], blocked_categories: [], blocked_merchants: [], approval_above: [], quiet_hours: null, cooling_off: null };

interface Options { reports?: number; failList?: number; failDetail?: number; denied?: boolean; slow?: boolean }

async function mockApi(page: Page, o: Options = {}) {
  const state = financeState({});
  state.reports = o.reports ?? 0;
  let listFailures = o.failList ?? 0, detailFailures = o.failDetail ?? 0;
  const asked: string[] = [];
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'member', active: true });
    if (path === '/api/v1/workspaces/current') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', archived_at: null, archived_by: null });
    if (path === '/api/v1/me/workspace-memberships') return json([{ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', role: 'member', active: true }]);
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals' || path === '/api/v1/conversations') return json([]);
    if (path.endsWith('/intel/overview')) {
      return json({
        simulation: { simulation_only: true, live_enabled: false, wider_live_enabled: false, note: 'Simulation only, no real orders.' },
        switches: { person_engaged: false, global_engaged: false, can_change_global: false }, venues: [], portfolios: [],
        risk: { version: 0, rules: NO_RULES, paper_always_needs_approval: true, read_only: true, note: '' },
        strategies: (['DRAFT', 'BACKTESTING', 'VALIDATION', 'PAPER_ACTIVE', 'PAPER_SUSPENDED', 'RETIRED'] as const).map((s) => ({ state: s, count: 0 })),
        pending_requests: 0, agent_requests: 0, note: 'SIMULATED.', generated_at: stamp,
      });
    }
    if (path.includes('/intel/daily-reports')) {
      if (request.method() !== 'GET') return json({}, 405);
      asked.push(`${path.replace('/api/v1/finance', '')}${url.search}`);
      if (o.slow) await new Promise((resolve) => setTimeout(resolve, 700));
      if (o.denied) return json({ error: { code: 'forbidden', message: 'Your role is viewer. Ask a workspace owner to change your role to member to use finance.', correlation_id: 'c', details: [] } }, 403);
      const one = /\/daily-reports\/[^/]+$/.test(path);
      if (!one && listFailures > 0) { listFailures -= 1; return json({ error: { code: 'internal_error', message: 'The database is not reachable.', correlation_id: 'c', details: [] } }, 503); }
      if (one && detailFailures > 0) { detailFailures -= 1; return json({ error: { code: 'internal_error', message: 'The report store timed out.', correlation_id: 'c', details: [] } }, 503); }
    }
    const body = request.postData() ? request.postDataJSON() : null;
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { asked };
}

const region = (page: Page) => page.getByRole('region', { name: 'Daily reports' });

test('the reports list pages newest first, and a report shows its sections read only', async ({ page }) => {
  const { asked } = await mockApi(page, { reports: 25 });
  await page.goto('/#finance-intel');
  const reports = region(page);
  const list = reports.getByRole('list', { name: 'Daily trading reports, newest first' });
  await expect(list.getByRole('listitem')).toHaveCount(10);
  await expect(list.getByRole('button').first()).toContainText('2026-10-15');
  await expect(list.getByRole('button').first()).toContainText('Needs you');
  await reports.getByRole('button', { name: 'Next' }).click();
  await expect(list.getByRole('button').first()).toContainText('2026-10-05');
  await expect(reports.getByText('11 to 20 of 25')).toBeVisible();
  await reports.getByRole('button', { name: 'Previous' }).click();
  await list.getByRole('button', { name: 'Open the report of 2026-10-15' }).click();
  const report = reports.getByRole('article');
  await expect(report.getByRole('heading', { name: /Report of 2026-10-15/ })).toBeVisible();
  await expect(report).toContainText('no real order was placed');
  await expect(report.getByRole('heading', { name: 'Model commentary' })).toBeVisible();
  await expect(report).toContainText('One order waits for your approval');
  await expect(report).toContainText('Written by an AI model (anthropic/example-model) from this report only. It may be wrong and is not advice.');
  await expect(report.locator('dt').filter({ hasText: 'Orders proposed for approval' })).toBeVisible();
  await expect(report).toContainText('buy 10 EXMPL');
  await expect(report).toContainText('Review the 1 SIMULATED order request(s)');
  await expect(report).toContainText(/Since\W*2026-09-01\W*: \W*\+1\.2%\W* against buy and hold \W*\+1\.8%/);
  await expect(page).toHaveURL(/#finance-intel-report=fin_dec_report_0$/);
  const download = page.waitForEvent('download');
  await report.getByRole('button', { name: 'Download as Markdown' }).click();
  expect((await download).suggestedFilename()).toBe('anum-trading-report-2026-10-15.md');
  await list.getByRole('button', { name: 'Open the report of 2026-10-13' }).click();
  await expect(report).toContainText('Suspended by Performance reviewer');
  await expect(report).toContainText('No model commentary: no AI model is set up for this workspace.');
  await expect(report).toContainText('Drawdown beyond what validation showed');
  await report.getByRole('button', { name: 'Close the report' }).click();
  await expect(reports.getByRole('article')).toHaveCount(0);
  expect(asked.every((a) => a.startsWith('/intel/daily-reports'))).toBe(true);
});

test('the report notice link opens the newest report; a link to one report opens it', async ({ page }) => {
  await mockApi(page, { reports: 3 });
  await page.goto('/#finance-intel-reports');
  const reports = region(page);
  await expect(reports.getByRole('article').getByRole('heading', { name: /Report of 2026-10-15/ })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Trading intelligence', level: 2 })).toBeVisible();
  await page.goto('/#finance-intel-report=fin_dec_report_1');
  await expect(region(page).getByRole('article').getByRole('heading', { name: /Report of 2026-10-14/ })).toBeVisible();
});

test('no report yet: an empty state with the next step', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#finance-intel');
  const reports = region(page);
  await expect(reports.getByRole('heading', { name: 'No daily report yet' })).toBeVisible();
  await expect(reports).toContainText('Run the post-close phase under Trading Supervisor');
});

test('loading, then failed loads say what failed and the retries work', async ({ page }) => {
  await mockApi(page, { reports: 2, failList: 1, failDetail: 1, slow: true });
  await page.goto('/#finance-intel-reports');
  const reports = region(page);
  await expect(reports.getByText('Loading the daily reports...')).toBeVisible();
  await expect(reports.getByRole('alert')).toContainText('The daily reports could not be loaded: The database is not reachable.');
  await reports.getByRole('button', { name: 'Try again' }).click();
  await expect(reports.getByRole('alert')).toContainText('The report could not be loaded: The report store timed out.');
  await reports.getByRole('button', { name: 'Try again' }).click();
  await expect(reports.getByRole('article').getByRole('heading', { name: /Report of 2026-10-15/ })).toBeVisible();
});

test('permission denied says who can give access', async ({ page }) => {
  await mockApi(page, { reports: 2, denied: true });
  await page.goto('/#finance-intel');
  await expect(region(page).getByRole('alert')).toContainText('workspace owner');
});

test('Arabic: the reports read right to left with dates kept left to right', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockApi(page, { reports: 3 });
  await page.goto('/#finance-intel-reports');
  const reports = page.getByRole('region', { name: 'التقارير اليومية' });
  await expect(reports.getByRole('article').getByRole('heading', { name: /تقرير/ })).toBeVisible();
  await expect(reports.getByRole('button', { name: 'نزّل بصيغة Markdown' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
  await expect(reports.locator('.finReportPick strong').first()).toHaveAttribute('dir', 'ltr');
});

const VIEWPORTS = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const [width, height] of VIEWPORTS) {
  test(`responsive: the reports fit with no sideways scroll at ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await mockApi(page, { reports: 25 });
    await page.goto('/#finance-intel-reports');
    const reports = region(page);
    await expect(reports.getByRole('article').getByRole('heading', { name: /Report of/ })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    const sticking = await page.locator('.finIntel').evaluate((root) => [...root.querySelectorAll('*')].filter((e) => {
      if (e.getBoundingClientRect().right <= document.documentElement.clientWidth + 1) return false;
      for (let p = e.parentElement; p && p !== root.parentElement; p = p.parentElement) if (getComputedStyle(p).overflowX !== 'visible') return false;
      return true;
    }).map((e) => `${e.tagName}.${e.className}`));
    expect(sticking).toEqual([]);
    for (const control of [reports.getByRole('button', { name: 'Next' }), reports.getByRole('button', { name: 'Download as Markdown' }), reports.getByRole('button', { name: 'Close the report' })]) {
      const box = await control.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(width + 0.5);
      expect(box!.height).toBeGreaterThanOrEqual(32);
    }
    await reports.screenshot({ path: test.info().outputPath(`reports-${width}x${height}.png`) });
  });
}
