// The debt overview, cards, alerts and advisor answers: the shapes the API answers and pure helpers for the pages
// (docs/finance.md#debt-overview, #credit-cards, #financial-alerts, #advisor-answers). No I/O here, so the unit tests cover it.

export type Evidence = 'verified' | 'provisional' | 'reported' | 'payslip' | 'entered';
export type ObligationStatus = 'open' | 'due_soon' | 'overdue' | 'paid_off' | 'closed' | 'disputed';
export type DebtType = 'personal_loan' | 'vehicle_finance' | 'mortgage' | 'credit_card' | 'employer_loan' | 'salary_advance' | 'bnpl' | 'informal'
  | 'borrowed_person' | 'lent_person' | 'other';
export const PROFILE_TYPES = ['personal_loan', 'vehicle_finance', 'mortgage', 'credit_card', 'employer_loan', 'salary_advance', 'bnpl', 'informal', 'other'] as const;

export interface HistoryItem { on: string | null; amount: string; currency: string; kind: string; evidence: Evidence; ref_type: string; ref_id: string | null; note: string | null }
export interface PromiseView { id: string; made_on: string; due_by: string; amount: string; note: string | null; status: 'kept' | 'part_kept' | 'broken' | 'pending'; paid: string }
export interface Obligation {
  id: string; ref_type: 'debt' | 'card' | 'person_loan' | 'installment_order' | 'salary_rule'; ref_id: string; type: DebtType; direction: 'owe' | 'owed_to_me';
  name: string; counterparty: string | null; currency: string; original_amount: string | null; start_on: string | null;
  schedule: 'monthly' | 'single' | 'revolving' | 'salary' | 'none'; interest: 'interest' | 'interest_free' | 'unknown'; rate: string | null;
  expected_installment: string | null; paid: string; remaining: string; interest_paid: string | null; fees: string | null; next_due_on: string | null;
  next_due_amount: string; overdue_amount: string; status: ObligationStatus; verified_balance: string | null; verified_on: string | null;
  difference: string | null; discrepancy: boolean; disputed_amount: string | null; unverified_amount: string; payoff_on: string | null;
  payoff_basis: string | null; assumptions: string[]; history: HistoryItem[]; promises: PromiseView[]; sources: { figure: string; source: string }[];
  missing: string[]; contact_id: string | null;
}
export interface CalendarItem { on: string; obligation_id: string; name: string; amount: string; currency: string; direction: 'owe' | 'owed_to_me';
  status: 'paid' | 'review' | 'reported_paid' | 'overdue' | 'due' }
export interface PlanRow { rank: number; obligation_id: string; name: string; currency: string; remaining: string; rate: string | null;
  reason: 'overdue' | 'highest_rate' | 'rate_unknown' | 'interest_free' }
export interface CurrencyTotal { currency: string; owe: string; owed_to_me: string; overdue: string; due_30_days: string }
export interface DebtOverview { today: string; items: Obligation[]; totals: CurrencyTotal[]; calendar: CalendarItem[]; plan: PlanRow[]; discrepancies: number; note: string }

export interface CardRecord { transaction_id: string; posted_on: string; amount: string; description: string; kind: string; evidence: 'posted' | 'pending' | 'unverified';
  linked: boolean; from_account_id: string | null; from_account_name: string | null }
export interface PayOption { code: 'full' | 'minimum'; amount: string | null; leaves: string | null; interest_estimate: string | null; months_to_clear: number | null;
  total_interest: string | null; never_clears: boolean }
export interface CardView {
  account_id: string; debt_id: string | null; name: string; last4: string | null; currency: string; outstanding: string; limit: string | null;
  limit_source: 'bank_messages' | 'entered' | null; available: string | null; utilization_percent: number | null; observed_balance: string | null;
  apr_percent: string | null; statement_day: number | null; statement_on: string | null; statement_balance: string | null; statement_minimum: string | null;
  next_closing_on: string | null; due_on: string | null; days_until_due: number | null; paid_since_statement: string; paid_provisional: string;
  payment_state: 'paid_full' | 'paid_minimum' | 'part_paid' | 'not_paid' | 'due' | 'no_statement';
  cycle: { start: string | null; end: string | null; purchases: string; refunds: string; cashback: string; fees: string; interest: string; cash_advances: string;
    installment_conversions: string; payments: string };
  check: { status: 'tied' | 'differs' | 'no_statement'; statement_on: string | null; statement_balance: string | null; ledger_balance: string | null;
    difference: string | null; unverified_rows: number };
  pay_options: PayOption[]; payments: CardRecord[]; records: CardRecord[]; evidence: Record<'posted' | 'pending' | 'unverified', number>;
  unverified_amount: string; missing: string[];
}

