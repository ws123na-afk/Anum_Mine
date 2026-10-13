// Unit tests for approval rules with AED bands and the routing words on approval cards
// (src/lib/approvalRules.ts, docs/approvals-and-risk.md#approval-routing).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { Approval, ApprovalRouting } from '@anum/contracts';
import {
  approverSetLabel, draftFromRule, emptyRuleDraft, formatAed, mayApprove, mayReject, parseAed, reasonText, routingView, ruleBand, ruleBody, ruleSteps,
  stepLabel, suppressedText, type ApprovalRule,
} from '../src/lib/approvalRules.ts';
import { setLocale } from '../src/lib/i18n.ts';

const rule = (overrides: Partial<ApprovalRule> = {}): ApprovalRule => ({
  id: 'r1', name: 'Refunds up to AED 250', action_pattern: '*refund*', minimum_approvers: 1, required_roles: ['owner'], enabled: true,
  created_at: '2026-10-12T00:00:00Z', conditions: { amount_at_most: 250, disputed: 'no' }, approver_steps: [{ approvers: ['owner', 'user:care_lead'] }], ...overrides,
});

function approval(routing: Partial<ApprovalRouting> | null, overrides: Partial<Approval> = {}): Approval {
  return {
    id: 'a1', taskId: 't1', action: 'commerce.own_store.refund_order', riskLevel: 'high', status: 'pending', reason: 'Payment', createdAt: '2026-10-12T10:00:00Z',
    payloadHash: 'a'.repeat(64),
    routing: routing === null ? null : {
      routed: true, rules: ['Refunds above AED 250'], steps: [{ approvers: [['owner']], approvedBy: null }], currentStep: 0, fallback: false,
      reasons: [{ code: 'amount_above', rule: 'Refunds above AED 250', applies: true, currency: 'AED', amount: '300', total: '300', threshold: '250', related: 0 }],
      canApprove: false, canReject: false, refusal: 'not_listed', summary: '', ...routing,
    },
    ...overrides,
  };
}

describe('rule form', () => {
  test('AED amounts accept commas and two decimals only', () => {
    assert.equal(parseAed('5,000'), 5000);
    assert.equal(parseAed('AED 250.50'), 250.5);
    assert.equal(parseAed(''), null);
    assert.equal(parseAed('2.505'), 'invalid');
    assert.equal(parseAed('-1'), 'invalid');
    assert.equal(parseAed('100000001'), 'invalid');
  });

  test('a band, a share of the order and two steps become the API body', () => {
    const draft = { ...emptyRuleDraft(), name: ' Commitments ', actionPattern: '*.purchase_*', amountAbove: '5,000', percentAtMost: '',
      first: { owner: false, orgAdmin: false, members: false, people: 'finance_reviewer' }, twoSteps: true };
    assert.deepEqual(ruleBody(draft), { ok: true, value: {
      name: 'Commitments', action_pattern: '*.purchase_*', minimum_approvers: 1, required_roles: ['owner'], enabled: true,
      conditions: { currency: 'AED', disputed: 'any', amount_above: 5000 },
      approver_steps: [{ approvers: ['user:finance_reviewer'] }, { approvers: ['owner'] }],
    } });
  });

  test('every problem is named', () => {
    const base = { ...emptyRuleDraft(), name: 'r', actionPattern: '*refund*' };
    assert.equal(ruleBody({ ...base, name: ' ' }).ok, false);
    assert.equal(ruleBody({ ...base, actionPattern: '' }).ok, false);
    assert.equal(ruleBody({ ...base, amountAbove: '500', amountAtMost: '250' }).ok, false);
    assert.equal(ruleBody({ ...base, amountAtMost: 'lots' }).ok, false);
    assert.equal(ruleBody({ ...base, percentAtMost: '150' }).ok, false);
    assert.equal(ruleBody({ ...base, amountField: 'a b' }).ok, false);
    assert.equal(ruleBody({ ...base, first: { owner: false, orgAdmin: false, members: false, people: '' } }).ok, false);
    assert.equal(ruleBody({ ...base, first: { owner: false, orgAdmin: false, members: false, people: 'bad id!' } }).ok, false);
    assert.equal(ruleBody({ ...base, twoSteps: true, second: { owner: false, orgAdmin: false, members: false, people: '' } }).ok, false);
  });

  test('a stored rule opens in the editor and saves back unchanged', () => {
    const stored = rule();
    const body = ruleBody(draftFromRule(stored));
    assert.ok(body.ok);
    assert.deepEqual(body.value.conditions, { currency: 'AED', disputed: 'no', amount_at_most: 250 });
    assert.deepEqual(body.value.approver_steps, [{ approvers: ['owner', 'user:care_lead'] }]);
  });

  test('the list says the band and who approves', () => {
    setLocale('en');
    assert.equal(ruleBand(rule().conditions), 'Up to AED 250 · Not disputed');
    assert.equal(ruleBand({ amount_above: 250, amount_at_most: 5000 }), 'Above AED 250, up to AED 5,000');
    assert.equal(ruleBand({}), null);
    assert.deepEqual(ruleSteps(rule()), ['Owners or care_lead']);
    assert.deepEqual(ruleSteps(rule({ approver_steps: [{ approvers: ['role:member'] }, { approvers: ['owner'] }] })), ['Step 1: Members', 'Step 2: Owners']);
    assert.deepEqual(ruleSteps(rule({ approver_steps: [], minimum_approvers: 3 })), ['3 different owners (owner)']);
  });
});

