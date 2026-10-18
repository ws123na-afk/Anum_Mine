import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { equityBody, type EquityOptions } from './support/financeEquity';
import { useSavedLocale } from './support/workspaceApi';

// SIMULATED P&L and drawdown over time in the Trading intelligence tab
// (docs/finance-trading-intel.md#pl-and-drawdown-over-time). Read only. Charts, the table fallback with
// paging, ranges, currencies kept apart, shadow portfolios, loading, empty with a next action, error
// with a retry, permission denied, Arabic and the responsive gate. The API is mocked at
// http://localhost:8000/** like the other specs.
const stamp = '2026-10-15T08:00:00Z';
const NO_RULES = { allowed_currencies: [], max_single: [], max_daily_total: [], category_caps: [], blocked_categories: [], blocked_merchants: [], approval_above: [], quiet_hours: null, cooling_off: null };

interface Options { equity?: EquityOptions; shadow?: boolean; failFirst?: boolean; denied?: boolean; slow?: boolean }

function book(id: string, name: string, mode: 'paper' | 'shadow') {
  return {
    id, name, mode, cash: mode === 'paper' ? [{ currency: 'USD', amount: '8998.50' }] : [], realised: [], unrealised: [{ currency: 'USD', amount: '49.50' }],
    fees: [{ currency: 'USD', amount: '1.00' }], slippage: [{ currency: 'USD', amount: '0.50' }], holdings: [], holdings_total: 0, unpriced: [], waiting: 0, simulated: true,
  };
}

async function mockApi(page: Page, o: Options = {}) {
  const state = financeState({});
  const asked: string[] = [];
  let failures = o.failFirst ? 1 : 0;
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
        switches: { person_engaged: false, global_engaged: false, can_change_global: false }, venues: [],
        portfolios: o.shadow ? [book('fin_pf_shadow', 'Shadow book', 'shadow')] : [book('fin_pf_1', 'Practice', 'paper')],
        risk: { version: 0, rules: NO_RULES, paper_always_needs_approval: true, read_only: true, note: '' },
        strategies: (['DRAFT', 'BACKTESTING', 'VALIDATION', 'PAPER_ACTIVE', 'PAPER_SUSPENDED', 'RETIRED'] as const).map((s) => ({ state: s, count: 0 })),
        pending_requests: 0, agent_requests: 0, note: 'SIMULATED.', generated_at: stamp,
      });
    }
    if (path.endsWith('/intel/strategies') || path.endsWith('/intel/decisions')) return json({ items: [], total: 0, limit: 10, offset: 0 });
    const equity = /\/intel\/portfolios\/([^/]+)\/equity$/.exec(path);
    if (equity) {
      if (request.method() !== 'GET') return json({}, 405);
      asked.push(url.search);
      if (o.slow) await new Promise((resolve) => setTimeout(resolve, 700));
      if (o.denied) return json({ error: { code: 'forbidden', message: 'Your role is viewer. Ask a workspace owner to change your role to member to use finance.', correlation_id: 'c', details: [] } }, 403);
      if (failures > 0) { failures -= 1; return json({ error: { code: 'internal_error', message: 'The database is not reachable.', correlation_id: 'c', details: [] } }, 503); }
      return json(equityBody(equity[1], Number(url.searchParams.get('days')), { mode: o.shadow ? 'shadow' : 'paper', ...o.equity }));
    }
    const body = request.postData() ? request.postDataJSON() : null;
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { asked };
}

const region = (page: Page) => page.getByRole('region', { name: 'P&L and drawdown over time' });

test('a paper portfolio shows its simulated P&L and drawdown from the stored history, with a paged table', async ({ page }) => {
  const { asked } = await mockApi(page);
  await page.goto('/#finance-intel');
  const charts = region(page);
  await expect(charts.getByRole('img', { name: /Simulated P&L in USD from 2026-09-06 to 2026-10-15/ })).toBeVisible();
  await expect(charts.getByRole('img', { name: /Drawdown from the running high/ })).toBeVisible();
  expect(asked[0]).toBe('?days=90');
  await expect(charts.locator('dt').filter({ hasText: /^Value$/ })).toBeVisible();
  await expect(charts).toContainText('10,269 USD');
  await expect(charts).toContainText('-420 USD (-4.08%), from the high on 2026-09-21 to the low on 2026-10-02');
  await expect(charts.getByRole('note')).toContainText('1 day is a gap');
  await expect(charts).toContainText('3 filled orders');
  await expect(charts).toContainText('not a promise of future results');
  await charts.getByText('Show the figures as a table').click();
  const table = charts.getByRole('table', { name: 'Daily simulated figures of Practice in USD' });
  await expect(table.locator('tbody tr').first()).toContainText('2026-10-15');
  await expect(table.locator('tbody tr')).toHaveCount(31);
  await expect(table).toContainText('Gap');
  await charts.getByRole('button', { name: 'Older' }).click();
  await expect(charts.getByText('Days 32 to 40 of 40')).toBeVisible();
  await expect(charts.getByRole('button', { name: 'Older' })).toBeDisabled();
});

