// The statement matching engine's answers in the import preview (docs/finance.md#statement-matching). Pure helpers:
// which group a row belongs to, what happens to it with the person's ticks, the decisions sent back to the API,
// and the plain-language reason, FX breakdown and balance check text. The API decides again on import.
import type { FinanceReconciliation, FinanceRowDecision, FinanceRowFx, FinanceRowMatch, FinanceMatchedRecord } from '@anum/contracts';
import { formatAmount as rawAmount, isolate } from './finance.ts';

/** Amounts and dates keep their left-to-right order inside Arabic sentences. */
const formatAmount = (value: string | null | undefined, currency: string) => isolate(rawAmount(value, currency));
const day = (value: string | null | undefined) => (value ? isolate(value) : '-');
import { t, type MessageKey } from './i18n.ts';

/** One statement row as the preview shows it (CSV line or PDF row). */
export interface MatchRow {
  id: string;
  line: number;
  posted_on: string;
  description: string;
  amount: string;
  match: FinanceRowMatch;
  fx?: FinanceRowFx | null;
}

/** The person's choice for one row; anything unset follows the engine's default. */
export interface Choice { action?: 'import' | 'skip' | null; bill?: boolean | null; transfer?: boolean | null; correct?: boolean | null }

export type GroupId = 'decide' | 'matched' | 'bills' | 'transfers' | 'recorded' | 'new';
export const GROUP_ORDER: readonly GroupId[] = ['decide', 'matched', 'bills', 'transfers', 'new', 'recorded'];
export const GROUP_PAGE = 25;

export type Outcome = 'import' | 'attach' | 'review' | 'skip' | 'duplicate';

/** A row needs the person when it is a possible duplicate, or a bill or transfer match is weak. */
export function needsDecision(match: FinanceRowMatch): boolean {
  return match.status === 'possible_duplicate' || match.bill?.strength === 'weak' || match.transfer?.strength === 'weak';
}

/** The groups a row is listed in: its status, and also "Pays a bill" or "Transfers" when it does. */
export function groupsFor(match: FinanceRowMatch): GroupId[] {
  const out: GroupId[] = [];
  if (needsDecision(match)) out.push('decide');
  if (match.status === 'matched_existing') out.push('matched');
  if (match.status === 'exact_duplicate') out.push('recorded');
  if (match.status === 'new' && !needsDecision(match)) out.push('new');
  if (match.bill?.strength === 'strong' && match.status !== 'exact_duplicate') out.push('bills');
  if (match.transfer?.strength === 'strong' && match.status !== 'exact_duplicate') out.push('transfers');
  return out;
}

export function groupRows<T extends MatchRow>(rows: readonly T[]): Record<GroupId, T[]> {
  const groups: Record<GroupId, T[]> = { decide: [], matched: [], bills: [], transfers: [], recorded: [], new: [] };
  for (const row of rows) for (const id of groupsFor(row.match)) groups[id].push(row);
  return groups;
}

/** A fuzzy "same statement row" may be imported anyway; a row seen before by its hash never can. */
export function canImportAnyway(match: FinanceRowMatch): boolean {
  return match.status === 'possible_duplicate' || (match.status === 'exact_duplicate' && match.code === 'same_statement_row');
}

/** What the import will do with the row, given the person's choice. */
export function outcomeOf(match: FinanceRowMatch, choice: Choice | undefined): Outcome {
  if (choice?.action === 'skip') return 'skip';
  if (choice?.action === 'import' && (canImportAnyway(match) || match.status === 'new')) return 'import';
  if (match.status === 'exact_duplicate') return 'duplicate';
  if (match.status === 'matched_existing') return choice?.action === 'import' ? 'import' : 'attach';
  if (match.status === 'possible_duplicate') return 'review';
  return 'import';
}

export function billOn(match: FinanceRowMatch, choice: Choice | undefined): boolean {
  return Boolean(match.bill) && choice?.bill !== false;
}

export function transferOn(match: FinanceRowMatch, choice: Choice | undefined): boolean {
  if (!match.transfer) return false;
  return match.transfer.strength === 'strong' ? choice?.transfer !== false : choice?.transfer === true;
}

export function correctOn(match: FinanceRowMatch, choice: Choice | undefined): boolean {
  return match.amount_difference !== null && match.status === 'matched_existing' && choice?.correct !== false;
}

/** Only the rows where the person changed the default, as the API reads them. */
export function decisionsFor(rows: readonly MatchRow[], choices: Readonly<Record<string, Choice>>): FinanceRowDecision[] {
  const out: FinanceRowDecision[] = [];
  for (const row of rows) {
    const choice = choices[row.id];
    if (!choice) continue;
    const decision: FinanceRowDecision = { line: row.line };
    if (choice.action) decision.action = choice.action;
    if (choice.bill === false) decision.bill = false;
    if (typeof choice.transfer === 'boolean') decision.transfer = choice.transfer;
    if (choice.correct === false) decision.correct = false;
    if (Object.keys(decision).length > 1) out.push(decision);
  }
  return out;
}

export interface Tally { import: number; attach: number; review: number; skip: number; duplicate: number; bills: number; transfers: number }

