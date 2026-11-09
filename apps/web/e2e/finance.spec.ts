import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
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
    // This person may not read the business books: the overview reads Personal (docs/finance.md#finance-overview).
    if (path.startsWith('/api/v1/business/books')) return json({ error: { code: 'forbidden', message: 'The business books are for owners and members with Business access.', details: [] } }, 403);
    const body = request.postData() ? request.postDataJSON() : null;
    if (path.startsWith('/api/v1/finance')) sent.push({ method: request.method(), path, body });
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { state, sent };
}

const tab = (page: Page, name: string) => page.getByRole('navigation', { name: 'Finance sections' }).getByRole('button', { name });

test('the overview shows net worth, bills, review, spending by category, recent activity and balances, and says it is read only', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#finance');
  await expect(page.getByRole('heading', { name: 'Finance overview', level: 1 })).toBeVisible();
  // Without the business books (this mock refuses them) the page reads Personal and the switch says why.
  await expect(page.getByRole('radio', { name: 'Personal', exact: true })).toBeChecked();
  await expect(page.getByRole('radio', { name: /^Business/ })).toBeDisabled();
  // The scope chosen was All finance; with the books refused the chart reads the person's own six months, never stays loading.
  const flow = page.getByRole('region', { name: 'Income and outflow' });
  await expect(flow.getByRole('img', { name: 'Income and outflow' }).or(flow.getByText('No income or spending this month yet.'))).toBeVisible();
  await expect(flow.getByRole('status')).toHaveCount(0);
  const kpis = page.getByRole('region', { name: 'This month at a glance' });
  await expect(kpis.getByText('Personal net worth')).toBeVisible();
  await expect(kpis.getByText('Spent')).toBeVisible();
  await expect(page.getByRole('list', { name: 'Accounts, bills and reviews' }).getByRole('button', { name: /bills? due/ })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Spending by category' }).getByRole('list', { name: 'Spending by category' })).toContainText('Utilities');
  await expect(page.getByRole('region', { name: 'Bills due soon' }).getByText('Rent')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Balances' }).getByText('ending 1234')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Recent finance activity' }).getByRole('row')).not.toHaveCount(0);
  await expect(page.getByText('Read only: ANUM never pays, transfers or trades from here.')).toBeVisible();
  // No pay, transfer or trade action anywhere on the page.
  await expect(page.getByRole('button', { name: /pay|transfer|buy|sell/i })).toHaveCount(0);
  await page.getByRole('button', { name: 'Previous month' }).click();
  await expect(page.getByRole('group', { name: 'Period' })).not.toContainText(new Date().toLocaleString('en', { month: 'long' }) + ' ' + new Date().getFullYear());
});

