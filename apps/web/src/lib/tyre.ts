// Tyre planning (docs/tyre-planning.md): API shapes and pure helpers for TyreView.
// No browser or Vite APIs, so `pnpm test:unit` exercises them directly.
import { formatDateTime, formatNumber, t, type MessageKey } from './i18n.ts';

export type SourceKind = 'supabase_auth' | 'supabase_postgres' | 'supabase_rest' | 'firestore';
export type EntityName = 'vehicles' | 'tyres' | 'inspections' | 'pressure_readings' | 'removals' | 'costs' | 'suppliers'
  | 'inspection_visits' | 'inspection_schedules' | 'checklists' | 'work_orders' | 'odometer_readings' | 'hour_readings' | 'sites';
export type ConnectionStatus = 'untested' | 'ok' | 'failed';
export type Severity = 'critical' | 'warning' | 'info';
export type AlertKind = 'low_tread' | 'under_inflation' | 'over_inflation' | 'overdue_inspection' | 'abnormal_wear' | 'cost_anomaly';
export type AlertStatus = 'open' | 'acknowledged' | 'resolved';
export type ReportKind = 'fleet_health' | 'wear_rates' | 'pressure_compliance' | 'cost_per_distance' | 'scrap_reasons' | 'brand_performance' | 'removal_forecast' | 'purchase_plan';
export type Confidence = 'high' | 'medium' | 'low';
export type ForecastState = 'due' | 'projected' | 'no_wear' | 'no_data';

export const ENTITIES: EntityName[] = [
  'tyres', 'vehicles', 'inspections', 'pressure_readings', 'removals', 'costs', 'suppliers',
  'inspection_visits', 'inspection_schedules', 'checklists', 'work_orders', 'odometer_readings', 'hour_readings', 'sites',
];
export const SOURCE_KINDS: SourceKind[] = ['supabase_auth', 'supabase_postgres', 'supabase_rest', 'firestore'];
export const REPORT_KINDS: ReportKind[] = ['fleet_health', 'wear_rates', 'pressure_compliance', 'cost_per_distance', 'scrap_reasons', 'brand_performance', 'removal_forecast', 'purchase_plan'];
export const ALERT_KINDS: AlertKind[] = ['low_tread', 'under_inflation', 'over_inflation', 'overdue_inspection', 'abnormal_wear', 'cost_anomaly'];
export const TYRE_LINKED: EntityName[] = ['inspections', 'pressure_readings', 'removals'];
/** Readings may also attach to a vehicle (and wheel position), as TyrePulse's inspections do. */
export const VEHICLE_LINK_ALLOWED: EntityName[] = ['inspections', 'pressure_readings'];

