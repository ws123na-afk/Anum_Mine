// The fixed asset register, loan schedules, the period-end revaluation and the group balance sheet on the web
// (docs/business-books.md#fixed-asset-register, #loan-schedules, #period-end-revaluation, #group-balance-sheet): labels, the
// forms and what a form is missing. Pure, so the unit tests cover it without a browser. The API is the authority: it
// works out every figure, never guesses a rate and refuses anything dated in a closed period.
import type {
  BusinessLoanInput, DepreciationMethod, EarlyRepayment, FixedAsset, FixedAssetUpdate, GroupBalanceRow, LoanActualRow, LoanFrequency, LoanMethod,
  LoanRepaymentInput, RevaluationCurrency,
} from '@anum/contracts';
import { t, type MessageKey } from './i18n.ts';

const label = (key: string) => t(key as MessageKey);
const AMOUNT = /^\d{1,12}(\.\d{1,3})?$/;
const RATE = /^\d{1,3}(\.\d{1,4})?$/;
const ISO = /^\d{4}-\d{2}-\d{2}$/;

export const ASSET_PARTS = ['register', 'loans', 'revaluation'] as const;
export const methodLabel = (method: DepreciationMethod) => label(`assets.method.${method}`);
export const frequencyLabel = (frequency: LoanFrequency) => label(`loans.frequency.${frequency}`);
export const loanMethodLabel = (method: LoanMethod) => label(`loans.method.${method}`);
export const earlyLabel = (value: EarlyRepayment) => label(`loans.early.${value}`);
export const actualStateLabel = (state: LoanActualRow['state']) => label(`loans.state.${state}`);
export const revaluationStatusLabel = (status: RevaluationCurrency['status']) => label(`reval.status.${status}`);
export const groupRowLabel = (key: GroupBalanceRow) => label(`groupbs.row.${key}`);
export const GROUP_TOTAL_ROWS: GroupBalanceRow[] = ['total_assets', 'total_liabilities', 'total_equity', 'liabilities_and_equity'];

export const PERIOD_MONTHS: Record<LoanFrequency, number> = { monthly: 1, quarterly: 3, yearly: 12 };

export type AssetForm = { name: string; category: string; useful_life_months: string; residual_value: string; method: DepreciationMethod; rate_pct: string;
  depreciation_start: string };

export function assetFormFrom(asset: FixedAsset): AssetForm {
  return { name: asset.name, category: asset.category, useful_life_months: asset.useful_life_months ? String(asset.useful_life_months) : '',
    residual_value: asset.residual_value, method: asset.method, rate_pct: asset.rate_pct ?? '', depreciation_start: asset.depreciation_start.slice(0, 7) };
}

/** Why the asset form cannot be sent, or null. ``cost`` is the asset's cost (the residual value stays below it). */
export function assetProblem(form: AssetForm, cost: string): MessageKey | null {
  if (form.name.trim().length < 2) return 'assets.problem.name';
  const life = form.useful_life_months.trim();
  if (life && (!/^\d{1,3}$/.test(life) || Number(life) < 1 || Number(life) > 600)) return 'assets.problem.life';
  if (!AMOUNT.test(form.residual_value.trim() || '0') || Number(form.residual_value || '0') >= Number(cost)) return 'assets.problem.residual';
  if (form.method === 'reducing_balance' && (!RATE.test(form.rate_pct.trim()) || Number(form.rate_pct) <= 0 || Number(form.rate_pct) > 100)) return 'assets.problem.rate';
  if (!/^\d{4}-\d{2}$/.test(form.depreciation_start)) return 'assets.problem.start';
  return null;
}

export function assetBody(form: AssetForm, version: number): FixedAssetUpdate {
  return { name: form.name.trim(), category: form.category.trim() || 'equipment', useful_life_months: form.useful_life_months.trim() ? Number(form.useful_life_months) : null,
    residual_value: form.residual_value.trim() || '0', method: form.method, rate_pct: form.method === 'reducing_balance' ? form.rate_pct.trim() : null,
    depreciation_start: `${form.depreciation_start}-01`, version };
}

export type LoanForm = { lender: string; principal: string; currency: string; annual_rate: string; start_date: string; term_months: string;
  frequency: LoanFrequency; method: LoanMethod; early_repayment: EarlyRepayment; received_adjustment_id: string };

export function emptyLoan(currency: string, today: string): LoanForm {
  return { lender: '', principal: '', currency, annual_rate: '', start_date: today, term_months: '12', frequency: 'monthly', method: 'annuity',
    early_repayment: 'reduce_term', received_adjustment_id: '' };
}

export function loanProblem(form: LoanForm, today: string): MessageKey | null {
  if (form.lender.trim().length < 2) return 'loans.problem.lender';
  if (!AMOUNT.test(form.principal.trim()) || Number(form.principal) <= 0) return 'loans.problem.principal';
  if (!RATE.test(form.annual_rate.trim()) || Number(form.annual_rate) > 100) return 'loans.problem.rate';
  if (!ISO.test(form.start_date) || form.start_date > today) return 'loans.problem.start';
  const term = Number(form.term_months);
  if (!/^\d{1,3}$/.test(form.term_months.trim()) || term < 1 || term > 600) return 'loans.problem.term';
  if (term % PERIOD_MONTHS[form.frequency] !== 0) return 'loans.problem.termFrequency';
  return null;
}

export function loanBody(form: LoanForm): BusinessLoanInput {
  return { lender: form.lender.trim(), principal: form.principal.trim(), currency: form.currency, annual_rate: form.annual_rate.trim(),
    start_date: form.start_date, term_months: Number(form.term_months), frequency: form.frequency, method: form.method,
    early_repayment: form.early_repayment, received_adjustment_id: form.received_adjustment_id || null };
}

export type RepaymentForm = { paid_on: string; amount: string; match: string };

/** ``match`` is '' (cash paid by hand), ``bill:<id>`` or ``adjustment:<id>``. */
export function repaymentProblem(form: RepaymentForm, today: string, start: string): MessageKey | null {
  if (!ISO.test(form.paid_on) || form.paid_on > today) return 'loans.problem.paidOn';
  if (form.paid_on < start) return 'loans.problem.beforeStart';
  if (!AMOUNT.test(form.amount.trim()) || Number(form.amount) <= 0) return 'loans.problem.amount';
  return null;
}

export function repaymentBody(form: RepaymentForm): LoanRepaymentInput {
  const [kind, id] = form.match.split(':');
  return { paid_on: form.paid_on, amount: form.amount.trim(), bill_id: kind === 'bill' ? id ?? null : null, adjustment_id: kind === 'adjustment' ? id ?? null : null };
}

/** The month ends a revaluation can be run on: the last twelve that have ended, newest first. */
export function monthEnds(today: string, count = 12): string[] {
  const [year, month] = today.split('-').map(Number);
  const out: string[] = [];
  for (let back = 0; back < count; back += 1) out.push(new Date(Date.UTC(year, month - 1 - back, 0)).toISOString().slice(0, 10));
  return out;
}

/** The share of a loan already repaid, 0 to 100, for the progress bar. */
export function repaidShare(principal: string, outstanding: string): number {
  const whole = Number(principal);
  if (!Number.isFinite(whole) || whole <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round(((whole - Number(outstanding)) / whole) * 100)));
}
