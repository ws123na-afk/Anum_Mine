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

import { ARCHIVE_MAX, EXPORT_MAX, capSelection, exportFailure, exportFileName, mergeLabels, rowLabelText, tidyCandidates, unlabelled, type MailRowLabel } from '../src/lib/mailAi.ts';

const label = (uid: number, category: MailRowLabel['category'], priority: MailRowLabel['priority'] = 'normal'): MailRowLabel => ({ uid, category, priority, reason: '', source: 'model' });

describe('list labels', () => {
  test('Label all sends only unlabelled rows, newest first, at most 25', () => {
    const labels = new Map([[9, label(9, 'order')]]);
    assert.deepEqual(unlabelled([10, 9, 8], labels), [10, 8]);
    assert.equal(unlabelled(Array.from({ length: 40 }, (_, i) => 100 - i), new Map()).length, 25);
  });
  test('the tidy-up picks newsletters, notifications and spam that are not high priority', () => {
    const labels = mergeLabels(new Map(), [label(1, 'newsletter', 'low'), label(2, 'notification', 'high'), label(3, 'customer'), label(4, 'spam')]);
    assert.deepEqual(tidyCandidates([1, 2, 3, 4, 5], labels), [1, 4]);
  });
  test('row words skip a normal priority', () => {
    assert.deepEqual(rowLabelText(label(1, 'invoice')), ['Invoice']);
    assert.deepEqual(rowLabelText(label(1, 'invoice', 'high')), ['Invoice', 'High priority']);
  });
  test('merging keeps earlier labels and replaces changed ones', () => {
    const merged = mergeLabels(new Map([[1, label(1, 'order')]]), [label(1, 'spam'), label(2, 'other')]);
    assert.equal(merged.get(1)?.category, 'spam');
    assert.equal(merged.size, 2);
  });
});

describe('bulk archive and export', () => {
  test('a selection is capped to what one request may carry', () => {
    assert.deepEqual(capSelection([3, 1, 2, 3], 2), { uids: [3, 2], dropped: 1 });
    assert.equal(capSelection(Array.from({ length: 150 }, (_, i) => i + 1), EXPORT_MAX).dropped, 50);
    assert.equal(capSelection(Array.from({ length: 150 }, (_, i) => i + 1), ARCHIVE_MAX).dropped, 0);
  });
  test('the export file is named by the UTC time', () => {
    assert.equal(exportFileName(new Date(Date.UTC(2026, 9, 18, 7, 5))), 'mail-export-20261018-0705.mbox');
  });
  test('download failures say whether the export expired or is not approved yet', () => {
    assert.equal(exportFailure(new ApiError(410, 'gone', null)).kind, 'expired');
    assert.match(exportFailure(new ApiError(410, 'gone', null)).text, /expired/);
    assert.equal(exportFailure(new ApiError(409, 'conflict', 'This export is pending, not approved.')).kind, 'pending');
    assert.equal(exportFailure(new Error('network down')).kind, 'error');
  });
});
