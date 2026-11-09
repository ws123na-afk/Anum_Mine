// Finance, Reconciliation (docs/finance.md#reconciliation-sessions): the pure helpers the page uses. Every figure is
// the API's; nothing here adds amounts across currencies or moves money. Covered by test/financeReconciliation.test.ts;
// the API calls are in financeReconciliationApi.ts.
import type { FinanceReconException, FinanceReconHealth, FinanceReconSeverity } from '@anum/contracts';

export const EXCEPTIONS_PAGE = 20;
export type SeverityFilter = 'all' | FinanceReconSeverity;

/** A card or loan statement prints what is owed as a positive figure; the ledger keeps it negative. */
export function ledgerClosing(kind: string, typed: string): string {
  const value = typed.trim();
  if ((kind === 'card' || kind === 'loan') && value && !value.startsWith('-') && Number(value) !== 0) return `-${value}`;
  return value;
}

/** The ledger records that may be the same payment as a statement row: equal amounts only, never a near miss. */
export function matchCandidates(row: FinanceReconException, ledger: readonly FinanceReconException[]): FinanceReconException[] {
  if (row.amount === null) return [];
  return ledger.filter((e) => e.transaction_id && (e.kind === 'unmatched_payment' || e.kind === 'unmatched_deposit' || e.kind === 'pending')
    && Number(e.amount) === Number(row.amount));
}

/** Whether a figure is exactly zero ("0.00", "-0.000"). */
export const isZero = (value: string | null | undefined): boolean => value !== null && value !== undefined && Number(value) === 0;

/** The tone of a session: completed and unchanged is good, changed or unresolved needs the owner. */
export function sessionTone(s: { status: string; state: string; changed: boolean }): 'ok' | 'warn' | 'bad' | 'info' {
  if (s.changed) return 'bad';
  if (s.status === 'completed') return 'ok';
  return s.state === 'balanced' ? 'info' : 'warn';
}

/** The key of a session's status label: completed, changed, ready to complete or unresolved. */
export function sessionStatus(s: { status: string; state: string; changed: boolean }): 'completed' | 'changed' | 'ready' | 'unresolved' {
  if (s.changed) return 'changed';
  if (s.status === 'completed') return 'completed';
  return s.state === 'balanced' ? 'ready' : 'unresolved';
}

/** The health card's next step as a message key plus what the button does. */
export function nextStep(h: FinanceReconHealth): { key: string; action: 'import' | 'reconcile' | 'open' | null; reconciliationId: string | null } {
  switch (h.next_step) {
    case 'import_statement': return { key: h.next_month ? 'frec.next.import' : 'frec.next.importAny', action: 'import', reconciliationId: null };
    case 'reconcile': return { key: 'frec.next.reconcile', action: 'reconcile', reconciliationId: null };
    case 'finish_reconciliation': return { key: 'frec.next.finish', action: 'open', reconciliationId: h.open_reconciliation_id };
    case 'review_changes': return { key: 'frec.next.review', action: 'open', reconciliationId: h.changed_reconciliation_id };
    case 'up_to_date': return { key: 'frec.next.upToDate', action: null, reconciliationId: null };
    default: return { key: 'frec.next.none', action: null, reconciliationId: null };
  }
}

/** The first and last day of the month before `today` ("YYYY-MM-DD"), the usual statement period. */
export function lastMonth(today: string): { start: string; end: string } {
  const [year, month] = today.slice(0, 7).split('-').map(Number);
  const first = new Date(Date.UTC(year, month - 2, 1));
  const last = new Date(Date.UTC(year, month - 1, 0));
  return { start: first.toISOString().slice(0, 10), end: last.toISOString().slice(0, 10) };
}

/** The reason codes the page explains in words; anything else (a currency code, a type) is shown as data, not translated. */
export const REASONS: ReadonlySet<string> = new Set([
  'held_for_review', 'unticked', 'dropped_by_reader', 'duplicate_skipped', 'sms', 'wallet', 'manual', 'statement',
  'rent_shown_as_incoming', 'money_in_type_paid_out', 'spending_type_paid_in',
]);
