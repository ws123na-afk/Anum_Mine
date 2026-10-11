import { expect, test, type Page, type Route } from '@playwright/test';

// Automations page (docs/automation.md) against a stateful mock of /api/v1/automation: create with
// validation, run now, schedules with the server preview, pause, delete, permissions, paging,
// search, errors, and failure alerts from the event stream.
type Json = Record<string, unknown>;
const at = (minutes: number) => new Date(Date.UTC(2026, 9, 11, 8, 0) + minutes * 60_000).toISOString();
const envelope = (status: number, message: string, details: { location: string; message: string }[] = []) =>
  ({ status, body: { error: { code: status === 422 ? 'validation_error' : status === 403 ? 'forbidden' : 'conflict', message, correlation_id: 'corr_e2e', details } } });

interface Options { role?: 'owner' | 'member' | 'viewer'; workflows?: Json[]; listFails?: number; stream?: Json[] }

function workflow(id: string, name: string, patch: Json = {}): Json {
  return {
    id, name, description: '', status: 'active', version: 1, overlap_policy: 'allow', created_at: at(0), updated_at: at(0), created_by: 'user_local', updated_by: null,
    last_run_at: null, last_run_status: null, next_run_at: null, active_runs: 0, run_count: 0, schedule_count: 0,
    steps: [{ id: 'step-1', name: 'Step 1', action: 'notify', input: {}, max_attempts: 3 }], ...patch,
  };
}