export interface FieldSpec { name: string; kind: 'text' | 'number' | 'date' | 'boolean'; required: boolean; description: string }
export interface SourceColumn { name: string; type: string }
export interface SourceTable { name: string; columns: SourceColumn[]; rows: number | null; writable: boolean }
/** `expand`: a column with one entry per tyre, read as "<column>.<key>"; `window_days`: only rows newer than that by `window_column`. */
export interface EntityMapping { source: string; fields: Record<string, string>; expand?: string | null; window_days?: number | null; window_column?: string | null }
export interface Units { tread: 'mm' | '32nds'; pressure: 'psi' | 'bar' | 'kpa'; distance: 'km' | 'miles'; currency: string }
export interface TyreMapping { preset: string | null; entities: Partial<Record<EntityName, EntityMapping>>; units: Units; status_values: Record<string, string>; position_aliases?: Record<string, string> }
export interface Assumptions {
  min_tread_mm: number; warning_band_mm: number; pressure_tolerance_pct: number; default_target_pressure_psi: number | null;
  inspection_interval_days: number; abnormal_wear_factor: number; cost_anomaly_factor: number; default_new_tread_mm: number;
  horizon_months: number; safety_stock_pct: number; include_low_confidence: boolean; subtract_stock: boolean;
  unit_prices: Record<string, number>; preferred_brands: Record<string, string>;
}
export interface ConnectionView {
  kind: SourceKind; label: string; endpoint: string; schema_name: string; credential_configured: boolean; secret_hint: string | null;
  status: ConnectionStatus; last_tested_at: string | null; last_error: string | null; updated_at: string; updated_by: string;
}
export interface EntityStats { read: number; kept: number; skipped: number; cut: boolean; issues: Record<string, number> }
export interface SyncView { synced_at: string; synced_by: string; duration_ms: number; entities: Record<string, EntityStats>; counts: Record<string, number> }
export interface DiscoveryView { tables: SourceTable[]; discovered_at: string | null; suggestion?: TyreMapping | null; writable_tables: string[] }
export interface TyreSetup {
  connection: ConnectionView | null; mapping: TyreMapping | null; mapping_problems: string[]; assumptions: Assumptions;
  discovery: DiscoveryView | null; sync: SyncView | null; fields: Record<EntityName, FieldSpec[]>; presets: string[];
  can_manage: boolean; can_operate: boolean; delivery_channels: string[];
}
export interface FleetSummary {
  as_of: string; currency: string; vehicles: number; tyres_fitted: number; tyres_in_stock: number; tyres_removed_last_30_days: number;
  tyres_with_readings: number; average_tread_mm: number | null; below_minimum: number; in_warning_band: number; pressure_checked: number;
  pressure_compliant_pct: number | null; under_inflated: number; over_inflated: number; overdue_inspections: number;
  due_next_30_days: number; due_next_90_days: number; cost_per_1000_km: number | null; cost_per_hour: number | null; min_tread_mm: number;
}
export interface TyreForecast {
  tyre_id: string; serial: string | null; brand: string | null; size: string | null; vehicle_id: string | null; vehicle_label: string | null;
  position: string | null; state: ForecastState; basis: 'distance' | 'time' | 'fitment' | 'group' | 'none'; confidence: Confidence; readings: number;
  last_inspected_at: string | null; current_tread_mm: number | null; wear_mm_per_1000km: number | null; wear_mm_per_30_days: number | null;
  remaining_mm: number | null; projected_removal: string | null; earliest_removal: string | null; latest_removal: string | null; note: string;
}
export interface TyreAlert {
  id: string; key: string; kind: AlertKind; severity: Severity; subject_type: string; subject_id: string; subject_label: string;
  message: string; value: Record<string, unknown>; status: AlertStatus; first_seen_at: string; last_seen_at: string;
  resolved_at: string | null; acknowledged_by: string | null; acknowledged_at: string | null;
}
export interface OperationsSummary {
  as_of: string; vehicles: number; vehicles_inspected_last_30_days: number; vehicles_inspected_last_90_days: number;
  inspections_last_30_days: number; inspections_total: number; last_inspection: string | null; tyre_readings: number;
  readings_on_tyre_records: number; schedules_upcoming_14_days: number; schedules_overdue: number; checklists_last_30_days: number;
  checklists_failed_last_30_days: number; checklists_total: number; work_orders_open: number; work_orders_opened_last_30_days: number;
  work_orders_synced: number; work_order_cost_last_30_days: number | null; work_order_window_days: number | null;
  odometer_readings: number; hour_readings: number; sites: number; currency: string;
}
export interface Dashboard {
  summary: FleetSummary; operations?: OperationsSummary; synced_at: string; alert_counts: Partial<Record<Severity, number>>; top_alerts: TyreAlert[];
  due_soon: TyreForecast[]; due_by_month: Record<string, number>;
}
export interface Page<T> { items: T[]; total: number; limit: number; offset: number }
export interface ForecastPage extends Page<TyreForecast> { min_tread_mm: number; synced_at: string }
export interface AlertPage extends Page<TyreAlert> { counts: Partial<Record<Severity, number>> }
export interface PlanRow {
  month: string; size: string; brand: string; due: number; safety: number; from_stock: number; to_buy: number;
  unit_price: number | null; price_source: 'assumption' | 'data_brand_size' | 'data_size' | 'unknown'; estimated_cost: number | null; low_confidence: number;
}
export interface PurchasePlan {
  as_of: string; currency: string; months: string[]; rows: PlanRow[]; total_tyres: number; total_cost: number | null;
  unpriced_rows: number; skipped_no_size: number; skipped_beyond_horizon: number; assumptions: Assumptions;
}
export interface Delivery { channel: string; to: string; format?: string; status: string; approval_id: string | null; task_id: string | null; requested_at: string; requested_by: string; message?: string }
export interface Highlight { label: string; value: string | number | null }
export interface ReportSummary {
  id: string; kind: ReportKind; title: string; period_from: string | null; period_to: string | null; data_as_of: string | null;
  created_by: string; created_at: string; source: 'manual' | 'schedule'; schedule_id: string | null; highlights: Highlight[]; deliveries: Delivery[];
}
export interface ReportTable { title: string; columns: string[]; rows: (string | number | null)[][] }
export interface Report extends Omit<ReportSummary, 'highlights'> { content: { highlights: Highlight[]; tables: ReportTable[]; notes: string[]; currency: string } }
export interface DeliveryResult { status: string; approval_id: string | null; payload_hash: string | null; task_id: string; message: string; report: ReportSummary }
export interface ReportSchedule {
  id: string; workflow_id: string; kind: ReportKind; title: string; cadence: string; description: string; cron: string; timezone: string;
  enabled: boolean; next_run_at: string | null; last_run_at: string | null; created_by: string | null;
  delivery: { channel: string; to: string; format: string } | null;
}
export interface ScheduleDraft { kind: ReportKind; cadence: 'daily' | 'weekly' | 'monthly'; weekday: number; time: string; timezone: string; channel: string; to: string; format: 'pdf' | 'csv' }
export interface PreviewResult { problems: string[]; stats: Record<string, EntityStats>; sample: Record<string, Record<string, unknown>[]> }

