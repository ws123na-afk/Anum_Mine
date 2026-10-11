// Unit tests for the Tasks view helpers in src/lib/tasks.ts.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { AgentRun, Approval, Task } from '@anum/contracts';
import {
  approvalsForTask, countByStatus, filterTasks, finalAnswer, hasMorePages, mergeTaskPages, parseAnswer, parseInline, runForTask, streamBadge,
} from '../src/lib/tasks.ts';

const task = (id: string, status: Task['status'], title = id, prompt = ''): Task => ({
  id, title, prompt, status, tenantId: 't', workspaceId: 'w', createdAt: '2026-10-01T00:00:00Z', updatedAt: '2026-10-01T00:00:00Z',
});
const run = (taskId: string, result?: string, steps: AgentRun['steps'] = []): AgentRun => ({ id: `run_${taskId}`, taskId, status: 'completed', steps, result });

describe('filterTasks', () => {
  const tasks = [task('a', 'running', 'Deploy', 'ship release'), task('b', 'completed', 'Report'), task('c', 'waiting_approval', 'Email')];
  test('status filter, including the active group', () => {
    assert.deepEqual(filterTasks(tasks, 'completed', '').map((t) => t.id), ['b']);
    assert.deepEqual(filterTasks(tasks, 'active', '').map((t) => t.id), ['a', 'c']);
    assert.equal(filterTasks(tasks, 'all', '').length, 3);
  });
  test('search matches title, instructions and id case-insensitively', () => {
    assert.deepEqual(filterTasks(tasks, 'all', 'RELEASE').map((t) => t.id), ['a']);
    assert.deepEqual(filterTasks(tasks, 'all', ' report ').map((t) => t.id), ['b']);
    assert.deepEqual(filterTasks(tasks, 'completed', 'deploy'), []);
  });
});

test('mergeTaskPages keeps first-page rows and drops duplicates', () => {
  const merged = mergeTaskPages([task('a', 'completed')], [task('a', 'running'), task('b', 'failed'), task('b', 'failed')]);
  assert.deepEqual(merged.map((t) => [t.id, t.status]), [['a', 'completed'], ['b', 'failed']]);
});

test('hasMorePages and countByStatus', () => {
  assert.equal(hasMorePages(50), true);
  assert.equal(hasMorePages(12), false);
  assert.deepEqual(countByStatus([task('a', 'queued'), task('b', 'completed'), task('c', 'cancelled'), task('d', 'failed')]), { active: 1, completed: 1, failed: 2 });
});

test('streamBadge reflects the connection state', () => {
  assert.deepEqual(streamBadge('live'), { label: 'Live updates', tone: 'success' });
  assert.equal(streamBadge('offline').tone, 'dangerTone');
  assert.equal(streamBadge('connecting').label, 'Connecting');
});

test('approvalsForTask lists pending first', () => {
  const a = (id: string, taskId: string, status: Approval['status'], createdAt: string) => ({ id, taskId, status, createdAt, action: 'x', riskLevel: 'low', reason: '' }) as Approval;
  const list = approvalsForTask([a('1', 't', 'approved', '2026-10-03'), a('2', 'other', 'pending', '2026-10-04'), a('3', 't', 'pending', '2026-10-01')], 't');
  assert.deepEqual(list.map((x) => x.id), ['3', '1']);
});

test('runForTask prefers the fetched run for the selected task', () => {
  assert.equal(runForTask('t', run('t', 'f'), run('t', 'r'))?.result, 'f');
  assert.equal(runForTask('t', run('x'), run('t', 'r'))?.result, 'r');
  assert.equal(runForTask('t', null, run('x')), null);
  assert.equal(runForTask(undefined, run('t'), null), null);
});

test('finalAnswer uses the result, else the final step', () => {
  assert.equal(finalAnswer(run('t', ' Done ')), 'Done');
  assert.equal(finalAnswer(run('t', undefined, [{ id: 's', type: 'final', summary: 'From step', createdAt: '' }])), 'From step');
  assert.equal(finalAnswer(run('t', '')), null);
  assert.equal(finalAnswer(null), null);
});

describe('parseAnswer', () => {
  test('paragraphs, headings, lists and code fences', () => {
    const blocks = parseAnswer('## Summary\nFirst line\ncontinued.\n\n- one\n- **two**\n\n1. a\n2) b\n\n```\n<b>x</b>\n```');
    assert.deepEqual(blocks.map((b) => b.kind), ['heading', 'paragraph', 'list', 'list', 'code']);
    assert.deepEqual(blocks[1], { kind: 'paragraph', inline: [{ kind: 'text', text: 'First line continued.' }] });
    assert.deepEqual(blocks[2], { kind: 'list', ordered: false, items: [[{ kind: 'text', text: 'one' }], [{ kind: 'strong', text: 'two' }]] });
    assert.equal(blocks[3].kind === 'list' && blocks[3].ordered, true);
    assert.deepEqual(blocks[4], { kind: 'code', text: '<b>x</b>' });
  });
  test('HTML stays literal text', () => {
    assert.deepEqual(parseAnswer('<img src=x onerror=alert(1)>'), [{ kind: 'paragraph', inline: [{ kind: 'text', text: '<img src=x onerror=alert(1)>' }] }]);
  });
  test('a list item continuation and an unterminated fence', () => {
    const blocks = parseAnswer('- item\n  more\n```\ncode');
    assert.deepEqual(blocks, [{ kind: 'list', ordered: false, items: [[{ kind: 'text', text: 'item more' }]] }, { kind: 'code', text: 'code' }]);
  });
});

test('parseInline splits bold, emphasis and code', () => {
  assert.deepEqual(parseInline('Use `pnpm` and **care**, *really*.'), [
    { kind: 'text', text: 'Use ' }, { kind: 'code', text: 'pnpm' }, { kind: 'text', text: ' and ' }, { kind: 'strong', text: 'care' },
    { kind: 'text', text: ', ' }, { kind: 'em', text: 'really' }, { kind: 'text', text: '.' },
  ]);
  assert.deepEqual(parseInline('2 * 3 * 4 snake_case_name'), [{ kind: 'text', text: '2 * 3 * 4 snake_case_name' }]);
});
