import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import { setLocale, t } from '../src/lib/i18n.ts';
import {
  capDraft, capMeter, capsPath, heldKindLabel, heldPath, parseCap, parseReason, parseTokenCap, stopSummary, tokenDraft, tokenMeter,
  type AgentCap, type AgentControls,
} from '../src/lib/agentControls.ts';
import { agentControlsEn } from '../src/lib/messages/agentControls.en.ts';
import { agentControlsAr } from '../src/lib/messages/agentControls.ar.ts';
import { NAV, viewFromHash, visibleViews } from '../src/lib/shell.ts';

afterEach(() => setLocale('en'));

// Owner agent controls (docs/agent-controls.md): the emergency stop and per-agent cost caps.
const cap = (overrides: Partial<AgentCap> = {}): AgentCap => ({
  agent_id: 'agent_outreach', name: 'Outreach', status: 'active', sharing: 'workspace', monthly_cost_limit_usd: 2,
  usage: { input_tokens: 10, output_tokens: 5, estimated_cost_usd: 0.5, calls: 3, unpriced_calls: 0 },
  percent_used: 25, exceeded: false, updated_by: 'user_local', updated_at: '2026-10-18T10:00:00Z', ...overrides,
});
const controls = (engaged: boolean, canRelease = false): AgentControls => ({
  emergency_stop: { engaged, reason: engaged ? 'Runaway agent' : null, changed_by: 'user_local', changed_by_you: true, changed_at: '2026-10-18T10:00:00Z', engaged_at: null },
  can_engage: true, can_release: canRelease, cancelled_tasks: 0,
});

describe('agent controls', () => {
  test('a reason is required, trimmed and bounded like the API', () => {
    assert.deepEqual(parseReason('  Runaway   agent  '), { ok: true, value: 'Runaway agent' });
    assert.equal(parseReason(' a ').ok, false);
    assert.equal(parseReason('x'.repeat(301)).ok, false);
  });

  test('a cap is empty (none), zero or a positive dollar amount', () => {
    assert.deepEqual(parseCap(''), { ok: true, value: null });
    assert.deepEqual(parseCap('0'), { ok: true, value: 0 });
    assert.deepEqual(parseCap('$1,250.456'), { ok: true, value: 1250.46 });
    assert.equal(parseCap('-1').ok, false);
    assert.equal(parseCap('ten').ok, false);
    assert.equal(parseCap('2000000000').ok, false);
    assert.equal(capDraft(cap({ monthly_cost_limit_usd: null })), '');
    assert.equal(capDraft(cap()), '2');
  });

  test('usage against the cap uses the API thresholds', () => {
    assert.deepEqual(
      { tone: capMeter(cap()).tone, bar: capMeter(cap()).barPercent, used: capMeter(cap()).usedText, limit: capMeter(cap()).limitText },
      { tone: 'ok', bar: 25, used: '$0.50', limit: '$2.00' },
    );
    assert.equal(capMeter(cap({ usage: { ...cap().usage, estimated_cost_usd: 1.7 } })).tone, 'warn');
    const over = capMeter(cap({ usage: { ...cap().usage, estimated_cost_usd: 3 } }));
    assert.equal(over.tone, 'stop');
    assert.equal(over.barPercent, 100);
    assert.equal(capMeter(cap({ monthly_cost_limit_usd: 0 })).tone, 'stop');
    const none = capMeter(cap({ monthly_cost_limit_usd: null }));
    assert.equal(none.percent, null);
    assert.equal(none.limitText, t('agentControls.noCap'));
  });

  test('the caps query carries search and paging', () => {
    assert.equal(capsPath('', 0), '/api/v1/agent-controls/agent-caps?limit=20&offset=0');
    assert.equal(capsPath('  out reach ', 40), '/api/v1/agent-controls/agent-caps?limit=20&offset=40&q=out+reach');
    assert.equal(capsPath('', -5), '/api/v1/agent-controls/agent-caps?limit=20&offset=0');
  });

  test('the summary says who can turn the stop off', () => {
    assert.equal(stopSummary(controls(false)), t('agentControls.offSummary'));
    assert.equal(stopSummary(controls(true, true)), t('agentControls.onSummaryRelease'));
    assert.equal(stopSummary(controls(true, false)), t('agentControls.onSummaryWait'));
  });

  test('the page is an admin entry reached by its hash', () => {
    assert.ok(NAV.find((group) => group.group === 'admin')?.views.includes('agentControls'));
    assert.equal(viewFromHash('#agentControls'), 'agentControls');
    assert.ok(visibleViews(false).includes('agentControls'));
  });

  test('English and Arabic catalogs have the same keys, and Arabic reads in Arabic', () => {
    assert.deepEqual(Object.keys(agentControlsAr).sort(), Object.keys(agentControlsEn).sort());
    setLocale('ar');
    assert.match(t('agentControls.title'), /[؀-ۿ]/);
    assert.match(t('agentControls.engaged', { count: 2 }), /[؀-ۿ]/);
    setLocale('en');
    assert.equal(t('agentControls.engaged', { count: 1 }), 'The emergency stop is on. 1 running task was cancelled.');
    assert.equal(t('agentControls.engaged', { count: 0 }), 'The emergency stop is on. No task was running.');
  });
});

describe('agent token caps and the held list', () => {
  test('a token cap is a whole number, empty clears it', () => {
    assert.deepEqual(parseTokenCap(''), { ok: true, value: null });
    assert.deepEqual(parseTokenCap('1,000,000'), { ok: true, value: 1000000 });
    assert.equal(parseTokenCap('1.5').ok, false);
    assert.equal(parseTokenCap('-2').ok, false);
    assert.equal(tokenDraft(cap({ monthly_token_limit: 500 })), '500');
    assert.equal(tokenDraft(cap()), '');
  });

  test('token usage against the token cap', () => {
    assert.equal(tokenMeter(cap()), null);
    const meter = tokenMeter(cap({ monthly_token_limit: 20, total_tokens: 30 }));
    assert.equal(meter?.tone, 'stop');
    assert.equal(meter?.barPercent, 100);
    assert.equal(meter?.usedText, '30');
  });

  test('held items are paged and named', () => {
    assert.equal(heldPath(0), '/api/v1/agent-controls/held?limit=20&offset=0');
    assert.equal(heldPath(-3, 5), '/api/v1/agent-controls/held?limit=5&offset=0');
    assert.equal(heldKindLabel('approval'), 'Approval');
    assert.equal(heldKindLabel('mail'), 'Scheduled email');
    assert.equal(heldKindLabel('post'), 'Scheduled post');
    setLocale('ar');
    assert.match(heldKindLabel('post'), /[؀-ۿ]/);
  });
});
