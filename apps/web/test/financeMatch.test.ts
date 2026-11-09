// The statement matching engine in the import preview (src/lib/financeMatch.ts, docs/finance.md#statement-matching).
// Every row is SYNTHETIC ("EXAMPLE MERCHANT"). Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import type { FinanceImport, FinanceRowMatch } from '@anum/contracts';
import {
  billOn, canImportAnyway, correctOn, decisionsFor, fxText, groupRows, groupsFor, outcomeOf, reasonText, reconciliationText, sourceText, tally, transferOn,
  type MatchRow,
} from '../src/lib/financeMatch.ts';
import { importNotice } from '../src/lib/financeImportNotice.ts';
import { importBody, reviewRows } from '../src/lib/financePdf.ts';
import { placeholdersOf, setLocale } from '../src/lib/i18n.ts';
import { financeMatchAr } from '../src/lib/messages/financeMatch.ar.ts';
import { financeMatchEn } from '../src/lib/messages/financeMatch.en.ts';

afterEach(() => setLocale('en'));

const sms = { record_id: 'e1:card', canonical_id: 'e1', transaction_id: 't1', account_id: 'card', source: 'sms', last4: '1234', posted_on: '2026-11-03',
  amount: '-45.50', currency: 'SAR', description: 'EXAMPLE MARKET' };

const match = (patch: Partial<FinanceRowMatch>): FinanceRowMatch => ({
  status: 'new', code: 'no_match', reason: '', record: null, candidates: [], lag_days: null, amount_difference: null, bill: null, transfer: null,
  default_import: true, ...patch,
});

const row = (id: number, m: FinanceRowMatch, amount = '-10.00'): MatchRow => ({ id: String(id), line: id, posted_on: '2026-11-04', description: 'EXAMPLE MERCHANT', amount, match: m });

const bill = { bill_id: 'b1', name: 'EXAMPLE TELECOM', due_on: '2026-11-15', expected: '290.00', currency: 'SAR', strength: 'strong' as const, reasons: ['payee'] };
const transfer = { direction: 'from' as const, account_id: 'bank', account_name: 'Salary account', account_last4: '5678', strength: 'strong' as const, reasons: ['transfer_words'],
  lag_days: 2, other: { ...sms, record_id: 'e2:bank', account_id: 'bank', source: 'statement', last4: null, amount: '-1000.00' } };

