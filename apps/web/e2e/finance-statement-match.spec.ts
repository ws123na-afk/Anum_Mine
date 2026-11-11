import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { PREVIEW, financeReply, financeState, matchOf } from './support/financeApi';
import { useSavedLocale } from './support/workspaceApi';

// The statement matching engine in the import preview (docs/finance.md#statement-matching): rows grouped by what the
// import will do, with reasons, the FX breakdown, a bill payment, an own transfer and the balance check; the person's
// ticks go to POST /imports as decisions; "Find transfers" previews pairs already in the ledger and pairs only the
// ticked ones. Every row is SYNTHETIC ("EXAMPLE MERCHANT") and the API is mocked.
const sms = { record_id: 'fin_je_9:fin_acct_current', canonical_id: 'fin_je_9', transaction_id: 'fin_txn_9', account_id: 'fin_acct_current', source: 'sms', last4: '1234',
  posted_on: '2026-11-03', amount: '-75.40', currency: 'AED', description: 'EXAMPLE STREAMING' };

const rows = [
  { line: 2, posted_on: '2026-11-04', amount: '-76.20', description: 'EXAMPLE STREAMING USD 20.00 @ 3.81', category: 'subscriptions',
    match: matchOf({ status: 'matched_existing', code: 'same_original_amount', record: sms, lag_days: 1, amount_difference: '-0.80', default_import: false }),
    fx: { original_amount: '20.00', original_currency: 'USD', rate: '3.81', rate_source: 'printed', fee: null, fee_currency: null, markup_percent: null, expected: '76.20',
      difference: '0.00', check: 'ties', implied_markup_percent: '0.00' } },
  { line: 3, posted_on: '2026-11-04', amount: '-45.50', description: 'CARD TXN 9912', category: 'other',
    match: matchOf({ status: 'possible_duplicate', code: 'amount_date_only', default_import: false, lag_days: 0,
      candidates: [{ ...sms, record_id: 'fin_je_8:fin_acct_current', amount: '-45.50', description: 'EXAMPLE MARKET' }] }) },
  { line: 4, posted_on: '2026-11-14', amount: '-290.00', description: 'EXAMPLE TELECOM BILL', category: 'telecom',
    match: matchOf({ bill: { bill_id: 'fin_bill_1', name: 'EXAMPLE TELECOM', due_on: '2026-11-15', expected: '290.00', currency: 'AED', strength: 'strong', reasons: ['payee', 'amount', 'due_window'] } }) },
  { line: 5, posted_on: '2026-11-05', amount: '-1000.00', description: 'TRANSFER TO CARD 5377', category: 'transfer',
    match: matchOf({ transfer: { direction: 'to', account_id: 'fin_acct_card', account_name: 'Card', account_last4: '5377', strength: 'strong', reasons: ['same_amount', 'transfer_words'], lag_days: 0,
      other: { ...sms, record_id: 'fin_je_7:fin_acct_card', account_id: 'fin_acct_card', source: 'statement', last4: null, amount: '1000.00', description: 'PAYMENT RECEIVED' } } }) },
  { line: 6, posted_on: '2026-11-01', amount: '-12.00', description: 'EXAMPLE BAKERY', category: 'food',
    match: matchOf({ status: 'exact_duplicate', code: 'imported_before', default_import: false, record: { ...sms, source: 'statement', last4: null, amount: '-12.00', description: 'EXAMPLE BAKERY', posted_on: '2026-11-01' } }) },
].map((row) => ({ currency: 'AED', duplicate: row.match.status === 'exact_duplicate', ...row }));

const preview = {
  ...PREVIEW, filename: 'november.csv', preset: 'generic', headers: ['Date', 'Description', 'Amount', 'Balance'], date_format_ambiguous: false,
  mapping: { date: 'Date', description: 'Description', amount: 'Amount', debit: null, credit: null, balance: 'Balance' },
  row_count: 5, valid_count: 5, new_count: 2, duplicate_count: 1, error_count: 0, errors: [], rows,
  counts: { new: 2, exact_duplicate: 1, matched_existing: 1, possible_duplicate: 1, bills: 1, transfers: 1 },
  reconciliation: { status: 'differs', opening: '2000.00', closing: '600.00', rows_total: '-1423.70', expected_closing: '576.30', difference: '23.70', opening_source: 'statement', closing_source: 'rows', breaks: [] },
};

