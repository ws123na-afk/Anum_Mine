// PDF statement review helpers (src/lib/financePdf.ts, docs/finance.md#pdf-statements). Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import type { FinancePdfStatement } from '@anum/contracts';
import { checkFile } from '../src/lib/finance.ts';
import {
  PDF_MAX_BYTES, checkRoles, filterRows, flagLabel, foreignDetail, fromMinor, importBody, isPdf, liveCheck, liveSummary, pdfErrorCode, reviewRows, roleLabel,
  rowProblem, toMinor,
} from '../src/lib/financePdf.ts';
import { placeholdersOf, setLocale } from '../src/lib/i18n.ts';
import { financePdfAr } from '../src/lib/messages/financePdf.ar.ts';
import { financePdfEn } from '../src/lib/messages/financePdf.en.ts';

afterEach(() => setLocale('en'));

// SYNTHETIC rows, as the API answers them for a generated statement.
const view = (): FinancePdfStatement => ({
  account_id: 'fin_acct_current', currency: 'AED', filename: 'nov.pdf', file_sha256: 'a'.repeat(64), source: 'text', page_count: 2, layout: 'table',
  columns: [], roles: ['date', 'description', 'debit', 'credit', 'balance'], date_format: '%d/%m/%Y', date_formats: ['%d/%m/%Y'], date_format_ambiguous: false,
  period_start: '2026-11-01', period_end: '2026-11-30', account_last4: '5678', account_last4_matches: true, statement_currency: 'AED',
  opening_balance: '2000.00', closing_balance: '13579.50',
  check: { status: 'matched', opening: '2000.00', closing: '13579.50', rows_total: '11579.50', expected_closing: '13579.50', difference: '0.00', opening_source: 'statement', closing_source: 'statement', breaks: [], page_breaks: [],
    summary: [{ name: 'deposits_count', stated: '1', found: '1', ok: true }, { name: 'withdrawals_total', stated: '420.50', found: '420.50', ok: true }] },
  fingerprint: 'f'.repeat(32), template_id: null, confidence: 1, kind: 'account', statement_date: null, due_date: null, skipped_pages: [],
  row_count: 3, duplicate_count: 1, skipped_lines: 4, warnings: [],
  rows: [
    { line: 1, page: 1, posted_on: '2026-11-01', description: 'SALARY NOVEMBER', amount: '12000.00', debit: null, credit: '12000.00', balance: '14000.00', reference: null, currency: null, original_amount: null, original_currency: null, fx_rate: null, fee: null, vat: null, kind: null, time: null, counterparty: null, parent_line: null, confidence: 1, category: 'income', duplicate: true, flags: [] },
    { line: 2, page: 1, posted_on: '2026-11-02', description: 'POS PANDA', amount: '-120.50', debit: '120.50', credit: null, balance: '13879.50', reference: null, currency: null, original_amount: null, original_currency: null, fx_rate: null, fee: null, vat: null, kind: null, time: null, counterparty: null, parent_line: null, confidence: 1, category: 'groceries', duplicate: false, flags: [] },
    { line: 3, page: 2, posted_on: '2026-11-04', description: 'STC BILL', amount: '-300.00', debit: '300.00', credit: null, balance: '13579.50', reference: '77AA01', currency: null, original_amount: '20.00', original_currency: 'USD', fx_rate: '3.75', fee: '1.50', vat: '0.23', kind: null, time: '07:46:19', counterparty: null, parent_line: null, confidence: 1, category: 'utilities', duplicate: false, flags: ['sign_unsure'] },
  ],
});

