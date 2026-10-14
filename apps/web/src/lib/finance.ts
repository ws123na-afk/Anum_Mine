// Finance view logic (docs/finance.md), pure so `pnpm --filter @anum/web test:unit` covers it.
// Amounts arrive as decimal strings next to their currency. The view formats them for display only;
// it never adds amounts of different currencies and never does arithmetic that the API should do.
import type {
  FinanceAmountLimit, FinanceCategory, FinanceCategoryCap, FinanceCurrencyAmount, FinanceGuardianRules, FinanceSafeLine, FinanceSummary,
} from '@anum/contracts';
import { getLocale, intlLocale, t, type Locale, type MessageKey } from './i18n.ts';

export type FinanceTab = 'overview' | 'safe' | 'transactions' | 'accounts' | 'import' | 'bills' | 'debts' | 'goals' | 'holdings' | 'guardian' | 'connections' | 'trading' | 'execution';
export const FINANCE_TABS: readonly FinanceTab[] = ['overview', 'safe', 'transactions', 'accounts', 'import', 'bills', 'debts', 'goals', 'holdings', 'guardian', 'connections', 'trading', 'execution'];

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


// ---- Stage 3: safe-to-spend -----------------------------------------------------------------------

/** Why a line is in (or out of) the figure, in the person's language. */
export function safeNote(line: Pick<FinanceSafeLine, 'note' | 'params' | 'currency'>): string {
  const { note, params } = line;
  if (note === 'bill_due') return t('finance.safe.note.bill_due', { date: isolate(params.date ?? '') });
  if (note === 'minimum_due' || note === 'minimum_passed') return t(`finance.safe.note.${note}`, { day: isolate(params.day ?? '') });
  if (note === 'goal_derived') return t('finance.safe.note.goal_derived', { left: isolate(formatAmount(params.left ?? '0', line.currency)), count: Number(params.months ?? 1) });
  return t(`finance.safe.note.${note}` as MessageKey);
}

/** The group a line is shown under, in build order. */
export const SAFE_GROUPS = ['balance', 'bill', 'minimum_payment', 'goal', 'buffer'] as const;

// ---- Stage 4: the Guardian rules ------------------------------------------------------------------

export interface LimitDraft { currency: string; amount: string }
export interface CapDraft extends LimitDraft { category: FinanceCategory }

/** The editable form of the rules: every field a string, so typing never loses precision. */
export interface RulesDraft {
  allowedCurrencies: string;
  maxSingle: LimitDraft[];
  maxDaily: LimitDraft[];
  approvalAbove: LimitDraft[];
  caps: CapDraft[];
  blockedCategories: FinanceCategory[];
  blockedMerchants: string;
  quietOn: boolean;
  quietStart: string;
  quietEnd: string;
  coolingOn: boolean;
  coolingHours: string;
  coolingAbove: LimitDraft[];
}

export const EMPTY_RULES: FinanceGuardianRules = {
  allowed_currencies: [], max_single: [], max_daily_total: [], category_caps: [], blocked_categories: [], blocked_merchants: [], approval_above: [],
  quiet_hours: null, cooling_off: null,
};

const limits = (items: FinanceAmountLimit[]): LimitDraft[] => items.map((i) => ({ currency: i.currency, amount: i.amount }));

export function draftFromRules(rules: FinanceGuardianRules): RulesDraft {
  return {
    allowedCurrencies: rules.allowed_currencies.join(', '),
    maxSingle: limits(rules.max_single), maxDaily: limits(rules.max_daily_total), approvalAbove: limits(rules.approval_above),
    caps: rules.category_caps.map((c) => ({ category: c.category, currency: c.currency, amount: c.amount })),
    blockedCategories: [...rules.blocked_categories], blockedMerchants: rules.blocked_merchants.join('\n'),
    quietOn: rules.quiet_hours !== null, quietStart: rules.quiet_hours?.start ?? '22:00', quietEnd: rules.quiet_hours?.end ?? '07:00',
    coolingOn: rules.cooling_off !== null, coolingHours: String(rules.cooling_off?.hours ?? 24), coolingAbove: limits(rules.cooling_off?.at_or_above ?? []),
  };
}

