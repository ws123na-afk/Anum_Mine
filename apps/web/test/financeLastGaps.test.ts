// The VAT filing deadline form helpers and the dismiss catalog keys (docs/business-tax.md#filing-deadlines,
// docs/business-books.md#receipts-to-bills).
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { deadlineSource, formFrom, presetKey, settingsBody, settingsProblem } from '../src/lib/businessTax.ts';
import { financeLastGapsEn } from '../src/lib/messages/financeLastGaps.en.ts';
import { financeLastGapsAr } from '../src/lib/messages/financeLastGaps.ar.ts';
import { en } from '../src/lib/messages/en.ts';
import { ar } from '../src/lib/messages/ar.ts';

const base = { ...formFrom(null, null), registered: true, registration_number: 'SYNTHETIC-1' };

describe('VAT filing deadline in the tax settings', () => {
  it('follows the owner, then a preset, then no VAT, then none, like the API', () => {
    assert.equal(deadlineSource({ ...base, jurisdiction: 'OTHER', country: 'FR', filing_due_days: '' }), 'none');
    assert.equal(deadlineSource({ ...base, jurisdiction: 'OTHER', country: 'FR', filing_due_days: '20' }), 'owner');
    assert.equal(deadlineSource({ ...base, jurisdiction: 'OTHER', country: 'om', filing_due_days: '' }), 'preset');
    assert.equal(deadlineSource({ ...base, jurisdiction: 'OTHER', country: 'KW', filing_due_days: '' }), 'no_vat');
    assert.equal(deadlineSource({ ...base, jurisdiction: 'SA', country: '', filing_due_days: '' }), 'preset');
    assert.equal(presetKey({ jurisdiction: 'OTHER', country: 'BH' }), 'gaps.deadline.endOfNextMonth');
    assert.equal(presetKey({ jurisdiction: 'AE', country: '' }), 'gaps.deadline.days28');
    assert.equal(presetKey({ jurisdiction: 'OTHER', country: 'OM' }), 'gaps.deadline.days30');
    assert.equal(presetKey({ jurisdiction: 'OTHER', country: 'QA' }), null);
  });

  it('sends the country only for Other and the due day as a number, and refuses bad values', () => {
    const body = settingsBody({ ...base, jurisdiction: 'OTHER', country: 'om', filing_due_days: ' 25 ' }, 3);
    assert.deepEqual([body.country, body.filing_due_days, body.version], ['OM', 25, 3]);
    const ksa = settingsBody({ ...base, jurisdiction: 'SA', registration_number: '300000000000003', country: 'OM', filing_due_days: '' }, null);
    assert.deepEqual([ksa.country, ksa.filing_due_days], [null, null]);
    const today = '2026-11-10';
    assert.equal(settingsProblem({ ...base, jurisdiction: 'OTHER', country: 'OMN', filing_due_days: '' }, today), 'gaps.problem.country');
    assert.equal(settingsProblem({ ...base, jurisdiction: 'OTHER', country: 'sa', filing_due_days: '' }, today), 'gaps.problem.countryJurisdiction');
    for (const due of ['0', '121', '2.5', 'ten']) assert.equal(settingsProblem({ ...base, jurisdiction: 'OTHER', country: '', filing_due_days: due }, today), 'gaps.problem.dueDays');
    assert.equal(settingsProblem({ ...base, jurisdiction: 'OTHER', country: 'BH', filing_due_days: '120' }, today), null);
  });

  it('prefills the country from the books only before tax is set up', () => {
    assert.equal(formFrom(null, 'bh').country, 'BH');
    assert.equal(formFrom(null, 'SA').country, '');
  });
});

describe('catalog', () => {
  it('has every new key in English and Arabic with the same placeholders, in the merged catalogs', () => {
    const vars = (text: unknown) => [...JSON.stringify(text).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().filter((v, i, all) => all.indexOf(v) === i);
    assert.deepEqual(Object.keys(financeLastGapsAr).sort(), Object.keys(financeLastGapsEn).sort());
    for (const [key, text] of Object.entries(financeLastGapsEn)) {
      assert.deepEqual(vars((financeLastGapsAr as Record<string, unknown>)[key]), vars(text), key);
      assert.ok(key in en && key in ar, key);
    }
    assert.match(financeLastGapsEn['fdepth.vat.filing_deadline_missing'], /^Set your VAT filing period and due day in Tax settings/);
    assert.match(financeLastGapsEn['gaps.deadline.portal'], /never files and does not check ZATCA or FTA compliance/);
  });
});
