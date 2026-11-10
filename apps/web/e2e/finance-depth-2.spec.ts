import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { GATE_SIZES, clippedControls, horizontalOverflow } from './support/gatePages';
import { useSavedLocale } from './support/workspaceApi';

// A repayment split across loans (the suggestion and the split dialog), foreign balances in the base currency on the
// overview and the account page, and personal trips and projects (docs/finance.md#lent-and-borrowed,
// #foreign-currency-balances, #trips-and-projects). The API is mocked at http://localhost:8000/** like the other specs; every
// person, account and amount is SYNTHETIC. Nothing is linked or tagged until the person presses the button.

const stamp = '2026-11-01T00:00:00Z';
const loan = {
  id: 'loan_1', direction: 'lent', person: 'Example Cousin', amount: '300.00', currency: 'AED', lent_on: '2026-08-01', due_on: '2026-11-05', notes: null,
  remind: true, remind_days_before: 3, closed_on: null, interest_rate: null, installments: null, first_due_on: null, repaid: '0.00', outstanding: '300.00',
  handed: '300.00', status: 'overdue', days_until_due: -5, interest: '0.00', total_due: '300.00', next_due_on: '2026-11-05', next_due_amount: '300.00',
  overdue_amount: '300.00', links: 1, created_at: stamp, updated_at: stamp,
};
const second = { ...loan, id: 'loan_2', amount: '200.00', lent_on: '2026-09-01', due_on: '2026-11-20', outstanding: '200.00', status: 'due_soon', days_until_due: 10,
  next_due_on: '2026-11-20', next_due_amount: '200.00', overdue_amount: '0.00', total_due: '200.00' };
const part = { canonical_id: 'je_9', dimension: 'person_loan', target_type: 'finance_person_loan', amount: '300.00', currency: 'AED', role: 'deposit',
  treatment: null, category: null, project_id: null };
const split = {
  key: 'loan_repayment:tx_9:split:loan_1,loan_2', producer: 'person_loans', kind: 'loan_repayment', detected: '', why: '',
  params: { person: 'Example Cousin', direction: 'lent', currency: 'AED', amount: '500.00', outstanding: '500.00', split: '2' },
  evidence: [{ label: 'Transfer from EXAMPLE COUSIN', ref_type: 'transaction', ref_id: 'tx_9', value: '2026-11-09 AED 500.00' },
    { label: 'Example Cousin 2026-08-01', ref_type: 'other', ref_id: 'loan_1', value: 'AED 300.00' },
    { label: 'Example Cousin 2026-09-01', ref_type: 'other', ref_id: 'loan_2', value: 'AED 200.00' }],
  confidence: '0.85', effect: [{ amount: '500.00', currency: 'AED', direction: 'in', note: null }],
  action: { kind: 'link', transaction_ids: ['tx_9'], category: null, subcategory_id: null, transaction_type: null, link: null,
    parts: [{ ...part, target_id: 'loan_1' }, { ...part, target_id: 'loan_2', amount: '200.00' }] },
  href: '#finance-debts-people', occurred_on: '2026-11-09', payload_hash: 'b'.repeat(64),
};
const usd = { id: 'fin_acct_usd', name: 'Example dollar wallet', kind: 'bank', currency: 'USD', institution: 'Example Bank', last4: '5555', opening_balance: '0.00',
  balance: '1000.00', archived: false, transaction_count: 2, created_at: stamp, updated_at: stamp };
const eur = { ...usd, id: 'fin_acct_eur', name: 'Example euro savings', currency: 'EUR', last4: '6666', balance: '400.00' };
const revaluation = {
  base_currency: 'AED', as_of: '2026-11-10', rate_max_age_days: 7, unrealised_total: '100.00', incomplete: 1, truncated: false, note: 'Display only.',
  items: [
    { account_id: usd.id, name: usd.name, currency: 'USD', base_currency: 'AED', balance: '1000.00', status: 'ok', value: '3700.00', rate: '3.7000000000',
      rate_as_of: '2026-11-09', rate_source: 'manual', cost: '3600.00', unrealised: '100.00', missing_rate_dates: [] },
    { account_id: eur.id, name: eur.name, currency: 'EUR', base_currency: 'AED', balance: '400.00', status: 'rate_not_available', value: null, rate: null,
      rate_as_of: null, rate_source: null, cost: null, unrealised: null, missing_rate_dates: [] },
  ],
};
const trip = { id: 'fin_tag_1', kind: 'trip', name: 'Example Muscat trip', starts_on: '2026-11-01', ends_on: '2026-11-04', keywords: 'Muscat', notes: null,
  archived: false, totals: { AED: '420.00' }, links: 1 };

