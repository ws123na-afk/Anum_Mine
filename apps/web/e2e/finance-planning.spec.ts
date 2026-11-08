import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { useSavedLocale } from './support/workspaceApi';

// Finance, Bills and budgets and Debts and goals (docs/finance.md#bills-and-budgets, #debts-and-goals).
// A bill is a forecast until a recorded payment is matched to its due date; budgets are Guardian caps
// with an alert threshold each; the payoff plan is worked out from the debts entered. The API is mocked
// at http://localhost:8000/** like the other Finance specs.
async function mockApi(page: Page, options: { empty?: boolean; denied?: boolean; failSchedule?: boolean } = {}) {
  const state = financeState({ empty: options.empty, denied: options.denied });
  const sent: { method: string; path: string; body: unknown }[] = [];
  const installments = new Map<string, Record<string, unknown>>();
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
    if (options.failSchedule && path === '/api/v1/finance/bill-schedule') {
      options.failSchedule = false;
      return json({ error: { code: 'internal_error', message: 'The database is not reachable.', correlation_id: 'c', details: [] } }, 503);
    }
    const body = request.postData() ? request.postDataJSON() : null;
    if (request.method() !== 'GET') sent.push({ method: request.method(), path, body });
    if (path === '/api/v1/finance/installment-email/mailboxes' && request.method() === 'GET')
      return json([{ id: 'gmail_llc', address: 'shahzeb.rahmanllc@gmail.com', selected: true }]);
    if (path === '/api/v1/email/accounts/gmail_llc/sync' && request.method() === 'POST')
      return json({ id: 'gmail_llc', address: 'shahzeb.rahmanllc@gmail.com', provider: 'gmail', status: 'connected' });
    if (path === '/api/v1/finance/installment-email/preview' && request.method() === 'GET')
      return json({
        account_id: 'gmail_llc', address: 'shahzeb.rahmanllc@gmail.com', messages_checked: 4,
        messages_matched: 3, warnings: [],
        rows: [
          { provider: 'Tamara', order_id: 'jarir-gmail-1', merchant: 'Jarir', purchase_on: '2026-10-15',
            due_on: '2026-10-15', installment: 1, total_installments: 2,
            amount: '602.00', currency: 'SAR', source_status: 'paid', source_email: null, review_notes: null },
          { provider: 'Tamara', order_id: 'jarir-gmail-1', merchant: 'Jarir', purchase_on: '2026-10-15',
            due_on: '2026-12-01', installment: 2, total_installments: 2,
            amount: '602.00', currency: 'SAR', source_status: 'scheduled', source_email: null, review_notes: null },
        ],
      });
    if (path === '/api/v1/finance/installment-statement' && request.method() === 'GET')
      return json({ items: [...installments.values()], total: installments.size });
    if (path === '/api/v1/finance/installment-statement/sync' && request.method() === 'POST') {
      let created = 0, updated = 0, unchanged = 0;
      for (const row of (body as { entries: Record<string, unknown>[] }).entries) {
        const key = String(row.provider) + '/' + String(row.order_id) + '/' + String(row.installment);
        const old = installments.get(key);
        if (!old) created++;
        else if (JSON.stringify(old) !== JSON.stringify(row)) updated++;
        else unchanged++;
        installments.set(key, row);
      }
      return json({ created, updated, unchanged, conflicts: [], total: installments.size });
    }
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { sent, state };
}

