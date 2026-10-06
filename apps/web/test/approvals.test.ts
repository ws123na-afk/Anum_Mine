// Unit tests for the approval display and decision helpers in src/lib/approvals.ts.
// Run with `pnpm --filter @anum/web test:unit` (Node's test runner with type stripping).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { Approval } from '@anum/contracts';
import {
  REDACTED,
  argumentRows,
  canApprove,
  decisionBody,
  decisionSummary,
  effectiveStatus,
  expiryLabel,
  normalizeReason,
  REASON_MAX_CHARS,
  shortHash,
  approvalProgress,
  hasApproved,
  joinNames,
} from '../src/lib/approvals.ts';

const hash = 'a'.repeat(52) + 'b'.repeat(12);
const now = new Date('2026-10-06T12:00:00Z');

function approval(overrides: Partial<Approval> = {}): Approval {
  return {
    id: 'approval_1',
    taskId: 'task_1',
    action: 'external.action',
    riskLevel: 'high',
    status: 'pending',
    reason: 'External or high-impact actions require explicit approval.',
    createdAt: '2026-10-06T11:00:00Z',
    arguments: {
      action: 'Send the quarterly summary',
      planned_response: 'Line one\nLine two',
      api_key: REDACTED,
      headers: { accept: 'application/json' },
      recipients: ['a@example.com', 'b@example.com'],
    },
    payloadHash: hash,
    expiresAt: '2026-10-07T11:00:00Z',
    decidedAt: null,
    decidedBy: null,
    ...overrides,
  };
}

describe('approval arguments (A1)', () => {
  test('every argument is a row, nested keys flattened, secrets flagged', () => {
    const rows = argumentRows(approval().arguments);
    assert.deepEqual(rows.map((row) => row.key), [
      'action',
      'api_key',
      'headers.accept',
      'planned_response',
      'recipients[0]',
      'recipients[1]',
    ]);
    const byKey = Object.fromEntries(rows.map((row) => [row.key, row]));
    assert.equal(byKey.action.value, 'Send the quarterly summary');
    assert.equal(byKey.api_key.redacted, true);
    assert.equal(byKey.action.redacted, false);
    assert.equal(byKey.planned_response.multiline, true);
    assert.equal(byKey.planned_response.value, 'Line one\nLine two');
  });

  test('empty and scalar edge cases stay visible', () => {
    assert.deepEqual(argumentRows({}), []);
    assert.deepEqual(argumentRows(undefined), []);
    const rows = argumentRows({ list: [], obj: {}, none: null, n: 3, flag: false });
    assert.deepEqual(
      rows.map((row) => [row.key, row.value]),
      [['flag', 'false'], ['list', '[]'], ['n', '3'], ['none', 'null'], ['obj', '{}']],
    );
  });
});

describe('approval decision binding (A2)', () => {
  test('approving sends exactly the displayed payload hash', () => {
    assert.deepEqual(decisionBody(approval()), { payload_hash: hash });
    assert.equal(decisionBody(approval({ payloadHash: null })), undefined);
  });

  test('an approval without a hash cannot be approved', () => {
    assert.equal(canApprove(approval(), now), true);
    assert.equal(canApprove(approval({ payloadHash: null }), now), false);
  });

  test('the hash is shortened for display only', () => {
    assert.equal(shortHash(hash), `${'a'.repeat(12)}…${'b'.repeat(6)}`);
    assert.equal(shortHash(null), 'not bound');
  });
});

