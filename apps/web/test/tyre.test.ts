import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { setLocale } from '../src/lib/i18n.ts';
import {
  ago, connectionBody, connectionDraft, emptyMapping, forecastQuery, isRecipient, mappingProblems, monthBars, newAlerts, pageRange, parsePrices,
  scheduleBody, setEntityField, setEntitySource, wizardSteps,
  type ConnectionView, type FieldSpec, type SourceTable, type TyreAlert, type TyreSetup,
} from '../src/lib/tyre.ts';

const fields: TyreSetup['fields'] = {
  tyres: [{ name: 'id', kind: 'text', required: true, description: 'id' }, { name: 'serial', kind: 'text', required: false, description: 'serial' }],
  vehicles: [{ name: 'id', kind: 'text', required: true, description: 'id' }],
  inspections: [
    { name: 'tyre_id', kind: 'text', required: false, description: '' }, { name: 'tyre_serial', kind: 'text', required: false, description: '' },
    { name: 'inspected_at', kind: 'date', required: true, description: '' },
  ],
  pressure_readings: [], removals: [], costs: [], suppliers: [],
} as Record<string, FieldSpec[]> as TyreSetup['fields'];

const tables: SourceTable[] = [
  { name: 'tyre_records', columns: [{ name: 'id', type: 'uuid' }, { name: 'serial_no', type: 'text' }], rows: 10, writable: false },
  { name: 'tyre_service_events', columns: [{ name: 'tyre_serial', type: 'text' }, { name: 'event_date', type: 'date' }], rows: 5, writable: false },
];

const saved: ConnectionView = {
  kind: 'supabase_rest', label: 'TyrePulse', endpoint: 'https://example.supabase.co', schema_name: 'public', credential_configured: true,
  secret_hint: '…-key', status: 'ok', last_tested_at: null, last_error: null, updated_at: '2026-10-01T00:00:00Z', updated_by: 'owner',
};

describe('tyre connection form', () => {
  test('a saved credential can be kept, a new kind needs one, and inputs are checked', () => {
    setLocale('en');
    const draft = connectionDraft(saved);
    assert.deepEqual(connectionBody(draft, saved), { ok: true, value: { kind: 'supabase_rest', label: 'TyrePulse', endpoint: 'https://example.supabase.co', schema_name: 'public' } });
    assert.deepEqual(connectionBody({ ...draft, kind: 'supabase_postgres' }, saved), { ok: false, error: 'tyre.connect.needSecret' });
    assert.deepEqual(connectionBody({ ...draft, endpoint: 'not a url' }, saved), { ok: false, error: 'tyre.connect.needUrl' });
    assert.deepEqual(connectionBody({ ...draft, kind: 'supabase_postgres', secret: 'mysql://x' }, null), { ok: false, error: 'tyre.connect.needPostgresUrl' });
    assert.deepEqual(connectionBody({ ...draft, kind: 'firestore', secret: '{"type":"user"}' }, null), { ok: false, error: 'tyre.connect.needServiceAccount' });
    const firestore = connectionBody({ ...draft, kind: 'firestore', endpoint: '', secret: '{"type":"service_account"}' }, null);
    assert.ok(firestore.ok && firestore.value.endpoint === '(default)' && firestore.value.secret);
    assert.deepEqual(connectionBody({ ...draft, schema_name: 'Bad-Name' }, saved), { ok: false, error: 'tyre.connect.badSchema' });
  });
});

