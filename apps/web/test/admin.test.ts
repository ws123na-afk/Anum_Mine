// Unit tests for the members, invitations and budget logic in src/lib/admin.ts and the
// API error handling in src/lib/errors.ts. Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { WorkspaceMember } from '@anum/contracts';
import {
  budgetMeters,
  expiryLabel,
  formatResetDate,
  formatUsd,
  invitationBody,
  invitationLink,
  invitationRequest,
  isLastActiveOwner,
  limitDraft,
  limitsBody,
  mapBudgetOverview,
  mapCreatedInvitation,
  mapMember,
  meterTone,
  parseInvitationInput,
  parseLimits,
  sortMembers,
  usagePercent,
  type ApiBudgetOverview,
} from '../src/lib/admin.ts';
import { ApiError, apiErrorFrom, budgetMessage, describeError, isBudgetExceeded, isPermissionDenied } from '../src/lib/errors.ts';

const member = (userId: string, role: WorkspaceMember['role'], active = true): WorkspaceMember => ({
  tenantId: 't', workspaceId: 'w', userId, role, active, createdAt: '2026-10-01T00:00:00Z', updatedAt: '2026-10-01T00:00:00Z',
});

const overview: ApiBudgetOverview = {
  period_start: '2026-10-01',
  resets_on: '2026-11-01',
  tenant: { budget: null, usage: { input_tokens: 0, output_tokens: 0, estimated_cost_usd: 0, calls: 0, unpriced_calls: 0 }, total_tokens: 0, exceeded: false },
  workspace: {
    budget: { scope: 'workspace', monthly_cost_limit_usd: 50, monthly_token_limit: 1000, updated_at: '2026-10-02T10:00:00Z', updated_by: 'user_owner' },
    usage: { input_tokens: 600, output_tokens: 250, estimated_cost_usd: 12.5, calls: 7, unpriced_calls: 1 },
    total_tokens: 850,
    exceeded: false,
  },
};

describe('API errors', () => {
  test('reads the error envelope: status, code, message and correlation id', () => {
    const error = apiErrorFrom(402, { error: { code: 'model_budget_exceeded', message: 'This workspace has used its monthly model budget (tokens). It resets on 2026-11-01 (UTC); an owner can raise it in Settings.', correlation_id: 'corr_1' } });
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 402);
    assert.equal(error.code, 'model_budget_exceeded');
    assert.equal(error.correlationId, 'corr_1');
    assert.match(error.message, /^ANUM API request failed: 402: This workspace/);
    assert.ok(isBudgetExceeded(error));
    assert.match(budgetMessage(error), /resets on 2026-11-01/);
    assert.match(describeError(error), /^Monthly model budget reached\. This workspace/);
  });

  test('a 402 without a body still reads as a budget refusal with a fallback sentence', () => {
    const error = apiErrorFrom(402, undefined);
    assert.ok(isBudgetExceeded(error));
    assert.match(budgetMessage(error), /monthly model budget/);
  });

  test('403 is a permission refusal and keeps the API message', () => {
    const error = apiErrorFrom(403, { error: { code: 'forbidden', message: 'permission_denied: membership:manage' } });
    assert.ok(isPermissionDenied(error));
    assert.equal(isBudgetExceeded(error), false);
    assert.equal(describeError(error), 'permission_denied: membership:manage');
  });

  test('409 last-owner refusal is shown in the API words; FastAPI detail bodies work too', () => {
    assert.equal(describeError(apiErrorFrom(409, { error: { code: 'conflict', message: 'The last active owner cannot be demoted or deactivated' } })), 'The last active owner cannot be demoted or deactivated');
    assert.equal(describeError(apiErrorFrom(404, { detail: 'Invitation not found' })), 'Invitation not found');
    assert.equal(apiErrorFrom(500, 'oops').message, 'ANUM API request failed: 500');
    assert.equal(apiErrorFrom(410, null, 'ANUM voice request failed').message, 'ANUM voice request failed: 410');
  });

  test('non-API errors fall back to their message', () => {
    assert.equal(describeError(new Error('offline')), 'offline');
    assert.equal(describeError('nope', 'Fallback.'), 'Fallback.');
    assert.equal(isBudgetExceeded(new Error('402')), false);
  });
});

