// Unit tests for the governance, routing and integration configuration helpers in
// src/lib/governance.ts. Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  emptyTargetDraft, enterpriseWriteAccess, integrationBadge, integrationConfigBody, integrationToolNote, organizationPolicyAccess, packState, parseList, regionSummary,
  roleTemplateBody, routingTargetBody, sortPacks, targetDraft, tenantWriteAccess, type PolicyPack, type RoutingTarget,
} from '../src/lib/governance.ts';

const pack = (name: string, version: number, active: boolean): PolicyPack => ({ id: `${name}-${version}`, name, description: '', version, active, rules: [], created_by: 'u', created_at: '2026-10-01T00:00:00Z' });
const target = (id: string, region: string, status: RoutingTarget['status']): RoutingTarget => ({ id, region, provider: 'p', model: 'm', status, modalities: ['text'], sensitivity: ['standard'], cost_per_1k_tokens: 0.5, latency_ms: 300 });

describe('policy packs', () => {
  test('active packs offer archive, archived packs offer activate', () => {
    assert.deepEqual(packState({ active: true }), { label: 'Active', tone: 'success', action: 'archive' });
    assert.deepEqual(packState({ active: false }), { label: 'Archived', tone: 'neutral', action: 'activate' });
  });
  test('versions of one pack sit together, newest first', () => {
    assert.deepEqual(sortPacks([pack('b', 1, true), pack('a', 1, false), pack('a', 3, true)]).map((p) => p.id), ['a-3', 'a-1', 'b-1']);
  });
});

describe('role templates', () => {
  test('parseList trims, splits and de-duplicates', () => {
    assert.deepEqual(parseList(' owner, member\nowner ,, '), ['owner', 'member']);
  });
  test('role template needs a name and a permission', () => {
    assert.equal(roleTemplateBody('  ', ['task:read']).ok, false);
    assert.equal(roleTemplateBody('Analyst', []).ok, false);
    assert.equal(roleTemplateBody('x'.repeat(81), ['task:read']).ok, false);
    assert.deepEqual(roleTemplateBody(' Analyst ', ['task:read', 'task:read']), { ok: true, value: { name: 'Analyst', permissions: ['task:read'] } });
  });
});

describe('regions', () => {
  const ops = (active_regions: number, failover_ready: boolean) => ({ active_regions, healthy_targets: 0, degraded_targets: 0, installed_packages: 0, failover_ready });
  test('no targets is not a pending failover', () => {
    assert.deepEqual(regionSummary(ops(0, false), []).failover, 'Not applicable');
    assert.equal(regionSummary(ops(0, false), []).regions, 'None configured');
  });
  test('one region is reported as single region', () => {
    const summary = regionSummary(ops(1, false), [target('a', 'eu-west', 'healthy'), target('b', 'eu-west', 'unverified')]);
    assert.equal(summary.regions, 'Single region (eu-west)');
    assert.equal(summary.failover, 'Not applicable');
  });
  test('several regions report the API failover answer', () => {
    const targets = [target('a', 'eu', 'healthy'), target('b', 'us', 'healthy')];
    assert.equal(regionSummary(ops(2, true), targets).failover, 'Ready');
    assert.equal(regionSummary(ops(2, false), targets).failover, 'Not ready');
  });
});

describe('routing target drafts', () => {
  test('round-trips a target', () => {
    const t = target('eu-1', 'eu-west', 'healthy');
    assert.deepEqual(routingTargetBody(targetDraft(t)), { ok: true, value: { ...t, enabled: true } });
  });
  test('rejects bad ids, missing fields and bad numbers', () => {
    const base = { ...emptyTargetDraft(), id: 'ok', region: 'r', provider: 'p', model: 'm' };
    assert.equal(routingTargetBody(base).ok, true);
    assert.equal(routingTargetBody({ ...base, id: 'has space' }).ok, false);
    assert.equal(routingTargetBody({ ...base, model: ' ' }).ok, false);
    assert.equal(routingTargetBody({ ...base, cost: '-1' }).ok, false);
    assert.equal(routingTargetBody({ ...base, cost: '' }).ok, false);
    assert.equal(routingTargetBody({ ...base, latency: '0' }).ok, false);
    assert.equal(routingTargetBody({ ...base, modalities: ' , ' }).ok, false);
  });
});

