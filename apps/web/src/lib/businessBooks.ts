// Business books (docs/business-books.md#web): sections, labels, amount checks, request bodies, P&L rows and period
// presets. Pure, so `pnpm --filter @anum/web test:unit` covers it without a browser. The API is the authority: it
// rounds every amount once, never adds currencies and converts only with a dated rate whose source it lists.
import type {
  BooksAdjustmentKind, BooksBasis, BooksBillFilter, BooksBillInput, BooksBudgetInput, BooksIntercompanyInput, BooksPaymentState, BooksReport,
  BooksSection, BooksSettings, BooksSettingsInput, BooksTagSection,
} from '@anum/contracts';
import { formatAmount } from './finance.ts';
import { t, type MessageKey } from './i18n.ts';
import type { Tone } from './business.ts';

export type BooksTab = 'overview' | 'bills' | 'vendors' | 'allocations' | 'budgets' | 'journal' | 'intercompany' | 'settings';
export const BOOKS_TABS: readonly BooksTab[] = ['overview', 'bills', 'vendors', 'allocations', 'budgets', 'journal', 'intercompany', 'settings'];
export const BOOKS_PAGE = 25;
export const BOOK_CURRENCIES = ['SAR', 'AED', 'USD', 'EUR', 'GBP', 'KWD', 'QAR', 'BHD', 'OMR', 'EGP', 'INR', 'PKR'] as const;
export const COUNTRIES = ['SA', 'AE', 'KW', 'QA', 'BH', 'OM', 'EG', 'GB', 'US'] as const;
const MONTH_KEYS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'] as const;

const label = (key: string) => t(key as MessageKey);

/** The Books section a `#business-books/<section>` hash opens; the overview when unknown. */
export function booksTabFromId(id: string | null | undefined): BooksTab {
  return BOOKS_TABS.includes(id as BooksTab) ? (id as BooksTab) : 'overview';
}

export const booksTabLabel = (tab: BooksTab) => label(`books.tab.${tab}`);
export const sectionLabel = (section: BooksTagSection) => label(`books.section.${section}`);
export const basisLabel = (basis: BooksBasis) => label(`books.basis.${basis}`);
export const monthLabel = (month: number) => label(`books.month.${MONTH_KEYS[(month - 1 + 12) % 12]}`);
export const adjustmentLabel = (kind: BooksAdjustmentKind) => label(`books.adj.${kind}`);
export const billFilterLabel = (filter: BooksBillFilter) => label(`books.billFilter.${filter}`);
export const paymentStateLabel = (state: BooksPaymentState) => label(`books.payState.${state}`);

export function paymentStateTone(state: BooksPaymentState, overdue: boolean): Tone {
  if (state === 'void') return 'neutral';
  if (state === 'paid') return 'success';
  return overdue ? 'dangerTone' : 'warning';
}

const KNOWN_LINES = new Set(['service', 'labor', 'external_cost', 'other', 'credit_notes', 'depreciation', 'loan_interest', 'intercompany', 'invoices', 'bills']);

/** A P&L line: a revenue kind or a fixed line in the catalog, else the category itself in words ("office_rent" -> "Office rent"). */
export function lineLabel(key: string): string {
  if (KNOWN_LINES.has(key)) return label(`books.line.${key}`);
  const words = key.replace(/_/g, ' ').trim();
  return words ? words[0].toUpperCase() + words.slice(1) : '-';
}

const AMOUNT = /^\d{1,12}(\.\d{1,3})?$/;
const CATEGORY = /^[a-z][a-z0-9_]{0,39}$/;

/** A positive amount as typed (digits and at most three decimals); the server rounds it to the currency. */
export function validAmount(value: string, allowZero = false): boolean {
  const text = value.trim();
  if (!AMOUNT.test(text)) return false;
  return allowZero || Number(text) > 0;
}

/** A category as the ledger keeps it: lowercase words joined by underscores. */
export function normalizeCategory(value: string): string {
  return value.trim().toLowerCase().replace(/[\s-]+/g, '_').replace(/[^a-z0-9_]/g, '');
}
export const validCategory = (value: string) => CATEGORY.test(value);

export const money = (amount: string | null | undefined, currency: string) => formatAmount(amount ?? null, currency);
export const moneyList = (items: { currency: string; amount: string }[] | undefined) =>
  (items && items.length ? items.map((item) => formatAmount(item.amount, item.currency)).join(' · ') : '-');
