// Unit tests for the Integrations, Skills and marketplace helpers in src/lib/catalog.ts.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  canSwitchIntegration, compareVersions, credentialBody, credentialSource, emptyMarketFilter, emptyPublishDraft, filterPackages, filterSkills,
  groupSkills, historyLabel, installSummary, lastErrorNote, packageBody, pageOf, publishBody, rollbackTargets,
  type IntegrationState, type MarketPackage,
} from '../src/lib/catalog.ts';

const at = '2026-10-06T12:00:00Z';
const integration = (over: Partial<IntegrationState> = {}): IntegrationState => ({
  id: 'valkey', name: 'Valkey', kind: 'cache', status: 'connected', endpoint: 'redis://valkey:6379', latency_ms: 2, detail: 'Health check passed.',
  credentials: { configured: true, source: 'environment', scopes: [], expires_at: null }, ...over,
});
const version = (v: string, over: Record<string, unknown> = {}) => ({ id: `id_${v}`, skill_id: 'acme.notes', version: v, name: 'Notes', description: 'Writes notes', required_tools: ['anum.respond'], risk_level: 'low', created_at: at, ...over });

describe('integrations', () => {
  test('a switch is offered only when the operator configured it', () => {
    assert.equal(canSwitchIntegration(integration()), true);
    assert.equal(canSwitchIntegration(integration({ available: false, status: 'disabled' })), false);
  });
  test('last error is shown once, with its time', () => {
    const f = (iso: string) => `T(${iso})`;
    assert.equal(lastErrorNote(integration(), f), null);
    assert.equal(lastErrorNote(integration({ last_error: 'Health check failed: X', last_error_at: at }), f), `Last error T(${at}): Health check failed: X`);
    assert.equal(lastErrorNote(integration({ status: 'degraded', detail: 'Health check failed: X', last_error: 'Health check failed: X', last_error_at: at }), f), `Failing since the check at T(${at}).`);
  });
  test('credential source in words', () => {
    assert.match(credentialSource(integration({ workspace_credential: { integration_id: 'x', configured: true, hint: '…abcd', updated_by: 'owner', updated_at: at } })), /Workspace token …abcd stored encrypted by owner/);
    assert.match(credentialSource(integration()), /operator configured/);
    assert.match(credentialSource(integration({ credentials: { configured: false, source: 'none', scopes: [], expires_at: null } })), /without an Authorization header/);
  });
  test('credential body mirrors the API', () => {
    assert.equal(credentialBody('').ok, false);
    assert.equal(credentialBody('short-token').ok, false);
    assert.equal(credentialBody('has a space inside').ok, false);
    assert.equal(credentialBody('tab\tinside-token').ok, false);
    assert.equal(credentialBody('é-not-ascii-token').ok, false);
    assert.deepEqual(credentialBody('example-workspace-abcd'), { ok: true, value: { secret: 'example-workspace-abcd' } });
  });
});

