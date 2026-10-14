// Finance view logic (docs/finance.md), pure so `pnpm --filter @anum/web test:unit` covers it.
// Amounts arrive as decimal strings next to their currency. The view formats them for display only;
// it never adds amounts of different currencies and never does arithmetic that the API should do.
import type { FinanceCategory, FinanceCurrencyAmount, FinanceSummary } from '@anum/contracts';
import { getLocale, intlLocale, t, type Locale, type MessageKey } from './i18n.ts';

export type FinanceTab = 'overview' | 'transactions' | 'accounts' | 'import' | 'bills' | 'debts' | 'goals' | 'holdings';
export const FINANCE_TABS: readonly FinanceTab[] = ['overview', 'transactions', 'accounts', 'import', 'bills', 'debts', 'goals', 'holdings'];

const THREE = new Set(['BHD', 'IQD', 'JOD', 'KWD', 'LYD', 'OMR', 'TND']);
const ZERO = new Set(['JPY', 'KRW', 'VND', 'CLP', 'ISK', 'UGX', 'XAF', 'XOF']);

/** Digits after the decimal point for a currency (the API rounds to the same, docs/finance.md#money-rules). */
export function minorDigits(currency: string): number {
  return THREE.has(currency) ? 3 : ZERO.has(currency) ? 0 : 2;
}

/** A wire amount ("1234.50") shown in the person's locale with its currency code, for display only. */
export function formatAmount(value: string | null | undefined, currency: string, locale: Locale = getLocale()): string {
  if (value === null || value === undefined || value === '' || !/^-?\d+(\.\d+)?$/.test(value)) return '-';
  const digits = minorDigits(currency);
  const code = /^[A-Z]{3}$/.test(currency) ? currency : null;
  const number = Number(value);
  const options: Intl.NumberFormatOptions = { minimumFractionDigits: digits, maximumFractionDigits: digits };
  return code
    ? new Intl.NumberFormat(intlLocale(locale), { ...options, style: 'currency', currency: code, currencyDisplay: 'code' }).format(number)
    : new Intl.NumberFormat(intlLocale(locale), options).format(number);
}

/** Per-currency figures joined, never summed: "AED 1,200.00 · USD 30.00", or "-" when none. */
export function formatAmounts(amounts: readonly FinanceCurrencyAmount[] | null | undefined, locale: Locale = getLocale()): string {
  if (!amounts || amounts.length === 0) return '-';
  return amounts.map((row) => formatAmount(row.amount, row.currency, locale)).join(' · ');
}

/** The figure for one currency (the base currency first), else the first one, else zero. */
export function pickAmount(amounts: readonly FinanceCurrencyAmount[], currency: string): FinanceCurrencyAmount {
  return amounts.find((row) => row.currency === currency) ?? amounts[0] ?? { currency, amount: '0' };
}

export type Checked<T> = { ok: true; value: T } | { ok: false; error: string };

/** A typed amount as the wire string: "1,234.5" and "(12)" are read; letters and extra decimals are refused. */
export function parseAmount(input: string, currency: string, { signed = true, allowZero = true } = {}): Checked<string> {
  let text = input.trim().replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660)).replace(/٫/g, '.').replace(/[,٬\s]/g, '');
  let negative = false;
  if (/^\(.*\)$/.test(text)) { negative = true; text = text.slice(1, -1); }
  if (text.startsWith('-') || text.startsWith('−')) { negative = !negative; text = text.slice(1); }
  else if (text.startsWith('+')) text = text.slice(1);
  if (!/^\d{1,12}(\.\d+)?$/.test(text)) return { ok: false, error: t('finance.error.amount') };
  const decimals = text.includes('.') ? text.split('.')[1].length : 0;
  if (decimals > minorDigits(currency)) return { ok: false, error: t('finance.error.decimals', { currency, digits: minorDigits(currency) }) };
  if (negative && !signed) return { ok: false, error: t('finance.error.positive') };
  if (!allowZero && Number(text) === 0) return { ok: false, error: t('finance.error.aboveZero') };
  return { ok: true, value: `${negative ? '-' : ''}${text}` };
}

