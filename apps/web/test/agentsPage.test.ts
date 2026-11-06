// Unit tests for the Agents and Automations page layout helpers (docs/agents.md#the-agents-page,
// docs/automation.md#the-automations-page). Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { agentBoundaries, appliedLevel, approvalSummary, barWidth, capFor, spendTotals, toolSummary } from '../src/lib/agentsPage.ts';
import type { AgentCap } from '../src/lib/agentControls.ts';
import { stepCounts } from '../src/lib/automation.ts';
import { CATALOGS } from '../src/lib/i18n.ts';

const floor = { autonomy_floor: { read: 'auto', write: 'ask', external: 'ask' } } as const;
const agent = (read: 'off' | 'ask' | 'auto', write: 'off' | 'ask' | 'auto', external: 'off' | 'ask') => ({ autonomy: { read, write, external } });
const cap = (id: string, used: number, limit: number | null, exceeded = false): AgentCap => ({
  agent_id: id, name: id, status: 'active', sharing: 'workspace', monthly_cost_limit_usd: limit,
  usage: { input_tokens: 0, output_tokens: 0, estimated_cost_usd: used, calls: 1, unpriced_calls: 0 }, percent_used: limit ? (used / limit) * 100 : null, exceeded, updated_by: null, updated_at: null,
});

describe('agent boundaries', () => {
  test('the workspace floor tightens the agent, never loosens it', () => {
    assert.equal(appliedLevel(agent('auto', 'auto', 'ask'), 'write', floor), 'ask');
    assert.equal(appliedLevel(agent('auto', 'off', 'ask'), 'write', floor), 'off');
    assert.equal(appliedLevel(agent('auto', 'auto', 'ask'), 'write', null), 'auto');
  });
  test('external actions are either blocked or wait for approval', () => {
    const lines = agentBoundaries(agent('auto', 'ask', 'ask'), floor).map((b) => b.key);
    assert.deepEqual(lines, ['agentsPage.bound.toolsOnly', 'agentsPage.bound.externalAsk', 'agentsPage.bound.writeAsk', 'agentsPage.bound.readAuto']);
    assert.equal(agentBoundaries(agent('off', 'off', 'off'), null)[1].key, 'agentsPage.bound.externalOff');
  });
  test('a change made without asking is flagged amber', () => {
    assert.equal(agentBoundaries(agent('auto', 'auto', 'ask'), null)[2].tone, 'warn');
  });
  test('the card summary names the strictest gate', () => {
    assert.equal(approvalSummary(agent('auto', 'ask', 'ask'), floor).key, 'agentsPage.approval.required');
    assert.equal(approvalSummary(agent('auto', 'off', 'off'), floor).key, 'agentsPage.approval.readOnly');
    assert.equal(approvalSummary(agent('auto', 'auto', 'off'), null).key, 'agentsPage.approval.policy');
  });
  test('every boundary key is translated in English and Arabic', () => {
    for (const locale of ['en', 'ar'] as const) for (const b of agentBoundaries(agent('ask', 'auto', 'ask'), null)) assert.ok(CATALOGS[locale][b.key]);
  });
});

describe('agent cards and spending', () => {
  test('tool summary shows two names and counts the rest', () => {
    assert.deepEqual(toolSummary({ allowed_tools: ['a', 'b', 'c', 'd'] }), { names: ['a', 'b'], more: 2 });
    assert.deepEqual(toolSummary({ allowed_tools: [] }), { names: [], more: 0 });
  });
  test('spending totals sum caps only where a cap exists', () => {
    const totals = spendTotals([cap('a', 3, 10), cap('b', 1.5, null), cap('c', 6, 5, true)]);
    assert.equal(totals.used, 10.5);
    assert.equal(totals.limit, 15);
    assert.equal(totals.percent, 70);
    assert.equal(totals.capped, 2);
    assert.equal(totals.over, 1);
  });
  test('no caps means no limit and no percent, never zero', () => {
    const totals = spendTotals([cap('a', 2, null)]);
    assert.equal(totals.limit, null);
    assert.equal(totals.percent, null);
  });
  test('capFor and barWidth', () => {
    assert.equal(capFor([cap('a', 1, 2)], 'a')?.agent_id, 'a');
    assert.equal(capFor(null, 'a'), null);
    assert.equal(barWidth(null), 0);
    assert.equal(barWidth(140), 100);
    assert.equal(barWidth(-3), 0);
  });
});

describe('automation builder', () => {
  test('stepCounts counts email and task steps for the review card', () => {
    assert.deepEqual(stepCounts(['task.create', 'email.send', 'notify', 'email.send']), { email: 2, tasks: 1 });
    assert.deepEqual(stepCounts([]), { email: 0, tasks: 0 });
  });
});
