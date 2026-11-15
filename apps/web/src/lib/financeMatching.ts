// Automatic matching (docs/finance-matching.md): the pure helpers its page uses, covered by test/financeMatching.test.ts
// without a browser (the requests are in financeMatchingApi.ts). The server decides everything; these only shape the request
// and the words.
import type {
  FinanceBackfillRun, FinanceInstallmentStatus, FinanceMatchOutcome, FinanceMatchingPolicy, FinanceMatchingPolicyInput, FinanceMatchingPolicyView,
} from '@anum/contracts';

export const SWITCHES = ['match_transactions', 'reconcile_installments', 'link_recurring_bills', 'link_card_repayments', 'link_bank_loan_repayments',
  'prevent_duplicates', 'reprocess_history', 'notify_exceptions'] as const;
export type MatchSwitch = typeof SWITCHES[number];
export const SOURCES = ['statement', 'sms', 'wallet', 'manual'] as const;
export const REVIEW_TABS: readonly FinanceMatchOutcome[] = ['review', 'auto_linked', 'stale'];
export const COUNT_KEYS = ['transactions', 'matched', 'auto_linked', 'verified', 'provisional', 'ambiguous', 'review', 'missing_source', 'duplicates',
  'corrected', 'differences', 'already_linked', 'kept_owner_choice', 'errors'] as const;
export const CONFIDENCE_STEPS = ['0.80', '0.85', '0.90', '0.95', '1.00'] as const;

/** The editable part of a stored policy: what PUT /automation/matching takes back. */
export function policyInput(policy: FinanceMatchingPolicy, approve = false): FinanceMatchingPolicyInput {
  const { revision, approved_at: _approvedAt, approved_by: _approvedBy, ...rest } = policy;
  return { ...rest, approve, expected_revision: revision };
}

/** Did the form change anything the stored policy holds? A change withdraws the approval unless it is approved again. */
export function policyChanged(stored: FinanceMatchingPolicy, form: FinanceMatchingPolicy): boolean {
  const keys: (keyof FinanceMatchingPolicy)[] = [...SWITCHES, 'min_confidence', 'review_confidence'];
  if (keys.some((k) => String(stored[k]) !== String(form[k]))) return true;
  const same = (a: string[], b: string[]) => a.length === b.length && [...a].sort().join() === [...b].sort().join();
  return !same(stored.settled_sources, form.settled_sources) || !same(stored.source_account_ids, form.source_account_ids);
}

/** Nothing links on its own unless the main switch, one kind, an account and the approval are all there. */
export function linksOnItsOwn(view: Pick<FinanceMatchingPolicyView, 'approved' | 'policy'>): boolean {
  const p = view.policy;
  const anyKind = p.reconcile_installments || p.link_recurring_bills || p.link_card_repayments || p.link_bank_loan_repayments;
  return view.approved && p.match_transactions && anyKind && p.source_account_ids.length > 0;
}

export function canApplyHistory(view: Pick<FinanceMatchingPolicyView, 'approved' | 'policy'>): boolean {
  return view.approved && view.policy.match_transactions && view.policy.reprocess_history;
}

/** "0.92" as a whole percent for people: 92%. */
export function percent(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '-';
  const n = Number(value);
  return Number.isFinite(n) ? `${Math.round(n * 100)}%` : '-';
}

/** The words key of a reason code; a ledger refusal (``link_refused_<code>``) reads as one. */
export function reasonKey(code: string): string {
  return code.startsWith('link_refused') ? 'fmatch.reason.link_refused' : `fmatch.reason.${code}`;
}

export type Tone = 'good' | 'warn' | 'bad' | 'neutral';

export function installmentTone(status: FinanceInstallmentStatus): Tone {
  if (status === 'verified_paid' || status === 'refunded') return 'good';
  if (status === 'overdue') return 'bad';
  if (status === 'partly_paid' || status === 'provisional' || status === 'review' || status === 'provider_paid' || status === 'no_bill') return 'warn';
  return 'neutral';
}

/** Done months over all months of a run, 0 to 100, for the progress bar. */
export function runProgress(run: Pick<FinanceBackfillRun, 'start_on' | 'end_on' | 'cursor_on' | 'status'>): number {
  if (run.status === 'done' || run.status === 'undone') return 100;
  if (!run.start_on || !run.end_on) return 0;
  const months = (day: string) => Number(day.slice(0, 4)) * 12 + Number(day.slice(5, 7));
  const total = months(run.end_on) - months(run.start_on) + 1;
  const done = run.cursor_on ? months(run.cursor_on) - months(run.start_on) + 1 : 0;
  return Math.max(0, Math.min(100, Math.round((done / Math.max(total, 1)) * 100)));
}

/** The counts worth showing (non-zero), in the fixed order. */
export function shownCounts(counts: Record<string, number>): { key: typeof COUNT_KEYS[number]; value: number }[] {
  return COUNT_KEYS.filter((k) => (counts[k] ?? 0) > 0).map((k) => ({ key: k, value: counts[k] }));
}
