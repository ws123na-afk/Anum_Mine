import { expect, test, type Page } from '@playwright/test';

// Approval integrity in the web client (docs/approvals-and-risk.md): the card shows the exact
// tool and arguments, approving sends back the displayed payload hash, history shows the decider.
const now = new Date().toISOString();
const later = new Date(Date.now() + 6 * 3600_000).toISOString();
const hash = '3f'.repeat(32);

const task = { id: 'task_risky', title: 'Publish update', prompt: 'Publish the final update', status: 'waiting_approval', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: now, updated_at: now };
const pending = {
  id: 'approval_e2e',
  task_id: 'task_risky',
  action: 'external.action',
  risk_level: 'high',
  status: 'pending',
  reason: 'External or high-impact actions require explicit approval.',
  created_at: now,
  run_id: 'run_risky',
  step_id: 'step_proposal',
  arguments: { action: 'Publish the final update', planned_response: 'Posting the release notes to #announcements', api_key: '[REDACTED]' },
  payload_hash: hash,
  expires_at: later,
  decided_at: null,
  decided_by: null,
};

async function mockApi(page: Page) {
  const decisions: unknown[] = [];
  let approval: Record<string, unknown> = { ...pending };
  await page.route('http://localhost:8000/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    // Header-mode development: no local session endpoint, the app falls back to headers.
    if (path === '/api/v1/auth/local/session') return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    if (path === '/api/v1/tasks') return json([task]);
    if (path === '/api/v1/approvals') return json([approval]);
    if (path === '/api/v1/approvals/approval_e2e/approve') {
      decisions.push(route.request().postDataJSON());
      approval = { ...approval, status: 'approved', decided_by: 'user_local', decided_at: new Date().toISOString() };
      return json({ approval, task: { ...task, status: 'completed' }, run: { id: 'run_risky', task_id: 'task_risky', status: 'completed', steps: [] } });
    }
    return route.abort();
  });
  return decisions;
}

test('approval card shows the exact call and approving sends the displayed hash', async ({ page }) => {
  const decisions = await mockApi(page);
  await page.goto('/#approvals');

  const card = page.getByRole('article', { name: 'Approval for external.action' });
  await expect(card).toBeVisible();
  await expect(card.getByText('Publish the final update', { exact: true })).toBeVisible();
  await expect(card.getByText('Posting the release notes to #announcements')).toBeVisible();
  await expect(card.getByText('[REDACTED]')).toBeVisible();
  await expect(card.getByText(/Expires in \d+ h/)).toBeVisible();
  await expect(card.getByText('3f3f3f3f3f3f…3f3f3f')).toBeVisible();

  await card.getByRole('button', { name: 'Approve' }).click();

  await expect.poll(() => decisions).toEqual([{ payload_hash: hash }]);
  const history = page.getByRole('region', { name: 'Approval history' });
  await expect(history.getByText(/Approved by user_local/)).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Queue clear' })).toBeVisible();
});
