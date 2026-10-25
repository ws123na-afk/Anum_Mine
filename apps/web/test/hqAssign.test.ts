import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { AgentCatalog, AgentProfile } from '@anum/contracts';
import { availability, assignHostAvailable, createAssignClient, emptyDraft, newAttemptKey, openAssign, openTaskPanel, parseAssignSentence, riskView, setAssignHostMounted, toRequest, validateDraft } from '../src/lib/hqAssign.ts';

const agent = (over: Partial<AgentProfile> = {}): AgentProfile => ({
  id: 'a1', name: 'Fleet Planner', status: 'active', can_run: true, can_edit: true, autonomy: { read: 'auto', write: 'ask', external: 'off' },
  allowed_tools: ['anum.respond', 'tyre_plan', 'send_email'], unavailable_tools: ['gone_tool'], ...over,
} as AgentProfile);
const catalog = { autonomy_floor: { read: 'auto', write: 'ask', external: 'ask' }, tools: [
  { name: 'anum.respond', category: 'read', risk_level: 'low', always_on: true }, { name: 'tyre_plan', category: 'read', risk_level: 'low', always_on: false },
  { name: 'send_email', category: 'external', risk_level: 'high', always_on: false }] } as unknown as AgentCatalog;

test('validation names every problem and passes a complete draft', () => {
  const now = new Date('2026-10-25T10:00:00Z');
  assert.deepEqual(Object.keys(validateDraft(emptyDraft(), now)).sort(), ['agent', 'instructions', 'title']);
  const ok = { ...emptyDraft({ agentId: 'a1' }), title: 'Check stock', instructions: 'Count them' };
  assert.deepEqual(validateDraft(ok, now), {});
  assert.equal(validateDraft({ ...ok, due: '2026-10-24T10:00' }, now).due, 'hqAssign.err.duePast');
  assert.equal(validateDraft({ ...ok, due: 'nope' }, now).due, 'hqAssign.err.dueInvalid');
  assert.equal(validateDraft({ ...ok, instructions: 'x'.repeat(6001) }, now).instructions, 'hqAssign.err.instructionsLong');
  assert.equal(validateDraft({ ...ok, expectedResult: 'x'.repeat(1001) }, now).expected, 'hqAssign.err.expectedLong');
});

test('the request carries trimmed text, optional fields as null and the source', () => {
  const draft = { ...emptyDraft({ agentId: 'a1', roomName: 'Ops' }), title: '  T ', instructions: ' do it ', priority: 'high' as const, due: '2026-12-01T09:30' };
  const body = toRequest(draft, 'room');
  assert.equal(body.title, 'T'); assert.equal(body.instructions, 'do it'); assert.equal(body.expected_result, null);
  assert.equal(body.context_note, 'Ops'); assert.equal(body.priority, 'high'); assert.equal(body.source, 'room'); assert.equal(body.run, true);
  assert.match(body.due_at ?? '', /^2026-12-01T/);
});

test('availability: paused says turn on first, no permission is denied, missing is unavailable', () => {
  assert.deepEqual(availability(agent(), true), { kind: 'ready' });
  assert.deepEqual(availability(agent({ status: 'paused', can_run: false }), true), { kind: 'paused', canTurnOn: true });
  assert.deepEqual(availability(agent({ status: 'paused', can_edit: false }), true), { kind: 'paused', canTurnOn: false });
  assert.deepEqual(availability(agent({ can_run: false }), true), { kind: 'denied' });
  assert.deepEqual(availability(null, true), { kind: 'unavailable' });
});

test('risk view groups tools by category, flags high risk, and never exceeds the workspace floor', () => {
  const v = riskView(agent({ autonomy: { read: 'auto', write: 'auto', external: 'ask' } }), catalog);
  assert.deepEqual(v.read.tools, ['tyre_plan']); assert.deepEqual(v.external.tools, ['send_email']); assert.deepEqual(v.highRisk, ['send_email']);
  assert.equal(v.write.level, 'ask'); // floor caps auto
  assert.deepEqual(v.unavailable, ['gone_tool']);
  assert.equal(riskView(agent(), null).read.tools.length, 0);
});

test('one attempt key per attempt, and different attempts differ', () => {
  const a = newAttemptKey(); const b = newAttemptKey();
  assert.match(a, /^assign-[A-Za-z0-9-]{8,}$/); assert.notEqual(a, b);
});

test('the client sends the Idempotency-Key header and one POST', async () => {
  const calls: { path: string; init: RequestInit }[] = [];
  const client = createAssignClient(async <T,>(path: string, init: RequestInit) => { calls.push({ path, init }); return {} as T; });
  await client.assign(toRequest({ ...emptyDraft({ agentId: 'a1' }), title: 'T', instructions: 'I' }, 'desk'), 'assign-key-0001');
  assert.equal(calls.length, 1); assert.equal(calls[0].path, '/api/v1/office/assignments'); assert.equal(calls[0].init.method, 'POST');
  assert.equal((calls[0].init.headers as Record<string, string>)['Idempotency-Key'], 'assign-key-0001');
});

test('open requests reach a host only when one is mounted', () => {
  const sent: string[] = [];
  const target = { dispatchEvent: (e: Event) => { sent.push(e.type); return true; } };
  assert.equal(assignHostAvailable(), false);
  assert.equal(openAssign({ agentId: 'a1' }, target), false);
  setAssignHostMounted(true);
  assert.equal(openAssign({ agentId: 'a1' }, target), true);
  assert.equal(openTaskPanel('task_1', target), true);
  assert.equal(openTaskPanel('bad id!', target), false);
  setAssignHostMounted(false);
  assert.deepEqual(sent, ['anum:hq-assign', 'anum:hq-open-task']);
});

test('assignment sentences only open the dialog pre-filled', () => {
  assert.deepEqual(parseAssignSentence('ask Fleet Planner to count the spare tyres'), { agentQuery: 'Fleet Planner', work: 'count the spare tyres' });
  assert.deepEqual(parseAssignSentence('start a task to check stock'), { agentQuery: null, work: 'check stock' });
  assert.deepEqual(parseAssignSentence('assign the audit to Fleet Planner'), { agentQuery: 'Fleet Planner', work: 'the audit' });
  assert.equal(parseAssignSentence('approve the request'), null);
});
