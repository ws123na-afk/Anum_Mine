// The figures behind the operations pages (docs/home.md#operations-overview, docs/vehicles.md#web-page,
// docs/business-books.md#overview-dashboard, docs/finance.md#finance-overview): vehicle status and usage,
// the books' expense breakdown, recurring costs and budgets, and the Attention Center's items. Pure, so
// `pnpm --filter @anum/web test:unit` covers it. Money stays per currency and is never added across currencies.
import type { BooksReport, VehicleDueItem, VehicleUsageMonth } from '@anum/contracts';
import { add, fromMilli, toMilli, type MonthFlow } from './financeStatements.ts';
import { lineLabel } from './businessBooks.ts';
import { formatDateTime, t } from './i18n.ts';

export type Tone = 'ok' | 'warn' | 'bad' | 'info' | 'muted';

// ---- vehicles -------------------------------------------------------------------------------------------

/** A vehicle's status from what is due: overdue (by date or kilometres), due soon, or in service. */
export function vehicleStatus(due: readonly VehicleDueItem[]): { tone: Tone; label: string; item: VehicleDueItem | null } {
  const overdue = due.find((d) => (d.days_left !== null && d.days_left < 0) || (d.km_left !== null && Number(d.km_left) < 0));
  if (overdue) return { tone: 'bad', label: t('ops.veh.status.overdue'), item: overdue };
  const next = nextDue(due);
  if (!next) return { tone: 'ok', label: t('ops.veh.status.ok'), item: null };
  if (next.days_left !== null) return { tone: 'warn', label: next.days_left === 0 ? t('ops.veh.status.today') : t('ops.veh.status.dueIn', { count: next.days_left }), item: next };
  return { tone: 'warn', label: t('ops.veh.status.dueKm'), item: next };
}

/** The soonest due item: by days left, then by kilometres left. */
export function nextDue(due: readonly VehicleDueItem[]): VehicleDueItem | null {
  const sorted = [...due].sort((a, b) => (a.days_left ?? 100_000) - (b.days_left ?? 100_000) || Number(a.km_left ?? 1e9) - Number(b.km_left ?? 1e9));
  return sorted[0] ?? null;
}

/** One row per month (oldest first), zero where the API has no usage, for the litres and kilometres chart. */
export function usageRows(usage: readonly VehicleUsageMonth[], months: readonly string[]): { month: string; litres: string; km: string }[] {
  return months.map((month) => {
    const row = usage.find((u) => u.month === month);
    return { month, litres: row?.fuel_litres ?? '0', km: row?.distance_km ?? '0' };
  });
}

/** Kilometres in one month ("0" when none). */
export const kmIn = (usage: readonly VehicleUsageMonth[], month: string) => usage.find((u) => u.month === month)?.distance_km ?? '0';

/** One currency's amount in a month from `{month, currency, amount}` rows ("0" when none). */
export const amountIn = (rows: readonly { month: string; currency: string; amount: string }[], month: string, currency: string) =>
  rows.find((r) => r.month === month && r.currency === currency)?.amount ?? '0';

/** The currency most rows are in (ties: the first seen), or the fallback. Money is shown in it, never converted. */
export function mainCurrency(rows: readonly { currency: string }[], fallback: string): string {
  const counts = new Map<string, number>();
  for (const row of rows) counts.set(row.currency, (counts.get(row.currency) ?? 0) + 1);
  let best = fallback, most = 0;
  for (const [currency, count] of counts) if (count > most) { best = currency; most = count; }
  return best;
}

/** L/100 km as km per litre (the figure fleets in the Gulf quote), one decimal; null without an average. */
export function kmPerLitre(per100: string | null | undefined): string | null {
  const value = Number(per100);
  if (!per100 || !Number.isFinite(value) || value <= 0) return null;
  return (Math.round((100 / value) * 10) / 10).toFixed(1);
}

export type PaperKind = 'registration' | 'insurance' | 'inspection';

