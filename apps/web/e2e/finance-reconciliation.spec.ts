import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { useSavedLocale } from './support/workspaceApi';

// Finance, Reconciliation (docs/finance.md#reconciliation-sessions): connection health with the next step and no live
// bank feed claimed, the sessions list, a session's equation and exceptions, a correcting entry approved from its
// legs, completing once it balances, starting a new session, every state (empty with a next step, error with a retry,
// denied), the sidebar entry, Arabic and the responsive gate. The API is mocked at http://localhost:8000/** like the
// other specs; every figure is SYNTHETIC. Nothing here moves money or forces a balance.

const stamp = '2026-11-01T00:00:00Z';
interface Mock { corrected: boolean; completed: boolean; created: boolean; posted: string[] }

function figures(m: Mock) {
  const closing = m.corrected ? '930.00' : '950.00';
  return { currency: 'AED', book_opening: '1000.00', deposits: '0.00', payments: m.corrected ? '-70.00' : '-50.00', book_closing: closing, statement_opening: '1000.00',
    statement_closing: '930.00', difference: m.corrected ? '0.00' : '-20.00', pending_total: '0.00', adjusted_book_closing: closing,
    unexplained: m.corrected ? '0.00' : '-20.00', opening_difference: '0.00' };
}

function session(m: Mock) {
  const f = figures(m);
  const items = [
    ...(m.corrected ? [] : [{ id: 'not_in_ledger:imp#2', kind: 'not_in_ledger', severity: 'blocking', actions: ['match', 'correct'], posted_on: '2026-10-04', amount: '-20.00',
      description: 'SAMPLE BOOKS', transaction_id: null, other_transaction_id: null, statement_ref: 'fin_imp_1#2', reason: 'unticked', month: null, age_days: null, details: {} }]),
    { id: 'pending:tx_sms', kind: 'pending', severity: 'warning', actions: ['attach', 'pending', 'match'], posted_on: '2026-10-05', amount: '-35.00', description: 'EXAMPLE CAFE',
      transaction_id: 'tx_sms', other_transaction_id: null, statement_ref: null, reason: 'sms', month: null, age_days: 35, details: {} },
    { id: 'reversal_pair:tx_a', kind: 'reversal_pair', severity: 'info', actions: ['accept_reversal'], posted_on: '2026-10-06', amount: '-80.00', description: 'CARD PURCHASE SAMPLE STORE',
      transaction_id: 'tx_a', other_transaction_id: 'tx_b', statement_ref: null, reason: null, month: null, age_days: null, details: {} },
  ];
  const balanced = m.corrected;
  return {
    id: 'rec_1', account_id: 'fin_acct_current', account_name: 'Everyday account', account_kind: 'bank', currency: 'AED', period_start: '2026-10-01', period_end: '2026-10-31',
    source: 'import', import_id: 'fin_imp_1', status: m.completed ? 'completed' : 'open', state: balanced ? 'balanced' : 'unresolved', changed: false,
    can_complete: balanced && !m.completed, blockers: balanced ? [] : ['difference', 'statement_rows_not_in_ledger'], statement_closing: '930.00', book_closing: f.book_closing,
    unexplained: f.unexplained, exceptions: items.filter((i) => i.severity !== 'info').length, blocking: items.filter((i) => i.severity === 'blocking').length,
    completed_at: m.completed ? stamp : null, completed_by: m.completed ? 'user_local' : null, version: m.completed ? 2 : 1,
    figures: f, card: null, counts: { blocking: balanced ? 0 : 1, warning: 1, info: 1 }, items, total: items.length, limit: 20, offset: 0,
    resolutions: m.corrected ? [{ id: 'rci_1', kind: 'correction', statement_ref: 'fin_imp_1#2', transaction_id: 'tx_new', other_transaction_id: null, note: null, created_at: stamp }] : [],
    history: m.completed ? [{ action: 'completed', at: stamp, by: 'user_local', unexplained: '0.00' }] : [], snapshot: {},
  };
}

const health = (empty: boolean) => ({ today: '2026-11-09', accounts: empty ? [] : [
  { account_id: 'fin_acct_current', account_name: 'Everyday account', kind: 'bank', currency: 'AED', last_statement_end: '2026-10-31', last_import_at: stamp, last_sms_on: '2026-11-05',
    last_apple_pay_on: null, missing_months: ['2026-09'], gaps: 1, reconciled_through: null, open_reconciliation_id: 'rec_1', changed_reconciliation_id: null,
    next_step: 'import_statement', next_month: '2026-09', bank_feed: 'not_connected' },
  { account_id: 'fin_acct_card', account_name: 'Rewards card', kind: 'card', currency: 'AED', last_statement_end: null, last_import_at: null, last_sms_on: null, last_apple_pay_on: '2026-11-02',
    missing_months: [], gaps: 0, reconciled_through: null, open_reconciliation_id: null, changed_reconciliation_id: null, next_step: 'import_statement', next_month: null, bank_feed: 'not_connected' },
] });

