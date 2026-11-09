// The business journal on the web (docs/business-books.md#the-journal): labels, the manual entry and account forms, the
// statement rows and dates. Pure, so `pnpm --filter @anum/web test:unit` covers it without a browser. The API is the
// authority: it posts from the source documents, never guesses a rate and refuses anything dated in a closed period.
import type {
  LedgerAccount, LedgerBalanceSheet, LedgerIssue, LedgerManualInput, LedgerPnl, LedgerSourceKind, LedgerSubtype, LedgerTrialRow,
} from '@anum/contracts';
import { t, type MessageKey } from './i18n.ts';

const label = (key: string) => t(key as MessageKey);

/** The first digit of an account code for each kind of account (1 assets ... 7 exchange differences and finance costs). */
export const SUBTYPE_DIGIT: Record<LedgerSubtype, string> = {
  bank: '1', receivable: '1', tax_receivable: '1', prepayment: '1', current_asset: '1', fixed_asset: '1', accumulated_depreciation: '1',
  payable: '2', tax_payable: '2', accrual: '2', interest_payable: '2', owed_to_members: '2', loan: '2', current_liability: '2',
  capital: '3', drawings: '3', retained_earnings: '3', current_result: '3',
  revenue: '4', other_income: '4', cost_of_sales: '5', operating_expense: '6', depreciation: '6', finance_cost: '7', fx: '7',
};
/** Kinds of account an owner or an accountant adds by hand (the posting rules' own accounts already exist). */
export const ADDABLE_SUBTYPES: LedgerSubtype[] = [
  'bank', 'receivable', 'current_asset', 'fixed_asset', 'payable', 'current_liability', 'loan', 'capital', 'revenue', 'other_income',
  'cost_of_sales', 'operating_expense', 'finance_cost',
];
const CODE = /^\d{4,6}$/;
const AMOUNT = /^\d{1,12}(\.\d{1,3})?$/;
const CATEGORY = /^[a-z][a-z0-9_]{0,39}$/;

export const subtypeLabel = (subtype: string) => label(`ledger.subtype.${subtype}`);
export const sourceLabel = (kind: LedgerSourceKind | string) => label(`ledger.source.${kind}`);
export const flowLabel = (flow: string) => label(`ledger.flow.${flow}`);

/** A system account's name in the reader's language (renamed accounts keep the owner's name). */
export function accountName(account: { system_key: string | null; name: string }, standardNames: Record<string, string> = STANDARD_NAMES): string {
  if (account.system_key && standardNames[account.system_key] === account.name) return label(`ledger.account.${account.system_key}`);
  return account.name;
}

/** The English names the API seeds, so a renamed account keeps its own name and an untouched one is translated. */
export const STANDARD_NAMES: Record<string, string> = {
  bank: 'Bank and cash', ar: 'Accounts receivable', ic_receivable: 'Owed by related businesses', tax_receivable: 'VAT receivable (input tax)',
  prepayments: 'Prepayments', fixed_assets: 'Fixed assets at cost', accumulated_depreciation: 'Accumulated depreciation', ap: 'Accounts payable',
  ic_payable: 'Owed to related businesses', tax_payable: 'VAT payable (output tax)', accruals: 'Accruals', interest_payable: 'Interest payable',
  loans: 'Loans', owed_to_members: 'Owed to members', capital: 'Owner capital', drawings: 'Owner drawings', retained_earnings: 'Retained earnings',
  current_result: 'Current year result', revenue_service: 'Revenue: services', revenue_labor: 'Revenue: labour', revenue_external_cost: 'Revenue: passed-on costs',
  revenue_other: 'Revenue: other', other_income: 'Other income', credit_notes: 'Credit notes', cost_of_sales: 'Cost of sales', operating: 'Operating expenses',
  depreciation: 'Depreciation', fx: 'Exchange gains and losses', finance_costs: 'Finance costs and interest',
  disposal_gain: 'Gains on disposal of assets', impairment: 'Impairment losses', disposal_loss: 'Losses on disposal of assets',
  fx_unrealised: 'Unrealised exchange gains and losses',
};

