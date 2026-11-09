import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { useSavedLocale } from './support/workspaceApi';

// Tamara refunds on the installment statement (docs/finance.md#tamara-refunds): the server finds the
// evidence and the new schedule; the page shows it per order and saves it only on "Apply refunds".
// The API is mocked at http://localhost:8000/** like the other Finance specs. Fixtures are plainly fake.
const HASH = 'a'.repeat(64);
const email = (id: string, code: string, amount: string | null) => ({ id: `email:${id}`, kind: 'email', strength: code === 'email_no_amount' ? 'weak' : 'strong',
  role: code === 'email_no_amount' ? 'review' : 'counted', code, amount, currency: 'SAR', on: '2026-11-02', installment: null,
  link: `https://mail.google.com/mail/u/0/#all/${id}`, transaction_id: null });
const inst = (n: number, state: string, before: string, after: string, estimate: string | null = null) => ({
  installment: n, total_installments: 4, due_on: `2026-${String(10 + n).padStart(2, '0')}-01`.replace('2026-13', '2027-01').replace('2026-14', '2027-02'),
  amount: '100.00', source_status: n === 1 ? 'paid' : 'scheduled', refunded_before: '0.00', refunded_after: n === 1 ? '0.00' : (Number(before) - Number(after)).toFixed(2),
  due_before: n === 1 ? '0.00' : before, due_after: n === 1 ? '0.00' : after, estimate_due: estimate, state_before: n === 1 ? 'paid' : 'due', state_after: state,
});

function fullRefund(applied: boolean) {
  return {
    provider: 'Tamara', order_id: 'example-order-a', merchant: 'Example Shop', currency: 'SAR', state: 'refunded', applied_state: applied ? 'refunded' : 'active',
    mode: 'cancel_all', applicable: !applied, pending: !applied, order_total: '400.00', paid_total: '100.00', refund_total: '400.00', credit_back: '100.00',
    credit_received: '0.00', outstanding_before: applied ? '0.00' : '300.00', outstanding_after: '0.00', next_due_before: applied ? null : '2026-12-01',
    next_due_after: null, evidence: [email('m-full', 'email_full', '400.00')],
    installments: [inst(1, 'paid', '0.00', '0.00'), inst(2, 'refunded', applied ? '0.00' : '100.00', '0.00'), inst(3, 'refunded', applied ? '0.00' : '100.00', '0.00'),
      inst(4, 'refunded', applied ? '0.00' : '100.00', '0.00')],
    reasons: [], plan_hash: HASH,
  };
}
const estimate = {
  provider: 'Tamara', order_id: 'example-order-b', merchant: 'Sample Store', currency: 'SAR', state: 'needs_review', applied_state: 'active', mode: 'estimate',
  applicable: false, pending: false, order_total: '400.00', paid_total: '100.00', refund_total: '150.00', credit_back: '0.00', credit_received: '0.00',
  outstanding_before: '300.00', outstanding_after: '300.00', next_due_before: '2026-12-01', next_due_after: '2026-12-01',
  evidence: [email('m-part', 'email_amount', '150.00')],
  installments: [inst(1, 'paid', '0.00', '0.00'), inst(2, 'due', '100.00', '100.00', '50.00'), inst(3, 'due', '100.00', '100.00', '50.00'), inst(4, 'due', '100.00', '100.00', '50.00')],
  reasons: ['estimate_only'], plan_hash: 'b'.repeat(64),
};

