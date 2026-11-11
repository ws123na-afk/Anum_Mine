import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type Route } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { suggestionsState } from './support/financeSuggestionsApi';
import { GATE_SIZES, clippedControls, horizontalOverflow } from './support/gatePages';
import { mockBooks } from './support/businessBooksApi';
import { useSavedLocale } from './support/workspaceApi';

// Evidence, attribution, card FX and exception notices (docs/business-books.md#supporting-documents-and-evidence,
// docs/finance.md#card-fx-and-fees, #attribution-suggestions, #exception-notices): a bill's supporting documents with the
// "Evidence missing" flag, attaching one from the vault, the evidence pack export; attribution items in Suggestions accepted
// through the links API; the exception notice switches; the FX details of a foreign card purchase and separating its fee;
// every state, Arabic and the gate. The API is mocked at http://localhost:8000/**; every figure is SYNTHETIC.

const stamp = '2026-11-01T00:00:00Z';
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

async function noSeriousAxe(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
  expect(serious).toEqual([]);
}

/** The books mock plus the evidence routes and the vault documents the picker lists. */
async function mockEvidence(page: Page, o: { failEvidence?: boolean } = {}) {
  const mock = await mockBooks(page);
  const attached: Record<string, { link_id: string; document_id: string; title: string; category: string; added_by: string; added_at: string }[]> = {};
  const calls: { method: string; path: string; body: unknown }[] = [];
  // With ``failEvidence`` the second read fails: the first is the bills list's quiet flag check, the second the bill's own panel.
  let reads = 0;
  await page.route('http://localhost:8000/api/v1/business/books/evidence**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const body = request.postData() ? request.postDataJSON() : null;
    calls.push({ method: request.method(), path: url.pathname, body });
    if (url.pathname.endsWith('/evidence') && request.method() === 'GET') {
      reads += 1;
      if (o.failEvidence && reads === 2) return json(route, { detail: 'The database is not reachable.', code: 'unavailable' }, 503);
      const ids = (url.searchParams.get('ids') ?? '').split(',').filter(Boolean);
      return json(route, { can_attach: true, items: Object.fromEntries(ids.map((id) => [id, { documents: attached[id] ?? [], evidence_missing: !(attached[id]?.length) }])) });
    }
    if (url.pathname.endsWith('/evidence') && request.method() === 'POST') {
      const b = body as { source_id: string; document_id: string };
      const doc = { link_id: `vlnk_${Object.keys(attached).length + 1}`, document_id: b.document_id, title: 'Synthetic rent receipt', category: 'receipt', added_by: 'user_local', added_at: stamp };
      attached[b.source_id] = [...(attached[b.source_id] ?? []), doc];
      return json(route, doc, 201);
    }
    if (request.method() === 'DELETE') {
      const [, , , , , , sourceId, linkId] = url.pathname.split('/');
      attached[sourceId] = (attached[sourceId] ?? []).filter((d) => d.link_id !== linkId);
      return route.fulfill({ status: 204 });
    }
    return json(route, { enabled: true, threshold: '500.00', currency: 'SAR', count: 0, items: [] });
  });
  await page.route('http://localhost:8000/api/v1/business/books/evidence-pack.*', (route) => route.fulfill({ status: 200, contentType: 'application/zip',
    headers: { 'content-disposition': 'attachment; filename="evidence-pack-2026-01-01-2026-12-31.zip"', 'access-control-allow-origin': '*', 'access-control-expose-headers': 'Content-Disposition' },
    body: 'PK synthetic' }));
  await page.route('http://localhost:8000/api/v1/documents?**', (route) => json(route, { next_cursor: null, items: [
    { id: 'vdoc_rent', title: 'Synthetic rent receipt', category: 'receipt', access_class: 'workspace', status: 'active', file: { name: 'rent.pdf' }, links_count: 0 },
  ] }));
  return { mock, calls, attached };
}

const books = (page: Page, name: string) => page.getByRole('navigation', { name: 'Books sections' }).getByRole('button', { name, exact: true });