test('transactions and ledger: period, search, server filters and paging; the drawer shows the masked SMS, a possible duplicate and saves the category', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-transactions');
  await expect(page.getByRole('heading', { name: 'Transactions and ledger', level: 1 })).toBeVisible();
  await expect(tab(page, 'Transactions')).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('radio', { name: 'Personal', exact: true })).toBeChecked();
  await expect(page.getByRole('radio', { name: /^Business/ })).toBeDisabled();
  await page.getByRole('combobox', { name: 'Period', exact: true }).selectOption({ label: 'All dates' });
  const table = page.locator('.fregTable');
  await expect(table.locator('tbody tr')).toHaveCount(25);
  await expect(page.getByText('1 to 25 of 64')).toBeVisible();
  // Untrusted statement text is shown as text, never run.
  await expect(table.getByText('=HYPERLINK(example)')).toBeVisible();
  const kpis = page.getByRole('region', { name: 'This period' });
  await expect(kpis).toContainText('Transactions');
  await expect(kpis).toContainText('64');
  await expect(page.getByRole('region', { name: /^Reconciliation/ })).toContainText('Waiting in review');
  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByText('26 to 50 of 64')).toBeVisible();
  await page.getByLabel('Source', { exact: true }).selectOption('sms');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.getByText('1 to 16 of 16')).toBeVisible();
  expect(sent.some((r) => r.path === '/api/v1/finance/register')).toBe(true);
  await expect(table.locator('tbody tr').first()).toContainText('SMS');
  // No live bank connection is ever claimed.
  await expect(page.getByText(/synced|connected bank/i)).toHaveCount(0);

  await page.getByRole('button', { name: 'Clear filters' }).click();
  await page.getByRole('searchbox', { name: 'Search merchant or reference' }).fill('talabat');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await table.locator('tbody tr').first().click();
  const drawer = page.locator('.fregDrawer');
  await expect(drawer.getByText('Original SMS (masked)')).toBeVisible();
  await expect(drawer).toContainText('***1234');
  await drawer.getByLabel('Choose category').selectOption('entertainment');
  // SMS-derived transactions use the audited SMS Review path, not direct PATCH.
  await expect(drawer.getByRole('button', { name: 'Save category' })).toBeDisabled();
  await expect(drawer.getByText(/Make its category correction under Finance/)).toBeVisible();
  expect(sent.some((r) => r.method === 'PATCH' && (r.body as { category?: string }).category === 'entertainment')).toBe(false);
  await drawer.getByRole('button', { name: 'Close details' }).click();

  await page.getByRole('searchbox', { name: 'Search merchant or reference' }).fill('');
  await page.getByLabel('Status', { exact: true }).selectOption('duplicate_candidate');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await table.locator('tbody tr').first().getByRole('button', { name: /^Details for/ }).click();
  await expect(drawer.getByText('Possible duplicate').first()).toBeVisible();
  await drawer.getByRole('button', { name: 'View match' }).click();
  await expect(drawer).toContainText('DEWA UTILITY');
  await drawer.getByRole('button', { name: 'Close details' }).click();
  await expect(drawer).toHaveCount(0);

  await tab(page, 'Bills').click();
  await expect(page).toHaveURL(/#finance-bills$/);
  await page.goBack();
  // The period picked (All dates) is kept in the URL, so Back returns to the same view.
  await expect(page).toHaveURL(/#finance-transactions\?month=all$/);
  await expect(page.getByRole('combobox', { name: 'Period', exact: true })).toHaveValue('');
  await expect(tab(page, 'Transactions')).toHaveAttribute('aria-current', 'page');
});

test('an empty current month says where the latest records are and opens that month', async ({ page }) => {
  const { state } = await mockApi(page);
  // Every SYNTHETIC row moved to two months ago: the current month (Finance time zone) has none.
  const now = new Date();
  const older = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 2, 10)).toISOString().slice(0, 10);
  state.transactions = state.transactions.map((row) => ({ ...row, posted_on: older }));
  await page.goto('/#finance-transactions');
  const banner = page.getByText(/^No transactions in .+; your latest records are in .+\.$/);
  await expect(banner).toBeVisible({ timeout: 20_000 });
  await page.locator('.finBanner').getByRole('button', { name: /^Show / }).click();
  await expect(page.getByRole('combobox', { name: 'Period', exact: true })).toHaveValue(older.slice(0, 7));
  await expect(page).toHaveURL(new RegExp(`#finance-transactions\\?month=${older.slice(0, 7)}$`));
  await expect(page.locator('.fregTable tbody tr').first()).toBeVisible();
  await expect(banner).toHaveCount(0);
  // A reload keeps the month.
  await page.reload();
  await expect(page.getByRole('combobox', { name: 'Period', exact: true })).toHaveValue(older.slice(0, 7), { timeout: 20_000 });
});

