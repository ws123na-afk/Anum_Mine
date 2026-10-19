import { expect, test, type Page } from '@playwright/test';

// Settings > Workspace: organisation export and deletion, and workspace deletion, against a
// mocked API (docs/organisation-lifecycle.md).
type Json = Record<string, unknown>;
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });
const DOWNLOAD_PATH = '/api/v1/organization/lifecycle/exports/download/tok_example';

function job(overrides: Json = {}): Json {
  return {
    id: 'exp_ready', kind: 'organisation_export', status: 'ready', workspace_id: 'workspace_foundation', requested_by: 'user_local',
    requested_at: '2026-10-17T09:00:00Z', purge_after: null, started_at: '2026-10-17T09:00:05Z', finished_at: '2026-10-17T09:02:00Z',
    cancelled_at: null, cancelled_by: null, expires_at: '2099-10-24T09:02:00Z', archive_bytes: 2_621_440, archive_sha256: 'abc',
    row_count: 1200, file_count: 3, error: null, ...overrides,
  };
}

interface Options { viewer?: boolean; failFirst?: boolean; noExports?: boolean; orgScheduled?: boolean }

async function mockApi(page: Page, options: Options = {}) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  let failures = options.failFirst ? 1 : 0;
  let exports: Json[] = options.viewer || options.noExports ? [] : [job(), job({ id: 'exp_failed', status: 'failed', requested_at: '2026-10-16T09:00:00Z', archive_bytes: null, row_count: null, file_count: null, expires_at: null, error: 'The archive is larger than 512 MiB.' })];
  const later = () => new Date(Date.now() + 7 * 86_400_000 + 3_600_000).toISOString();
  const deletion = (kind: string, status = 'scheduled'): Json => job({ id: `del_${kind}`, kind, status, purge_after: later(), archive_bytes: null, row_count: null, file_count: null, expires_at: null });
  let organisationDeletion: Json | null = options.orgScheduled ? deletion('organisation_deletion') : null;
  let workspaceDeletion: Json | null = null;
  let getsSinceRequest = -1;

  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    calls.push({ method, path, body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });

    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals') return json([]);
    if (path === '/api/v1/organization/lifecycle' && method === 'GET') {
      if (failures > 0) { failures -= 1; return json(envelope('service_unavailable', 'The database is unavailable'), 503); }
      if (getsSinceRequest >= 0) {
        getsSinceRequest += 1;
        if (getsSinceRequest >= 2) exports = exports.map((row) => row.id === 'exp_new' ? { ...row, status: 'ready', archive_bytes: 4096, row_count: 50, file_count: 0, expires_at: '2099-10-25T10:00:00Z' } : row);
      }
      return json({
        can_manage_organisation: !options.viewer, can_delete_workspace: !options.viewer,
        organisation_confirmation: 'tenant_local', workspace_confirmation: 'Foundation',
        cooling_off_hours: 168, export_link_ttl_seconds: 900, export_retention_hours: 168,
        exports, organisation_deletion: organisationDeletion, workspace_deletion: workspaceDeletion,
      });
    }
    if (path === '/api/v1/organization/lifecycle/exports' && method === 'POST') {
      const queued = job({ id: 'exp_new', status: 'queued', requested_at: new Date().toISOString(), archive_bytes: null, row_count: null, file_count: null, expires_at: null, finished_at: null });
      exports = [queued, ...exports];
      getsSinceRequest = 0;
      return json(queued, 202);
    }
    if (path.startsWith('/api/v1/organization/lifecycle/exports/') && path.endsWith('/link')) {
      return json({ url: DOWNLOAD_PATH, expires_at: '2099-10-18T10:15:00Z' });
    }
    if (path === DOWNLOAD_PATH) {
      return route.fulfill({ status: 200, contentType: 'application/zip', body: 'PK', headers: { 'content-disposition': 'attachment; filename="organisation-export.zip"', 'cache-control': 'no-store' } });
    }
    if (path === '/api/v1/organization/lifecycle/deletion') {
      if ((body as Json).confirm !== 'tenant_local') return json(envelope('validation_error', 'Type the organisation id'), 422);
      organisationDeletion = deletion('organisation_deletion');
      return json(organisationDeletion, 202);
    }
    if (path === '/api/v1/organization/lifecycle/deletion/cancel') {
      const cancelled = { ...organisationDeletion, status: 'cancelled' };
      organisationDeletion = null;
      return json(cancelled);
    }
    if (path === '/api/v1/workspaces/current/deletion') {
      if ((body as Json).confirm_name !== 'Foundation') return json(envelope('validation_error', 'Type the workspace name'), 422);
      workspaceDeletion = deletion('workspace_deletion');
      return json(workspaceDeletion, 202);
    }
    if (path === '/api/v1/workspaces/current/deletion/cancel') {
      const cancelled = { ...workspaceDeletion, status: 'cancelled' };
      workspaceDeletion = null;
      return json(cancelled);
    }
    if (path === '/api/v1/workspaces/current' && method === 'GET') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', archived_at: null, archived_by: null });
    if (path === '/api/v1/workspaces/current/permissions') {
      const manage = !options.viewer;
      return json({ workspace_id: 'workspace_foundation', role: manage ? 'owner' : 'viewer', organization_admin: manage, can_manage_workspace: manage, can_create_workspace: manage, can_manage_members: manage, can_read_audit: manage, can_export_audit: manage });
    }
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: request.headers()['x-workspace-id'], user_id: 'user_local', role: options.viewer ? 'viewer' : 'owner', active: true, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' });
    if (path === '/api/v1/onboarding') return json({ complete: true, model_configured: true });
    if (path === '/api/v1/notification-preferences') return json({ task_completed: true, approval_required: true, run_failed: true, automation_failed: true, email_enabled: false, desktop_enabled: false });
    return route.abort();
  });
  return calls;
}

