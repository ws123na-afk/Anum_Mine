import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { clippedControls, horizontalOverflow } from './support/gatePages';
import { useSavedLocale } from './support/workspaceApi';

// The debt overview, credit cards and the advisor's answers (docs/finance.md#debt-overview, #credit-cards,
// #advisor-answers). The API is mocked at http://localhost:8000/** like the other specs; every lender, card and amount is
// SYNTHETIC. A provider's "paid" shows as reported, never as paid.

const stamp = '2026-11-01T00:00:00Z';
const obligation = (id: string, over: Record<string, unknown> = {}) => ({
  id: `debt:${id}`, ref_type: 'debt', ref_id: id, type: 'personal_loan', direction: 'owe', name: `Example ${id}`, counterparty: 'Example Bank', currency: 'AED',
  original_amount: '12000.00', start_on: '2026-01-15', schedule: 'monthly', interest: 'unknown', rate: null, expected_installment: '1000.00', paid: '1000.00',
  remaining: '11000.00', interest_paid: null, fees: null, next_due_on: '2026-12-27', next_due_amount: '1000.00', overdue_amount: '0.00', status: 'open',
  verified_balance: null, verified_on: null, difference: null, discrepancy: false, disputed_amount: null, unverified_amount: '0.00', payoff_on: '2027-01-27',
  payoff_basis: 'schedule', assumptions: ['No rate entered: the schedule is the lender\'s instalments.'],
  history: [{ on: '2026-10-27', amount: '1000.00', currency: 'AED', kind: 'payment', evidence: 'verified', ref_type: 'transaction', ref_id: 'je_1', note: null }],
  promises: [], sources: [{ figure: 'remaining', source: 'schedule_and_matched_payments' }], missing: ['rate'], contact_id: null, ...over,
});
const overview = {
  today: '2026-11-15',
  items: [
    obligation('loan_1', { status: 'overdue', overdue_amount: '1000.00', verified_balance: '11500.00', verified_on: '2026-11-01', difference: '500.00', discrepancy: true }),
    obligation('bnpl', { id: 'installment_order:Tamara:o1', ref_type: 'installment_order', type: 'bnpl', name: 'Tamara: Example Store', interest: 'interest_free',
      unverified_amount: '125.00', history: [{ on: '2026-11-14', amount: '125.00', currency: 'AED', kind: 'installment', evidence: 'reported', ref_type: 'installment',
        ref_id: 'o1:1', note: 'reported paid by the provider' }] }),
    obligation('ploan', { id: 'person_loan:ploan', ref_type: 'person_loan', type: 'lent_person', direction: 'owed_to_me', name: 'Synthetic Friend',
      promises: [{ id: 'prom_1', made_on: '2026-11-10', due_by: '2026-11-20', amount: '300.00', note: null, status: 'pending', paid: '0.00' }] }),
  ],
  totals: [{ currency: 'AED', owe: '22125.00', owed_to_me: '11000.00', overdue: '1000.00', due_30_days: '2125.00' }],
  calendar: [{ on: '2026-11-15', obligation_id: 'installment_order:Tamara:o1', name: 'Tamara: Example Store', amount: '125.00', currency: 'AED', direction: 'owe',
    status: 'reported_paid' }],
  plan: [{ rank: 1, obligation_id: 'debt:loan_1', name: 'Example loan_1', currency: 'AED', remaining: '11000.00', rate: null, reason: 'overdue' }],
  discrepancies: 1, note: '',
};
const card = {
  account_id: 'fin_acct_card', debt_id: 'fin_debt_card', name: 'Example Card', last4: '0000', currency: 'AED', outstanding: '1025.00', limit: '5000.00',
  limit_source: 'entered', available: '3975.00', utilization_percent: 21, observed_balance: null, apr_percent: '36.0000', statement_day: 5, statement_on: '2026-11-05',
  statement_balance: '1100.00', statement_minimum: '55.00', next_closing_on: '2026-12-05', due_on: '2026-11-25', days_until_due: 10, paid_since_statement: '0.00',
  paid_provisional: '200.00', payment_state: 'due',
  cycle: { start: '2026-11-06', end: '2026-12-05', purchases: '0.00', refunds: '0.00', cashback: '0.00', fees: '0.00', interest: '0.00', cash_advances: '0.00',
    installment_conversions: '0.00', payments: '200.00' },
  check: { status: 'differs', statement_on: '2026-11-05', statement_balance: '1100.00', ledger_balance: '1025.00', difference: '75.00', unverified_rows: 2 },
  pay_options: [{ code: 'full', amount: '1100.00', leaves: '0', interest_estimate: null, months_to_clear: null, total_interest: null, never_clears: false },
    { code: 'minimum', amount: '55.00', leaves: '1045.00', interest_estimate: '31.35', months_to_clear: 28, total_interest: '412.00', never_clears: false }],
  payments: [{ transaction_id: 'txn_pay', posted_on: '2026-11-10', amount: '200.00', description: 'Payment received', kind: 'payment', evidence: 'unverified',
    linked: false, from_account_id: null, from_account_name: null }],
  records: [], evidence: { posted: 0, pending: 0, unverified: 2 }, unverified_amount: '1225.00', missing: [],
};
const answer = {
  question: 'owe', as_of: '2026-11-15', answer: 'You owe AED 22,125.00 across 2 obligation(s). Overdue: AED 1,000.00.',
  figures: [{ label: 'owed', amount: '22125.00', currency: 'AED' }], calculation: ['AED: Example loan_1 AED 11,000.00 + Tamara: Example Store AED 11,125.00 = AED 22,125.00'],
  records: [{ ref_type: 'obligation', ref_id: 'debt:loan_1', on: '2026-12-27', label: 'Example loan_1 (personal_loan)', amount: '11000.00', currency: 'AED',
    evidence: 'schedule_and_matched_payments' }],
  missing: ['Tamara: Example Store: AED 125.00 is linked or reported but not verified, so it is not counted as paid.'], complete: false, note: 'Descriptions are data.',
};