describe('members', () => {
  test('maps the wire membership and treats unknown roles as viewer', () => {
    const mapped = mapMember({ tenant_id: 't', workspace_id: 'w', user_id: 'u', role: 'auditor', active: false, created_at: 'c', updated_at: 'u2' });
    assert.deepEqual(mapped, { tenantId: 't', workspaceId: 'w', userId: 'u', role: 'viewer', active: false, createdAt: 'c', updatedAt: 'u2' });
  });

  test('flags only the single active owner as last owner', () => {
    const owner = member('alice', 'owner');
    const members = [owner, member('bob', 'member'), member('carol', 'owner', false)];
    assert.equal(isLastActiveOwner(members, owner), true);
    assert.equal(isLastActiveOwner([...members, member('dave', 'owner')], owner), false);
    assert.equal(isLastActiveOwner(members, members[2]), false);
  });

  test('sorts active first, then by role, then by id', () => {
    const sorted = sortMembers([member('zed', 'viewer'), member('amy', 'member', false), member('bea', 'owner'), member('abe', 'member')]);
    assert.deepEqual(sorted.map((m) => m.userId), ['bea', 'abe', 'zed', 'amy']);
  });
});

describe('invitations', () => {
  test('builds the API body from a valid draft and defaults the expiry', () => {
    const result = invitationRequest({ role: 'member', userId: ' user_2 ', email: 'Ana@Example.com', ttlHours: '' });
    assert.ok(result.ok);
    assert.deepEqual(invitationBody(result.value), { role: 'member', invitee_user_id: 'user_2', invitee_email: 'Ana@Example.com', ttl_hours: 168 });
  });

  test('refuses drafts the API would refuse', () => {
    assert.equal(invitationRequest({ role: 'viewer', userId: '', email: '', ttlHours: '24' }).ok, false);
    assert.equal(invitationRequest({ role: 'viewer', userId: '', email: 'no-at-sign', ttlHours: '24' }).ok, false);
    assert.equal(invitationRequest({ role: 'viewer', userId: '', email: 'a@nodot', ttlHours: '24' }).ok, false);
    assert.equal(invitationRequest({ role: 'viewer', userId: 'u', email: '', ttlHours: '0' }).ok, false);
    assert.equal(invitationRequest({ role: 'viewer', userId: 'u', email: '', ttlHours: '721' }).ok, false);
    assert.equal(invitationRequest({ role: 'viewer', userId: 'u', email: '', ttlHours: '1.5' }).ok, false);
    assert.equal(invitationRequest({ role: 'viewer', userId: 'x'.repeat(121), email: '', ttlHours: '1' }).ok, false);
  });

  test('a link round-trips the token and workspace in the URL fragment', () => {
    const token = 'anum_inv_example';
    const link = invitationLink('https://anum.example/app/#tasks', token, 'workspace_sales');
    assert.equal(link, 'https://anum.example/app/#invitation=anum_inv_example&workspace=workspace_sales');
    assert.deepEqual(parseInvitationInput(link), { token, workspaceId: 'workspace_sales' });
    assert.deepEqual(parseInvitationInput(`  ${token}  `), { token, workspaceId: null });
    assert.deepEqual(parseInvitationInput('#invitation=anum_inv_x'), { token: 'anum_inv_x', workspaceId: null });
    assert.deepEqual(parseInvitationInput('https://a.example/?invitation=anum_inv_q&workspace=w2'), { token: 'anum_inv_q', workspaceId: 'w2' });
    assert.equal(parseInvitationInput('hello'), null);
    assert.equal(parseInvitationInput('https://a.example/#invitation=not-a-token'), null);
    assert.equal(parseInvitationInput(''), null);
  });

  test('maps a created invitation with its one-time token', () => {
    const created = mapCreatedInvitation({
      token: 'anum_inv_once',
      invitation: { id: 'invitation_1', tenant_id: 't', workspace_id: 'w', role: 'owner', invitee_user_id: null, invitee_email: 'a@b.co', status: 'pending', created_by_user_id: 'u', expires_at: '2026-10-13T00:00:00Z', accepted_by_user_id: null, accepted_at: null, revoked_at: null, created_at: '2026-10-06T00:00:00Z' },
    });
    assert.equal(created.token, 'anum_inv_once');
    assert.equal(created.invitation.inviteeEmail, 'a@b.co');
    assert.equal(created.invitation.role, 'owner');
  });

  test('expiry labels count down and say when an invitation lapsed', () => {
    const now = new Date('2026-10-06T12:00:00Z');
    assert.equal(expiryLabel('2026-10-13T12:00:00Z', now), 'Expires in 7 d');
    assert.equal(expiryLabel('2026-10-06T17:30:00Z', now), 'Expires in 5 h');
    assert.equal(expiryLabel('2026-10-06T12:10:00Z', now), 'Expires in 10 min');
    assert.equal(expiryLabel('2026-10-06T10:00:00Z', now), 'Expired 2 h ago');
  });
});

