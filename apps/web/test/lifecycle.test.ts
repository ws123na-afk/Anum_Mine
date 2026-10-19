// Unit tests for organisation export and deletion helpers in src/lib/lifecycle.ts
// (docs/organisation-lifecycle.md). Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import { setLocale } from '../src/lib/i18n.ts';
import {
  absoluteUrl,
  archiveSize,
  canRequestExport,
  confirmationMatches,
  coolingOffText,
  hasActiveExport,
  identityText,
  isOpenDeletion,
  mapDeletedOrganisation,
  sortDeleted,
  type ApiDeletedOrganisation,
  mapExportLink,
  mapLifecycle,
  shouldPoll,
  statusLabel,
  statusLabelKey,
  statusTone,
  timeLeftText,
  timeUntil,
  type ApiLifecycleJob,
  type ApiLifecyclePage,
} from '../src/lib/lifecycle.ts';
import { lifecycleEn } from '../src/lib/messages/lifecycle.en.ts';
import { lifecycleAr } from '../src/lib/messages/lifecycle.ar.ts';

afterEach(() => setLocale('en'));

function job(overrides: Partial<ApiLifecycleJob> = {}): ApiLifecycleJob {
  return {
    id: 'job_1', kind: 'organisation_export', status: 'ready', workspace_id: 'workspace_foundation', requested_by: 'user_local',
    requested_at: '2026-10-18T10:00:00Z', purge_after: null, started_at: '2026-10-18T10:00:05Z', finished_at: '2026-10-18T10:02:00Z',
    cancelled_at: null, cancelled_by: null, expires_at: '2026-10-25T10:02:00Z', archive_bytes: 2_621_440, archive_sha256: 'abc',
    row_count: 1200, file_count: 3, error: null, ...overrides,
  };
}

function page(overrides: Partial<ApiLifecyclePage> = {}): ApiLifecyclePage {
  return {
    can_manage_organisation: true, can_delete_workspace: true, organisation_confirmation: 'tenant_local', workspace_confirmation: 'Foundation',
    cooling_off_hours: 168, export_link_ttl_seconds: 900, export_retention_hours: 168, exports: [job()],
    organisation_deletion: null, workspace_deletion: null, ...overrides,
  };
}

describe('organisation lifecycle helpers', () => {
  test('maps the page and its jobs from snake_case to camelCase', () => {
    const mapped = mapLifecycle(page({ organisation_deletion: job({ id: 'del_1', kind: 'organisation_deletion', status: 'scheduled', purge_after: '2026-10-25T10:00:00Z' }) }));
    assert.equal(mapped.canManageOrganisation, true);
    assert.equal(mapped.organisationConfirmation, 'tenant_local');
    assert.equal(mapped.workspaceConfirmation, 'Foundation');
    assert.equal(mapped.coolingOffHours, 168);
    assert.equal(mapped.exports[0].archiveBytes, 2_621_440);
    assert.equal(mapped.exports[0].rowCount, 1200);
    assert.equal(mapped.exports[0].expiresAt, '2026-10-25T10:02:00Z');
    assert.equal(mapped.organisationDeletion?.purgeAfter, '2026-10-25T10:00:00Z');
    assert.equal(mapped.workspaceDeletion, null);
    assert.deepEqual(mapLifecycle(page({ exports: null })).exports, []);
    assert.deepEqual(mapExportLink({ url: '/api/v1/x', expires_at: '2026-10-18T10:15:00Z' }), { url: '/api/v1/x', expiresAt: '2026-10-18T10:15:00Z' });
  });

  test('status labels and tones cover every status; an unknown one reads as unknown', () => {
    for (const status of ['queued', 'running', 'ready', 'failed', 'expired', 'scheduled', 'cancelled', 'purging', 'completed']) {
      assert.equal(statusLabelKey(status), `lifecycle.status.${status}`);
    }
    assert.equal(statusLabelKey('mystery'), 'lifecycle.status.unknown');
    assert.equal(statusLabel('ready'), 'Ready');
    assert.equal(statusTone('ready'), 'success');
    assert.equal(statusTone('running'), 'warning');
    assert.equal(statusTone('scheduled'), 'warning');
    assert.equal(statusTone('failed'), 'danger');
    assert.equal(statusTone('cancelled'), 'neutral');
  });

  test('the typed confirmation must match exactly, ignoring outer spaces only', () => {
    assert.equal(confirmationMatches('tenant_local', 'tenant_local'), true);
    assert.equal(confirmationMatches('  tenant_local ', 'tenant_local'), true);
    assert.equal(confirmationMatches('Tenant_local', 'tenant_local'), false);
    assert.equal(confirmationMatches('tenant', 'tenant_local'), false);
    assert.equal(confirmationMatches('', ''), false);
  });

  test('an export in progress blocks a new one and keeps the page polling', () => {
    const idle = mapLifecycle(page());
    assert.equal(hasActiveExport(idle.exports), false);
    assert.equal(canRequestExport(idle), true);
    assert.equal(shouldPoll(idle), false);
    const busy = mapLifecycle(page({ exports: [job({ status: 'running' })] }));
    assert.equal(canRequestExport(busy), false);
    assert.equal(shouldPoll(busy), true);
    const purging = mapLifecycle(page({ organisation_deletion: job({ kind: 'organisation_deletion', status: 'purging' }) }));
    assert.equal(canRequestExport(purging), false);
    assert.equal(shouldPoll(purging), true);
    assert.equal(canRequestExport(mapLifecycle(page({ can_manage_organisation: false }))), false);
  });

  test('open deletions are scheduled or purging', () => {
    const map = (status: ApiLifecycleJob['status']) => mapLifecycle(page({ workspace_deletion: job({ kind: 'workspace_deletion', status }) })).workspaceDeletion;
    assert.equal(isOpenDeletion(map('scheduled')), true);
    assert.equal(isOpenDeletion(map('purging')), true);
    assert.equal(isOpenDeletion(map('cancelled')), false);
    assert.equal(isOpenDeletion(map('failed')), false);
    assert.equal(isOpenDeletion(null), false);
  });

  test('archive sizes and cooling-off periods read for a person', () => {
    assert.equal(archiveSize(null), '-');
    assert.equal(archiveSize(512), '512 B');
    assert.equal(archiveSize(2_621_440), '2.5 MB');
    assert.equal(coolingOffText(168), '7 days');
    assert.equal(coolingOffText(24), '1 day');
    assert.equal(coolingOffText(36), '36 hours');
    assert.equal(coolingOffText(1), '1 hour');
  });

  test('time left until the purge, in days, hours or minutes', () => {
    const now = Date.parse('2026-10-18T10:00:00Z');
    assert.deepEqual(timeUntil('2026-10-25T13:00:00Z', now), { days: 7, hours: 3, minutes: 0, due: false });
    assert.deepEqual(timeUntil('2026-10-18T09:00:00Z', now), { days: 0, hours: 0, minutes: 0, due: true });
    assert.deepEqual(timeUntil('not a date', now).due, true);
    assert.equal(timeLeftText('2026-10-25T13:00:00Z', now), '7 days and 3 hours left');
    assert.equal(timeLeftText('2026-10-19T10:00:00Z', now), '1 day left');
    assert.equal(timeLeftText('2026-10-18T12:30:00Z', now), '2 hours left');
    assert.equal(timeLeftText('2026-10-18T10:45:00Z', now), '45 minutes left');
    assert.equal(timeLeftText('2026-10-18T09:00:00Z', now), 'Due now');
    setLocale('ar');
    assert.equal(timeLeftText('2026-10-20T10:00:00Z', now), 'متبقٍ يومان');
  });

  test('a relative download path becomes absolute against the API base', () => {
    assert.equal(absoluteUrl('http://localhost:8000', '/api/v1/organization/lifecycle/exports/download/tok'), 'http://localhost:8000/api/v1/organization/lifecycle/exports/download/tok');
    assert.equal(absoluteUrl('https://api.example.test/', 'api/v1/x'), 'https://api.example.test/api/v1/x');
    assert.equal(absoluteUrl('http://localhost:8000', 'https://files.example.test/a'), 'https://files.example.test/a');
  });

  test('the Arabic catalog has every English key', () => {
    assert.deepEqual(Object.keys(lifecycleAr).sort(), Object.keys(lifecycleEn).sort());
  });
});