describe('tyre mapping editor', () => {
  test('choosing tables and columns, and the same problems as the API', () => {
    let mapping = emptyMapping();
    assert.deepEqual(mappingProblems(mapping, fields).map((p) => p.key), ['tyre.map.problemTyres']);
    mapping = setEntitySource(mapping, 'tyres', 'tyre_records', tables);
    mapping = setEntityField(mapping, 'tyres', 'id', 'id');
    mapping = setEntityField(mapping, 'tyres', 'serial', 'serial_no');
    mapping = setEntitySource(mapping, 'inspections', 'tyre_service_events', tables);
    assert.deepEqual(mappingProblems(mapping, fields).map((p) => p.key), ['tyre.map.problemRequired', 'tyre.map.problemLink']);
    mapping = setEntityField(mapping, 'inspections', 'inspected_at', 'event_date');
    mapping = setEntityField(mapping, 'inspections', 'tyre_serial', 'tyre_serial');
    assert.deepEqual(mappingProblems(mapping, fields), []);
    // Switching a table drops the columns it does not have; clearing a column removes it.
    const moved = setEntitySource(mapping, 'tyres', 'tyre_service_events', tables);
    assert.deepEqual(moved.entities.tyres?.fields, {});
    assert.equal(setEntityField(mapping, 'tyres', 'serial', '').entities.tyres?.fields.serial, undefined);
    assert.equal(setEntitySource(mapping, 'inspections', '', tables).entities.inspections, undefined);
  });

  test('wizard steps follow the setup', () => {
    const base = { connection: null, mapping: null, mapping_problems: [], sync: null } as unknown as TyreSetup;
    assert.deepEqual(wizardSteps(base).map((s) => s.state), ['current', 'todo', 'todo', 'todo']);
    const tested = { ...base, connection: saved, mapping: emptyMapping(), mapping_problems: ['x'] } as TyreSetup;
    assert.deepEqual(wizardSteps(tested).map((s) => s.state), ['done', 'done', 'current', 'todo']);
  });
});

describe('tyre helpers', () => {
  test('prices, recipients, schedules and queries', () => {
    assert.deepEqual(parsePrices('315/80r22.5|brandalpha = 1200\n385/65r22.5: 1500.5\n\nnonsense'), {
      prices: { '315/80r22.5|brandalpha': 1200, '385/65r22.5': 1500.5 }, bad: ['nonsense'],
    });
    assert.ok(isRecipient('fleet@example.com') && isRecipient('+966 500 000 000'));
    assert.ok(!isRecipient('a@b') && !isRecipient('one@example.com, two@example.com'));
    assert.deepEqual(scheduleBody({ kind: 'fleet_health', cadence: 'weekly', weekday: 0, time: '07:00', timezone: 'Asia/Riyadh', channel: '', to: '', format: 'pdf' }).delivery, null);
    assert.deepEqual(scheduleBody({ kind: 'fleet_health', cadence: 'daily', weekday: 0, time: '07:00', timezone: 'UTC', channel: 'email', to: ' x@example.com ', format: 'csv' }).delivery, { channel: 'email', to: 'x@example.com', format: 'csv' });
    assert.equal(forecastQuery({ q: ' sn ', month: '2026-10', confidence: '', state: 'due', limit: 25, offset: 50 }), 'limit=25&offset=50&q=sn&month=2026-10&state=due');
  });

  test('pages, ages, bars and new alerts', () => {
    setLocale('en');
    assert.equal(pageRange({ items: [1, 2], total: 30, limit: 2, offset: 4 }), 'Showing 5-6 of 30');
    assert.equal(pageRange({ items: [], total: 0, limit: 2, offset: 0 }), 'Nothing to show');
    assert.equal(ago('2026-10-01T00:00:00Z', new Date('2026-10-04T12:00:00Z')), '3 days ago');
    assert.equal(ago('2026-10-04T00:00:00Z', new Date('2026-10-04T12:00:00Z')), 'today');
    assert.deepEqual(monthBars({ '2026-11': 1, '2026-10': 20 }), [{ month: '2026-10', count: 20, share: 100 }, { month: '2026-11', count: 1, share: 5 }]);
    const alert = (id: string, severity: TyreAlert['severity'], status: TyreAlert['status'] = 'open') => ({ id, severity, status }) as TyreAlert;
    const result = newAlerts([alert('a', 'critical'), alert('b', 'info'), alert('c', 'warning', 'acknowledged'), alert('d', 'warning')], ['d']);
    assert.deepEqual(result.fresh.map((item) => item.id), ['a']);
    assert.deepEqual(result.seen.sort(), ['a', 'b', 'c', 'd']);
    setLocale('ar');
    assert.equal(ago('2026-10-01T00:00:00Z', new Date('2026-10-04T12:00:00Z')), 'منذ 3 أيام');
    setLocale('en');
  });
});