describe('integrations', () => {
  test('an empty endpoint means the deployment default', () => {
    assert.deepEqual(integrationConfigBody(false, '  '), { ok: true, value: { enabled: false, endpoint: null } });
    assert.deepEqual(integrationConfigBody(true, ' http://x:1 '), { ok: true, value: { enabled: true, endpoint: 'http://x:1' } });
    assert.equal(integrationConfigBody(true, 'x'.repeat(501)).ok, false);
  });
  test('disabled integrations are "Not used"', () => {
    assert.deepEqual(integrationBadge('disabled'), { label: 'Not used', tone: 'neutral' });
    assert.equal(integrationBadge('connected').tone, 'success');
    assert.equal(integrationBadge('degraded').tone, 'dangerTone');
  });
  test('the search integration says whether agents can use web_search', () => {
    assert.equal(integrationToolNote('cache', 'connected'), null);
    assert.equal(integrationToolNote('search', 'connected')?.available, true);
    assert.match(integrationToolNote('search', 'degraded')?.text ?? '', /calls fail until search answers/);
    assert.deepEqual(integrationToolNote('search', 'disabled'), { text: 'Agent tool web_search: unavailable here.', available: false });
  });
  test('the browser research integration says whether agents can use web.browse_page', () => {
    assert.equal(integrationToolNote('tool', 'connected', 'browser-research')?.available, true);
    assert.match(integrationToolNote('tool', 'degraded', 'browser-research')?.text ?? '', /browser service answers/);
    assert.match(integrationToolNote('tool', 'disabled', 'browser-research')?.text ?? '', /Not available on this server/);
    assert.equal(integrationToolNote('tool', 'connected', 'external-webhook'), null);
  });
});

describe('organization policy access', () => {
  test('nothing is editable or explained before the summary loads', () => {
    assert.deepEqual(organizationPolicyAccess(null), { canManage: false, notice: null, howTo: null });
  });
  test('an organization administrator manages the tenant-wide controls', () => {
    assert.deepEqual(organizationPolicyAccess({ can_manage_organization_policy: true }), { canManage: true, notice: null, howTo: null });
  });
  test('anyone else, a workspace owner included, reads them with the reason and who can change them', () => {
    for (const summary of [{ can_manage_organization_policy: false }, {}]) {
      const access = organizationPolicyAccess(summary);
      assert.equal(access.canManage, false);
      // One line: what is read only and who can change it.
      assert.match(access.notice ?? '', /^Read only: policy packs, role templates, approval rules and memory governance apply to every workspace in this organization, so only an organization administrator can change them\.$/);
      // How to get the role, not just that it is missing (an upgraded realm may have nobody holding it), behind the disclosure.
      assert.match(access.howTo?.text ?? '', /Owning this workspace is not enough/);
      assert.match(access.howTo?.text ?? '', /realm role org_admin/);
      assert.match(access.howTo?.text ?? '', /sign out and sign in again/);
      assert.equal(access.howTo?.command, 'infra/oracle/keycloak-2fa.sh grant-org-admin <your username>');
    }
  });
});

describe('tenant-wide write access', () => {
  test('follows the API flag only, with nothing explained before it loads', () => {
    assert.deepEqual(tenantWriteAccess(null, 'routing targets'), { canManage: false, notice: null, howTo: null });
    assert.deepEqual(tenantWriteAccess(true, 'routing targets'), { canManage: true, notice: null, howTo: null });
    const denied = tenantWriteAccess(false, 'organization model budgets');
    assert.equal(denied.canManage, false);
    assert.match(denied.notice ?? '', /^Read only: organization model budgets apply to every workspace in this organization/);
    assert.match(denied.howTo?.text ?? '', /realm role org_admin/);
  });
  test('routing targets and the catalog take the governance summary flag', () => {
    assert.deepEqual(enterpriseWriteAccess(null), { canManage: false, notice: null, howTo: null });
    assert.equal(enterpriseWriteAccess({ can_manage_organization_policy: true }).canManage, true);
    const denied = enterpriseWriteAccess({});
    assert.equal(denied.canManage, false);
    assert.match(denied.notice ?? '', /routing targets and the marketplace catalog/);
    assert.match(denied.howTo?.text ?? '', /Owning this workspace is not enough/);
  });
});