export const pct = (value: string | null | undefined) => (value === null || value === undefined ? '-' : `${value}%`);
export const isNegative = (amount: string | null | undefined) => Boolean(amount && amount.startsWith('-'));

export interface BillDraft {
  vendor_id: string; reference: string; description: string; bill_date: string; due_date: string; currency: string; subtotal: string; tax: string;
  category: string; section: BooksSection | ''; cost_centre_id: string; project_id: string; recurrence: '' | 'monthly' | 'quarterly' | 'yearly'; useful_life_months: string;
}

export function emptyBill(today: string, currency: string): BillDraft {
  return { vendor_id: '', reference: '', description: '', bill_date: today, due_date: '', currency, subtotal: '', tax: '0', category: '', section: '',
    cost_centre_id: '', project_id: '', recurrence: '', useful_life_months: '' };
}

/** The first problem with a bill form, as a message key, or null. */
export function billProblem(draft: BillDraft): MessageKey | null {
  if (!draft.vendor_id) return 'books.bill.needVendor';
  if (draft.description.trim().length < 2) return 'books.bill.needDescription';
  if (!draft.bill_date) return 'books.bill.needDate';
  if (draft.due_date && draft.due_date < draft.bill_date) return 'books.bill.dueBeforeDate';
  if (!validAmount(draft.subtotal, true) || !validAmount(draft.tax || '0', true)) return 'books.amountInvalid';
  if (Number(draft.subtotal) + Number(draft.tax || '0') <= 0) return 'books.amountInvalid';
  if (draft.category && !validCategory(normalizeCategory(draft.category))) return 'books.categoryInvalid';
  if (draft.useful_life_months && (draft.section !== 'capital' || !/^\d{1,3}$/.test(draft.useful_life_months) || Number(draft.useful_life_months) < 1)) {
    return 'books.bill.lifeNeedsCapital';
  }
  return null;
}

export function billBody(draft: BillDraft): BooksBillInput {
  return {
    vendor_id: draft.vendor_id, reference: draft.reference.trim(), description: draft.description.trim(), bill_date: draft.bill_date,
    due_date: draft.due_date || null, currency: draft.currency, subtotal: draft.subtotal.trim(), tax: (draft.tax || '0').trim(),
    category: draft.category ? normalizeCategory(draft.category) : null, section: draft.section || null, cost_centre_id: draft.cost_centre_id || null,
    project_id: draft.project_id || null, recurrence: draft.recurrence || null,
    useful_life_months: draft.section === 'capital' && draft.useful_life_months ? Number(draft.useful_life_months) : null,
  };
}

export function settingsBody(form: { base_currency: string; country: string; fiscal_year_start_month: number; basis: BooksBasis; tax_label: string;
  rate_max_age_days: string; mappings: { category: string; section: BooksTagSection }[] }, current: BooksSettings | null): BooksSettingsInput {
  const category_sections: Record<string, BooksTagSection> = {};
  for (const row of form.mappings) {
    const key = normalizeCategory(row.category);
    if (key) category_sections[key] = row.section;
  }
  return { base_currency: form.base_currency, country: form.country || null, fiscal_year_start_month: form.fiscal_year_start_month, basis: form.basis,
    tax_label: form.tax_label.trim(), category_sections, rate_max_age_days: Number(form.rate_max_age_days) || 31, version: current ? current.version : null };
}

export function budgetProblem(form: BooksBudgetInput): MessageKey | null {
  if (form.name.trim().length < 2) return 'books.budget.needName';
  if (!form.period_start) return 'books.budget.needStart';
  if (!validAmount(form.amount)) return 'books.amountInvalid';
  if (form.category && !validCategory(form.category)) return 'books.categoryInvalid';
  return null;
}

export const adjustmentSpreads = (kind: BooksAdjustmentKind) => kind === 'prepayment' || kind === 'capital_item';
export const adjustmentHasSection = (kind: BooksAdjustmentKind) => kind === 'accrual' || kind === 'prepayment';

export function intercompanyProblem(form: BooksIntercompanyInput): MessageKey | null {
  if (!form.workspace_id) return 'books.ic.needParty';
  if (!validAmount(form.amount)) return 'books.amountInvalid';
  if (form.description.trim().length < 3) return 'books.needDescription3';
  return null;
}