async function mockFinance(page: Page) {
  const state = financeState();
  state.accounts.push(usd, eur);
  const sent: { method: string; path: string; body: unknown }[] = [];
  let tags = [trip];
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
      return json({ items: [loan, second], total: 2, limit: 20, offset: 0, overdue: 1, note: 'x',
        owed: [{ currency: 'AED', they_owe_me: '500.00', i_owe: '0.00', they_owe_me_overdue: '300.00', i_owe_overdue: '0.00' }] });
    }
    if (sub === '/people-loans/loan_1') return json({ ...loan, movements: [], schedule: [] });
    if (sub === '/people-loans/split') return json({ links: [{ id: 'link_a' }, { id: 'link_b' }], replayed: false }, 201);
    if (sub === '/payment-candidates') {
      return json({ items: [{ canonical_id: 'je_9', posted_on: '2026-11-09', merchant: 'Transfer from EXAMPLE COUSIN', category: 'other', account_id: 'fin_acct_current',
        amount: '500.00', currency: 'AED', remaining: '500.00' }] });
    }
    if (sub === '/suggestions' && request.method() === 'GET') {
      const done = sent.some((s) => s.path.endsWith('/suggestions/accept'));
      return json({ items: done ? [] : [split], total: done ? 0 : 1, limit: 25, offset: 0, counts: done ? [] : [{ kind: 'loan_repayment', count: 1 }], unavailable: [], dismissed: 0 });
    }
    if (sub === '/suggestions/accept') return json({ key: split.key, status: 'accepted', batch: null, link_id: 'link_a', link_ids: ['link_a', 'link_b'] });
    if (sub === '/fx-revaluation') {
      const id = url.searchParams.get('account_id');
      return json(id ? { ...revaluation, items: revaluation.items.filter((r) => r.account_id === id) } : revaluation);
    }
    if (sub === '/tags' && request.method() === 'GET') return json({ items: tags, total: tags.length, limit: 20, offset: 0, note: 'x' });
    if (sub === '/tags' && request.method() === 'POST') {
      const made = { ...trip, ...body, id: 'fin_tag_2', totals: {}, links: 0 };
      tags = [made, ...tags];
      return json(made, 201);
    }
    if (sub === '/tags/fin_tag_1' && request.method() === 'GET') {
      return json({ ...trip, movements: [{ link: { id: 'link_t', canonical_id: 'je_5', dimension: 'trip', role: 'payment', target_type: 'finance_tag', target_id: trip.id,
        amount: '420.00', currency: 'AED', status: 'active' }, posted_on: '2026-11-02', description: 'Example Muscat hotel', account_id: 'fin_acct_current' }] });
    }
    if (sub === '/tags/fin_tag_1/links') return json({ link: { id: 'link_u' }, replayed: false }, 201);
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { state, sent };
}

test('a split repayment is proposed with one part per loan and accepted only on Link', async ({ page }) => {
  const { sent } = await mockFinance(page);
  await page.goto('/#finance-suggestions');
  const card = page.getByRole('listitem').filter({ hasText: 'over several loans' });
  await expect(card.getByRole('heading')).toContainText(/Example Cousin repaying .*500\.00.* over several loans\?/);
  await expect(card).toContainText(/300\.00.* to the loan of .*2026-08-01/);
  await expect(card).toContainText(/200\.00.* to the loan of .*2026-09-01/);
  expect(sent.filter((s) => s.path.endsWith('/accept'))).toEqual([]);
  await card.getByRole('button', { name: 'Link' }).click();
  await expect(page.getByText(/Linked/).first()).toBeVisible();
  expect(sent.find((s) => s.path.endsWith('/suggestions/accept'))?.body).toMatchObject({ key: split.key, payload_hash: split.payload_hash });
});

test('the split dialog checks the parts and links them in one request', async ({ page }) => {
  const { sent } = await mockFinance(page);
  await page.goto('/#finance-debts-people');
  await page.getByRole('button', { name: 'Open Example Cousin' }).first().click();
  await page.getByRole('button', { name: 'Split one payment across loans' }).click();
  const dialog = page.getByRole('dialog', { name: /Split a payment across loans with Example Cousin/ });
  await dialog.getByRole('button', { name: /Transfer from EXAMPLE COUSIN/ }).click();
  await expect(dialog).toContainText(/Parts: .*500\.00.* of .*500\.00/);
  const first = dialog.getByRole('textbox', { name: /Amount for the loan of/ }).first();
  await first.fill('350');
  await expect(dialog.getByText('A part is more than what is left on its loan.')).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Link the parts' })).toBeDisabled();
  await first.fill('300');
  await dialog.getByRole('button', { name: 'Link the parts' }).click();
  await expect(page.getByText('Linked 2 parts.')).toBeVisible();
  expect(sent.find((s) => s.path.endsWith('/people-loans/split'))?.body).toEqual({ canonical_id: 'je_9', kind: 'repayment',
    parts: [{ loan_id: 'loan_1', amount: '300' }, { loan_id: 'loan_2', amount: '200.00' }] });
});