// ---------------------------------------------------------------- depth: editor, versions, health

import {
  DEFAULT_MEMORY_GOVERNANCE, baselinePackDraft, deleteBlocker, diffRules, emptyPackDraft, filterPacks, filterRules, filterTemplates, memoryGovernanceBody, memoryGovernanceDraft, moveRule, packBody, packGroups, ruleLabel, targetHealth, templateEffect,
} from '../src/lib/governance.ts';

const ruled = (name: string, version: number, active: boolean, rules: PolicyPack['rules']): PolicyPack => ({ ...pack(name, version, active), rules });

describe('policy pack editor', () => {
  test('a pack needs a name and at least one rule with an action', () => {
    assert.equal(packBody(emptyPackDraft()).ok, false);
    assert.equal(packBody({ name: 'Guard', description: '', rules: [] }).ok, false);
    assert.equal(packBody({ name: 'Guard', description: '', rules: [{ action: ' ', effect: 'deny', conditions: '' }] }).ok, false);
  });
  test('conditions are a JSON object or empty', () => {
    const bad = packBody({ name: 'Guard', description: '', rules: [{ action: 'x', effect: 'deny', conditions: '[1]' }] });
    assert.equal(bad.ok, false);
    const good = packBody({ name: ' Guard ', description: ' d ', rules: [{ action: 'pay.*', effect: 'require_approval', conditions: ' {"risk":"high"} ' }, { action: 'x', effect: 'deny', conditions: '' }] });
    assert.ok(good.ok);
    if (good.ok) assert.deepEqual(good.value, { name: 'Guard', description: 'd', rules: [{ action: 'pay.*', effect: 'require_approval', conditions: { risk: 'high' } }, { action: 'x', effect: 'deny', conditions: {} }] });
  });
  test('the baseline template is a valid pack in the page language', () => {
    const body = packBody(baselinePackDraft());
    assert.ok(body.ok);
    if (body.ok) assert.equal(body.value.rules.length, 3);
  });
  test('moveRule swaps neighbours and ignores the edges', () => {
    assert.deepEqual(moveRule([1, 2, 3], 0, 1), [2, 1, 3]);
    assert.deepEqual(moveRule([1, 2, 3], 2, -1), [1, 3, 2]);
    assert.deepEqual(moveRule([1, 2, 3], 0, -1), [1, 2, 3]);
    assert.deepEqual(moveRule([1, 2, 3], 2, 1), [1, 2, 3]);
  });
});

describe('policy pack versions', () => {
  test('packGroups puts the versions of one name together, newest first, and finds the active one', () => {
    const groups = packGroups([pack('b', 1, true), pack('a', 1, false), pack('A', 3, true), pack('a', 2, false)]);
    assert.deepEqual(groups.map((g) => [g.name, g.versions.map((v) => v.version), g.active?.version ?? null]), [['A', [3, 2, 1], 3], ['b', [1], 1]]);
  });
  test('filterPacks matches name, description, action or effect', () => {
    const packs = [ruled('Finance', 1, true, [{ action: 'payment.send', effect: 'deny' }]), ruled('Mail', 1, true, [{ action: 'email.send', effect: 'allow' }])];
    assert.deepEqual(filterPacks(packs, 'PAYMENT').map((p) => p.name), ['Finance']);
    assert.deepEqual(filterPacks(packs, 'allow').map((p) => p.name), ['Mail']);
    assert.equal(filterPacks(packs, '  ').length, 2);
  });
  test('diffRules reports added, removed, changed and unchanged rules by action', () => {
    const before = [{ action: 'a', effect: 'deny' }, { action: 'b', effect: 'allow' }, { action: 'c', effect: 'allow', conditions: { x: 1 } }];
    const after = [{ action: 'a', effect: 'require_approval' }, { action: 'c', effect: 'allow', conditions: { x: 1 } }, { action: 'd', effect: 'deny' }];
    assert.deepEqual(diffRules(before, after).map((c) => [c.action, c.kind]), [['a', 'changed'], ['c', 'same'], ['d', 'added'], ['b', 'removed']]);
    assert.equal(ruleLabel({ action: 'c', effect: 'allow', conditions: { x: 1 } }), 'c: allow {"x":1}');
  });
  test('filterTemplates and filterRules match names and their details', () => {
    assert.deepEqual(filterTemplates([{ id: '1', name: 'Reader', permissions: ['task:read'], created_at: '' }, { id: '2', name: 'Runner', permissions: ['task:run'], created_at: '' }], 'task:run').map((t) => t.name), ['Runner']);
    const rule = { id: 'r', tenant_id: 't', name: 'Refunds', action_pattern: '*refund*', minimum_approvers: 1, required_roles: ['owner'], enabled: true, created_at: '' };
    assert.equal(filterRules([rule], 'REFUND').length, 1);
    assert.equal(filterRules([rule], 'mail').length, 0);
  });
});