describe('deleted organisations in the platform console', () => {
  const receipt = (overrides: Partial<ApiDeletedOrganisation> = {}): ApiDeletedOrganisation => ({
    tenant_id: 'acme', display_name: 'Acme Ltd', plan: 'trial', requested_by: 'admin_1',
    requested_at: '2026-10-10T10:00:00Z', purge_started_at: '2026-10-17T10:00:00Z', deleted_at: '2026-10-17T10:05:00Z',
    status: 'deleted', row_count: 52, file_count: 2, counts: { tasks: 40, work_items: 12, favorites: 0 },
    identity_cleanup: { status: 'removed', removed: 3, detached: 1, finished_at: '2026-10-17T10:06:00Z' },
    ...overrides,
  });

  test('maps a receipt with the largest tables first and empty ones left out', () => {
    const row = mapDeletedOrganisation(receipt());
    assert.equal(row.name, 'Acme Ltd');
    assert.equal(row.deleting, false);
    assert.deepEqual(row.counts, [{ table: 'tasks', rows: 40 }, { table: 'work_items', rows: 12 }]);
    assert.equal(mapDeletedOrganisation(receipt({ display_name: null })).name, 'acme');
    assert.equal(mapDeletedOrganisation(receipt({ status: 'purging', deleted_at: null })).deleting, true);
  });

  test('sorts newest deletion first, a running purge by when it started', () => {
    const rows = sortDeleted([
      mapDeletedOrganisation(receipt({ tenant_id: 'old', deleted_at: '2026-09-01T00:00:00Z' })),
      mapDeletedOrganisation(receipt({ tenant_id: 'running', status: 'purging', deleted_at: null, purge_started_at: '2026-10-18T00:00:00Z' })),
      mapDeletedOrganisation(receipt({ tenant_id: 'recent' })),
    ]);
    assert.deepEqual(rows.map((r) => r.tenantId), ['running', 'recent', 'old']);
  });

  test('says what happened to the sign-in accounts in both languages', () => {
    const identity = mapDeletedOrganisation(receipt()).identity;
    assert.equal(identityText(identity), '3 removed, 1 kept for their other organisations');
    assert.match(identityText({ status: 'not_configured', removed: 0, detached: 0 }), /Keycloak/);
    setLocale('ar');
    assert.equal(identityText(identity), 'حُذف 3، وبقي 1 لمؤسساتهم الأخرى');
  });
});
