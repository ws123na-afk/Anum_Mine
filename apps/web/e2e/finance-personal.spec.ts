import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { useSavedLocale } from './support/workspaceApi';

// Finance, Lent and borrowed, Payslips, Send home, Fixed costs and the Money advisor's Plan view
// (docs/finance.md#lent-and-borrowed onwards): add a loan and link a repayment, add a payslip and see the
// net against the salary credit, add a transfer home, fixed costs without double counting, the Plan's debt
// order, affordability, cash against a loan with a cited live rate, every state (empty with a next step,
// error with a retry, denied), Arabic and the responsive gate. The API is mocked at http://localhost:8000/**
// like the other specs; every figure is SYNTHETIC. ANUM only advises: nothing here moves money.

interface Mock { loans: Record<string, unknown>[]; payslips: Record<string, unknown>[]; remittances: Record<string, unknown>[]; asked: string[]; salary: Record<string, unknown> | null; decisions: Record<string, string> }
const stamp = '2026-11-01T00:00:00Z';

async function mockApi(page: Page, o: { empty?: boolean; fail?: number; denied?: boolean; search?: 'off' | 'on' } = {}): Promise<Mock> {
  const state = financeState({ denied: o.denied });
  let failures = o.fail ?? 0;
  const mock: Mock = {
    asked: [],
    loans: o.empty ? [] : [{ id: 'loan_1', direction: 'lent', person: 'Friend A', amount: '1000.00', currency: 'AED', lent_on: '2026-10-20', due_on: '2026-11-12',
      notes: null, remind: true, remind_days_before: 3, closed_on: null, repaid: '0.00', outstanding: '1000.00', handed: '1000.00', status: 'due_soon',
      days_until_due: 5, links: 1, created_at: stamp, updated_at: stamp }],
    payslips: [],
    remittances: [],
    salary: null,
    decisions: {},
  };
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
    const body = request.postData() ? request.postDataJSON() : null;
    const mine = /^\/(people-loans|payslips|remittances|fixed-commitments|planner|payment-candidates|salary)/.test(sub);
    if (mine) {
      mock.asked.push(`${request.method()} ${sub}`);
      if (o.denied) return json({ error: { code: 'forbidden', message: 'Finance is for workspace owners and members. Ask a workspace owner to change your role from viewer to member.', correlation_id: 'c', details: [] } }, 403);
      if (failures > 0 && request.method() === 'GET') { failures -= 1; return json({ error: { code: 'unavailable', message: 'The database is not reachable.', correlation_id: 'c', details: [] } }, 503); }
      if (sub === '/people-loans' && request.method() === 'GET') {
        const owed = mock.loans.length ? [{ currency: 'AED', they_owe_me: mock.loans.filter((l) => l.direction === 'lent').reduce((s, l) => s + Number(l.outstanding), 0).toFixed(2), i_owe: '0.00' }] : [];
        return json({ items: mock.loans, total: mock.loans.length, limit: 20, offset: 0, owed, overdue: 0, note: 'x' });
      }
      if (sub === '/people-loans' && request.method() === 'POST') {
        const made = { ...body, id: `loan_${mock.loans.length + 1}`, amount: Number(body.amount).toFixed(2), currency: body.currency || 'AED', closed_on: null, repaid: '0.00',
          outstanding: Number(body.amount).toFixed(2), handed: '0.00', status: 'open', days_until_due: null, links: 0, created_at: stamp, updated_at: stamp };
        mock.loans.unshift(made);
        return json(made, 201);
      }
      const loan = /^\/people-loans\/([^/]+)(\/links)?$/.exec(sub);
      if (loan) {
        const found = mock.loans.find((l) => l.id === loan[1]);
        if (!found) return json({ error: { code: 'not_found', message: 'Loan not found.', correlation_id: 'c', details: [] } }, 404);
        if (loan[2] && request.method() === 'POST') {
          found.repaid = '400.00'; found.outstanding = '600.00'; found.status = 'open';
          return json({ link: { id: 'link_r1', canonical_id: body.canonical_id, dimension: 'person_loan', role: 'deposit', target_type: 'finance_person_loan', target_id: found.id,
            target_workspace_id: null, amount: '400.00', currency: 'AED', status: 'active', status_reason: null, source_event_ids: [], document_id: null, created_by: 'user_local',
            created_at: stamp, updated_at: stamp, voided_at: null, version: 1 }, replayed: false }, 201);
        }
        if (request.method() === 'DELETE') { mock.loans = mock.loans.filter((l) => l !== found); return route.fulfill({ status: 204 }); }
        const movements = Number(found.repaid) > 0 ? [{ link: { id: 'link_r1', role: 'deposit', amount: '400.00', currency: 'AED', status: 'active' }, posted_on: '2026-11-04', description: 'From Friend A', account_id: 'fin_acct_current' }] : [];
        return json({ ...found, movements });
      }
      if (sub === '/salary/profile' && request.method() === 'GET') return json({ profile: mock.salary });
      if (sub === '/salary/profile' && request.method() === 'PUT') {
        mock.salary = { ...body, id: 'sal_1', currency: body.currency || 'SAR', account_id: body.account_id ?? null, rules: [], created_at: stamp, updated_at: stamp,
          expected_gross: Number(body.expected_gross).toFixed(2), expected_net: (Number(body.expected_gross) - 450).toFixed(2), next_pay_on: '2026-11-27', advances: [] };
        return json(mock.salary);
      }
      if (sub === '/salary/months') {
        if (!mock.salary) return json({ currency: null, months: [], total: 0, missing: 0, waiting: 0, limit: 12, offset: 0 });
        const expected = { gross: '10000.00', deductions: [{ reason: 'social_insurance', label: 'GOSI', amount: '450.00' }], advance_repayment: '0.00', net: '9550.00', payslip: false };
        const september = mock.decisions['2026-09'];
        const months = [
          { month: '2026-11', currency: 'SAR', pay_on: '2026-11-27', status: 'upcoming', flags: [], expected, received: '0.00', difference: '-9550.00', found: null, basis: null, rejected: 0 },
          { month: '2026-10', currency: 'SAR', pay_on: '2026-10-27', status: 'missing', flags: ['missing'], expected, received: '0.00', difference: '-9550.00', found: null, basis: null, rejected: 0 },
          september === 'reject'
            ? { month: '2026-09', currency: 'SAR', pay_on: '2026-09-27', status: 'missing', flags: ['missing'], expected, received: '0.00', difference: '-9550.00', found: null, basis: null, rejected: 1 }
            : { month: '2026-09', currency: 'SAR', pay_on: '2026-09-27', status: 'received', flags: [], expected, received: '9550.00', difference: '0.00', rejected: 0,
              basis: september === 'confirm' ? 'confirmed' : 'suggested',
              found: { transaction_id: 'tx_sep', posted_on: '2026-09-27', amount: '9550.00', description: 'Example Trading Co salary', account_id: 'fin_acct_current', confidence: 'high', score: 100 } },
        ];
        return json({ currency: 'SAR', months, total: 3, missing: months.filter((m) => m.status === 'missing').length, waiting: september ? 0 : 1, limit: 12, offset: 0 });
      }
      const decision = /^\/salary\/months\/([0-9-]+)\/decision$/.exec(sub);
      if (decision) { mock.decisions[decision[1]] = request.method() === 'DELETE' ? '' : body.decision; return json(mock.salary); }
      if (sub === '/payment-candidates') return json({ items: [{ canonical_id: 'je_in_1', posted_on: '2026-11-04', merchant: 'From Friend A', category: 'income', account_id: 'fin_acct_current', amount: '400.00', currency: 'AED', remaining: '400.00' }] });
      if (sub === '/payslips' && request.method() === 'GET') {
        const months = mock.payslips.map((s) => ({ month: s.month, currency: 'AED', gross: s.gross, deductions: s.total_deductions, net: s.net, received: '0.00', by_kind: s.by_kind, payslips: 1 }));
        return json({ items: mock.payslips, total: mock.payslips.length, limit: 20, offset: 0, months });
      }
      if (sub === '/payslips' && request.method() === 'POST') {
        const total = (body.deductions as { amount: string }[]).reduce((s, d) => s + Number(d.amount), 0);
        const made = { ...body, id: 'slip_1', currency: 'AED', paid_on: null, notes: null, gross: Number(body.gross).toFixed(2), total_deductions: total.toFixed(2),
          net: (Number(body.gross) - total).toFixed(2), received: '0.00', difference: (total - Number(body.gross)).toFixed(2), status: 'not_linked',
          by_kind: Object.fromEntries((body.deductions as { kind: string; amount: string }[]).map((d) => [d.kind, Number(d.amount).toFixed(2)])), created_at: stamp, updated_at: stamp };
        mock.payslips.unshift(made);
        return json(made, 201);
      }
      if (sub === '/remittances' && request.method() === 'GET') {
        const providers = mock.remittances.length ? [{ provider: 'Exchange A', corridor: 'AED>INR', transfers: 1, average_fee_percent: '2.00', best_effective_rate: '22.058824', latest_effective_rate: '22.058824', latest_on: '2026-11-05' }] : [];
        const months = mock.remittances.length ? [{ month: '2026-11', currency: 'AED', sent: '1000.00', fees: '20.00', count: 1, received: { INR: '22500.00' } }] : [];
        return json({ items: mock.remittances, total: mock.remittances.length, limit: 20, offset: 0, months, providers });
      }
      if (sub === '/remittances' && request.method() === 'POST') {
        const made = { ...body, id: 'remit_1', currency: 'AED', amount: '1000.00', fee: '20.00', fx_rate: '22.5', received_amount: '22500.00', received_currency: 'INR',
          total_paid: '1020.00', fee_percent: '2.00', effective_rate: '22.058824', linked: '0.00', notes: null, created_at: stamp, updated_at: stamp };
        mock.remittances.unshift(made);
        return json(made, 201);
      }
      if (sub === '/fixed-commitments') {
        if (o.empty) return json({ month: '2026-11', items: [], not_counted: [], totals: [], note: 'x' });
        return json({ month: '2026-11', note: 'x', totals: [{ currency: 'AED', amount: '5180.00' }],
          items: [{ kind: 'rent', name: 'Flat rent', currency: 'AED', monthly: '4000.00', source: 'bill', ref_id: 'b1', note: null },
            { kind: 'installment', name: 'Tamara: Phone Shop', currency: 'AED', monthly: '500.00', source: 'installment', ref_id: 't1', note: null },
            { kind: 'card_minimum', name: 'Rewards card', currency: 'AED', monthly: '680.00', source: 'debt', ref_id: 'd1', note: null }],
          not_counted: [{ kind: 'loan_minimum', name: 'Car loan', currency: 'AED', monthly: '1500.00', source: 'debt', ref_id: 'd2', note: 'in_bill' }] });
      }
      if (sub === '/planner/payoff') return json({ note: 'x', currencies: [{ currency: 'AED', payment: '300.00', planned_payment: null, below_minimums: false, interest_saved: '3.06',
        first_win_months: 1, missing_rates: ['Loan B'], cheaper: 'avalanche',
        debts: [{ id: 'a', name: 'Card A', balance: '1200.00', apr_percent: '12', minimum: '100.00', avalanche_month: 5, snowball_month: 5 },
          { id: 'b', name: 'Loan B', balance: '200.00', apr_percent: '0', minimum: '50.00', avalanche_month: 4, snowball_month: 1 }],
        avalanche: { strategy: 'avalanche', months: 5, total_interest: '35.97', order: ['a', 'b'] }, snowball: { strategy: 'snowball', months: 5, total_interest: '39.03', order: ['b', 'a'] } }] });
      if (sub === '/planner/afford') return json({ currency: 'AED', item: body.item, price: '500.00', monthly_cost: null, safe_to_spend: '2400.00', liquid: '15349.50',
        income_monthly: '15000.00', commitments_monthly: '5180.00', essential_monthly: '6000.00', ratio_now: '0.35', ratio_after: '0.35', months_left_after_cash: '2.5',
        verdict: 'yes_from_cash', reasons: ['within_safe_to_spend'], pay_with: [{ account_id: 'fin_acct_current', name: 'Everyday account', kind: 'bank', currency: 'AED', balance: '15349.50', note: 'covers' }],
        avoid: [{ code: 'late_fees', currency: 'AED', params: { amount: '100.00', count: '2' } }], note: 'x' });
      if (sub === '/planner/research') {
        if (o.search === 'off') return json({ status: 'unavailable', query: 'q', read_on: '2026-11-07', items: [], message: 'Live prices are unavailable', note: 'x' });
        return json({ status: 'ok', query: 'q', read_on: '2026-11-07', message: '', note: 'x',
          items: [{ title: 'Car finance from 4.5% flat', url: 'https://bank.example/rates', read_on: '2026-11-07', figures: [{ kind: 'percent', value: '4.5', currency: null }] }] });
      }
      if (sub === '/planner/compare') {
        const loanOption = body.options[1];
        return json({ currency: 'AED', price: '12000.00', note: 'x', options: [
          { kind: 'cash', label: 'Cash', upfront: '12000.00', monthly: null, months: 0, total_cost: '12000.00', interest: '0', extra_over_cash: '0.00', rate_percent: null, basis: 'reducing', rate_source: 'owner', rate_read_on: null, status: 'ok' },
          { kind: 'bank_loan', label: 'Bank loan', upfront: '0.00', monthly: '1066.19', months: 12, total_cost: '12794.28', interest: '794.28', extra_over_cash: '794.28', rate_percent: loanOption.rate_percent,
            basis: 'reducing', rate_source: loanOption.rate_source ?? 'owner', rate_read_on: loanOption.rate_read_on ?? null, status: 'ok' },
          { kind: 'installments', label: 'Installments', upfront: '0.00', monthly: null, months: 4, total_cost: null, interest: null, extra_over_cash: null, rate_percent: null, basis: 'flat', rate_source: 'owner', rate_read_on: null, status: 'rate_unknown' }] });
      }
    }
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return mock;
}

