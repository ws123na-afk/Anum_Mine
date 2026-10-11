// Unit tests for the governance, routing and integration configuration helpers in
// src/lib/governance.ts. Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  approvalRuleBody, emptyTargetDraft, integrationBadge, integrationConfigBody, packState, parseList, regionSummary,
  roleTemplateBody, routingTargetBody, sortPacks, targetDraft, type PolicyPack, type RoutingTarget,
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

describe('role templates and approval rules', () => {
  test('parseList trims, splits and de-duplicates', () => {
    assert.deepEqual(parseList(' owner, member\nowner ,, '), ['owner', 'member']);
  });
  test('role template needs a name and a permission', () => {
    assert.equal(roleTemplateBody('  ', ['task:read']).ok, false);
    assert.equal(roleTemplateBody('Analyst', []).ok, false);
    assert.equal(roleTemplateBody('x'.repeat(81), ['task:read']).ok, false);
    assert.deepEqual(roleTemplateBody(' Analyst ', ['task:read', 'task:read']), { ok: true, value: { name: 'Analyst', permissions: ['task:read'] } });
  });
  test('approval rule validates approvers and defaults roles to owner', () => {
    assert.equal(approvalRuleBody({ name: 'r', actionPattern: 'a.*', minimumApprovers: '0', requiredRoles: '' }).ok, false);
    assert.equal(approvalRuleBody({ name: 'r', actionPattern: 'a.*', minimumApprovers: '1.5', requiredRoles: '' }).ok, false);
    assert.equal(approvalRuleBody({ name: 'r', actionPattern: ' ', minimumApprovers: '1', requiredRoles: '' }).ok, false);
    assert.deepEqual(approvalRuleBody({ name: ' r ', actionPattern: 'integration.*', minimumApprovers: '2', requiredRoles: '' }),
      { ok: true, value: { name: 'r', action_pattern: 'integration.*', minimum_approvers: 2, required_roles: ['owner'] } });
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
});