describe('approval cards', () => {
  test('a refund above the band reads "AED 300 is more than AED 250: owners only"', () => {
    setLocale('en');
    const view = routingView(approval({}));
    assert.ok(view);
    assert.deepEqual(view.why, ['Refunds above AED 250: AED 300 is more than AED 250']);
    assert.equal(view.who, 'Owners only');
    assert.equal(view.explanation, 'Read only: Owners only can approve this. You can see it but not decide it.');
    assert.equal(mayApprove(approval({})), false);
  });

  test('two steps show who approved and who is next', () => {
    setLocale('en');
    const view = routingView(approval({ steps: [{ approvers: [['user:finance_reviewer']], approvedBy: 'finance_reviewer' }, { approvers: [['owner']], approvedBy: null }], currentStep: 1, canApprove: true, canReject: true, refusal: null }));
    assert.ok(view);
    assert.equal(view.step, 'Step 2 of 2');
    assert.deepEqual(view.steps.map((s) => [s.who, s.approvedBy, s.current]), [['finance_reviewer only', 'finance_reviewer', false], ['Owners only', null, true]]);
    assert.equal(view.explanation, null);
  });

  test('unrouted approvals keep the owner behaviour and other reasons are worded', () => {
    assert.equal(routingView(approval(null)), null);
    assert.equal(routingView(approval({ routed: false })), null);
    assert.equal(mayApprove(approval(null)), true);
    assert.equal(mayReject(approval({ canReject: true })), true);
    setLocale('en');
    assert.equal(reasonText({ code: 'amount_at_most', rule: 'Small', applies: true, currency: 'AED', total: '300', threshold: '250', related: 1, amount: '150' }),
      'Small: AED 300 is within AED 250 (with 1 other amount in this task)');
    assert.equal(reasonText({ code: 'no_band', applies: true, currency: 'AED', related: 0 }), 'No delegated band applies, so owners decide.');
    assert.equal(reasonText({ code: 'rule', applies: true, currency: 'AED', related: 0 }), null);
    assert.equal(approverSetLabel(['owner', 'role:member', 'user:u1']), 'Owners, Members or u1');
    assert.equal(stepLabel([['owner'], ['role:member']]), 'Owners and Members');
  });

  test('suppressed recipients are named and Arabic reads naturally', () => {
    setLocale('en');
    assert.match(suppressedText(approval(null, { suppressed: [{ recipient: '+971•••4567', channel: 'whatsapp', purpose: 'all' }] })) ?? '', /\+971•••4567 asked not to be messaged/);
    assert.equal(suppressedText(approval(null)), null);
    setLocale('ar');
    assert.equal(formatAed(5000), '5,000 درهم');
    assert.ok(routingView(approval({}))?.who.includes('المالكون'));
    setLocale('en');
  });
});