const card = (page: Page, name: string | RegExp) => page.getByRole('region', { name });

test('lent and borrowed: add a loan, link a repayment and see what is left', async ({ page }) => {
  const mock = await mockApi(page);
  // The old address still works: Lent and borrowed is now the People section of Debts and goals.
  await page.goto('/#finance-people');
  await expect(page).toHaveURL(/#finance-debts-people$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Debts and goals' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'People' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByText('Not spending, not income', { exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Lent and borrowed totals' })).toContainText('1,000.00');
  await page.getByRole('button', { name: 'Add a loan' }).first().click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('I borrowed money (I owe them)').check();
  await dialog.getByLabel('Person').fill('Cousin B');
  await dialog.getByLabel('Amount').fill('500');
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Cousin B is saved.')).toBeVisible();
  expect(mock.asked).toContain('POST /people-loans');
  await page.getByRole('button', { name: 'Open Friend A and its linked transactions' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Link a repayment' }).click();
  await page.getByRole('button', { name: /Link From Friend A on/ }).click();
  await expect(page.getByRole('dialog', { name: 'Loan with Friend A' })).toContainText('600.00');
  expect(mock.asked).toContain('POST /people-loans/loan_1/links');
});

test('payslips: gross, deductions and the net preview, then the month summary', async ({ page }) => {
  await mockApi(page);
  // Payslips sit with the salary on the Money advisor (the old address opens them there).
  await page.goto('/#finance-payslips');
  await expect(page).toHaveURL(/#finance-advisor-salary$/);
  await expect(page.getByRole('tab', { name: 'Salary' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByText('No payslips yet')).toBeVisible();
  await page.getByRole('button', { name: 'Add a payslip' }).first().click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Employer').fill('Example Co');
  await dialog.getByLabel('Gross pay').fill('10000');
  await dialog.getByLabel('Amount').first().fill('975');
  await expect(dialog.getByText(/Net pay: .*9,025\.00/)).toBeVisible();
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(card(page, /Gross against net by month/)).toContainText('9,025.00');
  await expect(page.getByRole('cell', { name: 'Not linked' })).toBeVisible();
});

test('salary: add the profile, see each month expected against received, confirm a match and reject one', async ({ page }) => {
  const mock = await mockApi(page);
  await page.goto('/#finance-advisor-salary');
  await expect(page.getByText('No salary profile yet')).toBeVisible();
  await page.getByRole('button', { name: 'Add your salary profile' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Employer').fill('Example Trading Co');
  await dialog.getByLabel('Joining date').fill('2026-09-01');
  await dialog.getByLabel('Currency').fill('SAR');
  await dialog.getByLabel('Expected monthly salary (gross)').fill('10000');
  await dialog.getByLabel('Usual pay day').fill('27');
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('The salary profile is saved.')).toBeVisible();
  expect(mock.asked).toContain('PUT /salary/profile');
  await expect(page.getByRole('note').filter({ hasText: 'No salary found for October 2026, expected around October 27.' })).toBeVisible();
  const september = page.locator('.fsalMonth').filter({ hasText: 'September 2026' });
  await expect(september).toContainText('Likely match');
  await september.getByRole('button', { name: 'This is my salary' }).click();
  await expect(page.getByText('Salary for September 2026 confirmed.')).toBeVisible();
  await expect(september).toContainText('Confirmed by you');
  if (process.env.ANUM_E2E_SHOTS) {
    for (const [width, height] of [[1440, 900], [390, 844]] as const) {
      await page.setViewportSize({ width, height });
      await page.screenshot({ path: `${process.env.ANUM_E2E_SHOTS}/finance-salary-months-${width}x${height}.png`, fullPage: true });
    }
  }
  await september.getByRole('button', { name: 'Undo' }).click();
  await september.getByRole('button', { name: 'Not my salary' }).click();
  await expect(september).toContainText('Missing');
  expect(mock.asked).toContain('POST /salary/months/2026-09/decision');
});

test('send home: add a transfer and compare providers from your own records', async ({ page }) => {
  await mockApi(page);
  // Send home is a section of Goals, next to the savings for family at home.
  await page.goto('/#finance-remittances');
  await expect(page).toHaveURL(/#finance-goals-send-home$/);
  await expect(page.getByRole('tab', { name: 'Send home' })).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('button', { name: 'Add a transfer' }).first().click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Recipient').fill('Family');
  await dialog.getByLabel('Provider').fill('Exchange A');
  await dialog.getByLabel('Amount sent').fill('1000');
  await dialog.getByLabel('Received currency').fill('INR');
  await dialog.getByLabel('Exchange rate').fill('22.5');
  await expect(dialog.getByText(/22,500\.00/)).toBeVisible();
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(card(page, 'Your providers compared')).toContainText('Cheapest so far');
  await expect(card(page, 'Sent by month')).toContainText('20.00');
});

test('fixed costs: one total per currency and what is left out so nothing counts twice', async ({ page }) => {
  await mockApi(page);
  // Monthly fixed costs is a section of Bills and budgets.
  await page.goto('/#finance-commitments');
  await expect(page).toHaveURL(/#finance-bills-fixed$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Bills and budgets' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Monthly fixed costs' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('region', { name: 'Fixed costs per currency' })).toContainText('5,180.00');
  await expect(page.getByRole('region', { name: 'Rent' })).toContainText('Flat rent');
  await expect(card(page, 'Left out so nothing counts twice')).toContainText('already counted as a saved bill');
});

test('empty fixed costs offer the next step; an error offers a retry; denied explains who may', async ({ page }) => {
  await mockApi(page, { empty: true, fail: 1 });
  await page.goto('/#finance-bills-fixed');
  const alert = page.getByRole('alert').filter({ hasText: 'could not be loaded' });
  await expect(alert).toContainText('The database is not reachable.');
  await alert.getByRole('button', { name: 'Try again' }).click();
  await expect(page.getByText('No fixed costs yet')).toBeVisible();
  await page.getByRole('button', { name: 'Add a bill' }).click();
  await expect(page).toHaveURL(/#finance-bills$/);
  await expect(page.getByRole('tab', { name: 'Bills', exact: true })).toHaveAttribute('aria-selected', 'true');
});

test('permission denied on the personal money pages', async ({ page }) => {
  await mockApi(page, { denied: true });
  await page.goto('/#finance-debts-people');
  await expect(page.locator('.cmDenied')).toContainText(/owner|member/);
});

test('plan: which debt first, can I afford it, and cash against a loan with a cited live rate', async ({ page }) => {
  const mock = await mockApi(page);
  await page.goto('/#finance-advisor');
  await page.getByRole('tab', { name: 'Plan' }).click();
  await expect(page.getByText('Advice only. It never pays, buys or transfers.', { exact: false })).toBeVisible();
  const payoff = card(page, 'Which debt to pay first');
  await expect(payoff).toContainText('Pay Card A first (Avalanche): it saves');
  await expect(payoff).toContainText('No rate entered for Loan B');
  const afford = card(page, 'Can I afford it?');
  await afford.getByLabel('What (optional)').fill('Headphones');
  await afford.getByLabel('Price').fill('500');
  await afford.getByRole('button', { name: 'Check' }).click();
  await expect(afford).toContainText('Yes: it fits in this month\'s Safe to spend.');
  await expect(afford).toContainText('Late payments: you paid AED 100.00 in late fees 2 times.');
  await expect(afford.getByRole('button', { name: 'After you buy: record it in Purchases' })).toBeVisible();
  const compare = card(page, 'Cash, a loan or installments?');
  await compare.getByText('Look up current loan and financing rates').click();
  await compare.getByRole('button', { name: 'Look up' }).click();
  await compare.getByRole('button', { name: 'Use 4.5% as the loan rate' }).click();
  await expect(compare).toContainText('Using the rate read on https://bank.example/rates');
  await compare.getByLabel('Price').fill('12000');
  await compare.getByRole('button', { name: 'Compare' }).click();
  await expect(compare.getByRole('row', { name: /Bank loan/ })).toContainText('1,066.19');
  await expect(compare.getByRole('link', { name: /4\.5% read/ })).toHaveAttribute('href', 'https://bank.example/rates');
  await expect(compare.getByRole('row', { name: /Installments/ })).toContainText('Unknown: add the rate or look it up');
  expect(mock.asked).toContain('POST /planner/research');
});

test('plan: live prices unavailable says so and keeps your own figures', async ({ page }) => {
  await mockApi(page, { search: 'off' });
  await page.goto('/#finance-advisor');
  await page.getByRole('tab', { name: 'Plan' }).click();
  const afford = card(page, 'Can I afford it?');
  await afford.getByText('Look up current prices').click();
  await afford.getByRole('button', { name: 'Look up' }).click();
  await expect(afford).toContainText('Live prices are unavailable: web search is not set up here.');
});

test('Arabic: lent and borrowed reads right to left with figures left to right', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockApi(page);
  await page.goto('/#finance-debts-people');
  await expect(page.getByRole('heading', { level: 1, name: 'الديون والأهداف' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'الأشخاص' })).toHaveAttribute('aria-selected', 'true');
  expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
  await expect(page.locator('.finGrid .finMoney').first()).toHaveAttribute('dir', 'ltr');
});

const VIEWPORTS = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const [hash, ready] of [['#finance-debts-people', '.finGrid'], ['#finance-bills-fixed', '.fpmGroup'], ['#finance-advisor', '.fpmPayoff'], ['#finance-advisor-salary', '.fpmPage']] as const) {
  for (const [width, height] of VIEWPORTS) {
    test(`responsive @gate: ${hash} fits with no sideways scroll at ${width}x${height}`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      await mockApi(page);
      await page.goto(`/${hash}`);
      if (hash === '#finance-advisor') await page.getByRole('tab', { name: 'Plan' }).click();
      await expect(page.locator(ready).first()).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      const sticking = await page.locator('.finPage, .fplPage').first().evaluate((root) => [...root.querySelectorAll('*')].filter((e) => {
        const box = e.getBoundingClientRect();
        if (box.right <= document.documentElement.clientWidth + 1 && box.left >= -1) return false;
        for (let p = e.parentElement; p && p !== root.parentElement; p = p.parentElement) if (getComputedStyle(p).overflowX !== 'visible') return false;
        return true;
      }).map((e) => `${e.tagName}.${e.className}`));
      expect(sticking).toEqual([]);
      if (process.env.ANUM_E2E_SHOTS) await page.screenshot({ path: `${process.env.ANUM_E2E_SHOTS}/${hash.slice(1)}-${width}x${height}.png`, fullPage: true });
    });
  }
}
