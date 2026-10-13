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
    assert.deepEqual(routingTargetBody(targetDraft(t)), { ok: true, value: t });
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
});

describe('organization policy access', () => {
  test('nothing is editable or explained before the summary loads', () => {
    assert.deepEqual(organizationPolicyAccess(null), { canManage: false, notice: null });
  });
  test('an organization administrator manages the tenant-wide controls', () => {
    assert.deepEqual(organizationPolicyAccess({ can_manage_organization_policy: true }), { canManage: true, notice: null });
  });
  test('anyone else, a workspace owner included, reads them with the reason and who can change them', () => {
    for (const summary of [{ can_manage_organization_policy: false }, {}]) {
      const access = organizationPolicyAccess(summary);
      assert.equal(access.canManage, false);
      assert.match(access.notice ?? '', /every workspace in this organization/);
      assert.match(access.notice ?? '', /organization administrator/);
      assert.match(access.notice ?? '', /Owning this workspace is not enough/);
      // How to get the role, not just that it is missing (an upgraded realm may have nobody holding it).
      assert.match(access.notice ?? '', /realm role org_admin/);
      assert.match(access.notice ?? '', /keycloak-2fa\.sh grant-org-admin <your username>/);
      assert.match(access.notice ?? '', /sign out and sign in again/);
    }
  });
});

describe('tenant-wide write access', () => {
  test('follows the API flag only, with nothing explained before it loads', () => {
    assert.deepEqual(tenantWriteAccess(null, 'routing targets'), { canManage: false, notice: null });
    assert.deepEqual(tenantWriteAccess(true, 'routing targets'), { canManage: true, notice: null });
    const denied = tenantWriteAccess(false, 'organization model budgets');
    assert.equal(denied.canManage, false);
    assert.match(denied.notice ?? '', /^Read only: organization model budgets apply to every workspace in this organization/);
    assert.match(denied.notice ?? '', /realm role org_admin/);
  });
  test('routing targets and the catalog take the governance summary flag', () => {
    assert.deepEqual(enterpriseWriteAccess(null), { canManage: false, notice: null });
    assert.equal(enterpriseWriteAccess({ can_manage_organization_policy: true }).canManage, true);
    const denied = enterpriseWriteAccess({});
    assert.equal(denied.canManage, false);
    assert.match(denied.notice ?? '', /routing targets and the marketplace catalog/);
    assert.match(denied.notice ?? '', /Owning this workspace is not enough/);
  });
});