// ---- formatting ----

export function num(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '-';
  const rounded = Math.round(value * 10 ** digits) / 10 ** digits;
  return formatNumber(rounded);
}

export function money(value: number | null | undefined, currency: string): string {
  if (value === null || value === undefined) return t('tyre.common.noPrice');
  return `${formatNumber(Math.round(value))} ${currency}`;
}

export function day(value: string | null | undefined): string {
  if (!value) return '-';
  return formatDateTime(`${value.slice(0, 10)}T12:00:00Z`, { dateStyle: 'medium' });
}

/** "YYYY-MM" as a short month name in the current locale. */
export function monthLabel(month: string): string {
  return formatDateTime(`${month}-15T12:00:00Z`, { month: 'short', year: 'numeric' });
}

export function severityTone(severity: Severity): string {
  return severity === 'critical' ? 'dangerTone' : severity === 'warning' ? 'warning' : 'neutral';
}

export function confidenceTone(confidence: Confidence): string {
  return confidence === 'high' ? 'success' : confidence === 'medium' ? 'warning' : 'neutral';
}

export function forecastTone(item: TyreForecast, today: string): string {
  if (item.state === 'due' || (item.projected_removal && item.projected_removal <= today)) return 'dangerTone';
  if (item.projected_removal && daysBetween(today, item.projected_removal) <= 30) return 'warning';
  return 'neutral';
}

export function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(`${to.slice(0, 10)}T00:00:00Z`) - Date.parse(`${from.slice(0, 10)}T00:00:00Z`)) / 86_400_000);
}

/** Days since a timestamp as "today" / "N days ago" (for "synced" and "inspected"). */
export function ago(value: string, now: Date = new Date()): string {
  const days = Math.max(0, Math.floor((now.getTime() - Date.parse(value)) / 86_400_000));
  return days === 0 ? t('tyre.common.today') : t('tyre.common.daysAgo', { count: days });
}

export function pageRange(page: Page<unknown>): string {
  if (!page.total) return t('tyre.common.noRows');
  return t('tyre.common.showing', { from: page.offset + 1, to: Math.min(page.offset + page.items.length, page.total), total: page.total });
}

export function entityLabel(entity: EntityName): string {
  return t(`tyre.entity.${entity}` as MessageKey);
}

export function sourceKindLabel(kind: SourceKind): string {
  return t(`tyre.kind.${kind}` as MessageKey);
}

export function reportKindLabel(kind: ReportKind): string {
  return t(`tyre.report.${kind}` as MessageKey);
}

export function alertKindLabel(kind: AlertKind): string {
  return t(`tyre.alertKind.${kind}` as MessageKey);
}

// ---- connection form ----

/** Connection form state. `api_key`, `email` and `password` are used only by `supabase_auth`. */
export interface ConnectionDraft {
  kind: SourceKind; label: string; endpoint: string; schema_name: string; secret: string;
  api_key: string; email: string; password: string;
}

export function connectionDraft(connection: ConnectionView | null): ConnectionDraft {
  return {
    kind: connection?.kind ?? 'supabase_auth',
    label: connection?.label ?? 'TyrePulse',
    endpoint: connection && connection.kind !== 'supabase_postgres' ? connection.endpoint : '',
    schema_name: connection?.schema_name ?? 'public',
    secret: '', api_key: '', email: '', password: '',
  };
}

/** The write-only secret the form would send: for the reader sign-in, its three fields as JSON. */
function draftSecret(draft: ConnectionDraft): string {
  if (draft.kind !== 'supabase_auth') return draft.secret.trim();
  if (!draft.api_key.trim() && !draft.email.trim() && !draft.password) return '';
  return JSON.stringify({ api_key: draft.api_key.trim(), email: draft.email.trim(), password: draft.password });
}

