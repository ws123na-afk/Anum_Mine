import { expect, test, type Page, type Route } from '@playwright/test';

// Automations that do real work (docs/automation.md): the trigger editor (event triggers with
// conditions, webhooks with a secret shown once, rotate, turn off, take over), steps that send
// email and push, a run waiting for approval and a retry, a schedule taken over, the Arabic
// schedule description, and every layout without horizontal overflow.
type Json = Record<string, unknown>;
const at = (minutes: number) => new Date(Date.UTC(2026, 9, 11, 8, 0) + minutes * 60_000).toISOString();
const envelope = (status: number, message: string) => ({ error: { code: status === 403 ? 'forbidden' : 'conflict', message, correlation_id: 'corr_e2e', details: [] } });

const EVENTS = [
  { type: 'email.received', label: 'Email received', description: 'A new message arrived in a connected mailbox.', fields: ['account_id', 'message_id', 'from', 'subject', 'category'] },
  { type: 'agent_run.completed', label: 'Task completed', description: 'An agent task finished.', fields: ['task_id'] },
  { type: 'file.uploaded', label: 'File uploaded', description: 'Someone uploaded a file.', fields: ['file_id', 'name', 'content_type'] },
];

function workflow(patch: Json = {}): Json {
  return {
    id: 'wf_a', name: 'Supplier follow-up', description: 'Answers Noon order emails', status: 'active', version: 1, overlap_policy: 'allow', created_at: at(0), updated_at: at(0),
    created_by: 'user_local', updated_by: null, last_run_at: at(10), last_run_status: 'running', next_run_at: null, active_runs: 1, run_count: 2, schedule_count: 1,
    steps: [
      { id: 'draft', name: 'Draft the reply', action: 'task.create', input: { title: 'Reply to {{trigger.from}}', prompt: 'Draft a reply' }, max_attempts: 3, retry_delay_seconds: 60 },
      { id: 'mail', name: 'Send it', action: 'email.send', input: { account_id: 'acct_1', to: ['{{trigger.from}}'], subject: 'Re: order', body: '{{steps.draft.result}}' }, max_attempts: 1, retry_delay_seconds: 60 },
    ],
    ...patch,
  };
}

const waitingRun = {
  id: 'run_wait', workflow_id: 'wf_a', workflow_name: 'Supplier follow-up', workflow_version: 1, status: 'running', trigger: 'event', trigger_id: 'trigger_1', trigger_type: 'email.received',
  waiting_for: 'approval', idempotency_key: 'event:trigger_1:e1', retry_of: null, schedule_id: null, current_step: 1, created_at: at(10), updated_at: at(11), created_by: 'user_local', error: null,
  steps: [
    { id: 'draft', name: 'Draft the reply', action: 'task.create', status: 'completed', attempt: 1, max_attempts: 3, input: {}, output: { task_id: 'task_1', title: 'Reply to orders@noon.com', status: 'completed', result: 'Dear Noon team, the 40 tyres ship on Sunday.' }, error: null },
    { id: 'mail', name: 'Send it', action: 'email.send', status: 'running', waiting_for: 'approval', attempt: 1, max_attempts: 1, input: {}, output: { task_id: 'task_2', approval_id: 'approval_1', to: ['orders@noon.com'] }, error: null },
  ],
};
const retryRun = {
  ...waitingRun, id: 'run_retry', trigger: 'webhook', waiting_for: 'retry', created_at: at(5), current_step: 0,
  steps: [{ id: 'draft', name: 'Draft the reply', action: 'task.create', status: 'running', waiting_for: 'retry', next_attempt_at: at(20), attempt: 1, max_attempts: 3, input: {}, output: {}, error: 'The agent task failed: Noon API timed out' }],
};

