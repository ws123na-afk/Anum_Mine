import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { useSavedLocale } from './support/workspaceApi';

// The Finance shell, Finance reports and Safe to spend (docs/finance.md#finance-shell, #reports,
// #safe-to-spend): the sections under Finance in the sidebar, the report views from the month
// summaries and the business books, and the transparent safe-to-spend build. Synthetic values only.
const now = new Date();
const pad = (n: number) => String(n).padStart(2, '0');
const month = `${now.getFullYear()}-${pad(now.getMonth() + 1)}`;

function booksReport(from: string) {
  const current = from.startsWith(month);
  const amount = (a: string, b: string) => (current ? a : b);
  return {
    period: { from, to: from, basis: 'accrual' }, base_currency: 'AED',
    pnl: {
      currency: 'AED', revenue: { lines: [{ key: 'service', amount: amount('31280.00', '29860.00') }], total: amount('31280.00', '29860.00') },
      cost_of_sales: { lines: [{ key: 'vehicles', amount: amount('7140.00', '7760.00') }], total: amount('7140.00', '7760.00') }, gross_profit: amount('24140.00', '22100.00'), gross_margin_pct: '77.2',
      operating_expenses: { lines: [{ key: 'office_rent', amount: amount('2500.00', '2500.00') }, { key: 'utilities', amount: amount('1420.00', '1420.00') }], total: amount('3920.00', '3920.00') },
      operating_profit: amount('20220.00', '18180.00'), other_income: { lines: [], total: '0.00' }, finance_costs: { lines: [], total: '0.00' }, fx_differences: '0.00',
      net_profit: amount('20220.00', '18180.00'), net_margin_pct: '64.6', not_converted: [], complete: true,
    },
    tax: { label: 'VAT', country: 'AE', by_currency: [{ currency: 'AED', output: '1564.00', input: '357.00', net: '1207.00' }], allocations_without_tax_split: 1 },
    cash_flow: { lines: { customers_paid: [{ currency: 'AED', amount: '28000.00' }], suppliers_paid: [{ currency: 'AED', amount: '-9100.00' }] }, net: [{ currency: 'AED', amount: '18900.00' }], paid_by_members: {} },
    balances: { as_of: from, aged_on: from, receivables: [{ currency: 'AED', total: '5200.00' }], outstanding_invoices: [], outstanding_invoices_count: 0, payables: [], unpaid_bills: [], unpaid_bills_count: 0, owed_to_members: [], debt: [], intercompany: [] },
    equity: { contributions: [], drawings: [] },
    trend: Array.from({ length: 6 }, (_, i) => { const d = new Date(now.getFullYear(), now.getMonth() - 5 + i, 1); return { month: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-01`, revenue: String(27000 + i * 800), costs: String(16000 + i * 400), profit: String(11000 + i * 400), unconverted: false }; }),
    customers: [], projects: [], cost_centres: [],
    budgets: [{ id: 'bud_1', name: 'Vehicles', period_start: `${month}-01`, period_end: `${month}-28`, currency: 'AED', budget: '8000.00', actual: '7140.00', variance: '860.00', used_pct: '89.3', comparable: true }],
    recurring: { items: [], per_month: [], upcoming: [] }, allocations_in_review: 2, conversions: [], notes: [], truncated: false,
  };
}

async function mockApi(page: Page, { booksDenied = false, empty = false }: { booksDenied?: boolean; empty?: boolean } = {}) {
  const state = financeState({ empty });
  const sent: { method: string; path: string; search: string }[] = [];
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    sent.push({ method: request.method(), path, search: url.search });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'member', active: true });
    if (path === '/api/v1/workspaces/current') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', archived_at: null, archived_by: null });
    if (path === '/api/v1/me/workspace-memberships') return json([{ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', role: 'member', active: true }]);
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals' || path === '/api/v1/conversations') return json([]);
    if (path === '/api/v1/business/books/report') {
      if (booksDenied) return json({ error: { code: 'forbidden', message: 'Business is for workspace owners and members.', correlation_id: 'c', details: [] } }, 403);
      return json(booksReport(url.searchParams.get('from') ?? `${month}-01`));
    }
    if (path === '/api/v1/business/books/report.csv') return route.fulfill({ status: 200, contentType: 'text/csv', body: 'section,line,currency,amount\n' });
    const body = request.postData() ? request.postDataJSON() : null;
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { state, sent };
}

async function shot(page: Page, name: string) {
  const dir = process.env.FIN_SHOTS;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
}

test('the Finance sections sit under Finance in the sidebar, the current one marked, with Business Expenses and P&L', async ({ page, isMobile }) => {
  await mockApi(page);
  await page.goto('/#finance-reports');
  const sections = page.getByRole('navigation', { name: 'Finance sections' });
  await expect(sections.getByRole('button', { name: 'Reports' })).toHaveAttribute('aria-current', 'page');
  if (!isMobile) await expect(page.getByRole('navigation', { name: 'Primary' }).getByRole('navigation', { name: 'Finance sections' })).toBeVisible();
  await sections.getByRole('button', { name: 'Safe to spend' }).click();
  await expect(page).toHaveURL(/#finance-safe$/);
  await expect(sections.getByRole('button', { name: 'Safe to spend' })).toHaveAttribute('aria-current', 'page');
  await expect(sections.getByRole('link', { name: /Business Expenses and P&L/ })).toHaveAttribute('href', '#business-books');
});

test('reports: profit and loss from the month summaries and the books, every view, and the real CSV downloads', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-reports');
  await expect(page.getByRole('heading', { level: 1, name: 'Finance reports' })).toBeVisible();
  const kpis = page.getByRole('region', { name: /Figures for/ });
  for (const label of ['Income', 'Expenses', 'Net result', 'Savings rate']) await expect(kpis).toContainText(label);
  await expect(kpis).toContainText('AED');
  await expect(page.getByRole('img', { name: 'Income, expenses and net result' })).toBeVisible();
  const expenses = page.getByRole('region', { name: /Categorized expenses/ });
  await expect(expenses.getByRole('table')).toContainText('Groceries');
  await expect(expenses.getByRole('table')).toContainText('Total expenses');
  const business = page.getByRole('region', { name: /Business P&L/ });
  await expect(business).toContainText('31,280.00');
  await expect(business.getByRole('link', { name: 'Open Business Expenses and P&L' })).toHaveAttribute('href', '#business-books');
  await expect(page.getByRole('region', { name: 'Report quality' })).toContainText('categorized');
  await expect(page.getByRole('region', { name: /Business and personal/ })).toContainText('Personal');
  await shot(page, 'reports-pnl');

  // Business scope reads the books: operating margin and the books' P&L CSV.
  await page.getByRole('radio', { name: 'Business' }).click();
  await expect(kpis).toContainText('Operating margin');
  await expect(kpis).toContainText('64.6%');
  await page.getByRole('button', { name: 'Download P&L CSV' }).click();
  await expect.poll(() => sent.some((r) => r.path === '/api/v1/business/books/report.csv' && r.search.includes(`from=${month}-01`))).toBe(true);

  await page.getByRole('tab', { name: 'Tax summary' }).click();
  await expect(page.getByRole('tabpanel')).toContainText('VAT');
  await expect(page.getByRole('tabpanel')).toContainText('1,207.00');
  await page.getByRole('radio', { name: 'Personal' }).click();
  await expect(page.getByRole('heading', { name: 'No tax figures for personal finance' })).toBeVisible();
  await page.getByRole('button', { name: 'Show the business tax summary' }).click();
  await expect(page.getByRole('radio', { name: 'Business' })).toHaveAttribute('aria-checked', 'true');
  await page.getByRole('radio', { name: 'Personal' }).click();

  // The arrow keys move between the views.
  await page.getByRole('tab', { name: 'Tax summary' }).focus();
  await page.keyboard.press('ArrowLeft');
  await expect(page.getByRole('tab', { name: 'Net worth' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('tabpanel')).toContainText('Net worth by currency');
  await page.getByRole('tab', { name: 'Cash flow' }).click();
  await expect(page.getByRole('tabpanel')).toContainText('Money in');
  await page.getByRole('tab', { name: 'Spending' }).click();
  await expect(page.getByRole('tabpanel')).toContainText('Spending by category');

  await page.getByRole('tab', { name: 'Custom' }).click();
  await expect(page.getByRole('status').filter({ hasText: /matching transaction/ })).toBeVisible();
  await page.getByLabel('Category').selectOption('groceries');
  await page.getByRole('tabpanel').getByRole('button', { name: 'Download CSV' }).click();
  await expect.poll(() => sent.some((r) => r.path === '/api/v1/finance/transactions/export.csv' && r.search.includes('category=groceries') && r.search.includes('from='))).toBe(true);
});

test('reports: the period picker moves months and stops at this month', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#finance-reports');
  await expect(page.getByRole('button', { name: 'Next month' })).toBeDisabled();
  await page.getByRole('button', { name: 'Previous month' }).click();
  await expect(page.getByRole('button', { name: 'Next month' })).toBeEnabled();
  await expect(page.getByRole('region', { name: /Figures for/ })).toBeVisible();
});

test('reports: without access to the books, Business says why and the personal report still works', async ({ page }) => {
  await mockApi(page, { booksDenied: true });
  await page.goto('/#finance-reports');
  await expect(page.getByRole('radio', { name: /Business\. Your role cannot read the business books\./ })).toBeDisabled();
  await expect(page.getByRole('region', { name: /Business P&L/ })).toContainText('Workspace owners and members with business access');
  await expect(page.getByRole('region', { name: /Figures for/ })).toContainText('Income');
});

test('reports: an empty ledger is led to import a statement', async ({ page }) => {
  await mockApi(page, { empty: true });
  await page.goto('/#finance-reports');
  await expect(page.getByText(/Nothing recorded for/)).toBeVisible();
  await page.getByRole('button', { name: 'Import a statement' }).click();
  await expect(page).toHaveURL(/#finance-import$/);
});

test('safe to spend: the transparent calculation, the runway, commitments, guardrails and the Guardian preview', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#finance-safe');
  await expect(page.getByRole('heading', { level: 1, name: 'Safe to spend' })).toBeVisible();
  await expect(page.getByText('This is an estimate, not financial advice or a guarantee.')).toBeVisible();
  const calc = page.getByRole('region', { name: 'Transparent calculation' });
  for (const text of ['Account balance', 'Bills and payments', 'Goals and buffer', 'Estimated remaining']) await expect(calc).toContainText(text);
  await expect(page.getByRole('img', { name: 'Estimated balance by day in AED' })).toBeVisible();
  await expect(page.getByText(/Daily spending pace/)).toBeVisible();
  await expect(page.getByRole('region', { name: 'Upcoming commitments' })).toContainText('Rent');
  const guard = page.getByRole('region', { name: /Spending guardrails/ });
  await expect(guard).toContainText('Dining');
  await expect(guard.getByRole('meter')).toHaveAttribute('aria-label', /Dining: .*% of the limit used/);
  await expect(page.getByText(/Records are up to date|Estimate may be stale/)).toBeVisible();
  await expect(page.getByRole('region', { name: /Guardian rules/ })).toContainText('casino');
  await shot(page, 'safe-now');
  await page.getByRole('region', { name: /Guardian rules/ }).getByRole('link', { name: 'Open Guardian' }).click();
  await expect(page).toHaveURL(/#finance-guardian$/);
});

test('safe to spend: business scope shows the business budgets and says the estimate is personal', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#finance-safe');
  await page.getByRole('radio', { name: 'Business' }).click();
  await expect(page.getByText('Safe to spend covers your personal accounts')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Business budgets' })).toContainText('Vehicles');
  await page.getByRole('tab', { name: 'Rules' }).click();
  await expect(page.getByRole('tabpanel')).toContainText('casino');
});

test('reports and safe to spend read in Arabic, right to left', async ({ page }) => {
  await mockApi(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#finance-reports');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { level: 1, name: 'تقارير المالية' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'الأرباح والخسائر' })).toHaveAttribute('aria-selected', 'true');
  await shot(page, 'reports-ar');
  await page.getByRole('navigation', { name: 'أقسام المالية' }).getByRole('button', { name: 'المتاح للصرف' }).click();
  await expect(page.getByRole('region', { name: 'الحساب بوضوح' })).toBeVisible();
  await shot(page, 'safe-ar');
});

const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;

for (const locale of ['en', 'ar'] as const) {
  test(`@gate reports and safe to spend fit every size without sideways scrolling (${locale})`, async ({ page }) => {
    await mockApi(page);
    if (locale === 'ar') await useSavedLocale(page, 'ar');
    for (const hash of ['finance-reports', 'finance-safe']) {
      for (const [width, height] of SIZES) {
        await page.setViewportSize({ width, height });
        await page.goto(`/#${hash}`);
        await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
        await expect(page.locator('.finKpis')).toBeVisible();
        await expect(page.locator('figure.finFigure svg')).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), `${hash} at ${width}x${height}`).toBeLessThanOrEqual(0);
        await shot(page, `${hash}-${locale}-${width}`);
      }
    }
  });
}
