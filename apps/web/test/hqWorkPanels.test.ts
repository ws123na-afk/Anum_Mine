import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setAssignHostMounted } from '../src/lib/hqAssign.ts';
import { fullPageHash, HQ_PANEL_EVENT, HQ_SCENE_PANEL_EVENT, inHqClick, openHqPanel, openViewInHq, panelForRecord, panelForView, validPanel } from '../src/lib/hqWorkPanels.ts';
import { agentTasksPath } from '../src/hq-assign/agentTasksPath.ts';

// Work opened inside the headquarters (docs/ANUM_MINE_ASSIGN_WORK.md, "Work inside the office").
const recorder = () => {
  const sent: { type: string; detail: unknown }[] = [];
  return { sent, target: { dispatchEvent: (e: Event) => { sent.push({ type: e.type, detail: (e as CustomEvent).detail }); return true; } } };
};

test('without a mounted headquarters nothing is opened, so the caller navigates as before', () => {
  const { sent, target } = recorder();
  assert.equal(openHqPanel({ kind: 'approval', id: 'appr_1' }, target), false);
  assert.equal(openViewInHq('approvals', target), false);
  assert.deepEqual(sent, []);
});

test('with the headquarters mounted each kind is one event; a task keeps its own event', () => {
  const { sent, target } = recorder();
  setAssignHostMounted(true);
  try {
    assert.equal(openHqPanel({ kind: 'approval', id: 'appr_1' }, target), true);
    assert.equal(openHqPanel({ kind: 'approvals', taskId: 'task_1' }, target), true);
    assert.equal(openHqPanel({ kind: 'meeting', id: null }, target), true);
    assert.equal(openHqPanel({ kind: 'agent', id: 'agent_fleet' }, target), true);
    assert.equal(openHqPanel({ kind: 'task', id: 'task_1' }, target), true);
    assert.equal(openViewInHq('tasks', target), true);
    assert.equal(openViewInHq('calendar', target), false);
  } finally { setAssignHostMounted(false); }
  assert.deepEqual(sent.map((e) => e.type), [HQ_PANEL_EVENT, HQ_PANEL_EVENT, HQ_PANEL_EVENT, HQ_PANEL_EVENT, 'anum:hq-open-task', HQ_SCENE_PANEL_EVENT]);
  assert.deepEqual(sent[1].detail, { kind: 'approvals', taskId: 'task_1' });
  assert.equal(sent[5].detail, 'flow');
});

test('ids are plain record ids or nothing opens', () => {
  assert.equal(validPanel({ kind: 'task', id: 'task_1' }), true);
  assert.equal(validPanel({ kind: 'approval', id: '../admin' }), false);
  assert.equal(validPanel({ kind: 'agent', id: '' }), false);
  assert.equal(validPanel({ kind: 'meeting', id: 'm 1' }), false);
  assert.equal(validPanel({ kind: 'approvals' }), true);
  const { sent, target } = recorder();
  setAssignHostMounted(true);
  try { assert.equal(openHqPanel({ kind: 'approval', id: 'bad id!' }, target), false); } finally { setAssignHostMounted(false); }
  assert.deepEqual(sent, []);
});

test('every drawer names its full page', () => {
  assert.equal(fullPageHash({ kind: 'task', id: 'task_1' }), '#tasks');
  assert.equal(fullPageHash({ kind: 'run', id: 'run_1' }), '#tasks');
  assert.equal(fullPageHash({ kind: 'approvals' }), '#approvals');
  assert.equal(fullPageHash({ kind: 'meeting', id: 'meeting_live' }), '#office/meeting/meeting_live');
  assert.equal(fullPageHash({ kind: 'meeting' }), '#meetings');
  assert.equal(fullPageHash({ kind: 'agent', id: 'agent_fleet' }), '#agents/team/agent/agent_fleet');
});

test('records and views map to drawers; other modules stay pages', () => {
  assert.deepEqual(panelForRecord('run', 'run_1'), { kind: 'run', id: 'run_1' });
  assert.equal(panelForRecord('lead', 'lead_1'), null);
  assert.equal(panelForRecord('task', 'x y'), null);
  assert.deepEqual(panelForView('approvals'), { kind: 'approvals' });
  assert.deepEqual(panelForView('meetings'), { kind: 'meeting' });
  assert.equal(panelForView('agents'), 'team');
  assert.equal(panelForView('finance'), null);
});

test('a link keeps its href for a new tab and opens in place on a plain click', () => {
  let prevented = 0;
  const ev = (over: Record<string, unknown> = {}) => ({ preventDefault: () => { prevented += 1; }, button: 0, ...over });
  setAssignHostMounted(true);
  try {
    const click = inHqClick({ kind: 'approval', id: 'appr_1' });
    click(ev({ ctrlKey: true })); click(ev({ button: 1 }));
    assert.equal(prevented, 0);
    const realDispatch = globalThis.dispatchEvent;
    (globalThis as { dispatchEvent?: unknown }).dispatchEvent = () => true;
    try { click(ev()); } finally { (globalThis as { dispatchEvent?: unknown }).dispatchEvent = realDispatch; }
    assert.equal(prevented, 1);
    inHqClick(null)(ev());
    assert.equal(prevented, 1);
  } finally { setAssignHostMounted(false); }
});

test('the agent work log reads the Tasks list route for that agent, paged by the last id', () => {
  assert.equal(agentTasksPath('agent_fleet', null), '/api/v1/tasks?agent_id=agent_fleet&limit=15');
  assert.equal(agentTasksPath('agent fleet', 'task_9', 5), '/api/v1/tasks?agent_id=agent+fleet&limit=5&cursor=task_9');
});
