// Mocked /api/v1/vehicles answers (docs/vehicles.md) shared by vehicles.spec.ts and the workspace mock (accessibility,
// layout and journey gates). Synthetic values only: example registrations, round figures, no real VIN.
const now = new Date();
const stamp = now.toISOString();
const day = (offset: number) => new Date(now.getTime() + offset * 86_400_000).toISOString().slice(0, 10);
const ago = (hours: number) => new Date(now.getTime() - hours * 3_600_000).toISOString();

type Row = Record<string, unknown>;
export interface VehiclesState {
  vehicles: Row[];
  fuel: Row[];
  trips: Row[];
  service: Row[];
  readings: Row[];
  preferences: Row;
  candidates: Row[];
  accounts: Row[];
  denied: boolean;
  failOverview: boolean;
  links: number;
  cashTransactions: Set<string>;
}

const vehicle = (id: string, registration: string, extra: Row = {}): Row => ({
  id, owner_user_id: 'user_local', ownership: 'personal', registration, vin: '•••••••••••••0001', vin_masked: true, nickname: null, make: 'Toyota', model: 'Camry',
  year: 2022, vehicle_type: 'car', country: 'SA', currency: 'SAR', fuel_type: 'petrol_91', tank_litres: '60.00', acquisition_date: day(-400), purchase_price: '120000.00',
  residual_value: '30000.00', useful_life_months: 60, purchase_payment: null, insurance_expires_on: day(10), registration_expires_on: day(200), inspection_expires_on: null,
  notes: null, archived_at: null, version: 1, created_at: stamp, updated_at: stamp, can_edit: true, mine: true, odometer_km: '10400.0',
  due: [{ vehicle_id: id, registration, kind: 'insurance', source_id: id, due_on: day(10), days_left: 10, due_km: null, km_left: null }], ...extra,
});
const child = (id: string, vehicleId: string, extra: Row): Row => ({
  id, owner_user_id: 'user_local', vehicle_id: vehicleId, visibility: 'personal', vehicle_owner_id: 'user_local', version: 1, created_at: stamp, updated_at: stamp,
  can_edit: true, mine: true, ...extra,
});
const payment = (amount: string): Row => ({ link_id: 'fin_link_1', canonical_id: 'fin_je_fuel', status: 'active', status_reason: null, amount, currency: 'SAR',
  paid_by: 'user_local', recorded_as_cash: false, linked_at: stamp });

export function vehiclesState({ empty = false, denied = false, failOverview = false, many = false }: { empty?: boolean; denied?: boolean; failOverview?: boolean; many?: boolean } = {}): VehiclesState {
  const vehicles = empty ? [] : [vehicle('veh_car', 'ABC 1234'), vehicle('veh_van', 'VAN 77', { ownership: 'business', make: 'Ford', model: 'Transit', vehicle_type: 'van', fuel_type: 'diesel', due: [], can_edit: false, mine: false, owner_user_id: 'user_manager' })];
  if (many) for (let i = 0; i < 23; i += 1) vehicles.push(vehicle(`veh_${i}`, `FLT ${100 + i}`, { due: [] }));
  return {
    vehicles,
    fuel: empty ? [] : [
      child('vfuel_2', 'veh_car', { filled_at: ago(30), station: 'Example Station North', fuel_type: 'petrol_91', litres: '40.000', price_per_litre: '2.330', total: '93.20', currency: 'SAR',
        odometer_km: '10400.0', full_tank: true, payment_method: 'apple_pay', payment: null, document_id: null, notes: null }),
      child('vfuel_1', 'veh_car', { filled_at: ago(200), station: 'Example Station South', fuel_type: 'petrol_91', litres: '42.000', price_per_litre: '2.330', total: '97.86', currency: 'SAR',
        odometer_km: '10000.0', full_tank: true, payment_method: 'card', payment: payment('97.86'), document_id: null, notes: null }),
    ],
    trips: empty ? [] : [child('vtrip_1', 'veh_car', { status: 'closed', started_at: ago(50), ended_at: ago(49), start_odometer_km: '10300.0', end_odometer_km: '10342.5', distance_km: '42.5',
      distance_basis: 'typed', purpose: 'business', purpose_note: 'Site visit', project_id: null, origin: null, destination: null, notes: null, source: 'device', device_id: null })],
    service: empty ? [] : [child('vsvc_1', 'veh_car', { kind: 'oil', service_on: day(-20), odometer_km: '10100.0', workshop: 'Example Lube', description: null, reference: null,
      next_due_on: null, next_due_km: '15100.0', warranty_until: null, trip_id: null, receipt_total: '180.00', currency: 'SAR', payment_method: 'cash', payment: null, document_id: null })],
    readings: empty ? [] : [
      child('vodo_3', 'veh_car', { reading_km: '10400.0', read_at: ago(30), source: 'fuel', source_id: 'vfuel_2', correction: false, reason: null }),
      child('vodo_2', 'veh_car', { reading_km: '10100.0', read_at: ago(480), source: 'service', source_id: 'vsvc_1', correction: false, reason: null }),
      child('vodo_1', 'veh_car', { reading_km: '10000.0', read_at: ago(500), source: 'manual', source_id: null, correction: false, reason: null }),
    ],
    preferences: { owner_user_id: 'user_local', trip_places: false, default_vehicle_id: null, reminder_days: 30, reminders: true, version: 0, updated_at: null },
    candidates: [
      { canonical_id: 'fin_je_fuel2', posted_on: day(-1), merchant: 'EXAMPLE FUEL NORTH', category: 'fuel', amount: '93.20', currency: 'SAR', remaining: '93.20', linked_to_vehicle: false },
      { canonical_id: 'fin_je_shop', posted_on: day(-3), merchant: 'EXAMPLE MARKET', category: 'groceries', amount: '45.00', currency: 'SAR', remaining: '45.00', linked_to_vehicle: false },
    ],
    accounts: [{ id: 'fin_acc_cash', name: 'Wallet', kind: 'cash', currency: 'SAR' }],
    denied, failOverview, links: 1, cashTransactions: new Set(),
  };
}

