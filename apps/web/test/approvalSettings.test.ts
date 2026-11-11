import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import { setLocale } from '../src/lib/i18n.ts';
import { routingSummary, rulesCount, sectionFromHash } from '../src/lib/approvalSettings.ts';
import type { ApprovalRule } from '../src/lib/approvalRules.ts';
import { NAV, VIEWS, viewFromHash, visibleViews } from '../src/lib/shell.ts';

afterEach(() => setLocale('en'));

// The Approvals settings page (docs/approvals-and-risk.md#approvals-settings).
const rule = (over: Partial<ApprovalRule> = {}): ApprovalRule => ({
  id: 'rule_1', name: 'Refunds above 250', action_pattern: 'commerce.refund', minimum_approvers: 1, required_roles: ['owner'], enabled: true, created_at: '2026-10-01T00:00:00Z',
  conditions: { amount_above: 250 }, approver_steps: [{ approvers: ['owner'] }], ...over,
});
const policy = { twoPersonRule: true, mediumRiskRequiresApproval: false, updatedBy: null, updatedAt: null };

describe('routing summary', () => {
  test('bands come from the rules that are on, in words, with the policy switches', () => {
    const summary = routingSummary([rule(), rule({ id: 'rule_2', name: 'Small refunds', enabled: false, conditions: { amount_at_most: 250 }, approver_steps: [{ approvers: ['role:member'] }] })], policy);
    assert.equal(summary.bands.length, 1);
    assert.equal(summary.bands[0].id, 'rule_1');
    assert.match(summary.bands[0].text, /^Refunds above 250: .*AED 250.*Owners/);
    assert.equal(summary.off, 1);
    assert.deepEqual(summary.policy, [
      'Two-person rule on: the person who created the task or started the run never approves its high-risk actions.',
      'Medium-risk actions run without approval.',
    ]);
  });
  test('no rules and no policy', () => {
    const summary = routingSummary([], null);
    assert.deepEqual(summary, { bands: [], off: 0, policy: [] });
    assert.equal(rulesCount([rule(), rule({ enabled: false })]), '2 rules, 1 on');
    assert.equal(rulesCount([rule()]), '1 rule, 1 on');
  });
  test('a rule without a band applies to every amount', () => {
    const summary = routingSummary([rule({ conditions: {}, approver_steps: [] })], policy);
    assert.match(summary.bands[0].text, /every amount/);
  });
  test('reads in Arabic', () => {
    setLocale('ar');
    const summary = routingSummary([rule()], { ...policy, mediumRiskRequiresApproval: true });
    assert.match(summary.bands[0].text, /Refunds above 250:/);
    assert.match(summary.policy[1], /تتوقف الإجراءات متوسطة الخطورة/);
  });
});

describe('the page in the shell', () => {
  test('#policy and the section links open Approvals settings, which sits in the Admin group', () => {
    assert.equal(viewFromHash('#policy'), 'approvalSettings');
    assert.equal(viewFromHash('#approvalSettings'), 'approvalSettings');
    assert.equal(viewFromHash('#approvalSettings/rules'), 'approvalSettings');
    assert.equal(sectionFromHash('#approvalSettings/rules'), 'rules');
    assert.equal(sectionFromHash('#approvalSettings/elsewhere'), null);
    assert.equal(sectionFromHash('#approvalSettings'), null);
    assert.ok(NAV.find((group) => group.group === 'admin')!.views.includes('approvalSettings'));
    assert.equal((VIEWS as readonly string[]).includes('policy'), false);
    assert.ok(visibleViews(false).includes('approvalSettings'));
  });
});
