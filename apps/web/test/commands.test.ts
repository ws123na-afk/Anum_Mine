// Unit tests for ANUM Command's pure rules (src/lib/commands.ts). docs/command.md#the-web-command-center.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { Approval, PlanStep, Task } from '@anum/contracts';
import {
  canDelete, canStart, commandLocale, commandTone, isLive, mapPlan, mapQuick, mapSummary, mapView, moveStep, quickTitle, removeStep, stoppableTasks, unstartedTasks,
  confirmedDependencies, readyToContinue,
} from '../src/lib/commands.ts';

const apiStep = (position: number, status: string, runId: string | null = null) => ({
  position, title: `Step ${position}`, instruction: `Do ${position}`, agent: { id: 'email', name: 'Email agent' }, tool: 'search_email', risk_level: 'low', approval: 'none',
  task_id: `task_${position}`, task: null, status, progress: 0, run_id: runId, result: null, started_at: null, finished_at: null, approvals: [],
});

describe('mapping the API', () => {
  test('a plan keeps every step field in camelCase', () => {
    const plan = mapPlan({
      text: 'x', language: 'ar', approvals_required: 1, blocked: 0, agents: [{ id: 'calendar', name: 'Calendar agent' }],
      steps: [{ position: 1, title: 't', instruction: 'i', agent: { id: 'calendar', name: 'Calendar agent' }, skills: [], tool: { name: 'draft', description: 'd', category: null, action_kind: 'write', target: null }, risk_level: 'medium', approval: 'required', approval_reason: 'r', connected: false, note: 'n' }],
    });
    assert.equal(plan.language, 'ar');
    assert.equal(plan.approvalsRequired, 1);
    assert.deepEqual(plan.steps[0].tool, { name: 'draft', description: 'd', category: null, actionKind: 'write', target: null });
    assert.equal(plan.steps[0].connected, false);
    assert.equal(plan.steps[0].approval, 'required');
  });
  test('summaries, views and quick commands', () => {
    const raw = { id: 'cmd', text: 't', source: 'typed', locale: 'en', status: 'running', progress: 0.5, step_count: 2, completed_steps: 1, pending_approvals: 0, agents: [], created_at: 'a', updated_at: 'b' };
    assert.deepEqual(mapSummary(raw), { id: 'cmd', text: 't', source: 'typed', locale: 'en', status: 'running', progress: 0.5, stepCount: 2, completedSteps: 1, pendingApprovals: 0, agents: [], createdAt: 'a', updatedAt: 'b' });
    const view = mapView({ ...raw, created_by: 'u', steps: [apiStep(1, 'completed', 'run_1')], pending: [{ id: 'ap' }], timeline: [], files: [{ kind: 'run_log', name: 'log', path: '/p', file_id: null, task_id: 'task_1', content_type: null }], attachments: [], duration_seconds: 4 },
      ((t: unknown) => t as Task) as never, ((a: { id: string }) => ({ id: a.id }) as Approval) as never);
    assert.equal(view.steps[0].taskId, 'task_1');
    assert.equal(view.steps[0].runId, 'run_1');
    assert.deepEqual(view.pending.map((a) => a.id), ['ap']);
    assert.equal(view.files[0].kind, 'run_log');
    assert.equal(view.durationSeconds, 4);
    assert.equal(mapQuick({ id: 'q', title: 'T', text: 'x', category: 'odd', position: 1 }).category, 'custom');
  });
});

describe('the plan editor', () => {
  const steps = [{ approval: 'none' }, { approval: 'required' }, { approval: 'none' }] as PlanStep[];
  test('move and remove keep the rest in order', () => {
    assert.deepEqual(moveStep(['a', 'b', 'c'], 2, -1), ['a', 'c', 'b']);
    assert.deepEqual(moveStep(['a', 'b'], 0, -1), ['a', 'b']);
    assert.deepEqual(removeStep(['a', 'b', 'c'], 1), ['a', 'c']);
  });
  test('a plan starts only with steps and nothing blocked', () => {
    assert.ok(canStart(steps));
    assert.ok(!canStart([]));
    assert.ok(!canStart([...steps, { approval: 'blocked' } as PlanStep]));
  });
});

