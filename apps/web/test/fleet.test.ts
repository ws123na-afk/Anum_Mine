// Unit tests for the fleet helpers (src/lib/fleet.ts, docs/vehicles.md#fleet).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { FleetImportRow, FleetTotal, FleetVehicleRow } from '@anum/contracts';
import {
  TEMPLATE_HEADERS, fleetRows, importDecisions, importFileProblem, importFormatOf, importRowsShown, importTotals, mainTotal, percentChange, recordedShare, rowIssues, templateCsv,
} from '../src/lib/fleet.ts';
import { hashForTab, tabFromHash, VEHICLE_TABS } from '../src/lib/vehicles.ts';
import { setLocale } from '../src/lib/i18n.ts';
import { vehiclesAr } from '../src/lib/messages/vehicles.ar.ts';
import { vehiclesEn } from '../src/lib/messages/vehicles.en.ts';

const total = (currency: string, amount: string, extra: Partial<FleetTotal> = {}): FleetTotal => ({
  month: '2026-10', currency, fuel: '0.00', service: '0.00', tyres: '0.00', other: '0.00', total: amount, distance_km: '0.0', cost_per_km: null,
  previous_total: '0.00', linked: '0.00', recorded: amount, ...extra,
});
const vehicle = (registration: string, amount: string, perKm: string | null, extra: Partial<FleetVehicleRow> = {}): FleetVehicleRow => ({
  vehicle_id: registration, registration, nickname: null, make: null, model: null, currency: 'AED', archived: false, distance_km: '0.0', previous_distance_km: '0.0',
  fuel_litres: '0.000', litres_per_100km: null, costs: [{ currency: 'AED', fuel: amount, service: '0.00', tyres: '0.00', other: '0.00', total: amount, previous_total: '0.00', cost_per_km: perKm }],
  ...extra,
});
const row = (extra: Partial<FleetImportRow>): FleetImportRow => ({
  row: 2, registration: 'A 1', summary: {}, status: 'new', action: 'create', actions: ['create', 'skip'], vehicle_id: null, duplicate_of: null, issues: [], ...extra,
});

describe('fleet costs', () => {
  test('the main currency is the one with the most money; currencies are never added', () => {
    assert.equal(mainTotal([total('SAR', '10.00'), total('AED', '900.00')])?.currency, 'AED');
    assert.equal(mainTotal([]), null);
    assert.equal(recordedShare(total('AED', '200.00', { recorded: '50.00' })), 25);
    assert.equal(recordedShare(total('AED', '0.00')), 0);
  });
  test('vehicles are searched and sorted by cost, cost per km or registration', () => {
    const rows = [vehicle('B 2', '100.00', '0.500', { nickname: 'Mixer 7' }), vehicle('A 1', '300.00', '0.200'), vehicle('C 3', '0.00', null, { costs: [] })];
    assert.deepEqual(fleetRows(rows, '', 'total').map((r) => r.registration), ['A 1', 'B 2', 'C 3']);
    assert.deepEqual(fleetRows(rows, '', 'per_km').map((r) => r.registration), ['B 2', 'A 1', 'C 3']);
    assert.deepEqual(fleetRows(rows, '', 'registration').map((r) => r.registration), ['A 1', 'B 2', 'C 3']);
    assert.deepEqual(fleetRows(rows, 'mixer', 'total').map((r) => r.registration), ['B 2']);
  });
  test('the change from the month before', () => {
    assert.equal(percentChange('150', '100'), 50);
    assert.equal(percentChange('50', '200'), -75);
    assert.equal(percentChange('10', '0'), null);
  });
  test('Fleet costs and Import are tabs of their own', () => {
    assert.ok(VEHICLE_TABS.includes('costs') && VEHICLE_TABS.includes('import'));
    assert.equal(tabFromHash('#vehicles-costs'), 'costs');
    assert.equal(hashForTab('import'), '#vehicles-import');
  });
});

describe('fleet import', () => {
  test('only Excel and CSV files of a sendable size are accepted', () => {
    assert.equal(importFormatOf({ name: 'fleet.XLSX', type: '' }), 'xlsx');
    assert.equal(importFormatOf({ name: 'fuel.csv', type: '' }), 'csv');
    assert.equal(importFormatOf({ name: 'old.xls', type: '' }), null);
    setLocale('en');
    assert.match(importFileProblem({ name: 'old.xls', size: 10, type: '' }) ?? '', /Excel/);
    assert.match(importFileProblem({ name: 'f.csv', size: 0, type: '' }) ?? '', /empty/);
    assert.match(importFileProblem({ name: 'f.xlsx', size: 6_000_000, type: '' }) ?? '', /5 MB/);
    assert.equal(importFileProblem({ name: 'f.xlsx', size: 1000, type: '' }), null);
  });
  test('decisions keep the suggestion unless another allowed action was chosen, and add up', () => {
    const rows = [row({ row: 2 }), row({ row: 3, status: 'exists', action: 'skip', actions: ['skip', 'update'] }), row({ row: 4, status: 'invalid', action: 'skip', actions: ['skip'] })];
    const decisions = importDecisions(rows, { 3: 'update', 4: 'create' });
    assert.deepEqual(decisions, [{ row: 2, action: 'create' }, { row: 3, action: 'update' }, { row: 4, action: 'skip' }]);
    assert.deepEqual(importTotals(decisions), { create: 1, update: 1, skip: 1 });
  });
  test('rows are filtered by status and searched by registration or figures', () => {
    const rows = [row({ row: 2, registration: 'A 1', summary: { date: '2026-10-01' } }), row({ row: 3, registration: 'B 2', status: 'invalid' })];
    assert.deepEqual(importRowsShown(rows, 'invalid', '').map((r) => r.row), [3]);
    assert.deepEqual(importRowsShown(rows, '', '2026-10').map((r) => r.row), [2]);
  });
  test('row problems are worded in the reader\'s language, the server\'s sentence for anything else', () => {
    const problem = row({ status: 'invalid', issues: [{ code: 'number', field: 'litres', value: 'ten', text: 'x' }, { code: 'other', field: null, value: '', text: 'Server words.' }] });
    setLocale('en');
    assert.deepEqual(rowIssues(problem), ['Litres: "ten" is not a number.', 'Server words.']);
    setLocale('ar');
    assert.match(rowIssues(problem)[0], /^اللترات: ".?ten.?" ليس رقمًا\.$/u);  // the value is isolated for right-to-left text
    setLocale('en');
  });
  test('templates name the columns the server recognises', () => {
    assert.equal(templateCsv('odometer'), 'Registration,Date,Odometer\r\n');
    assert.ok(TEMPLATE_HEADERS.vehicles.includes('Insurance expiry'));
  });
  test('every fleet message has an Arabic text of its own', () => {
    const keys = Object.keys(vehiclesEn).filter((k) => k.startsWith('veh.fleet.') || k === 'veh.tab.costs' || k === 'veh.tab.import');
    assert.ok(keys.length > 150);
    for (const key of keys) {
      const ar = (vehiclesAr as Record<string, string>)[key];
      assert.ok(ar, key);
      assert.notEqual(ar, (vehiclesEn as Record<string, string>)[key], key);
    }
  });
});