async function mockApi(page: Page, options: Options = {}) {
  const state = { workflows: [...(options.workflows ?? [])], schedules: [] as Json[], runs: [] as Json[], listFails: options.listFails ?? 0, queries: [] as string[] };
  const summary = (w: Json) => {
    const runs = state.runs.filter((r) => r.workflow_id === w.id), schedules = state.schedules.filter((s) => s.workflow_id === w.id);
    return { ...w, run_count: runs.length, last_run_status: runs[0]?.status ?? null, last_run_at: runs[0]?.created_at ?? null, schedule_count: schedules.length,
      active_runs: runs.filter((r) => r.status === 'paused').length, next_run_at: w.status === 'active' ? schedules[0]?.next_run_at ?? null : null };
  };
  await page.route('http://localhost:8000/**', async (route: Route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method();
    const json = (value: unknown, status = 200, headers: Record<string, string> = {}) =>
      route.fulfill({ status, contentType: 'application/json', headers: { 'Access-Control-Expose-Headers': 'X-Total-Count', ...headers }, body: JSON.stringify(value) });
    const fail = ({ status, body }: { status: number; body: unknown }) => json(body, status);
    const body = () => (request.postDataJSON() ?? {}) as Json;

    if (path === '/api/v1/events/stream') {
      if (!options.stream) return route.fulfill({ status: 204 });
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: options.stream.map((e) => `id: ${e.id}\ndata: ${JSON.stringify(e)}\n\n`).join('') });
    }
    if (path === '/api/v1/auth/local/session') return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals') return json([]);
    if (path === '/api/v1/notification-preferences') return json({ task_completed: true, approval_required: true, run_failed: true, automation_failed: true, email_enabled: false, desktop_enabled: false });
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: options.role ?? 'owner', active: true });
    if (path === '/api/v1/tasks/task_made') return json({ id: 'task_made', title: 'Weekly digest', prompt: 'Summarise', status: 'created', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: at(1), updated_at: at(1) });

    if (path === '/api/v1/automation/workflows' && method === 'GET') {
      if (state.listFails > 0) { state.listFails -= 1; return fail(envelope(503, 'Automation store unavailable')); }
      const query = (url.searchParams.get('query') ?? '').toLowerCase(), status = url.searchParams.get('status');
      state.queries.push(url.search);
      const offset = Number(url.searchParams.get('offset') ?? 0), limit = Number(url.searchParams.get('limit') ?? 50);
      const matching = state.workflows.filter((w) => (!query || String(w.name).toLowerCase().includes(query)) && (!status || w.status === status));
      return json(matching.slice(offset, offset + limit).map(summary), 200, { 'X-Total-Count': String(matching.length) });
    }
    if (path === '/api/v1/automation/workflows' && method === 'POST') {
      const created = workflow(`wf_${state.workflows.length + 1}`, String(body().name), { ...body(), created_by: 'user_local' });
      state.workflows.unshift(created);
      return json(summary(created), 201);
    }
    const wf = path.match(/^\/api\/v1\/automation\/workflows\/([^/]+)(?:\/(\w+))?$/);
    if (wf) {
      const item = state.workflows.find((w) => w.id === wf[1]);
      if (!item) return fail(envelope(404, 'No automation in this workspace.'));
      if (!wf[2] && method === 'GET') return json(summary(item));
      if (!wf[2] && method === 'PUT') {
        if (options.role === 'member' && item.created_by !== 'user_local') return fail(envelope(403, `Only the automation's creator (${item.created_by}) or a workspace owner can change this automation.`));
        Object.assign(item, body(), { version: Number(item.version) + 1, updated_by: 'user_local' });
        return json(summary(item));
      }
      if (!wf[2] && method === 'DELETE') { state.workflows = state.workflows.filter((w) => w !== item); return route.fulfill({ status: 204 }); }
      if (wf[2] === 'disable' || wf[2] === 'enable') { item.status = wf[2] === 'enable' ? 'active' : 'disabled'; return json(summary(item)); }
      if (wf[2] === 'runs') {
        if (item.status !== 'active') return fail(envelope(409, `"${item.name}" is paused. Resume the automation before running it.`));
        const run = { id: `automation_run_${state.runs.length + 1}`, workflow_id: item.id, workflow_name: item.name, workflow_version: item.version, status: 'completed', trigger: 'manual',
          idempotency_key: request.headers()['idempotency-key'] ?? null, retry_of: null, schedule_id: null, current_step: 1, created_at: at(5), updated_at: at(5), created_by: 'user_local', error: null,
          steps: [{ id: 'make', name: 'Make digest', action: 'task.create', status: 'completed', attempt: 1, input: { title: 'Weekly digest', prompt: 'Summarise' }, output: { task_id: 'task_made', title: 'Weekly digest' }, error: null }] };
        state.runs.unshift(run);
        return json(run, 201);
      }
    }
    if (path === '/api/v1/automation/schedules/preview') {
      const cron = String(body().cron);
      if (cron.startsWith('0 25')) return fail(envelope(422, 'Request validation failed', [{ location: 'body.cron', message: 'Value error, Hour field: 25 is outside 0-23.' }]));
      return json({ cron, timezone: body().timezone, description: cron === '0 9 * * 1-5' ? 'At 09:00, Monday through Friday' : 'Every 15 minutes', next_runs: [at(60), at(1500), at(2940)] });
    }
    if (path === '/api/v1/automation/schedules' && method === 'GET') {
      const items = state.schedules.filter((s) => s.workflow_id === url.searchParams.get('workflow_id'));
      return json(items, 200, { 'X-Total-Count': String(items.length) });
    }
    if (path === '/api/v1/automation/schedules' && method === 'POST') {
      const b = body();
      const schedule = { id: `schedule_${state.schedules.length + 1}`, ...b, cron_description: b.cron === '0 9 * * 1-5' ? 'At 09:00, Monday through Friday' : 'Every 15 minutes', next_run_at: at(60), last_run_at: null, last_outcome: null, created_by: 'user_local', created_at: at(2), updated_at: at(2) };
      state.schedules.push(schedule);
      return json(schedule, 201);
    }
    if (path === '/api/v1/automation/runs' && method === 'GET') {
      const items = state.runs.filter((r) => r.workflow_id === url.searchParams.get('workflow_id'));
      return json(items, 200, { 'X-Total-Count': String(items.length) });
    }
    return json({ error: { code: 'not_found', message: `Unmocked ${method} ${path}`, correlation_id: 'x', details: [] } }, 404);
  });
  return state;
}

const openAutomations = async (page: Page) => { await page.goto('/#automation'); await expect(page.getByRole('heading', { name: 'Automations', level: 2, exact: true })).toBeVisible(); };

