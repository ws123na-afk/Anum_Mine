import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { useSavedLocale } from './support/workspaceApi';

// PDF bank statements (docs/finance.md#pdf-statements): a password-protected PDF asks for its password,
// the review shows the period, the last four digits and the balance check, the owner fixes a row, the
// live check follows, the rows are imported through POST /imports/pdf and the import can be undone.
// Every statement here is SYNTHETIC: the API is mocked and the PDF bytes are a placeholder.
const PDF = { name: 'november.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 SYNTHETIC') };

function statement(roles = ['date', 'description', 'debit', 'credit', 'balance']) {
  const swapped = roles[2] === 'credit';
  const sign = (value: string) => (swapped ? (value.startsWith('-') ? value.slice(1) : `-${value}`) : value);
  const rows = [
    { line: 1, page: 1, posted_on: '2026-11-01', description: 'SALARY NOVEMBER', amount: sign('12000.00'), balance: '14000.00', reference: null, category: 'income', duplicate: true, flags: [] },
    { line: 2, page: 1, posted_on: '2026-11-02', description: 'POS PANDA CARD ***1234', amount: sign('-120.50'), balance: '13879.50', reference: null, category: 'groceries', duplicate: false, flags: [] },
    { line: 3, page: 2, posted_on: '2026-11-04', description: 'DEWA BILL', amount: sign('-300.00'), balance: '13579.50', reference: '77AA01', category: 'utilities', duplicate: false, flags: [] },
    ...Array.from({ length: 30 }, (_, i) => ({
      line: 4 + i, page: 2, posted_on: '2026-11-05', description: `COFFEE ${i + 1}`, amount: sign('-1.00'), balance: (13578.5 - i).toFixed(2), reference: null, category: 'dining', duplicate: false, flags: [],
    })),
  ].map((row) => ({ debit: null, credit: null, currency: null, original_amount: null, original_currency: null, fx_rate: null, fee: null, vat: null,
    kind: null, time: null, counterparty: null, parent_line: null, confidence: 1, ...row }));
  return {
    account_id: 'fin_acct_current', currency: 'AED', filename: 'november.pdf', file_sha256: 'a'.repeat(64), source: 'text', page_count: 2, layout: 'table',
    columns: ['Date', 'Description', 'Debit', 'Credit', 'Balance'].map((label, index) => ({ index, label, role: roles[index] })), roles,
    date_format: '%d/%m/%Y', date_formats: ['%d/%m/%Y'], date_format_ambiguous: false, period_start: '2026-11-01', period_end: '2026-11-30',
    account_last4: '1234', account_last4_matches: true, statement_currency: 'AED', opening_balance: '2000.00', closing_balance: '13549.50',
    check: { status: swapped ? 'mismatch' : 'matched', opening: '2000.00', closing: '13549.50', rows_total: '11549.50', expected_closing: '13549.50', difference: '0.00', opening_source: 'statement', closing_source: 'statement', breaks: [], page_breaks: [],
      summary: [{ name: 'withdrawals_count', stated: '32', found: '32', ok: true }] },
    fingerprint: 'f'.repeat(32), template_id: null, confidence: 1, kind: 'account', statement_date: null, due_date: null, skipped_pages: [{ page: 3, reason: 'not_transactions' }],
    row_count: rows.length, duplicate_count: 1, skipped_lines: 4, warnings: ['rows_left_out'], rows,
    dropped_rows: [{ page: 3, reason: 'outside_period', text: 'SAMPLE EXAMPLE ROW', posted_on: '2019-01-08', amount: '-41.20' }],
  };
}

