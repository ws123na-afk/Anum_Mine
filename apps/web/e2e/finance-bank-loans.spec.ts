import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { clippedControls, horizontalOverflow } from './support/gatePages';
import { useSavedLocale } from './support/workspaceApi';

// A bank loan on a debt (docs/finance.md#bank-loans): the schedule with each instalment paid, missed, upcoming or waiting for a
// pick, the totals, the payments found (accepted at once, undone at once) and the loans paid off. The API is mocked at
// http://localhost:8000/** like the other specs; every lender, account and amount is SYNTHETIC. Nothing is linked until the
// person presses Link all or picks a payment.

const stamp = '2026-11-01T00:00:00Z';
const debt = { id: 'fin_debt_loan', name: 'Example car loan', kind: 'loan', balance: '99999.00', currency: 'SAR', apr_percent: '0.0000', minimum_payment: '0.00',
  due_day: null, account_id: null, notes: null, created_at: stamp, updated_at: stamp };
const row = (n: number, due: string, status: string, extra: Record<string, unknown> = {}) => ({
  number: n, due_on: due, payment: '1000.00', principal: '940.00', interest: '60.00', balance_after: `${24000 - n * 1000}.00`, status,
  paid: status === 'paid' ? '1000.00' : '0.00',
  payments: status === 'paid' ? [{ kind: 'link', ref: `je_${n}`, on: due, amount: '1000.00', link_id: `link_${n}`, batch_id: 'lbatch_1' }] : [],
  candidates: [], found: null, ...extra,
});
const months = ['2024-11-27', '2024-12-27', '2025-01-27', '2025-02-27', '2025-03-27', '2025-04-27'];
const totals = { status: 'open', basis: 'reducing', installment: '1000.00', installment_from: 'lender', split_known: true, never_ends: false, effective_rate: '5.5000',
  total_payable: '25440.00', total_interest: '1440.00', paid: '3000.00', interest_paid: '180.00', principal_paid: '2820.00', balance: '21180.00',
  payoff_on: '2026-10-27', due_count: 24, paid_count: 3, months_ahead: -21, next_due_on: '2025-02-27', next_amount: '1000.00', from_salary: false,
  found: 1, missing: 1, ambiguous: 1 };
const loanView = {
  debt_id: debt.id, name: debt.name, kind: 'loan', currency: 'SAR',
  terms: { lender: 'Example Bank', start_on: '2024-10-15', amount: '24000.00', rate_type: 'reducing', rate: '5.5000', term_months: 24, installment: '1000.00',
    payment_day: 27, repayment: 'bank_debit', account_id: 'fin_acct_current', status: 'open', closed_on: null },
  totals,
  rows: [row(1, months[0], 'paid'), row(2, months[1], 'paid'), row(3, months[2], 'paid'), row(4, months[3], 'missed'),
    row(5, months[4], 'ambiguous', { candidates: [
      { canonical_id: 'je_a', posted_on: '2025-03-27', amount: '1000.00', currency: 'SAR', merchant: 'Standing order one', account_id: 'fin_acct_current' },
      { canonical_id: 'je_b', posted_on: '2025-03-26', amount: '1000.00', currency: 'SAR', merchant: 'Standing order two', account_id: 'fin_acct_current' }] }),
    row(6, months[5], 'upcoming', { found: { kind: 'link', ref: 'je_6', on: months[5], amount: '1000.00', link_id: null, batch_id: null } })],
  batches: [{ id: 'lbatch_1', debt_id: debt.id, status: 'active', created_at: stamp, undone_at: null, count: 3, amount: '3000.00', currency: 'SAR' }],
};
const finished = { debt_id: 'fin_debt_old', name: 'Example personal loan', lender: 'Example Bank', currency: 'SAR', repayment: 'salary_deduction',
  totals: { ...totals, status: 'paid_off', balance: '0.00', payoff_on: '2025-06-30', months_ahead: 0, found: 0, missing: 0, ambiguous: 0 } };
const suggestion = {
  key: 'loan_payments:fin_debt_loan:abc', producer: 'bank_loans', kind: 'loan_payments', detected: '', why: '',
  params: { debt_id: debt.id, name: debt.name, lender: 'Example Bank', currency: 'SAR', found: '1', missing: '1', ambiguous: '1', total: '1000.00', repayment: 'bank_debit' },
  evidence: [], confidence: '0.85', effect: [{ amount: '1000.00', currency: 'SAR', direction: 'out', note: 'instalments' }],
  action: { kind: 'link_batch', transaction_ids: [], category: null, subcategory_id: null, transaction_type: null, debt_id: debt.id,
    links: [{ canonical_id: 'je_6', dimension: 'debt', target_type: 'finance_debt', target_id: debt.id, amount: '1000.00', currency: 'SAR', treatment: null, category: null, project_id: null }],
    loan_months: [] },
  months: [
    { number: 4, due_on: months[3], state: 'missing', expected: '1000.00', currency: 'SAR', amount: null, canonical_id: null, source: null, source_id: null, candidates: 0 },
    { number: 5, due_on: months[4], state: 'ambiguous', expected: '1000.00', currency: 'SAR', amount: null, canonical_id: null, source: null, source_id: null, candidates: 2 },
    { number: 6, due_on: months[5], state: 'found', expected: '1000.00', currency: 'SAR', amount: '1000.00', canonical_id: 'je_6', source: 'bank_debit', source_id: null, candidates: 1 }],
  href: '#finance-debts-loans', occurred_on: months[5], payload_hash: 'b'.repeat(64),
};

