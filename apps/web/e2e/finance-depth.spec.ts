import { expect, test, type Page, type Route } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { mockBooks } from './support/businessBooksApi';
import { clippedControls, horizontalOverflow } from './support/gatePages';
import { useSavedLocale } from './support/workspaceApi';

// Loan schedules with interest and their suggestions, expected one-off income and card interest in the forecast, the
// adjustment approval step, VAT return warnings and receipts proposed for bills (docs/finance.md#lent-and-borrowed,
// #forecast, docs/business-books.md#adjustment-approval, #receipts-to-bills). The API is mocked at http://localhost:8000/**
// like the other specs; every person, vendor and amount is SYNTHETIC. Nothing is linked, attached or posted until the
// person presses the button.

const stamp = '2026-11-01T00:00:00Z';
const loan = {
  id: 'loan_1', direction: 'lent', person: 'Example Cousin', amount: '900.00', currency: 'AED', lent_on: '2026-08-01', due_on: '2026-12-31', notes: null,
  remind: true, remind_days_before: 3, closed_on: null, interest_rate: null, installments: 3, first_due_on: '2026-10-31', repaid: '300.00', outstanding: '600.00',
  handed: '900.00', status: 'overdue', days_until_due: -9, interest: '0.00', total_due: '900.00', next_due_on: '2026-11-30', next_due_amount: '300.00',
  overdue_amount: '0.00', links: 2, created_at: stamp, updated_at: stamp,
};
const overdueLoan = { ...loan, id: 'loan_2', person: 'Example Friend', direction: 'borrowed', next_due_on: '2026-10-31', overdue_amount: '300.00', status: 'overdue' };
const schedule = [
  { number: 1, due_on: '2026-10-31', principal: '300.00', interest: '0.00', amount: '300.00', state: 'repaid' },
  { number: 2, due_on: '2026-11-30', principal: '300.00', interest: '0.00', amount: '300.00', state: 'due' },
  { number: 3, due_on: '2026-12-31', principal: '300.00', interest: '0.00', amount: '300.00', state: 'due' },
];
const repayment = {
  key: 'loan_repayment:tx_1:loan_1', producer: 'person_loans', kind: 'loan_repayment', detected: '', why: '',
  params: { person: 'Example Cousin', direction: 'lent', currency: 'AED', amount: '300.00', outstanding: '600.00', next: '300.00' },
  evidence: [{ label: 'Transfer from EXAMPLE COUSIN', ref_type: 'transaction', ref_id: 'tx_1', value: '2026-11-08 AED 300.00' }], confidence: '0.85',
  effect: [{ amount: '300.00', currency: 'AED', direction: 'in', note: null }],
  action: { kind: 'link', transaction_ids: ['tx_1'], category: null, subcategory_id: null, transaction_type: null,
    link: { canonical_id: 'je_1', dimension: 'person_loan', target_type: 'finance_person_loan', target_id: 'loan_1', amount: '300.00', currency: 'AED', role: 'deposit',
      treatment: null, category: null, project_id: null } },
  href: '#finance-debts-people', occurred_on: '2026-11-08', payload_hash: 'a'.repeat(64),
};