describe('routing target health', () => {
  test('a typed status counts for nothing until a probe ran', () => {
    const typed = { ...target('a', 'eu', 'healthy'), last_checked_at: null };
    assert.equal(targetHealth(typed).label, 'Not yet probed');
    assert.match(targetHealth(typed).detail, /^Never probed\. The scheduled check probes it once a workspace has a model configuration for p;/);
    assert.equal(deleteBlocker(typed), null);
    // Checked, but no configuration reached the provider: says so, never "not yet probed".
    const unverified = { ...typed, status: 'unverified' as const, last_checked_at: '2026-11-10T10:00:00Z', last_error: 'Not probed: this workspace has no model configuration.' };
    assert.equal(targetHealth(unverified).label, 'Not verified');
    assert.match(targetHealth(unverified).detail, /no model configuration/);
  });
  test('a probed healthy target in routing is in use; out of routing it can go', () => {
    const probed = { ...target('a', 'eu', 'healthy'), last_checked_at: '2026-11-10T10:00:00Z', measured_latency_ms: 240 };
    assert.equal(targetHealth(probed).label, 'healthy');
    assert.match(targetHealth(probed).detail, /240 ms/);
    assert.ok(deleteBlocker(probed));
    assert.equal(deleteBlocker({ ...probed, enabled: false }), null);
    assert.equal(targetHealth({ ...probed, enabled: false }).label, 'Out of routing');
    const offline = { ...probed, status: 'offline' as const, last_error: 'Could not reach it.' };
    assert.equal(targetHealth(offline).tone, 'danger');
    assert.match(targetHealth(offline).detail, /Could not reach it/);
    assert.equal(deleteBlocker(offline), null);
  });
  test('the region summary says when nothing has been probed yet', () => {
    const summary = regionSummary({ active_regions: 0, healthy_targets: 0, degraded_targets: 0, installed_packages: 0, failover_ready: false, unprobed_targets: 2 }, [target('a', 'eu', 'unverified'), target('b', 'us', 'unverified')]);
    assert.equal(summary.failover, 'Unknown');
    assert.match(summary.detail, /2 targets in routing have not been probed yet/);
  });
});

describe('memory governance', () => {
  test('the body needs 1 to 3650 days and lower-cases source types', () => {
    assert.equal(memoryGovernanceBody({ ...memoryGovernanceDraft(DEFAULT_MEMORY_GOVERNANCE), retentionDays: '0' }).ok, false);
    assert.equal(memoryGovernanceBody({ ...memoryGovernanceDraft(DEFAULT_MEMORY_GOVERNANCE), retentionDays: '12.5' }).ok, false);
    const body = memoryGovernanceBody({ retentionDays: '90', allowPermanent: true, requireProvenance: false, sourceTypes: 'Task, document ,task' });
    assert.ok(body.ok);
    if (body.ok) assert.deepEqual(body.value, { default_retention_days: 90, allow_permanent_retention: true, require_provenance: false, allowed_source_types: ['task', 'document'] });
  });
});

describe('role templates on members', () => {
  test('templateEffect counts what applies and names what the role does not grant', () => {
    assert.deepEqual(templateEffect(['task:read', 'task:run'], ['task:read']), { applies: 1, outsideRole: ['task:run'] });
    assert.deepEqual(templateEffect([], null), { applies: 0, outsideRole: [] });
  });
});
