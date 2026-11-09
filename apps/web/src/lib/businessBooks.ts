// Business books (docs/business-books.md#web): sections, labels, amount checks, request bodies, P&L rows and period
// presets. Pure, so `pnpm --filter @anum/web test:unit` covers it without a browser. The API is the authority: it
// rounds every amount once, never adds currencies and converts only with a dated rate whose source it lists.
import type {
  BooksAdjustmentKind, BooksBasis, BooksBillFilter, BooksBillInput, BooksBudgetInput, BooksFrequency, BooksGroup, BooksGroupRow, BooksIntercompanyInput,
  BooksPaymentState, BooksReport, BooksSection, BooksSettings, BooksSettingsInput, BooksTagSection, BooksTemplate, BooksTemplateInput, TaxCode,
} from '@anum/contracts';
import { formatAmount } from './finance.ts';
import { t, type MessageKey } from './i18n.ts';
import type { Tone } from './business.ts';

export type BooksTab = 'overview' | 'review' | 'group' | 'bills' | 'recurring' | 'vendors' | 'allocations' | 'budgets' | 'journal' | 'ledger' | 'statements' | 'accounts'
  | 'close' | 'tax' | 'assets' | 'loans' | 'revaluation' | 'intercompany' | 'settings';
/** ``journal`` is the adjustments; ``ledger``, ``statements``, ``accounts`` and ``close`` are the double-entry journal (docs/business-books.md#the-journal). */
export const BOOKS_TABS: readonly BooksTab[] = ['overview', 'review', 'group', 'bills', 'recurring', 'vendors', 'allocations', 'budgets', 'journal', 'ledger', 'statements', 'accounts',
  'close', 'tax', 'assets', 'loans', 'revaluation', 'intercompany', 'settings'];
/** Sections only owners see in the navigation (the API refuses everyone else anyway). */
export const OWNER_TABS: readonly BooksTab[] = ['group'];
/** Sections for owners and accountants only (``permissions.account``): the Accountant review (docs/business-books.md#accountant-review). */
export const ACCOUNTANT_TABS: readonly BooksTab[] = ['review'];

/** Whether a section shows in the Books navigation for these permissions. */
export function tabShown(tab: BooksTab, permissions: { manage: boolean; account?: boolean }): boolean {
  if (OWNER_TABS.includes(tab)) return permissions.manage;
  if (ACCOUNTANT_TABS.includes(tab)) return permissions.manage || Boolean(permissions.account);
  return true;
}
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
  if (state === 'void' || state === 'draft') return 'neutral';
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
  /** docs/business-tax.md#tax-codes: '' keeps the bill "unknown" for the VAT return. */
  tax_code?: TaxCode | '';
}

export function emptyBill(today: string, currency: string): BillDraft {
  return { vendor_id: '', reference: '', description: '', bill_date: today, due_date: '', currency, subtotal: '', tax: '0', category: '', section: '',
    cost_centre_id: '', project_id: '', recurrence: '', useful_life_months: '', tax_code: '' };
}

/** Tax codes a supplier charges no tax under (the reverse charge is self-assessed by the business, not charged). */
export const NO_TAX_BILL_CODES: readonly TaxCode[] = ['zero_rated', 'export', 'exempt', 'out_of_scope', 'reverse_charge'];

/** The first problem with a bill form, as a message key, or null. */
export function billProblem(draft: BillDraft): MessageKey | null {
  if (!draft.vendor_id) return 'books.bill.needVendor';
  if (draft.description.trim().length < 2) return 'books.bill.needDescription';
  if (!draft.bill_date) return 'books.bill.needDate';
  if (draft.due_date && draft.due_date < draft.bill_date) return 'books.bill.dueBeforeDate';
  if (!validAmount(draft.subtotal, true) || !validAmount(draft.tax || '0', true)) return 'books.amountInvalid';
  if (Number(draft.subtotal) + Number(draft.tax || '0') <= 0) return 'books.amountInvalid';
  if (draft.tax_code && NO_TAX_BILL_CODES.includes(draft.tax_code) && Number(draft.tax || '0') !== 0) return 'tax.problem.billNoTax';
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
    tax_code: draft.tax_code || null,
  };
}