function report(state: VehiclesState, vehicleId: string): Row {
  const spend = state.fuel.concat(state.service).filter((r) => r.vehicle_id === vehicleId && (r.payment as Row | null)?.status === 'active')
    .reduce((sum, r) => sum + Number((r.payment as Row).amount), 0);
  return {
    vehicle_id: vehicleId, start: null, end: null, odometer_km: '10400.0', distance_km: '400.0', spend: spend ? [{ currency: 'SAR', amount: spend.toFixed(2) }] : [],
    cost_per_km: spend ? [{ currency: 'SAR', amount: (spend / 400).toFixed(3) }] : [], monthly: spend ? [{ month: day(0).slice(0, 7), currency: 'SAR', amount: spend.toFixed(2) }] : [],
    by_category: spend ? [{ currency: 'SAR', category: 'fuel', amount: spend.toFixed(2) }] : [], fuel_litres: '82.000',
    usage: [{ month: day(-31).slice(0, 7), distance_km: '100.0', fuel_litres: '42.000' }, { month: day(0).slice(0, 7), distance_km: '300.0', fuel_litres: '40.000' }],
    fuel_price_trend: [{ month: day(0).slice(0, 7), currency: 'SAR', fuel_type: 'petrol_91', price_per_litre: '2.330' }],
    consumption: { average_per_100: '10.00', segments: [{ start_id: 'vfuel_1', end_id: 'vfuel_2', km: '400.0', litres: '40.000', per_100: '10.00', ended_on: day(-1) }] },
    anomalies: [], unlinked_records: state.fuel.concat(state.service).filter((r) => r.vehicle_id === vehicleId && !r.payment).length,
    ownership_cost: { operating: spend ? [{ currency: 'SAR', amount: spend.toFixed(2) }] : [],
      depreciation: { currency: 'SAR', purchase_price: '120000.00', residual_value: '30000.00', monthly: '1500.00', months: 13, accumulated: '19500.00', book_value: '100500.00' },
      capital_paid: null },
  };
}

const refusal = (status: number, detail: string, code: string) => ({ status, body: { detail, code } });

