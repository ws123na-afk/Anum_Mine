// Unit tests for the audit log and workspace administration logic in src/lib/audit.ts.
// Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  EMPTY_FILTER_DRAFT,
  actionLabel,
  actionOptions,
  archiveConfirmed,
  auditFilters,
  auditQuery,
  exportFilename,
  hasFilters,
  isOnlyActiveOwner,
  localDayStart,
  mapAuditPage,
  mapMembersSummary,
  mapPermissions,
  mapWorkspace,
  memberQuery,
  metadataRows,
  outcomeTone,
  workspaceIdInput,
  workspaceName,
} from '../src/lib/audit.ts';
import { membershipEntries, membershipLabel, workspaceOptions, EMPTY_MEMORY } from '../src/lib/workspaces.ts';

const record = {
  id: 'audit_1', tenant_id: 't', workspace_id: 'w', actor: 'owner_1', action: 'workspace_member.role_change',
  target: 'user_2', outcome: 'success', correlation_id: 'c', created_at: '2026-09-01T10:00:00Z', metadata: { role: 'viewer' },
};

describe('audit records', () => {
  test('maps a page and falls back to the documented retention', () => {
    const page = mapAuditPage({ items: [record, { ...record, id: 'audit_2', metadata: null }], next_cursor: 'abc' });
    assert.equal(page.items[0].correlationId, 'c');
    assert.deepEqual(page.items[1].metadata, {});
    assert.equal(page.nextCursor, 'abc');
    assert.equal(page.retention, '');
    assert.equal(mapAuditPage({ items: [], next_cursor: '', retention: 'Kept forever.' }).nextCursor, null);
    assert.equal(mapAuditPage({ items: [], next_cursor: null, retention: 'Kept forever.' }).retention, 'Kept forever.');
  });

  test('labels actions and outcomes', () => {
    assert.equal(actionLabel('workspace_member.role_change'), 'Workspace member role change');
    assert.equal(actionLabel('workspace.*'), 'Workspace');
    assert.equal(outcomeTone('success'), 'success');
    assert.equal(outcomeTone('refused_too_large'), 'dangerTone');
    assert.equal(outcomeTone('partial'), 'warning');
  });

  test('offers a prefix option for each action family with several actions', () => {
    const options = actionOptions(['workspace.rename', 'audit.export', 'workspace.archive']);
    assert.deepEqual(options.map((option) => option.value), ['workspace.*', 'audit.export', 'workspace.archive', 'workspace.rename']);
    assert.equal(options[0].label, 'Workspace: any');
  });

  test('shows metadata sorted, with nested values as JSON', () => {
    assert.deepEqual(metadataRows({ b: { x: 1 }, a: 'y', c: null }), [{ key: 'a', value: 'y' }, { key: 'b', value: '{"x":1}' }, { key: 'c', value: 'null' }]);
  });

  test('takes the export file name from the response, refusing anything unsafe', () => {
    assert.equal(exportFilename('attachment; filename="audit-w-20261011T120000Z.csv"', 'csv'), 'audit-w-20261011T120000Z.csv');
    assert.equal(exportFilename('attachment; filename="../../etc/passwd"', 'json'), 'audit-export.json');
    assert.equal(exportFilename(null, 'csv'), 'audit-export.csv');
  });
});

describe('audit filters', () => {
  test('trims text filters and drops empty ones', () => {
    const result = auditFilters({ ...EMPTY_FILTER_DRAFT, actor: ' owner_1 ', action: 'workspace.*', q: '  ' });
    assert.deepEqual(result, { ok: true, value: { actor: 'owner_1', action: 'workspace.*' } });
    assert.equal(hasFilters({}), false);
    assert.equal(hasFilters({ actor: 'x' }), true);
  });

  test('turns an inclusive local day range into the API\'s since and exclusive until', () => {
    const result = auditFilters({ ...EMPTY_FILTER_DRAFT, from: '2026-09-01', to: '2026-09-03' });
    assert.ok(result.ok);
    if (!result.ok) return;
    assert.equal(result.value.since, new Date(2026, 8, 1).toISOString());
    assert.equal(result.value.until, new Date(2026, 8, 4).toISOString());
    const sameDay = auditFilters({ ...EMPTY_FILTER_DRAFT, from: '2026-09-01', to: '2026-09-01' });
    assert.ok(sameDay.ok);
  });

  test('refuses reversed ranges, impossible dates and long values', () => {
    assert.deepEqual(auditFilters({ ...EMPTY_FILTER_DRAFT, from: '2026-09-05', to: '2026-09-01' }), { ok: false, error: 'The start date must be on or before the end date.' });
    assert.deepEqual(auditFilters({ ...EMPTY_FILTER_DRAFT, from: '2026-02-30' }), { ok: false, error: 'Enter a valid start date.' });
    assert.equal(localDayStart('2026-13-01'), null);
    assert.deepEqual(auditFilters({ ...EMPTY_FILTER_DRAFT, actor: 'x'.repeat(161) }), { ok: false, error: 'Actor is at most 160 characters.' });
  });

  test('builds the query string with the API\'s parameter names', () => {
    const query = auditQuery({ actor: 'a b', correlationId: 'c1', since: '2026-09-01T00:00:00.000Z' }, { cursor: 'xyz', limit: 50, format: null });
    const params = new URLSearchParams(query);
    assert.equal(params.get('actor'), 'a b');
    assert.equal(params.get('correlation_id'), 'c1');
    assert.equal(params.get('since'), '2026-09-01T00:00:00.000Z');
    assert.equal(params.get('cursor'), 'xyz');
    assert.equal(params.get('limit'), '50');
    assert.equal(params.has('format'), false);
  });
});