const section = (page: Page) => page.getByRole('region', { name: 'Data export and deletion' });

test('an owner sees exports, requests one that becomes ready, and downloads a ready one', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#settings');
  const exportsCard = section(page).getByRole('region', { name: 'Export organisation data' });
  const list = exportsCard.getByRole('list', { name: 'Recent exports' });
  await expect(list.getByRole('listitem')).toHaveCount(2);
  await expect(list.getByText('Ready', { exact: true })).toBeVisible();
  await expect(list.getByText('2.5 MB')).toBeVisible();
  await expect(list.getByText('The export failed: The archive is larger than 512 MiB.')).toBeVisible();
  await expect(exportsCard.getByText(/Each person downloads their own under Settings > Privacy/)).toBeVisible();

  const download = page.waitForRequest((request) => new URL(request.url()).pathname === DOWNLOAD_PATH);
  await list.getByRole('button', { name: /Download the export requested/ }).click();
  await download;
  expect(calls.some((c) => c.method === 'POST' && c.path === '/api/v1/organization/lifecycle/exports/exp_ready/link')).toBe(true);
  await expect(section(page).getByText('Download started. The link works for a few minutes only.')).toBeVisible();

  await exportsCard.getByRole('button', { name: 'Export organisation data' }).click();
  await expect(section(page).getByText(/Export requested\. It is built in the background/)).toBeVisible();
  await expect(list.getByRole('listitem')).toHaveCount(3);
  await expect(list.getByText('Queued', { exact: true })).toBeVisible();
  await expect(exportsCard.getByRole('button', { name: 'Export organisation data' })).toBeDisabled();
  // The page polls every 5 seconds while an export is queued or running.
  await expect(list.getByRole('button', { name: /Download the export requested/ })).toHaveCount(2, { timeout: 15_000 });
  await expect(exportsCard.getByRole('button', { name: 'Export organisation data' })).toBeEnabled();
});

