import type { Page } from '@playwright/test';

// Mocked /api/v1/tyre for e2e/tyre-planning.spec.ts (docs/tyre-planning.md): a synthetic,
// TyrePulse-shaped fleet. Later routes win in Playwright, so call it after mockWorkspaceApi.
const now = new Date().toISOString();
const today = now.slice(0, 10);
const month = today.slice(0, 7);
const fields = {
  tyres: [{ name: 'id', kind: 'text', required: true, description: 'Tyre record id' }, { name: 'serial', kind: 'text', required: false, description: 'Tyre serial number' }, { name: 'size', kind: 'text', required: false, description: 'Size' }],
  vehicles: [{ name: 'id', kind: 'text', required: true, description: 'Vehicle id' }],
  inspections: [{ name: 'tyre_id', kind: 'text', required: false, description: 'Tyre id' }, { name: 'tyre_serial', kind: 'text', required: false, description: 'Tyre serial' }, { name: 'inspected_at', kind: 'date', required: true, description: 'Inspection date' }, { name: 'tread_depth', kind: 'number', required: false, description: 'Tread' }],
  pressure_readings: [], removals: [], costs: [], suppliers: [],
};
const assumptions = { min_tread_mm: 3, warning_band_mm: 2, pressure_tolerance_pct: 10, default_target_pressure_psi: null, inspection_interval_days: 30, abnormal_wear_factor: 1.5, cost_anomaly_factor: 3, default_new_tread_mm: 16, horizon_months: 12, safety_stock_pct: 10, include_low_confidence: true, subtract_stock: true, unit_prices: {}, preferred_brands: {} };
const tables = [
  { name: 'tyre_records', columns: [{ name: 'id', type: 'uuid' }, { name: 'serial_no', type: 'text' }, { name: 'size', type: 'text' }], rows: 32, writable: false },
  { name: 'vehicle_fleet', columns: [{ name: 'asset_no', type: 'text' }], rows: 6, writable: false },
  { name: 'tyre_service_events', columns: [{ name: 'tyre_serial', type: 'text' }, { name: 'event_date', type: 'date' }, { name: 'tread_depth', type: 'numeric' }], rows: 98, writable: false },
];
const preset = { preset: 'tyrepulse', units: { tread: 'mm', pressure: 'psi', distance: 'km', currency: 'SAR' }, status_values: { active: 'fitted' }, entities: {
  tyres: { source: 'tyre_records', fields: { id: 'id', serial: 'serial_no', size: 'size' } },
  vehicles: { source: 'vehicle_fleet', fields: { id: 'asset_no' } },
  inspections: { source: 'tyre_service_events', fields: { tyre_serial: 'tyre_serial', inspected_at: 'event_date', tread_depth: 'tread_depth' } },
} };
const sync = { synced_at: now, synced_by: 'user_local', duration_ms: 420, entities: { tyres: { read: 32, kept: 32, skipped: 0, cut: false, issues: {} }, inspections: { read: 98, kept: 92, skipped: 6, cut: false, issues: { 'no tread or pressure reading': 6 } } }, counts: { tyres: 32, vehicles: 6, inspections: 92 } };
const forecast = (n: number, extra: Record<string, unknown> = {}) => ({
  tyre_id: `t${n}`, serial: `fake-sn-${String(n).padStart(4, '0')}`, brand: 'brandalpha', size: '315/80r22.5', vehicle_id: 'fake-truck-01', vehicle_label: 'fake-plate-01', position: `P${n % 4 + 1}`,
  state: 'projected', basis: 'time', confidence: 'medium', readings: 4, last_inspected_at: today, current_tread_mm: 6.8, wear_mm_per_1000km: null, wear_mm_per_30_days: 2.4,
  remaining_mm: 3.8, projected_removal: `${month}-28`, earliest_removal: `${month}-25`, latest_removal: `${month}-28`, note: '', ...extra,
});
const forecasts = Array.from({ length: 30 }, (_, i) => forecast(i + 1));
forecasts[0] = forecast(1, { state: 'due', current_tread_mm: 2.8, confidence: 'high' });
const alert = (id: string, kind: string, severity: string, label: string, message: string) => ({ id, key: `${kind}:${id}`, kind, severity, subject_type: 'tyre', subject_id: id, subject_label: label, message, value: {}, status: 'open', first_seen_at: now, last_seen_at: now, resolved_at: null, acknowledged_by: null, acknowledged_at: null });
const summary = { as_of: today, currency: 'SAR', vehicles: 6, tyres_fitted: 24, tyres_in_stock: 2, tyres_removed_last_30_days: 2, tyres_with_readings: 23, average_tread_mm: 10.75, below_minimum: 1, in_warning_band: 1, pressure_checked: 23, pressure_compliant_pct: 91.3, under_inflated: 1, over_inflated: 1, overdue_inspections: 1, due_next_30_days: 2, due_next_90_days: 2, cost_per_1000_km: 33.58, cost_per_hour: null, min_tread_mm: 3 };

export interface State { connected: boolean; saved?: Record<string, string>; mapped: boolean; synced: boolean; role: string; alerts: ReturnType<typeof alert>[]; reports: Record<string, unknown>[]; requests: string[] }

