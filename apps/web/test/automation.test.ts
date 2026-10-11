// Unit tests for the automation workbench helpers (src/lib/automation.ts).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { cronLabel, draftsFromSteps, intervalCron, moveStep, newStep, nextRunLabel, parseStepInput, removeStep, workflowBody, type StepDraft } from '../src/lib/automation.ts';

const draft = (id: string, patch: Partial<StepDraft> = {}): StepDraft => ({ id, name: `Name ${id}`, action: 'task.execute', input: '{}', maxAttempts: 3, ...patch });

describe('workflow steps', () => {
  test('newStep picks an unused id', () => {
    assert.equal(newStep([draft('step-2')]).id, 'step-3');
    assert.equal(newStep([]).id, 'step-1');
  });
  test('moveStep swaps and ignores out-of-range moves', () => {
    assert.deepEqual(moveStep(['a', 'b', 'c'], 0, 1), ['b', 'a', 'c']);
    assert.deepEqual(moveStep(['a', 'b'], 0, -1), ['a', 'b']);
  });
  test('removeStep keeps the last step', () => {
    assert.deepEqual(removeStep(['a', 'b'], 0), ['b']);
    assert.deepEqual(removeStep(['a'], 0), ['a']);
  });
  test('parseStepInput accepts objects only', () => {
    assert.deepEqual(parseStepInput(' '), {});
    assert.deepEqual(parseStepInput('{"message":"x"}'), { message: 'x' });
    assert.throws(() => parseStepInput('[1]'), /JSON object/);
    assert.throws(() => parseStepInput('{bad'));
  });
  test('workflowBody builds the API body and validates', () => {
    const body = workflowBody(' Review ', '', [draft('a'), draft('b', { action: 'fail', input: '{"message":"stop"}', maxAttempts: 40 })]);
    assert.deepEqual(body.steps[1], { id: 'b', name: 'Name b', action: 'fail', input: { message: 'stop' }, max_attempts: 10 });
    assert.equal(body.name, 'Review');
    assert.throws(() => workflowBody('x', '', [draft('a'), draft('a')]), /used twice/);
    assert.throws(() => workflowBody('', '', [draft('a')]), /name/);
    assert.throws(() => workflowBody('x', '', [draft('a', { input: 'nope' })]), /Step 1 input/);
  });
  test('draftsFromSteps round-trips', () => {
    const steps = [{ id: 'a', name: 'A', action: 'notify', input: { to: 'ops' }, max_attempts: 2 }];
    assert.deepEqual(workflowBody('w', '', draftsFromSteps(steps)).steps, steps);
  });
});

describe('schedules', () => {
  test('intervalCron expands intervals to cron', () => {
    assert.equal(intervalCron(15), '*/15 * * * *');
    assert.equal(intervalCron(60), '0 * * * *');
    assert.equal(intervalCron(360), '0 */6 * * *');
    assert.equal(intervalCron(1440), '0 0 * * *');
    assert.throws(() => intervalCron(0));
    assert.throws(() => intervalCron(420));
  });
  test('cronLabel names presets', () => {
    assert.equal(cronLabel('0 9 * * 1-5'), 'Weekdays at 09:00');
    assert.equal(cronLabel('5 4 * * *'), '5 4 * * *');
  });
  test('nextRunLabel is human readable', () => {
    const now = new Date('2026-10-11T08:00:00Z');
    assert.equal(nextRunLabel('2026-10-11T09:00:00Z', false, now), 'Paused');
    assert.equal(nextRunLabel(null, true, now), 'Not scheduled');
    assert.equal(nextRunLabel('2026-10-11T08:20:00Z', true, now), 'In 20 min (2026-10-11 08:20 UTC)');
    assert.equal(nextRunLabel('2026-10-11T11:00:00Z', true, now), 'In 3 h (2026-10-11 11:00 UTC)');
    assert.equal(nextRunLabel('2026-10-15T08:00:00Z', true, now), 'In 4 days (2026-10-15 08:00 UTC)');
    assert.match(nextRunLabel('2026-10-11T07:00:00Z', true, now), /^Due now/);
  });
});