test('an owner schedules organisation deletion only after typing the id, sees the banner and cancels', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#settings');
  const card = section(page).getByRole('region', { name: 'Delete the organisation' });
  await expect(card.getByText(/Nothing is deleted for 7 days/)).toBeVisible();
  await card.getByRole('button', { name: 'Delete organisation' }).click();
  const dialog = page.getByRole('alertdialog', { name: 'Delete this organisation?' });
  const input = dialog.getByLabel('Organisation ID');
  await expect(input).toBeFocused();
  const confirm = dialog.getByRole('button', { name: 'Delete organisation' });
  await expect(confirm).toBeDisabled();
  await input.fill('tenant');
  await expect(dialog.getByText('That is not the organisation ID. Type it exactly as shown.')).toBeVisible();
  await expect(confirm).toBeDisabled();
  await input.fill(' tenant_local ');
  await confirm.click();
  await expect(dialog).toHaveCount(0);
  expect(calls.find((c) => c.path === '/api/v1/organization/lifecycle/deletion')?.body).toEqual({ confirm: 'tenant_local' });

  const banner = section(page).getByRole('status').filter({ hasText: 'This organisation is scheduled for deletion' });
  await expect(banner).toBeVisible();
  await expect(banner.getByText(/7 days and 1 hour left/)).toBeVisible();
  await expect(card.getByRole('button', { name: 'Delete organisation' })).toHaveCount(0);
  await banner.getByRole('button', { name: 'Cancel organisation deletion' }).click();
  await expect(section(page).getByText('Organisation deletion cancelled. Nothing was deleted.')).toBeVisible();
  await expect(banner).toHaveCount(0);
  await expect(card.getByRole('button', { name: 'Delete organisation' })).toBeVisible();
});

test('an owner schedules and cancels deleting this workspace with its name', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#settings');
  const card = section(page).getByRole('region', { name: 'Delete this workspace' });
  await card.getByRole('button', { name: 'Delete workspace' }).click();
  const dialog = page.getByRole('alertdialog', { name: 'Delete Foundation?' });
  await expect(dialog.getByRole('button', { name: 'Delete workspace' })).toBeDisabled();
  await dialog.getByLabel('Workspace name').fill('foundation');
  await expect(dialog.getByText('That is not the workspace name. Type it exactly as shown.')).toBeVisible();
  await dialog.getByLabel('Workspace name').fill('Foundation');
  await dialog.getByRole('button', { name: 'Delete workspace' }).click();
  expect(calls.find((c) => c.path === '/api/v1/workspaces/current/deletion')?.body).toEqual({ confirm_name: 'Foundation' });
  const banner = section(page).getByRole('status').filter({ hasText: 'This workspace is scheduled for deletion' });
  await expect(banner).toBeVisible();
  await expect(card.getByText(/A deletion is scheduled/)).toBeVisible();
  await banner.getByRole('button', { name: 'Cancel workspace deletion' }).click();
  await expect(section(page).getByText('Workspace deletion cancelled. Nothing was deleted.')).toBeVisible();
  await expect(card.getByRole('button', { name: 'Delete workspace' })).toBeVisible();
});

test('a viewer sees the facts and who can act, with no controls', async ({ page }) => {
  await mockApi(page, { viewer: true, orgScheduled: true });
  await page.goto('/#settings');
  const panel = section(page);
  await expect(panel.getByText('This organisation is scheduled for deletion')).toBeVisible();
  await expect(panel.getByText('An organisation administrator who is an owner of this workspace can cancel it.')).toBeVisible();
  await expect(panel.getByText('Exporting the organisation needs an organisation administrator who is also an owner of this workspace.')).toBeVisible();
  await expect(panel.getByText('Deleting this workspace needs an owner of this workspace.')).toBeVisible();
  await expect(panel.getByRole('button')).toHaveCount(0);
});

test('an owner with no exports is offered the first one', async ({ page }) => {
  await mockApi(page, { noExports: true });
  await page.goto('/#settings');
  await expect(section(page).getByText('No exports yet. Choose Export organisation data to build the first archive.')).toBeVisible();
});

test('a failed load says what failed and retries', async ({ page }) => {
  await mockApi(page, { failFirst: true });
  await page.goto('/#settings');
  const panel = section(page);
  await expect(panel.getByRole('alert')).toContainText("Couldn't load data export and deletion: The server could not reach its database");
  await panel.getByRole('button', { name: 'Try again' }).click();
  await expect(panel.getByRole('list', { name: 'Recent exports' })).toBeVisible();
});

const sizes = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const [width, height] of sizes) {
  test(`data export and deletion fit at ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await mockApi(page, { orgScheduled: true });
    await page.goto('/#settings');
    await expect(section(page).getByRole('list', { name: 'Recent exports' })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `horizontal overflow at ${width}px`).toBeLessThanOrEqual(0);
    const box = await section(page).boundingBox();
    expect(box && box.x >= 0 && box.x + box.width <= width + 1).toBe(true);
  });
}