export async function mockTyre(page: Page, initial: Partial<State> = {}) {
  const state: State = { connected: true, mapped: true, synced: true, role: 'owner', alerts: [
    alert('a1', 'low_tread', 'critical', 'fake-sn-0103 on fake-plate-01 at P3', 'Tread 2.8 mm is at or below the 3.0 mm minimum: remove now.'),
    alert('a2', 'under_inflation', 'critical', 'fake-sn-0201 on fake-plate-02 at P1', '85 psi against a 110 psi target (-23%).'),
    alert('a3', 'abnormal_wear', 'warning', 'fake-sn-0101 on fake-plate-01 at P1', 'Wearing 2.40 mm per 30 days, 2.4x the median.'),
  ], reports: [], requests: [], ...initial };
  const setup = () => ({
    connection: state.connected ? (state.saved?.kind === 'supabase_auth'
      ? { kind: 'supabase_auth', label: state.saved.label, endpoint: state.saved.endpoint, schema_name: 'public', credential_configured: true, secret_hint: 'anum-reader@example.com · key …mple', status: 'ok', last_tested_at: now, last_error: null, updated_at: now, updated_by: 'user_local' }
      : { kind: 'supabase_postgres', label: 'TyrePulse', endpoint: 'reader@db.example.supabase.co:5432/postgres', schema_name: 'public', credential_configured: true, secret_hint: 'reader@db.example.supabase.co', status: 'ok', last_tested_at: now, last_error: null, updated_at: now, updated_by: 'user_local' }) : null,
    mapping: state.mapped ? preset : null, mapping_problems: [], assumptions,
    discovery: state.connected ? { tables, discovered_at: now, writable_tables: [] } : null,
    sync: state.synced ? sync : null, fields, presets: ['tyrepulse'],
    can_manage: state.role === 'owner', can_operate: state.role !== 'viewer', delivery_channels: [],
  });
  await page.route('http://localhost:8000/api/v1/tyre/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1/tyre', '');
    const method = request.method();
    state.requests.push(`${method} ${path}`);
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/setup') return json(setup());
    if (path === '/connection' && method === 'PUT') { state.connected = true; state.saved = request.postDataJSON() as Record<string, string>; return json(setup()); }
    if (path === '/connection/test') return json({ ok: true, message: state.saved?.kind === 'supabase_auth' ? 'Signed in as anum-reader@example.com (read-only). Rows visible in the first page: tyre_records 1.' : 'Connected as reader (read-only session: on).', setup: setup() });
    if (path === '/discover') return json({ tables, discovered_at: now, suggestion: preset, writable_tables: [] });
    if (path === '/presets/tyrepulse') return json(preset);
    if (path === '/mapping/preview') return json({ problems: [], stats: sync.entities, sample: {} });
    if (path === '/mapping') { state.mapped = true; return json(setup()); }
    if (path === '/sync') { state.synced = true; return json({ setup: setup(), new_alerts: 3 }); }
    if (path === '/dashboard') return json({ summary, synced_at: now, alert_counts: { critical: 2, warning: 1 }, top_alerts: state.alerts, due_soon: forecasts.slice(0, 5), due_by_month: { [month]: 2, '2027-04': 11 } });
    if (path === '/forecasts') {
      const q = url.searchParams.get('q') ?? '';
      const offset = Number(url.searchParams.get('offset') ?? 0), limit = Number(url.searchParams.get('limit') ?? 25);
      const items = forecasts.filter((item) => !q || item.serial.includes(q));
      return json({ items: items.slice(offset, offset + limit), total: items.length, limit, offset, min_tread_mm: 3, synced_at: now });
    }
    if (path === '/purchase-plan') return json({ as_of: today, currency: 'SAR', months: [month], rows: [{ month, size: '315/80r22.5', brand: 'brandalpha', due: 2, safety: 1, from_stock: 2, to_buy: 1, unit_price: 1200, price_source: 'data_brand_size', estimated_cost: 1200, low_confidence: 0 }], total_tyres: 1, total_cost: 1200, unpriced_rows: 0, skipped_no_size: 0, skipped_beyond_horizon: 0, assumptions });
    if (path === '/alerts') return json({ items: state.alerts, total: state.alerts.length, limit: 25, offset: 0, counts: { critical: 2, warning: 1 } });
    const ack = /^\/alerts\/(\w+)\/acknowledge$/.exec(path);
    if (ack) { const found = state.alerts.find((item) => item.id === ack[1])!; Object.assign(found, { status: 'acknowledged', acknowledged_by: 'user_local' }); return json(found); }
    if (path === '/reports' && method === 'POST') {
      const report = { id: `r${state.reports.length + 1}`, kind: request.postDataJSON().kind, title: 'Fleet tyre health', period_from: today, period_to: today, data_as_of: now, created_by: 'user_local', created_at: now, source: 'manual', schedule_id: null, deliveries: [], highlights: [{ label: 'Tyres fitted', value: 24 }, { label: 'At or below 3 mm', value: 1 }] };
      state.reports.unshift(report);
      return json({ ...report, content: { highlights: report.highlights, tables: [], notes: [], currency: 'SAR' } }, 201);
    }
    if (path === '/reports') return json({ items: state.reports, total: state.reports.length, limit: 10, offset: 0 });
    if (path === '/report-schedules') return json([]);
    return json({ error: { message: `unmocked ${path}` } }, 404);
  });
  return state;
}