async function mockApi(page: Page, options: { password?: boolean; ocrDown?: boolean } = {}) {
  const state = financeState({});
  const reads: { headers: Record<string, string>; search: URLSearchParams }[] = [];
  const imports: unknown[] = [];
  const templates: Record<string, unknown>[] = [];
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
    if (path === '/api/v1/finance/imports/pdf/read') {
      const headers = request.headers();
      reads.push({ headers, search: url.searchParams });
      if (options.ocrDown) return json({ error: { code: 'service_unavailable', message: 'This PDF is a scan and could not be read: OCR is not connected on this server.', correlation_id: 'c1', details: [{ type: 'pdf_ocr_unavailable', message: 'x' }] } }, 503);
      if (options.password && headers['x-statement-password'] !== 'example') {
        const wrong = Boolean(headers['x-statement-password']);
        return json({ error: { code: 'validation_error', message: wrong ? 'That password did not open the PDF. Check it and try again.' : 'This PDF is protected with a password.', correlation_id: 'c1', details: [{ type: wrong ? 'pdf_password_wrong' : 'pdf_password_required', message: 'x' }] } }, 422);
      }
      return json(statement(url.searchParams.get('roles')?.split(',')));
    }
    if (path === '/api/v1/finance/imports/pdf/templates') return json(templates);
    if (path.startsWith('/api/v1/finance/imports/pdf/templates/') && request.method() === 'DELETE') { templates.splice(0, 1); return route.fulfill({ status: 204 }); }
    const body = request.postData() ? request.postDataJSON() : null;
    if (path === '/api/v1/finance/imports/pdf') {
      imports.push(body);
      const layout = (body as { layout?: { remember: boolean; labels: string[]; roles: string[] } }).layout;
      if (layout?.remember) templates.push({ id: 'fin_tpl_1', labels: layout.labels, roles: layout.roles, date_format: null, uses: 1, created_at: '2026-11-05T10:00:00Z', updated_at: '2026-11-05T10:00:00Z', last_used_at: '2026-11-05T10:00:00Z' });
      const created = { id: `fin_imp_${state.imports.length + 1}`, account_id: 'fin_acct_current', filename: 'november.pdf', preset: 'pdf', date_format: 'pdf', row_count: 33, imported_count: 31, duplicate_count: 2, error_count: 0, created_at: '2026-11-05T10:00:00Z', reconciled: 1, held_for_review: 0 };
      state.imports.unshift(created);
      return json(created, 201);
    }
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { reads, imports, templates };
}