describe('PDF statement review', () => {
  test('PDFs are recognised and capped at their own size', () => {
    assert.equal(isPdf({ name: 'Statement.PDF' }), true);
    assert.equal(isPdf({ name: 'x.bin', type: 'application/pdf' }), true);
    assert.equal(isPdf({ name: 'oct.csv', type: 'text/csv' }), false);
    assert.equal(checkFile({ name: 'nov.pdf', size: 5_000_000 }, 2_000_000).ok, true);
    const big = checkFile({ name: 'nov.pdf', size: PDF_MAX_BYTES + 1 }, 2_000_000);
    assert.equal(big.ok, false);
    assert.match(big.ok ? '' : big.error, /larger than 10 MB/);
  });

  test('amounts become whole minor units and back, in two and three decimal currencies', () => {
    assert.equal(toMinor('-1,234.5', 'AED'), -123450n);
    assert.equal(toMinor('١٢٫٥٠', 'AED'), 1250n);
    assert.equal(toMinor('(12)', 'AED'), -1200n);
    assert.equal(toMinor('1.250', 'KWD'), 1250n);
    assert.equal(toMinor('1.2.3', 'AED'), null);
    assert.equal(fromMinor(-123450n, 'AED'), '-1234.50');
    assert.equal(fromMinor(5n, 'AED'), '0.05');
    assert.equal(fromMinor(1250n, 'KWD'), '1.250');
    // 0.1 + 0.2 never drifts: the check works in integers.
    assert.equal(fromMinor((toMinor('0.10', 'AED') ?? 0n) + (toMinor('0.20', 'AED') ?? 0n), 'AED'), '0.30');
  });

  test('the live check follows edits and dropped rows', () => {
    const rows = reviewRows(view()).map((row) => ({ ...row, flags: [] }));
    const ok = liveCheck(rows, '2000.00', '13579.50', 'AED');
    assert.deepEqual([ok.status, ok.count, ok.total, ok.difference, ok.breaks], ['matched', 3, '11579.50', '0.00', []]);
    const edited = rows.map((row) => (row.line === 2 ? { ...row, amount: '-12.50' } : row));
    const bad = liveCheck(edited, '2000.00', '13579.50', 'AED');
    assert.deepEqual([bad.status, bad.expected, bad.difference, bad.breaks], ['mismatch', '13687.50', '-108.00', [2]]);
    const dropped = liveCheck(rows.map((row) => (row.line === 3 ? { ...row, include: false } : row)), '2000.00', '13579.50', 'AED');
    assert.equal(dropped.status, 'mismatch');
    assert.equal(liveCheck(rows, '', '13579.50', 'AED').status, 'unknown');
    // A guessed sign means rows that add up prove nothing.
    assert.equal(liveCheck(reviewRows(view()), '2000.00', '13579.50', 'AED').status, 'unknown');
  });

  test('a row needs a date, a description and a non-zero amount', () => {
    const [row] = reviewRows(view());
    assert.equal(rowProblem(row, 'AED'), null);
    assert.equal(rowProblem({ ...row, posted_on: '' }, 'AED'), 'Enter the date.');
    assert.equal(rowProblem({ ...row, description: '  ' }, 'AED'), 'Enter a description.');
    assert.equal(rowProblem({ ...row, amount: 'abc' }, 'AED'), 'Enter the amount, with a minus for money out.');
    assert.equal(rowProblem({ ...row, amount: '0.00' }, 'AED'), 'The amount cannot be zero: fix it or untick the row.');
    assert.match(rowProblem({ ...row, amount: '1.234' }, 'AED') ?? '', /amount/);
  });

  test('the import body carries only the included rows in wire form and says when the owner accepted a mismatch', () => {
    const rows = reviewRows(view()).map((row) => (row.line === 2 ? { ...row, amount: '-1,120.5' } : row.line === 3 ? { ...row, include: false } : row));
    const body = importBody(view(), 'fin_acct_current', rows, '2,000', '13579.50', true);
    assert.equal(body.rows.length, 2);
    assert.deepEqual(body.rows[1], { posted_on: '2026-11-02', description: 'POS PANDA', amount: '-1120.50', balance: '13879.50', reference: null, currency: null, time: null });
    assert.equal(body.opening_balance, '2000.00');
    assert.equal(body.accept_mismatch, true);
    assert.equal(body.file_sha256, 'a'.repeat(64));
  });

  test('the statement summary follows the edits, and card details read as one line', () => {
    const rows = reviewRows(view());
    assert.deepEqual(liveSummary(view().check.summary, rows, '2000.00', 'AED').map((s) => [s.name, s.found, s.ok]), [
      ['deposits_count', '1', true], ['withdrawals_total', '420.50', true]]);
    const dropped = rows.map((row) => (row.line === 3 ? { ...row, include: false } : row));
    assert.deepEqual(liveSummary(view().check.summary, dropped, '2000.00', 'AED').map((s) => s.ok), [true, false]);
    // A card: previous balance (owed, as a minus) plus the charges is the amount due.
    const due = liveSummary([{ name: 'total_due', stated: '420.50', found: '0', ok: false }], rows.filter((r) => r.line > 1), '0.00', 'AED');
    assert.deepEqual([due[0].found, due[0].ok], ['420.50', true]);
    assert.equal(foreignDetail(rows[2]), 'spent USD 20.00, rate 3.75, fee 1.50, VAT 0.23');
    assert.equal(foreignDetail(rows[0]), null);
    const layout = { fingerprint: 'f'.repeat(32), labels: ['Date'], roles: ['date' as const], date_format: null, remember: true };
    assert.deepEqual(importBody(view(), 'a', rows, '2000', '13579.50', false, layout).layout, layout);
    assert.equal(importBody(view(), 'a', rows, '2000', '13579.50', false).rows[2].time, '07:46:19');
  });

  test('a row in another currency starts left out', () => {
    const foreign = { ...view(), rows: [{ ...view().rows[0], currency: 'USD', flags: ['foreign_currency'] }] };
    assert.equal(reviewRows(foreign)[0].include, false);
  });

  test('search, mapping checks and error codes', () => {
    const rows = reviewRows(view());
    assert.deepEqual(filterRows(rows, 'stc').map((r) => r.line), [3]);
    assert.deepEqual(filterRows(rows, '77aa').map((r) => r.line), [3]);
    assert.equal(filterRows(rows, ' ').length, 3);
    assert.equal(checkRoles(['date', 'description', 'debit', 'credit', 'balance']), null);
    assert.equal(checkRoles(['date', 'date', 'debit']), 'Pick exactly one date column.');
    assert.equal(checkRoles(['date', 'description']), 'Pick at least one money column.');
    assert.equal(checkRoles(['date', 'amount', 'debit']), 'Pick either an amount column or debit and credit columns, not both.');
    assert.equal(checkRoles(['date', 'balance', 'balance']), 'Each role can be picked for one column only.');
    assert.equal(pdfErrorCode({ details: [{ type: 'pdf_password_required', message: 'x' }] }), 'password_required');
    assert.equal(pdfErrorCode({ details: [{ type: 'value_error', message: 'x' }] }), null);
    assert.equal(pdfErrorCode(new Error('x')), null);
    assert.equal(roleLabel('debit'), 'Debit (money out)');
    assert.equal(flagLabel('sign_unsure'), 'Sign guessed: money in or out?');
    assert.equal(flagLabel('unknown_flag'), null);
  });

  test('Arabic has every key with the same placeholders', () => {
    for (const [key, value] of Object.entries(financePdfEn)) {
      const ar = financePdfAr[key as keyof typeof financePdfEn];
      assert.ok(ar, key);
      const en = typeof value === 'string' ? value : value.other;
      const arText = typeof ar === 'string' ? ar : ar.other;
      assert.deepEqual(placeholdersOf(arText).sort(), placeholdersOf(en).sort(), key);
    }
    setLocale('ar');
    assert.equal(roleLabel('credit'), 'دائن (مال داخل)');
  });
});