export interface DebtProfileInput {
  debt_type?: string | null; verified_balance?: string | null; verified_on?: string | null; disputed_amount?: string | null; dispute_note?: string | null;
  fees?: string | null; card_limit?: string | null; card_statement_day?: number | null; card_statement_balance?: string | null;
  card_statement_minimum?: string | null; card_statement_on?: string | null;
}
export interface AlertKind { kind: string; on: boolean; title: string }
export interface AlertPreviewItem { kind: string; title: string; body: string; on: string; evidence: string; on_for_you: boolean }

export const QUESTIONS = ['cash', 'owe', 'due_week', 'installment_today', 'card_missing', 'category_spend', 'words_spend', 'sent_home', 'owed_to_me',
  'loan_priority', 'after_salary', 'afford', 'subscriptions_rose', 'uncategorized', 'stale_sources', 'backfill_changes'] as const;
export type Question = typeof QUESTIONS[number];
export interface AnswerRecord { ref_type: string; ref_id: string | null; on: string | null; label: string; amount: string | null; currency: string | null; evidence: string | null }
export interface AdvisorAnswer { question: Question; as_of: string; answer: string; figures: { label: string; amount: string; currency: string }[];
  calculation: string[]; records: AnswerRecord[]; missing: string[]; complete: boolean; note: string }
export interface AnswerParams { category?: string; words?: string; country?: string; currency?: string; month?: string; amount?: string }

/** The chip tone of an obligation's status: the words carry the meaning, the colour only helps. */
export function statusTone(status: ObligationStatus): 'ok' | 'warn' | 'bad' | 'info' | 'muted' {
  return status === 'overdue' ? 'bad' : status === 'disputed' || status === 'due_soon' ? 'warn' : status === 'paid_off' || status === 'closed' ? 'ok' : 'info';
}

/** The chip tone of a payment's evidence: only a verified (matched) payment is green. */
export function evidenceTone(evidence: string | null): 'ok' | 'warn' | 'bad' | 'info' | 'muted' {
  return evidence === 'verified' || evidence === 'posted' ? 'ok' : evidence === 'reported' || evidence === 'provisional' || evidence === 'pending' ? 'warn'
    : evidence === 'overdue' ? 'bad' : 'muted';
}

/** Obligations grouped as the page shows them: what I owe first, then what is owed to me; paid off and closed last. */
export function grouped(items: Obligation[]): { owe: Obligation[]; owedToMe: Obligation[]; finished: Obligation[] } {
  const done = (o: Obligation) => o.status === 'paid_off' || o.status === 'closed';
  return { owe: items.filter((o) => o.direction === 'owe' && !done(o)), owedToMe: items.filter((o) => o.direction === 'owed_to_me' && !done(o)), finished: items.filter(done) };
}

/** Only the parameters a question uses are sent; an empty one is left out. */
export function answerParams(question: Question, values: AnswerParams): AnswerParams {
  const used: Record<Question, (keyof AnswerParams)[]> = {
    cash: [], owe: [], due_week: [], installment_today: [], card_missing: [], category_spend: ['category', 'month'], words_spend: ['words', 'month'],
    sent_home: ['country', 'currency', 'month'], owed_to_me: [], loan_priority: [], after_salary: [], afford: ['amount', 'currency'], subscriptions_rose: [],
    uncategorized: [], stale_sources: [], backfill_changes: [],
  };
  const out: AnswerParams = {};
  for (const key of used[question]) {
    const value = values[key]?.trim();
    if (value) out[key] = value;
  }
  return out;
}

/** A debt's profile body from the dialog: empty fields are sent as null (cleared). */
export function profileBody(values: Record<string, string>): DebtProfileInput {
  const text = (key: string) => (values[key] ?? '').trim() || null;
  const day = text('card_statement_day');
  return {
    debt_type: text('debt_type'), verified_balance: text('verified_balance'), verified_on: text('verified_on'), disputed_amount: text('disputed_amount'),
    dispute_note: text('dispute_note'), fees: text('fees'), card_limit: text('card_limit'), card_statement_day: day ? Number(day) : null,
    card_statement_balance: text('card_statement_balance'), card_statement_minimum: text('card_statement_minimum'), card_statement_on: text('card_statement_on'),
  };
}

const EVIDENCE = new Set(['verified', 'provisional', 'reported', 'payslip', 'entered', 'posted', 'pending', 'unverified', 'ledger', 'due', 'overdue', 'review',
  'paid', 'reported_paid', 'last_transaction', 'applied', 'undone', 'active', 'needs_review', 'not_paid', 'part_paid', 'paid_minimum', 'paid_full', 'no_statement']);
const SOURCES = new Set(['schedule_and_matched_payments', 'ledger', 'entered_less_matched_payments', 'provider_statement', 'salary_profile', 'amount_less_linked_repayments',
  'matched_bill_payments', 'lender_terms', 'schedule', 'entered', 'not_entered', 'interest_free', 'payslip']);

/** The message key for an evidence or source code an answer's record carries, or null for a code the page does not know. */
export function evidenceKey(code: string): string | null {
  return EVIDENCE.has(code) ? `fdebt.evidence.${code}` : SOURCES.has(code) ? `fdebt.source.${code}` : null;
}