describe('approval expiry and decider (A3)', () => {
  test('a pending approval past its expiry reads as expired and cannot be approved', () => {
    const lapsed = approval({ expiresAt: '2026-10-06T11:59:59Z' });
    assert.equal(effectiveStatus(lapsed, now), 'expired');
    assert.equal(canApprove(lapsed, now), false);
    assert.equal(expiryLabel(lapsed, now), 'Expired');
    assert.equal(effectiveStatus(approval({ status: 'approved', expiresAt: '2026-10-06T11:00:00Z' }), now), 'approved');
  });

  test('expiry label counts down', () => {
    assert.equal(expiryLabel(approval({ expiresAt: '2026-10-06T12:30:00Z' }), now), 'Expires in 30 min');
    assert.equal(expiryLabel(approval(), now), 'Expires in 23 h');
    assert.equal(expiryLabel(approval({ expiresAt: '2026-10-10T12:00:00Z' }), now), 'Expires in 4 d');
    assert.equal(expiryLabel(approval({ status: 'approved' }), now), '');
  });

  test('history shows who decided and when', () => {
    const format = (iso: string) => `at ${iso}`;
    assert.equal(
      decisionSummary(approval({ status: 'approved', decidedBy: 'user_owner', decidedAt: '2026-10-06T11:30:00Z' }), format, now),
      'Approved by user_owner · at 2026-10-06T11:30:00Z',
    );
    assert.equal(
      decisionSummary(approval({ status: 'rejected', decidedBy: 'user_2', decidedAt: '2026-10-06T11:31:00Z' }), format, now),
      'Rejected by user_2 · at 2026-10-06T11:31:00Z',
    );
    assert.equal(
      decisionSummary(approval({ status: 'expired', decidedAt: '2026-10-07T11:00:00Z' }), format, now),
      'Expired without a decision · at 2026-10-07T11:00:00Z',
    );
    assert.equal(decisionSummary(approval(), format, now), '');
  });
});

describe('decision reason (A4)', () => {
  test('the optional reason is trimmed and sent with the hash', () => {
    assert.deepEqual(decisionBody(approval(), '  Checked with legal \r\n twice '), {
      payload_hash: hash,
      reason: 'Checked with legal \n twice',
    });
    assert.deepEqual(decisionBody(approval(), '   '), { payload_hash: hash });
  });

  test('rejecting an unbound approval may send only a reason', () => {
    assert.deepEqual(decisionBody(approval({ payloadHash: null }), 'No'), { reason: 'No' });
  });

  test('reasons are capped at the API limit and blank means none', () => {
    assert.equal(normalizeReason(undefined), undefined);
    assert.equal(normalizeReason(''), undefined);
    assert.equal(normalizeReason('x'.repeat(REASON_MAX_CHARS + 20))?.length, REASON_MAX_CHARS);
  });
});

describe('approval chains', () => {
  const approvers = (...ids: string[]) => ids.map((userId, index) => ({ userId, approvedAt: `2026-10-06T11:3${index}:00Z`, reason: null }));

  test('a single-decision approval shows no progress', () => {
    assert.equal(approvalProgress(approval()), null);
    assert.equal(approvalProgress(approval({ requiredApprovals: 1, approvers: approvers('owner_b') })), null);
  });

  test('a chain shows how many approved and who', () => {
    const partial = approval({ requiredApprovals: 3, approvers: approvers('owner_b') });
    assert.deepEqual(approvalProgress(partial), { collected: 1, required: 3, label: '1 of 3 approvals', approvers: ['owner_b'] });
    assert.equal(approvalProgress(approval({ requiredApprovals: 3 }))?.label, '0 of 3 approvals');
    assert.equal(hasApproved(partial, 'owner_b'), true);
    assert.equal(hasApproved(partial, 'owner_c'), false);
    assert.equal(hasApproved(partial, null), false);
  });

  test('history names every approver of a completed chain', () => {
    const done = approval({ status: 'approved', decidedBy: 'owner_d', decidedAt: '2026-10-06T11:40:00Z', requiredApprovals: 3, approvers: approvers('owner_b', 'owner_c', 'owner_d') });
    assert.equal(decisionSummary(done, (iso) => iso, now), 'Approved by owner_b, owner_c and owner_d · 2026-10-06T11:40:00Z');
    assert.equal(approvalProgress(done)?.label, '3 of 3 approvals');
    assert.equal(joinNames(['a', 'b']), 'a and b');
    assert.equal(joinNames([]), '');
  });
});
