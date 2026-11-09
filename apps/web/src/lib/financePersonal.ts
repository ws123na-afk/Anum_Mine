// Lent and borrowed, payslips, sending money home, fixed commitments and the Plan view: pure helpers for the
// pages (docs/finance.md#lent-and-borrowed and onwards), so `pnpm --filter @anum/web test:unit` covers them
// without a browser. The API works out every stored figure; these only preview a form and pick tones and labels.
// Money is worked in whole minor units, never floating point sums.
import type { FinanceAffordVerdict, FinanceCommitment, FinanceDeduction, FinanceLoanStatus, FinancePayoff } from '@anum/contracts';
import { minorDigits } from './finance.ts';

export const PERSONAL_PAGE = 20;

/** "1234.5" in `currency` as whole minor units; empty or unreadable is null. */
export function minor(value: string | null | undefined, currency: string): number | null {
  const text = (value ?? '').trim().replace(/,/g, '');
  if (!/^\d+(\.\d+)?$/.test(text)) return null;
  const [whole, frac = ''] = text.split('.');
  const digits = minorDigits(currency);
  const padded = (frac + '0'.repeat(digits)).slice(0, digits);
  const rest = frac.slice(digits);
  let units = Number(whole) * 10 ** digits + (digits ? Number(padded) : 0);
  // Half to even on the first dropped digit, like the server.
  if (rest) {
    const first = Number(rest[0]), more = /[1-9]/.test(rest.slice(1));
    if (first > 5 || (first === 5 && (more || units % 2 === 1))) units += 1;
  }
  return units;
}

export function fromMinorUnits(units: number, currency: string): string {
  const digits = minorDigits(currency);
  const sign = units < 0 ? '-' : '';
  const abs = Math.abs(units);
  if (!digits) return `${sign}${abs}`;
  const text = String(abs).padStart(digits + 1, '0');
  return `${sign}${text.slice(0, -digits)}.${text.slice(-digits)}`;
}

/** Gross less the deductions typed so far, as the server will work it out (null while gross is not a number). */
export function previewNet(gross: string, deductions: readonly Pick<FinanceDeduction, 'amount'>[], currency: string): string | null {
  const g = minor(gross, currency);
  if (g === null) return null;
  const total = deductions.reduce((sum, d) => sum + (minor(d.amount, currency) ?? 0), 0);
  return fromMinorUnits(g - total, currency);
}

/** What a rate gives for an amount sent (received currency's minor unit), or null. */
export function previewReceived(amount: string, rate: string, receivedCurrency: string): string | null {
  const a = Number(amount), r = Number(rate);
  if (!amount || !rate || !Number.isFinite(a) || !Number.isFinite(r) || a <= 0 || r <= 0) return null;
  const digits = minorDigits(receivedCurrency);
  return (Math.round(a * r * 10 ** digits) / 10 ** digits).toFixed(digits);
}

export function loanTone(status: FinanceLoanStatus): 'ok' | 'warn' | 'bad' | 'info' | 'muted' {
  return status === 'overdue' ? 'bad' : status === 'due_soon' ? 'warn' : status === 'repaid' ? 'ok' : status === 'closed' ? 'muted' : 'info';
}

export function verdictTone(verdict: FinanceAffordVerdict): 'good' | 'warn' | 'bad' | '' {
  if (verdict === 'yes_from_cash' || verdict === 'monthly_comfortable') return 'good';
  if (verdict === 'too_much') return 'bad';
  if (verdict === 'not_enough_data') return '';
  return 'warn';
}

/** The commitments of one currency, grouped by kind in the server's order, with each group's monthly total. */
export function commitmentGroups(items: readonly FinanceCommitment[], currency: string): { kind: FinanceCommitment['kind']; total: string; items: FinanceCommitment[] }[] {
  const groups = new Map<FinanceCommitment['kind'], FinanceCommitment[]>();
  for (const item of items) if (item.currency === currency) groups.set(item.kind, [...(groups.get(item.kind) ?? []), item]);
  return [...groups.entries()].map(([kind, rows]) => ({
    kind, items: rows, total: fromMinorUnits(rows.reduce((s, r) => s + (minor(r.monthly, currency) ?? 0), 0), currency),
  }));
}

/** The order the cheaper strategy pays debts in, as names, for the Plan view's headline. */
export function payoffHeadline(p: FinancePayoff): { strategy: 'avalanche' | 'snowball'; first: string | null } {
  const strategy = p.cheaper === 'snowball' ? 'snowball' : 'avalanche';
  const firstId = p[strategy].order[0];
  return { strategy, first: p.debts.find((d) => d.id === firstId)?.name ?? null };
}

/** A page count for offset paging. */
export function pages(total: number, size = PERSONAL_PAGE): number {
  return Math.max(1, Math.ceil(total / size));
}

/** Today in the browser as YYYY-MM-DD (UTC), for new records' default dates. */
export function todayIso(now = new Date()): string {
  return now.toISOString().slice(0, 10);
}
