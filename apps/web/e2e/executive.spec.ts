import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';

// Executive desk and PA (docs/ANUM_MINE_EXECUTIVE_PA.md): workspaces kept apart, grants with an
// approval, an explicit switch, the PA moving through the office and asking before it acts.
const HASH = 'a1'.repeat(32);
const stamp = '2026-10-01T00:00:00Z';
type Row = Record<string, unknown>;

const tools = [
  ['navigate', 'read', 'Go to a building, floor, room or agent', 'low', 'automatic'], ['open_panel', 'read', 'Open a panel', 'low', 'automatic'],
  ['summarise_workspace', 'read', 'Summarise a workspace you may see', 'low', 'automatic'], ['list_blocked_work', 'read', 'List blocked work', 'low', 'automatic'],
  ['list_pending_approvals', 'read', 'List pending approvals', 'low', 'automatic'], ['start_task', 'write', 'Start a task', 'medium', 'confirm_each'],
  ['delegate', 'write', 'Delegate work to an agent', 'medium', 'confirm_each'], ['schedule_meeting', 'write', 'Create a live agent meeting', 'medium', 'confirm_each'],
].map(([name, kind, label, risk, autonomy]) => ({ name, kind, label, risk, autonomy, governed_by: 'the existing governed API', permissions: [], allowed: true }));

const stops = [
  { building_id: 'hq', building_name: 'Headquarters', floor_id: 'f0', floor_name: 'Ground', level: 0, room_id: 'r_rec', room_name: 'Reception', room_kind: 'reception', capacity: 6, desks: 0 },
  { building_id: 'hq', building_name: 'Headquarters', floor_id: 'f1', floor_name: 'First floor', level: 1, room_id: 'r_exec', room_name: 'Executive office', room_kind: 'office', capacity: 4, desks: 1 },
];

interface Options { overview?: 'ok' | 'denied' | 'missing' | 'empty'; tour?: 'ok' | 'empty'; grantsDenied?: boolean }
const HASH2 = 'b2'.repeat(32);

