// System health (docs/system-health.md): problems first, the sentences under each check in both languages,
// the deep link and the notice kinds.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setLocale } from '../src/lib/i18n.ts';
import { KNOWN_KINDS, kindLabel, notificationHash } from '../src/lib/notificationCenter.ts';
import { viewFromHash } from '../src/lib/shell.ts';
import {
  HEALTH_KEYS, fixKey, formatAgeHours, formatGigabytes, formatUptime, healthLines, needsAttention, sortHealth, titleKey, whyKey,
  type HealthItem,
} from '../src/lib/systemHealth.ts';
import { systemHealthAr } from '../src/lib/messages/systemHealth.ar.ts';
import { systemHealthEn } from '../src/lib/messages/systemHealth.en.ts';

const item = (key: HealthItem['key'], state: HealthItem['state'], detail: Record<string, unknown> = {}): HealthItem => ({ key, state, detail });

test('problems come first, in the API order within each state, and unknown keys are dropped', () => {
  const items = [
    item('backup_local', 'green'), item('ai_model', 'red'), item('email', 'amber'), item('disk', 'unknown'),
    item('backup_offsite', 'red'), { key: 'later' as HealthItem['key'], state: 'red', detail: {} },
  ];
  assert.deepEqual(sortHealth(items).map((i) => i.key), ['ai_model', 'backup_offsite', 'email', 'disk', 'backup_local']);
  assert.equal(needsAttention(items.slice(0, 5)), 3);
});

test('numbers read as sentences, with Western digits in Arabic too', () => {
  setLocale('en');
  assert.equal(formatGigabytes(5 * 1024 ** 3), '5 GB');
  assert.equal(formatGigabytes(300 * 1024 ** 2), '300 MB');
  assert.equal(formatGigabytes(Number.NaN), '-');
  assert.equal(formatAgeHours(3), '3 hours');
  assert.equal(formatAgeHours(1), '1 hour');
  assert.equal(formatAgeHours(72), '3 days');
  assert.equal(formatUptime(90), '2 minutes');
  const offsite = healthLines(item('backup_offsite', 'red', { recorded: true, ever: false, last_ok: false, last_run_at: '2026-11-12T02:41:00Z', max_age_hours: 26 }));
  assert.equal(offsite.length, 3);
  assert.equal(offsite[0], 'No backup has succeeded yet.');
  assert.match(offsite[1], /^The last run, .+, failed\.$/);
  assert.equal(offsite[2], 'Expected at least every 26 hours.');
  assert.deepEqual(healthLines(item('ai_model', 'red', { provider: 'mock', placeholder: true, key_missing: false })), ['The server uses the mock model (ANUM_MODEL_PROVIDER=mock).']);
  assert.deepEqual(healthLines(item('disk', 'amber', { readable: true, free_bytes: 15 * 1024 ** 3, total_bytes: 100 * 1024 ** 3, free_pct: 15 })), ['15 GB free of 100 GB (15%).']);
  assert.deepEqual(healthLines(item('errors', 'green', { count_24h: 0 })), ['No errors in the last 24 hours.', 'Counted since the API started, so a restart sets it back to zero.']);
  assert.deepEqual(healthLines(item('backup_local', 'unknown', { recorded: false })), ['This server has no backup status folder (ANUM_BACKUP_STATUS_DIR), so it cannot know.']);
  setLocale('ar');
  assert.equal(formatAgeHours(5), '5 ساعات');
  assert.match(healthLines(item('disk', 'amber', { readable: true, free_bytes: 15 * 1024 ** 3, total_bytes: 100 * 1024 ** 3, free_pct: 15 }))[0], /^15 غيغابايت حرة من 100 غيغابايت/);
  setLocale('en');
});

test('every check has a title, a reason and a fix in English and Arabic, written for real', () => {
  for (const key of HEALTH_KEYS) {
    for (const messageKey of [titleKey(key), whyKey(key), fixKey(key)]) {
      const en = (systemHealthEn as Record<string, unknown>)[messageKey], ar = (systemHealthAr as Record<string, unknown>)[messageKey];
      assert.ok(typeof en === 'string' && en.length > 3, messageKey);
      assert.ok(typeof ar === 'string' && /[؀-ۿ]/.test(ar), messageKey);
    }
  }
  assert.deepEqual(Object.keys(systemHealthAr).sort(), Object.keys(systemHealthEn).sort());
});

test('the tab opens by link and the owner notices open it', () => {
  assert.equal(viewFromHash('#platformBilling-health'), 'platformBilling');
  for (const kind of ['system_health', 'system_restart']) {
    assert.ok((KNOWN_KINDS as readonly string[]).includes(kind));
    assert.notEqual(kindLabel(kind), 'Other');
    assert.equal(notificationHash({ kind, work_item_id: null }), '#platformBilling-health');
  }
});
