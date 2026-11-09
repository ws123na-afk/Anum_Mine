// Reconciliation helpers (src/lib/financeReconciliation.ts, docs/finance.md#reconciliation-sessions). Run with
// `pnpm --filter @anum/web test:unit`. Every figure is SYNTHETIC.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { FinanceReconException, FinanceReconHealth } from '@anum/contracts';
import { FINANCE_TABS, hashForTab, tabFromHash } from '../src/lib/finance.ts';
import { FINANCE_NAV, OWN_HEAD_TABS, groupOf } from '../src/lib/financeNav.ts';
import { REASONS, isZero, lastMonth, ledgerClosing, matchCandidates, nextStep, sessionFromHash, sessionStatus, sessionTone } from '../src/lib/financeReconciliation.ts';
import { placeholdersOf } from '../src/lib/i18n.ts';
import { financeReconAr } from '../src/lib/messages/financeRecon.ar.ts';
import { financeReconEn } from '../src/lib/messages/financeRecon.en.ts';

const issue = (over: Partial<FinanceReconException>): FinanceReconException => ({
  id: 'x', kind: 'not_in_ledger', severity: 'blocking', actions: ['match'], posted_on: '2026-10-04', amount: '-20.00', description: 'SAMPLE BOOKS',
  transaction_id: null, other_transaction_id: null, statement_ref: 'imp#1', reason: 'unticked', month: null, age_days: null, details: {}, ...over,
});

const health = (over: Partial<FinanceReconHealth>): FinanceReconHealth => ({
  account_id: 'a', account_name: 'Current', kind: 'bank', currency: 'SAR', last_statement_end: null, last_import_at: null, last_sms_on: null,
  last_apple_pay_on: null, missing_months: [], gaps: 0, reconciled_through: null, open_reconciliation_id: null, changed_reconciliation_id: null,
  next_step: 'up_to_date', next_month: null, bank_feed: 'not_connected', ...over,
});

describe('reconciliation page', () => {
  test('is a deep-linked Finance tab under data capture with its own head', () => {
    assert.ok(FINANCE_TABS.includes('reconciliation'));
    assert.equal(tabFromHash(hashForTab('reconciliation')), 'reconciliation');
    assert.equal(groupOf('reconciliation'), 'capture');
    assert.ok(OWN_HEAD_TABS.has('reconciliation'));
    assert.equal(FINANCE_NAV.flatMap((g) => g.entries).filter((e) => e.kind === 'tab' && e.tab === 'reconciliation').length, 1);
  });

  test('a suggestion queue link opens its session on the reconciliation tab', () => {
    assert.equal(tabFromHash('#finance-reconciliation?session=fin_recon_1'), 'reconciliation');
    assert.equal(sessionFromHash('#finance-reconciliation?session=fin_recon_1'), 'fin_recon_1');
    assert.equal(sessionFromHash('#finance-reconciliation'), null);
    assert.equal(sessionFromHash('#finance-transactions?session=fin_recon_1'), null);
    assert.equal(sessionFromHash('#finance-reconciliation?session=%3Cscript%3E'), null);
  });

  test('a card or loan typed as owed becomes negative in the ledger; a bank balance keeps its sign', () => {
    assert.equal(ledgerClosing('card', '500.00'), '-500.00');
    assert.equal(ledgerClosing('loan', '-20'), '-20');
    assert.equal(ledgerClosing('card', '0'), '0');
    assert.equal(ledgerClosing('bank', '930.00'), '930.00');
  });

  test('only ledger records with exactly the same amount are offered for a match', () => {
    const row = issue({});
    const ledger = [issue({ id: 'a', kind: 'unmatched_payment', transaction_id: 't1', amount: '-20.00', statement_ref: null }),
      issue({ id: 'b', kind: 'pending', transaction_id: 't2', amount: '-20.01', statement_ref: null }),
      issue({ id: 'c', kind: 'wrong_direction', transaction_id: 't3', amount: '-20.00', statement_ref: null })];
    assert.deepEqual(matchCandidates(row, ledger).map((e) => e.id), ['a']);
    assert.deepEqual(matchCandidates(issue({ amount: null }), ledger), []);
  });

  test('a session reads as completed, changed, ready or unresolved', () => {
    assert.equal(sessionStatus({ status: 'completed', state: 'balanced', changed: true }), 'changed');
    assert.equal(sessionTone({ status: 'completed', state: 'balanced', changed: true }), 'bad');
    assert.equal(sessionStatus({ status: 'completed', state: 'balanced', changed: false }), 'completed');
    assert.equal(sessionStatus({ status: 'open', state: 'balanced', changed: false }), 'ready');
    assert.equal(sessionStatus({ status: 'open', state: 'unresolved', changed: false }), 'unresolved');
    assert.ok(isZero('0.00') && isZero('-0.000') && !isZero('0.01') && !isZero(null));
  });

  test('the next step names the month and what the button does, and never a live feed', () => {
    assert.deepEqual(nextStep(health({ next_step: 'import_statement', next_month: '2026-10' })), { key: 'frec.next.import', action: 'import', reconciliationId: null });
    assert.equal(nextStep(health({ next_step: 'import_statement' })).key, 'frec.next.importAny');
    assert.deepEqual(nextStep(health({ next_step: 'review_changes', changed_reconciliation_id: 'r1' })).reconciliationId, 'r1');
    assert.equal(nextStep(health({ next_step: 'none' })).action, null);
  });

  test('the usual period is the month before today', () => {
    assert.deepEqual(lastMonth('2026-11-09'), { start: '2026-10-01', end: '2026-10-31' });
    assert.deepEqual(lastMonth('2026-01-15'), { start: '2025-12-01', end: '2025-12-31' });
  });

  test('every key has an Arabic twin with the same placeholders, and every explained reason has words', () => {
    assert.deepEqual(Object.keys(financeReconAr).sort(), Object.keys(financeReconEn).sort());
    for (const [key, value] of Object.entries(financeReconEn)) {
      assert.deepEqual(placeholdersOf(financeReconAr[key as keyof typeof financeReconEn]), placeholdersOf(value), key);
    }
    for (const reason of REASONS) assert.ok(`frec.reason.${reason}` in financeReconEn, reason);
  });
});