async function mockApi(page: Page, options: Options = {}) {
  const seen = { created: [] as Row[], approved: [] as Row[], narrowed: [] as Row[], rejects: 0, revokes: 0, invokes: [] as Row[] };
  let grant: Row | null = { id: 'xgrant_1', workspace_id: 'workspace_foundation', grantee_user_id: 'user_exec', scopes: ['counts', 'tasks', 'cost'], status: 'pending', stored_status: 'pending', payload_hash: HASH2, revision: 1, requested_by: 'user_local', requested_at: stamp, request_expires_at: stamp, approved_by: null, approved_at: null, expires_at: null, revoked_by: null, revoked_at: null, decision_reason: null };
  const overview = (): Row[] => [
    { workspace_id: 'workspace_employer', name: 'Employer Ltd', access: 'granted', grant_id: 'xg_e', scopes: ['approvals', 'tasks'], expires_at: null, as_of: stamp, summary: { approvals: { pending: 3, oldest_pending_at: null }, tasks: { by_status: { completed: 4, waiting_approval: 1 }, blocked_agents: 0 } } },
    { workspace_id: 'workspace_foundation', name: 'ANUM OS', access: 'not_granted', reason: 'no_grant', open_with: 'workspace_switch' },
    { workspace_id: 'workspace_shop', name: 'Tyre Shop', access: 'not_granted', reason: 'no_grant', open_with: 'workspace_switch' },
  ];
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/approvals' || path === '/api/v1/tasks') return json([]);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: request.headers()['x-workspace-id'] ?? 'workspace_foundation', user_id: 'user_local', role: 'owner', active: true, created_at: stamp, updated_at: stamp });
    if (path === '/api/v1/workspaces/current') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: stamp, updated_at: stamp, archived_at: null, archived_by: null });
    if (path === '/api/v1/executive/overview') {
      if (options.overview === 'denied') return json({ error: { code: 'forbidden', message: 'Membership needed' } }, 403);
      if (options.overview === 'missing') return json({ error: { code: 'unavailable', message: 'Needs PostgreSQL' } }, 503);
      return json({ version: 1, tenant_id: 'tenant_local', home_workspace_id: 'workspace_foundation', generated_at: stamp, truncated: false, workspaces: options.overview === 'empty' ? [] : overview() });
    }
    if (path === '/api/v1/executive/grants' && method === 'GET') {
      if (options.grantsDenied) return json({ error: { code: 'forbidden', message: 'Owners only' } }, 403);
      return json({ version: 1, items: grant ? [grant] : [] });
    }
    if (path === '/api/v1/executive/grants' && method === 'POST') {
      const body = JSON.parse(request.postData() ?? '{}') as Row;
      seen.created.push(body);
      grant = { ...(grant ?? {}), id: 'xgrant_2', grantee_user_id: body.grantee_user_id, scopes: body.scopes, status: 'pending', payload_hash: HASH2, revision: 1 };
      return json({ ...grant, approval_required: true }, 201);
    }
    if (path.endsWith('/approve') && method === 'POST') { seen.approved.push(JSON.parse(request.postData() ?? '{}')); grant = { ...grant, status: 'active', stored_status: 'active' }; return json(grant); }
    if (path.endsWith('/reject') && method === 'POST') { seen.rejects += 1; grant = { ...grant, status: 'rejected' }; return json(grant); }
    if (path.startsWith('/api/v1/executive/grants/') && method === 'PATCH') { const body = JSON.parse(request.postData() ?? '{}') as Row; seen.narrowed.push(body); grant = { ...grant, scopes: body.scopes, revision: 2 }; return json(grant); }
    if (path.startsWith('/api/v1/executive/grants/') && method === 'DELETE') { seen.revokes += 1; grant = { ...grant, status: 'revoked' }; return route.fulfill({ status: 204 }); }
    if (path === '/api/v1/executive-pa/tools') return json({ tools });
    if (path === '/api/v1/executive-pa/tour') return json(options.tour === 'empty' ? { status: 'empty', stops: [] } : { status: 'ready', stops });
    if (path === '/api/v1/executive-pa/invoke') {
      const body = JSON.parse(request.postData() ?? '{}') as Row;
      seen.invokes.push(body);
      const args = body.arguments as Row;
      if (body.tool === 'navigate') return json({ tool: 'navigate', status: 'ok', navigate: { building_id: 'hq', floor_id: args.target_id === 'r_exec' ? 'f1' : 'f0', room_id: args.target_id } });
      if (body.tool === 'summarise_workspace') return json({ tool: body.tool, status: 'ok', summary: { tasks: { by_status: { completed: 10, waiting_approval: 2 } }, approvals: { pending: 1 } } });
      if (body.tool === 'open_panel') return json({ tool: body.tool, status: 'ok', open_panel: { view: args.panel } });
      if (body.tool === 'list_pending_approvals') return json({ tool: body.tool, status: 'ok', items: [] });
      if (body.tool === 'list_blocked_work') return json({ tool: body.tool, status: 'ok', items: [{ id: 't1', title: 'Order tyres', status: 'waiting_approval' }] });
      if (body.tool === 'start_task' && !body.confirm_hash) return json({ tool: 'start_task', status: 'needs_confirmation', payload_hash: HASH, risk: 'medium', autonomy: 'confirm_each', governed_by: 'POST /tasks then run', preview: args });
      if (body.tool === 'start_task') return json({ tool: 'start_task', status: 'approval_pending', task_id: 'task_1', approval_id: 'ap_1' });
    }
    return json({ error: { code: 'not_found', message: 'unmocked' } }, 404);
  });
  return seen;
}

test('the desk keeps workspaces apart: granted numbers only, not granted shows nothing, switch is explicit', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#executive');
  await expect(page.getByRole('heading', { level: 1, name: 'Executive desk' })).toBeVisible();
  const employer = page.getByRole('listitem').filter({ hasText: 'Employer Ltd' });
  await expect(employer.getByText('Granted', { exact: true })).toBeVisible();
  await expect(employer).toContainText('Approvals waiting');
  await expect(employer).toContainText('Not shared');
  const shop = page.getByRole('listitem').filter({ hasText: 'Tyre Shop' });
  await expect(shop.getByText('Not granted', { exact: true })).toBeVisible();
  await expect(shop).toContainText('Nothing from this workspace is shown here.');
  await expect(shop.getByText('Confidential content opens only after you switch workspace.')).toBeVisible();
  const home = page.getByRole('listitem').filter({ hasText: 'ANUM OS' });
  await expect(home.getByText('Current workspace')).toBeVisible();
  await expect(home).toContainText('12');
  await shop.getByRole('button', { name: 'Switch to Tyre Shop' }).click();
  await expect(shop.getByText('Current workspace')).toBeVisible();
});