describe('groups and outcomes', () => {
  test('each row lands in its status group, and bills and transfers are listed again', () => {
    assert.deepEqual(groupsFor(match({})), ['new']);
    assert.deepEqual(groupsFor(match({ status: 'matched_existing', code: 'same_amount_merchant', record: sms })), ['matched']);
    assert.deepEqual(groupsFor(match({ status: 'exact_duplicate', code: 'imported_before' })), ['recorded']);
    assert.deepEqual(groupsFor(match({ status: 'possible_duplicate', code: 'amount_date_only' })), ['decide']);
    assert.deepEqual(groupsFor(match({ bill })), ['new', 'bills']);
    assert.deepEqual(groupsFor(match({ bill: { ...bill, strength: 'weak' } })), ['decide']);
    assert.deepEqual(groupsFor(match({ transfer, status: 'new' })), ['new', 'transfers']);
    const groups = groupRows([row(1, match({})), row(2, match({ status: 'exact_duplicate', code: 'imported_before' })), row(3, match({ bill }))]);
    assert.deepEqual(groups.new.map((r) => r.id), ['1', '3']);
    assert.deepEqual(groups.recorded.map((r) => r.id), ['2']);
    assert.deepEqual(groups.bills.map((r) => r.id), ['3']);
  });

  test('defaults: new imports, matched attaches, duplicate stays out, possible waits for review', () => {
    assert.equal(outcomeOf(match({}), undefined), 'import');
    assert.equal(outcomeOf(match({}), { action: 'skip' }), 'skip');
    assert.equal(outcomeOf(match({ status: 'matched_existing' }), undefined), 'attach');
    assert.equal(outcomeOf(match({ status: 'exact_duplicate', code: 'imported_before' }), { action: 'import' }), 'duplicate');
    assert.equal(outcomeOf(match({ status: 'exact_duplicate', code: 'same_statement_row' }), { action: 'import' }), 'import');
    assert.equal(outcomeOf(match({ status: 'possible_duplicate' }), undefined), 'review');
    assert.equal(outcomeOf(match({ status: 'possible_duplicate' }), { action: 'import' }), 'import');
    assert.equal(canImportAnyway(match({ status: 'exact_duplicate', code: 'reconciled_before' })), false);
  });

  test('bill, transfer and correction ticks follow strong and weak', () => {
    assert.equal(billOn(match({ bill }), undefined), true);
    assert.equal(billOn(match({ bill }), { bill: false }), false);
    assert.equal(transferOn(match({ transfer }), undefined), true);
    assert.equal(transferOn(match({ transfer: { ...transfer, strength: 'weak' } }), undefined), false);
    assert.equal(transferOn(match({ transfer: { ...transfer, strength: 'weak' } }), { transfer: true }), true);
    const fx = match({ status: 'matched_existing', code: 'same_original_amount', record: sms, amount_difference: '-0.80' });
    assert.equal(correctOn(fx, undefined), true);
    assert.equal(correctOn(fx, { correct: false }), false);
  });

  test('only changed rows are sent, by line, and the tally counts what will happen', () => {
    const rows = [row(2, match({})), row(3, match({ status: 'possible_duplicate' })), row(4, match({ bill })), row(5, match({ status: 'exact_duplicate', code: 'imported_before' }))];
    const choices = { 2: { action: 'skip' as const }, 3: { action: 'import' as const }, 4: { bill: false } };
    assert.deepEqual(decisionsFor(rows, choices), [{ line: 2, action: 'skip' }, { line: 3, action: 'import' }, { line: 4, bill: false }]);
    assert.deepEqual(decisionsFor(rows, {}), []);
    assert.deepEqual(tally(rows, choices), { import: 2, attach: 0, review: 0, skip: 1, duplicate: 1, bills: 0, transfers: 0 });
    assert.deepEqual(tally(rows, {}), { import: 2, attach: 0, review: 1, skip: 0, duplicate: 1, bills: 1, transfers: 0 });
  });
});