test('bills are forecasts until a recorded payment is matched; then the due date is paid', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-bills');
  await expect(page.getByRole('heading', { level: 1, name: 'Bills and budgets' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Bills and budgets at a glance' }).getByText('Bills this month')).toBeVisible();
  const upcoming = page.getByRole('region', { name: /Upcoming bills/ });
  const rent = upcoming.getByRole('row').filter({ hasText: 'Rent' }).first();
  await expect(rent).toBeVisible();
  await expect(rent.getByText('Paid')).toHaveCount(0);
  await expect(upcoming.getByText('Bills are forecasts until matched to a recorded payment.')).toBeVisible();
  await rent.getByRole('button', { name: 'Match a payment to Rent' }).click();
  const dialog = page.getByRole('dialog', { name: /Match a payment to Rent/ });
  await dialog.getByRole('listitem').filter({ hasText: 'RENT TRANSFER' }).getByRole('button', { name: 'Match' }).click();
  await expect(page.getByText('Payment matched: Rent is paid.')).toBeVisible();
  await expect(rent.getByText('Paid')).toBeVisible();
  const match = sent.find((r) => r.method === 'POST' && /\/bills\/fin_bill_rent\/matches$/.test(r.path));
  expect((match?.body as { canonical_id: string }).canonical_id).toBe('fin_je_rent');
  await rent.getByRole('button', { name: 'Remove the matched payment from Rent' }).click();
  await expect(page.getByText('Match removed: Rent is a forecast again.')).toBeVisible();
  await expect(rent.getByText('Paid')).toHaveCount(0);
});

test('budgets: health, alert threshold saved, and a new cap confirmed with its code', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-budgets');
  await expect(page.getByRole('heading', { level: 1, name: 'Bills and budgets' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Budgets' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('progressbar', { name: /Dining: \d+% of the budget spent/ }).first()).toBeVisible();
  const rules = page.getByRole('region', { name: 'Budget rules' });
  await rules.getByLabel('Alert threshold for Dining').selectOption('90');
  await expect(page.getByText('Alerts saved.')).toBeVisible();
  expect(sent.some((r) => r.path.endsWith('/budgets/alerts') && (r.body as { alerts: { alert_percent: number }[] }).alerts[0].alert_percent === 90)).toBe(true);
  await rules.getByRole('button', { name: 'Add category cap' }).click();
  const dialog = page.getByRole('dialog', { name: 'Add category cap' });
  await dialog.getByLabel('Category').selectOption('fuel');
  await dialog.getByLabel('Monthly cap (AED)').fill('800');
  await dialog.getByRole('button', { name: 'Review change' }).click();
  await expect(dialog.getByText('Confirmation code:')).toBeVisible();
  await dialog.getByRole('button', { name: 'Confirm change' }).click();
  await expect(page.getByText('Budget saved.')).toBeVisible();
  await expect(rules.getByRole('rowheader', { name: 'Fuel' })).toBeVisible();
});

test('recurring and calendar tabs list the bills, and a detected payment is kept as a bill', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#finance-bills');
  await page.getByRole('tab', { name: 'Recurring' }).click();
  await page.getByRole('button', { name: 'Keep as bill' }).click();
  await expect(page.getByText('NETFLIX.COM saved as a bill.')).toBeVisible();
  await page.getByRole('tab', { name: 'Calendar' }).click();
  await expect(page.getByRole('button', { name: /^Rent, AED/ }).first()).toBeVisible();
});

test('debts and goals: the payoff plan is saved, a goal paused, a payment matched to a debt', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-debts');
  await expect(page.getByRole('heading', { level: 1, name: 'Debts and goals' })).toBeVisible();
  await expect(page.getByText(/estimates you entered/)).toBeVisible();
  await expect(page.getByRole('region', { name: 'Your debts' }).getByText('Rewards card')).toBeVisible();
  await page.getByRole('radio', { name: /Snowball/ }).click();
  await expect(page.getByRole('radio', { name: /Snowball/ })).toBeChecked();
  await expect(page.getByText('Plan saved.')).toBeVisible();
  expect(sent.some((r) => r.path.endsWith('/debt-plan') && (r.body as { plans: { strategy: string }[] }).plans[0].strategy === 'snowball')).toBe(true);
  await page.getByLabel('Planned monthly payment (AED)').fill('100');
  await page.getByRole('button', { name: 'Preview plan' }).click();
  await expect(page.getByText(/less than the minimum payments together/)).toBeVisible();
  await page.getByLabel('Planned monthly payment (AED)').fill('600');
  await page.getByRole('button', { name: 'Preview plan' }).click();
  await expect(page.getByText(/Paid off in \d+ months/)).toBeVisible();

  await page.getByRole('button', { name: 'Pause Emergency fund' }).click();
  await expect(page.getByText(/Emergency fund is paused/)).toBeVisible();
  expect(sent.some((r) => r.method === 'PATCH' && (r.body as { paused?: boolean }).paused === true)).toBe(true);

  await page.getByRole('button', { name: 'More actions for Rewards card' }).click();
  await page.getByRole('button', { name: 'Match a recorded payment' }).click();
  await page.getByRole('dialog', { name: /Match a payment to Rewards card/ }).getByRole('listitem').filter({ hasText: 'CARD PAYMENT' }).getByRole('button', { name: 'Match' }).click();
  await expect(page.getByText('Payment matched.')).toBeVisible();
  await page.getByRole('tab', { name: 'History' }).click();
  await expect(page.getByRole('region', { name: 'Debt payment history' }).getByRole('row')).toHaveCount(3);
});

test('the new plan wizard steps through debts, strategy and payment', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-debts');
  await page.getByRole('button', { name: 'New plan wizard' }).click();
  const wizard = page.getByRole('dialog', { name: 'New plan (AED)' });
  await expect(wizard.getByText('Rewards card')).toBeVisible();
  await wizard.getByRole('button', { name: 'Next' }).click();
  await wizard.getByRole('radio', { name: /Avalanche/ }).check();
  await wizard.getByRole('button', { name: 'Next' }).click();
  await wizard.getByLabel('Planned monthly payment (AED)').fill('500');
  await wizard.getByRole('button', { name: 'Save plan' }).click();
  await expect(page.getByText('Plan saved.')).toBeVisible();
  expect(sent.some((r) => r.path.endsWith('/debt-plan') && Number((r.body as { plans: { monthly_payment: string }[] }).plans[0].monthly_payment) === 500)).toBe(true);
});

test('empty: no bills and no debts lead to adding one', async ({ page }) => {
  await mockApi(page, { empty: true });
  await page.goto('/#finance-bills');
  await expect(page.getByRole('heading', { name: 'No bills due this month' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'No budgets yet' })).toBeVisible();
  await page.goto('/#finance-debts');
  await expect(page.getByRole('heading', { name: 'No debts recorded' })).toBeVisible();
  await page.getByRole('region', { name: 'Your debts' }).getByRole('button', { name: 'Add debt' }).last().click();
  await expect(page.getByRole('dialog', { name: 'Add debt' }).getByLabel('Starting balance (AED)')).toBeVisible();
});

test('a failed load says what failed and retries', async ({ page }) => {
  await mockApi(page, { failSchedule: true });
  await page.goto('/#finance-bills');
  await expect(page.getByText(/Bills could not be loaded: The database is not reachable/)).toBeVisible();
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.getByRole('region', { name: /Upcoming bills/ })).toBeVisible();
});