async function mockApi(page: Page, role: 'owner' | 'member' | 'viewer' = 'owner') {
  const state = {
    triggers: [] as Json[],
    schedules: [{ id: 'schedule_1', workflow_id: 'wf_a', name: 'Weekday check', cron: '0 9 * * 1-5', cron_description: 'At 09:00, Monday through Friday', cron_description_ar: 'في الساعة 09:00، من الاثنين إلى الجمعة', timezone: 'Asia/Riyadh', enabled: true, next_run_at: at(60), last_run_at: null, last_outcome: null, created_by: 'member_m', created_at: at(0), updated_at: at(0) }] as Json[],
    bodies: [] as Json[], rotated: 0, tookOver: [] as string[],
  };
  await page.route('http://localhost:8000/**', async (route: Route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method();
    const json = (value: unknown, status = 200, headers: Record<string, string> = {}) =>
      route.fulfill({ status, contentType: 'application/json', headers: { 'Access-Control-Expose-Headers': 'X-Total-Count', ...headers }, body: JSON.stringify(value) });
    const body = () => (request.postDataJSON() ?? {}) as Json;
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals') return json([]);
    if (path === '/api/v1/notification-preferences') return json({ task_completed: true, approval_required: true, run_failed: true, automation_failed: true, email_enabled: false, desktop_enabled: false });
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role, active: true });
    if (path === '/api/v1/email/accounts') return json([{ id: 'acct_1', address: 'sales@example.com' }]);
    if (path === '/api/v1/automation/workflows' && method === 'GET') return json([workflow()], 200, { 'X-Total-Count': '1' });
    if (path === '/api/v1/automation/workflows/wf_a' && method === 'GET') return json(workflow());
    if (path === '/api/v1/automation/workflows/wf_a' && method === 'PUT') { state.bodies.push(body()); return json(workflow({ ...body(), version: 2 })); }
    if (path === '/api/v1/automation/schedules' && method === 'GET') return json(state.schedules, 200, { 'X-Total-Count': String(state.schedules.length) });
    if (path === '/api/v1/automation/schedules/schedule_1/take-over') { state.tookOver.push('schedule_1'); state.schedules[0].created_by = 'user_local'; return json(state.schedules[0]); }
    if (path === '/api/v1/automation/runs') return json([waitingRun, retryRun], 200, { 'X-Total-Count': '2' });
    if (path === '/api/v1/automation/trigger-events') return json(EVENTS);
    if (path === '/api/v1/automation/triggers' && method === 'GET') return json(state.triggers, 200, { 'X-Total-Count': String(state.triggers.length) });
    if (path === '/api/v1/automation/triggers' && method === 'POST') {
      if (role === 'viewer') return json(envelope(403, 'Changing or running automations needs the owner or member role in this workspace.'), 403);
      const b = body(), id = `trigger_${state.triggers.length + 1}`;
      const trigger = { id, workflow_id: 'wf_a', enabled: true, secret_hint: b.kind === 'webhook' ? 'k3y4' : null, secret_rotated_at: null, last_fired_at: null, last_outcome: null, fire_count: 0,
        created_by: 'user_local', created_at: at(1), updated_at: at(1), webhook_path: b.kind === 'webhook' ? `/api/v1/automation/hooks/tenant_local/workspace_foundation/${id}` : null, ...b };
      state.triggers.push(trigger);
      state.bodies.push(b);
      return json({ trigger, secret: b.kind === 'webhook' ? 'whsec_example_shown_once' : null }, 201);
    }
    const one = path.match(/^\/api\/v1\/automation\/triggers\/([^/]+)(?:\/([\w-]+))?$/);
    if (one) {
      const trigger = state.triggers.find((item) => item.id === one[1]);
      if (!trigger) return json(envelope(404, 'No trigger'), 404);
      if (one[2] === 'disable' || one[2] === 'enable') { trigger.enabled = one[2] === 'enable'; return json(trigger); }
      if (one[2] === 'rotate-secret') { state.rotated += 1; return json({ trigger, secret: 'whsec_example_rotated' }); }
      if (one[2] === 'take-over') { trigger.created_by = 'user_local'; return json(trigger); }
      if (!one[2] && method === 'DELETE') { state.triggers = state.triggers.filter((item) => item !== trigger); return route.fulfill({ status: 204 }); }
      if (!one[2] && method === 'PUT') { Object.assign(trigger, body()); return json(trigger); }
    }
    return json({ error: { code: 'not_found', message: `Unmocked ${method} ${path}`, correlation_id: 'x', details: [] } }, 404);
  });
  return state;
}