export function isLast4(value: string): boolean {
  return value === '' || /^\d{4}$/.test(value);
}

export function categoryLabel(category: FinanceCategory): string {
  return t(`finance.category.${category}` as MessageKey);
}

/** "October 2026" for "2026-10" in the person's language. */
export function monthLabel(month: string, locale: Locale = getLocale()): string {
  const [year, number] = month.split('-').map(Number);
  if (!year || !number) return month;
  return new Intl.DateTimeFormat(intlLocale(locale), { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(Date.UTC(year, number - 1, 1)));
}

/** The month before or after "YYYY-MM". */
export function shiftMonth(month: string, by: number): string {
  const [year, number] = month.split('-').map(Number);
  const index = year * 12 + (number - 1) + by;
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}`;
}

export interface SpendBar { category: FinanceCategory; amount: string; value: number; count: number; share: number }

/** The month's spend by category in one currency, largest first, with each bar's share of the largest. */
export function spendBars(summary: Pick<FinanceSummary, 'spend_by_category'>, currency: string): SpendBar[] {
  const rows = summary.spend_by_category.filter((row) => row.currency === currency).map((row) => ({ category: row.category, amount: row.amount, value: Number(row.amount), count: row.count }));
  rows.sort((a, b) => b.value - a.value || a.category.localeCompare(b.category));
  const max = rows[0]?.value ?? 0;
  return rows.map((row) => ({ ...row, share: max > 0 ? row.value / max : 0 }));
}

/** Currencies that have spending this month, the base currency first. */
export function spendCurrencies(summary: Pick<FinanceSummary, 'spend_by_category'>, base: string): string[] {
  const found = [...new Set(summary.spend_by_category.map((row) => row.currency))];
  return found.sort((a, b) => (a === base ? -1 : b === base ? 1 : a.localeCompare(b)));
}

/** The tab a URL hash opens: `#finance-transactions` and friends; anything else is the overview. */
export function tabFromHash(hash: string): FinanceTab {
  const id = hash.replace(/^#finance-?/, '');
  return (FINANCE_TABS as readonly string[]).includes(id) ? id as FinanceTab : 'overview';
}

export function hashForTab(tab: FinanceTab): string {
  return tab === 'overview' ? '#finance' : `#finance-${tab}`;
}

/** Read a picked file as text, refusing anything over the API's size limit before it is sent. */
export function checkFile(file: { name: string; size: number }, maxBytes: number): Checked<true> {
  if (file.size === 0) return { ok: false, error: t('finance.import.error.empty') };
  if (file.size > maxBytes) return { ok: false, error: t('finance.import.error.size', { mb: Math.floor(maxBytes / 1_000_000) }) };
  if (!/\.(csv|txt)$/i.test(file.name)) return { ok: false, error: t('finance.import.error.type') };
  return { ok: true, value: true };
}

/** "Due in 3 days", "Due today", "Due tomorrow". */
export function dueLabel(days: number): string {
  if (days <= 0) return t('finance.bills.dueToday');
  if (days === 1) return t('finance.bills.dueTomorrow');
  return t('finance.bills.dueIn', { count: days });
}

/** "1 to 50 of 230". */
export function rangeLabel(offset: number, shown: number, total: number): string {
  if (total === 0) return t('finance.page.none');
  return t('finance.page.range', { from: offset + 1, to: offset + shown, total });
}

/** Tone for a signed amount: money in is positive, money out negative. */
export function amountTone(value: string): 'in' | 'out' | '' {
  return value.startsWith('-') ? 'out' : Number(value) > 0 ? 'in' : '';
}

/** A date, amount or code kept left to right inside a sentence, also in Arabic (Unicode isolates). */
export function isolate(value: string): string {
  return `\u2066${value}\u2069`;
}