test('Arabic: the pages read right to left with Arabic labels', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockApi(page);
  await page.goto('/#finance-bills');
  await expect(page.getByRole('heading', { level: 1, name: 'الفواتير والميزانيات' })).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await page.goto('/#finance-debts');
  await expect(page.getByRole('heading', { level: 1, name: 'الديون والأهداف' })).toBeVisible();
});

for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const) {
  test(`no horizontal overflow at ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await mockApi(page);
    for (const hash of ['#finance-bills', '#finance-debts']) {
      await page.goto(`/${hash}`);
      await expect(page.locator('.fplPage .finKpis')).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
    }
  });
}

test('Bills CSV import maps Tamara fields, skips paid and review rows and creates only unpaid one-time bills', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-bills');
  await page.getByRole('button', { name: 'Import CSV' }).click();
  const dialog = page.getByRole('dialog', { name: 'Import installment bills from CSV' });
  const csv = [
    'Due Date,Merchant,Tamara Order ID,Installment #,Amount (SAR),Currency,Reconciliation Status',
    '2026-11-01,Jarir,example-1,2,602.00,SAR,Paid',
    '2026-12-01,Jarir,example-1,3,602.00,SAR,Scheduled',
    '2026-12-02,Amazon,example-2,2,75.00,SAR,Refund adjusted - review',
  ].join('\n');
  await dialog.locator('input[type="file"]').setInputFiles({ name: 'tamara.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) });
  await expect(dialog.getByText('New: 1. Existing to update: 0. Skipped: 2. Errors: 0. Conflicts: 0.')).toBeVisible();
  // Paid and refund-review rows are excluded; the scheduled installment is imported.
  await dialog.getByRole('button', { name: 'Sync 1 new + 0 existing bills' }).click();
  await expect.poll(() => sent.filter((x) => x.method === 'POST' && x.path === '/api/v1/finance/bills').length).toBe(1);
  const created = sent.find((x) => x.method === 'POST' && x.path === '/api/v1/finance/bills')?.body as { next_due_on: string; cadence: string; amount: string };
  expect(created).toMatchObject({ next_due_on: '2026-12-01', cadence: 'once', amount: '602.00' });
});

test('Tamara CSV preview reports paid and upcoming installments by order without importing paid rows', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-bills');
  await page.getByRole('button', { name: 'Import CSV' }).click();
  const dialog = page.getByRole('dialog', { name: 'Import installment bills from CSV' });
  const csv = [
    'Due Date,Merchant,Tamara Order ID,Installment #,Amount (SAR),Currency,Reconciliation Status',
    '2026-10-01,Jarir,jarir-1,1,602.00,SAR,Paid',
    '2026-11-01,Jarir,jarir-1,2,602.00,SAR,Paid',
    '2026-12-01,Jarir,jarir-1,3,602.00,SAR,Scheduled',
    '2027-01-01,Jarir,jarir-1,4,602.00,SAR,Scheduled',
    '2026-12-05,Amazon,amazon-1,2,29.00,SAR,Refund adjusted - review',
  ].join('\n');
  await dialog.locator('input[type="file"]').setInputFiles({ name: 'tamara.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) });
  const summary = dialog.getByRole('region', { name: 'Imported order payment summary' });
  await expect(summary.getByRole('row').filter({ hasText: 'jarir-1' })).toContainText('1204.00');
  await expect(summary.getByRole('row').filter({ hasText: 'jarir-1' })).toContainText('2026-12-01');
  await expect(dialog.getByText('New: 2. Existing to update: 0. Skipped: 3. Errors: 0. Conflicts: 0.')).toBeVisible();
  await dialog.getByRole('button', { name: 'Sync 2 new + 0 existing bills' }).click();
  await expect.poll(() => sent.filter(r => r.method === 'POST' && r.path === '/api/v1/finance/bills').length).toBe(2);
  const body = sent.find(r => r.method === 'POST' && r.path === '/api/v1/finance/bills')?.body as { notes: string; cadence: string };
  expect(body.cadence).toBe('once');
  expect(body.notes).toContain('CSV Paid Count: 2');
  expect(body.notes).toContain('CSV Paid Amount: 1204.00');
});

test('re-uploading the same CSV preserves all paid history without duplicating scheduled bills', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-bills');
  const csv = [
    'Due Date,Merchant,Tamara Order ID,Installment #,Total Installments,Amount (SAR),Currency,Reconciliation Status',
    '2026-10-15,Jarir,jarir-1,1,3,602.00,SAR,Paid',
    '2026-11-01,Jarir,jarir-1,2,3,602.00,SAR,Paid',
    '2026-12-01,Jarir,jarir-1,3,3,602.00,SAR,Scheduled',
  ].join('\n');
  const upload = async () => {
    await page.getByRole('button', { name: 'Import CSV' }).click();
    const dialog = page.getByRole('dialog', { name: 'Import installment bills from CSV' });
    await dialog.locator('input[type="file"]').setInputFiles({ name: 'tamara.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) });
    return dialog;
  };
  let dialog = await upload();
  await expect(dialog.getByText('Full statement storage: 3 of 3 rows ready.')).toBeVisible();
  await dialog.getByRole('button', { name: 'Sync 1 new + 0 existing bills' }).click();
  await expect(dialog).not.toBeVisible();

  dialog = await upload();
  await expect(dialog.getByText(/New: 0\. Existing to update: 1\./)).toBeVisible();
  await dialog.getByRole('button', { name: 'Sync 0 new + 1 existing bills' }).click();
  await expect(dialog).not.toBeVisible();

  dialog = await upload();
  await expect(dialog.getByText(/New: 0\. Existing to update: 0\./)).toBeVisible();
  await dialog.getByRole('button', { name: 'Sync 0 new + 0 existing bills' }).click();
  const posts = sent.filter(x => x.path === '/api/v1/finance/bills' && x.method === 'POST');
  expect(posts).toHaveLength(1);
  expect(sent.filter(x => x.path === '/api/v1/finance/installment-statement/sync' && x.method === 'POST')).toHaveLength(3);
  await expect(page.getByRole('region', { name: 'Complete installment statement' }).getByText('jarir-1')).toBeVisible();
});

test('full statement keeps completed orders, excludes refund cases from monthly dues and exports data', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#finance-bills');
  await page.getByRole('button', { name: 'Import CSV' }).click();
  const dialog = page.getByRole('dialog', { name: 'Import installment bills from CSV' });
  const csv = [
    'Due Date,Merchant,Tamara Order ID,Installment #,Total Installments,Amount (SAR),Currency,Reconciliation Status,Review Notes',
    '2026-06-01,Noon,completed-1,1,2,50.00,SAR,Paid,',
    '2026-07-01,Noon,completed-1,2,2,50.00,SAR,Paid,',
    '2026-11-01,Amazon,refund-1,1,1,99.00,SAR,Refund adjusted - review,Awaiting confirmation',
    '2026-12-01,Jarir,open-1,1,1,602.00,SAR,Scheduled,',
  ].join('\n');
  await dialog.locator('input[type="file"]').setInputFiles({ name: 'all.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) });
  await expect(dialog.getByText('Full statement storage: 4 of 4 rows ready.')).toBeVisible();
  await dialog.getByRole('button', { name: 'Sync 1 new + 0 existing bills' }).click();
  const section = page.getByRole('region', { name: 'Complete installment statement' });
  await expect(section.getByText('completed-1')).toBeVisible();
  await expect(section.getByText('refund-1')).toBeVisible();
  await expect(section.getByText('open-1')).toBeVisible();
  await expect(section.getByRole('row').filter({ hasText: '2026-12' })).toContainText('602.00');
  await expect(section.getByText('2026-11', { exact: true })).toHaveCount(0);
  await expect(section.getByRole('button', { name: 'Export statement CSV' })).toBeVisible();
});

test('Gmail sync for preferred LLC account previews and imports only pending installments', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-bills');
  await page.getByRole('button', { name: 'Sync Gmail' }).click();
  const dialog = page.getByRole('dialog', { name: 'Import Tamara payments from connected Gmail' });
  await expect(dialog.getByLabel('Gmail mailbox')).toContainText('shahzeb.rahmanllc@gmail.com');
  await dialog.getByRole('button', { name: 'Preview Tamara emails' }).click();
  await expect(dialog.getByText('Installments found: 2.')).toBeVisible();
  await expect(dialog.getByText('New Bills: 1.')).toBeVisible();
  await dialog.getByRole('button', { name: 'Approve Gmail statement import' }).click();
  await expect(dialog.getByText(/Saved 2 email installment entries/)).toBeVisible();
  expect(sent.filter(x => x.path === '/api/v1/finance/installment-statement/sync' && x.method === 'POST')).toHaveLength(1);
  const bills = sent.filter(x => x.path === '/api/v1/finance/bills' && x.method === 'POST');
  expect(bills).toHaveLength(1);
  expect(bills[0].body).toMatchObject({ next_due_on: '2026-12-01', amount: '602.00', cadence: 'once' });
  expect(sent.some(x => x.path === '/api/v1/email/accounts/gmail_llc/sync')).toBe(true);
});
