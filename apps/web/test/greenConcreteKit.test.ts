// Green Concrete agents kit (src/lib/greenConcreteKit.ts) and agent memory helpers (src/lib/agents.ts): reads change nothing,
// setup and the routine switch send only clicks with hashes, schedules read in words, memory forms are checked before
// anything is sent, and both languages carry every string with the same placeholders.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { AgentMemory, GcKit, GcKitSkill, GcRoutine } from '@anum/contracts';
import {
  KitApiError, createKitClient, kitText, routineAction, scheduleWords, setupOutcomeKey, skillCounts, sortSkills,
} from '../src/lib/greenConcreteKit.ts';
import { emptyMemoryForm, memoryChanges, memoryCreateBody, memoryFormOf, memoryListPath, memoryProblems } from '../src/lib/agents.ts';
import { greenConcreteKitEn } from '../src/lib/messages/greenConcreteKit.en.ts';
import { greenConcreteKitAr } from '../src/lib/messages/greenConcreteKit.ar.ts';
import { en } from '../src/lib/messages/en.ts';
import { ar } from '../src/lib/messages/ar.ts';

const HASH = 'b'.repeat(64);
const kit = (patch: Partial<GcKit> = {}): GcKit => ({
  version: 1, kit_version: 1, as_of: 'now', lang: 'en', building: null, skills: [], tools: [], roles: [], agents: [], sources: [], gaps: [],
  knowledge: { state: 'missing', collection: null }, routines: [], items: [], can_manage: false, can_manage_automations: false, ...patch,
});
const skill = (id: string, state: GcKitSkill['state']): GcKitSkill => ({
  id, version: '1.0.0', name: { en: id, ar: id }, description: { en: '', ar: '' }, tool: 'gc.x', inputs: [], output: { en: '', ar: '' },
  guardrails: [], sources: [], state, roles: [],
});
const routine = (state: GcRoutine['state']): GcRoutine => ({
  key: 'daily_ops_brief', name: { en: 'Brief', ar: 'موجز' }, description: { en: '', ar: '' }, role: 'gc_pmv_manager', skill: 'gc.daily-ops-brief',
  cron: '0 6 * * *', timezone: 'Asia/Dubai', state, workflow_id: null, schedule_id: null, next_run_at: null, agent: null,
});

function recorder(answer: unknown) {
  const calls: { path: string; init: RequestInit }[] = [];
  const request = async <T,>(path: string, init: RequestInit): Promise<T> => { calls.push({ path, init }); return answer as T; };
  return { calls, request };
}

describe('kit client', () => {
  test('reads with GET and refuses an answer that is not version 1', async () => {
    const ok = recorder(kit());
    assert.equal((await createKitClient(ok.request).get('ar')).version, 1);
    assert.equal(ok.calls[0].path, '/api/v1/office/green-concrete/kit?lang=ar');
    assert.equal(ok.calls[0].init.method, 'GET');
    await assert.rejects(createKitClient(recorder({ version: 2 }).request).get('en'), KitApiError);
  });
  test('setup sends a click with each item and its hash, nothing else', async () => {
    const r = recorder({ version: 1, created: 1, results: [] });
    await createKitClient(r.request).setup([{ id: 'knowledge', hash: HASH }]);
    assert.deepEqual(JSON.parse(String(r.calls[0].init.body)), { input_mode: 'click', items: [{ id: 'knowledge', hash: HASH }] });
    await assert.rejects(createKitClient(r.request).setup([]), KitApiError);
    await assert.rejects(createKitClient(r.request).setup([{ id: 'knowledge', hash: 'nope' }]), KitApiError);
  });
  test('the routine switch is a click on a known key', async () => {
    const r = recorder({ key: 'daily_ops_brief', enabled: true, workflow_id: 'w' });
    await createKitClient(r.request).routine('daily_ops_brief', true);
    assert.equal(r.calls[0].path, '/api/v1/office/green-concrete/kit/routines/daily_ops_brief/state');
    assert.deepEqual(JSON.parse(String(r.calls[0].init.body)), { input_mode: 'click', enabled: true });
    await assert.rejects(createKitClient(r.request).routine('../x', true), KitApiError);
  });
});