/** Why an account form cannot be sent, or null. */
export function accountProblem(form: { code: string; name: string; subtype: LedgerSubtype | ''; category: string }): MessageKey | null {
  if (!form.subtype) return 'ledger.account.needSubtype';
  if (!CODE.test(form.code.trim())) return 'ledger.account.badCode';
  if (form.code.trim()[0] !== SUBTYPE_DIGIT[form.subtype]) return 'ledger.account.codeRange';
  if (form.name.trim().length < 2) return 'ledger.account.needName';
  if (form.category.trim() && !CATEGORY.test(form.category.trim())) return 'books.categoryInvalid';
  if (form.category.trim() && !['cost_of_sales', 'operating_expense'].includes(form.subtype)) return 'ledger.account.categoryKind';
  return null;
}

export interface ManualLineDraft { account_id: string; debit: string; credit: string }

/** Debits and credits of a manual entry as typed, in minor units (thousandths), so the sums are exact. */
export function manualTotals(lines: ManualLineDraft[]): { debit: number; credit: number } {
  const units = (value: string) => (AMOUNT.test(value.trim()) ? Math.round(Number(value.trim()) * 1000) : 0);
  return lines.reduce((sum, line) => ({ debit: sum.debit + units(line.debit), credit: sum.credit + units(line.credit) }), { debit: 0, credit: 0 });
}

/** Why a manual entry cannot be sent, or null: a date, words, two lines or more each with one side, and equal sides. */
export function manualProblem(form: { entry_date: string; description: string; lines: ManualLineDraft[] }): MessageKey | null {
  if (!form.entry_date) return 'ledger.manual.needDate';
  if (form.description.trim().length < 3) return 'ledger.manual.needWords';
  const used = form.lines.filter((line) => line.account_id || line.debit.trim() || line.credit.trim());
  if (used.length < 2) return 'ledger.manual.twoLines';
  for (const line of used) {
    if (!line.account_id) return 'ledger.manual.needAccount';
    const debit = line.debit.trim(), credit = line.credit.trim();
    const hasDebit = Boolean(debit) && Number(debit) > 0, hasCredit = Boolean(credit) && Number(credit) > 0;
    if ((debit && !AMOUNT.test(debit)) || (credit && !AMOUNT.test(credit))) return 'ledger.manual.badAmount';
    if (hasDebit === hasCredit) return 'ledger.manual.oneSide';
  }
  const totals = manualTotals(used);
  if (totals.debit !== totals.credit) return 'ledger.manual.unbalanced';
  return null;
}

export function manualBody(form: { entry_date: string; description: string; lines: ManualLineDraft[] }): LedgerManualInput {
  return { entry_date: form.entry_date, description: form.description.trim(),
    lines: form.lines.filter((line) => line.account_id).map((line) => ({ account_id: line.account_id, debit: line.debit.trim() || '0', credit: line.credit.trim() || '0' })) };
}

/** The last day of the month before ``today`` (ISO), the month a close usually covers. */
export function lastMonthEnd(today: string): string {
  const [year, month] = today.split('-').map(Number);
  const end = new Date(Date.UTC(year, month - 1, 0));
  return end.toISOString().slice(0, 10);
}

/** A trial balance row's closing balance as one signed figure: debit positive. */
export function closingNet(row: LedgerTrialRow): string {
  const value = Number(row.closing_debit) - Number(row.closing_credit);
  return value.toFixed(Math.max(decimals(row.closing_debit), decimals(row.closing_credit)));
}
const decimals = (value: string) => (value.includes('.') ? value.split('.')[1].length : 0);

export type StatementRow = { key: string; label: string; amount: string; kind: 'line' | 'total' | 'result' };

