import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockBooks } from './support/businessBooksApi';
import { mockVault } from './support/vaultApi';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Home, Operations overview (docs/home.md#operations-overview): the business month from the books, the fleet and its
// kilometres, the Attention center, fleet status, attendance, document expiry and purchases, each from its own module's
// API; a refused or failing module shows its own state while the rest loads; My day keeps the person's day; Arabic.
async function mockAll(page: Page, { books = 'ok', vehicles = 'ok' }: { books?: 'ok' | 'denied'; vehicles?: 'ok' | 'fail' } = {}) {
  await mockWorkspaceApi(page);
  await mockBooks(page);
  await mockVault(page);
  if (books === 'denied') {
    await page.route('http://localhost:8000/api/v1/business/books/**', (route) => route.fulfill({ status: 403, contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'forbidden', message: 'Only owners and members read the business books.', details: [] } }) }));
  }
  if (vehicles === 'fail') {
    await page.route('http://localhost:8000/api/v1/vehicles/overview**', (route) => route.fulfill({ status: 503, contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'unavailable', message: 'The database is not answering.', details: [] } }) }));
  }
}

test('the overview shows the month, the fleet, what needs attention, attendance, documents and purchases', async ({ page }) => {
  await mockAll(page);
  await page.goto('/#home');
  await expect(page.getByRole('heading', { level: 1, name: 'Operations overview' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true');
  const kpis = page.getByRole('region', { name: 'This month at a glance' });
  await expect(kpis).toContainText('SAR 21,900.00');
  await expect(kpis).toContainText('2 vehicles');
  await expect(kpis).toContainText('300 km');
  await expect(page.getByRole('list', { name: 'Spend by category' })).toContainText('Office rent');
  const attention = page.getByRole('region', { name: 'Attention center' });
  await expect(attention.getByRole('link', { name: /1 approval waiting/ })).toHaveAttribute('href', '#approvals');
  await expect(attention.getByRole('link', { name: /1 bill is overdue/ })).toHaveAttribute('href', '#business-books/bills');
  const fleet = page.getByRole('region', { name: 'Fleet status' });
  await expect(fleet.getByRole('row', { name: /ABC 1234/ })).toContainText('Due in 10 days');
  await expect(fleet.getByRole('row', { name: /ABC 1234/ })).toContainText('10.0 km/L');
  await expect(page.getByRole('region', { name: 'Document expiry' })).toContainText('Next expiry');
  await expect(page.getByRole('region', { name: 'Purchases and delivery' })).toContainText('On the way');
  await expect(page.getByRole('region', { name: 'Attendance' })).toContainText('This week');
  await expect(page.getByRole('region', { name: 'Recent activity' })).toBeVisible();
  // My day keeps the person's day; the tab is a link of its own.
  await page.getByRole('tab', { name: /My day/ }).click();
  await expect(page).toHaveURL(/#home-day$/);
  await expect(page.getByRole('region', { name: 'Today at a glance' })).toBeVisible();
  await page.getByRole('tab', { name: 'Overview' }).click();
  await attention.getByRole('link', { name: /1 bill is overdue/ }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Expenses and P&L' })).toBeVisible();
});

test('a refused module says who may see it and a failing one offers a retry, while the rest loads', async ({ page }) => {
  await mockAll(page, { books: 'denied', vehicles: 'fail' });
  await page.goto('/#home');
  await expect(page.getByRole('region', { name: 'Income vs. expenses' })).toContainText('The business books are for owners and members with Business access.');
  const fleet = page.getByRole('region', { name: 'Fleet status' });
  await expect(fleet).toContainText('Could not load this part: The database is not answering.');
  await expect(fleet.getByRole('button', { name: 'Try again' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Purchases and delivery' })).toContainText('On the way');
});

test('Arabic reads right to left with no serious accessibility issue', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockAll(page);
  await page.goto('/#home');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { level: 1, name: 'نظرة عامة على العمليات' })).toBeVisible();
  await expect(page.locator('.opsHome .opsShares li').first()).toBeVisible();
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 3).join(', ')}`)).toEqual([]);
});

test('a slow reply for a month the person has left never replaces the month on screen', async ({ page }) => {
  await mockAll(page);
  const now = new Date();
  const previous = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  const before = `${previous.getFullYear()}-${String(previous.getMonth() + 1).padStart(2, '0')}-01`;
  // The month before answers late, and with a revenue the current month never has.
  await page.route('http://localhost:8000/api/v1/business/books/report**', async (route) => {
    if (new URL(route.request().url()).searchParams.get('from') !== before) return route.fallback();
    const zero = { lines: [], total: '0.00' };
    const body = { period: { from: before, to: before, basis: 'accrual' }, base_currency: 'SAR',
      pnl: { currency: 'SAR', revenue: { lines: [{ key: 'service', amount: '99999.00' }], total: '99999.00' }, cost_of_sales: zero, gross_profit: '99999.00', gross_margin_pct: '100.0',
        operating_expenses: zero, operating_profit: '99999.00', other_income: zero, finance_costs: zero, fx_differences: '0.00', net_profit: '99999.00', net_margin_pct: '100.0',
        not_converted: [], complete: true },
      tax: { label: 'VAT', country: 'SA', by_currency: [], allocations_without_tax_split: 0 }, cash_flow: { lines: {}, net: [], paid_by_members: {} },
      balances: { as_of: before, aged_on: before, receivables: [], outstanding_invoices: [], outstanding_invoices_count: 0, payables: [], unpaid_bills: [], unpaid_bills_count: 0,
        owed_to_members: [], debt: [], intercompany: [] }, equity: { contributions: [], drawings: [] }, trend: [], customers: [], projects: [], cost_centres: [], budgets: [],
      recurring: { items: [], per_month: [], upcoming: [] }, allocations_in_review: 0, conversions: [], notes: [], truncated: false };
    await new Promise((resolve) => setTimeout(resolve, 2_500));
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }).catch(() => undefined);
  });
  await page.goto('/#home');
  const kpis = page.getByRole('region', { name: 'This month at a glance' });
  await expect(kpis).toContainText('SAR 21,900.00');
  await page.getByRole('button', { name: 'Previous month' }).click();
  await page.getByRole('button', { name: 'Next month' }).click();
  await expect(kpis).toContainText('SAR 21,900.00');
  // Long after the old month's reply has arrived, the current month is still the one shown.
  await page.waitForTimeout(3_500);
  await expect(kpis).toContainText('SAR 21,900.00');
  await expect(kpis).not.toContainText('99,999.00');
});

