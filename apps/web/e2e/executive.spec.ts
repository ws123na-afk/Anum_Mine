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

interface Options { overview?: 'ok' | 'denied' | 'missing' | 'empty'; tour?: 'ok' | 'empty' }

async function mockApi(page: Page, options: Options = {}) {
  const seen = { grants: [] as Row[], invokes: [] as Row[], revokes: 0 };
  let shopGranted = false;
  const rows = (): Row[] => [
    { workspace_id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'ANUM OS', kind: 'anum_os', grant_state: 'own', summary: { tasks_total: 12, tasks_blocked: 2, approvals_pending: 1, cost_usd_month: 4.5, meetings_active: 0 } },
    { workspace_id: 'workspace_employer', tenant_id: 'tenant_local', name: 'Employer Ltd', kind: 'employment', grant_state: 'not_granted', summary: { tasks_total: 999 } },
    { workspace_id: 'workspace_shop', tenant_id: 'tenant_local', name: 'Tyre Shop', kind: 'business', grant_state: shopGranted ? 'granted' : 'not_granted', grant_id: shopGranted ? 'grant_shop' : null, summary: shopGranted ? { tasks_total: 7, approvals_pending: 3 } : null },
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
      if (options.overview === 'denied') return json({ error: { code: 'forbidden', message: 'Owners only' } }, 403);
      if (options.overview === 'missing') return json({ error: { code: 'not_found', message: 'Not found' } }, 404);
      return json({ workspaces: options.overview === 'empty' ? [] : rows() });
    }
    if (path === '/api/v1/executive/grants' && method === 'GET') return json({ items: [] });
    if (path === '/api/v1/executive/grants' && method === 'POST') {
      seen.grants.push(JSON.parse(request.postData() ?? '{}'));
      shopGranted = true;
      return json({ id: 'grant_shop', workspace_id: 'workspace_shop', scopes: ['tasks', 'approvals'], status: 'active' }, 201);
    }
    if (path === '/api/v1/executive/grants/grant_shop' && method === 'DELETE') { seen.revokes += 1; shopGranted = false; return route.fulfill({ status: 204 }); }
    if (path === '/api/v1/executive-pa/tools') return json({ tools });
    if (path === '/api/v1/executive-pa/tour') return json(options.tour === 'empty' ? { status: 'empty', stops: [] } : { status: 'ready', stops });
    if (path === '/api/v1/executive-pa/invoke') {
      const body = JSON.parse(request.postData() ?? '{}') as Row;
      seen.invokes.push(body);
      const args = body.arguments as Row;
      if (body.tool === 'navigate') return json({ tool: 'navigate', status: 'ok', navigate: { building_id: 'hq', floor_id: args.target_id === 'r_exec' ? 'f1' : 'f0', room_id: args.target_id } });
      if (body.tool === 'summarise_workspace') return json({ tool: body.tool, status: 'ok', summary: { tasks_total: 12, tasks_blocked: 2, approvals_pending: 1 } });
      if (body.tool === 'list_blocked_work') return json({ tool: body.tool, status: 'ok', items: [{ id: 't1', title: 'Order tyres', status: 'waiting_approval' }] });
      if (body.tool === 'start_task' && !body.confirm_hash) return json({ tool: 'start_task', status: 'needs_confirmation', payload_hash: HASH, risk: 'medium', autonomy: 'confirm_each', governed_by: 'POST /tasks then run', preview: args });
      if (body.tool === 'start_task') return json({ tool: 'start_task', status: 'approval_pending', task_id: 'task_1', approval_id: 'ap_1' });
    }
    return json({ error: { code: 'not_found', message: 'unmocked' } }, 404);
  });
  return seen;
}

test('the desk keeps workspaces apart, grants a summary with the owner approval flow and switches explicitly', async ({ page }) => {
  const seen = await mockApi(page);
  await page.goto('/#executive');
  await expect(page.getByRole('heading', { level: 1, name: 'Executive desk' })).toBeVisible();
  const employer = page.getByRole('listitem').filter({ hasText: 'Employer Ltd' });
  await expect(employer.getByText('Not granted')).toBeVisible();
  await expect(employer).not.toContainText('999');
  await expect(page.getByRole('listitem').filter({ hasText: 'ANUM OS' })).toContainText('12');

  const shop = page.getByRole('listitem').filter({ hasText: 'Tyre Shop' });
  await shop.getByRole('button', { name: 'Grant a summary' }).click();
  await shop.getByLabel('Task counts').uncheck();
  await expect(shop.getByRole('alert')).toContainText('Pick at least one');
  await shop.getByLabel('Task counts').check();
  await shop.getByLabel('Approvals waiting').check();
  await shop.getByRole('button', { name: 'Save grant' }).click();
  await expect(page.locator('.exPage > .notice')).toContainText('Grant saved for Tyre Shop');
  expect(seen.grants).toEqual([{ workspace_id: 'workspace_shop', scopes: ['tasks', 'approvals'] }]);
  await expect(shop.getByText('Granted')).toBeVisible();
  await expect(shop).toContainText('7');

  await expect(shop.getByText('Confidential content opens only after you switch workspace.')).toBeVisible();
  await shop.getByRole('button', { name: 'Switch to Tyre Shop' }).click();
  await expect(shop.getByText('Current workspace')).toBeVisible();

  await shop.getByRole('button', { name: 'Revoke' }).click();
  await shop.getByRole('button', { name: /Revoke: Tyre Shop/ }).click();
  await expect(shop.getByText('No summary is shared.')).toBeVisible();
  expect(seen.revokes).toBe(1);
});

test('the PA moves through the office, reads, and asks before it acts', async ({ page }) => {
  const seen = await mockApi(page);
  await page.goto('/#executive');
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
  await expect(page.getByText('The assistant can never approve or reject anything.')).toBeVisible();

  await input.fill('approve everything');
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByRole('log')).toContainText('I did not understand that');
  expect(seen.invokes.some((b) => b.tool === 'approve')).toBe(false);
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
  await expect(page.getByRole('heading', { name: 'Executive grants are not connected on this server' })).toBeVisible();
  await expect(page.getByText('There are no rooms to visit yet.')).toBeVisible();

  await page.unrouteAll();
  await mockApi(page, { overview: 'denied' });
  await page.goto('/#executive');
  await page.reload();
  await expect(page.getByRole('heading', { name: 'The executive desk is for workspace owners' })).toBeVisible();

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
    await page.getByLabel('Tell the assistant what to do').fill('take me to the executive office');
    await page.getByRole('button', { name: 'Send' }).click();
    await expect(page.getByRole('log')).toContainText('Moved to');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `${width}x${height}`).toBeLessThanOrEqual(0);
    if (dir) await page.screenshot({ path: `${dir}/executive-${width}x${height}.png`, fullPage: true });
  }
});