export async function mockRefunds(page: Page, options: { empty?: boolean; fail?: boolean; stale?: boolean } = {}) {
  const state = financeState({});
  const sent: { method: string; path: string; body: unknown }[] = [];
  let applied = false;
  const record = { id: 'fin_refund_1', provider: 'Tamara', order_id: 'example-order-a', currency: 'SAR', applied_at: '2026-11-09T10:00:00Z', refund_total: '400.00',
    credit_back: '100.00', installments: 3, bills: 3, undone: false, can_undo: true };
  const preview = () => options.empty ? { today: '2026-11-09', orders: [], pending: 0, needs_review: 0, records: [] }
    : { today: '2026-11-09', orders: [fullRefund(applied), estimate], pending: applied ? 0 : 1, needs_review: 1, records: applied ? [record] : [] };
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
    const body = request.postData() ? request.postDataJSON() : null;
    if (request.method() !== 'GET') sent.push({ method: request.method(), path, body });
    if (path === '/api/v1/finance/installment-statement') return json({ items: [], total: 0 });
    // A Gmail read that finds only a refund email (no new schedule rows).
    if (path === '/api/v1/finance/installment-email/mailboxes') return json([{ id: 'gmail_example', address: 'owner@example.com', selected: false }]);
    if (path === '/api/v1/email/accounts/gmail_example/sync') return json({ id: 'gmail_example', address: 'owner@example.com', provider: 'gmail', status: 'connected' });
    if (path === '/api/v1/finance/installment-email/preview') return json({ account_id: 'gmail_example', address: 'owner@example.com', messages_checked: 1, messages_matched: 1,
      warnings: [], rows: [], refunds: [{ provider: 'Tamara', order_id: 'example-order-a', amount: '400.00', currency: 'SAR', full: true, received_on: '2026-11-02',
        message_ref: 'm-full', source_email: 'https://mail.google.com/mail/u/0/#all/m-full' }] });
    if (path === '/api/v1/finance/installment-statement/sync' && request.method() === 'POST')
      return json({ created: 0, updated: 0, unchanged: 0, conflicts: [], total: 0, refund_signals: [], notices_saved: 1 });
    if (path === '/api/v1/finance/installment-refunds' && request.method() === 'GET') {
      if (options.fail) { options.fail = false; return json({ error: { code: 'internal_error', message: 'The database is not reachable.', correlation_id: 'c', details: [] } }, 503); }
      return json(preview());
    }
    if (path === '/api/v1/finance/installment-refunds/apply' && request.method() === 'POST') {
      if (options.stale) { options.stale = false; return json({ error: { code: 'conflict', message: 'The refund evidence for this order changed. Reload and check it again.', correlation_id: 'c', details: [] } }, 409); }
      applied = true;
      return json({ applied: 1, installments: 3, bills_paused: 3, bills_lowered: 0, record_ids: [record.id], preview: preview() });
    }
    if (path === '/api/v1/finance/installment-refunds/fin_refund_1/undo' && request.method() === 'POST') {
      applied = false;
      return json(preview());
    }
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { sent };
}

test('refunds: evidence per order, an estimate stays in review, Apply refunds saves the certain one and Undo puts it back', async ({ page }) => {
  const { sent } = await mockRefunds(page);
  await page.goto('/#finance-bills');
  const card = page.getByRole('region', { name: 'Refunds' });
  await expect(card).toBeVisible();
  await expect(card.getByText('1 order is ready to apply. 1 order needs your review')).toBeVisible();
  await expect(card.getByText(/No longer due: .*300\.00/)).toBeVisible();
  const full = card.getByRole('row').filter({ hasText: 'example-order-a' });
  await expect(full.getByText('Refunded')).toBeVisible();
  await expect(full.getByText('Ready to apply')).toBeVisible();

  const review = card.getByRole('row').filter({ hasText: 'example-order-b' });
  await expect(review.getByText('Needs review')).toBeVisible();
  await review.getByRole('button', { name: 'Details for Sample Store' }).click();
  await expect(card.getByText(/estimate spreads it over the upcoming installments in proportion/)).toBeVisible();
  await expect(card.getByText(/Estimate only: confirm the new amounts in the Tamara app/)).toBeVisible();
  await expect(card.getByRole('columnheader', { name: 'Estimate' })).toBeVisible();
  await expect(card.getByRole('link', { name: 'Open email' })).toHaveAttribute('href', 'https://mail.google.com/mail/u/0/#all/m-part');
  await review.getByRole('button', { name: 'Details for Sample Store' }).click();

  await full.getByRole('button', { name: 'Details for Example Shop' }).click();
  await expect(card.getByText('Tamara email: the whole order was refunded')).toBeVisible();
  await expect(card.getByText(/Money back expected: .*100\.00/)).toBeVisible();
  await expect(card.getByText('Refunded, not due')).toHaveCount(3);

  await card.getByRole('button', { name: 'Apply refunds' }).click();
  const dialog = page.getByRole('alertdialog', { name: 'Apply these refunds?' });
  await expect(dialog.getByText(/The refund on 1 order will be saved/)).toBeVisible();
  await dialog.getByRole('button', { name: 'Apply refunds' }).click();
  await expect(card.getByText('Refund applied to 1 order. 3 installments changed.')).toBeVisible();
  expect(sent.filter((x) => x.path === '/api/v1/finance/installment-refunds/apply')).toEqual([
    { method: 'POST', path: '/api/v1/finance/installment-refunds/apply', body: { orders: [{ provider: 'Tamara', order_id: 'example-order-a', plan_hash: HASH }] } },
  ]);
  await expect(card.getByRole('button', { name: 'Apply refunds' })).toBeDisabled();
  await expect(card.getByRole('heading', { name: 'Applied refunds' })).toBeVisible();
  await card.getByRole('button', { name: 'Undo' }).click();
  await expect(card.getByText('Refund undone for order example-order-a.')).toBeVisible();
  await expect(card.getByRole('button', { name: 'Apply refunds' })).toBeEnabled();
});