export function settingsBody(form: { base_currency: string; country: string; fiscal_year_start_month: number; basis: BooksBasis; tax_label: string;
  rate_max_age_days: string; mappings: { category: string; section: BooksTagSection }[]; policy_note?: string }, current: BooksSettings | null): BooksSettingsInput {
  const category_sections: Record<string, BooksTagSection> = {};
  for (const row of form.mappings) {
    const key = normalizeCategory(row.category);
    if (key) category_sections[key] = row.section;
  }
  return { base_currency: form.base_currency, country: form.country || null, fiscal_year_start_month: form.fiscal_year_start_month, basis: form.basis,
    tax_label: form.tax_label.trim(), category_sections, rate_max_age_days: Number(form.rate_max_age_days) || 31,
    ...(form.policy_note !== undefined ? { policy_note: form.policy_note.trim() } : {}), version: current ? current.version : null };
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

// ---- recurring bills --------------------------------------------------------------------------------------------------

/** "Monthly", "Every 2 weeks", "Every 10 days"... */
export function frequencyLabel(frequency: BooksFrequency, interval: number): string {
  if (frequency === 'monthly' || frequency === 'quarterly' || frequency === 'yearly') return label(`books.freq.${frequency}`);
  return t(`books.freq.every.${frequency}` as MessageKey, { count: interval });
}

export const templateStatusTone = (status: BooksTemplate['status']): Tone => (status === 'active' ? 'success' : status === 'paused' ? 'warning' : 'neutral');

export interface TemplateDraft {
  vendor_id: string; reference: string; description: string; currency: string; subtotal: string; tax: string; category: string; section: BooksSection | '';
  cost_centre_id: string; project_id: string; useful_life_months: string; frequency: BooksFrequency; interval: string; start_date: string; due_days: string;
  ends: 'never' | 'on' | 'after'; end_date: string; max_count: string; create_as: 'open' | 'draft';
}

export function emptyTemplate(today: string, currency: string): TemplateDraft {
  return { vendor_id: '', reference: '', description: '', currency, subtotal: '', tax: '0', category: '', section: '', cost_centre_id: '', project_id: '',
    useful_life_months: '', frequency: 'monthly', interval: '1', start_date: today, due_days: '30', ends: 'never', end_date: '', max_count: '', create_as: 'open' };
}

export function templateDraftFrom(item: BooksTemplate): TemplateDraft {
  return { vendor_id: item.vendor_id, reference: item.reference, description: item.description, currency: item.currency, subtotal: item.subtotal, tax: item.tax,
    category: item.category, section: item.section, cost_centre_id: item.cost_centre_id ?? '', project_id: item.project_id ?? '',
    useful_life_months: item.useful_life_months ? String(item.useful_life_months) : '', frequency: item.frequency, interval: String(item.interval),
    start_date: item.start_date, due_days: String(item.due_days), ends: item.end_date ? 'on' : item.max_count ? 'after' : 'never', end_date: item.end_date ?? '',
    max_count: item.max_count ? String(item.max_count) : '', create_as: item.create_as };
}

export const frequencyHasInterval = (frequency: BooksFrequency) => frequency === 'days' || frequency === 'weeks' || frequency === 'months';
const INTERVAL_MAX: Record<string, number> = { days: 366, weeks: 52, months: 24 };

/** The first problem with a recurring bill form, as a message key, or null. */
export function templateProblem(draft: TemplateDraft): MessageKey | null {
  if (!draft.vendor_id) return 'books.bill.needVendor';
  if (draft.description.trim().length < 2) return 'books.bill.needDescription';
  if (!draft.start_date) return 'books.rec.needStart';
  if (!validAmount(draft.subtotal, true) || !validAmount(draft.tax || '0', true)) return 'books.amountInvalid';
  if (Number(draft.subtotal) + Number(draft.tax || '0') <= 0) return 'books.amountInvalid';
  if (draft.category && !validCategory(normalizeCategory(draft.category))) return 'books.categoryInvalid';
  if (frequencyHasInterval(draft.frequency)) {
    const every = Number(draft.interval);
    if (!/^\d{1,3}$/.test(draft.interval) || every < 1 || every > INTERVAL_MAX[draft.frequency]) return 'books.rec.intervalInvalid';
  }
  if (!/^\d{1,3}$/.test(draft.due_days || '0') || Number(draft.due_days || '0') > 366) return 'books.rec.dueInvalid';
  if (draft.ends === 'on' && (!draft.end_date || draft.end_date < draft.start_date)) return 'books.rec.endInvalid';
  if (draft.ends === 'after' && (!/^\d{1,4}$/.test(draft.max_count) || Number(draft.max_count) < 1 || Number(draft.max_count) > 1000)) return 'books.rec.countInvalid';
  if (draft.useful_life_months && (draft.section !== 'capital' || !/^\d{1,3}$/.test(draft.useful_life_months) || Number(draft.useful_life_months) < 1)) {
    return 'books.bill.lifeNeedsCapital';
  }
  return null;
}

export function templateBody(draft: TemplateDraft): BooksTemplateInput {
  return {
    vendor_id: draft.vendor_id, reference: draft.reference.trim(), description: draft.description.trim(), currency: draft.currency, subtotal: draft.subtotal.trim(),
    tax: (draft.tax || '0').trim(), category: draft.category ? normalizeCategory(draft.category) : null, section: draft.section || null,
    cost_centre_id: draft.cost_centre_id || null, project_id: draft.project_id || null,
    useful_life_months: draft.section === 'capital' && draft.useful_life_months ? Number(draft.useful_life_months) : null,
    frequency: draft.frequency, interval: frequencyHasInterval(draft.frequency) ? Number(draft.interval) : 1, start_date: draft.start_date,
    due_days: Number(draft.due_days || '0'), end_date: draft.ends === 'on' ? draft.end_date : null, max_count: draft.ends === 'after' ? Number(draft.max_count) : null,
    create_as: draft.create_as,
  };
}

// ---- consolidated books -----------------------------------------------------------------------------------------------

const RESULT_ROWS = new Set(['gross_profit', 'operating_profit', 'net_profit', 'net']);
export const groupRowLabel = (section: 'pnl' | 'balances' | 'cash_flow', key: string) => label(`books.group.${section}.${key}`);
export const groupRowIsResult = (key: string) => RESULT_ROWS.has(key);

/** Rows worth showing: results always; other rows only when some column holds money. */
export function groupRowsShown(rows: BooksGroupRow[] | undefined): BooksGroupRow[] {
  return (rows ?? []).filter((row) => groupRowIsResult(row.key) || Number(row.total) !== 0 || Number(row.eliminations) !== 0
    || Object.values(row.businesses).some((amount) => Number(amount) !== 0));
}

/** The names of the businesses by workspace id, for the column heads and the notes. */
export const groupNames = (group: BooksGroup) => new Map(group.businesses.map((item) => [item.workspace_id, item.name || item.workspace_id]));
