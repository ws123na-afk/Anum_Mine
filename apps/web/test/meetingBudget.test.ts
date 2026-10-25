// Unit tests for the agent meeting budget helpers (src/lib/meetingBudget.ts). No browser, no network.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { budgetBlocks, budgetTone, parseCapInput, usd } from '../src/lib/meetingBudget.ts';
import { CATALOGS, placeholdersOf, type MessageKey } from '../src/lib/i18n.ts';
import type { MeetingBudget } from '../src/lib/liveMeeting.ts';

const budget = (patch: Partial<MeetingBudget> = {}): MeetingBudget => ({
  limit_usd: 50, default_limit_usd: 50, is_default: true, used_usd: 0, remaining_usd: 50, percent: 0, turns: 0, exceeded: false,
  period_start: '2026-10-01', resets_on: '2026-11-01', updated_by: null, updated_at: null, message: null, can_manage: true, ...patch,
});

describe('money text', () => {
  test('cents above a dollar, a few decimals below so small spend is not shown as zero', () => {
    assert.equal(usd(0), '0.00');
    assert.equal(usd(12.5), '12.50');
    assert.equal(usd(50), '50.00');
    assert.equal(usd(49.999), '49.99'); // never rounds up to the limit
    assert.equal(usd(12.34), '12.34');
    assert.equal(usd(0.5), '0.50');
    assert.equal(usd(0.0021), '0.0021');
    assert.equal(usd(0.002), '0.002');
    assert.equal(usd(0.00001), '0.0001');
    assert.equal(usd(Number.NaN), '0.00');
  });
});

describe('the tone of the meter', () => {
  test('amber from 80 percent and red once the budget is used up', () => {
    assert.equal(budgetTone(budget({ percent: 10 })), 'ok');
    assert.equal(budgetTone(budget({ percent: 80 })), 'near');
    assert.equal(budgetTone(budget({ percent: 99.9 })), 'near');
    assert.equal(budgetTone(budget({ percent: 100 })), 'near');
    assert.equal(budgetTone(budget({ percent: 100, exceeded: true })), 'full');
    assert.equal(budgetTone(budget({ percent: 40, exceeded: true })), 'full');
  });
  test('only the server decides whether starting is blocked', () => {
    assert.equal(budgetBlocks(null), false);
    assert.equal(budgetBlocks(budget()), false);
    assert.equal(budgetBlocks(budget({ exceeded: true })), true);
  });
});

describe('the owner types a limit', () => {
  test('whole cents from 0 to 100000, either decimal mark, Arabic digits too', () => {
    assert.deepEqual(parseCapInput('50'), { ok: true, value: 50 });
    assert.deepEqual(parseCapInput(' 12.5 '), { ok: true, value: 12.5 });
    assert.deepEqual(parseCapInput('12,50'), { ok: true, value: 12.5 });
    assert.deepEqual(parseCapInput('0'), { ok: true, value: 0 });
    assert.deepEqual(parseCapInput('100000'), { ok: true, value: 100000 });
    assert.deepEqual(parseCapInput('٥٠'), { ok: true, value: 50 });
    assert.deepEqual(parseCapInput('۲۵'), { ok: true, value: 25 });
  });
  test('refuses anything else', () => {
    for (const bad of ['', 'abc', '-1', '100001', '1.234', '1e3', '12.', '.5', '5 0', '1000000']) assert.deepEqual(parseCapInput(bad), { ok: false }, bad);
  });
});

describe('texts', () => {
  test('the new keys exist in English and Arabic with the same placeholders', () => {
    const keys = Object.keys(CATALOGS.en).filter((k) => k.startsWith('meetingBudget.') || /^meetingRoom\.(budget|transcriptOwnerOnly|outputsOwnerOnly|filesOwnerOnly)/.test(k)) as MessageKey[];
    assert.ok(keys.length >= 30);
    for (const key of keys) {
      assert.deepEqual(placeholdersOf(CATALOGS.ar[key]).sort(), placeholdersOf(CATALOGS.en[key]).sort(), key);
      assert.ok(!/[–—]/.test(String(CATALOGS.en[key]) + String(CATALOGS.ar[key])), 'no long dashes: ' + key);
    }
  });
});