test('refunds: a failed load retries, and a changed plan is refused and reloaded', async ({ page }) => {
  await mockRefunds(page, { stale: true, fail: true });
  await page.goto('/#finance-bills');
  const card = page.getByRole('region', { name: 'Refunds' });
  await expect(card.getByText(/Refunds could not be checked/)).toBeVisible();
  await card.getByRole('button', { name: 'Try again' }).click();
  await card.getByRole('button', { name: 'Apply refunds' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Apply refunds' }).click();
  await expect(card.getByText(/The refund evidence changed since this page loaded/)).toBeVisible();
});

test('refunds: none found leads to reading Tamara emails', async ({ page }) => {
  await mockRefunds(page, { empty: true });
  await page.goto('/#finance-bills');
  const card = page.getByRole('region', { name: 'Refunds' });
  await expect(card.getByText('No refunds found')).toBeVisible();
  await card.getByRole('button', { name: 'Read Tamara emails' }).click();
  await expect(page.getByRole('dialog', { name: /Import Tamara payments/ })).toBeVisible();
});

test('refunds: a Gmail read with only a refund email can be approved and saves the evidence', async ({ page }) => {
  const { sent } = await mockRefunds(page, { empty: true });
  await page.goto('/#finance-bills');
  await page.getByRole('region', { name: 'Refunds' }).getByRole('button', { name: 'Read Tamara emails' }).click();
  const dialog = page.getByRole('dialog', { name: /Import Tamara payments/ });
  await dialog.getByRole('button', { name: 'Preview Tamara emails' }).click();
  await expect(dialog.getByText(/Installments found: 0\. Refund emails: 1\./)).toBeVisible();
  await dialog.getByRole('button', { name: 'Approve Gmail statement import' }).click();
  await expect(dialog.getByText(/1 refund signals found/)).toBeVisible();
  const synced = sent.filter((x) => x.path === '/api/v1/finance/installment-statement/sync');
  expect(synced).toHaveLength(1);
  expect(synced[0].body).toMatchObject({ entries: [], refund_notices: [{ order_id: 'example-order-a', message_ref: 'm-full' }] });
});

test('refunds: Arabic labels', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockRefunds(page);
  await page.goto('/#finance-bills');
  const card = page.getByRole('region', { name: 'الاستردادات' });
  await expect(card).toBeVisible();
  await expect(card.getByRole('button', { name: 'طبّق الاستردادات' })).toBeEnabled();
  await expect(card.getByRole('row').filter({ hasText: 'example-order-b' }).getByText('يحتاج إلى مراجعة')).toBeVisible();
});
