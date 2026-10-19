// Unit tests for the paged approval list helpers in src/lib/approvalPaging.ts.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { Approval } from '@anum/contracts';
import { APPROVAL_SEARCH_MAX_CHARS, approvalSignal, collectPages, emptyKind, mergeApprovals, searchText, splitApprovals, summaryTotal } from '../src/lib/approvalPaging.ts';

const now = new Date('2026-10-06T12:00:00Z');

function approval(id: string, overrides: Partial<Approval> = {}): Approval {
  return {
    id, taskId: 'task_1', action: 'send_email', riskLevel: 'high', status: 'pending', reason: 'Needs a person',
    createdAt: '2026-10-06T09:00:00Z', runId: null, stepId: null, arguments: {}, payloadHash: 'a'.repeat(64), expiresAt: null,
    decidedAt: null, decidedBy: null, decisionReason: null, requestedBy: null, target: null, requiredApprovals: 1,
    approvers: [], routing: null, suppressed: [], ...overrides,
  } as Approval;
}

describe('mergeApprovals', () => {
  test('appends a page and keeps the first copy of an id', () => {
    const first = [approval('a'), approval('b')];
    const merged = mergeApprovals(first, [approval('b', { reason: 'newer copy' }), approval('c')]);
    assert.deepEqual(merged.map((item) => item.id), ['a', 'b', 'c']);
    assert.equal(merged[1]?.reason, 'Needs a person');
  });
});

describe('splitApprovals', () => {
  test('a lapsed pending approval counts as history and the server order is kept', () => {
    const items = [
      approval('p1'),
      approval('lapsed', { expiresAt: '2026-10-06T11:00:00Z' }),
      approval('done', { status: 'approved' }),
    ];
    const { pending, history } = splitApprovals(items, now);
    assert.deepEqual(pending.map((item) => item.id), ['p1']);
    assert.deepEqual(history.map((item) => item.id), ['lapsed', 'done']);
  });
});

describe('emptyKind', () => {
  test('search wins over the filter', () => {
    assert.equal(emptyKind('pending', ' invoice '), 'search');
    assert.equal(emptyKind('pending', ''), 'pending');
    assert.equal(emptyKind('decided', '   '), 'decided');
    assert.equal(emptyKind('all', ''), 'none');
  });
});

describe('searchText and summaryTotal', () => {
  test('search text is trimmed and capped', () => {
    assert.equal(searchText('  hello '), 'hello');
    assert.equal(searchText('x'.repeat(500)).length, APPROVAL_SEARCH_MAX_CHARS);
  });
  test('totals follow the filter', () => {
    const summary = { pending: 2, approved: 3, rejected: 1, expired: 4 };
    assert.equal(summaryTotal(summary, 'pending'), 2);
    assert.equal(summaryTotal(summary, 'decided'), 8);
    assert.equal(summaryTotal(summary, 'all'), 10);
    assert.equal(summaryTotal(null, 'all'), null);
  });
});

describe('approvalSignal', () => {
  test('changes when step one of a chain is completed by someone else, though the status stays pending', () => {
    const before = approval('chain', { requiredApprovals: 2, approvers: [], routing: { currentStep: 0, canApprove: true, canReject: true } as Approval['routing'] });
    const after = approval('chain', {
      requiredApprovals: 2,
      approvers: [{ userId: 'owner_b', approvedAt: '2026-10-06T10:00:00Z', reason: null }],
      routing: { currentStep: 1, canApprove: false, canReject: true, refusal: 'not_listed' } as Approval['routing'],
    });
    assert.equal(before.status, after.status);
    assert.notEqual(approvalSignal([before]), approvalSignal([after]));
  });
  test('is stable for identical data and changes for a new approval', () => {
    assert.equal(approvalSignal([approval('a')]), approvalSignal([approval('a')]));
    assert.notEqual(approvalSignal([approval('a')]), approvalSignal([approval('a'), approval('b')]));
  });
});

describe('collectPages', () => {
  test('follows the cursor until the list ends, so a task with more than one page keeps every approval', async () => {
    const pages: Record<string, { items: number[]; nextCursor: string | null }> = {
      start: { items: [1, 2], nextCursor: 'p2' },
      p2: { items: [3, 4], nextCursor: 'p3' },
      p3: { items: [5], nextCursor: null },
    };
    const seen: (string | null)[] = [];
    const all = await collectPages<number>(async (cursor) => { seen.push(cursor); return pages[cursor ?? 'start']!; });
    assert.deepEqual(all.items, [1, 2, 3, 4, 5]);
    assert.equal(all.complete, true);
    assert.deepEqual(seen, [null, 'p2', 'p3']);
  });
  test('stops at the page cap and says it is incomplete', async () => {
    const all = await collectPages<number>(async () => ({ items: [1], nextCursor: 'more' }), 3);
    assert.equal(all.items.length, 3);
    assert.equal(all.complete, false);
  });
});