export type StatementRow = { key: string; label: string; amount: string; kind: 'line' | 'total' | 'result'; negative: boolean };

/** The P&L as table rows, in the base currency: each group's lines, its total, then the results between groups. */
export function statementRows(report: BooksReport): StatementRow[] {
  const pnl = report.pnl;
  const rows: StatementRow[] = [];
  const group = (key: 'revenue' | 'cost_of_sales' | 'operating_expenses' | 'other_income' | 'finance_costs') => {
    for (const line of pnl[key].lines) rows.push({ key: `${key}.${line.key}`, label: lineLabel(line.key), amount: line.amount, kind: 'line', negative: isNegative(line.amount) });
    rows.push({ key: `${key}.total`, label: label(`books.pnl.${key}`), amount: pnl[key].total, kind: 'total', negative: isNegative(pnl[key].total) });
  };
  const result = (key: 'gross_profit' | 'operating_profit' | 'fx_differences' | 'net_profit') =>
    rows.push({ key, label: label(`books.pnl.${key}`), amount: pnl[key], kind: key === 'fx_differences' ? 'total' : 'result', negative: isNegative(pnl[key]) });
  group('revenue');
  group('cost_of_sales');
  result('gross_profit');
  group('operating_expenses');
  result('operating_profit');
  if (pnl.other_income.lines.length) group('other_income');
  if (pnl.finance_costs.lines.length) group('finance_costs');
  if (Number(pnl.fx_differences) !== 0) result('fx_differences');
  result('net_profit');
  return rows;
}

/** True when the period holds no money at all (the dashboard shows its empty state with next actions). */
export function reportIsEmpty(report: BooksReport): boolean {
  const zero = (value: string) => Number(value) === 0;
  const pnl = report.pnl;
  return zero(pnl.revenue.total) && zero(pnl.cost_of_sales.total) && zero(pnl.operating_expenses.total) && zero(pnl.other_income.total)
    && zero(pnl.finance_costs.total) && pnl.not_converted.length === 0 && Object.keys(report.cash_flow.lines).length === 0
    && Object.keys(report.cash_flow.paid_by_members).length === 0 && report.balances.receivables.length === 0 && report.balances.payables.length === 0
    // Balances carried from earlier periods (loans, money owed to members, related businesses) and owner money in or out.
    && report.balances.debt.length === 0 && report.balances.owed_to_members.length === 0 && report.balances.intercompany.length === 0
    && report.equity.contributions.length === 0 && report.equity.drawings.length === 0;
}

const iso = (value: Date) => `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;

export type PeriodPreset = 'this_year' | 'last_year' | 'this_quarter' | 'this_month' | 'last_month';
export const PERIOD_PRESETS: readonly PeriodPreset[] = ['this_year', 'this_month', 'last_month', 'this_quarter', 'last_year'];

/** A preset period, the financial year following the business's first month (local dates, no clock inside). */
export function presetPeriod(preset: PeriodPreset, today: Date, fyStartMonth: number): { from: string; to: string } {
  const year = today.getFullYear(), month = today.getMonth();
  if (preset === 'this_month') return { from: iso(new Date(year, month, 1)), to: iso(new Date(year, month + 1, 0)) };
  if (preset === 'last_month') return { from: iso(new Date(year, month - 1, 1)), to: iso(new Date(year, month, 0)) };
  if (preset === 'this_quarter') {
    const offset = ((month - (fyStartMonth - 1)) % 3 + 3) % 3;
    return { from: iso(new Date(year, month - offset, 1)), to: iso(new Date(year, month - offset + 3, 0)) };
  }
  const startYear = month >= fyStartMonth - 1 ? year : year - 1;
  const first = preset === 'last_year' ? startYear - 1 : startYear;
  return { from: iso(new Date(first, fyStartMonth - 1, 1)), to: iso(new Date(first + 1, fyStartMonth - 1, 0)) };
}

export const todayIso = () => iso(new Date());
export const newKey = (prefix: string) => `${prefix}-${crypto.randomUUID()}`;

/** A rate typed by a person: above zero, at most ten decimals. */
export const validRate = (value: string) => /^\d{1,9}(\.\d{1,10})?$/.test(value.trim()) && Number(value) > 0;