test('an owner creates, runs, schedules, pauses and deletes an automation', async ({ page }) => {
  const state = await mockApi(page);
  await openAutomations(page);
  await expect(page.getByRole('heading', { name: 'No automations yet' })).toBeVisible();

  await page.getByRole('button', { name: 'Create automation' }).first().click();
  const composer = page.getByRole('form', { name: 'New automation' });
  await composer.getByRole('button', { name: 'Create automation' }).click();
  await expect(composer.getByText('Give the automation a name.', { exact: true })).toBeVisible();
  await expect(composer.getByText('Step 1: the task needs a title.', { exact: true })).toBeVisible();
  await expect(composer.getByRole('alert')).toContainText('3 fields need attention');
  await expect(page.getByLabel('Name', { exact: true })).toHaveAttribute('aria-invalid', 'true');

  await page.getByLabel('Name', { exact: true }).fill('Weekly digest');
  await page.getByLabel('Task title').fill('Weekly digest');
  await page.getByLabel('Prompt for the agent').fill('Summarise the week');
  await page.getByLabel('When a run is already active').selectOption('skip');
  await composer.getByRole('button', { name: 'Create automation' }).click();
  await expect(page.getByText('Created "Weekly digest".')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Weekly digest', level: 2 })).toBeVisible();
  expect((state.workflows[0].steps as Json[])[0]).toMatchObject({ action: 'task.create', input: { title: 'Weekly digest', prompt: 'Summarise the week' } });

  await page.getByRole('button', { name: 'Run now' }).click();
  await expect(page.getByText(/Run of "Weekly digest" started: completed/)).toBeVisible();
  const runs = page.getByRole('list', { name: 'Runs' });
  await runs.getByRole('button', { expanded: false }).first().click();
  await expect(runs.getByRole('button', { name: 'Open task "Weekly digest"' })).toBeVisible();

  await page.getByRole('button', { name: 'Add schedule' }).click();
  await page.getByLabel('Schedule name').fill('Weekday mornings');
  await page.getByLabel('Repeat').selectOption('cron');
  await page.getByLabel('Cron (minute hour day month weekday)').fill('0 25 * * *');
  await expect(page.getByText('Hour field: 25 is outside 0-23.')).toBeVisible();
  await page.getByLabel('Cron (minute hour day month weekday)').fill('0 9 * * 1-5');
  await expect(page.locator('.autoPreview').getByText('At 09:00, Monday through Friday')).toBeVisible();
  await page.getByRole('button', { name: 'Add schedule' }).last().click();
  await expect(page.getByRole('list', { name: 'Schedules' }).getByText('Weekday mornings')).toBeVisible();

  await page.getByRole('button', { name: 'Pause', exact: true }).click();
  await expect(page.getByText(/Paused "Weekly digest"/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run now' })).toBeDisabled();
  await page.getByRole('button', { name: 'Resume', exact: true }).click();

  await page.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Delete automation' })).toBeFocused();
  await page.getByRole('button', { name: 'Delete automation' }).click();
  await expect(page.getByText('Deleted "Weekly digest" with its schedules and run history.')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'No automations yet' })).toBeVisible();
});

test('a run links to the task its step created', async ({ page }) => {
  await mockApi(page, { workflows: [workflow('wf_a', 'Digest')] });
  await openAutomations(page);
  await page.getByRole('button', { name: /^Digest/ }).click();
  await page.getByRole('button', { name: 'Run now' }).click();
  await page.getByRole('list', { name: 'Runs' }).getByRole('button', { expanded: false }).first().click();
  await page.getByRole('button', { name: 'Open task "Weekly digest"' }).click();
  await expect(page.getByRole('heading', { name: 'Tasks', exact: true })).toBeVisible();
});

test('viewers read automations without dead controls', async ({ page }) => {
  await mockApi(page, { role: 'viewer', workflows: [workflow('wf_a', 'Nightly backup check')] });
  await openAutomations(page);
  await expect(page.getByText(/You can view automations. Creating, running or changing them needs the owner or member role/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'New automation' })).toHaveCount(0);
  await page.getByRole('button', { name: /^Nightly backup check/ }).click();
  await expect(page.getByRole('heading', { name: 'Nightly backup check', level: 2 })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run now' })).toHaveCount(0);
  await expect(page.getByText('This automation runs only when someone starts it.')).toBeVisible();
});

test('members change only their own automations', async ({ page }) => {
  await mockApi(page, { role: 'member', workflows: [workflow('wf_a', 'Owner report', { created_by: 'owner_x' })] });
  await openAutomations(page);
  await page.getByRole('button', { name: /^Owner report/ }).click();
  await expect(page.getByRole('button', { name: 'Run now' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Edit' })).toHaveCount(0);
  await expect(page.getByText('Only owner_x or a workspace owner can change this.')).toBeVisible();
});

test('a failed list load says what failed and retries', async ({ page }) => {
  await mockApi(page, { listFails: 1, workflows: [workflow('wf_a', 'Digest')] });
  await openAutomations(page);
  await expect(page.getByText('Could not load automations. Automation store unavailable')).toBeVisible();
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.getByRole('button', { name: /^Digest/ })).toBeVisible();
});

test('long lists page and search on the server', async ({ page }) => {
  const workflows = Array.from({ length: 25 }, (_, i) => workflow(`wf_${i}`, i === 24 ? 'Invoice sweep' : `Report ${i + 1}`));
  const state = await mockApi(page, { workflows });
  await openAutomations(page);
  await expect(page.getByText('1–20 of 25')).toBeVisible();
  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByText('21–25 of 25')).toBeVisible();
  await page.getByLabel('Search').fill('invoice');
  await expect(page.getByRole('list', { name: 'Automations' }).getByRole('button')).toHaveCount(1);
  expect(state.queries.some((q) => q.includes('query=invoice') && q.includes('offset=0'))).toBe(true);
  await page.getByLabel('Search').fill('nothing like this');
  await expect(page.getByRole('heading', { name: 'No matches' })).toBeVisible();
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await expect(page.getByText('1–20 of 25')).toBeVisible();
});

test('a failed run addressed to me shows an alert that opens the run', async ({ page }) => {
  await mockApi(page, {
    workflows: [workflow('wf_a', 'Digest')],
    stream: [{ id: 'event_1', type: 'automation_run.failed', subject: 'run_1', payload: { workflow_id: 'wf_a', workflow_name: 'Digest', run_id: 'run_1', error: 'Upstream down', failed_step: 'Fetch', notify_user_ids: ['user_local'] } }],
  });
  await page.goto('/#tasks');
  const alert = page.getByRole('region', { name: 'Automation alerts' });
  await expect(alert.getByText('Digest failed')).toBeVisible();
  await expect(alert.getByText('Fetch: Upstream down')).toBeVisible();
  await alert.getByRole('button', { name: 'Open run' }).click();
  await expect(page.getByRole('heading', { name: 'Digest', level: 2 })).toBeVisible();
  await expect(alert).toHaveCount(0);
});

test('the page has no horizontal overflow and named controls', async ({ page }) => {
  await mockApi(page, { workflows: [workflow('wf_a', 'A very long automation name that keeps going to test wrapping behaviour on small screens')] });
  await openAutomations(page);
  await page.getByRole('button', { name: /^A very long automation name/ }).click();
  await expect(page.getByRole('heading', { name: 'Run history' })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  const unnamed = await page.locator('button:visible').evaluateAll((buttons) => buttons.filter((b) => !(b.textContent?.trim() || b.getAttribute('aria-label') || b.getAttribute('title'))).length);
  expect(unnamed).toBe(0);
});

test('the page reads in Arabic, right to left, without overflow', async ({ page }) => {
  await mockApi(page, { workflows: [workflow('wf_a', 'Digest')] });
  await page.addInitScript(() => { try { localStorage.setItem('anum.locale', 'ar'); } catch { /* storage blocked */ } });
  await page.goto('/#automation');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { name: 'الأتمتة', level: 2, exact: true })).toBeVisible();
  await page.getByRole('button', { name: /Digest/ }).click();
  await expect(page.getByRole('heading', { name: 'سجل التشغيل' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'تشغيل الآن' })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