/** A vehicle's papers with an expiry date, soonest first, with days left from `today` ("YYYY-MM-DD"). */
export function papers(v: { registration_expires_on: string | null; insurance_expires_on: string | null; inspection_expires_on: string | null }, today: string):
  { kind: PaperKind; on: string; days: number }[] {
  const day = (iso: string) => Date.parse(`${iso}T00:00:00Z`);
  const list: { kind: PaperKind; on: string | null }[] = [
    { kind: 'registration', on: v.registration_expires_on }, { kind: 'insurance', on: v.insurance_expires_on }, { kind: 'inspection', on: v.inspection_expires_on },
  ];
  return list.filter((p): p is { kind: PaperKind; on: string } => Boolean(p.on))
    .map((p) => ({ ...p, days: Math.round((day(p.on) - day(today)) / 86_400_000) }))
    .sort((a, b) => a.days - b.days);
}

export interface RecentRecord { kind: 'fuel' | 'service' | 'trips'; id: string; at: string; title: string; detail: string; figure: string }

/** The newest records of each kind as one list, newest first, at most `count`. */
export function newestFirst(records: readonly RecentRecord[], count = 4): RecentRecord[] {
  return [...records].sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0)).slice(0, count);
}

// ---- business books -------------------------------------------------------------------------------------

/** Revenue, costs and profit for each of `months` ("YYYY-MM", oldest first) from the report's trend; zero where it has none. */
export function trendFor(report: BooksReport, months: readonly string[]): MonthFlow[] {
  return months.map((month) => {
    const row = report.trend.find((r) => r.month.slice(0, 7) === month);
    return { month, income: row?.revenue ?? '0', expenses: row?.costs ?? '0', net: row?.profit ?? '0' };
  });
}

/** The month a period covers when it is exactly one calendar month ("YYYY-MM"), else null. */
export function singleMonth(period: { from: string; to: string }): string | null {
  const month = period.from.slice(0, 7);
  const [year, number] = month.split('-').map(Number);
  if (!year || !number) return null;
  const last = new Date(Date.UTC(year, number, 0)).getUTCDate();
  return period.from === `${month}-01` && period.to === `${month}-${String(last).padStart(2, '0')}` ? month : null;
}

/** The month before `month`, as the KPI chips name it ("vs October"). */
export function previousMonthName(month: string): string {
  const [year, number] = month.split('-').map(Number);
  return formatDateTime(Date.UTC(year, number - 2, 15, 12), { month: 'long', timeZone: 'UTC' });
}

export interface BreakdownRow { key: string; label: string; amount: string }

/**
 * Where the money went in the books' base currency: cost of sales, operating expense and finance cost
 * lines (a category in two groups is one row), largest first. Above `top` rows the rest are one "Other"
 * row, so the shares still add up to the whole.
 */
export function expenseBreakdown(report: BooksReport, top = 5): BreakdownRow[] {
  const c = report.base_currency;
  const sums = new Map<string, string>();
  for (const group of [report.pnl.cost_of_sales, report.pnl.operating_expenses, report.pnl.finance_costs]) {
    for (const line of group.lines) sums.set(line.key, add(c, sums.get(line.key) ?? '0', line.amount));
  }
  const rows = [...sums.entries()].filter(([, amount]) => toMilli(amount) > 0n)
    .map(([key, amount]) => ({ key, label: lineLabel(key), amount }))
    .sort((a, b) => (toMilli(b.amount) > toMilli(a.amount) ? 1 : toMilli(b.amount) < toMilli(a.amount) ? -1 : a.label.localeCompare(b.label)));
  if (rows.length <= top) return rows;
  const rest = rows.slice(top - 1);
  return [...rows.slice(0, top - 1), { key: '__other', label: t('ops.other'), amount: add(c, ...rest.map((r) => r.amount)) }];
}

/** The sum of the breakdown in the base currency (the total its shares are of). */
export const breakdownTotal = (rows: readonly BreakdownRow[], currency: string) => fromMilli(rows.reduce((s, r) => s + toMilli(r.amount), 0n), currency);

/** Cash flow for the period in one currency ("0" when none). */
export const netIn = (rows: readonly { currency: string; amount: string }[], currency: string) => rows.find((r) => r.currency === currency)?.amount ?? '0';