async function mockFinance(page: Page) {
  const state = financeState();
  state.debts.push(debt);
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
    const accepted = sent.some((s) => s.path.endsWith('/suggestions/accept'));
    if (sub === '/loans') return json({ items: [{ debt_id: debt.id, name: debt.name, lender: 'Example Bank', currency: 'SAR', repayment: 'bank_debit', totals }, finished] });
    if (sub === `/debts/${debt.id}/loan` && request.method() === 'GET') return json(loanView);
    if (sub === '/suggestions' && request.method() === 'GET') {
      return json({ items: accepted ? [] : [suggestion], total: accepted ? 0 : 1, limit: 100, offset: 0, counts: accepted ? [] : [{ kind: 'loan_payments', count: 1 }],
        unavailable: [], dismissed: 0 });
    }
    if (sub === '/suggestions/accept') return json({ key: suggestion.key, status: 'accepted', batch: null, loan_batch_id: 'lbatch_2' });
    if (sub === `/debts/${debt.id}/payments` && request.method() === 'POST') return json({ link: { id: 'link_pick' }, replayed: false }, 201);
    if (sub === '/loan-batches/lbatch_1/undo') return json({ ...loanView.batches[0], status: 'undone', undone_at: stamp });
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { sent };
}

async function shot(page: Page, name: string) {
  const dir = process.env.FIN_SHOTS;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
}

test('a past bank loan shows its schedule, accepts the found payments at once, takes a pick and undoes a batch', async ({ page }) => {
  const { sent } = await mockFinance(page);
  await page.goto('/#finance-debts-loans');
  await expect(page.getByRole('heading', { name: 'Your loans' })).toBeVisible();
  await expect(page.getByText('Example personal loan')).toBeVisible();
  await expect(page.getByText(/months behind/).first()).toBeVisible();
  await page.getByRole('button', { name: /Open the loan .?Example car loan/ }).click();
  await expect(page.getByRole('heading', { name: 'Example car loan', exact: true })).toBeVisible();
  await expect(page.getByText(/1 payment found for .?Example car loan/)).toBeVisible();
  const table = page.getByRole('region', { name: 'Schedule' });
  await expect(table.getByText('Missed')).toBeVisible();
  await expect(table.getByText('Paid', { exact: true }).first()).toBeVisible();
  await shot(page, `bank-loan-${page.viewportSize()?.width}`);
  expect(await horizontalOverflow(page)).toBe(0);
  expect(await clippedControls(page)).toEqual([]);
  // Nothing was linked before the accept.
  expect(sent.filter((s) => s.method === 'POST')).toEqual([]);
  await page.getByRole('button', { name: 'Link 1 payment' }).click();
  await expect(page.getByText(/Linked 1 payment/)).toBeVisible();
  expect(sent.find((s) => s.path.endsWith('/suggestions/accept'))?.body).toEqual({ key: suggestion.key, payload_hash: suggestion.payload_hash });
  // The ambiguous month waits for a pick.
  await table.getByRole('button', { name: 'Pick the payment' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('Standing order two')).toBeVisible();
  await dialog.getByRole('button', { name: 'This one' }).first().click();
  await expect(page.getByText('Instalment 5 is linked to the payment you picked.')).toBeVisible();
  expect(sent.find((s) => s.path.endsWith(`/debts/${debt.id}/payments`))?.body).toEqual({ canonical_id: 'je_a' });
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByText('Undone. The payments are no longer linked to this loan.')).toBeVisible();
  expect(sent.some((s) => s.path.endsWith('/loan-batches/lbatch_1/undo'))).toBe(true);
});

test('the loan page reads right to left in Arabic', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockFinance(page);
  await page.goto('/#finance-debts-loans');
  await page.getByRole('button', { name: /فتح القرض .?Example car loan/ }).click();
  await expect(page.getByText(/وُجدت دفعة واحدة لـ .?Example car loan/)).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await shot(page, `bank-loan-ar-${page.viewportSize()?.width}`);
  expect(await horizontalOverflow(page)).toBe(0);
});