test('a protected PDF is opened with its password, reviewed, fixed and imported; the import can be undone', async ({ page }) => {
  const { reads, imports } = await mockApi(page, { password: true });
  await page.goto('/#finance-import');
  await page.getByLabel('Statement file (CSV or PDF)').setInputFiles(PDF);
  // A PDF has no CSV layout to pick.
  await expect(page.getByLabel('Layout', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Preview' }).click();
  const locked = page.getByRole('region', { name: 'This PDF is protected with a password' });
  await locked.getByRole('button', { name: 'Open the PDF' }).click();
  await expect(locked.getByRole('alert')).toHaveText('Type the password first.');
  await locked.getByLabel('PDF password').fill('not-it');
  await locked.getByRole('button', { name: 'Open the PDF' }).click();
  await expect(locked.getByRole('alert')).toContainText('That password did not open the PDF');
  await locked.getByLabel('PDF password').fill('example');
  await locked.getByRole('button', { name: 'Open the PDF' }).click();

  const review = page.getByRole('region', { name: 'Review november.pdf' });
  await expect(review).toContainText('2026-11-01 to 2026-11-30');
  await expect(review).toContainText('ending 1234');
  await expect(review).toContainText('33 found, 1 already imported');
  await expect(review.getByText(/The rows add up/)).toBeVisible();
  await expect(review.getByText('Page 3 skipped: not transactions (bank examples or terms).')).toBeVisible();
  // A row the reader left out is listed with its reason, never dropped silently.
  const dropped = review.getByText('1 row or line left out by the reader');
  await expect(dropped).toBeVisible();
  await dropped.click();
  await expect(review.getByRole('listitem').filter({ hasText: 'SAMPLE EXAMPLE ROW' })).toContainText('Dated outside the statement period');
  await expect(review.getByText('Number of withdrawals: the statement says 32, the rows give 32.')).toBeVisible();
  // The password went in a header, never the URL.
  const last = reads.at(-1)!;
  expect(last.headers['x-statement-password']).toBe('example');
  expect([...last.search.keys()]).not.toContain('password');
  expect(reads.every((r) => !r.search.toString().includes('example'))).toBe(true);

  // Paging and search over a long statement.
  await expect(review.locator('tbody tr')).toHaveCount(25);
  await review.getByRole('button', { name: 'Next rows' }).click();
  await expect(review.locator('tbody tr')).toHaveCount(8);
  await review.getByLabel('Search rows').fill('dewa');
  await expect(review.locator('tbody tr')).toHaveCount(1);
  await expect(review.getByText('Ref 77AA01')).toBeVisible();

  // A wrong amount breaks the check; import waits for a fix or an explicit confirmation.
  await review.getByLabel('Amount of row 3', { exact: true }).fill('-30.00');
  await expect(review.getByRole('alert').filter({ hasText: 'The rows do not add up. The opening balance' })).toBeVisible();
  const importButton = review.getByRole('button', { name: 'Import 32 rows' });
  await expect(importButton).toBeDisabled();
  await review.getByLabel('Amount of row 3', { exact: true }).fill('-300.00');
  await expect(review.getByText(/The rows add up/)).toBeVisible();
  // Dropping a row also breaks it; the owner confirms on purpose.
  await review.getByLabel('Import row 3', { exact: true }).uncheck();
  await expect(review.getByText('1 row left out.')).toBeVisible();
  await review.getByLabel('The rows do not add up and I want to import them anyway').check();
  await review.getByRole('button', { name: 'Import 31 rows' }).click();
  await expect(page.getByText(/Imported 31 rows; 2 were already there/)).toBeVisible();
  const sent = imports[0] as { rows: { description: string; include?: boolean; action?: string }[]; accept_mismatch: boolean; file_sha256: string; opening_balance: string };
  // The unticked row travels marked left out (include: false), so identical rows keep their places; it is never imported.
  expect(sent.rows.filter((r) => r.include !== false)).toHaveLength(32);
  expect(sent.rows.filter((r) => r.description === 'DEWA BILL').map((r) => [r.include, r.action])).toEqual([[false, 'skip']]);
  expect(sent.accept_mismatch).toBe(true);
  expect(sent.opening_balance).toBe('2000.00');
  await expect(review).toHaveCount(0);

  const history = page.getByRole('region', { name: 'Earlier imports' });
  await history.getByRole('listitem').filter({ hasText: 'november.pdf' }).getByRole('button', { name: 'Undo import' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Undo import' }).click();
  await expect(page.getByText('The import of november.pdf was undone.')).toBeVisible();
});

test('the owner fixes the column mapping, the PDF is read again with it, and the layout is remembered', async ({ page }) => {
  const { reads, imports } = await mockApi(page);
  await page.goto('/#finance-import');
  await page.getByLabel('Statement file (CSV or PDF)').setInputFiles(PDF);
  await page.getByRole('button', { name: 'Preview' }).click();
  const review = page.getByRole('region', { name: 'Review november.pdf' });
  const mapping = review.getByRole('group', { name: 'Columns found in the PDF' });
  await mapping.getByLabel('Column Debit').selectOption('date');
  await mapping.getByRole('button', { name: 'Read again' }).click();
  await expect(review.getByRole('alert')).toHaveText('Pick exactly one date column.');
  await mapping.getByLabel('Column Debit').selectOption('credit');
  await mapping.getByLabel('Column Credit').selectOption('debit');
  await mapping.getByRole('button', { name: 'Read again' }).click();
  await expect.poll(() => reads.at(-1)?.search.get('roles')).toBe('date,description,credit,debit,balance');
  await expect(review.getByLabel('Amount of row 1', { exact: true })).toHaveValue('-12000.00');
  // A corrected layout is offered to be remembered for that bank; then it is listed and can be forgotten.
  await expect(review.getByLabel('Remember this layout for the next statement from this bank')).toBeChecked();
  await review.getByLabel('The rows do not add up and I want to import them anyway').check();
  await review.getByRole('button', { name: /^Import \d+ rows$/ }).click();
  await expect.poll(() => (imports.at(-1) as { layout?: { remember: boolean; roles: string[] } } | undefined)?.layout?.roles?.[2]).toBe('credit');
  const taught = page.getByRole('region', { name: 'Statement layouts you taught' });
  await expect(taught.getByText('Date · Description · Debit · Credit · Balance')).toBeVisible();
  await taught.getByRole('button', { name: 'Forget: Date · Description · Debit · Credit · Balance' }).click();
  await page.getByRole('alertdialog', { name: 'Forget this layout?' }).getByRole('button', { name: 'Forget' }).click();
  await expect(taught.getByText('The layout was forgotten.')).toBeVisible();
  await expect(taught.getByText(/None yet/)).toBeVisible();
});

test('a scan that cannot be read says why and offers another try', async ({ page }) => {
  await mockApi(page, { ocrDown: true });
  await page.goto('/#finance-import');
  await page.getByLabel('Statement file (CSV or PDF)').setInputFiles(PDF);
  await page.getByRole('button', { name: 'Preview' }).click();
  const failedPanel = page.getByRole('region', { name: 'The PDF could not be read' });
  await expect(failedPanel.getByRole('alert')).toContainText('OCR is not connected on this server');
  await expect(failedPanel.getByRole('button', { name: 'Try again' })).toBeVisible();
  await failedPanel.getByRole('button', { name: 'Pick another file' }).click();
  await expect(failedPanel).toHaveCount(0);
});

test('the PDF review reads in Arabic, right to left', async ({ page }) => {
  await mockApi(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#finance-import');
  await page.getByLabel('ملف الكشف (CSV أو PDF)').setInputFiles(PDF);
  await page.getByRole('button', { name: 'معاينة' }).click();
  const review = page.getByRole('region', { name: /^مراجعة / });
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('region', { name: 'مطابقة الرصيد' }).getByRole('status').filter({ hasText: 'الصفوف متطابقة' })).toHaveCount(1);
  await expect(page.getByRole('group', { name: 'الأعمدة الموجودة في ملف PDF' })).toBeVisible();
  await expect(review).toContainText('ينتهي بـ 1234');
});