async function mockApi(page: Page, o: { empty?: boolean; fail?: number; denied?: boolean } = {}): Promise<Mock> {
  const state = financeState({ denied: o.denied });
  let failures = o.fail ?? 0;
  const mock: Mock = { corrected: false, completed: false, created: false, posted: [] };
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
    if (sub.startsWith('/reconciliations')) {
      mock.posted.push(`${request.method()} ${sub}`);
      if (o.denied) return json({ error: { code: 'forbidden', message: 'Finance is for workspace owners and members. Ask a workspace owner to change your role.', correlation_id: 'c', details: [] } }, 403);
      if (failures > 0 && request.method() === 'GET') { failures -= 1; return json({ error: { code: 'unavailable', message: 'The database is not reachable.', correlation_id: 'c', details: [] } }, 503); }
      const none = o.empty && !mock.created;
      if (sub === '/reconciliations/health') return json(health(Boolean(o.empty)));
      if (sub === '/reconciliations' && request.method() === 'GET') {
        const { items: _items, figures: _f, card: _c, counts: _k, resolutions: _r, history: _h, snapshot: _s, total: _t, limit: _l, offset: _o, ...summary } = session(mock);
        return json({ items: none ? [] : [summary], total: none ? 0 : 1, limit: 10, offset: 0 });
      }
      if (sub === '/reconciliations' && request.method() === 'POST') { mock.created = true; return json(session(mock), 201); }
      if (sub === '/reconciliations/rec_1' && request.method() === 'GET') {
        const all = session(mock);
        const severity = url.searchParams.get('severity');
        const items = severity ? all.items.filter((i) => i.severity === severity) : all.items;
        return json({ ...all, items, total: items.length });
      }
      if (sub === '/reconciliations/rec_1/corrections/preview') return json({ statement_ref: body.statement_ref, account_id: 'fin_acct_current', posted_on: '2026-10-04', amount: '-20.00',
        currency: 'AED', description: 'SAMPLE BOOKS', category: 'shopping', payload_hash: 'a'.repeat(64),
        legs: [{ ledger_account: 'asset:fin_acct_current', amount: '-20.00', currency: 'AED' }, { ledger_account: 'expense:shopping', amount: '20.00', currency: 'AED' }] });
      if (sub === '/reconciliations/rec_1/corrections') { mock.corrected = body.payload_hash === 'a'.repeat(64); return json({ transaction_id: 'tx_new', resolution_id: 'rci_1' }, 201); }
      if (sub === '/reconciliations/rec_1/resolutions') return json({ id: 'rci_2', kind: body.kind, statement_ref: null, transaction_id: body.transaction_id, other_transaction_id: null, note: null, created_at: stamp }, 201);
      if (sub === '/reconciliations/rec_1/complete') {
        if (!mock.corrected) return json({ error: { code: 'conflict', message: 'This reconciliation does not balance yet.', correlation_id: 'c', details: [] } }, 409);
        mock.completed = true;
        return json(session(mock));
      }
      return json({ error: { code: 'not_found', message: 'Reconciliation not found.', correlation_id: 'c', details: [] } }, 404);
    }
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return mock;
}

async function shot(page: Page, name: string) {
  const dir = process.env.FIN_SHOTS;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
}