test('a bill shows Evidence missing until a vault document is attached, and the evidence pack downloads', async ({ page }) => {
  const { calls } = await mockEvidence(page);
  await page.goto('/#business-books');
  await books(page, 'Bills').click();
  await expect(page.locator('.bzTable tbody tr').first().getByText('Evidence missing')).toBeVisible();
  await page.locator('.bzTable tbody tr').first().getByRole('button').first().click();
  const panel = page.locator('.bkEvidence');
  await expect(panel.getByRole('heading', { name: /Supporting documents/ })).toContainText('Evidence missing');
  await expect(panel).toContainText('Nothing is attached, and this is above the evidence threshold.');
  await panel.getByRole('button', { name: 'Attach a document' }).click();
  const picker = page.getByRole('dialog', { name: 'Attach a document from the vault' });
  await expect(picker).toContainText('Synthetic rent receipt');
  await picker.getByRole('button', { name: 'Attach', exact: true }).click();
  await expect(picker).toHaveCount(0);
  await expect(panel.getByRole('link', { name: /Synthetic rent receipt/ })).toHaveAttribute('href', '#vault-documents?doc=vdoc_rent');
  await expect(panel.getByRole('heading', { name: /Supporting documents/ })).not.toContainText('Evidence missing');
  expect(calls.some((c) => c.method === 'POST' && (c.body as { document_id?: string }).document_id === 'vdoc_rent')).toBe(true);
  await panel.getByRole('button', { name: 'Remove Synthetic rent receipt' }).click();
  await expect(panel).toContainText('Document removed from this record. It stays in the Document vault.');
  await noSeriousAxe(page);
  await page.keyboard.press('Escape');

  await books(page, 'Statements').click();
  const pack = page.getByRole('region', { name: 'Evidence pack' }).or(page.locator('section', { has: page.getByRole('heading', { name: 'Evidence pack' }) }));
  await expect(pack.first()).toContainText('never a bank account or card');
  const download = page.waitForEvent('download');
  await pack.first().getByRole('button', { name: 'Download pack (zip)' }).click();
  expect((await download).suggestedFilename()).toBe('evidence-pack-2026-01-01-2026-12-31.zip');
  await expect(pack.first()).toContainText('Downloaded. The export is recorded in the audit log.');
});

test('the evidence panel says what failed and tries again', async ({ page }) => {
  await mockEvidence(page, { failEvidence: true });
  await page.goto('/#business-books');
  await books(page, 'Bills').click();
  await page.locator('.bzTable tbody tr').first().getByRole('button').first().click();
  const panel = page.locator('.bkEvidence');
  await expect(panel.getByRole('alert')).toContainText('not reachable');
  await panel.getByRole('button', { name: 'Try again' }).click();
  await expect(panel.getByRole('button', { name: 'Attach a document' })).toBeVisible();
});

test('Arabic: the evidence panel and the pack read right to left', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockEvidence(page);
  await page.goto('/#business-books');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await page.getByRole('navigation', { name: 'أقسام الدفاتر' }).getByRole('button', { name: 'فواتير الموردين', exact: true }).click();
  await page.locator('.bzTable tbody tr').first().getByRole('button').first().click();
  await expect(page.locator('.bkEvidence')).toContainText('المستندات الداعمة');
  await expect(page.locator('.bkEvidence')).toContainText('المستند ناقص');
});

// -- Finance: attribution, notices and card FX ------------------------------------------------------------------------------

const link = { canonical_id: 'fin_je_print', dimension: 'business', target_type: 'business_workspace', target_id: 'ws_synthetic_trading', amount: '80.00',
  currency: 'AED', treatment: 'reimbursable', category: 'shopping', project_id: 'prj_synthetic' };
const fx = {
  transaction_id: 'x', foreign: true, original_amount: '20.00', original_currency: 'USD', billed_amount: '76.88', billed_currency: 'AED', purchase_amount: '75.00',
  rate: '3.750000', rate_kind: 'implied', rate_date: '2026-11-03', rate_source: 'statement',
  fee: { amount: '1.88', currency: 'AED', source: 'markup_stated', transaction_id: null, markup_percent: '2.5', included_in_billed: true, linked: false },
  split: 'available', notes: [], plan_hash: 'b'.repeat(64), can_correct: true, transfer: null,
};

