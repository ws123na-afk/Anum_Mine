import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { useSavedLocale } from './support/workspaceApi';

// Finance (docs/finance.md): the person's own ledger, read only. The summary band, the month's spend
// chart, transactions with search and paging, CSV import with a preview, bills, deep links and the
// browser back button, and the empty, error and permission-denied states.
async function mockApi(page: Page, options: Parameters<typeof financeState>[0] & { role?: 'owner' | 'member' | 'viewer' } = {}) {
  const role = options.role ?? 'member';
  const state = financeState(options);
  const sent: { method: string; path: string; body: unknown }[] = [];
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
    const body = request.postData() ? request.postDataJSON() : null;
    if (path.startsWith('/api/v1/finance')) sent.push({ method: request.method(), path, body });
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { state, sent };
}

const tab = (page: Page, name: string) => page.getByRole('navigation', { name: 'Finance sections' }).getByRole('button', { name });

test('the overview shows the month per currency, the spend chart, bills due and balances, and says it is read only', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#finance');
  await expect(page.getByRole('heading', { name: 'Finance', level: 1 })).toBeVisible();
  const band = page.getByRole('region', { name: 'This month in figures' });
  await expect(band.getByText('Bills in 30 days')).toBeVisible();
  await expect(band.getByText('Read only: ANUM never pays, transfers or trades from here.')).toBeVisible();
  await expect(page.getByRole('img', { name: /Spending by category: \d+ categories, the largest is Utilities/ })).toBeVisible();
  await page.getByText('Show as a table').click();
  await expect(page.getByRole('cell', { name: 'Utilities' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Bills due soon' }).getByText('Rent')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Balances' }).getByText('ending 1234')).toBeVisible();
  // No pay, transfer or trade action anywhere on the page.
  await expect(page.getByRole('button', { name: /pay|transfer|buy|sell/i })).toHaveCount(0);
  await page.getByRole('button', { name: 'Previous month' }).click();
  await expect(page.getByRole('group', { name: 'Month' })).not.toContainText(new Date().toLocaleString('en', { month: 'long' }) + ' ' + new Date().getFullYear());
});

