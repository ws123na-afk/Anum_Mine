// Pure helpers for Finance, Accounts and Transactions (docs/finance.md#register-and-account-overview).
// No network and no DOM, so `pnpm --filter @anum/web test:unit` covers them.
import type {
  FinanceAccount, FinanceAccountActivity, FinanceAccountOverview, FinanceAccountSource, FinanceCategory, FinanceOrigin, FinanceRowStatus,
} from '@anum/contracts';

export interface RegisterFilters {
  q: string;
  account_id: string;
  category: FinanceCategory | '';
  from: string;
  to: string;
  origin: FinanceOrigin | '';
  evidence: 'linked' | 'missing' | '';
  status: FinanceRowStatus | '';
}

export const NO_REGISTER_FILTERS: RegisterFilters = { q: '', account_id: '', category: '', from: '', to: '', origin: '', evidence: '', status: '' };

export const ORIGINS: readonly FinanceOrigin[] = ['sms', 'apple_pay', 'statement', 'manual'];
export const ROW_STATUSES: readonly FinanceRowStatus[] = ['linked', 'needs_review', 'duplicate_candidate', 'uncategorized', 'recorded'];

/** The query the API takes: empty values are left out. */
export function registerQuery(filters: RegisterFilters): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(filters)) if (value) out[key] = value;
  return out;
}

/** True when anything but the period narrows the list (the period always applies). */
export function narrowed(filters: RegisterFilters): boolean {
  return Boolean(filters.q || filters.account_id || filters.category || filters.origin || filters.evidence || filters.status);
}

/** First and last day of a "YYYY-MM" month. */
export function monthBounds(month: string): { from: string; to: string } {
  const [y, m] = month.split('-').map(Number);
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return { from: `${month}-01`, to: `${month}-${String(last).padStart(2, '0')}` };
}

/** Only ever the last four digits: hidden shows the dots alone. */
export function maskedNumber(last4: string | null, shown: boolean): string {
  if (!last4) return '-';
  return shown ? `•••• ${last4}` : '••••';
}

export type ChipTone = 'ok' | 'warn' | 'bad' | 'info' | 'muted';

export function statusTone(status: FinanceRowStatus): ChipTone {
  switch (status) {
    case 'linked': return 'ok';
    case 'needs_review': return 'warn';
    case 'duplicate_candidate': return 'bad';
    case 'uncategorized': return 'info';
    default: return 'muted';
  }
}

/** Money you can spend now, the one definition Safe to spend uses: a bank or cash account that is not archived. */
export function isAvailable(account: Pick<FinanceAccount, 'kind' | 'archived'>): boolean {
  return (account.kind === 'bank' || account.kind === 'cash') && !account.archived;
}

export type AccountsTab = 'cash' | 'cards' | 'wallets' | 'other' | 'sources' | 'archived';

/** Which Accounts tab lists an account: archived first, then wallets (an Apple Wallet card is mapped to it), then its kind. */
export function accountTab(account: FinanceAccount, overview: FinanceAccountOverview | undefined): Exclude<AccountsTab, 'sources'> {
  if (account.archived) return 'archived';
  if (account.kind === 'card') return 'cards';
  if (overview?.sources.includes('apple_wallet')) return 'wallets';
  if (account.kind === 'bank' || account.kind === 'cash') return 'cash';
  return 'other';
}

/** The badge an account card shows: the richest source that feeds it. Never "connected": ANUM has no live bank link. */
export function primarySource(sources: readonly FinanceAccountSource[] | undefined): FinanceAccountSource {
  for (const s of ['sms', 'apple_wallet', 'statement'] as const) if (sources?.includes(s)) return s;
  return 'manual';
}

/** Whole percent, or null when there is nothing to divide. */
export function percentOf(part: number, whole: number): number | null {
  return whole > 0 ? Math.round((part * 100) / whole) : null;
}

export interface ActivityBar { day: string; inflow: number; outflow: number }

/** Bars for the activity chart and a "nice" top of the scale with four ticks. */
export function activityScale(activity: Pick<FinanceAccountActivity, 'days'>): { bars: ActivityBar[]; top: number; ticks: number[]; empty: boolean } {
  const bars = activity.days.map((d) => ({ day: d.day, inflow: Number(d.inflow), outflow: Number(d.outflow) }));
  const max = Math.max(0, ...bars.map((b) => Math.max(b.inflow, b.outflow)));
  if (max === 0) return { bars, top: 1, ticks: [0], empty: true };
  const raw = max / 4;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((f) => f * power).find((s) => s >= raw) ?? raw;
  const top = step * 4;
  return { bars, top, ticks: [0, step, step * 2, step * 3, top], empty: false };
}

/** Compact axis figures: 15000 -> "15k". */
export function compact(value: number, locale: string): string {
  return new Intl.NumberFormat(locale, { notation: 'compact', maximumFractionDigits: 1 }).format(value);
}

/** A figure without its currency code, for columns whose header already names the currency ("Inflow (SAR)"). */
export function plainAmount(value: string, digits: number, locale: string): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return '-';
  return new Intl.NumberFormat(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);
}