const open = async (page: Page) => {
  await page.goto('/#automation');
  await page.getByRole('button', { name: /^Supplier follow-up/ }).click();
  await expect(page.getByRole('heading', { name: 'Triggers', level: 2 })).toBeVisible();
};

test('an owner adds an event trigger with conditions and a webhook whose secret is shown once', async ({ page }) => {
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  const state = await mockApi(page);
  await open(page);
  await expect(page.getByRole('heading', { name: 'No triggers' })).toBeVisible();

  await page.getByRole('button', { name: 'Add event trigger' }).click();
  const form = page.getByRole('form', { name: 'Add event trigger' });
  await form.getByRole('button', { name: 'Add trigger' }).click();
  await expect(form.locator('small.autoFieldError', { hasText: 'Give the trigger a name.' })).toBeVisible();
  await expect(form.getByLabel('Trigger name')).toHaveAttribute('aria-invalid', 'true');
  await form.getByLabel('Trigger name').fill('Noon order mail');
  await form.getByLabel('When this happens').selectOption('email.received');
  await expect(form.getByText(/Fields of this event: account_id, message_id, from, subject, category/)).toBeVisible();
  await form.getByRole('button', { name: 'Add condition' }).click();
  await form.getByLabel('Field 1').fill('from');
  await form.getByLabel('Test').selectOption('contains');
  await form.getByRole('button', { name: 'Add trigger' }).click();
  await expect(form.locator('small.autoFieldError', { hasText: 'Condition 1: enter the value to compare with.' })).toBeVisible();
  await form.getByLabel('Value').fill('noon.com');
  await form.getByRole('button', { name: 'Add trigger' }).click();
  await expect(page.getByText('Trigger "Noon order mail" added.')).toBeVisible();
  expect(state.bodies[0]).toMatchObject({ kind: 'event', event_type: 'email.received', conditions: [{ field: 'from', operator: 'contains', value: 'noon.com' }] });
  const list = page.getByRole('list', { name: 'Triggers' });
  await expect(list.getByText('Email received')).toBeVisible();
  await expect(list.getByText(/from contains "noon.com"/)).toBeVisible();

  await page.getByRole('button', { name: 'Add webhook' }).click();
  const hook = page.getByRole('form', { name: 'Add webhook' });
  await hook.getByLabel('Trigger name').fill('Store orders');
  await hook.getByLabel('Replay window (seconds)').fill('120');
  await hook.getByRole('button', { name: 'Add trigger' }).click();
  const secret = page.getByRole('region', { name: 'Webhook secret for "Store orders".' });
  await expect(secret.getByTestId('webhook-secret')).toHaveText('whsec_example_shown_once');
  await expect(secret.locator('code').first()).toContainText('/api/v1/automation/hooks/tenant_local/workspace_foundation/trigger_2');
  await secret.getByText('How to sign a request').click();
  await expect(secret.locator('pre')).toContainText('X-Anum-Signature: t=$TS,v1=$SIG');
  await secret.getByRole('button', { name: 'I saved the secret' }).click();
  await expect(page.getByTestId('webhook-secret')).toHaveCount(0);
  await expect(list.getByText('Secret ends in k3y4')).toBeVisible();

  page.once('dialog', (dialog) => void dialog.accept());
  await page.getByRole('button', { name: 'Create a new secret for Store orders' }).click();
  await expect(page.getByTestId('webhook-secret')).toHaveText('whsec_example_rotated');
  expect(state.rotated).toBe(1);

  await page.getByRole('button', { name: 'Turn off trigger Noon order mail' }).click();
  await expect(page.getByText('Trigger "Noon order mail" is off.')).toBeVisible();
  page.once('dialog', (dialog) => void dialog.accept());
  await page.getByRole('button', { name: 'Delete trigger Noon order mail' }).click();
  await expect(page.getByText('Trigger "Noon order mail" deleted.')).toBeVisible();
});

