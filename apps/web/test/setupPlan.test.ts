// Unit tests for the Setup plan page helpers in src/lib/setupPlan.ts (docs/setup-plan.md).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  actionTone, applyRequestFor, changeCount, checkChanged, checkStatus, checkTone, evidenceProblem, failedItems, filterItems, groupBySection, itemHref, outcomeSummary, outcomeTone, previewMatches, reasonText, rolloutWeeks,
  type SetupPlanItem,
} from '../src/lib/setupPlan.ts';
import { setLocale } from '../src/lib/i18n.ts';

const item = (key: string, over: Partial<SetupPlanItem> = {}): SetupPlanItem => ({
  key, section: 'agents', kind: 'agent', name: key, action: 'create', reason_code: 'will_create', reason: 'Created active.',
  week: null, link: null, facts: [], outcome: null, error: null, ...over,
});

describe('setup plan items', () => {
  test('create, update and linking an existing object count as changes', () => {
    assert.equal(changeCount({ items: [item('a'), item('b', { action: 'update' }), item('c', { action: 'present' }), item('d', { action: 'skipped' })] }), 2);
    assert.equal(changeCount({ items: [item('c', { action: 'present', reason_code: 'found_by_name' })] }), 1);
    assert.equal(changeCount({ items: [item('c', { action: 'present', reason_code: 'resume_stopped_apply' })] }), 1);
  });
  test('apply sends what the shown preview was built with, and a preview for other choices is not current', () => {
    const shown = { phase: 'pilot' as const, overwrite_edited: false, plan: { version: 3 } };
    assert.equal(previewMatches(shown, 'pilot', false), true);
    assert.equal(previewMatches(shown, 'all', false), false);
    assert.equal(previewMatches(shown, 'pilot', true), false);
    assert.deepEqual(applyRequestFor(shown), { phase: 'pilot', overwrite_edited: false, plan_version: 3 });
  });
  test('filter by section, words (name, reason, facts) and changes only, keeping failures', () => {
    const items = [
      item('Customer Care', { facts: ['Tools: read_whatsapp'] }),
      item('Morning Brief', { section: 'automations', action: 'present' }),
      item('Failed one', { section: 'knowledge', action: 'present', outcome: 'failed' }),
    ];
    assert.deepEqual(filterItems(items, { section: 'all', query: 'whatsapp', changesOnly: false }).map((i) => i.key), ['Customer Care']);
    assert.deepEqual(filterItems(items, { section: 'automations', query: '', changesOnly: false }).map((i) => i.key), ['Morning Brief']);
    assert.deepEqual(filterItems(items, { section: 'all', query: '', changesOnly: true }).map((i) => i.key), ['Customer Care', 'Failed one']);
    assert.deepEqual(filterItems(items, { section: 'all', query: 'CÚSTOMER care', changesOnly: false }).map((i) => i.key), ['Customer Care']);
  });
  test('groups follow the apply order and drop empty sections', () => {
    const groups = groupBySection([item('x', { section: 'automations' }), item('y', { section: 'governance' }), item('z', { section: 'automations' })]);
    assert.deepEqual(groups.map((g) => [g.section, g.items.length]), [['governance', 1], ['automations', 2]]);
  });
  test('tones say what needs attention', () => {
    assert.equal(actionTone('create'), 'success');
    assert.equal(actionTone('kept'), 'warning');
    assert.equal(actionTone('unsupported'), 'dangerTone');
    assert.equal(actionTone('present'), 'neutral');
    assert.equal(outcomeTone('failed'), 'dangerTone');
    assert.equal(outcomeTone('busy'), 'warning');
  });
  test('links open the item page; a knowledge folder opens itself', () => {
    assert.equal(itemHref(null), null);
    assert.equal(itemHref({ view: 'agents', id: 'agent_1' }), '#agents');
    assert.equal(itemHref({ view: 'knowledge', id: 'kbc 1' }), '#knowledge/kbc%201');
  });
});

describe('rollout and results', () => {
  test('weeks list the agents and assistants that start in them; week 1 is the pilot', () => {
    const weeks = rolloutWeeks([{ week: 2, title: 'Routine' }, { week: 1, title: 'Pilot' }], [
      item('Chief', { week: 1 }), item('Command', { section: 'assistants', week: 1 }), item('Folder', { section: 'knowledge', week: 1 }),
    ]);
    assert.deepEqual(weeks.map((w) => [w.week, w.pilot, w.names]), [[1, true, ['Chief', 'Command']], [2, false, []]]);
  });
  test('outcome summary keeps a fixed order without zeros, and lists failures', () => {
    const result = { outcomes: { failed: 1, created: 12, present: 3 }, items: [item('a', { outcome: 'failed', error: 'boom' }), item('b', { outcome: 'created' })] };
    assert.deepEqual(outcomeSummary(result), [{ outcome: 'created', count: 12 }, { outcome: 'present', count: 3 }, { outcome: 'failed', count: 1 }]);
    assert.deepEqual(failedItems(result).map((i) => i.error), ['boom']);
  });
  test('fixed reasons are shown in the reader\'s language; others are the API sentence', () => {
    setLocale('ar');
    try {
      assert.match(reasonText(item('a', { reason_code: 'needs_org_admin', reason: 'Needs the organization administrator role' })), /مسؤول المؤسسة/);
      assert.doesNotMatch(reasonText(item('a', { reason_code: 'needs_org_admin', reason: 'x' })), /^Needs/);
      assert.equal(reasonText(item('a', { reason_code: 'will_create', reason: 'Created active.' })), 'Created active.');
    } finally { setLocale('en'); }
  });
});

describe('pilot checks', () => {
  const recorded = { result: { plan_id: 'p', check_id: 'CC01', status: 'fail' as const, note: 'x', evidence: 'task_1', updated_by: 'u', updated_at: '2026-10-12T10:00:00Z' } };
  test('an unrecorded check is not run; tones follow the result', () => {
    assert.equal(checkStatus({ result: null }), 'not_run');
    assert.equal(checkStatus(recorded), 'fail');
    assert.equal(checkTone('pass'), 'success');
    assert.equal(checkTone('fail'), 'dangerTone');
    assert.equal(checkTone('not_run'), 'neutral');
  });
  test('save is offered only for a real change (trimmed)', () => {
    assert.equal(checkChanged({ result: null }, { status: 'not_run', note: ' ', evidence: '' }), false);
    assert.equal(checkChanged({ result: null }, { status: 'pass', note: '', evidence: '' }), true);
    assert.equal(checkChanged(recorded, { status: 'fail', note: 'x ', evidence: ' task_1' }), false);
    assert.equal(checkChanged(recorded, { status: 'fail', note: 'y', evidence: 'task_1' }), true);
  });
  test('evidence is an id or an https link of at most 300 characters', () => {
    assert.equal(evidenceProblem('approval_12'), null);
    assert.equal(evidenceProblem('https://anum.example/tasks/1'), null);
    assert.equal(evidenceProblem('http://anum.example/tasks/1'), 'plan.checks.evidenceHttps');
    assert.equal(evidenceProblem('x'.repeat(301)), 'plan.checks.evidenceLong');
  });
});