test('foreign balances show the gain, and a missing rate is said, never guessed', async ({ page }) => {
  await mockFinance(page);
  await page.goto('/#finance-overview');
  const card = page.getByRole('region', { name: 'Foreign balances in AED' });
  await expect(card).toContainText('Example dollar wallet');
  await expect(card).toContainText(/Worth now.*3,700\.00/);
  await expect(card).toContainText(/Unrealised gain or loss.*100\.00/);
  await expect(card.getByText('Rate not available')).toBeVisible();
  await expect(card).toContainText('1 account has a missing rate and is left out of the total.');
  await page.goto('/#finance-accounts');
  await page.getByRole('button', { name: /Example dollar wallet/ }).first().click();
  await expect(page.getByRole('region', { name: 'Foreign balances in AED' })).toContainText(/1 USD = .*3\.7.* AED/);
});

test('a trip is added and a payment tagged to it from Trips and projects', async ({ page }) => {
  const { sent } = await mockFinance(page);
  await page.goto('/#finance-goals-trips');
  const list = page.getByRole('region', { name: 'Trips and projects' });
  await expect(list.getByRole('row', { name: /Example Muscat trip/ })).toContainText(/420\.00/);
  await list.getByRole('button', { name: 'Add a trip or project' }).click();
  const form = page.getByRole('dialog');
  await form.getByLabel('Name').fill('Example Riyadh trip');
  await form.getByLabel('From', { exact: true }).fill('2026-12-05');
  await form.getByLabel('To', { exact: true }).fill('2026-12-01');
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(form.getByText('The end date is before the start date.')).toBeVisible();
  await form.getByLabel('To', { exact: true }).fill('2026-12-08');
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Saved Example Riyadh trip.')).toBeVisible();
  expect(sent.find((s) => s.path.endsWith('/finance/tags'))?.body).toMatchObject({ kind: 'trip', name: 'Example Riyadh trip', starts_on: '2026-12-05', ends_on: '2026-12-08' });
  await list.getByRole('button', { name: 'Payments of Example Muscat trip' }).click();
  const detail = page.getByRole('dialog', { name: 'Example Muscat trip' });
  await expect(detail).toContainText('Example Muscat hotel');
  await detail.getByRole('button', { name: 'Tag a payment' }).click();
  await page.getByRole('dialog', { name: 'Tag a payment to Example Muscat trip' }).getByRole('button', { name: /Transfer from EXAMPLE COUSIN/ }).click();
  expect(sent.find((s) => s.path.endsWith('/tags/fin_tag_1/links'))?.body).toEqual({ canonical_id: 'je_9' });
});

test('@gate the split dialog, foreign balances and trips fit at five sizes in English and Arabic', async ({ page }, info) => {
  test.skip(info.project.name === 'mobile', 'the desktop project sets every size itself');
  test.setTimeout(240_000);
  for (const locale of ['en', 'ar'] as const) {
    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await useSavedLocale(page, locale);
    await mockFinance(page);
    for (const size of GATE_SIZES) {
      await page.setViewportSize(size);
      const where = `${locale} ${size.width}x${size.height}`;
      await page.goto('/#finance-overview');
      await page.reload();
      await expect(page.locator('.fdepth2Fx')).toBeVisible();
      expect(await horizontalOverflow(page), `${where} overview`).toBeLessThanOrEqual(0);
      expect(await clippedControls(page), `${where} overview`).toEqual([]);
      await page.goto('/#finance-goals-trips');
      await page.reload();
      await expect(page.locator('.fdepth2Tags table')).toBeVisible();
      expect(await horizontalOverflow(page), `${where} trips`).toBeLessThanOrEqual(0);
      expect(await clippedControls(page), `${where} trips`).toEqual([]);
      await page.goto('/#finance-debts-people');
      await page.reload();
      await page.locator('.fpmRowActions button').first().click();
      await page.locator('.fpmDialog .actions button.secondary').last().click();
      await page.locator('.fpmCandidates button').first().click();
      await expect(page.locator('.fpmSplitLines input')).toHaveCount(2);
      expect(await horizontalOverflow(page), `${where} split`).toBeLessThanOrEqual(0);
      expect(await clippedControls(page), `${where} split`).toEqual([]);
    }
  }
});