/** The draft as rules for the API, or the first mistake in the person's language. */
export function rulesFromDraft(draft: RulesDraft): Checked<FinanceGuardianRules> {
  const currencies = draft.allowedCurrencies.split(/[\s,،]+/).map((c) => c.trim().toUpperCase()).filter(Boolean);
  if (currencies.some((c) => !/^[A-Z]{3}$/.test(c))) return { ok: false, error: t('finance.error.currency') };
  const amounts = (rows: LimitDraft[]): Checked<FinanceAmountLimit[]> => {
    const out: FinanceAmountLimit[] = [];
    for (const row of rows) {
      if (!row.amount.trim() && !row.currency.trim()) continue;
      const currency = row.currency.trim().toUpperCase();
      if (!/^[A-Z]{3}$/.test(currency)) return { ok: false, error: t('finance.error.currency') };
      if (out.some((o) => o.currency === currency)) return { ok: false, error: t('finance.guardian.error.twice', { currency }) };
      const amount = parseAmount(row.amount, currency, { signed: false, allowZero: false });
      if (!amount.ok) return amount;
      out.push({ currency, amount: amount.value });
    }
    return { ok: true, value: out };
  };
  const single = amounts(draft.maxSingle), daily = amounts(draft.maxDaily), approval = amounts(draft.approvalAbove), cooling = amounts(draft.coolingAbove);
  for (const checked of [single, daily, approval, cooling]) if (!checked.ok) return checked;
  const caps: FinanceCategoryCap[] = [];
  for (const row of draft.caps) {
    if (!row.amount.trim()) continue;
    const currency = row.currency.trim().toUpperCase();
    if (!/^[A-Z]{3}$/.test(currency)) return { ok: false, error: t('finance.error.currency') };
    if (caps.some((c) => c.category === row.category && c.currency === currency)) return { ok: false, error: t('finance.guardian.error.capTwice', { category: categoryLabel(row.category), currency }) };
    const amount = parseAmount(row.amount, currency, { signed: false, allowZero: false });
    if (!amount.ok) return amount;
    caps.push({ category: row.category, currency, amount: amount.value });
  }
  const merchants = draft.blockedMerchants.split(/\n|,/).map((m) => m.trim()).filter(Boolean);
  if (merchants.some((m) => m.length < 2)) return { ok: false, error: t('finance.guardian.error.merchant') };
  if (draft.quietOn && (!/^\d{2}:\d{2}$/.test(draft.quietStart) || !/^\d{2}:\d{2}$/.test(draft.quietEnd) || draft.quietStart === draft.quietEnd)) {
    return { ok: false, error: t('finance.guardian.error.quiet') };
  }
  const hours = Number(draft.coolingHours);
  if (draft.coolingOn && (!Number.isInteger(hours) || hours < 1 || hours > 168)) return { ok: false, error: t('finance.guardian.error.cooling') };
  return {
    ok: true,
    value: {
      allowed_currencies: [...new Set(currencies)], max_single: single.ok ? single.value : [], max_daily_total: daily.ok ? daily.value : [],
      approval_above: approval.ok ? approval.value : [], category_caps: caps, blocked_categories: [...new Set(draft.blockedCategories)],
      blocked_merchants: [...new Set(merchants)],
      quiet_hours: draft.quietOn ? { start: draft.quietStart, end: draft.quietEnd } : null,
      cooling_off: draft.coolingOn ? { hours, at_or_above: cooling.ok ? cooling.value : [] } : null,
    },
  };
}

/** Each rule as one plain sentence, in a fixed order (an empty list: no rules). */
export function describeRules(rules: FinanceGuardianRules): string[] {
  const money = (l: FinanceAmountLimit) => isolate(formatAmount(l.amount, l.currency));
  const lines: string[] = [];
  if (rules.allowed_currencies.length) lines.push(t('finance.guardian.rule.currencies', { list: isolate(rules.allowed_currencies.join(', ')) }));
  for (const l of rules.max_single) lines.push(t('finance.guardian.rule.single', { amount: money(l) }));
  for (const l of rules.max_daily_total) lines.push(t('finance.guardian.rule.daily', { amount: money(l) }));
  for (const c of rules.category_caps) lines.push(t('finance.guardian.rule.cap', { category: categoryLabel(c.category), amount: money(c) }));
  if (rules.blocked_categories.length) lines.push(t('finance.guardian.rule.blockedCategories', { list: rules.blocked_categories.map(categoryLabel).join(', ') }));
  if (rules.blocked_merchants.length) lines.push(t('finance.guardian.rule.blockedMerchants', { list: rules.blocked_merchants.join(', ') }));
  for (const l of rules.approval_above) lines.push(t('finance.guardian.rule.approval', { amount: money(l) }));
  if (rules.quiet_hours) lines.push(t('finance.guardian.rule.quiet', { start: isolate(rules.quiet_hours.start), end: isolate(rules.quiet_hours.end) }));
  if (rules.cooling_off) {
    const at = rules.cooling_off.at_or_above.map(money).join(', ');
    lines.push(at ? t('finance.guardian.rule.cooling', { count: rules.cooling_off.hours, amount: at }) : t('finance.guardian.rule.coolingAll', { count: rules.cooling_off.hours }));
  }
  return lines;
}

/** A rule kind's name ("max_single" to "Single payment limit"). */
export function ruleKindLabel(kind: string): string {
  return t(`finance.guardian.kind.${kind}` as MessageKey);
}

/** The first characters of a payload hash, grouped, so the person can compare what they confirm. */
export function shortHash(hash: string): string {
  return hash.slice(0, 12).replace(/(.{4})(?=.)/g, '$1 ');
}

/** "In 23 hours", "In 5 minutes": when a proposal lapses. */
export function expiresLabel(expiresAt: string, now: Date = new Date()): string {
  const minutes = Math.max(0, Math.round((new Date(expiresAt).getTime() - now.getTime()) / 60_000));
  if (minutes < 60) return t('finance.guardian.expiresMinutes', { count: minutes });
  return t('finance.guardian.expiresHours', { count: Math.round(minutes / 60) });
}
