// Finance, Reconciliation (docs/finance.md#reconciliation-sessions): the pure helpers the page uses. Every figure is
// the API's; nothing here adds amounts across currencies or moves money. Covered by test/financeReconciliation.test.ts;
// the API calls are in financeReconciliationApi.ts.
import type { FinanceReconDetail, FinanceReconException, FinanceReconHealth, FinanceReconInput, FinanceReconQueueItem, FinanceReconSeverity } from '@anum/contracts';
import { parseAmount, type Checked } from './finance.ts';

export const EXCEPTIONS_PAGE = 20;

/**
 * The session a link opens: `#finance-reconciliation?session=<id>` (the suggestion queue's Review action), else null.
 * Only an id of letters, digits, `_` and `-` is taken.
 */
export function sessionFromHash(hash: string): string | null {
  const [path, query = ''] = hash.split('?');
  if (path !== '#finance-reconciliation') return null;
  const id = new URLSearchParams(query).get('session');
  return id && /^[A-Za-z0-9_-]{1,80}$/.test(id) ? id : null;
}
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

/** The statement figure as the owner types it: a card or loan shows what is owed as a positive figure. */
export function typedFromLedger(kind: string, stored: string | null): string {
  if (stored === null) return '';
  if ((kind === 'card' || kind === 'loan') && Number(stored) !== 0) return stored.startsWith('-') ? stored.slice(1) : `-${stored}`;
  return stored;
}

export interface EditDraft { opening: string; closing: string; due: string; minimum: string; dueOn: string }

/** The edit form starts from the session's stored statement figures. */
export function editDraftOf(d: Pick<FinanceReconDetail, 'account_kind' | 'figures' | 'card'>): EditDraft {
  return {
    opening: typedFromLedger(d.account_kind, d.figures.statement_opening), closing: typedFromLedger(d.account_kind, d.figures.statement_closing),
    due: d.card?.amount_due ?? '', minimum: d.card?.minimum_due ?? '', dueOn: d.card?.due_on ?? '',
  };
}

/** The PATCH body for the statement figures as printed (compare and set on `version`), or what to fix. */
export function editBody(draft: EditDraft, d: Pick<FinanceReconDetail, 'account_kind' | 'currency' | 'version'>): Checked<Partial<FinanceReconInput> & { version: number }> {
  const closing = draft.closing.trim();
  if (!closing) return { ok: false, error: 'closing' };
  for (const typed of [closing, draft.opening.trim()]) {
    if (!typed) continue;
    const check = parseAmount(ledgerClosing(d.account_kind, typed), d.currency);
    if (!check.ok) return check;
  }
  const card = d.account_kind === 'card';
  for (const typed of card ? [draft.due.trim(), draft.minimum.trim()] : []) {
    if (!typed) continue;
    const check = parseAmount(typed, d.currency, { signed: false });
    if (!check.ok) return check;
  }
  return {
    ok: true,
    value: {
      version: d.version, statement_closing: ledgerClosing(d.account_kind, closing), statement_opening: draft.opening.trim() ? ledgerClosing(d.account_kind, draft.opening.trim()) : null,
      ...(card ? { amount_due: draft.due.trim() || null, minimum_due: draft.minimum.trim() || null, due_on: draft.dueOn || null } : {}),
    },
  };
}

/** The exceptions table across accounts: GET /reconciliations/exceptions orders by these columns. */
export type QueueSort = 'severity' | 'account' | 'period' | 'kind' | 'posted' | 'amount';
export const QUEUE_SORTS: readonly QueueSort[] = ['severity', 'account', 'period', 'kind', 'posted', 'amount'];
export interface QueueOrder { sort: QueueSort; desc: boolean }
export const DEFAULT_QUEUE_ORDER: QueueOrder = { sort: 'severity', desc: false };
export const QUEUE_PAGE = 20;

/** A header click sorts that column; the same column again turns it round. Dates and amounts start with the latest and largest. */
export function nextQueueOrder(current: QueueOrder, sort: QueueSort): QueueOrder {
  if (current.sort === sort) return { sort, desc: !current.desc };
  return { sort, desc: sort === 'posted' || sort === 'amount' || sort === 'period' };
}

/** What settles a queued exception: its session, when it has one; otherwise an import (a missing month) or the register. */
export function queueAction(item: Pick<FinanceReconQueueItem, 'reconciliation_id' | 'exception'>): 'session' | 'import' | 'transactions' {
  if (item.reconciliation_id) return 'session';
  return item.exception.actions.includes('import') ? 'import' : 'transactions';
}
