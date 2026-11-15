// System health (docs/system-health.md): the platform owner's read-only view of this deployment from
// GET /api/v1/platform/health. The API reads every value from real state; these helpers only order the
// checks, turn their numbers into sentences and say what to do. Pure, so the unit tests run it without a
// browser (test/systemHealth.test.ts).
import { formatDateTime, formatNumber, t, type MessageKey } from './i18n.ts';

export type HealthState = 'green' | 'amber' | 'red' | 'unknown';
export type HealthKey =
  | 'backup_local' | 'backup_offsite' | 'restore_drill' | 'ai_model' | 'disk' | 'memory' | 'email' | 'scheduler' | 'errors'
  | 'restarts' | 'notices';

export const HEALTH_KEYS: readonly HealthKey[] = [
  'backup_local', 'backup_offsite', 'restore_drill', 'ai_model', 'disk', 'memory', 'email', 'scheduler', 'errors', 'restarts', 'notices',
];

export type HealthItem = { key: HealthKey; state: HealthState; detail: Record<string, unknown> };
export type SystemHealth = {
  checked_at: string;
  overall: Exclude<HealthState, 'unknown'>;
  started_at: string;
  uptime_seconds: number;
  counts: Record<HealthState, number>;
  items: HealthItem[];
  you: { tenant_id: string; workspace_id: string };
};

const RANK: Record<HealthState, number> = { red: 0, amber: 1, unknown: 2, green: 3 };

/** Problems first (red, then amber, then not known), each group in the API's order; unknown keys dropped. */
export function sortHealth(items: HealthItem[]): HealthItem[] {
  return items
    .filter((item) => (HEALTH_KEYS as readonly string[]).includes(item.key))
    .map((item, index) => ({ item, index }))
    .sort((a, b) => RANK[a.item.state] - RANK[b.item.state] || a.index - b.index)
    .map(({ item }) => item);
}

/** How many checks need the owner (red or amber). */
export function needsAttention(items: HealthItem[]): number {
  return items.filter((item) => item.state === 'red' || item.state === 'amber').length;
}

/** A byte count in GB (one decimal) or MB, with Western digits in both languages. */
export function formatGigabytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '-';
  const gb = bytes / 1024 ** 3;
  return gb >= 1 ? t('health.unit.gb', { value: formatNumber(Math.round(gb * 10) / 10) }) : t('health.unit.mb', { value: formatNumber(Math.round(bytes / 1024 ** 2)) });
}

/** An age in hours as "N hours" below two days, else "N days". */
export function formatAgeHours(hours: number): string {
  if (!Number.isFinite(hours) || hours < 0) return '-';
  if (hours < 48) return t('health.age.hours', { count: Math.max(0, Math.round(hours)) });
  return t('health.age.days', { count: Math.round(hours / 24) });
}

/** Uptime in minutes, hours or days. */
export function formatUptime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '-';
  if (seconds < 3600) return t('health.age.minutes', { count: Math.max(1, Math.round(seconds / 60)) });
  return formatAgeHours(seconds / 3600);
}

const when = (value: unknown): string => (typeof value === 'string' && value ? formatDateTime(value, { dateStyle: 'medium', timeStyle: 'short' }) : '-');
const num = (value: unknown): number => (typeof value === 'number' ? value : Number.NaN);

/** The sentences under one check, from its numbers. */
export function healthLines(item: HealthItem): string[] {
  const d = item.detail;
  const lines: string[] = [];
  switch (item.key) {
    case 'backup_local': case 'backup_offsite': case 'restore_drill': {
      if (d.recorded === false) { lines.push(t('health.backup.notRecorded')); break; }
      if (!d.ever) lines.push(t(item.key === 'restore_drill' ? 'health.drill.never' : 'health.backup.never'));
      else {
        const age = item.key === 'restore_drill' ? num(d.age_days) * 24 : num(d.age_hours);
        lines.push(t('health.backup.lastSuccess', { when: when(d.last_success_at), age: formatAgeHours(age) }));
      }
      if (d.last_ok === false && d.last_run_at) lines.push(t('health.backup.lastFailed', { when: when(d.last_run_at) }));
      if (item.key !== 'restore_drill' && typeof d.max_age_hours === 'number') lines.push(t('health.backup.limit', { hours: d.max_age_hours }));
      break;
    }
    case 'ai_model':
      lines.push(d.placeholder ? t('health.model.placeholder') : d.key_missing ? t('health.model.keyMissing', { provider: String(d.provider) }) : t('health.model.real', { provider: String(d.provider) }));
      break;
    case 'disk':
      if (d.readable === false) lines.push(t('health.unreadable'));
      else lines.push(t('health.disk.free', { free: formatGigabytes(num(d.free_bytes)), total: formatGigabytes(num(d.total_bytes)), pct: formatNumber(num(d.free_pct)) }));
      break;
    case 'memory':
      if (typeof d.available_pct === 'number') lines.push(t('health.memory.vm', { free: formatGigabytes(num(d.available_bytes)), total: formatGigabytes(num(d.total_bytes)), pct: formatNumber(d.available_pct) }));
      if (typeof d.api_free_pct === 'number') lines.push(t('health.memory.api', { used: formatGigabytes(num(d.api_used_bytes)), limit: formatGigabytes(num(d.api_limit_bytes)), pct: formatNumber(d.api_free_pct) }));
      if (!lines.length) lines.push(t('health.unreadable'));
      break;
    case 'email':
      lines.push(d.configured ? t('health.email.on') : t('health.email.off'));
      break;
    case 'scheduler':
      if (!d.enabled) lines.push(t('health.scheduler.off'));
      else if (d.starting) lines.push(t('health.scheduler.starting'));
      else lines.push(d.last_pass_at ? t('health.scheduler.lastPass', { when: when(d.last_pass_at) }) : t('health.scheduler.noPass'));
      if (d.enabled && d.sweep_last_pass_at) lines.push(t('health.scheduler.sweep', { when: when(d.sweep_last_pass_at) }));
      break;
    case 'errors':
      lines.push(t('health.errors.count', { count: num(d.count_24h) || 0 }));
      if (d.last_at) lines.push(t('health.errors.last', { when: when(d.last_at) }));
      lines.push(t('health.errors.scope'));
      break;
    case 'restarts':
      lines.push(t('health.restarts.uptime', { since: when(d.started_at), uptime: formatUptime(num(d.uptime_seconds)) }));
      if (d.configured === false) lines.push(t('health.restarts.notConfigured'));
      else if (d.readable === false) lines.push(t('health.unreadable'));
      else lines.push(d.last_unexpected_at ? t('health.restarts.last', { when: when(d.last_unexpected_at) }) : t('health.restarts.none'));
      break;
    case 'notices':
      lines.push(d.configured ? t('health.notices.on') : t('health.notices.off'));
      break;
  }
  return lines;
}

export const stateKey = (state: HealthState): MessageKey => `health.state.${state}` as MessageKey;
export const titleKey = (key: HealthKey): MessageKey => `health.item.${key}` as MessageKey;
export const whyKey = (key: HealthKey): MessageKey => `health.why.${key}` as MessageKey;
export const fixKey = (key: HealthKey): MessageKey => `health.fix.${key}` as MessageKey;
