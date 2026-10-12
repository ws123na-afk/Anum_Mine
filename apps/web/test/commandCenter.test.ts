// Unit tests for the Command Center helpers (src/lib/commandCenter.ts) and the shell model
// (src/lib/shell.ts). docs/design/anum360.md#web-command-center.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import type { AgentRun, Approval, Task } from '@anum/contracts';
import { setLocale } from '../src/lib/i18n.ts';
import {
  CAPABILITY_TILES, ORB_CHIPS, activeTasks, buildSuggestions, commandPrompt, commandTitle, currentTasks, initialSpeechLanguage, phaseProgress,
  quickCommands, recentCommands, relativeTime, sizeLabel, statusTone, trackerPhases,
} from '../src/lib/commandCenter.ts';
import {
  NAV, VIEWS, badgeText, displayName, initials, isPaletteShortcut, matchScore, matchViews, moveActive, notificationCount, viewFromHash, visibleViews,
} from '../src/lib/shell.ts';

afterEach(() => setLocale('en'));

const at = (minutes: number) => new Date(Date.UTC(2026, 9, 1, 9, minutes)).toISOString();
const task = (id: string, status: Task['status'], minutes = 0, extra: Partial<Task> = {}): Task => ({
  id, title: `Title ${id}`, prompt: `Prompt ${id}`, status, tenantId: 't', workspaceId: 'w', createdAt: at(minutes), updatedAt: at(minutes), ...extra,
});
const step = (type: AgentRun['steps'][number]['type'], seconds: number) => ({ id: `${type}_${seconds}`, type, summary: type, createdAt: new Date(Date.parse(at(0)) + seconds * 1000).toISOString() });

describe('tracker phases', () => {
  test('nothing to follow is all to do', () => {
    assert.deepEqual(trackerPhases(null, null).map((p) => p.state), ['todo', 'todo', 'todo', 'todo', 'todo']);
  });
  test('submitting shows the first step active', () => {
    assert.deepEqual(trackerPhases(null, null, true).map((p) => p.state), ['active', 'todo', 'todo', 'todo', 'todo']);
  });
  test('a completed run is all done, with durations from step times', () => {
    const phases = trackerPhases(task('a', 'completed'), { status: 'completed', steps: [step('retrieval', 2), step('model_call', 5), step('final', 7)] });
    assert.deepEqual(phases.map((p) => p.state), ['done', 'done', 'done', 'done', 'done']);
    assert.equal(phases[1].seconds, 5);
    assert.equal(phases[3].seconds, 2);
    assert.equal(phaseProgress(phases), 100);
  });
  test('waiting for approval holds the agents step', () => {
    const phases = trackerPhases(task('a', 'waiting_approval'), { status: 'waiting_approval', steps: [step('model_call', 1), step('approval_wait', 2)] });
    assert.deepEqual(phases.map((p) => p.state), ['done', 'done', 'waiting', 'todo', 'todo']);
    assert.equal(phaseProgress(phases), 50);
  });
  test('a failed run marks the step it reached', () => {
    const phases = trackerPhases(task('a', 'failed'), { status: 'failed', steps: [step('model_call', 1)] });
    assert.deepEqual(phases.map((p) => p.state), ['done', 'failed', 'todo', 'todo', 'todo']);
  });
  test('running without steps is planning', () => {
    assert.equal(trackerPhases(task('a', 'running'), null)[1].state, 'active');
  });
});

describe('task lists', () => {
  const tasks = [task('done', 'completed', 1), task('run', 'running', 2), task('new', 'created', 3), task('wait', 'waiting_approval', 4), task('arch', 'running', 5, { archivedAt: at(6) })];
  test('current tasks are open, unarchived, newest change first', () => {
    const open = currentTasks(tasks);
    assert.deepEqual(open.items.map((x) => x.id), ['wait', 'new', 'run']);
    assert.equal(open.total, 3);
  });
  test('active tasks exclude created ones', () => assert.deepEqual(activeTasks(tasks).map((x) => x.id), ['wait', 'run']));
  test('recent commands are newest first', () => assert.deepEqual(recentCommands(tasks, 2).map((x) => x.id), ['wait', 'new']));
  test('status tones', () => {
    assert.equal(statusTone('completed'), 'success');
    assert.equal(statusTone('failed'), 'dangerTone');
    assert.equal(statusTone('created'), 'neutral');
    assert.equal(statusTone('running'), 'warning');
  });
});

describe('commands', () => {
  test('title is the first line, capped', () => {
    assert.equal(commandTitle('  hello\nworld'), 'hello');
    assert.equal(commandTitle('x'.repeat(100)).length, 80);
  });
  test('prompt names attached documents', () => {
    assert.equal(commandPrompt(' do it '), 'do it');
    assert.match(commandPrompt('do it', [{ id: 'file_1', name: 'a.pdf' }]), /a\.pdf \(file id file_1\)/);
  });
  test('quick commands put history first without repeats, then examples', () => {
    const chips = quickCommands([task('a', 'completed', 1, { prompt: 'Same' }), task('b', 'completed', 2, { prompt: 'same' })]);
    assert.equal(chips.length, 5);
    assert.deepEqual(chips.filter((c) => c.fromHistory).map((c) => c.text), ['same']);
    assert.notDeepEqual(quickCommands([], 1).map((c) => c.id), quickCommands([], 0).map((c) => c.id));
  });
  test('speech language follows a saved choice, else the interface', () => {
    assert.equal(initialSpeechLanguage('ar', 'en'), 'ar');
    assert.equal(initialSpeechLanguage('ur', 'ar'), 'ar');
    assert.equal(initialSpeechLanguage(null, 'en'), 'en');
  });
  test('shortcuts never point at a missing module (no calendar, no calls)', () => {
    for (const item of [...ORB_CHIPS, ...CAPABILITY_TILES]) assert.ok(!/meeting|calendar|call/i.test(item.id), item.id);
  });
});