/** The rows of the journal's P&L in reading order: each section's accounts then its total, and the results. */
export function pnlRows(pnl: LedgerPnl): StatementRow[] {
  const out: StatementRow[] = [];
  const block = (key: 'revenue' | 'cost_of_sales' | 'operating_expenses' | 'other_income' | 'finance_costs') => {
    for (const row of pnl[key].rows) out.push({ key: `${key}:${row.account_id ?? row.code}`, label: `${row.code} ${accountName(row)}`, amount: row.amount, kind: 'line' });
    out.push({ key, label: label(`ledger.pnl.${key}`), amount: pnl[key].total, kind: 'total' });
  };
  block('revenue');
  block('cost_of_sales');
  out.push({ key: 'gross_profit', label: label('ledger.pnl.gross_profit'), amount: pnl.gross_profit, kind: 'result' });
  block('operating_expenses');
  out.push({ key: 'operating_profit', label: label('ledger.pnl.operating_profit'), amount: pnl.operating_profit, kind: 'result' });
  block('other_income');
  block('finance_costs');
  out.push({ key: 'fx_differences', label: label('ledger.pnl.fx_differences'), amount: pnl.fx_differences, kind: 'total' });
  out.push({ key: 'net_profit', label: label('ledger.pnl.net_profit'), amount: pnl.net_profit, kind: 'result' });
  return out;
}

/** The balance sheet in reading order. */
export function balanceRows(sheet: LedgerBalanceSheet): StatementRow[] {
  const out: StatementRow[] = [];
  const block = (rows: { account_id: string | null; code: string; name: string; system_key: string | null; amount: string }[], total: string, key: string) => {
    for (const row of rows) out.push({ key: `${key}:${row.account_id ?? row.system_key}`, label: row.system_key === 'unclosed_result' ? label('ledger.bs.unclosed')
      : `${row.code} ${accountName(row)}`, amount: row.amount, kind: 'line' });
    out.push({ key, label: label(`ledger.bs.${key}`), amount: total, kind: 'total' });
  };
  block(sheet.assets.current.rows, sheet.assets.current.total, 'current_assets');
  block(sheet.assets.non_current.rows, sheet.assets.non_current.total, 'non_current_assets');
  out.push({ key: 'assets', label: label('ledger.bs.assets'), amount: sheet.assets.total, kind: 'result' });
  block(sheet.liabilities.current.rows, sheet.liabilities.current.total, 'current_liabilities');
  block(sheet.liabilities.non_current.rows, sheet.liabilities.non_current.total, 'non_current_liabilities');
  out.push({ key: 'liabilities', label: label('ledger.bs.liabilities'), amount: sheet.liabilities.total, kind: 'result' });
  block(sheet.equity.rows, sheet.equity.total, 'equity');
  out.push({ key: 'liabilities_and_equity', label: label('ledger.bs.liabilities_and_equity'), amount: sheet.liabilities_and_equity, kind: 'result' });
  return out;
}

/** What a posting issue says, in words. */
export function issueText(issue: LedgerIssue): string {
  if (issue.reason === 'missing_rate') return t('ledger.issue.missingRate', { currency: issue.currency ?? '-', day: issue.rate_date ?? issue.event_date });
  if (issue.reason === 'missing_closing_rate') return t('ledger.issue.missingClosingRate', { currency: issue.currency ?? '-', day: issue.rate_date ?? issue.event_date });
  return t('ledger.issue.tooSmall');
}

/** Where a journal entry's source document opens in the books (a bill, the adjustments, Business invoices), or null. */
export function sourceHash(kind: LedgerSourceKind, sourceId: string | null): string | null {
  if (!sourceId) return null;
  if (kind === 'bill' || kind === 'bill_payment') return 'business-books/bills';
  if (kind === 'adjustment') return 'business-books/journal';
  if (kind === 'intercompany') return 'business-books/intercompany';
  if (kind === 'allocation') return 'business-books/allocations';
  if (kind === 'invoice') return `business-invoices/${encodeURIComponent(sourceId)}`;
  if (kind === 'invoice_payment' || kind === 'credit_note') return 'business-invoices';
  if (kind === 'asset') return 'business-books/assets';
  if (kind === 'loan' || kind === 'loan_repayment') return 'business-books/loans';
  if (kind === 'revaluation') return 'business-books/revaluation';
  return null;
}

export const LEDGER_TABS = ['accounts', 'ledger', 'statements', 'close'] as const;
export type LedgerTab = (typeof LEDGER_TABS)[number];

export function accountsByType(accounts: LedgerAccount[]): Record<string, LedgerAccount[]> {
  const out: Record<string, LedgerAccount[]> = {};
  for (const account of [...accounts].sort((a, b) => a.code.localeCompare(b.code))) (out[account.type] ??= []).push(account);
  return out;
}
