import { expect, test, type Page } from '@playwright/test';

// Developer mode (docs/developer-mode.md): owners ask for a change, follow it to a pull request and
// approve the merge with the payload hash shown; everyone else sees who can use it.
const now = '2026-10-11T09:00:00.000Z';
const later = new Date(Date.now() + 6 * 3600_000).toISOString();
const hash = '7c'.repeat(32);
const stages = ['queued', 'coding', 'pr_opened', 'ci', 'approval', 'merged'];
const labels: Record<string, string> = { queued: 'Queued', coding: 'Coding', pr_opened: 'Pull request opened', ci: 'CI checks', approval: 'Waiting for approval', merged: 'Merged' };
const timeline = (active: string, failed = false) => stages.map((id, index) => ({
  id, label: labels[id],
  state: active === 'all' || index < stages.indexOf(active) ? 'done' : index === stages.indexOf(active) ? (failed ? 'failed' : 'active') : 'pending',
}));

const setupReady = { configured: true, enabled_here: true, repository: 'acme/anum', workflow: 'anum-developer.yml', base_branch: 'main', missing: [], message: 'Ready', max_requests_per_day: 10, requests_today: 0 };

function devRequest(id: string, text: string, status: string, extra: Record<string, unknown> = {}) {
  return {
    id, created_by: 'user_local', text, source: 'text', status, allow_protected_paths: false, branch: `anum-dev/${id}`,
    run_id: null, run_url: null, pr_number: null, pr_url: null, pr_title: null, pr_draft: null, head_sha: null, checks: null,
    failure_reason: null, merge_error: null, approval_id: null, merge_commit_sha: null, created_at: now, updated_at: now,
    merged_at: null, synced_at: null, timeline: timeline(status === 'queued' ? 'queued' : 'coding'), summary: 'Queued: waiting for the developer workflow to start.', approval: null, ...extra,
  };
}

const approval = {
  id: 'approval_dev', task_id: 'task_merge', action: 'developer.merge_pull_request', risk_level: 'high', status: 'pending',
  reason: 'External or high-impact actions require explicit approval. Proposed action: Squash-merge pull request #7',
  created_at: now, run_id: 'run_merge', step_id: 'step_merge', payload_hash: hash, expires_at: later, target: 'api.github.com',
  arguments: { request_id: 'dev_one', repository: 'acme/anum', pull_number: 7, head_sha: 'a'.repeat(40), base: 'main', merge_method: 'squash', title: 'Add a dark/light toggle to settings' },
};

async function mockApi(page: Page, { role = 'owner', setup = setupReady }: { role?: string; setup?: Record<string, unknown> } = {}) {
  const sent: { creates: unknown[]; decisions: unknown[]; asks: number } = { creates: [], decisions: [], asks: 0 };
  let items: Record<string, unknown>[] = [];
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role, active: true });
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals' || path === '/api/v1/conversations') return json([]);
    if (path.startsWith('/api/v1/developer/') && role !== 'owner') return json({ error: { code: 'forbidden', message: 'Workspace permission denied', correlation_id: 'c' } }, 403);
    if (path === '/api/v1/developer/setup') return json(setup);
    if (path === '/api/v1/developer/requests' && request.method() === 'GET') return json(items);
    if (path === '/api/v1/developer/requests' && request.method() === 'POST') {
      const body = request.postDataJSON() as { text: string; source: string };
      sent.creates.push(body);
      const created = devRequest('dev_one', body.text, 'queued', { source: body.source });
      items = [created];
      return json(created, 201);
    }
    if (path === '/api/v1/developer/requests/dev_one' && request.method() === 'GET') return json(items[0]);
    if (path === '/api/v1/developer/requests/dev_one/merge-approval') {
      items = [{ ...items[0], approval_id: approval.id, approval }];
      return json({ request: items[0], approval });
    }
    if (path === '/api/v1/approvals/approval_dev/approve') {
      sent.decisions.push(request.postDataJSON());
      items = [{ ...items[0], status: 'merged', merge_commit_sha: 'm'.repeat(40), approval: null, timeline: timeline('all'), summary: 'Merged: Add a dark/light toggle to settings.' }];
      return json({ approval: { ...approval, status: 'approved', decided_by: 'user_local', decided_at: now }, task: { id: 'task_merge', title: 'Merge', prompt: 'Merge', status: 'completed', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: now, updated_at: now }, run: { id: 'run_merge', task_id: 'task_merge', status: 'completed', steps: [], result: 'Merged pull request #7 into main.' } });
    }
    if (/\/ask$/.test(path)) sent.asks += 1;
    return route.abort();
  });
  return {
    sent,
    makeReady() {
      items = [devRequest('dev_one', 'Add a dark/light toggle to settings', 'waiting_approval', {
        run_id: 1001, run_url: 'https://github.com/acme/anum/actions/runs/1001', pr_number: 7, pr_url: 'https://github.com/acme/anum/pull/7',
        pr_title: 'Add a dark/light toggle to settings', pr_draft: true, head_sha: 'a'.repeat(40), checks: { total: 4, passed: 4, failed: 0, pending: 0, failing: [] },
        timeline: timeline('approval'), summary: 'The change is ready for review: Add a dark/light toggle to settings. Approve to merge.',
      })];
    },
  };
}