test('a receipt is attached from the drawer to the vault and linked to the payment; SMS review and Imports open the existing flows', async ({ page }) => {
  const { state } = await mockApi(page);
  const vault: string[] = [];
  await page.route('http://localhost:8000/api/v1/documents**', async (route) => {
    const url = new URL(route.request().url());
    vault.push(`${route.request().method()} ${url.pathname}`);
    if (url.pathname === '/api/v1/documents') return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ id: 'doc_new' }) });
    state.receipts = [...(state.receipts ?? []), 'fin_txn_2'];
    return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ id: 'vlnk_1', target_type: 'finance_transaction', status: 'confirmed' }) });
  });
  await page.goto('/#finance-transactions');
  await page.getByRole('combobox', { name: 'Period', exact: true }).selectOption({ label: 'All dates' });
  await page.getByRole('searchbox', { name: 'Search merchant or reference' }).fill('talabat');
  await page.getByLabel('Evidence status').selectOption('missing');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await page.locator('.fregTable tbody tr').filter({ hasText: 'TALABAT' }).first().click();
  const drawer = page.locator('.fregDrawer');
  await drawer.locator('input[type=file]').setInputFiles({ name: 'talabat.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 SYNTHETIC') });
  await expect(drawer.getByText('Receipt saved in your vault and linked to this payment.')).toBeVisible();
  expect(vault).toEqual(['POST /api/v1/documents', 'POST /api/v1/documents/doc_new/links']);
  await drawer.getByRole('button', { name: 'Done' }).click();

  await page.getByRole('tab', { name: /SMS review/ }).click();
  await expect(page.getByRole('tab', { name: /SMS review/ })).toContainText('2');
  await expect(page.getByRole('link', { name: 'SMS capture settings' })).toHaveAttribute('href', '#finance-sms');
  await page.getByRole('button', { name: 'Import statement' }).click();
  await expect(page.getByRole('tab', { name: 'Imports' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByLabel('Statement file (CSV or PDF)')).toBeVisible();
});

test('accounts: groups, sources, masked numbers, actions, the register with running balances, rules and activity', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-accounts');
  await expect(page.getByRole('heading', { name: 'Accounts', level: 1 })).toBeVisible();
  const kpis = page.getByRole('region', { name: 'Accounts at a glance' });
  await expect(kpis).toContainText('Total available');
  await expect(kpis).toContainText('15,349.50');
  const card = page.locator('.facctCard').first();
  await expect(card).toContainText('Everyday account');
  await expect(card).toContainText('Statement');
  await expect(card).not.toContainText('1234');
  await card.getByRole('button', { name: 'Show the last four digits of Everyday account' }).click();
  await expect(card).toContainText('•••• 1234');
  await expect(page.getByText('This account is read from the statements you import.')).toBeVisible();
  const register = page.getByRole('region', { name: 'Account register: Everyday account' }).first();
  await expect(register.locator('tbody tr')).toHaveCount(10);
  await expect(register).toContainText('Showing 10 of 64 transactions');
  await expect(register.locator('tbody tr').first().locator('td').last()).toContainText('142,959.50');
  await expect(page.getByRole('img', { name: /Money in and out over 14 days, in AED/ })).toBeVisible();
  await page.getByLabel('Range').selectOption('30');
  await expect(page.getByRole('img', { name: /over 30 days/ })).toBeVisible();

  await page.getByRole('tab', { name: /Cards/ }).click();
  const visa = page.locator('.facctCard').first();
  await expect(visa).toContainText('Rewards card');
  await expect(visa).toContainText('SMS');
  await expect(visa).toContainText('20,000.00');
  await expect(visa).toContainText('Payment due');

  await page.getByRole('tab', { name: /Cash and bank/ }).click();
  await page.getByRole('button', { name: 'Set opening balance' }).click();
  const dialog = page.getByRole('dialog', { name: 'Opening balance of Everyday account' });
  await dialog.getByLabel('Opening balance (AED)').fill('1200');
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Opening balance of Everyday account saved.')).toBeVisible();
  expect(sent.some((r) => r.method === 'PATCH' && Number((r.body as { opening_balance?: string }).opening_balance) === 1200)).toBe(true);
  await page.getByRole('button', { name: 'Archive' }).click();
  await expect(page.getByText('Everyday account is archived.')).toBeVisible();

  await page.getByRole('tab', { name: 'Data sources' }).click();
  await expect(page.getByText('Open banking is not connected.', { exact: false })).toBeVisible();
  await page.getByRole('tab', { name: /Archived/ }).click();
  await page.getByRole('button', { name: 'View all transactions' }).click();
  await expect(page).toHaveURL(/#finance-transactions$/);
  await expect(page.getByRole('combobox', { name: 'Account', exact: true })).toHaveValue('fin_acct_current');
});