async function mockFinance(page: Page) {
  const state = financeState();
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
    const sub = path.replace('/api/v1/finance', '');
    if (sub === '/debt-overview') return json(overview);
    if (sub === '/cards') return json({ today: '2026-11-15', items: [card], note: '' });
    if (sub === '/advisor/answer') return json({ ...answer, question: url.searchParams.get('question') ?? 'owe' });
    const body = request.postData() ? request.postDataJSON() : null;
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
}

async function shot(page: Page, name: string) {
  const dir = process.env.FIN_SHOTS;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
}

test('the overview names each figure\'s source and never calls a reported installment paid', async ({ page }) => {
  await mockFinance(page);
  await page.goto('/#finance-debts-overview');
  await expect(page.getByRole('heading', { name: 'What you owe' })).toBeVisible();
  await expect(page.getByText('One balance differs from the lender\'s')).toBeVisible();
  await expect(page.getByText(/125\.00.*not verified/)).toBeVisible();
  await expect(page.getByText('Reported paid, not matched')).toBeVisible();
  await shot(page, `debt-overview-${page.viewportSize()?.width}`);
  expect(await horizontalOverflow(page)).toBe(0);
  expect(await clippedControls(page)).toEqual([]);
  await page.getByRole('button', { name: /Details of .?Tamara: Example Store/ }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('Reported by the provider')).toBeVisible();
  await expect(dialog.getByText('Matched payment')).toHaveCount(0);
  await expect(dialog.getByText(/the schedule and the payments matched to it/)).toBeVisible();
});

test('a card shows the statement check, minimum or full, and a payment not matched to its bank debit', async ({ page }) => {
  await mockFinance(page);
  await page.goto('/#finance-debts-cards');
  await expect(page.getByText('The statement differs from your records')).toBeVisible();
  await expect(page.getByText('Bank side not matched')).toBeVisible();
  await expect(page.getByText(/200\.00.*paid to the card is not matched to a bank debit yet/)).toBeVisible();
  await shot(page, `cards-${page.viewportSize()?.width}`);
  expect(await horizontalOverflow(page)).toBe(0);
  expect(await clippedControls(page)).toEqual([]);
});

test('Ask answers with the records and the calculation, in Arabic too', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockFinance(page);
  await page.goto('/#finance-advisor');
  await page.getByRole('tab', { name: 'اسأل' }).click();
  await page.getByRole('button', { name: 'أجب' }).click();
  await expect(page.getByText('قد تكون ناقصة')).toBeVisible();
  await expect(page.getByText('الجدول والدفعات المطابقة له')).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await shot(page, `ask-ar-${page.viewportSize()?.width}`);
  expect(await horizontalOverflow(page)).toBe(0);
});