/** The recurring costs with the largest cost per month in `currency`, with each one's share of the month's expenses. */
export function topRecurring(report: BooksReport, expenses: string, count = 3): { key: string; name: string; category: string; perMonth: string; share: number | null }[] {
  const c = report.base_currency;
  const whole = toMilli(expenses);
  return report.recurring.items.filter((item) => item.currency === c && toMilli(item.per_month) > 0n)
    .sort((a, b) => (toMilli(b.per_month) > toMilli(a.per_month) ? 1 : -1)).slice(0, count)
    .map((item) => ({
      key: item.template_id ?? item.bill_id ?? `${item.vendor_id}:${item.category}`, name: item.vendor_name, category: item.category, perMonth: item.per_month,
      share: whole > 0n ? Number((toMilli(item.per_month) * 1000n) / whole) / 10 : null,
    }));
}

/** Budgets that can be compared with what was spent, most used first. */
export function budgetProgress(report: BooksReport): { id: string; name: string; currency: string; budget: string; actual: string; percent: number; tone: Tone }[] {
  return report.budgets.filter((b) => b.comparable && b.actual !== null && b.used_pct !== null)
    .map((b) => {
      const percent = Number(b.used_pct);
      return { id: b.id, name: b.name, currency: b.currency, budget: b.budget, actual: b.actual ?? '0', percent, tone: (percent > 100 ? 'bad' : percent >= 85 ? 'warn' : 'ok') as Tone };
    })
    .sort((a, b) => b.percent - a.percent);
}

/** What still needs doing before the month can be closed, from the report: each item with a count and a tone. */
export function closeChecklist(report: BooksReport): { key: string; count: number; done: boolean }[] {
  const overdueBills = report.balances.unpaid_bills.filter((b) => b.days_overdue > 0).length;
  const overdueInvoices = report.balances.outstanding_invoices.filter((i) => i.days_overdue > 0).length;
  return [
    { key: 'allocations', count: report.allocations_in_review, done: report.allocations_in_review === 0 },
    { key: 'overdueBills', count: overdueBills, done: overdueBills === 0 },
    { key: 'overdueInvoices', count: overdueInvoices, done: overdueInvoices === 0 },
    { key: 'converted', count: report.pnl.not_converted.length, done: report.pnl.complete && report.pnl.not_converted.length === 0 },
  ];
}

// ---- the Attention Center -------------------------------------------------------------------------------

export type AttentionKind = 'vehicle' | 'document' | 'review' | 'return' | 'purchase' | 'approval' | 'allocation' | 'bill';
export interface AttentionItem { key: string; kind: AttentionKind; title: string; detail: string; chip: string | null; tone: Tone; href: string; rank: number; count: number }

/** Most urgent first: by `rank` (lower is sooner), then by title. At most `count`. */
export function rankAttention(items: readonly AttentionItem[], count = 6): AttentionItem[] {
  return [...items].sort((a, b) => a.rank - b.rank || a.title.localeCompare(b.title)).slice(0, count);
}

/** Everything that waits, counted once per thing (an item for "3 documents" counts 3). */
export const attentionTotal = (items: readonly AttentionItem[]) => items.reduce((sum, item) => sum + item.count, 0);

/** Of the week's scheduled days, how many had work; without a schedule, the days worked alone. */
export function weekAttendance(days: readonly { scheduled_seconds: number; worked_seconds: number }[]): { attended: number; scheduled: number | null } {
  const scheduled = days.filter((d) => d.scheduled_seconds > 0);
  if (scheduled.length === 0) return { attended: days.filter((d) => d.worked_seconds > 0).length, scheduled: null };
  return { attended: scheduled.filter((d) => d.worked_seconds > 0).length, scheduled: scheduled.length };
}

/** "12 days", "Today", "3 days late" for a number of days left. */
export function daysChip(days: number): string {
  if (days < 0) return t('ops.days.late', { count: -days });
  if (days === 0) return t('ops.days.today');
  return t('ops.days.left', { count: days });
}

export function daysTone(days: number): Tone {
  return days < 0 ? 'bad' : days <= 14 ? 'warn' : 'info';
}
