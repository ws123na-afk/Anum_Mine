// Unit tests for the Prospecting and Outreach helpers (src/lib/businessPipeline.ts, docs/business.md#prospecting-pipeline).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { BusinessSignal } from '@anum/contracts';
import {
  estimateLine, fitText, isActive, outboxStatusLabel, outboxTone, rateText, runStatusLabel, runTone, signalGroups, targetBody, targetDraft, wordsOf,
} from '../src/lib/businessPipeline.ts';
import { TABS, tabFromHash } from '../src/lib/business.ts';
import { setLocale } from '../src/lib/i18n.ts';
import { businessPipelineAr } from '../src/lib/messages/businessPipeline.ar.ts';
import { businessPipelineEn } from '../src/lib/messages/businessPipeline.en.ts';

const signal = (code: string, category: BusinessSignal['category']): BusinessSignal => ({
  code, label: code, description: '', category, value_type: 'bool', cost_note: '', criterion: null, status: 'proposed', decided_by: null, decided_at: null,
});

describe('business prospecting and outreach', () => {
  test('the new tabs open from their hashes', () => {
    assert.ok(TABS.includes('prospecting') && TABS.includes('outreach'));
    assert.equal(tabFromHash('#business-prospecting'), 'prospecting');
    assert.equal(tabFromHash('#business-outreach'), 'outreach');
  });

  test('words are split on commas and lines, trimmed, deduplicated and capped', () => {
    assert.deepEqual(wordsOf('fleet, Fleet ,\nlogistics,, concrete'), ['fleet', 'logistics', 'concrete']);
    assert.deepEqual(wordsOf('a,b,c,d', 2), ['a', 'b']);
    assert.deepEqual(wordsOf('   '), []);
  });

  test('a target profile round-trips through the form and drops unknown sizes', () => {
    const profile = { industries: ['Fleet'], company_sizes: ['medium' as const], regions: ['Saudi Arabia', 'UAE'], roles: ['Fleet manager'], keywords: ['tyre'],
      disqualifiers: ['government'], value_proposition: 'Track every tyre.', value_proposition_ar: 'تتبع كل إطار.' };
    const draft = targetDraft(profile);
    assert.equal(draft.regions, 'Saudi Arabia, UAE');
    assert.deepEqual(targetBody({ ...draft, sizes: [...draft.sizes, 'huge'], pitch: '  Track every tyre.  ' }), profile);
  });

  test('signals group in catalog order and skip empty groups', () => {
    const groups = signalGroups([signal('TECH_STACK', 'profile'), signal('FOUND_IN_SEARCH', 'discovery'), signal('NO_SCHEMA_MARKUP', 'profile')]);
    assert.deepEqual(groups.map((group) => group.category), ['discovery', 'profile']);
    assert.equal(groups[1].items.length, 2);
  });

  test('statuses have labels and tones; only a running run is polled', () => {
    setLocale('en');
    assert.equal(runStatusLabel('interrupted'), 'Interrupted: confirm again to resume');
    assert.equal(runTone('succeeded'), 'success');
    assert.equal(runTone('failed'), 'dangerTone');
    assert.equal(outboxStatusLabel('held'), 'Approved, held');
    assert.equal(outboxTone('sent'), 'success');
    assert.equal(outboxTone('pending_approval'), 'warning');
    assert.ok(isActive({ status: 'running' }) && !isActive({ status: 'estimated' }));
  });

  test('estimates, fits and rates say what they rest on; unknown stays unknown', () => {
    setLocale('en');
    const estimate = { searches: 2, page_fetches: 5, model_calls: 1, estimated_tokens: 3500, estimated_cost_usd: null, searches_left_today: 28, fetches_left_today: 95,
      budget_cost_left_usd: null, budget_tokens_left: null, fits: true, problems: [], notes: [] };
    assert.equal(estimateLine(estimate), '2 search(es), 5 page read(s), 1 model call(s), estimated cost unknown (the model has no price set).');
    assert.match(estimateLine({ ...estimate, estimated_cost_usd: 0.0123 }), /USD 0\.0123/);
    assert.equal(fitText({ score: 4, disqualified: false }), '4 of 5');
    assert.equal(fitText({ score: null, disqualified: false }), 'No target profile');
    assert.equal(fitText({ score: 0, disqualified: true }), 'Disqualified');
    assert.equal(rateText({ count: 1, of: 20, rate_pct: 5 }), '1 of 20 sent (5%)');
    assert.equal(rateText({ count: 0, of: 0, rate_pct: null }), '0 of 0 sent (-)');
  });

  test('English and Arabic catalogs have the same keys and no long dashes', () => {
    assert.deepEqual(Object.keys(businessPipelineAr).sort(), Object.keys(businessPipelineEn).sort());
    const dashes = new RegExp(`[${String.fromCharCode(0x2013)}${String.fromCharCode(0x2014)}]`);
    for (const value of [...Object.values(businessPipelineEn), ...Object.values(businessPipelineAr)]) assert.ok(!dashes.test(String(value)), String(value));
    setLocale('ar');
    assert.equal(outboxStatusLabel('sent'), 'مُرسلة');
    setLocale('en');
  });
});
