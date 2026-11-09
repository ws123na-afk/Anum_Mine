// The sentence shown after a statement import (CSV or PDF): rows added, already there, unreadable, then what the matching
// engine did (docs/finance.md#statement-matching). Kept apart from FinanceView so it is unit tested and shared.
import type { FinanceImport } from '@anum/contracts';
import { t } from './i18n.ts';

export function importNotice(result: FinanceImport): string {
  let text = t('finance.import.done', { imported: result.imported_count, duplicates: result.duplicate_count, errors: result.error_count });
  if ((result.reconciled ?? 0) || (result.held_for_review ?? 0)) {
    text += ` ${t('fsms.import.reconciled', { reconciled: result.reconciled ?? 0, review: result.held_for_review ?? 0 })}`;
  }
  if ((result.corrected ?? 0) || (result.bills_paid ?? 0) || (result.bills_review ?? 0) || (result.transfers ?? 0)) {
    text += ` ${t('fmatch.done.extras', { corrected: result.corrected ?? 0, bills: result.bills_paid ?? 0, billsReview: result.bills_review ?? 0, transfers: result.transfers ?? 0 })}`;
  }
  return text;
}