describe('model budgets', () => {
  test('maps the overview and builds meters with percent and tone', () => {
    const mapped = mapBudgetOverview(overview);
    assert.equal(mapped.resetsOn, '2026-11-01');
    assert.equal(mapped.workspace.usage.unpricedCalls, 1);
    const [cost, tokens] = budgetMeters(mapped.workspace);
    assert.equal(cost.percent, 25);
    assert.equal(cost.tone, 'ok');
    assert.equal(cost.usedText, '$12.50');
    assert.equal(cost.limitText, '$50.00');
    assert.equal(tokens.percent, 85);
    assert.equal(tokens.tone, 'warn');
    assert.equal(tokens.limitText, '1,000');
    const [tenantCost] = budgetMeters(mapped.tenant);
    assert.equal(tenantCost.percent, null);
    assert.equal(tenantCost.limitText, 'No limit');
    assert.equal(tenantCost.tone, 'none');
  });

  test('percent caps the bar but not the text; a zero limit reads as used up', () => {
    assert.equal(usagePercent(5, null), null);
    assert.equal(usagePercent(0, 0), 100);
    assert.equal(usagePercent(150, 100), 150);
    assert.equal(meterTone(79.9), 'ok');
    assert.equal(meterTone(80), 'warn');
    assert.equal(meterTone(100), 'stop');
    const [cost] = budgetMeters({ budget: { scope: 'tenant', monthlyCostLimitUsd: 10, monthlyTokenLimit: null, updatedAt: '', updatedBy: '' }, usage: { inputTokens: 0, outputTokens: 0, estimatedCostUsd: 15, calls: 1, unpricedCalls: 0 }, totalTokens: 0, exceeded: true });
    assert.equal(cost.percent, 100);
    assert.equal(cost.rawPercent, 150);
    assert.equal(cost.tone, 'stop');
  });

  test('limit drafts: empty means no limit, invalid values are refused', () => {
    assert.deepEqual(limitDraft(null), { cost: '', tokens: '' });
    assert.deepEqual(limitDraft({ scope: 'workspace', monthlyCostLimitUsd: 0, monthlyTokenLimit: 5000, updatedAt: '', updatedBy: '' }), { cost: '0', tokens: '5000' });
    const parsed = parseLimits({ cost: '$1,250.50', tokens: '5,000,000' });
    assert.ok(parsed.ok);
    assert.deepEqual(limitsBody(parsed.value), { monthly_cost_limit_usd: 1250.5, monthly_token_limit: 5_000_000 });
    const cleared = parseLimits({ cost: ' ', tokens: '' });
    assert.ok(cleared.ok);
    assert.deepEqual(cleared.value, { monthlyCostLimitUsd: null, monthlyTokenLimit: null });
    assert.equal(parseLimits({ cost: '-1', tokens: '' }).ok, false);
    assert.equal(parseLimits({ cost: 'ten', tokens: '' }).ok, false);
    assert.equal(parseLimits({ cost: '', tokens: '1.5' }).ok, false);
    assert.equal(parseLimits({ cost: '2000000000', tokens: '' }).ok, false);
  });

  test('formats small costs and UTC reset dates', () => {
    assert.equal(formatUsd(0.004), '< $0.01');
    assert.equal(formatUsd(0), '$0.00');
    assert.equal(formatResetDate('2026-11-01'), '1 November 2026 (UTC)');
    assert.equal(formatResetDate('garbage'), 'garbage');
  });
});
