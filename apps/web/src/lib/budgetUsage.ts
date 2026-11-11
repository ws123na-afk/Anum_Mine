// The spend breakdown on Budgets (docs/model-gateway.md#spend-breakdown): `GET /api/v1/model-budgets/usage`
// grouped by day, month, person, agent or feature. Pure helpers (ranges, the request path, labels, the
// chart's scale), so `pnpm --filter @anum/web test:unit` covers them without a browser.
import { formatUsd, formatTokens } from './admin.ts';
import { formatDateTime, t, type MessageKey } from './i18n.ts';

export type UsageGroup = 'day' | 'month' | 'user' | 'agent' | 'feature';
export type UsageScope = 'workspace' | 'tenant';
export type UsageMeasure = 'cost' | 'tokens';
export type RangePreset = 'month' | '30d' | '3m' | '12m' | 'custom';

export const USAGE_GROUPS: readonly UsageGroup[] = ['day', 'month', 'user', 'agent', 'feature'];
export const RANGE_PRESETS: readonly RangePreset[] = ['month', '30d', '3m', '12m', 'custom'];
/** Groups per page; the API caps a page at 500. */
export const USAGE_PAGE = 50;
/** The API refuses a longer range. */
export const MAX_RANGE_DAYS = 400;

/** One row of the API's answer (snake_case, as sent). */
export interface UsageRow {
  key: string;
  label: string | null;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
  calls: number;
  unpriced_calls: number;
}

export interface UsageTotals { input_tokens: number; output_tokens: number; estimated_cost_usd: number; calls: number; unpriced_calls: number }

export interface UsageBreakdown {
  scope: UsageScope;
  group_by: UsageGroup;
  from_day: string;
  to_day: string;
  rows: UsageRow[];
  total: UsageTotals;
  count: number;
  limit: number;
  offset: number;
  truncated: boolean;
  recorded_since: string | null;
}

export interface UsageRange { from: string; to: string }

const iso = (date: Date): string => date.toISOString().slice(0, 10);
const utc = (year: number, month: number, day: number): Date => new Date(Date.UTC(year, month, day));

/** The `from` and `to` (both inclusive, UTC days) a preset means on `today`. */
export function rangeFor(preset: RangePreset, today: Date, custom: UsageRange = { from: '', to: '' }): UsageRange {
  const y = today.getUTCFullYear(), m = today.getUTCMonth(), d = today.getUTCDate();
  switch (preset) {
    case 'month': return { from: iso(utc(y, m, 1)), to: iso(utc(y, m, d)) };
    case '30d': return { from: iso(utc(y, m, d - 29)), to: iso(utc(y, m, d)) };
    case '3m': return { from: iso(utc(y, m - 2, 1)), to: iso(utc(y, m, d)) };
    case '12m': return { from: iso(utc(y, m - 11, 1)), to: iso(utc(y, m, d)) };
    default: return custom;
  }
}

/** Whether `range` is two dates the API accepts: `to` not before `from`, at most 400 days apart. */
export function validRange(range: UsageRange): boolean {
  const from = Date.parse(`${range.from}T00:00:00Z`), to = Date.parse(`${range.to}T00:00:00Z`);
  if (Number.isNaN(from) || Number.isNaN(to) || to < from) return false;
  return (to - from) / 86_400_000 <= MAX_RANGE_DAYS;
}

/** The request path for one page (or the whole range as CSV). */
export function usagePath(input: { group: UsageGroup; scope: UsageScope; range: UsageRange; offset?: number; limit?: number; format?: 'json' | 'csv' }): string {
  const params = new URLSearchParams({ group_by: input.group, scope: input.scope, from: input.range.from, to: input.range.to });
  if (input.format === 'csv') params.set('format', 'csv');
  else { params.set('limit', String(input.limit ?? USAGE_PAGE)); params.set('offset', String(input.offset ?? 0)); }
  return `/api/v1/model-budgets/usage?${params.toString()}`;
}

const FEATURES = new Set(['tasks', 'voice', 'assistant', 'email', 'knowledge', 'meetings', 'business', 'marketing', 'finance', 'calls', 'speech', 'browser', 'attendance', 'executive', 'whatsapp', 'commerce', 'developer', 'other']);

/** The words a row's key reads as: a date, a month, a person, an agent's name or a feature's name. */
export function rowLabel(group: UsageGroup, row: Pick<UsageRow, 'key' | 'label'>, currentUserId: string | null = null): string {
  const isDate = !Number.isNaN(Date.parse(`${row.key}T12:00:00Z`));
  switch (group) {
    // A key that is not a date (never from the API) is shown as it is rather than failing the page.
    case 'day': return isDate ? formatDateTime(`${row.key}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }) : row.key || '-';
    case 'month': return isDate ? formatDateTime(`${row.key}T12:00:00Z`, { month: 'long', year: 'numeric', timeZone: 'UTC' }) : row.key || '-';
    case 'user': return row.key === currentUserId ? t('budgets.usage.user.you', { user: row.key }) : row.key || '-';
    case 'agent': return row.label ?? row.key ?? '-';
    default: return FEATURES.has(row.key) ? t(`budgets.usage.feature.${row.key}` as MessageKey) : row.key || '-';
  }
}

/** A short tick for the chart's axis (day of month, or the month's short name). */
export function tickLabel(group: UsageGroup, key: string): string {
  if (Number.isNaN(Date.parse(`${key}T12:00:00Z`))) return key;
  if (group === 'day') return formatDateTime(`${key}T12:00:00Z`, { day: 'numeric', timeZone: 'UTC' });
  if (group === 'month') return formatDateTime(`${key}T12:00:00Z`, { month: 'short', timeZone: 'UTC' });
  return key;
}

export const measureOf = (row: UsageRow | UsageTotals, measure: UsageMeasure): number =>
  measure === 'cost' ? row.estimated_cost_usd : row.input_tokens + row.output_tokens;

export const formatMeasure = (value: number, measure: UsageMeasure): string => (measure === 'cost' ? formatUsd(value) : formatTokens(value));

/** Each row's share of the period's total, whole percents (0 when the total is 0). */
export function shareOf(row: UsageRow, total: UsageTotals, measure: UsageMeasure): number {
  const whole = measureOf(total, measure);
  return whole > 0 ? Math.round((measureOf(row, measure) / whole) * 100) : 0;
}

/** Bar lengths as percents of the largest loaded row (at least 2 so a tiny value still shows). */
export function barLengths(rows: readonly UsageRow[], measure: UsageMeasure): number[] {
  const top = Math.max(0, ...rows.map((row) => measureOf(row, measure)));
  return rows.map((row) => (top > 0 ? Math.max(2, (measureOf(row, measure) / top) * 100) : 0));
}

/** Round axis ticks (0, step, 2 step, ...) that cover `max`, `count` of them. */
export function ticks(max: number, count = 4): number[] {
  if (max <= 0) return [0, 1];
  const raw = max / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((candidate) => candidate >= raw) ?? magnitude * 10;
  const result: number[] = [];
  for (let value = 0; value < max + step; value += step) result.push(Number(value.toFixed(8)));
  return result;
}

/** Whether `group` is a time series (columns) rather than a ranking (bars). */
export const isTimeSeries = (group: UsageGroup): boolean => group === 'day' || group === 'month';

/** The CSV file name the API sends, for the download link's fallback. */
export const csvFileName = (scope: UsageScope, group: UsageGroup, range: UsageRange): string => `model-usage-${scope}-${group}-${range.from}-${range.to}.csv`;