describe('words', () => {
  test('the reason names the source, the card and the posting lag', () => {
    assert.equal(sourceText(sms), 'SMS from card ending 1234');
    assert.equal(sourceText({ ...sms, source: 'wallet', last4: null }), 'Apple Pay');
    assert.equal(reasonText(match({ status: 'matched_existing', code: 'same_amount_merchant', record: sms, lag_days: 1 }), 'SAR'),
      'Same amount and merchant as SMS from card ending 1234, posted 1 day later.');
    assert.match(reasonText(match({ status: 'possible_duplicate', code: 'two_candidates', candidates: [sms, sms] }), 'SAR'), /2 recorded payments/);
    setLocale('ar');
    assert.match(reasonText(match({ status: 'matched_existing', code: 'same_amount_merchant', record: sms, lag_days: 0 }), 'SAR'), /1234/);
  });

  test('the FX breakdown says what is printed, what is computed and the difference', () => {
    const lines = fxText({ original_amount: '20.00', original_currency: 'USD', rate: '3.84', rate_source: 'printed', fee: '1.20', fee_currency: 'SAR', markup_percent: null,
      expected: '76.80', difference: '0.20', check: 'differs', implied_markup_percent: '0.26' }, 'SAR');
    assert.equal(lines.length, 5);
    assert.match(lines[1]!, /3\.84\W* \(printed\)/);
    assert.match(lines[3]!, /0\.20\W* more/);
    assert.match(lines[4]!, /computed/);
    assert.deepEqual(fxText(null, 'SAR'), []);
    const implied = fxText({ original_amount: '10.00', original_currency: 'EUR', rate: '4.15', rate_source: 'implied', fee: null, fee_currency: null, markup_percent: null,
      expected: '41.50', difference: '0.00', check: 'ties', implied_markup_percent: null }, 'SAR');
    assert.match(implied[1]!, /computed from the two amounts/);
  });

  test('the balance check ties, differs with the amount, or says why it cannot check', () => {
    const base = { opening: '0.00', closing: '950.00', rows_total: '950.00', expected_closing: '950.00', difference: '0.00', opening_source: 'statement', closing_source: 'statement', breaks: [] };
    assert.equal(reconciliationText({ ...base, status: 'tied' }, 'SAR').tone, 'ok');
    const off = reconciliationText({ ...base, status: 'differs', closing: '900.00', difference: '-50.00' }, 'SAR');
    assert.equal(off.tone, 'error');
    assert.match(off.text, /50\.00/);
    assert.equal(reconciliationText({ ...base, status: 'unknown' }, 'SAR').tone, 'info');
  });

  test('the done notice adds what the engine did', () => {
    const result = { id: 'i', account_id: 'a', filename: 'nov.csv', preset: 'generic', date_format: '%Y-%m-%d', row_count: 3, imported_count: 1, duplicate_count: 2,
      error_count: 0, created_at: '2026-11-09T00:00:00Z', reconciled: 1, held_for_review: 0, corrected: 1, bills_paid: 1, bills_review: 0, transfers: 1 } satisfies FinanceImport;
    const text = importNotice(result);
    assert.match(text, /Imported 1 rows/);
    assert.match(text, /Corrected 1, recorded 1 bill payments/);
    assert.doesNotMatch(importNotice({ ...result, corrected: 0, bills_paid: 0, transfers: 0 }), /Corrected/);
  });

  test('every English key has an Arabic twin with the same placeholders', () => {
    assert.deepEqual(Object.keys(financeMatchAr).sort(), Object.keys(financeMatchEn).sort());
    for (const [key, value] of Object.entries(financeMatchEn)) {
      const en = typeof value === 'string' ? value : value.other;
      const arValue = financeMatchAr[key as keyof typeof financeMatchAr];
      const ar = typeof arValue === 'string' ? arValue : arValue.other;
      assert.deepEqual(placeholdersOf(ar).sort(), placeholdersOf(en).sort(), key);
    }
  });
});

describe('PDF rows carry the ticks', () => {
  test('a ticked possible duplicate and an unticked bill are sent with their row', () => {
    const view = {
      account_id: 'card', currency: 'SAR', filename: 'nov.pdf', file_sha256: 'a'.repeat(64), source: 'text' as const, check: { summary: [] },
      rows: [
        { line: 1, page: 1, posted_on: '2026-11-03', description: 'CARD TXN 9912', amount: '-45.50', debit: '45.50', credit: null, balance: null, reference: null, currency: null,
          original_amount: null, original_currency: null, fx_rate: null, fee: null, vat: null, kind: null, time: null, counterparty: null, parent_line: null, confidence: 1,
          category: 'other', duplicate: false, flags: [], match: match({ status: 'possible_duplicate', code: 'amount_date_only' }), fx: null },
        { line: 2, page: 1, posted_on: '2026-11-14', description: 'EXAMPLE TELECOM', amount: '-290.00', debit: '290.00', credit: null, balance: null, reference: null, currency: null,
          original_amount: null, original_currency: null, fx_rate: null, fee: null, vat: null, kind: null, time: null, counterparty: null, parent_line: null, confidence: 1,
          category: 'utilities', duplicate: false, flags: [], match: match({ bill }), fx: null },
      ],
    };
    const rows = reviewRows(view as never);
    assert.equal(rows[0]!.match?.status, 'possible_duplicate');
    const body = importBody(view as never, 'card', rows, '', '', false, null, { '1:1': { action: 'import' }, '1:2': { bill: false } });
    assert.equal(body.rows[0]!.action, 'import');
    assert.equal(body.rows[1]!.bill, false);
    assert.equal(body.rows[1]!.action, undefined);
  });
});
