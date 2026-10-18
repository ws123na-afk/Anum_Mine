// Unit tests for AI on the Email Center (src/lib/mailAi.ts, docs/email.md#ai-on-email).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { ApiError } from '../src/lib/errors.ts';
import { MAIL_CATEGORIES, assistFailure, categoryLabel, cleanInstructions, priorityLabel, priorityTone } from '../src/lib/mailAi.ts';

describe('assistFailure', () => {
  test('a refused grant or role reads as permission denied with the server sentence', () => {
    const found = assistFailure(new ApiError(403, 'forbidden', 'Drafting from this mailbox needs send access from its owner.'));
    assert.deepEqual(found, { kind: 'denied', text: 'Drafting from this mailbox needs send access from its owner.' });
    assert.equal(assistFailure(new ApiError(403, 'forbidden', null)).kind, 'denied');
  });
  test('no model set up reads as unavailable; other conflicts and outages are errors', () => {
    assert.equal(assistFailure(new ApiError(409, 'conflict', 'No AI model is set up for this workspace.')).kind, 'unavailable');
    assert.equal(assistFailure(new ApiError(409, 'conflict', 'The Email Center is turned off on this server.')).kind, 'error');
    const outage = assistFailure(new ApiError(503, 'service_unavailable', 'The AI model did not answer. Try again in a minute.'));
    assert.deepEqual(outage, { kind: 'error', text: 'The AI model did not answer. Try again in a minute.' });
    assert.equal(assistFailure(new Error('network down')).kind, 'error');
  });
  test('a used-up model budget names the budget', () => {
    assert.match(assistFailure(new ApiError(402, 'model_budget_exceeded', 'Monthly budget used.')).text, /budget/i);
  });
});

describe('labels', () => {
  test('every category and priority has words, and high priority stands out', () => {
    for (const category of MAIL_CATEGORIES) assert.ok(categoryLabel(category) && !categoryLabel(category).startsWith('mailai.'));
    assert.equal(priorityLabel('high'), 'High priority');
    assert.equal(priorityTone('high'), 'dangerTone');
    assert.equal(priorityTone('normal'), 'warning');
    assert.equal(priorityTone('low'), 'neutral');
  });
  test('instructions are trimmed, bounded and dropped when empty', () => {
    assert.equal(cleanInstructions('   '), undefined);
    assert.equal(cleanInstructions('  say thanks '), 'say thanks');
    assert.equal(cleanInstructions('x'.repeat(1500))?.length, 1000);
  });
});