const candidates = {
  start: '2026-08-11', end: '2026-11-09', strong: 1, weak: 1,
  pairs: [
    { out: { ...sms, record_id: 'fin_je_1:fin_acct_current', amount: '-1000.00', description: 'EXAMPLE CARD PAYMENT 5377' }, into: { ...sms, record_id: 'fin_je_2:fin_acct_card', account_id: 'fin_acct_card', amount: '1000.00', description: 'EXAMPLE PAYMENT', source: 'manual', last4: null },
      strength: 'strong', reasons: ['same_amount', 'account_named'], lag_days: 3, out_account_name: 'Current account', into_account_name: 'Card', into_last4: '5377' },
    { out: { ...sms, record_id: 'fin_je_3:fin_acct_current', amount: '-300.00', description: 'EXAMPLE PAYEE' }, into: { ...sms, record_id: 'fin_je_4:fin_acct_savings', account_id: 'fin_acct_savings', amount: '300.00', description: 'EXAMPLE DEPOSIT', source: 'manual', last4: null },
      strength: 'weak', reasons: ['same_amount'], lag_days: 0, out_account_name: 'Current account', into_account_name: 'Savings', into_last4: null },
  ],
};

async function mockApi(page: Page, shown: Record<string, unknown> = preview) {
  const state = financeState({});
  const sent: { path: string; body: unknown }[] = [];
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
    if (path === '/api/v1/finance/imports/pdf/templates') return json([]);
    const body = request.postData() ? request.postDataJSON() : null;
    if (path === '/api/v1/finance/imports/preview') { sent.push({ path, body }); return json(shown); }
    if (path === '/api/v1/finance/imports' && request.method() === 'POST') {
      sent.push({ path, body });
      const created = { id: 'fin_imp_1', account_id: 'fin_acct_current', filename: 'november.csv', preset: 'generic', date_format: '%Y-%m-%d', row_count: 5, imported_count: 3,
        duplicate_count: 2, error_count: 0, created_at: '2026-11-09T10:00:00Z', reconciled: 1, held_for_review: 0, corrected: 1, bills_paid: 1, bills_review: 0, transfers: 1 };
      state.imports.unshift(created);
      return json(created, 201);
    }
    if (path === '/api/v1/finance/transfers/candidates') { sent.push({ path, body: Object.fromEntries(url.searchParams) }); return json(candidates); }
    if (path === '/api/v1/finance/transfers') { sent.push({ path, body }); return json({ paired: 1, recategorized: 2, failed: [] }, 201); }
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return sent;
}

test('the preview groups rows by what the import will do and sends the ticks', async ({ page }) => {
  const sent = await mockApi(page);
  await page.goto('/#finance-import');
  await page.getByLabel('Statement file (CSV or PDF)').setInputFiles({ name: 'november.csv', mimeType: 'text/csv', buffer: Buffer.from('Date,Description,Amount\n2026-11-04,EXAMPLE MERCHANT,-1.00\n') });
  await page.getByRole('button', { name: 'Preview' }).click();
  const region = page.getByRole('region', { name: 'Preview of november.csv' });
  // The balance check says by how much it does not tie.
  await expect(region.getByRole('alert').filter({ hasText: 'The statement does not tie' })).toContainText('23.70');
  const engine = region.getByRole('region', { name: 'What this import will do' });
  await expect(engine.getByText('Needs your decision (1)')).toBeVisible();
  await expect(engine.getByText('Matched to SMS, Apple Pay or your entries (1)')).toBeVisible();
  await expect(engine.getByText('Already recorded (1)')).toBeVisible();
  // Plain reasons, the FX breakdown, the bill and the transfer.
  await expect(engine.getByText(/Same foreign purchase as SMS from card ending 1234, posted 1 day later/)).toBeVisible();
  await expect(engine.getByText(/Rate \W*3\.81\W* \(printed\)/)).toBeVisible();
  await expect(engine.getByText(/Pays EXAMPLE TELECOM due \W*2026-11-15/).first()).toBeVisible();
  await expect(engine.getByText('Transfer to Card ending 5377').first()).toBeVisible();
  // Tick the possible duplicate, keep the recorded FX amount, do not record the bill.
  const decide = engine.locator('details[data-group="decide"]');
  await decide.getByLabel('Import as a separate payment').check();
  await engine.locator('details[data-group="matched"]').getByLabel(/Correct the recorded amount/).uncheck();
  await engine.locator('details[data-group="bills"]').getByLabel(/Record as the payment of EXAMPLE TELECOM/).uncheck();
  await expect(engine.getByText(/Adds 3, confirms 1 already recorded/)).toBeVisible();
  await region.getByRole('button', { name: 'Import 3 rows' }).click();
  await expect(page.getByText(/Imported 3 rows; 2 were already there/)).toContainText('paired 1 transfers');
  const confirm = sent.find((r) => r.path === '/api/v1/finance/imports')?.body as { decisions: unknown[] };
  expect(confirm.decisions).toEqual([{ line: 2, correct: false }, { line: 3, action: 'import' }, { line: 4, bill: false }]);
});

