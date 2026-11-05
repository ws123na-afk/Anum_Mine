// Unit tests for the vehicle helpers (src/lib/vehicles.ts, docs/vehicles.md#web).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { VehicleDueItem, VehiclePayment } from '@anum/contracts';
import { decimal, dueText, fromLocalInput, hashForTab, km, localInput, moneyList, newKey, paymentState, paymentText, tabFromHash, vehicleName } from '../src/lib/vehicles.ts';
import { guideFor } from '../src/lib/iphoneIntegrations.ts';
import { setLocale } from '../src/lib/i18n.ts';
import { vehiclesAr } from '../src/lib/messages/vehicles.ar.ts';
import { vehiclesEn } from '../src/lib/messages/vehicles.en.ts';
import { iphoneEn } from '../src/lib/messages/iphone.en.ts';
import { viewFromHash } from '../src/lib/shell.ts';

const payment = (extra: Partial<VehiclePayment> = {}): VehiclePayment => ({
  link_id: 'l1', canonical_id: 'c1', status: 'active', status_reason: null, amount: '150.00', currency: 'SAR', paid_by: 'u', recorded_as_cash: false,
  linked_at: '2026-11-01T10:00:00Z', ...extra,
});
const due = (extra: Partial<VehicleDueItem>): VehicleDueItem => ({
  vehicle_id: 'v', registration: 'ABC 1', kind: 'insurance', source_id: 'v', due_on: '2026-11-10', days_left: 6, due_km: null, km_left: null, ...extra,
});

describe('vehicle helpers', () => {
  test('tabs are deep-linkable and the shell opens Vehicles for them', () => {
    assert.equal(tabFromHash('#vehicles'), 'overview');
    assert.equal(tabFromHash('#vehicles-list'), 'list');
    assert.equal(tabFromHash('#vehicles-settings'), 'settings');
    assert.equal(tabFromHash('#vehicles-nope'), 'overview');
    assert.equal(hashForTab('overview'), '#vehicles');
    assert.equal(hashForTab('list'), '#vehicles-list');
    assert.equal(viewFromHash('#vehicles-settings'), 'vehicles');
    assert.equal(viewFromHash('#vehicles'), 'vehicles');
  });

  test('money is listed per currency, never added across currencies', () => {
    setLocale('en');
    assert.equal(moneyList([]), '-');
    const text = moneyList([{ currency: 'AED', amount: '90.00' }, { currency: 'SAR', amount: '100.00' }]);
    assert.match(text, /AED\s?90\.00 · SAR\s?100\.00/);
    assert.ok(!text.includes('190'));
  });

  test('a payment says what it is: linked, changed, undone or none', () => {
    setLocale('en');
    assert.equal(paymentState(null), 'none');
    assert.equal(paymentText(null), 'No payment linked');
    assert.match(paymentText(payment()), /SAR\s?150\.00/);
    assert.match(paymentText(payment({ status: 'needs_review' })), /check: the payment changed/);
    assert.equal(paymentText(payment({ status: 'void', status_reason: 'transaction_undone' })), 'The payment was undone in Finance');
  });

  test('due dates read as today, in N days, overdue or by kilometres', () => {
    setLocale('en');
    assert.equal(dueText(due({ days_left: 0 })), 'Due today');
    assert.equal(dueText(due({ days_left: 1 })), 'In 1 day');
    assert.equal(dueText(due({ days_left: -3 })), 'Overdue by 3 days');
    assert.equal(dueText(due({ days_left: null, km_left: '400', kind: 'service' })), 'In 400 km');
  });

  test('numbers typed with Arabic digits or commas are read, too many decimals refused', () => {
    assert.equal(decimal('٤٥٫٥'), '45.5');
    assert.equal(decimal('1,250.75'), '1250.75');
    assert.equal(decimal('12.345'), null);
    assert.equal(decimal('12.345', 3), '12.345');
    assert.equal(decimal('-3'), null);
    assert.equal(decimal('abc'), null);
    assert.equal(km(null), '-');
    assert.equal(km('12345.5'), '12,345.5 km');
  });

  test('local date inputs round trip and names fall back to the registration', () => {
    const at = new Date(2026, 10, 4, 9, 30);
    assert.equal(fromLocalInput(localInput(at)), at.toISOString());
    assert.equal(fromLocalInput(''), null);
    assert.equal(vehicleName({ nickname: null, make: 'Toyota', model: 'Hilux', year: 2022, registration: 'X 1' }), 'Toyota Hilux');
    assert.equal(vehicleName({ nickname: null, make: null, model: null, year: null, registration: 'X 1' }), 'X 1');
    assert.notEqual(newKey(), newKey());
  });

  test('the iPhone page has the driving session guide and never claims to read the odometer', () => {
    setLocale('en');
    const guide = guideFor('vehicle.driving');
    assert.ok(guide);
    const steps = guide.steps('https://api.example.test/api/v1/vehicles/device/sessions');
    assert.equal(steps.length, 6);
    assert.ok(steps[2].includes('https://api.example.test/api/v1/vehicles/device/sessions'));
    assert.ok(guide.body.some((line) => line.startsWith('odometer_km = Provided Input')));
    assert.match(iphoneEn['iph.int.vehicle.driving.purpose'], /never reads it/);
  });

  test('English and Arabic catalogs have the same keys and the same placeholders', () => {
    assert.deepEqual(Object.keys(vehiclesAr).sort(), Object.keys(vehiclesEn).sort());
    for (const [key, value] of Object.entries(vehiclesEn)) {
      const names = (text: unknown) => [...JSON.stringify(text).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).filter((n) => n !== 'count').sort();
      assert.deepEqual([...new Set(names((vehiclesAr as Record<string, unknown>)[key]))], [...new Set(names(value))], key);
    }
    for (const text of [...Object.values(vehiclesEn), ...Object.values(vehiclesAr)]) assert.ok(!/[–—]/.test(JSON.stringify(text)));
  });
});