test('grants: request, approve the shown hash, narrow only, revoke', async ({ page }) => {
  const seen = await mockApi(page);
  await page.goto('/#executive');
  const grants = page.getByRole('region', { name: 'Who may see a summary of this workspace' });
  const pending = grants.getByRole('listitem').filter({ hasText: 'user_exec' });
  await expect(pending.getByText('Waiting for approval')).toBeVisible();
  await expect(pending.getByText(HASH2)).toBeVisible();
  await pending.getByRole('button', { name: 'Approve this exact grant' }).click();
  await expect(grants.getByText('Grant approved.')).toBeVisible();
  expect(seen.approved).toEqual([{ payload_hash: HASH2 }]);

  await pending.getByRole('button', { name: 'Narrow' }).click();
  const form = pending.getByRole('form', { name: 'Narrow' });
  await expect(form.getByLabel('Approvals waiting')).toHaveCount(0);
  await form.getByLabel('Monthly cost').uncheck();
  await form.getByRole('button', { name: 'Save narrower grant' }).click();
  await expect(grants.getByText('Grant narrowed.')).toBeVisible();
  expect(seen.narrowed).toEqual([{ expected_revision: 1, scopes: ['counts', 'tasks'] }]);

  await pending.getByRole('button', { name: 'Revoke' }).click();
  await pending.getByRole('button', { name: /Revoke for user_exec/ }).click();
  await expect(grants.getByText('Grant revoked.')).toBeVisible();
  expect(seen.revokes).toBe(1);

  await grants.getByRole('button', { name: 'Request a grant' }).click();
  const create = grants.getByRole('form', { name: 'Request a grant' });
  await create.getByLabel('Person (user id)').fill('user_new');
  await create.getByLabel('Approvals waiting').check();
  await create.getByRole('button', { name: 'Request grant' }).click();
  await expect(grants.getByText(/gives no access until an owner approves/)).toBeVisible();
  expect(seen.created).toEqual([{ grantee_user_id: 'user_new', scopes: ['counts', 'approvals'] }]);
});

test('the PA moves through the office, reads, and asks before it acts', async ({ page }) => {
  const seen = await mockApi(page);
  await page.goto('/#executive');
  await expect(page.getByRole('button', { name: 'Start tour' })).toBeVisible();
  const input = page.getByLabel('Tell the assistant what to do');
  await input.fill('take me to the executive office');
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByRole('log')).toContainText('Moved to Executive office, First floor, Headquarters.');
  await expect(page.locator('.paPlace')).toContainText('Executive office');
  expect(await page.evaluate(() => sessionStorage.getItem('anum.hq.pendingNavigation'))).toContain('r_exec');

  await input.fill('list blocked work');
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByRole('log')).toContainText('Order tyres');

  await input.fill('start a task to draft the weekly report');
  await page.getByRole('button', { name: 'Send' }).click();
  const confirm = page.getByRole('group', { name: /Confirm: Start a task/ });
  await expect(confirm).toBeVisible();
  expect(seen.invokes.filter((b) => b.tool === 'start_task')).toHaveLength(1);
  await confirm.getByRole('button', { name: 'Confirm and run' }).click();
  await expect(page.getByRole('log')).toContainText('stopped for approval');
  const last = seen.invokes[seen.invokes.length - 1];
  expect(last).toMatchObject({ tool: 'start_task', input_mode: 'click', confirm_hash: HASH });
  await page.getByText('What the assistant may do for you').click();
  await expect(page.getByText('The assistant can never approve or reject anything.')).toBeVisible();

  await input.fill('approve everything');
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByRole('log')).toContainText('I did not understand that');
  expect(seen.invokes.some((b) => b.tool === 'approve')).toBe(false);
});