export function vehiclesReply(state: VehiclesState, method: string, url: URL, body: unknown): { status: number; body: unknown } | null {
  const path = url.pathname;
  if (path === '/api/v1/finance/accounts' && method === 'GET') return { status: 200, body: state.accounts };
  if (!path.startsWith('/api/v1/vehicles')) return null;
  if (state.denied) return { status: 403, body: { error: { code: 'forbidden', message: 'Vehicles are for workspace owners and members. Ask a workspace owner to change your role from viewer to member.', details: [] } } };
  const sub = path.slice('/api/v1/vehicles'.length);
  const b = (body ?? {}) as Row;
  const page = (rows: Row[]) => {
    const q = (url.searchParams.get('q') ?? '').toLowerCase();
    const offset = Number(url.searchParams.get('offset') ?? 0), limit = Number(url.searchParams.get('limit') ?? 20);
    const items = rows.filter((r) => !q || JSON.stringify(r).toLowerCase().includes(q));
    return { status: 200, body: { items: items.slice(offset, offset + limit), total: items.length, limit, offset } };
  };
  if (method === 'GET' && sub === '') return page(state.vehicles.filter((v) => (url.searchParams.get('archived') === 'true') === Boolean(v.archived_at)));
  if (method === 'POST' && sub === '') {
    if (state.vehicles.some((v) => String(v.registration).toUpperCase() === String(b.registration).toUpperCase())) return refusal(409, 'A vehicle with this registration is already in this workspace.', 'duplicate_registration');
    const created = vehicle(`veh_new_${state.vehicles.length}`, String(b.registration).toUpperCase(), { ...b, vin: b.vin ?? null, vin_masked: false, odometer_km: b.odometer_km ?? null, due: [] });
    state.vehicles.unshift(created);
    return { status: 201, body: created };
  }
  if (method === 'GET' && sub === '/overview') {
    if (state.failOverview) return { status: 503, body: { error: { code: 'unavailable', message: 'The database is not answering.', details: [] } } };
    const active = state.vehicles.filter((v) => !v.archived_at);
    const spend = state.fuel.concat(state.service).filter((r) => (r.payment as Row | null)?.status === 'active').reduce((s, r) => s + Number((r.payment as Row).amount), 0);
    return { status: 200, body: { start: null, end: null, vehicles: active.length, spend: spend ? [{ currency: 'SAR', amount: spend.toFixed(2) }] : [],
      monthly: spend ? [{ month: day(0).slice(0, 7), currency: 'SAR', amount: spend.toFixed(2) }] : [],
      by_vehicle: spend ? [{ vehicle_id: 'veh_car', registration: 'ABC 1234', currency: 'SAR', amount: spend.toFixed(2) }] : [],
      due: active.flatMap((v) => v.due as Row[]), unlinked_records: state.fuel.concat(state.service).filter((r) => !r.payment).length,
      usage: active.length ? [{ month: day(-31).slice(0, 7), distance_km: '100.0', fuel_litres: '42.000' }, { month: day(0).slice(0, 7), distance_km: '300.0', fuel_litres: '40.000' }] : [],
      by_vehicle_usage: active.map((v) => ({ vehicle_id: v.id, distance_km: v.id === 'veh_car' ? '300.0' : '0.0', fuel_litres: v.id === 'veh_car' ? '40.000' : '0.000',
        average_per_100: v.id === 'veh_car' ? '10.00' : null })) } };
  }
  if (method === 'GET' && sub === '/reminders') return { status: 200, body: { items: state.vehicles.flatMap((v) => v.due as Row[]), window_days: 30 } };
  if (method === 'GET' && sub === '/preferences') return { status: 200, body: state.preferences };
  if (method === 'PUT' && sub === '/preferences') {
    state.preferences = { ...state.preferences, ...b, version: Number(b.version) + 1, updated_at: stamp };
    return { status: 200, body: state.preferences };
  }
  if (method === 'GET' && sub === '/payments') return { status: 200, body: { items: state.candidates } };
  const cost = sub.match(/^\/costs\/(fuel_entry|service_record|purchase)\/([^/]+)\/(payment|cash)$/);
  if (cost) {
    const [, kind, id, action] = cost;
    const rows = kind === 'fuel_entry' ? state.fuel : kind === 'service_record' ? state.service : state.vehicles;
    const row = rows.find((r) => r.id === id);
    if (!row) return refusal(404, 'Record not found.', 'not_found');
    const field = kind === 'purchase' ? 'purchase_payment' : 'payment';
    if (method === 'DELETE') { row[field] = null; return { status: 204, body: null }; }
    const current = row[field] as Row | null;
    if (action === 'cash') {
      if (current && current.recorded_as_cash) return { status: 200, body: { payment: current, replayed: true } };
      state.cashTransactions.add(id);
      row[field] = { ...payment(String(b.amount)), link_id: `fin_link_${++state.links}`, canonical_id: `fin_je_cash_${id}`, recorded_as_cash: true };
      return { status: 201, body: { payment: row[field], replayed: false } };
    }
    if (current && current.canonical_id === b.canonical_id) return { status: 200, body: { payment: current, replayed: true } };
    const candidate = state.candidates.find((c) => c.canonical_id === b.canonical_id);
    if (!candidate) return refusal(404, 'Payment not found in your Finance ledger.', 'transaction_not_found');
    row[field] = { ...payment(String(candidate.remaining)), link_id: `fin_link_${++state.links}`, canonical_id: candidate.canonical_id };
    candidate.remaining = '0.00';
    return { status: 201, body: { payment: row[field], replayed: false } };
  }
  const recordEdit = sub.match(/^\/(fuel|trips|service)\/([^/]+)$/);
  if (recordEdit) {
    const [, kind, id] = recordEdit;
    const rows = state[kind as 'fuel' | 'trips' | 'service'];
    const found = rows.find((r) => r.id === id);
    if (!found) return refusal(404, 'Record not found.', 'not_found');
    if (method === 'DELETE') { state[kind as 'fuel' | 'trips' | 'service'] = rows.filter((r) => r.id !== id); return { status: 204, body: null }; }
    Object.assign(found, b, { version: Number(found.version) + 1 });
    return { status: 200, body: found };
  }
  const nested = sub.match(/^\/([^/]+)(?:\/(report|odometer|fuel|trips|service|archive|export\.csv))?$/);
  if (!nested) return refusal(404, 'Not found.', 'not_found');
  const [, vehicleId, part] = nested;
  const v = state.vehicles.find((x) => x.id === vehicleId);
  if (!v) return refusal(404, "Vehicle not found. It may have been deleted, or it is someone's personal vehicle.", 'not_found');
  if (!part && method === 'GET') return { status: 200, body: v };
  if (!part && method === 'PATCH') { Object.assign(v, b, { version: Number(v.version) + 1 }); return { status: 200, body: v }; }
  if (!part && method === 'DELETE') { state.vehicles = state.vehicles.filter((x) => x.id !== vehicleId); return { status: 204, body: null }; }
  if (part === 'archive') { v.archived_at = b.archived ? stamp : null; v.version = Number(v.version) + 1; return { status: 200, body: v }; }
  if (part === 'report') return { status: 200, body: report(state, vehicleId) };
  if (part === 'odometer' && method === 'GET') return page(state.readings.filter((r) => r.vehicle_id === vehicleId));
  if (part === 'odometer' && method === 'POST') {
    const latest = state.readings.find((r) => r.vehicle_id === vehicleId);
    if (!b.correction && latest && Number(b.reading_km) < Number(latest.reading_km)) {
      return { status: 409, body: { detail: `The odometer cannot go down: ${latest.reading_km} km was recorded on ${String(latest.read_at).slice(0, 10)}. If the odometer was replaced or a reading was wrong, add a correction with a reason.`, code: 'odometer_decrease' } };
    }
    const created = child(`vodo_${state.readings.length + 10}`, vehicleId, { reading_km: `${Number(b.reading_km).toFixed(1)}`, read_at: stamp, source: b.correction ? 'correction' : 'manual', source_id: null, correction: Boolean(b.correction), reason: b.reason ?? null });
    state.readings.unshift(created);
    v.odometer_km = created.reading_km;
    return { status: 201, body: created };
  }
  if (part && ['fuel', 'trips', 'service'].includes(part)) {
    const kind = part as 'fuel' | 'trips' | 'service';
    if (method === 'GET') return page(state[kind].filter((r) => r.vehicle_id === vehicleId));
    const created = child(`v${kind}_${state[kind].length + 10}`, vehicleId, { payment: null, currency: 'SAR', document_id: null, ...b, ...(kind === 'trips' ? { status: b.ended_at ? 'closed' : 'open', source: 'web', distance_km: null, distance_basis: null } : {}) });
    state[kind].unshift(created);
    return { status: 201, body: { ...created, replayed: false } };
  }
  return refusal(404, 'Not found.', 'not_found');
}
