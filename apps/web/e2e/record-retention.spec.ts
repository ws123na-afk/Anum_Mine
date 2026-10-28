import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

// Settings > Workspace: how long meeting transcripts and visitor records are kept, against a mocked API
// (owner decision O5, docs/record-retention.md).
type Json = Record<string, unknown>;
const URL = '/api/v1/workspace/record-retention';
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });

interface Options { viewer?: boolean; failFirst?: boolean; denied?: boolean; staleOnce?: boolean }

function view(transcriptDays: number | null, visitorDays: number | null, revision: number, manage: boolean): Json {
  const period = (days: number | null, standard: number, min: number) => ({ days: days ?? standard, is_default: days === null, default_days: standard, min_days: min, max_days: 1825 });
  return {
    transcripts: period(transcriptDays, 90, 7), visitors: period(visitorDays, 365, 30), revision,
    updated_by: revision ? 'owner_example' : null, updated_at: revision ? '2026-10-28T09:00:00Z' : null,
    snapshots_stored: false, can_manage: manage,
  };
}

async function mockApi(page: Page, options: Options = {}) {
  const puts: Json[] = [];
  let failures = options.failFirst ? 1 : 0, stale = Boolean(options.staleOnce);
  let state = { transcripts: null as number | null, visitors: null as number | null, revision: 0 };
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const path = new globalThis.URL(request.url()).pathname;
    const method = request.method();
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals') return json([]);
    if (path === URL && method === 'GET') {
      if (options.denied) return json(envelope('permission_denied', 'Active workspace membership required'), 403);
      if (failures > 0) { failures -= 1; return json(envelope('service_unavailable', 'Office persistence needs the PostgreSQL backend'), 503); }
      return json(view(state.transcripts, state.visitors, state.revision, !options.viewer));
    }
    if (path === URL && method === 'PUT') {
      const body = request.postDataJSON() as Json;
      puts.push(body);
      if (stale) {
        stale = false;
        state = { transcripts: 60, visitors: null, revision: state.revision + 1 };
        return json(envelope('conflict', 'The retention setting changed; reload and try again'), 409);
      }
      if (body.expected_revision !== state.revision) return json(envelope('conflict', 'The retention setting changed; reload and try again'), 409);
      state = { transcripts: body.transcript_days as number | null, visitors: body.visitor_days as number | null, revision: state.revision + 1 };
      return json(view(state.transcripts, state.visitors, state.revision, true));
    }
    if (path === '/api/v1/organization/lifecycle') {
      return json({ can_manage_organisation: !options.viewer, can_delete_workspace: !options.viewer, organisation_confirmation: 'tenant_local',
        workspace_confirmation: 'Foundation', cooling_off_hours: 168, export_link_ttl_seconds: 900, export_retention_hours: 168,
        exports: [], organisation_deletion: null, workspace_deletion: null });
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
  return puts;
}

const card = (page: Page) => page.getByRole('region', { name: 'How long records are kept' });

test('an owner sees the standard periods, changes one, is told about bad input and goes back to the standard', async ({ page }) => {
  const puts = await mockApi(page);
  await page.goto('/#settings');
  const transcripts = card(page).getByRole('region', { name: 'Meeting transcripts' });
  const visitors = card(page).getByRole('region', { name: 'Visitor records' });
  await expect(transcripts.getByTestId('retention-transcripts')).toHaveText('90 days');
  await expect(visitors.getByTestId('retention-visitors')).toHaveText('365 days');
  await expect(transcripts.getByText('Standard', { exact: true })).toBeVisible();
  await expect(transcripts.getByText(/deleted 90 days after it ends\. The meeting, its summary, confirmed decisions/)).toBeVisible();
  await expect(card(page).getByText('Office history snapshots: none are stored, so there is nothing to delete.')).toBeVisible();
  await expect(card(page).getByText('Nobody has changed these periods.')).toBeVisible();

  const input = transcripts.getByLabel('Days to keep, from 7 to 1,825');
  await input.fill('5');
  await transcripts.getByRole('button', { name: 'Save' }).click();
  await expect(transcripts.getByRole('alert')).toHaveText('Choose from 7 to 1,825 days.');
  await input.fill('two');
  await input.press('Enter');
  await expect(transcripts.getByRole('alert')).toHaveText('Enter whole days, for example 90.');
  expect(puts).toEqual([]);

  await input.fill('30');
  await transcripts.getByRole('button', { name: 'Save' }).click();
  await expect(transcripts.getByRole('status')).toHaveText('Saved. The next daily clean-up uses 30 days.');
  expect(puts[0]).toEqual({ transcript_days: 30, visitor_days: null, expected_revision: 0, input_mode: 'click' });
  await expect(transcripts.getByTestId('retention-transcripts')).toHaveText('30 days');
  await expect(transcripts.getByText('Set by an owner')).toBeVisible();
  await expect(card(page).getByText(/Last changed by owner_example/)).toBeVisible();

  await visitors.getByLabel('Days to keep, from 30 to 1,825').fill('730');
  await visitors.getByRole('button', { name: 'Save' }).click();
  expect(puts[1]).toEqual({ transcript_days: 30, visitor_days: 730, expected_revision: 1, input_mode: 'click' });
  await expect(visitors.getByTestId('retention-visitors')).toHaveText('730 days');

  await transcripts.getByRole('button', { name: 'Use the standard (90 days)' }).click();
  await expect(transcripts.getByRole('status')).toHaveText('Saved. The standard 90 days applies again.');
  expect(puts[2]).toEqual({ transcript_days: null, visitor_days: 730, expected_revision: 2, input_mode: 'click' });
  await expect(transcripts.getByRole('button', { name: /Use the standard/ })).toHaveCount(0);
  await expect(visitors.getByTestId('retention-visitors')).toHaveText('730 days');
});

test('a stale save says so and shows what is stored now', async ({ page }) => {
  await mockApi(page, { staleOnce: true });
  await page.goto('/#settings');
  const transcripts = card(page).getByRole('region', { name: 'Meeting transcripts' });
  await transcripts.getByLabel(/Days to keep/).fill('45');
  await transcripts.getByRole('button', { name: 'Save' }).click();
  await expect(transcripts.getByRole('alert')).toHaveText(/Someone changed this setting a moment ago/);
  await expect(transcripts.getByTestId('retention-transcripts')).toHaveText('60 days');
});

test('a member reads the periods without a form and is told who can change them', async ({ page }) => {
  const puts = await mockApi(page, { viewer: true });
  await page.goto('/#settings');
  await expect(card(page).getByTestId('retention-visitors')).toHaveText('365 days');
  await expect(card(page).getByText('Only a workspace owner can change how long records are kept.')).toBeVisible();
  await expect(card(page).getByRole('button', { name: 'Save' })).toHaveCount(0);
  expect(puts).toEqual([]);
});

test('a failed read says what failed and tries again; a refusal says who to ask', async ({ page }) => {
  await mockApi(page, { failFirst: true });
  await page.goto('/#settings');
  await expect(card(page).getByRole('alert')).toContainText("Couldn't read how long records are kept");
  await card(page).getByRole('button', { name: 'Try again' }).click();
  await expect(card(page).getByTestId('retention-transcripts')).toHaveText('90 days');
});

test('without membership the card explains who can add the person', async ({ page }) => {
  await mockApi(page, { denied: true });
  await page.goto('/#settings');
  await expect(card(page).getByRole('alert')).toContainText('Ask a workspace owner to add you to this workspace.');
  await expect(card(page).getByRole('button', { name: 'Try again' })).toHaveCount(0);
});

test('the card has no accessibility violations and fits every width', async ({ page }) => {
  await mockApi(page);
  for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]]) {
    await page.setViewportSize({ width, height });
    await page.goto('/#settings');
    await expect(card(page).getByTestId('retention-transcripts')).toHaveText('90 days');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `horizontal overflow at ${width}`).toBeLessThanOrEqual(0);
    const box = await card(page).boundingBox();
    expect(box && box.x >= 0 && box.x + box.width <= width + 1, `card fits at ${width}`).toBe(true);
    if (process.env.RETENTION_SHOTS) await card(page).screenshot({ path: `${process.env.RETENTION_SHOTS}/retention-${width}x${height}.png` });
  }
  const results = await new AxeBuilder({ page }).include('section[aria-label="How long records are kept"]').analyze();
  expect(results.violations.map((v) => v.id)).toEqual([]);
});

test('Arabic: the card reads right to left with Arabic words and Western digits', async ({ page }) => {
  await page.addInitScript(() => { try { localStorage.setItem('anum.locale', 'ar'); } catch { /* storage off */ } });
  await mockApi(page);
  await page.goto('/#settings');
  const region = page.getByRole('region', { name: 'مدة الاحتفاظ بالسجلات' });
  await expect(region.getByRole('region', { name: 'نصوص الاجتماعات' })).toBeVisible();
  await expect(region.getByTestId('retention-transcripts')).toContainText('90');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  if (process.env.RETENTION_SHOTS) await region.screenshot({ path: `${process.env.RETENTION_SHOTS}/retention-ar.png` });
});
