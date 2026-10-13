// Unit tests for the Command Center helpers (src/lib/commandCenter.ts: orb chips, tiles, the step
// timeline, live activity, suggestions) and the shell model (src/lib/shell.ts).
// docs/design/anum360.md#web-command-center.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import { setLocale } from '../src/lib/i18n.ts';
import type { CommandSummary } from '@anum/contracts';
import { t } from '../src/lib/i18n.ts';
import {
  CAPABILITY_TILES, EXAMPLE_COMMANDS, ORB_CHIPS, buildSuggestions, initialSpeechLanguage, liveCommands, mergeHistory, relativeTime, sizeLabel, stepState, trackerTitle,
} from '../src/lib/commandCenter.ts';
import {
  NAV, VIEWS, badgeText, displayName, initials, isPaletteShortcut, matchScore, matchViews, moveActive, notificationCount, viewFromHash, visibleViews,
} from '../src/lib/shell.ts';

afterEach(() => setLocale('en'));

const at = (minutes: number) => new Date(Date.UTC(2026, 9, 1, 9, minutes)).toISOString();

test('speech language follows a saved choice, else the interface', () => {
  assert.equal(initialSpeechLanguage('ar', 'en'), 'ar');
  assert.equal(initialSpeechLanguage('ur', 'ar'), 'ar');
  assert.equal(initialSpeechLanguage(null, 'en'), 'en');
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

const summary = (id: string, status: CommandSummary['status'], text = id): CommandSummary => ({
  id, text, source: 'typed', locale: 'en', status, progress: 0, stepCount: 1, completedSteps: 0, pendingApprovals: 0, agents: [], createdAt: at(0), updatedAt: at(0),
});

describe('classic Command Center layout', () => {
  test('eight orb chips and eight tiles, each with a label in both catalogs and a real action', () => {
    assert.equal(ORB_CHIPS.length, 8);
    assert.equal(CAPABILITY_TILES.length, 8);
    assert.deepEqual(ORB_CHIPS.map((c) => t(c.label)), ['Check my emails', 'Prepare a report', 'Find information', 'Create a task', 'Analyze data', 'WhatsApp messages', 'Automate work', 'Open anything']);
    assert.deepEqual(CAPABILITY_TILES.map((c) => t(c.label)), ['Email', 'Data analysis', 'Reports', 'Voice assistant', 'WhatsApp', 'Tasks', 'Web research', 'Automations']);
    for (const item of [...ORB_CHIPS, ...CAPABILITY_TILES]) assert.ok(['view', 'prefill', 'palette'].includes(item.action.kind));
    setLocale('ar');
    assert.equal(t(ORB_CHIPS[0].label), 'افحص بريدي');
    assert.ok(EXAMPLE_COMMANDS.every((key) => t(key).length > 10));
  });

  test('a step\'s timeline state follows its task', () => {
    assert.equal(stepState('completed'), 'done');
    assert.equal(stepState('running'), 'active');
    assert.equal(stepState('queued'), 'active');
    assert.equal(stepState('waiting_approval'), 'waiting');
    assert.equal(stepState('created'), 'todo');
    assert.equal(stepState('cancelled'), 'failed');
    assert.equal(stepState('missing'), 'failed');
  });

  test('the tracker heading says what ANUM is doing', () => {
    assert.equal(t(trackerTitle(null)), 'ANUM is ready');
    assert.equal(t(trackerTitle('waiting_approval')), 'Waiting for your approval');
    assert.equal(t(trackerTitle('running')), 'ANUM is working...');
    assert.equal(t(trackerTitle('completed')), 'Last command');
  });

  test('live activity keeps running and waiting commands; a quiet reload keeps older pages', () => {
    const items = [summary('a', 'completed'), summary('b', 'running'), summary('c', 'waiting_approval'), summary('d', 'ready'), summary('e', 'failed')];
    assert.deepEqual(liveCommands(items).map((c) => c.id), ['b', 'c', 'd']);
    assert.deepEqual(liveCommands(items, 1).map((c) => c.id), ['b']);
    assert.deepEqual(mergeHistory([summary('n', 'running'), summary('a', 'completed')], items).map((c) => c.id), ['n', 'a', 'b', 'c', 'd', 'e']);
  });

  test('suggestions come from the workspace, most urgent first; a failed command is planned again', () => {
    const out = buildSuggestions({ pendingApprovals: 2, commands: [summary('x', 'failed', 'Compare suppliers')], unreadEmails: 3, mailboxes: 1, files: 0 });
    assert.deepEqual(out.map((s) => s.id), ['approvals', 'failed:x', 'unread', 'files']);
    assert.equal(out[0].title, '2 approvals are waiting for you');
    assert.deepEqual(out[1].action, { kind: 'replan', text: 'Compare suppliers' });
    assert.deepEqual(buildSuggestions({ pendingApprovals: 0, commands: [], unreadEmails: null, mailboxes: 0, files: 4 }).map((s) => s.id), ['mailbox', 'first']);
    assert.deepEqual(buildSuggestions({ pendingApprovals: null, commands: null, unreadEmails: null, mailboxes: null, files: null }), []);
  });
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