describe('skills', () => {
  test('compares semantic versions numerically', () => {
    assert.ok(compareVersions('1.10.0', '1.9.9') > 0);
    assert.equal(compareVersions('2.0.0', '2.0.0'), 0);
  });
  test('groups versions per skill with install state and upgrade', () => {
    const groups = groupSkills([version('1.0.0'), version('1.2.0', { name: 'Notes v2' }), version('1.0.0', { skill_id: 'beta.other', name: 'Alpha' })],
      [{ id: 'i', skill_id: 'acme.notes', version: '1.0.0', approved_tools: [], enabled: false, installed_at: at }]);
    assert.deepEqual(groups.map((g) => g.skillId), ['beta.other', 'acme.notes']);
    const notes = groups[1];
    assert.deepEqual(notes.versions.map((v) => v.version), ['1.2.0', '1.0.0']);
    assert.equal(notes.state, 'disabled');
    assert.equal(notes.name, 'Notes'); // the installed version's name
    assert.equal(notes.upgrade?.version, '1.2.0');
    assert.equal(groups[0].state, 'available');
  });
  test('filters by text, tool and install state', () => {
    const groups = groupSkills([version('1.0.0'), version('1.0.0', { skill_id: 'beta.search', name: 'Search', required_tools: ['web_search'] })],
      [{ id: 'i', skill_id: 'acme.notes', version: '1.0.0', approved_tools: [], enabled: true, installed_at: at }]);
    assert.deepEqual(filterSkills(groups, 'WEB_SEARCH', 'all').map((g) => g.skillId), ['beta.search']);
    assert.deepEqual(filterSkills(groups, '', 'installed').map((g) => g.skillId), ['acme.notes']);
    assert.deepEqual(filterSkills(groups, '', 'available').map((g) => g.skillId), ['beta.search']);
  });
  test('pages with show more', () => {
    assert.deepEqual(pageOf([1, 2, 3, 4, 5], 1, 2), { shown: [1, 2], remaining: 3 });
    assert.deepEqual(pageOf([1, 2, 3], 2, 2), { shown: [1, 2, 3], remaining: 0 });
  });
  test('rollback targets are older than the installed version, newest first', () => {
    assert.deepEqual(rollbackTargets([{ version: '2.0.0' }, { version: '1.0.0' }, { version: '1.5.0' }], '2.0.0'), ['1.5.0', '1.0.0']);
    assert.deepEqual(rollbackTargets([{ version: '1.0.0' }], null), []);
  });
  test('history labels', () => {
    const entry = (action: string, metadata: Record<string, unknown>) => ({ id: 'a', action, actor: 'u', outcome: 'success', created_at: at, metadata });
    assert.equal(historyLabel(entry('skill.rolled_back', { from_version: '2.0.0', to_version: '1.0.0' })), 'Rolled back 2.0.0 → 1.0.0');
    assert.equal(historyLabel(entry('skill.installed', { version: '1.2.0', previous_version: '1.0.0' })), 'Installed 1.2.0 (was 1.0.0)');
    assert.equal(historyLabel(entry('skill.disabled', { version: '1.0.0' })), 'Disabled 1.0.0');
  });
  test('publish body is validated like the API', () => {
    const draft = { ...emptyPublishDraft(), skillId: 'acme.notes', name: 'Notes', description: 'Writes', instructions: 'Do it', tools: 'web_search, anum.respond\nweb_search' };
    const ok = publishBody(draft);
    assert.ok(ok.ok && ok.value.required_tools.length === 2);
    assert.equal(publishBody({ ...draft, skillId: 'No' }).ok, false);
    assert.equal(publishBody({ ...draft, version: '1.0' }).ok, false);
    assert.equal(publishBody({ ...draft, instructions: ' ' }).ok, false);
  });
});

describe('marketplace', () => {
  const pkg = (over: Partial<MarketPackage> = {}): MarketPackage => ({ id: 'acme.crm', name: 'CRM', kind: 'integration', version: '1.0.0', publisher: 'Acme', verified: true, permissions: ['integration:read'], regions: ['eu'], ...over });
  test('filters by query, kind, verification and installed; installed first', () => {
    const items = [pkg(), pkg({ id: 'beta.notes', name: 'Alpha notes', kind: 'skill', verified: false, publisher: 'Beta' })];
    const installs = [{ package_id: 'acme.crm', version: '1.0.0', enabled: true, installed_by: 'u', installed_at: at }];
    assert.deepEqual(filterPackages(items, installs, emptyMarketFilter()).map((p) => p.id), ['acme.crm', 'beta.notes']);
    assert.deepEqual(filterPackages(items, installs, { ...emptyMarketFilter(), query: 'beta' }).map((p) => p.id), ['beta.notes']);
    assert.deepEqual(filterPackages(items, installs, { ...emptyMarketFilter(), verifiedOnly: true }).map((p) => p.id), ['acme.crm']);
    assert.deepEqual(filterPackages(items, installs, { ...emptyMarketFilter(), kind: 'skill' }).map((p) => p.id), ['beta.notes']);
    assert.deepEqual(filterPackages(items, [], { ...emptyMarketFilter(), installedOnly: true }), []);
  });
  test('install summary names publisher, permissions and warns when unverified', () => {
    assert.equal(installSummary(pkg()).warning, null);
    assert.match(installSummary(pkg()).lines.join(' '), /from Acme.*1 permission/);
    assert.match(installSummary(pkg({ verified: false, permissions: [] })).warning ?? '', /Not verified/);
  });
  test('package body is validated like the API', () => {
    const draft = { id: 'acme.crm', name: 'CRM', kind: 'integration' as const, version: '1.0.0', publisher: 'Acme', verified: true, permissions: 'a, b, a', regions: '' };
    const ok = packageBody(draft);
    assert.ok(ok.ok && ok.value.permissions.length === 2 && ok.value.regions.length === 0);
    assert.equal(packageBody({ ...draft, id: 'Bad Id' }).ok, false);
    assert.equal(packageBody({ ...draft, publisher: '' }).ok, false);
  });
});