/** The body to save, or the problem to show (message key). A saved credential may be kept. */
export function connectionBody(draft: ConnectionDraft, saved: ConnectionView | null):
  { ok: true; value: Record<string, string> } | { ok: false; error: MessageKey } {
  const keepSecret = Boolean(saved && saved.kind === draft.kind && saved.credential_configured);
  const secret = draftSecret(draft);
  if (!secret && !keepSecret) return { ok: false, error: 'tyre.connect.needSecret' };
  if ((draft.kind === 'supabase_rest' || draft.kind === 'supabase_auth') && !/^https?:\/\/[^\s/]+/i.test(draft.endpoint.trim())) return { ok: false, error: 'tyre.connect.needUrl' };
  if (draft.kind === 'supabase_auth' && secret) {
    if (!draft.api_key.trim() || !draft.email.trim() || !draft.password) return { ok: false, error: 'tyre.connect.needReader' };
    if (!/^[^@\s]+@[^@\s]+$/.test(draft.email.trim())) return { ok: false, error: 'tyre.connect.badEmail' };
    if (/^sb_secret_/i.test(draft.api_key.trim())) return { ok: false, error: 'tyre.connect.secretKeyRefused' };
  }
  if (draft.kind === 'supabase_postgres' && secret && !/^postgres(ql)?:\/\//i.test(secret)) return { ok: false, error: 'tyre.connect.needPostgresUrl' };
  if (draft.kind === 'firestore' && secret) {
    try { const parsed = JSON.parse(secret); if (parsed?.type !== 'service_account') return { ok: false, error: 'tyre.connect.needServiceAccount' }; } catch { return { ok: false, error: 'tyre.connect.needServiceAccount' }; }
  }
  if (!/^[a-z_][a-z0-9_]*$/.test(draft.schema_name)) return { ok: false, error: 'tyre.connect.badSchema' };
  const value: Record<string, string> = { kind: draft.kind, label: draft.label.trim() || 'TyrePulse', endpoint: draft.kind === 'firestore' ? (draft.endpoint.trim() || '(default)') : draft.endpoint.trim(), schema_name: draft.schema_name };
  if (secret) value.secret = secret;
  return { ok: true, value };
}

// ---- mapping editor ----

export function emptyMapping(): TyreMapping {
  return { preset: null, entities: {}, units: { tread: 'mm', pressure: 'psi', distance: 'km', currency: 'SAR' }, status_values: {}, position_aliases: {} };
}

/** Set (or clear, with '') the table of one entity; columns that do not exist in the new table are dropped. */
export function setEntitySource(mapping: TyreMapping, entity: EntityName, source: string, tables: SourceTable[]): TyreMapping {
  const entities = { ...mapping.entities };
  if (!source) { delete entities[entity]; return { ...mapping, preset: null, entities }; }
  const columns = new Set(tables.find((table) => table.name === source)?.columns.map((column) => column.name) ?? []);
  const fields = Object.fromEntries(Object.entries(entities[entity]?.fields ?? {}).filter(([, column]) => columns.has(column)));
  entities[entity] = { source, fields };
  return { ...mapping, preset: null, entities };
}

export function setEntityField(mapping: TyreMapping, entity: EntityName, field: string, column: string): TyreMapping {
  const current = mapping.entities[entity];
  if (!current) return mapping;
  const fields = { ...current.fields };
  if (column) fields[field] = column; else delete fields[field];
  return { ...mapping, preset: null, entities: { ...mapping.entities, [entity]: { ...current, fields } } };
}

/** The same checks as the API's TyreMapping.problems, as message keys with values. */
export function mappingProblems(mapping: TyreMapping, fields: Record<EntityName, FieldSpec[]>): { key: MessageKey; vars?: Record<string, string> }[] {
  const problems: { key: MessageKey; vars?: Record<string, string> }[] = [];
  if (!mapping.entities.tyres) problems.push({ key: 'tyre.map.problemTyres' });
  for (const entity of ENTITIES) {
    const current = mapping.entities[entity];
    if (!current) continue;
    for (const spec of fields[entity] ?? []) {
      if (spec.required && !current.fields[spec.name]) problems.push({ key: 'tyre.map.problemRequired', vars: { entity: entityLabel(entity), field: spec.name } });
    }
    if (VEHICLE_LINK_ALLOWED.includes(entity)) {
      if (!current.fields.tyre_id && !current.fields.tyre_serial && !current.fields.vehicle_id) problems.push({ key: 'tyre.map.problemLinkVehicle', vars: { entity: entityLabel(entity) } });
    } else if (TYRE_LINKED.includes(entity) && !current.fields.tyre_id && !current.fields.tyre_serial) {
      problems.push({ key: 'tyre.map.problemLink', vars: { entity: entityLabel(entity) } });
    }
  }
  return problems;
}

/** Whether the dashboard has any fleet operations data to show (else its empty state). */
export function hasOperations(operations: OperationsSummary | undefined): boolean {
  if (!operations) return false;
  return operations.inspections_total + operations.checklists_total + operations.work_orders_synced
    + operations.schedules_overdue + operations.schedules_upcoming_14_days + operations.odometer_readings + operations.hour_readings > 0;
}

export function mappedCount(mapping: TyreMapping | null): number {
  return mapping ? Object.values(mapping.entities).reduce((sum, item) => sum + Object.keys(item?.fields ?? {}).length, 0) : 0;
}

// ---- wizard steps ----

export type StepState = 'done' | 'current' | 'todo';
export interface WizardStep { key: 'source' | 'test' | 'map' | 'sync'; state: StepState }

export function wizardSteps(setup: TyreSetup): WizardStep[] {
  const connected = Boolean(setup.connection);
  const tested = setup.connection?.status === 'ok';
  const mapped = Boolean(setup.mapping) && setup.mapping_problems.length === 0;
  const synced = Boolean(setup.sync);
  const done = [connected, tested, mapped, synced];
  const firstOpen = done.indexOf(false);
  return (['source', 'test', 'map', 'sync'] as const).map((key, index) => ({
    key, state: done[index] ? 'done' : index === firstOpen ? 'current' : 'todo',
  }));
}

// ---- assumptions and schedules ----

/** Parse "size|brand = price" lines; returns prices and the lines that could not be read. */
export function parsePrices(text: string): { prices: Record<string, number>; bad: string[] } {
  const prices: Record<string, number> = {};
  const bad: string[] = [];
  for (const raw of text.split('\n')) {
    const line = raw.trim();
    if (!line) continue;
    const match = /^(.+?)\s*[=:]\s*([0-9]+(?:\.[0-9]+)?)$/.exec(line);
    if (!match) { bad.push(line); continue; }
    prices[match[1].trim()] = Number(match[2]);
  }
  return { prices, bad };
}

export function formatPrices(prices: Record<string, number>): string {
  return Object.entries(prices).map(([key, price]) => `${key} = ${price}`).join('\n');
}

export function scheduleBody(draft: ScheduleDraft): Record<string, unknown> {
  return {
    kind: draft.kind, cadence: draft.cadence, weekday: draft.weekday, time: draft.time, timezone: draft.timezone,
    delivery: draft.channel && draft.to.trim() ? { channel: draft.channel, to: draft.to.trim(), format: draft.format } : null,
  };
}

export function isRecipient(value: string): boolean {
  const text = value.trim();
  return /^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$/.test(text) || /^\+?[0-9 ]{7,20}$/.test(text);
}

/** Query string for the forecasts list. */
export function forecastQuery(filters: { q?: string; month?: string; confidence?: string; state?: string; limit: number; offset: number }): string {
  const params = new URLSearchParams({ limit: String(filters.limit), offset: String(filters.offset) });
  if (filters.q?.trim()) params.set('q', filters.q.trim());
  if (filters.month) params.set('month', filters.month);
  if (filters.confidence) params.set('confidence', filters.confidence);
  if (filters.state) params.set('state', filters.state);
  return params.toString();
}

/** Alert ids not seen before (for desktop notifications), and the new "seen" list to keep. */
export function newAlerts(alerts: TyreAlert[], seen: string[]): { fresh: TyreAlert[]; seen: string[] } {
  const known = new Set(seen);
  const fresh = alerts.filter((alert) => alert.status === 'open' && alert.severity !== 'info' && !known.has(alert.id));
  return { fresh, seen: [...new Set([...seen, ...alerts.map((alert) => alert.id)])].slice(-500) };
}

/** Bars for "removals per month": share of the busiest month, at least 4% so a 1 is visible. */
export function monthBars(byMonth: Record<string, number>): { month: string; count: number; share: number }[] {
  const entries = Object.entries(byMonth).sort(([a], [b]) => a.localeCompare(b));
  const max = Math.max(1, ...entries.map(([, count]) => count));
  return entries.map(([month, count]) => ({ month, count, share: Math.max(4, Math.round((count / max) * 100)) }));
}