describe('suggestions', () => {
  const approval = { id: 'ap', taskId: 'a', action: 'x', riskLevel: 'high', status: 'pending', reason: '', createdAt: at(0) } as Approval;
  test('urgent first, each with an action', () => {
    const out = buildSuggestions({ approvals: [approval], tasks: [task('f', 'failed')], unreadEmails: 3, mailboxes: 1, files: 2 });
    assert.deepEqual(out.map((s) => s.id), ['approvals', 'failed:f', 'unread']);
    assert.match(out[0].title, /1 approval is waiting/);
    assert.deepEqual(out[1].action, { kind: 'retry', taskId: 'f' });
  });
  test('a new workspace gets setup suggestions; unknown inbox says nothing', () => {
    const out = buildSuggestions({ approvals: [], tasks: [], unreadEmails: null, mailboxes: null, files: 0 });
    assert.deepEqual(out.map((s) => s.id), ['files', 'first']);
    assert.deepEqual(buildSuggestions({ approvals: [], tasks: [], unreadEmails: 0, mailboxes: 0, files: 1 }).map((s) => s.id), ['mailbox', 'first']);
  });
  test('Arabic plural forms', () => {
    setLocale('ar');
    const out = buildSuggestions({ approvals: [approval, { ...approval, id: 'b' }], tasks: [task('a', 'completed')], unreadEmails: null, mailboxes: null, files: 1 });
    assert.equal(out[0].title, 'موافقتان بانتظارك');
  });
});

test('relative time and sizes', () => {
  const now = Date.parse(at(30));
  assert.equal(relativeTime(at(30), now), 'just now');
  assert.equal(relativeTime(at(20), now), '10 minutes ago');
  assert.equal(relativeTime('nope', now), '');
  assert.equal(sizeLabel(512), '512 B');
  assert.equal(sizeLabel(2048), '2 KB');
  assert.equal(sizeLabel(2.4 * 1024 * 1024), '2.4 MB');
});

describe('shell model', () => {
  test('every view is in exactly one sidebar group, the mockup entries first', () => {
    assert.equal(new Set(VIEWS).size, VIEWS.length);
    assert.deepEqual(NAV[0].views.slice(0, 3), ['home', 'command', 'assistant']);
  });
  test('hash routing keeps deep links', () => {
    assert.equal(viewFromHash(''), 'home');
    assert.equal(viewFromHash('#tasks'), 'tasks');
    assert.equal(viewFromHash('#language'), 'settings');
    assert.equal(viewFromHash('#connections'), 'integrations');
    assert.equal(viewFromHash('#invitation=abc'), 'members');
    assert.equal(viewFromHash('#nope'), 'home');
  });
  test('developer only for owners', () => {
    assert.ok(!visibleViews(null).includes('developer'));
    assert.ok(visibleViews(true).includes('developer'));
    // The platform console is only for platform operators.
    assert.ok(!visibleViews(true).includes('organisations'));
    assert.ok(visibleViews(null, true).includes('organisations'));
    assert.ok(visibleViews(null).includes('team') && visibleViews(null).includes('assistants'));
  });
  test('palette matching ranks exact, prefix, substring', () => {
    assert.equal(matchScore('Tasks', 'tasks'), 3);
    assert.equal(matchScore('Tasks', 'ta'), 2);
    assert.equal(matchScore('My Tasks', 'ask'), 1);
    assert.equal(matchScore('Tasks', 'zz'), 0);
    const labels = (v: string) => ({ tasks: 'Tasks', automation: 'Automations', home: 'Home' } as Record<string, string>)[v] ?? v;
    assert.deepEqual(matchViews('a', ['tasks', 'automation', 'home'], labels as never).map((x) => x.id), ['automation', 'tasks']);
    assert.equal(matchViews('', ['home', 'tasks'], labels as never).length, 2);
  });
  test('shortcut and arrow keys', () => {
    assert.ok(isPaletteShortcut({ key: 'k', metaKey: true, ctrlKey: false }));
    assert.ok(isPaletteShortcut({ key: 'K', metaKey: false, ctrlKey: true }));
    assert.ok(isPaletteShortcut({ key: '/', metaKey: false, ctrlKey: false }));
    assert.ok(!isPaletteShortcut({ key: '/', metaKey: false, ctrlKey: false }, true));
    assert.equal(moveActive(2, 3, 'ArrowDown'), 0);
    assert.equal(moveActive(0, 3, 'ArrowUp'), 2);
    assert.equal(moveActive(0, 0, 'ArrowDown'), -1);
  });
  test('names, initials, counts', () => {
    assert.equal(displayName({ given_name: 'Shahzeb', name: 'Shahzeb Rahman' }), 'Shahzeb');
    assert.equal(displayName({ email: 'owner@example.com' }), 'owner');
    assert.equal(displayName(null), null);
    assert.equal(initials('shahzeb rahman'), 'SR');
    assert.equal(initials('user_local'), 'UL');
    assert.equal(initials('شاه زيب'), 'شز');
    assert.equal(notificationCount({ pendingApprovals: 2, alerts: 1, notices: 0 }), 3);
    assert.equal(notificationCount({ pendingApprovals: 2, alerts: 1, notices: 0, team: 4 }), 7);
    assert.equal(badgeText(120), '99+');
  });
});
