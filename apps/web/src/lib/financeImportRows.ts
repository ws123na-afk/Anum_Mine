// Import history rows (docs/finance.md#import-history): the filters the row list offers, with the counts the import
// stored, and the one line the history shows for an import that is not fully in the ledger.
import type { FinanceImport, FinanceRowDispositionKind } from '@anum/contracts';

export type ImportRowFilter = 'all' | 'not_in_ledger' | FinanceRowDispositionKind;

export interface ImportRowOption { id: ImportRowFilter; count: number | null }

/** Every filter, with its count where the import stored one; empty groups are left out (all and not_in_ledger stay). */
export function importRowFilters(item: FinanceImport): ImportRowOption[] {
  const options: ImportRowOption[] = [
    { id: 'all', count: null },
    { id: 'not_in_ledger', count: item.not_in_ledger ?? 0 },
    { id: 'held_for_review', count: item.held_for_review ?? 0 },
    { id: 'unticked', count: item.skipped ?? 0 },
    { id: 'attached_to_existing', count: item.reconciled ?? 0 },
    { id: 'imported', count: item.imported_count },
    { id: 'duplicate_skipped', count: item.duplicate_count },
    { id: 'dropped_by_reader', count: item.dropped ?? 0 },
  ];
  return options.filter((option) => option.id === 'all' || option.id === 'not_in_ledger' || (option.count ?? 0) > 0);
}

/** Rows of the statement the ledger does not hold (held for review or left out by the person). */
export function rowsNotInLedger(item: FinanceImport): number {
  return Math.max(0, item.not_in_ledger ?? 0);
}