export function tally(rows: readonly MatchRow[], choices: Readonly<Record<string, Choice>>): Tally {
  const out: Tally = { import: 0, attach: 0, review: 0, skip: 0, duplicate: 0, bills: 0, transfers: 0 };
  for (const row of rows) {
    const choice = choices[row.id];
    const result = outcomeOf(row.match, choice);
    out[result] += 1;
    if (result === 'import' || result === 'attach') {
      if (billOn(row.match, choice)) out.bills += 1;
      if (transferOn(row.match, choice)) out.transfers += 1;
    }
  }
  return out;
}

/** "SMS from card ending 1234", "Apple Pay", "an entry you made", "a statement". */
export function sourceText(record: FinanceMatchedRecord): string {
  const source = ['sms', 'wallet', 'manual', 'statement'].includes(record.source) ? record.source : 'statement';
  const name = t(`fmatch.source.${source}` as MessageKey);
  return record.last4 && (source === 'sms' || source === 'wallet') ? t('fmatch.source.card', { source: name, last4: record.last4 }) : name;
}

function lagText(days: number | null): string {
  return days ? t('fmatch.lag', { count: days }) : t('fmatch.sameDay');
}

/** The plain-language reason for the row's status, in the current language. */
export function reasonText(match: FinanceRowMatch, currency: string): string {
  const record = match.record;
  switch (match.code) {
    case 'imported_before': return t('fmatch.reason.imported_before', { date: day(record?.posted_on) });
    case 'reconciled_before': return t('fmatch.reason.reconciled_before', { source: record ? sourceText(record) : '-', date: day(record?.posted_on) });
    case 'waiting_review': return t('fmatch.reason.waiting_review');
    case 'same_statement_row': return t('fmatch.reason.same_statement_row', { source: record ? sourceText(record) : '-' });
    case 'same_reference': return t('fmatch.reason.same_reference', { source: record ? sourceText(record) : '-', when: lagText(match.lag_days) });
    case 'same_amount_merchant': return t('fmatch.reason.same_amount_merchant', { source: record ? sourceText(record) : '-', when: lagText(match.lag_days) });
    case 'same_original_amount': return t('fmatch.reason.same_original_amount', {
      source: record ? sourceText(record) : '-', when: lagText(match.lag_days), recorded: formatAmount(record?.amount ?? null, currency) });
    case 'two_candidates': return t('fmatch.reason.two_candidates', { count: match.candidates.length });
    case 'amount_date_only': return t('fmatch.reason.amount_date_only', { source: match.candidates[0] ? sourceText(match.candidates[0]) : '-' });
    default: return t('fmatch.reason.no_match');
  }
}

/** "Original USD 20.00 at 3.84 (printed) · Fee SAR 1.20 · Expected SAR 76.80: billed 0.20 more". */
export function fxText(fx: FinanceRowFx | null | undefined, currency: string): string[] {
  if (!fx) return [];
  const lines: string[] = [];
  if (fx.original_amount && fx.original_currency) lines.push(t('fmatch.fx.original', { amount: formatAmount(fx.original_amount, fx.original_currency) }));
  if (fx.rate) lines.push(t(fx.rate_source === 'implied' ? 'fmatch.fx.rateImplied' : 'fmatch.fx.rate', { rate: isolate(fx.rate) }));
  if (fx.fee) lines.push(t('fmatch.fx.fee', { amount: formatAmount(fx.fee, fx.fee_currency ?? currency) }));
  if (fx.markup_percent) lines.push(t('fmatch.fx.markup', { percent: fx.markup_percent }));
  if (fx.expected && fx.check === 'ties') lines.push(t('fmatch.fx.ties', { expected: formatAmount(fx.expected, currency) }));
  if (fx.expected && fx.check === 'differs' && fx.difference) {
    lines.push(t('fmatch.fx.differs', { expected: formatAmount(fx.expected, currency), difference: formatAmount(fx.difference.replace('-', ''), currency),
      direction: t(fx.difference.startsWith('-') ? 'fmatch.fx.less' : 'fmatch.fx.more') }));
  }
  if (fx.implied_markup_percent && fx.implied_markup_percent !== '0.00') lines.push(t('fmatch.fx.impliedMarkup', { percent: fx.implied_markup_percent }));
  return lines;
}

/** The balance check banner: tied, the difference, or why it cannot be checked. */
export function reconciliationText(check: FinanceReconciliation, currency: string): { tone: 'ok' | 'error' | 'info'; text: string } {
  if (check.status === 'tied') {
    return { tone: 'ok', text: t('fmatch.recon.tied', { opening: formatAmount(check.opening, currency), total: formatAmount(check.rows_total, currency),
      closing: formatAmount(check.closing, currency) }) };
  }
  if (check.status === 'differs') {
    return { tone: 'error', text: t('fmatch.recon.differs', { opening: formatAmount(check.opening, currency), total: formatAmount(check.rows_total, currency),
      expected: formatAmount(check.expected_closing, currency), closing: formatAmount(check.closing, currency), difference: formatAmount(check.difference, currency) }) };
  }
  return { tone: 'info', text: t('fmatch.recon.unknown') };
}