test('reconcile a statement: health says what to do next, a correcting entry is approved from its legs, then it completes', async ({ page }) => {
  const mock = await mockApi(page);
  await page.goto('/#finance-reconciliation');
  await expect(page.getByRole('heading', { level: 1, name: 'Reconciliation' })).toBeVisible();
  const health = page.getByRole('region', { name: /Connection health/ });
  await expect(health).toContainText('No live bank feed');
  await expect(health).toContainText('Next: import the statement for September 2026.');
  await expect(health).toContainText('One month has activity but no statement');
  await page.getByRole('button', { name: /Open the reconciliation of Everyday account to (31 Oct|Oct 31,) 2026/ }).click();
  await expect(page.getByText('Unexplained difference', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Complete reconciliation' })).toBeDisabled();
  await expect(page.getByText('Statement row not in your books')).toBeVisible();
  await page.getByRole('tab', { name: /Needs you/ }).click();
  await expect(page.getByText('Looks like a payment and its reversal')).toHaveCount(0);
  await page.getByRole('button', { name: 'Post correcting entry' }).click();
  const dialog = page.getByRole('alertdialog', { name: 'Post a correcting entry' });
  await expect(dialog).toContainText('expense:shopping');
  await dialog.getByRole('button', { name: 'Approve and post' }).click();
  await expect(page.getByText('Correcting entry posted.')).toBeVisible();
  expect(mock.posted).toContain('POST /reconciliations/rec_1/corrections');
  await page.getByRole('tab', { name: /All/ }).click();
  await page.getByRole('button', { name: 'Complete reconciliation' }).click();
  await expect(page.getByText('Reconciliation completed.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Reopen' })).toBeVisible();
});

test('an SMS record can be marked not on this statement, and the page sends only the owner\'s choice', async ({ page }) => {
  const mock = await mockApi(page);
  await page.goto('/#finance-reconciliation');
  await page.getByRole('button', { name: /Open the reconciliation of Everyday account/ }).click();
  const row = page.locator('.frecException', { hasText: 'EXAMPLE CAFE' });
  await expect(row).toContainText('SMS, 35 days old');
  await row.getByRole('button', { name: 'Not on this statement' }).click();
  await expect(page.getByText('Marked as not on this statement')).toBeVisible();
  expect(mock.posted.filter((p) => p.startsWith('POST')).sort()).toEqual(['POST /reconciliations/rec_1/resolutions']);
});

test('empty: no reconciliation yet offers to start one, typed from the statement', async ({ page }) => {
  await mockApi(page, { empty: true });
  await page.goto('/#finance-reconciliation');
  await expect(page.getByRole('heading', { name: 'No reconciliation yet' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'No bank or card account yet' })).toBeVisible();
  await page.getByRole('region', { name: /Statement reconciliations/ }).getByRole('button', { name: 'New reconciliation' }).click();
  const dialog = page.getByRole('dialog', { name: 'New reconciliation' });
  await dialog.getByLabel('Closing balance (AED)').fill('930.00');
  await dialog.getByRole('button', { name: 'Start reconciling' }).click();
  await expect(page.getByText('Reconciliation started for Everyday account.')).toBeVisible();
  await expect(page.getByRole('heading', { name: /Everyday account/ })).toBeVisible();
});

test('an error says what failed and how to retry', async ({ page }) => {
  await mockApi(page, { fail: 2 });
  await page.goto('/#finance-reconciliation');
  await expect(page.getByRole('alert').first()).toContainText('Reconciliation could not be loaded');
  const retry = page.getByRole('button', { name: 'Try again' });
  await expect(retry.first()).toBeVisible();
  while (await retry.count()) await retry.first().click();
  await expect(page.getByRole('button', { name: /Open the reconciliation of Everyday account/ })).toBeVisible();
});

test('a role without Finance is told who can do it', async ({ page }) => {
  await mockApi(page, { denied: true });
  await page.goto('/#finance-reconciliation');
  await expect(page.getByRole('heading', { name: 'Finance is not available to your role' })).toBeVisible();
});

test('the sidebar lists Reconciliation under data capture and keeps it in view at 1440 (phones: the section strip)', async ({ page, isMobile }) => {
  await mockApi(page);
  if (!isMobile) await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/#finance-reconciliation');
  const sections = page.getByRole('navigation', { name: 'Finance sections' });
  await expect(sections.getByRole('button', { name: 'Reconciliation' })).toHaveAttribute('aria-current', 'page');
  if (!isMobile) await expect(sections.locator('[aria-current="page"]')).toBeInViewport();
});

const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;

for (const locale of ['en', 'ar'] as const) {
  test(`@gate reconciliation fits every size without sideways scrolling (${locale})`, async ({ page }) => {
    await mockApi(page);
    if (locale === 'ar') await useSavedLocale(page, 'ar');
    for (const [width, height] of SIZES) {
      await page.setViewportSize({ width, height });
      await page.goto('/#finance-reconciliation');
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      await expect(page.locator('.frecHealth')).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), `list at ${width}x${height}`).toBeLessThanOrEqual(0);
      await shot(page, `recon-list-${locale}-${width}`);
      await page.locator('.frecSession button').first().click();
      await expect(page.locator('.frecEquation')).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), `detail at ${width}x${height}`).toBeLessThanOrEqual(0);
      await shot(page, `recon-detail-${locale}-${width}`);
      await page.locator('.frecDetailHead button').click();
    }
  });
}
