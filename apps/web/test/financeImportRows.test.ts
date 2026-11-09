// Import history rows (src/lib/financeImportRows.ts, docs/finance.md#import-history). SYNTHETIC import records.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { FinanceImport } from '@anum/contracts';
import { importRowFilters, rowsNotInLedger } from '../src/lib/financeImportRows.ts';
import { placeholdersOf } from '../src/lib/i18n.ts';
import { financeConnectionsAr } from '../src/lib/messages/financeConnections.ar.ts';
import { financeConnectionsEn } from '../src/lib/messages/financeConnections.en.ts';

const item = (extra: Partial<FinanceImport> = {}): FinanceImport => ({
  id: 'fin_imp_1', account_id: 'fin_acct_1', filename: 'nov.csv', preset: 'generic', date_format: '%Y-%m-%d', row_count: 6, imported_count: 1,
  duplicate_count: 0, error_count: 1, created_at: '2026-11-09T10:00:00Z', reconciled: 1, held_for_review: 1, skipped: 1, dropped: 1, not_in_ledger: 2, ...extra,
});

describe('import history rows', () => {
  test('filters list only the groups the import has, with their stored counts', () => {
    assert.deepEqual(importRowFilters(item()).map((o) => [o.id, o.count]), [
      ['all', null], ['not_in_ledger', 2], ['held_for_review', 1], ['unticked', 1], ['attached_to_existing', 1], ['imported', 1], ['dropped_by_reader', 1]]);
    const clean = item({ held_for_review: 0, skipped: 0, dropped: 0, not_in_ledger: 0, reconciled: 0 });
    assert.deepEqual(importRowFilters(clean).map((o) => o.id), ['all', 'not_in_ledger', 'imported']);
  });

  test('an import made before the counts were stored reads as nothing missing', () => {
    const old = item();
    delete old.not_in_ledger;
    assert.equal(rowsNotInLedger(old), 0);
    assert.equal(rowsNotInLedger(item()), 2);
  });

  test('every row message has its Arabic twin with the same placeholders', () => {
    for (const key of Object.keys(financeConnectionsEn).filter((k) => k.startsWith('fimprows.') || k.startsWith('fconn.imports.'))) {
      const en = financeConnectionsEn[key as keyof typeof financeConnectionsEn];
      const ar = financeConnectionsAr[key as keyof typeof financeConnectionsAr];
      assert.ok(ar, key);
      assert.deepEqual(placeholdersOf(ar), placeholdersOf(en), key);
    }
  });
});