test('an owner asks for a change, follows it and approves the merge with the payload hash', async ({ page }) => {
  const api = await mockApi(page);
  await page.goto('/#developer');
  await expect(page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Developer' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'No developer requests yet' })).toBeVisible();

  await page.getByRole('button', { name: 'Add a dark/light toggle to Settings' }).click();
  await expect(page.getByLabel('What should change?')).toHaveValue('Add a dark/light toggle to Settings');
  await page.getByRole('button', { name: 'Ask the developer' }).click();
  await expect.poll(() => api.sent.creates).toEqual([{ text: 'Add a dark/light toggle to Settings', allow_protected_paths: false, source: 'text' }]);
  const card = page.getByRole('article', { name: /Developer request: Add a dark\/light toggle/ });
  await expect(card.getByText('Queued', { exact: true }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Ask the developer' })).toBeDisabled();

  api.makeReady();
  await card.getByRole('button', { name: 'Check now' }).click();
  await expect(card.getByText('Waiting for approval').first()).toBeVisible();
  await expect(card.getByRole('link', { name: /Pull request #7/ })).toHaveAttribute('href', 'https://github.com/acme/anum/pull/7');
  await expect(card.getByText('4 of 4 checks passed')).toBeVisible();

  await card.getByRole('button', { name: 'Approve & merge' }).click();
  const review = card.getByRole('region', { name: 'Merge approval' });
  await expect(review.getByText('7c7c7c7c7c7c…7c7c7c')).toBeVisible();
  await expect(review.getByText('api.github.com')).toBeVisible();
  await review.getByRole('button', { name: 'Confirm merge' }).click();
  await expect.poll(() => api.sent.decisions).toEqual([{ payload_hash: hash }]);
  await expect(page.getByText('Merged pull request #7.')).toBeVisible();
  await expect(card.getByText('Merged', { exact: true }).first()).toBeVisible();
});

test('non-owners do not see Developer and get an explanation when they open it', async ({ page }) => {
  await mockApi(page, { role: 'member' });
  await page.goto('/#developer');
  await expect(page.getByRole('heading', { name: 'Developer mode is for workspace owners' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Developer' })).toHaveCount(0);
});

test('the setup state names the missing settings', async ({ page }) => {
  await mockApi(page, { setup: { ...setupReady, configured: false, enabled_here: false, repository: null, missing: ['ANUM_DEVELOPER_GITHUB_REPO', 'ANUM_DEVELOPER_GITHUB_TOKEN'], message: 'Developer mode needs these API settings.' } });
  await page.goto('/#developer');
  await expect(page.getByRole('heading', { name: 'Set up developer mode' })).toBeVisible();
  await expect(page.getByText('ANUM_DEVELOPER_GITHUB_TOKEN', { exact: true })).toBeVisible();
  await expect(page.getByLabel('What should change?')).toHaveCount(0);
});

test('a chat message starting with build asks for confirmation before starting the developer', async ({ page }) => {
  const api = await mockApi(page);
  await page.goto('/#assistant');
  const box = page.getByLabel('Message the assistant');
  await box.fill('build a dark/light toggle in settings');
  await box.press('Enter');
  const assist = page.getByRole('region', { name: 'Developer' });
  await expect(assist.getByText("I'll ask the developer to: a dark/light toggle in settings. Start?")).toBeVisible();
  expect(api.sent.creates).toEqual([]);
  await assist.getByRole('button', { name: 'Start' }).click();
  await expect.poll(() => api.sent.creates).toEqual([{ text: 'a dark/light toggle in settings', allow_protected_paths: false, source: 'text' }]);
  await expect(assist.getByText(/Started\. The developer is on it/)).toBeVisible();
  expect(api.sent.asks).toBe(0);
});

for (const size of [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 }]) {
  test(`the Developer view fits ${size.width}x${size.height} without horizontal overflow`, async ({ page }) => {
    const api = await mockApi(page);
    api.makeReady();
    await page.setViewportSize(size);
    await page.goto('/#developer');
    await expect(page.getByRole('article', { name: /Developer request/ })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
}
