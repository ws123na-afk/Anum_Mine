// A repayment split across loans, foreign balances in the base currency and personal trips and projects
// (docs/finance.md#lent-and-borrowed, #foreign-currency-balances, #trips-and-projects). Pure, so the unit tests cover it
// without a browser. The API checks every figure again; these only say early what it would refuse.
import type { FinanceFxRevaluationRow, FinancePersonLoan, FinanceTagInput } from '@anum/contracts';
import { fromMinor, toMinor } from './financeDebtPlan.ts';

export interface SplitLine { loanId: string; amount: string }

export type SplitProblem = 'too_few' | 'invalid_amount' | 'over_loan' | 'over_payment' | null;

/** The loans a split may cover: the same person, direction and currency as the loan opened, still open, earliest due first. */
export function splitCandidates(loans: readonly FinancePersonLoan[], from: FinancePersonLoan): FinancePersonLoan[] {
  const person = from.person.trim().toLocaleLowerCase();
  return loans.filter((l) => l.direction === from.direction && l.currency === from.currency && l.person.trim().toLocaleLowerCase() === person
    && l.status !== 'repaid' && l.status !== 'closed' && Number(l.outstanding) > 0)
    .sort((a, b) => (a.next_due_on ?? a.due_on ?? a.lent_on).localeCompare(b.next_due_on ?? b.due_on ?? b.lent_on) || a.id.localeCompare(b.id));
}

/** What each loan's line starts at: what is due on it now (overdue, else the next repayment), never more than what is left. */
export function suggestedPart(loan: FinancePersonLoan): string {
  const due = Number(loan.overdue_amount) > 0 ? loan.overdue_amount : loan.next_due_amount;
  return toMinor(due, loan.currency) > 0 ? fromMinor(Math.min(toMinor(due, loan.currency), toMinor(loan.outstanding, loan.currency)), loan.currency) : '';
}

/** The parts that carry an amount, their total in minor units, and the first thing the API would refuse. */
export function checkSplit(lines: readonly SplitLine[], loans: readonly FinancePersonLoan[], remaining: string, currency: string):
  { parts: SplitLine[]; total: string; problem: SplitProblem } {
  const parts = lines.filter((l) => l.amount.trim() !== '');
  const total = parts.reduce((sum, l) => sum + toMinor(l.amount, currency), 0);
  let problem: SplitProblem = null;
  if (parts.some((l) => !/^\d+(\.\d+)?$/.test(l.amount.trim()) || toMinor(l.amount, currency) <= 0)) problem = 'invalid_amount';
  else if (parts.length < 2) problem = 'too_few';
  else if (parts.some((l) => toMinor(l.amount, currency) > toMinor(loans.find((x) => x.id === l.loanId)?.outstanding ?? '0', currency))) problem = 'over_loan';
  else if (total > toMinor(remaining, currency)) problem = 'over_payment';
  return { parts, total: fromMinor(total, currency), problem };
}

export type RevaluationTone = 'ok' | 'bad' | 'muted' | 'warn';

/** A gain reads as good, a loss as bad, a missing rate as a warning, an empty balance as nothing. */
export function revaluationTone(row: Pick<FinanceFxRevaluationRow, 'status' | 'unrealised'>): RevaluationTone {
  if (row.status === 'rate_not_available' || row.status === 'cost_rate_not_available') return 'warn';
  if (row.status !== 'ok' || row.unrealised === null) return 'muted';
  const n = Number(row.unrealised);
  return n > 0 ? 'ok' : n < 0 ? 'bad' : 'muted';
}

/** The rows worth showing: accounts with money in them (a missing rate is shown, an empty balance is not). */
export function revaluationRows(rows: readonly FinanceFxRevaluationRow[]): FinanceFxRevaluationRow[] {
  return rows.filter((r) => r.status !== 'nothing_to_revalue');
}

export interface TagForm { kind: 'trip' | 'project'; name: string; starts_on: string; ends_on: string; keywords: string; notes: string; archived: boolean }

export type TagProblem = 'name' | 'dates' | null;

/** The form checked as the API does; a trip needs no dates to be saved, but only a dated trip is ever proposed. */
export function checkTag(form: TagForm): TagProblem {
  if (!form.name.trim()) return 'name';
  if (form.starts_on && form.ends_on && form.ends_on < form.starts_on) return 'dates';
  return null;
}

export function tagBody(form: TagForm): FinanceTagInput {
  return { kind: form.kind, name: form.name.trim(), starts_on: form.starts_on || null, ends_on: form.ends_on || null,
    keywords: form.keywords.trim() || null, notes: form.notes.trim() || null, archived: form.archived };
}

/** A trip without both dates is never proposed by the suggestions: the page says so. */
export const tripNeedsDates = (form: Pick<TagForm, 'kind' | 'starts_on' | 'ends_on'>): boolean => form.kind === 'trip' && (!form.starts_on || !form.ends_on);
