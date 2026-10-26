// Unit tests for the meeting summary, model drafts and decision to work links in src/lib/liveMeeting.ts. No browser, no network.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  applyEvent, citedMessages, initialState, isModelDraft, linksOf, liveSummary, workIndex,
  type RoomBundle, type RoomEvent, type RoomMeeting, type RoomOutput, type WorkLink,
} from '../src/lib/liveMeeting.ts';

const meeting: RoomMeeting = {
  id: 'm1', title: 'Plan', status: 'finished', revision: 4, created_by: 'owner', max_turns: 8, max_duration_seconds: 600, max_tokens: 5000, max_cost_usd: '1.000000',
  max_participants: 6, total_input_tokens: 10, total_output_tokens: 20, total_cost_usd: '0.001000', started_at: null, ended_at: null, ended_reason: 'owner',
  hand_raised_by: null, transcript_deleted_at: null,
};
const out = (over: Partial<RoomOutput>): RoomOutput => ({ id: 'o1', kind: 'decision', body: 'Ship Friday', state: 'confirmed', owner_label: null, source_sequences: [1], task_id: null, created_by: 'owner', decided_by: null, ...over });
const link = (over: Partial<WorkLink>): WorkLink => ({ id: 'l1', output_id: 'o1', target_kind: 'task', target_id: 't1', target_label: 'Release notes', target_status: 'created', ...over });
const bundle = (outputs: RoomOutput[], links?: WorkLink[]): RoomBundle => ({
  meeting, participants: [], attendance: [], agenda: [], outputs, links, files: [], speaking: [], last_event_seq: 5, transcript_deleted: false, viewer: { user_id: 'owner', is_owner: true },
});
let seq = 5;
const ev = (payload: Record<string, unknown>): RoomEvent => ({ seq: ++seq, kind: 'output', actor_id: 'owner', payload, created_at: '2026-10-25T10:00:00Z' });

describe('summary', () => {
  test('the confirmed summary wins over a draft and a dismissed one is not shown', () => {
    const draft = out({ id: 's1', kind: 'summary', state: 'draft' });
    const done = out({ id: 's2', kind: 'summary', state: 'confirmed' });
    const gone = out({ id: 's3', kind: 'summary', state: 'dismissed' });
    assert.equal(liveSummary([draft, done, gone])?.id, 's2');
    assert.equal(liveSummary([gone, draft])?.id, 's1');
    assert.equal(liveSummary([gone, out({})]), null);
  });

  test('a summary and a model extraction are model drafts, a typed one is not', () => {
    assert.equal(isModelDraft(out({ kind: 'summary' })), true);
    assert.equal(isModelDraft(out({ origin: 'model' })), true);
    assert.equal(isModelDraft(out({ origin: 'person' })), false);
    assert.equal(isModelDraft(out({})), false);
  });

  test('citations in a summary line become message numbers', () => {
    assert.deepEqual(citedMessages('The group agreed on Friday. [#3, #12]'), [3, 12]);
    assert.deepEqual(citedMessages('- Point [#1]'), [1]);
    assert.deepEqual(citedMessages('Issue #7 is not a citation, and neither is [#a]'), []);
    assert.deepEqual(citedMessages(''), []);
  });
});

describe('links', () => {
  test('a link event adds once, an unlink event removes, a replay changes nothing', () => {
    const state = initialState(bundle([out({})]), []);
    const added = applyEvent(state, ev({ op: 'link', link: link({}) }));
    assert.deepEqual(added.links.map((l) => l.id), ['l1']);
    const again = applyEvent(added, { ...ev({ op: 'link', link: link({}) }), seq: added.lastSeq });
    assert.equal(again, added);
    assert.deepEqual(applyEvent(added, ev({ op: 'unlink', link: link({}) })).links, []);
  });

  test('a stored output event keeps the hash already held for the same text, and drops it when the text changed', () => {
    const state = initialState(bundle([out({ id: 's1', kind: 'summary', state: 'draft', payload_hash: 'a'.repeat(64) })]), []);
    const same = applyEvent(state, ev({ op: 'update', output: out({ id: 's1', kind: 'summary', state: 'draft' }) }));
    assert.equal(same.outputs[0].payload_hash, 'a'.repeat(64));
    const edited = applyEvent(same, ev({ op: 'update', output: out({ id: 's1', kind: 'summary', state: 'draft', body: 'Changed' }) }));
    assert.equal(edited.outputs[0].payload_hash, undefined);
  });

  test('links of a decision, and the work index in the other direction', () => {
    const decisions = [out({ id: 'o1' }), out({ id: 'o2', body: 'Hire two' })];
    const links = [link({}), link({ id: 'l2', output_id: 'o2' }), link({ id: 'l3', target_kind: 'run', target_id: 'r1', target_label: null, target_status: 'completed' }), link({ id: 'l4', output_id: 'gone' })];
    assert.deepEqual(linksOf(links, 'o1').map((l) => l.id), ['l1', 'l3']);
    const index = workIndex(links, decisions);
    assert.equal(index.length, 2);
    assert.deepEqual(index[0].decisions.map((d) => d.id), ['o1', 'o2']);
    assert.equal(index[1].label, 'r1');
  });

  test('a bundle without links (an older server) opens with none', () => {
    assert.deepEqual(initialState(bundle([]), []).links, []);
  });
});