test('the dock: every quick action calls a real tool, and nothing without a tool is offered', async ({ page }) => {
  const seen = await mockApi(page);
  await page.goto('/#executive');
  const dock = page.getByRole('group', { name: 'Quick actions' });
  await expect(dock.getByRole('button')).toHaveCount(7);
  for (const name of ['Show recent orders', 'Take me to my office', 'Show logistics activity']) await expect(page.getByRole('button', { name })).toHaveCount(0);
  await expect(page.getByRole('status').filter({ hasText: 'Ready' })).toBeVisible();
  await dock.getByRole('button', { name: 'List blocked work' }).click();
  await expect(page.getByRole('log')).toContainText('Order tyres');
  await dock.getByRole('button', { name: 'Pending approvals' }).click();
  await expect(page.getByRole('log')).toContainText('No approvals are waiting.');
  await dock.getByRole('button', { name: 'Summarise this workspace' }).click();
  await expect(page.getByRole('log')).toContainText('12 tasks');
  await expect(page.getByRole('button', { name: /Take me to Reception/ })).toBeVisible();
  await dock.getByRole('button', { name: 'Open meetings' }).click();
  await expect(page).toHaveURL(/#meetings/);
  const clicked = seen.invokes.filter((b) => b.input_mode === 'click');
  expect(clicked.map((b) => b.tool)).toEqual(['list_blocked_work', 'list_pending_approvals', 'summarise_workspace', 'open_panel']);
  expect(clicked.every((b) => !('confirm_hash' in b))).toBe(true);
});

test('the guided tour walks the accessible rooms only', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#executive');
  await page.getByRole('button', { name: 'Start tour' }).click();
  await expect(page.getByText('Stop 1 of 2: Reception, Ground, Headquarters')).toBeVisible();
  await page.getByRole('button', { name: 'Next stop' }).click();
  await expect(page.getByText('Stop 2 of 2: Executive office, First floor, Headquarters')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Next stop' })).toBeDisabled();
});

test('honest states: no grants installed, owners only, nothing to show, empty tour', async ({ page }) => {
  await mockApi(page, { overview: 'missing', tour: 'empty' });
  await page.goto('/#executive');
  await expect(page.getByRole('heading', { name: 'The executive desk is not available on this server' })).toBeVisible();
  await expect(page.getByText('There are no rooms to visit yet.')).toBeVisible();

  await page.unrouteAll();
  await mockApi(page, { overview: 'denied' });
  await page.goto('/#executive');
  await page.reload();
  await expect(page.getByRole('heading', { name: 'You cannot open the executive desk here' })).toBeVisible();

  await page.unrouteAll();
  await mockApi(page, { overview: 'empty' });
  await page.reload();
  await expect(page.getByRole('heading', { name: 'No workspaces to show yet' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Open workspace settings' })).toBeVisible();
});

test('Arabic renders right to left', async ({ page }) => {
  await page.addInitScript(() => { try { localStorage.setItem('anum.locale', 'ar'); } catch { /* blocked */ } });
  await mockApi(page);
  await page.goto('/#executive');
  await expect(page.getByRole('heading', { level: 1, name: 'المكتب التنفيذي' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'بدء الجولة' })).toBeVisible();
});

const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
test('the desk fits all five sizes without horizontal overflow', async ({ page }) => {
  await mockApi(page);
  const dir = process.env.EXEC_SHOTS;
  if (dir) mkdirSync(dir, { recursive: true });
  for (const [width, height] of SIZES) {
    await page.setViewportSize({ width, height });
    await page.goto('/#executive');
    await expect(page.getByRole('heading', { level: 1, name: 'Executive desk' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Start tour' })).toBeVisible();
    await page.getByLabel('Tell the assistant what to do').fill('take me to the executive office');
    await page.getByRole('button', { name: 'Send' }).click();
    await expect(page.getByRole('log')).toContainText('Moved to');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `${width}x${height}`).toBeLessThanOrEqual(0);
    if (dir) await page.screenshot({ path: `${dir}/executive-${width}x${height}.png`, fullPage: true });
  }
});