async function mockFinance(page: Page) {
  const state = financeState({});
  state.suggestions = suggestionsState({});
  state.suggestions.items.unshift({
    key: 'attribution:business:fin_txn_print:ws_synthetic_trading:prj_synthetic', producer: 'attribution', kind: 'attribution', detected: 'SYNTHETIC', why: 'SYNTHETIC',
    params: { dimension: 'business', business: 'ws_synthetic_trading', business_name: 'Synthetic Trading', agree: '3', seen: '3', treatment: 'reimbursable', merchant: 'example print shop' },
    evidence: [{ label: 'EXAMPLE PRINT SHOP', ref_type: 'transaction', ref_id: 'fin_txn_print', value: '2026-11-03 AED -80.00' }], confidence: '0.90',
    effect: [{ amount: '80.00', currency: 'AED', direction: 'out', note: null }],
    action: { kind: 'link', transaction_ids: ['fin_txn_print'], category: null, subcategory_id: null, transaction_type: null, link }, href: '#finance-transactions',
    occurred_on: '2026-11-03', payload_hash: 'c'.repeat(64),
  });
  const separate: unknown[] = [];
  let fxState = { ...fx };
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json(route, {}, 404);
    if (path === '/api/v1/workspace-memberships/current') return json(route, { tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'member', active: true });
    if (path === '/api/v1/workspaces/current') return json(route, { id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: stamp, updated_at: stamp, archived_at: null, archived_by: null });
    if (path === '/api/v1/me/workspace-memberships') return json(route, [{ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', role: 'member', active: true }]);
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals' || path === '/api/v1/conversations') return json(route, []);
    const body = request.postData() ? request.postDataJSON() : null;
    const fxMatch = /^\/api\/v1\/finance\/transactions\/([^/]+)\/fx(\/separate)?$/.exec(path);
    if (fxMatch && !fxMatch[2]) return json(route, { ...fxState, transaction_id: fxMatch[1] });
    if (fxMatch && fxMatch[2]) {
      separate.push(body);
      fxState = { ...fxState, split: 'done', billed_amount: '76.88' };
      return json(route, { purchase: fxState, fee_transaction_id: 'fin_txn_fee', mode: 'split' });
    }
    if (path === '/api/v1/finance/suggestions/accept' && (body as { key: string }).key.startsWith('attribution:')) {
      state.suggestions!.decided[(body as { key: string }).key] = 'accepted';
      return json(route, { key: (body as { key: string }).key, status: 'accepted', batch: null, link_id: 'fin_link_1' });
    }
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(route, reply.body, reply.status);
    return route.abort();
  });
  return { state, separate };
}

test('an attribution suggestion allocates through the links API, and exception notices are switched on', async ({ page }) => {
  const { state } = await mockFinance(page);
  await page.goto('/#finance-suggestions');
  const card = page.locator('.fsugCard', { hasText: 'Allocate to Synthetic Trading?' });
  await expect(card).toContainText('You allocated example print shop to this business 3 of 3 times, as to be paid back to you.');
  await expect(card).toContainText('90% sure');
  await card.getByRole('button', { name: 'Allocate' }).click();
  await expect(page.getByText('Linked. You can remove the link from the record if it is wrong.')).toBeVisible();
  await expect(page.locator('.fsugCard', { hasText: 'Allocate to Synthetic Trading?' })).toHaveCount(0);

  await page.getByRole('tab', { name: 'Automation' }).click();
  await expect(page.getByText('A notice in ANUM, never an email or a phone push. Each item at most once a day.')).toBeVisible();
  const days = page.getByLabel('Reconciliation exceptions open for at least (days)');
  await expect(days).toBeDisabled();
  await page.getByLabel('Reconciliation exceptions left open').check();
  await expect(days).toBeEnabled();
  await days.fill('10');
  await page.getByLabel('Business bills and adjustments with no supporting document').check();
  await page.getByRole('button', { name: 'Save automation' }).click();
  await expect(page.getByText('Automation saved.')).toBeVisible();
  expect(state.suggestions!.automation.notices).toEqual(['reconciliation_overdue', 'evidence_missing']);
  expect(state.suggestions!.automation.notice_exception_days).toBe(10);
});

test('the transaction drawer shows the card FX details and separates the fee with the figures shown', async ({ page }) => {
  const { separate } = await mockFinance(page);
  await page.goto('/#finance-transactions');
  await page.locator('.fregTable tbody tr').first().click();
  const panel = page.locator('.ffxPanel');
  await expect(panel.getByRole('heading', { name: 'Currency and bank fee' })).toBeVisible();
  await expect(panel).toContainText('worked out from the two amounts');
  await expect(panel).toContainText('markup stated on the row');
  await expect(panel).toContainText('3.75');
  await panel.getByRole('button', { name: 'Separate the fee' }).click();
  const confirm = page.getByRole('alertdialog', { name: 'Separate the bank FX fee' });
  await expect(confirm).toContainText('the account balance does not change');
  await confirm.getByRole('button', { name: 'Separate the fee' }).click();
  await expect(panel).toContainText('The fee is now its own bank fee line.');
  await expect(panel).toContainText('Fee recorded as its own bank fee line');
  expect(separate).toEqual([{ plan_hash: 'b'.repeat(64) }]);
});

test('an own transfer shows its other side in the drawer and is unpaired after a confirmation', async ({ page }) => {
  const { state } = await mockFinance(page);
  const first = String(state.transactions[0].id);
  state.transferPairs = { [first]: { paired: true, link_id: 'fin_link_9', link_version: 2, role: 'out',
    other: { transaction_id: 'fin_txn_in', account_id: 'fin_acct_card', account_name: 'Rewards card', posted_on: '2026-11-05', amount: '1000.00', currency: 'AED', description: 'EXAMPLE PAYMENT RECEIVED' } } };
  const voids: unknown[] = [];
  page.on('request', (request) => { if (request.method() === 'POST' && request.url().endsWith('/links/fin_link_9/void')) voids.push(request.postDataJSON()); });
  await page.goto('/#finance-transactions');
  await page.locator('.fregTable tbody tr').first().click();
  const pair = page.getByRole('region', { name: 'Own transfer' });
  await expect(pair).toContainText('Paired with the money that arrived on Rewards card.');
  await expect(pair).toContainText('EXAMPLE PAYMENT RECEIVED');
  await pair.getByRole('button', { name: 'Unpair' }).click();
  const confirm = page.getByRole('alertdialog', { name: 'Unpair this transfer?' });
  await expect(confirm).toContainText('Both rows stay in the ledger');
  await confirm.getByRole('button', { name: 'Unpair' }).click();
  await expect(page.getByText(/Unpaired\. Both rows stay as they are/)).toBeVisible();
  await expect(page.getByRole('region', { name: 'Own transfer' })).toHaveCount(0);
  expect(voids).toEqual([{ version: 2 }]);
});

for (const { width, height } of GATE_SIZES) {
  test(`responsive @gate: the bill's supporting documents and the evidence pack fit at ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await mockEvidence(page);
    await page.goto('/#business-books');
    await books(page, 'Statements').click();
    await expect(page.getByRole('heading', { name: 'Evidence pack' })).toBeVisible();
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
    expect(await clippedControls(page)).toEqual([]);
    if (width === 1440 || width === 390) await page.screenshot({ path: `test-results/evidence-pack-${width}.png`, fullPage: true });
    await books(page, 'Bills').click();
    await page.locator('.bzTable tbody tr').first().getByRole('button').first().click();
    await expect(page.locator('.bkEvidence')).toBeVisible();
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
    expect(await clippedControls(page)).toEqual([]);
    if (width === 1440 || width === 390) await page.screenshot({ path: `test-results/evidence-bill-${width}.png`, fullPage: true });
  });
  test(`responsive @gate: the FX details fit at ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await mockFinance(page);
    await page.goto('/#finance-transactions');
    await page.locator('.fregTable tbody tr').first().click();
    await expect(page.locator('.ffxPanel')).toBeVisible();
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
    if (width === 1440 || width === 390) await page.screenshot({ path: `test-results/fx-drawer-${width}.png`, fullPage: true });
  });
}