describe('a running command', () => {
  test('which steps to start and which to stop', () => {
    const view = mapView({ steps: [apiStep(1, 'created'), apiStep(2, 'running', 'r2'), apiStep(3, 'completed', 'r3'), apiStep(4, 'waiting_approval', 'r4')] }, ((t: unknown) => t) as never, ((a: unknown) => a) as never);
    assert.deepEqual(unstartedTasks(view), ['task_1']);
    assert.deepEqual(stoppableTasks(view), ['task_1', 'task_2', 'task_4']);
  });
  test('live, removable and tones by status', () => {
    assert.ok(isLive('running') && isLive('ready') && !isLive('waiting_approval') && !isLive('completed'));
    assert.ok(canDelete('completed') && canDelete('failed') && !canDelete('running') && !canDelete('waiting_approval'));
    assert.equal(commandTone('completed'), 'success');
    assert.equal(commandTone('partial'), 'dangerTone');
    assert.equal(commandTone('waiting_approval'), 'warning');
    assert.equal(commandTone('created'), 'neutral');
  });
  test('the planning language follows the text, else the interface', () => {
    assert.equal(commandLocale('تحقق من بريدي', 'en'), 'ar');
    assert.equal(commandLocale('Check my email', 'ar'), 'en');
    assert.equal(commandLocale('123', 'ar'), 'ar');
  });
  test('a quick command title is the first line, capped', () => {
    assert.equal(quickTitle('Line one\nLine two'), 'Line one');
    assert.equal(quickTitle('x'.repeat(80)).length, 60);
  });
});

describe('steps that build on each other', () => {
  const planned = (position: number, dependsOn: number[]) => ({ position, dependsOn });
  test('the preview and the live view carry the dependencies', () => {
    const plan = mapPlan({ text: 'x', steps: [{ position: 2, depends_on: [1, 'x', 1.5] }, { position: 1 }] });
    assert.deepEqual(plan.steps.map((s) => s.dependsOn), [[1], []]);
    const view = mapView({ steps: [{ ...apiStep(2, 'created'), depends_on: [1], waiting_for: [1], blocked_by: [] }] }, (t) => t as Task, (a) => a as Approval);
    assert.deepEqual([view.steps[0].dependsOn, view.steps[0].waitingFor, view.steps[0].blockedBy], [[1], [1], []]);
  });
  test('moving or removing steps keeps only waits for earlier steps still in the plan', () => {
    assert.deepEqual(confirmedDependencies([planned(1, []), planned(2, [1]), planned(3, [1, 2])]), [[], [1], [1, 2]]);
    // Step 3 moved first: it can no longer wait for what now comes after it.
    assert.deepEqual(confirmedDependencies([planned(3, [1, 2]), planned(1, []), planned(2, [1])]), [[], [], [2]]);
    // Step 1 removed: its wait disappears, the rest renumber.
    assert.deepEqual(confirmedDependencies([planned(2, [1]), planned(3, [1, 2])]), [[], [1]]);
  });
  test('a waiting step is continued once nothing is left to wait for', () => {
    const step = (status: string, dependsOn: number[], waitingFor: number[], runId: string | null = null) =>
      ({ status, dependsOn, waitingFor, blockedBy: [], runId }) as never;
    assert.equal(readyToContinue({ steps: [step('completed', [], [], 'r1'), step('created', [1], [])] }), true);
    assert.equal(readyToContinue({ steps: [step('running', [], [], 'r1'), step('created', [1], [1])] }), false);
    // A step with no dependency that never started is not this rule's business (Start already ran it).
    assert.equal(readyToContinue({ steps: [step('created', [], [])] }), false);
  });
});
