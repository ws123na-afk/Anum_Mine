// Commerce view helpers (src/lib/commerce.ts, docs/commerce.md). Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import {
  connectorBody, daysBetween, displayTitle, emptyConnectorForm, formatMoney, healthTone, niceMax, notificationFor, orderTone, pageLabel,
  parsePrice, parseQuantity, presetRange, salesChart,
} from '../src/lib/commerce.ts';
import { setLocale } from '../src/lib/i18n.ts';

afterEach(() => setLocale('en'));

describe('money and ranges', () => {
  test('money shows the currency code and two decimals, with Western digits in Arabic', () => {
    assert.equal(formatMoney(1234.5, 'SAR', 'en').replace(/\s/g, ' '), 'SAR 1,234.50');
    assert.match(formatMoney(1234.5, 'AED', 'ar'), /1,234\.50/);
    assert.equal(formatMoney(null, 'SAR'), '-');
  });

  test('presets are inclusive local days and every day of the range is charted', () => {
    const today = new Date(2026, 9, 11);
    assert.deepEqual(presetRange('today', today), { from: '2026-10-11', to: '2026-10-11' });
    assert.deepEqual(presetRange('7d', today), { from: '2026-10-05', to: '2026-10-11' });
    assert.equal(daysBetween('2026-09-29', '2026-10-02').length, 4);
    const chart = salesChart({
      start: '2026-10-09', end: '2026-10-11',
      by_day: [
        { date: '2026-10-09', channel: 'amazon', revenue: 900, orders: 1, units: 2 },
        { date: '2026-10-11', channel: 'noon', revenue: 300, orders: 1, units: 1 },
        { date: '2026-10-11', channel: 'amazon', revenue: 100, orders: 1, units: 1 },
      ],
    });
    assert.deepEqual(chart.channels, ['amazon', 'noon']);
    assert.deepEqual(chart.days.map((d) => d.total), [900, 0, 400]);
    assert.equal(chart.max, 900);
    assert.equal(niceMax(900), 1000);
    assert.equal(niceMax(0), 1);
  });
});

describe('connector form', () => {
  test('Amazon needs the seller id and all three LWA values', () => {
    const form = { ...emptyConnectorForm('amazon'), sellerId: 'aexampleseller1' };
    const missing = connectorBody(form);
    assert.equal(missing.ok, false);
    const ok = connectorBody({ ...form, lwaClientId: 'client', lwaClientSecret: 'secret', refreshToken: 'atzr-example', fetchBuyerDetails: true });
    assert.ok(ok.ok);
    if (ok.ok) {
      assert.equal(ok.value.channel, 'amazon');
      assert.deepEqual(ok.value.marketplaces, ['A17E79C6D8DWNP', 'A2VIGQ35RCS4UG']);
      assert.deepEqual(ok.value.settings, { seller_id: 'AEXAMPLESELLER1', fetch_buyer_details: true });
      assert.deepEqual(Object.keys(ok.value.credentials ?? {}).sort(), ['lwa_client_id', 'lwa_client_secret', 'refresh_token']);
    }
    assert.equal(connectorBody({ ...form, marketplaces: [] }).ok, false);
  });

  test('editing keeps saved credentials when fields are empty', () => {
    const result = connectorBody({ ...emptyConnectorForm('amazon'), sellerId: 'AEXAMPLESELLER1' }, true);
    assert.ok(result.ok);
    if (result.ok) {
      assert.equal(result.value.channel, undefined);
      assert.equal(result.value.credentials, undefined);
    }
  });

  test('own store modes ask for what each needs', () => {
    const store = { ...emptyConnectorForm('own_store'), baseUrl: 'https://store.example.test' };
    assert.equal(connectorBody(store).ok, false);
    const anum360 = connectorBody({ ...store, email: 'sync@store.example.test', password: 'example' });
    assert.ok(anum360.ok && anum360.value.settings?.mode === 'anum360' && anum360.value.settings?.currency === 'AED');
    const webhook = connectorBody({ ...emptyConnectorForm('own_store'), mode: 'webhook', currency: 'SAR' });
    assert.ok(webhook.ok && webhook.value.credentials === undefined && webhook.value.settings?.base_url === undefined);
    assert.equal(connectorBody({ ...store, mode: 'rest', baseUrl: 'store.example.test', apiKey: 'k' }).ok, false);
    assert.equal(connectorBody({ ...store, mode: 'rest', apiKey: 'k', currency: 'riyal' }).ok, false);
  });
});

describe('changes, tones and labels', () => {
  test('a new price or quantity is validated before anything is proposed', () => {
    assert.deepEqual(parsePrice('445'), { ok: true, value: 445 });
    assert.deepEqual(parsePrice('445,50'), { ok: true, value: 445.5 });
    assert.equal(parsePrice('0').ok, false);
    assert.equal(parsePrice('4.555').ok, false);
    assert.deepEqual(parseQuantity('12'), { ok: true, value: 12 });
    assert.equal(parseQuantity('-1').ok, false);
    assert.equal(parseQuantity('1.5').ok, false);
  });

  test('tones follow the state, late orders are always red', () => {
    assert.equal(healthTone('ok'), 'success');
    assert.equal(healthTone('auth_failed'), 'dangerTone');
    assert.equal(healthTone('throttled'), 'warning');
    assert.equal(orderTone('unshipped'), 'warning');
    assert.equal(orderTone('unshipped', true), 'dangerTone');
    assert.equal(orderTone('delivered'), 'success');
  });

  test('Arabic titles show in Arabic when there is one', () => {
    assert.equal(displayTitle({ title: 'Tote', title_ar: 'حقيبة' }, 'ar'), 'حقيبة');
    assert.equal(displayTitle({ title: 'Tote', title_ar: 'حقيبة' }, 'en'), 'Tote');
    assert.equal(displayTitle({ title: '', title_ar: 'حقيبة' }, 'en'), 'حقيبة');
  });

  test('a desktop notification needs both switches on', () => {
    const event = { type: 'commerce.alert.raised', payload: { title: 'Low stock: MX-1 on Amazon.sa', kind: 'low_stock' } };
    assert.deepEqual(notificationFor(event, { commerce_alerts: true, desktop_enabled: true }), { title: 'Store alert', body: 'Low stock: MX-1 on Amazon.sa' });
    assert.equal(notificationFor(event, { commerce_alerts: false, desktop_enabled: true }), null);
    assert.equal(notificationFor(event, { commerce_alerts: true, desktop_enabled: false }), null);
    assert.equal(notificationFor({ type: 'task.completed' }, { commerce_alerts: true }), null);
    assert.equal(notificationFor(event, null), null);
  });

  test('page labels', () => {
    assert.equal(pageLabel(20, 20, 132), '21 to 40 of 132');
    assert.equal(pageLabel(0, 0, 0), 'Nothing to show');
    setLocale('ar');
    assert.match(pageLabel(0, 5, 5), /1 إلى 5 من 5/);
  });
});
