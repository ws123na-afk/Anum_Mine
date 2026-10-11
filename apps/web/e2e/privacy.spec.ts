import { expect, test, type Page } from '@playwright/test';

// Settings -> Privacy (docs/privacy.md): what is stored, retention, Download my data and Delete my data.
const counts = { conversations: 2, conversation_turns: 6, private_memories: 1, retrieval_index_entries: 1, voice_sessions: 1, voice_transcript_segments: 3, notification_preferences: 1 };
const zero = Object.fromEntries(Object.keys(counts).map((key) => [key, 0]));
const shared = { tasks_created: 4, approvals_decided: 1, shared_memories_created: 0, files_uploaded: 2 };

function summary(privateCounts: Record<string, number>) {
  return {
    tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'owner',
    private: privateCounts, shared,
    retention: { conversation_days: 30, voice_transcript_days: 30, voice_transcript_choices: ['session', '30_days', 'permanent'], private_memories: 'Kept until you delete them (Memory, or Delete my data).', task_memories: 'Kept with their task, or until the expiry set when they were saved.', audit_records: 'Kept as the workspace’s security record; they hold ids and counts, not your content.' },
    model_provider: { deployment: 'anthropic', workspace: null, conversation_memory_enabled: true },
    privacy_policy_url: 'https://example.com/privacy', delete_confirmation: 'DELETE MY DATA', export_max_bytes: 26214400,
    account_note: 'Your sign-in account is managed by your organization’s identity provider, not by ANUM. To close it, ask your workspace owner or organization administrator.',
    shared_note: 'Tasks, runs, approvals, shared memories and files belong to the workspace, so they are kept. Ask a workspace owner to remove them.',
  };
}

async function mockApi(page: Page, options: { summaryStatus?: number; exportStatus?: number } = {}) {
  let current: Record<string, number> = { ...counts };
  const calls = { deleted: [] as unknown[] };
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/approvals' || path === '/api/v1/tasks') return json([]);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'owner', active: true, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z' });
    if (path === '/api/v1/privacy') {
      if (options.summaryStatus) return json({ detail: 'Workspace permission denied' }, options.summaryStatus);
      return json(summary(current));
    }
    if (path === '/api/v1/privacy/export') {
      if (options.exportStatus) return json({ detail: 'Your data is larger than this server’s export limit (25 MB). Delete conversations you no longer need, or ask your administrator to raise ANUM_PRIVACY_EXPORT_MAX_BYTES, then try again.' }, options.exportStatus);
      return route.fulfill({ status: 200, contentType: 'application/json', headers: { 'content-disposition': 'attachment; filename="anum-my-data-20261011-090000.json"', 'access-control-allow-origin': '*', 'access-control-expose-headers': 'X-Correlation-ID, Retry-After, Content-Disposition' }, body: JSON.stringify({ format: 'anum.privacy-export/v1' }) });
    }
    if (path === '/api/v1/privacy/delete' && request.method() === 'POST') {
      calls.deleted.push(request.postDataJSON());
      const deleted = current;
      current = { ...zero };
      return json({ deleted, kept: shared, account_note: summary(current).account_note, shared_note: summary(current).shared_note });
    }
    return route.abort();
  });
  return calls;
}

test('a member sees what is stored, downloads it and deletes their private data after typing the phrase', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#privacy');
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Privacy' })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('heading', { name: 'What ANUM stores about you' })).toBeVisible();
  await expect(page.getByText('Assistant conversations').first()).toBeVisible();
  await expect(page.getByText(/Deleted after 30 days without activity/)).toBeVisible();
  await expect(page.getByText(/sent to Anthropic \(Claude\)/)).toBeVisible();
  await expect(page.getByRole('link', { name: 'Read the privacy policy' })).toHaveAttribute('href', 'https://example.com/privacy');

  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download my data' }).click();
  expect((await download).suggestedFilename()).toBe('anum-my-data-20261011-090000.json');
  await expect(page.getByText(/Downloaded anum-my-data-20261011-090000.json/)).toBeVisible();

  const remove = page.getByRole('button', { name: 'Delete my data' });
  await expect(remove).toBeDisabled();
  await page.getByLabel('Type DELETE MY DATA to confirm').fill('delete my data');
  await expect(remove).toBeDisabled();
  await page.getByLabel('Type DELETE MY DATA to confirm').fill('DELETE MY DATA');
  await remove.click();
  await expect(page.getByText(/Deleted 15 private items\. 7 shared workspace records stay\./)).toBeVisible();
  expect(calls.deleted).toEqual([{ confirm: 'DELETE MY DATA' }]);
  await expect(page.getByText('Nothing private is stored for you here yet.', { exact: false })).toBeVisible();
});

test('an export over the server limit says how to fix it', async ({ page }) => {
  await mockApi(page, { exportStatus: 413 });
  await page.goto('/#privacy');
  await page.getByRole('button', { name: 'Download my data' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'ANUM_PRIVACY_EXPORT_MAX_BYTES' })).toBeVisible();
});

test('a caller without an active membership is told who can help', async ({ page }) => {
  await mockApi(page, { summaryStatus: 403 });
  await page.goto('/#privacy');
  await expect(page.getByRole('alert').filter({ hasText: 'Ask a workspace owner to restore your membership' })).toBeVisible();
});

test('the Workspace and Privacy sections switch from Settings', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#privacy');
  await page.getByRole('button', { name: 'Workspace', exact: true }).click();
  await expect(page).toHaveURL(/#settings$/);
  await page.getByRole('button', { name: 'Privacy', exact: true }).click();
  await expect(page).toHaveURL(/#privacy$/);
  await expect(page.getByRole('heading', { name: 'Delete my data' })).toBeVisible();
});