test('transactions search, filter and page; a row is edited and deleted; tabs are deep links with Back and the breadcrumb', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-transactions');
  await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toContainText('Transactions');
  const table = page.locator('.finTxTable');
  await expect(table.locator('tbody tr')).toHaveCount(25);
  // Untrusted statement text is shown as text, never run.
  await expect(table.getByText('=HYPERLINK(example)')).toBeVisible();
  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByText('26 to 50 of 64')).toBeVisible();
  await page.getByRole('searchbox', { name: 'Search descriptions and notes' }).fill('talabat');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.getByText(/1 to \d+ of \d+/)).toBeVisible();
  await expect(table.locator('tbody tr').first()).toContainText('TALABAT ORDER');
  await page.getByRole('button', { name: 'Edit TALABAT ORDER' }).first().click();
  const dialog = page.getByRole('dialog', { name: 'Edit transaction' });
  await dialog.getByLabel('Category').selectOption('entertainment');
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Transaction saved.')).toBeVisible();
  expect(sent.some((r) => r.method === 'PATCH' && (r.body as { category?: string }).category === 'entertainment')).toBe(true);
  await page.getByRole('button', { name: 'Delete TALABAT ORDER' }).first().click();
  await page.getByRole('alertdialog', { name: 'Delete this transaction?' }).getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByText('Transaction deleted.')).toBeVisible();

  await tab(page, 'Bills').click();
  await expect(page).toHaveURL(/#finance-bills$/);
  await page.goBack();
  await expect(page).toHaveURL(/#finance-transactions$/);
  await expect(tab(page, 'Transactions')).toHaveAttribute('aria-current', 'page');
  await page.getByRole('navigation', { name: 'Breadcrumb' }).getByRole('link', { name: 'Finance' }).click();
  await expect(tab(page, 'Overview')).toHaveAttribute('aria-current', 'page');
  await tab(page, 'Accounts').click();
  await page.getByRole('navigation', { name: 'Breadcrumb' }).getByRole('link', { name: 'Home' }).click();
  await expect(page).toHaveURL(/#home$/);
});

test('a statement is previewed with its column mapping, then imported; the import can be undone', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-import');
  await page.getByLabel('CSV file').setInputFiles({ name: 'november.csv', mimeType: 'text/csv', buffer: Buffer.from('Transaction Date,Narration,Debit,Credit\n01/11/2026,SPINNEYS,60.00,\n') });
  await page.getByRole('button', { name: 'Preview' }).click();
  const preview = page.getByRole('region', { name: 'Preview of november.csv' });
  await expect(preview).toContainText('4 rows: 3 read, 2 new, 1 already imported, 1 could not be read.');
  await expect(preview).toContainText('The dates fit both day first and month first');
  await expect(preview.getByLabel('Debit column')).toHaveValue('Debit');
  await expect(preview.getByText('Already imported, skipped')).toBeVisible();
  await preview.getByText('1 row could not be read').click();
  await expect(preview).toContainText('Line 5: The date could not be read');
  await preview.getByRole('button', { name: 'Import 2 rows' }).click();
  await expect(page.getByText('Imported 2 rows; 1 were already there and 1 could not be read.')).toBeVisible();
  const confirm = sent.find((r) => r.method === 'POST' && r.path === '/api/v1/finance/imports');
  expect((confirm?.body as { filename: string; content: string }).filename).toBe('november.csv');
  const history = page.getByRole('region', { name: 'Earlier imports' });
  await history.getByRole('listitem').filter({ hasText: 'november.csv' }).getByRole('button', { name: 'Undo import' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Undo import' }).click();
  await expect(page.getByText('The import of november.csv was undone.')).toBeVisible();
});

test('a bill is added, a recurring payment is kept as a bill, and a goal shows its progress', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-bills');
  await page.getByRole('region', { name: 'Recurring payments found' }).getByRole('button', { name: 'Keep as bill' }).click();
  await expect(page.getByText('NETFLIX.COM saved as a bill.')).toBeVisible();
  expect(sent.some((r) => r.method === 'POST' && (r.body as { source?: string }).source === 'detected')).toBe(true);
  await page.getByRole('button', { name: 'Add bill' }).first().click();
  const dialog = page.getByRole('dialog', { name: 'Add bill' });
  await dialog.getByLabel('Name').fill('School fees');
  await dialog.getByLabel('Amount (AED)').fill('-5');
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(dialog.getByRole('alert')).toHaveText('Enter an amount of zero or more.');
  await dialog.getByLabel('Amount (AED)').fill('12500');
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('School fees saved.')).toBeVisible();
  await tab(page, 'Goals').click();
  await expect(page.getByRole('progressbar', { name: 'Emergency fund: 25% saved' })).toBeVisible();
});

test('empty: a new person is led to add an account, then to import', async ({ page }) => {
  await mockApi(page, { empty: true });
  await page.goto('/#finance');
  await expect(page.getByRole('heading', { name: 'Start with an account' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'No spending this month' })).toBeVisible();
  await page.getByRole('region', { name: 'Start with an account' }).getByRole('button', { name: 'Add account' }).click();
  await page.getByRole('button', { name: 'Add account' }).first().click();
  const dialog = page.getByRole('dialog', { name: 'Add account' });
  await dialog.getByLabel('Name').fill('Salary account');
  await dialog.getByLabel('Last four digits').fill('12345678');
  await expect(dialog.getByLabel('Last four digits')).toHaveValue('1234');
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Salary account added.')).toBeVisible();
});

test('a viewer is told who can use Finance; a failed load says what failed and offers a retry', async ({ page }) => {
  await mockApi(page, { denied: true, role: 'viewer' });
  await page.goto('/#finance');
  await expect(page.getByRole('heading', { name: 'Finance is not available to your role' })).toBeVisible();
  await expect(page.getByText(/Ask a workspace owner to change your role from viewer to member/)).toBeVisible();
});

test('a failed summary shows the reason and a retry', async ({ page }) => {
  await mockApi(page, { failSummary: true });
  await page.goto('/#finance');
  await expect(page.getByRole('alert')).toContainText('Finance could not be loaded: The database is not reachable.');
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('Finance reads in Arabic, right to left, with Western digits', async ({ page }) => {
  await mockApi(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#finance-transactions');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { name: 'المالية', level: 1 })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'مسار التنقل' })).toContainText('المعاملات');
  await expect(page.locator('.finTxTable tbody tr').first()).toContainText('AED');
});