test('one row for several recorded payments: the person ticks the group and the import sends exactly it', async ({ page }) => {
  // docs/finance.md#one-row-for-several-recorded-payments
  const part = (id: string, amount: string) => ({ ...sms, record_id: `${id}:fin_acct_current`, canonical_id: id, amount, description: 'EXAMPLE MARKET' });
  const split = { ...rows[0], line: 2, amount: '-150.00', description: 'POS EXAMPLE MARKET', fx: null,
    match: matchOf({ status: 'new', code: 'no_match', splits: [[part('fin_je_21', '-100.00'), part('fin_je_22', '-50.00')]] }) };
  const sent = await mockApi(page, { ...preview, rows: [split], row_count: 1, valid_count: 1, new_count: 1, duplicate_count: 0,
    counts: { new: 1, exact_duplicate: 0, matched_existing: 0, possible_duplicate: 0, bills: 0, transfers: 0, splits: 1 } });
  await page.goto('/#finance-import');
  await page.getByLabel('Statement file (CSV or PDF)').setInputFiles({ name: 'november.csv', mimeType: 'text/csv', buffer: Buffer.from('Date,Description,Amount\n2026-11-04,EXAMPLE MERCHANT,-1.00\n') });
  await page.getByRole('button', { name: 'Preview' }).click();
  const engine = page.getByRole('region', { name: 'What this import will do' });
  const decide = engine.locator('details[data-group="decide"]');
  const group = decide.getByRole('group', { name: 'Several recorded payments add up to this row' });
  await expect(group).toContainText('EXAMPLE MARKET');
  await group.getByLabel('This row is these 2 payments together').check();
  await expect(engine.getByText(/Adds 0, confirms 1 already recorded/)).toBeVisible();
  await page.getByRole('button', { name: 'Import 0 rows' }).click();
  const confirm = sent.find((r) => r.path === '/api/v1/finance/imports')?.body as { decisions: unknown[] };
  expect(confirm.decisions).toEqual([{ line: 2, split: ['fin_je_21:fin_acct_current', 'fin_je_22:fin_acct_current'] }]);
});

test('Find transfers previews pairs already in the ledger and pairs only the ticked ones', async ({ page }) => {
  const sent = await mockApi(page);
  await page.goto('/#finance-import');
  const finder = page.getByRole('region', { name: 'Transfers between your accounts' });
  await finder.getByRole('button', { name: 'Find transfers' }).click();
  await expect(finder.getByText('Found 1 likely transfers and 1 to check.')).toBeVisible();
  const strong = finder.getByLabel(/Pair Current account to Card ending 5377/);
  const weak = finder.getByLabel(/Pair Current account to Savings/);
  await expect(strong).toBeChecked();
  await expect(weak).not.toBeChecked();
  await finder.getByRole('button', { name: 'Pair 1 transfers' }).click();
  await expect(finder.getByText('Paired 1 transfers.')).toBeVisible();
  const paired = sent.find((r) => r.path === '/api/v1/finance/transfers')?.body as { pairs: unknown[] };
  expect(paired.pairs).toEqual([{ out_record_id: 'fin_je_1:fin_acct_current', into_record_id: 'fin_je_2:fin_acct_card' }]);
});

const VIEWPORTS = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const locale of ['en', 'ar'] as const) {
  for (const [width, height] of VIEWPORTS) {
    test(`responsive @gate: the import preview fits with no sideways scroll at ${width}x${height} (${locale})`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      if (locale === 'ar') await useSavedLocale(page, 'ar');
      await mockApi(page);
      await page.goto('/#finance-import');
      await page.locator('input[type="file"]').setInputFiles({ name: 'november.csv', mimeType: 'text/csv', buffer: Buffer.from('Date,Description,Amount\n2026-11-04,EXAMPLE MERCHANT,-1.00\n') });
      await page.locator('form.finForm button[type="submit"]').first().click();
      await expect(page.locator('.fmatch')).toBeVisible({ timeout: 20_000 });
      await page.locator('.ftransferFind button[type="submit"]').click();
      await expect(page.locator('.ftransferFind .fmatchRow').first()).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      for (const control of await page.locator('.fmatch button:visible, .fmatch input:visible, .ftransferFind button:visible').all()) {
        const box = await control.boundingBox();
        if (!box) continue;
        expect(box.x).toBeGreaterThanOrEqual(-0.5);
        expect(box.x + box.width).toBeLessThanOrEqual(width + 0.5);
      }
      const serious = (await new AxeBuilder({ page }).include('.fmatch').include('.ftransferFind').analyze()).violations
        .filter((v) => v.impact === 'critical' || v.impact === 'serious');
      expect(serious).toEqual([]);
      await page.screenshot({ path: test.info().outputPath(`fmatch-${locale}-${width}x${height}.png`), fullPage: true });
    });
  }
}