test('a statement is previewed with its column mapping, then imported; the import can be undone', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-import');
  await page.getByLabel('Statement file (CSV or PDF)').setInputFiles({ name: 'november.csv', mimeType: 'text/csv', buffer: Buffer.from('Transaction Date,Narration,Debit,Credit\n01/11/2026,SPINNEYS,60.00,\n') });
  await page.getByRole('button', { name: 'Preview' }).click();
  const preview = page.getByRole('region', { name: 'Preview of november.csv' });
  await expect(preview).toContainText('4 rows: 3 read, 2 new, 1 already imported, 1 could not be read.');
  await expect(preview).toContainText('The dates fit both day first and month first');
  await expect(preview.getByLabel('Debit column')).toHaveValue('Debit');
  // The matching engine's groups: the duplicate is listed as already recorded, with its reason.
  await expect(preview.getByText('Already recorded (1)')).toBeVisible();
  await expect(preview.getByText('New (2)')).toBeVisible();
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
  await page.getByRole('tab', { name: 'Recurring' }).click();
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

test('a card opens with what it owes as a negative balance, or In credit when it was overpaid', async ({ page }) => {
  const { sent } = await mockApi(page, { empty: true });
  await page.goto('/#finance');
  await page.getByRole('region', { name: 'Start with an account' }).getByRole('button', { name: 'Add account' }).click();
  await page.getByRole('button', { name: 'Add account' }).first().click();
  const dialog = page.getByRole('dialog', { name: 'Add account' });
  await dialog.getByLabel('Name').fill('Sample card');
  await dialog.getByLabel('Type').selectOption('card');
  await expect(dialog.getByText('Cards and loans: what is owed is negative (owes 3,000 is -3000).')).toBeVisible();
  await dialog.getByLabel('Opening balance (AED)').fill('3000');
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(dialog.getByRole('alert')).toContainText('enter what it owes as a negative amount');
  await dialog.getByLabel('In credit (overpaid)').check();
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Sample card added.')).toBeVisible();
  const created = sent.find((r) => r.method === 'POST' && (r.body as { name?: string } | null)?.name === 'Sample card');
  expect(created?.body).toMatchObject({ kind: 'card', opening_balance: '3000', credit_balance: true });
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
  await expect(page.getByRole('heading', { name: 'المعاملات والدفتر', level: 1 })).toBeVisible();
  await page.getByRole('combobox', { name: 'الفترة', exact: true }).selectOption({ label: 'كل التواريخ' });
  await expect(page.locator('.fregTable tbody tr').first()).toContainText('AED');
  await page.goto('/#finance-accounts');
  await expect(page.getByRole('heading', { name: 'الحسابات', level: 1 })).toBeVisible();
  await expect(page.getByText('ANUM غير متصل بأي بنك.', { exact: false })).toBeVisible();
});

const VIEWPORTS = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const locale of ['en', 'ar'] as const) {
  for (const [width, height] of VIEWPORTS) {
    for (const view of ['transactions', 'accounts'] as const) {
      test(`responsive @gate: ${view} fits with no sideways scroll at ${width}x${height} (${locale})`, async ({ page }) => {
        await page.setViewportSize({ width, height });
        if (locale === 'ar') await useSavedLocale(page, 'ar');
        await mockApi(page);
        await page.goto(`/#finance-${view}`);
        if (view === 'transactions') {
          await page.getByRole('combobox', { name: locale === 'ar' ? 'الفترة' : 'Period', exact: true }).selectOption('');
          await page.locator('.fregTable tbody tr').nth(2).click();
          await expect(page.locator('.fregDrawer .fregFacts')).toBeVisible();
        } else {
          await expect(page.locator('.facctRegister tbody tr').first()).toBeVisible();
        }
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        expect(overflow).toBeLessThanOrEqual(0);
        for (const control of await page.locator('.fregPage button:visible').all()) {
          if (await control.evaluate((el) => Boolean(el.closest('.finTableWrap, .finTabs')))) continue;
          const box = await control.boundingBox();
          if (!box) continue;
          expect(box.x).toBeGreaterThanOrEqual(-0.5);
          expect(box.x + box.width).toBeLessThanOrEqual(width + 0.5);
        }
        const serious = (await new AxeBuilder({ page }).include('main').analyze()).violations.filter((v) => v.impact === 'critical' || v.impact === 'serious');
        expect(serious).toEqual([]);
      });
    }
  }
}