test('the P&L chart compares the portfolio with buy and hold of the same symbols', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#finance-intel');
  const charts = region(page);
  await expect(charts.getByTestId('equity-benchmark-line')).toHaveCount(1);
  await expect(charts.getByText('Buy and hold, same symbols')).toBeVisible();
  await expect(charts.locator('dt').filter({ hasText: /^Buy and hold$/ })).toBeVisible();
  await expect(charts).toContainText('311 USD');
  await expect(charts).toContainText('Buy and hold of EXMPL in equal weights from 2026-09-06');
  await expect(charts.getByRole('img', { name: /Buy and hold of the same symbols, newest 311 USD/ })).toBeVisible();
  await charts.getByText('Show the figures as a table').click();
  const table = charts.getByRole('table', { name: 'Daily simulated figures of Practice in USD' });
  await expect(table.locator('td[data-label="Buy and hold"]').first()).toContainText('311 USD');
});

test('the period and the currency can be changed; currencies are never mixed', async ({ page }) => {
  const { asked } = await mockApi(page, { equity: { currencies: ['AED', 'USD'] } });
  await page.goto('/#finance-intel');
  const charts = region(page);
  await expect(charts.getByRole('img', { name: /in AED/ }).first()).toBeVisible();
  await charts.getByLabel('Currency').selectOption('USD');
  await expect(charts.getByRole('img', { name: /Simulated P&L in USD/ })).toBeVisible();
  await charts.getByLabel('Period').selectOption('30');
  await expect(charts.getByRole('img', { name: /from 2026-09-16 to 2026-10-15/ })).toBeVisible();
  expect(asked).toContain('?days=30');
});

test('a shadow portfolio has no cash: P&L and its drawdown in the currency', async ({ page }) => {
  await mockApi(page, { shadow: true });
  await page.goto('/#finance-intel');
  const charts = region(page);
  await expect(charts.getByRole('img', { name: /Drawdown of P&L from its running high, in USD/ })).toBeVisible();
  await expect(charts.locator('dt').filter({ hasText: /^Value$/ })).toHaveCount(0);
  await expect(charts.getByTestId('equity-benchmark-line')).toHaveCount(0);
  await expect(charts).toContainText('No buy and hold line: a shadow portfolio has no cash to invest.');
});

test('no filled order yet: an empty state with the next step', async ({ page }) => {
  await mockApi(page, { equity: { orders: 0 } });
  await page.goto('/#finance-intel');
  const charts = region(page);
  await expect(charts.getByRole('heading', { name: 'No simulated trades in this run yet' })).toBeVisible();
  await charts.getByRole('button', { name: 'Open paper trading' }).click();
  await expect(page).toHaveURL(/#finance-trading$/);
});

test('loading, then a failed load says what failed and the retry works', async ({ page }) => {
  await mockApi(page, { failFirst: true, slow: true });
  await page.goto('/#finance-intel');
  const charts = region(page);
  await expect(charts.getByText('Loading the simulated history...')).toBeVisible();
  await expect(charts.getByRole('alert')).toContainText('The simulated history could not be loaded: The database is not reachable.');
  await charts.getByRole('button', { name: 'Try again' }).click();
  await expect(charts.getByRole('img', { name: /Simulated P&L in USD/ })).toBeVisible();
});

test('permission denied says who can give access', async ({ page }) => {
  await mockApi(page, { denied: true });
  await page.goto('/#finance-intel');
  await expect(region(page)).toContainText(/owner|admin/i);
});

test('Arabic: labels read right to left, the time axis stays left to right', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockApi(page);
  await page.goto('/#finance-intel');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  const charts = page.getByRole('region', { name: 'الربح والخسارة والتراجع عبر الزمن' });
  await expect(charts.getByRole('img', { name: /الربح والخسارة المحاكى بعملة/ })).toBeVisible();
  await expect(charts.locator('.finEquityChart svg').first()).toHaveAttribute('direction', 'ltr');
});

const VIEWPORTS = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const [width, height] of VIEWPORTS) {
  test(`responsive: the charts fit with no sideways scroll at ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await mockApi(page, { equity: { currencies: ['AED', 'USD'] } });
    await page.goto('/#finance-intel');
    const charts = region(page);
    await expect(charts.getByRole('img').first()).toBeVisible();
    await charts.getByText('Show the figures as a table').click();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    const sticking = await page.locator('.finIntel').evaluate((root) => [...root.querySelectorAll('*')].filter((e) => {
      if (e.getBoundingClientRect().right <= document.documentElement.clientWidth + 1) return false;
      for (let p = e.parentElement; p && p !== root.parentElement; p = p.parentElement) if (getComputedStyle(p).overflowX !== 'visible') return false;
      return true;
    }).map((e) => `${e.tagName}.${e.className}`));
    expect(sticking).toEqual([]);
    for (const control of [charts.getByLabel('Period'), charts.getByLabel('Currency'), charts.getByRole('button', { name: 'Older' })]) {
      const box = await control.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(width + 0.5);
      expect(box!.height).toBeGreaterThanOrEqual(32);
    }
    // Content-driven height: each chart is its drawn height, not stretched.
    const heights = await charts.locator('.finEquityChart svg').evaluateAll((svgs) => svgs.map((s) => s.getBoundingClientRect().height));
    for (const h of heights) expect(h).toBeLessThanOrEqual(200);
    await charts.screenshot({ path: test.info().outputPath(`equity-${width}x${height}.png`) });
  });
}