test('a run shows the step waiting for approval, the task result and a retry', async ({ page }) => {
  await mockApi(page);
  await open(page);
  const runs = page.getByRole('list', { name: 'Runs' });
  await expect(runs.getByText('Waiting for approval', { exact: true })).toBeVisible();
  await expect(runs.getByText('Started by an event')).toBeVisible();
  await runs.getByRole('button', { expanded: false }).first().click();
  await expect(runs.getByText(/Result: Dear Noon team, the 40 tyres ship on Sunday./)).toBeVisible();
  await expect(runs.getByText(/Waiting for approval: someone must approve/)).toBeVisible();
  await expect(runs.getByRole('button', { name: /Open task/ }).first()).toBeVisible();
  await runs.getByRole('button', { expanded: false }).first().click();
  await expect(runs.getByText(/Failed; attempt 2 of 3 starts/)).toBeVisible();
  await expect(runs.getByText('The agent task failed: Noon API timed out').first()).toBeVisible();
});

test('the composer edits email, push and retry settings', async ({ page }) => {
  const state = await mockApi(page);
  await open(page);
  await page.getByRole('button', { name: 'Edit', exact: true }).click();
  await expect(page.getByLabel('Send from')).toHaveValue('acct_1');
  await expect(page.getByLabel('To', { exact: true })).toHaveValue('{{trigger.from}}');
  await page.getByLabel('Run the task (uncheck to only create it under Tasks)').uncheck();
  await page.getByLabel('Attempts').first().fill('4');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByText(/Saved "Supplier follow-up"/)).toBeVisible();
  const steps = state.bodies[0].steps as Json[];
  expect(steps[0]).toMatchObject({ input: { title: 'Reply to {{trigger.from}}', prompt: 'Draft a reply', run: false }, max_attempts: 4 });
  expect(steps[1]).toMatchObject({ action: 'email.send', input: { account_id: 'acct_1', to: ['{{trigger.from}}'], body: '{{steps.draft.result}}' } });
});

test('an owner takes over a member\'s schedule', async ({ page }) => {
  const state = await mockApi(page);
  await open(page);
  page.once('dialog', (dialog) => void dialog.accept());
  await page.getByRole('button', { name: 'Take over schedule Weekday check' }).click();
  await expect(page.getByText('Schedule "Weekday check" now runs as you.')).toBeVisible();
  expect(state.tookOver).toEqual(['schedule_1']);
});

test('members cannot take over and viewers see triggers without controls', async ({ page }) => {
  await mockApi(page, 'viewer');
  await open(page);
  await expect(page.getByRole('button', { name: 'Add event trigger' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Take over/ })).toHaveCount(0);
  await expect(page.getByText('This automation starts manually or on a schedule.')).toBeVisible();
});

test('Arabic: the schedule and triggers read right to left', async ({ page }) => {
  await mockApi(page);
  await page.addInitScript(() => { try { localStorage.setItem('anum.locale', 'ar'); } catch { /* storage blocked */ } });
  await page.goto('/#automation');
  await page.getByRole('button', { name: /Supplier follow-up/ }).click();
  await expect(page.getByRole('heading', { name: 'المُشغِّلات', level: 2 })).toBeVisible();
  await expect(page.getByText('في الساعة 09:00، من الاثنين إلى الجمعة')).toBeVisible();
  await expect(page.getByRole('list', { name: 'التشغيلات' }).getByText('في انتظار الموافقة', { exact: true })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]]) {
  test(`no horizontal overflow with the trigger editor open at ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await mockApi(page);
    await open(page);
    await page.getByRole('button', { name: 'Add event trigger' }).click();
    await page.getByRole('button', { name: 'Add condition' }).click();
    await page.getByRole('list', { name: 'Runs' }).getByRole('button', { expanded: false }).first().click();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    const unnamed = await page.locator('button:visible').evaluateAll((buttons) => buttons.filter((b) => !(b.textContent?.trim() || b.getAttribute('aria-label') || b.getAttribute('title'))).length);
    expect(unnamed).toBe(0);
    await page.screenshot({ path: `test-results/automation-triggers-${width}x${height}.png`, fullPage: true });
  });
}
