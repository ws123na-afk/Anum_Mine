// Unit tests for the shared command helpers (src/lib/commandCenter.ts) and the shell model
// (src/lib/shell.ts). docs/design/anum360.md#web-command-center.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import { setLocale } from '../src/lib/i18n.ts';
import { initialSpeechLanguage, relativeTime, sizeLabel } from '../src/lib/commandCenter.ts';
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
