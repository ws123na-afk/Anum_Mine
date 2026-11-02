// Live eligibility check helpers (src/lib/financePreflight.ts, docs/finance.md#live-eligibility-preflight).
// Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import type { FinancePreflight } from '@anum/contracts';
import {
  ASSETS, BROKERS, PREFLIGHT_GROUP_NAMES, PREFLIGHT_GROUPS, emptyPreflightDraft, missingCount, missingIn, preflightRequest, safeSource,
} from '../src/lib/financePreflight.ts';
import { setLocale, t, type MessageKey } from '../src/lib/i18n.ts';
import { financeTradingAr } from '../src/lib/messages/financeTrading.ar.ts';
import { financeTradingEn } from '../src/lib/messages/financeTrading.en.ts';

afterEach(() => setLocale('en'));

const result = (over: Partial<FinancePreflight> = {}): FinancePreflight => ({
  stage: 'not_connected', missing_data_requirements: ['regulated_provider_verified'], missing_research_requirements: [],
  missing_live_review_requirements: ['written_live_approval', 'emergency_stop_verified'], system_preconditions: [], notes: [],
  broker: null, assets: [], eligibility_confirmed: false, live_trading_enabled: false, credentials_connected: false,
  deposits_into_anum_supported: false, orders_sent: 0, explanation: 'x', ...over,
});

describe('live eligibility check request', () => {
  test('no broker sends the checklist alone, only ticked facts', () => {
    const draft = emptyPreflightDraft();
    draft.facts = { regulated_provider_verified: true, paper_testing_passed: false };
    assert.deepEqual(preflightRequest(draft), { ok: true, value: { facts: { regulated_provider_verified: true }, profile: null } });
  });

  test('a broker needs a two-letter country and at least one asset, sent in a fixed order', () => {
    const draft = { ...emptyPreflightDraft(), broker: 'alpaca' as const, country: 'sa', assets: ['crypto', 'stock'] as ('stock' | 'crypto')[] };
    assert.deepEqual(preflightRequest(draft), { ok: true, value: { facts: {}, profile: { broker: 'alpaca', country_of_residence: 'SA', assets: ['stock', 'crypto'] } } });
    const noCountry = preflightRequest({ ...draft, country: 'S' });
    assert.equal(noCountry.ok, false);
    assert.equal(!noCountry.ok && noCountry.error, t('finance.preflight.error.country'));
    const noAssets = preflightRequest({ ...draft, assets: [] });
    assert.equal(!noAssets.ok && noAssets.error, t('finance.preflight.error.assets'));
  });

  test('read-only access before the broker is checked is refused before sending, as the server does', () => {
    const draft = { ...emptyPreflightDraft(), facts: { read_only_access_verified: true } };
    const checked = preflightRequest(draft);
    assert.equal(!checked.ok && checked.error, t('finance.preflight.error.order'));
  });

  test('the groups list every fact once, matching the server', () => {
    const all = PREFLIGHT_GROUP_NAMES.flatMap((g) => [...PREFLIGHT_GROUPS[g]]);
    assert.equal(new Set(all).size, all.length);
    assert.equal(all.length, 18);
  });
});

describe('live eligibility check answer', () => {
  test('missing items are counted and read per group', () => {
    const answer = result();
    assert.equal(missingCount(answer), 3);
    assert.deepEqual(missingIn(answer, 'live_review'), ['written_live_approval', 'emergency_stop_verified']);
    assert.deepEqual(missingIn(answer, 'research'), []);
  });

  test('only https sources become links', () => {
    assert.equal(safeSource('https://cma.gov.sa/en/x.aspx'), 'https://cma.gov.sa/en/x.aspx');
    assert.equal(safeSource('javascript:alert(1)'), null);
    assert.equal(safeSource('http://example.org'), null);
  });
});

describe('live eligibility check words', () => {
  test('every label exists in English and Arabic', () => {
    const keys: string[] = ['finance.preflight.title', 'finance.preflight.note.sa_crypto_not_licensed'];
    for (const g of PREFLIGHT_GROUP_NAMES) { keys.push(`finance.preflight.group.${g}`); for (const f of PREFLIGHT_GROUPS[g]) keys.push(`finance.preflight.fact.${f}`); }
    for (const b of BROKERS) keys.push(`finance.preflight.broker.${b}`);
    for (const a of ASSETS) keys.push(`finance.preflight.asset.${a}`);
    for (const s of ['not_connected', 'paper_only', 'read_only', 'live_blocked']) keys.push(`finance.preflight.stage.${s}`);
    for (const key of keys) {
      assert.ok(key in financeTradingEn, key);
      assert.ok(key in financeTradingAr, key);
    }
  });

  test('the count reads in both languages', () => {
    assert.equal(t('finance.preflight.missing' as MessageKey, { count: 1 }), '1 item still missing on your list');
    setLocale('ar');
    assert.equal(t('finance.preflight.missing' as MessageKey, { count: 2 }), 'ينقص بندان في قائمتك');
  });
});