describe('kit helpers', () => {
  test('skills sort ready first and count what has its data', () => {
    const list = [skill('a', 'not_connected'), skill('b', 'ready'), skill('c', 'unavailable')];
    assert.deepEqual(sortSkills(list).map((s) => s.id), ['b', 'a', 'c']);
    assert.deepEqual(skillCounts(list), { ready: 1, waiting: 2 });
  });
  test('schedules in words for a daily and a weekly time, nothing guessed for others', () => {
    assert.deepEqual(scheduleWords('0 6 * * *'), { key: 'gck.schedule.daily', time: '06:00' });
    assert.deepEqual(scheduleWords('45 13 * * *'), { key: 'gck.schedule.daily', time: '13:45' });
    assert.deepEqual(scheduleWords('0 8 * * 1'), { key: 'gck.schedule.weekly', time: '08:00', day: 'gck.day.1' });
    assert.equal(scheduleWords('*/5 * * * *'), null);
  });
  test('what an owner can do with a routine, and nothing for anyone else', () => {
    assert.equal(routineAction(routine('missing'), true), 'add');
    assert.equal(routineAction(routine('off'), true), 'turn_on');
    assert.equal(routineAction(routine('on'), true), 'turn_off');
    assert.equal(routineAction(routine('needs_agent'), true), 'needs_agent');
    assert.equal(routineAction(routine('off'), false), 'none');
    assert.equal(kitText({ en: 'A', ar: 'ب' }, 'ar'), 'ب');
    assert.equal(setupOutcomeKey('already_done'), 'gck.outcome.already_done');
  });
});

describe('agent memory helpers', () => {
  const memory: AgentMemory = {
    id: 'm1', agent_id: 'a', content: 'Rotate at 6 mm', source: 'manual', task_id: null, created_by: 'u', created_at: '2026-11-01T00:00:00Z',
    kind: 'decision', source_label: 'SOP tyres', source_date: '2026-10-30',
  };
  test('a form is checked before it is sent', () => {
    assert.deepEqual(memoryProblems(emptyMemoryForm(), '2026-11-02'), ['agents.memory.err.content']);
    assert.deepEqual(memoryProblems({ ...emptyMemoryForm(), content: 'x', source_date: '2026-12-01' }, '2026-11-02'), ['agents.memory.err.future']);
    assert.deepEqual(memoryProblems({ ...emptyMemoryForm(), content: 'x', source_date: '2026-13-45' }, '2026-11-02'), ['agents.memory.err.date']);
    assert.deepEqual(memoryProblems({ ...emptyMemoryForm(), content: 'x', source_label: 'y'.repeat(201) }, '2026-11-02'), ['agents.memory.err.sourceLong']);
  });
  test('create body trims and sends null for an empty source or date', () => {
    assert.deepEqual(memoryCreateBody({ content: '  Fact  ', kind: 'fact', source_label: ' ', source_date: '' }),
      { content: 'Fact', kind: 'fact', source_label: null, source_date: null });
  });
  test('an edit sends only what changed, or nothing', () => {
    assert.equal(memoryChanges(memory, memoryFormOf(memory)), null);
    assert.deepEqual(memoryChanges(memory, { ...memoryFormOf(memory), content: 'Rotate at 5 mm' }), { content: 'Rotate at 5 mm' });
    assert.deepEqual(memoryChanges(memory, { ...memoryFormOf(memory), source_label: '', kind: 'fact' }), { kind: 'fact', source_label: '' });
  });
  test('the list path carries the kind filter', () => {
    assert.equal(memoryListPath('a 1', 'tyre', null, 'decision'), '/api/v1/agents/a%201/memories?limit=20&q=tyre&kind=decision');
  });
});

describe('messages', () => {
  test('Arabic has every kit key with the same placeholders, and no long dashes', () => {
    const placeholders = (v: unknown) => [...new Set([...JSON.stringify(v).matchAll(/\{(\w+)\}/g)].map((m) => m[1]))].sort().join(',');
    for (const [key, value] of Object.entries(greenConcreteKitEn)) {
      const arabic = (greenConcreteKitAr as Record<string, unknown>)[key];
      assert.ok(arabic, key);
      assert.equal(placeholders(arabic), placeholders(value), key);
      assert.ok(/[؀-ۿ]/.test(JSON.stringify(arabic)), key);
      assert.ok(!/[\u2013\u2014]/.test(JSON.stringify(value) + JSON.stringify(arabic)), key);
    }
    for (const key of Object.keys(en).filter((k) => k.startsWith('agents.memory.'))) assert.ok(key in ar, key);
  });
});