describe('members and workspaces', () => {
  test('maps the summary, permissions and workspace', () => {
    assert.deepEqual(mapMembersSummary({ total: 4, active: 3, deactivated: 1, active_owners: 1, pending_invitations: 2 }), { total: 4, active: 3, deactivated: 1, activeOwners: 1, pendingInvitations: 2 });
    const permissions = mapPermissions({ workspace_id: 'w', role: 'auditor', organization_admin: true, can_manage_workspace: true, can_create_workspace: false, can_manage_members: false, can_read_audit: true, can_export_audit: true });
    assert.equal(permissions.role, null);
    assert.equal(permissions.organizationAdmin, true);
    assert.equal(permissions.canCreateWorkspace, false);
    const workspace = mapWorkspace({ id: 'w', tenant_id: 't', name: 'W', created_at: 'c', updated_at: 'u' });
    assert.equal(workspace.archivedAt, null);
    assert.equal(mapWorkspace({ id: 'w', tenant_id: 't', name: 'W', created_at: 'c', updated_at: 'u', archived_at: 'a', archived_by: 'o' }).archivedBy, 'o');
  });

  test('builds the paged member query', () => {
    const params = new URLSearchParams(memberQuery({ q: ' ada ', status: 'active', role: 'owner', cursor: 'next' }));
    assert.deepEqual(Object.fromEntries(params), { q: 'ada', status: 'active', role: 'owner', limit: '50', cursor: 'next' });
    assert.deepEqual(Object.fromEntries(new URLSearchParams(memberQuery({ q: '', status: 'all', role: 'all' }))), { limit: '50' });
  });

  test('protects the only active owner using the summary count', () => {
    const owner = { active: true, role: 'owner' as const };
    assert.equal(isOnlyActiveOwner({ total: 2, active: 2, deactivated: 0, activeOwners: 1, pendingInvitations: 0 }, owner), true);
    assert.equal(isOnlyActiveOwner({ total: 3, active: 3, deactivated: 0, activeOwners: 2, pendingInvitations: 0 }, owner), false);
    assert.equal(isOnlyActiveOwner({ total: 2, active: 2, deactivated: 0, activeOwners: 1, pendingInvitations: 0 }, { active: true, role: 'member' }), false);
    assert.equal(isOnlyActiveOwner(null, owner), false);
  });

  test('validates workspace names, ids and the archive confirmation', () => {
    assert.deepEqual(workspaceName('  Finance   team '), { ok: true, value: 'Finance team' });
    assert.equal(workspaceName('   ').ok, false);
    assert.equal(workspaceName('x'.repeat(161)).ok, false);
    assert.deepEqual(workspaceIdInput(''), { ok: true, value: null });
    assert.deepEqual(workspaceIdInput('workspace_fin'), { ok: true, value: 'workspace_fin' });
    assert.equal(workspaceIdInput('a b').ok, false);
    assert.equal(archiveConfirmed(' Finance ', 'Finance'), true);
    assert.equal(archiveConfirmed('finance', 'Finance'), false);
  });

  test('the switcher marks archived workspaces', () => {
    const entries = membershipEntries([{ workspace_id: 'workspace_old', workspace_name: 'Old', role: 'owner', archived: true }, { workspace_id: 'workspace_new', workspace_name: 'New', role: 'viewer' }]);
    assert.equal(entries[0].archived, true);
    assert.equal('archived' in entries[1], false);
    const options = workspaceOptions('workspace_new', [], EMPTY_MEMORY, entries);
    assert.equal(membershipLabel(options.find((option) => option.id === 'workspace_old')!), 'Old · your role: owner · archived (read-only)');
    assert.equal(membershipLabel(options.find((option) => option.id === 'workspace_new')!), 'New · your role: viewer');
  });
});