async function mockFinance(page: Page) {
  const state = financeState();
  const sent: { method: string; path: string; body: unknown }[] = [];
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'member', active: true });
    if (path === '/api/v1/workspaces/current') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: stamp, updated_at: stamp, archived_at: null, archived_by: null });
    if (path === '/api/v1/me/workspace-memberships') return json([{ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', role: 'member', active: true }]);
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals' || path === '/api/v1/conversations') return json([]);
    const body = request.postData() ? request.postDataJSON() : null;
    if (request.method() !== 'GET') sent.push({ method: request.method(), path, body });
    const sub = path.replace('/api/v1/finance', '');
    if (sub === '/people-loans' && request.method() === 'GET') {
      return json({ items: [loan, overdueLoan], total: 2, limit: 20, offset: 0, overdue: 1, note: 'x',
        owed: [{ currency: 'AED', they_owe_me: '600.00', i_owe: '600.00', they_owe_me_overdue: '0.00', i_owe_overdue: '300.00' }] });
    }
    if (sub === '/people-loans' && request.method() === 'POST') return json({ ...loan, ...body, id: 'loan_3', person: body.person }, 201);
    if (sub === '/people-loans/loan_1') return json({ ...loan, movements: [], schedule });
    if (sub === '/suggestions' && request.method() === 'GET') {
      return json({ items: sent.some((s) => s.path.endsWith('/suggestions/accept')) ? [] : [repayment], total: 1, limit: 25, offset: 0,
        counts: [{ kind: 'loan_repayment', count: 1 }], unavailable: [] });
    }
    if (sub === '/suggestions/accept') return json({ key: repayment.key, status: 'accepted', batch: null, link_id: 'link_1' });
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

test('a loan with a monthly schedule shows its next repayment, what is overdue and the schedule', async ({ page }) => {
  const { sent } = await mockFinance(page);
  await page.goto('/#finance-debts-people');
  const list = page.getByRole('region', { name: /Loans with people/ });
  await expect(list).toContainText('3 monthly repayments');
  await expect(list).toContainText(/300\.00\W* overdue/);
  await expect(page.getByText(/You owe, overdue: .*300\.00/)).toBeVisible();
  await list.getByRole('button', { name: 'Open Example Cousin' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name: 'Repayment schedule' })).toBeVisible();
  await expect(dialog.getByRole('row')).toHaveCount(4);
  await expect(dialog).toContainText(/Next repayment/);
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await list.getByRole('button', { name: 'Add a loan' }).click();
  const form = page.getByRole('dialog');
  await form.getByLabel('Person').fill('Example Uncle');
  await form.getByLabel('Amount').fill('1200');
  await form.getByLabel('Interest a year (%)').fill('12');
  await form.getByLabel('Monthly repayments').fill('12');
  await form.getByLabel('First repayment').fill('2026-12-01');
  await form.getByRole('button', { name: 'Save' }).click();
  const created = sent.find((s) => s.method === 'POST' && s.path.endsWith('/people-loans'));
  expect(created?.body).toMatchObject({ person: 'Example Uncle', interest_rate: '12', installments: 12, first_due_on: '2026-12-01', due_on: null });
});

test('a possible repayment is linked only when the person presses Link', async ({ page }) => {
  const { sent } = await mockFinance(page);
  await page.goto('/#finance-suggestions');
  const item = page.getByRole('listitem').filter({ hasText: 'Example Cousin repaying you' });
  await expect(item).toBeVisible();
  expect(sent.some((s) => s.path.endsWith('/suggestions/accept'))).toBe(false);
  await item.getByRole('button', { name: 'Link' }).click();
  await expect(page.getByText(/^Linked\./)).toBeVisible();
  const accept = sent.find((s) => s.path.endsWith('/suggestions/accept'));
  expect(accept?.body).toMatchObject({ key: repayment.key, payload_hash: repayment.payload_hash });
});

test('expected one-off income is added from Safe to spend and the forecast shows card interest', async ({ page }) => {
  const { sent } = await mockFinance(page);
  await page.goto('/#finance-safe');
  const forecast = page.getByRole('region', { name: /Forecast to the end of/ });
  await forecast.getByRole('button', { name: '3 months' }).click();
  await expect(forecast.getByRole('table', { name: 'Card interest expected' })).toContainText('Rewards card');
  const card = page.getByRole('region', { name: /Expected one-off income/ });
  await expect(card).toContainText('No expected income');
  await card.getByRole('button', { name: 'Add expected income' }).first().click();
  const form = page.getByRole('dialog');
  await form.getByLabel('What it is').fill('Example bonus');
  await form.getByLabel('Amount').fill('2500');
  await form.getByLabel('Expected on').fill('2026-12-15');
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(card).toContainText('Example bonus');
  expect(sent.find((s) => s.method === 'POST' && s.path.endsWith('/expected-income'))?.body).toMatchObject({ label: 'Example bonus', amount: '2500', expected_on: '2026-12-15' });
  await card.getByRole('button', { name: 'Mark received' }).click();
  await expect(page.getByText(/as received/)).toBeVisible();
  expect(sent.find((s) => s.method === 'PATCH')?.body).toMatchObject({ label: 'Example bonus' });
});

// -- Business books --------------------------------------------------------------------------------------------------------

async function reviewRoutes(page: Page, own: boolean) {
  const calls: string[] = [];
  const items = [
    { key: 'adjustment_approval:badj_1', producer: 'adjustments', kind: 'adjustment_approval', detected: '', params: { adjustment_kind: 'accrual', description: 'Example accrued rent' },
      severity: 'blocking', occurred_on: '2026-11-05', amount: '5000.00', currency: 'SAR', ref_id: 'badj_1', section: 'journal', action: 'approve', can_act: !own,
      why_not: own ? 'own_adjustment' : null },
    { key: 'vat:return_due_soon', producer: 'tax', kind: 'vat', detected: '', params: { code: 'return_due_soon', from: '2026-10-01', to: '2026-10-31', due: '2026-11-30', days: '21' },
      severity: 'warning', occurred_on: '2026-11-30', amount: null, currency: null, ref_id: null, section: 'tax', action: 'open', can_act: true, why_not: null },
    { key: 'receipt_match:bbill_1:vdoc_1', producer: 'receipts', kind: 'receipt_match', detected: '', params: { document_id: 'vdoc_1', document: 'Example rent receipt',
      bill_id: 'bbill_1', reference: 'R-1', vendor: 'Example Landlord', merchant: 'EXAMPLE LANDLORD', receipt_date: '2026-11-03', vendor_agrees: '1', confidence: '0.9' },
      severity: 'warning', occurred_on: '2026-11-03', amount: '1150.00', currency: 'SAR', ref_id: 'bbill_1', section: 'bills', action: 'link', can_act: true, why_not: null },
  ];
  const month = (start: string, end: string, status: string) => ({ month: start.slice(0, 7), start, end, status, issues: 0, drafts: 0, allocations_in_review: 0,
    adjustments_pending: 1, ready: false });
  await page.route('http://localhost:8000/api/v1/business/books/**', async (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace('/api/v1/business/books', '');
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/review' && request.method() === 'GET') {
      const left = items.filter((i) => !calls.some((c) => c.includes(i.ref_id ?? 'none')));
      const counts: Record<string, number> = {};
      for (const i of left) counts[i.kind] = (counts[i.kind] ?? 0) + 1;
      return json({ items: left, total: left.length, limit: 25, offset: 0, counts, blocking: 1, closed_through: '2026-09-30', to_post: 0,
        close_months: [month('2026-10-01', '2026-10-31', 'open'), month('2026-11-01', '2026-11-30', 'not_ended')], producers: ['adjustments', 'tax', 'receipts'], unavailable: [] });
    }
    if (path === '/adjustments/badj_1/approve') { calls.push(`approve badj_1`); return json({ id: 'badj_1', status: 'active' }); }
    if (path === '/evidence/receipt-matches/accept') { calls.push(`attach bbill_1 ${request.postData()}`); return json({ document_id: 'vdoc_1', link_id: 'vlnk_1' }, 201); }
    return route.fallback();
  });
  return calls;
}

test('the accountant review: approve another person\'s adjustment, attach a proposed receipt, read the VAT warning', async ({ page }) => {
  await mockBooks(page, { role: 'member', accountant: true });
  const calls = await reviewRoutes(page, false);
  await page.goto('/#business-books/review');
  const list = page.getByRole('region', { name: /items waiting/ });
  await expect(list.getByRole('listitem').filter({ hasText: 'VAT return' })).toContainText(/usually due on .*in 21 days\. ANUM never files it/);
  const receipt = list.getByRole('listitem').filter({ hasText: 'Receipt for a bill' });
  await expect(receipt).toContainText('Example rent receipt looks like the receipt for the bill R-1 from Example Landlord');
  expect(calls).toEqual([]);
  await receipt.getByRole('button', { name: 'Attach' }).click();
  await expect(page.getByText('Attached Example rent receipt to the bill.')).toBeVisible();
  expect(calls[0]).toContain('"document_id":"vdoc_1"');
  const adjustment = list.getByRole('listitem').filter({ hasText: 'Adjustment to approve' });
  await adjustment.getByRole('button', { name: 'Approve' }).click();
  await expect(page.getByText('Approved the adjustment. It is posted now.')).toBeVisible();
  await expect(page.getByText('Adjustments waiting for approval: 1').first()).toBeVisible();
});

test('the maker of a pending adjustment sees who approves it, not a button', async ({ page }) => {
  await mockBooks(page, { role: 'member', accountant: true });
  await reviewRoutes(page, true);
  await page.goto('/#business-books/review');
  const adjustment = page.getByRole('listitem').filter({ hasText: 'Adjustment to approve' });
  await expect(adjustment).toContainText('You recorded this adjustment: another owner or an accountant approves it.');
  await expect(adjustment.getByRole('button', { name: 'Approve' })).toHaveCount(0);
});

for (const locale of ['en', 'ar'] as const) {
  test(`@gate the new loan, forecast and review views fit 1440x900 and 390x844 (${locale})`, async ({ page }) => {
    await mockFinance(page);
    if (locale === 'ar') await useSavedLocale(page, 'ar');
    for (const size of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(size);
      for (const hash of ['finance-debts-people', 'finance-safe']) {
        await page.goto(`/#${hash}`);
        await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
        await page.waitForLoadState('networkidle');
        expect(await horizontalOverflow(page), `${hash} at ${size.width}`).toBeLessThanOrEqual(0);
        expect(await clippedControls(page), `${hash} at ${size.width}`).toEqual([]);
        await shot(page, `${hash}-${locale}-${size.width}`);
      }
    }
    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await mockBooks(page, { role: 'member', accountant: true });
    await reviewRoutes(page, false);
    if (locale === 'ar') await useSavedLocale(page, 'ar');
    for (const size of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(size);
      await page.goto('/#business-books/review');
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      await page.waitForLoadState('networkidle');
      expect(await horizontalOverflow(page), `review at ${size.width}`).toBeLessThanOrEqual(0);
      expect(await clippedControls(page), `review at ${size.width}`).toEqual([]);
      await shot(page, `books-review-depth-${locale}-${size.width}`);
    }
  });
}
