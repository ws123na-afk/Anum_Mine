import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type Route } from '@playwright/test';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { flowItems, mockFlow, type FlowMock } from './support/hqFlowApi';
import { mockAssign, type AssignMock } from './support/hqAssignApi';
import { workFixture } from './support/hqCampusApi';
import { approvalPagesResponse } from './support/approvalPages';
import { createRoomMock } from './support/meetingRoomApi';

// Work is given and followed inside the headquarters (owner, 2 November 2026: "any task I want to give goes back to
// other modules"). Assigning, opening a task, deciding an approval, opening a meeting and an agent's work log all
// happen in drawers over the scene: the URL never leaves the office. The API is mocked; the requests are the modules' own.
const registry = hqRegistry(TENANT_A, { rooms: 4, desks: 4, agentIds: ['agent_fleet', 'agent_leads', 'agent_care', 'agent_invoice'] });
test.use({ actionTimeout: 30_000 });
const HASH = '#office-preview';
const FULL_HASH = 'ab'.repeat(32);
const stamp = '2026-11-02T08:00:00Z';
let flow: FlowMock;
let assign: AssignMock;
let work: Work;

interface Work { created: { id: string; title: string; agent_id: string }[]; decisions: { path: string; body: Record<string, unknown> | null }[]; taskQueries: string[]; approvals: Record<string, unknown>[] }
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
const approval = (id: string, taskId: string, over: Record<string, unknown> = {}) => ({
  id, task_id: taskId, action: 'send_email', risk_level: 'high', status: 'pending', reason: 'Sending mail outside the company needs a person.', created_at: stamp,
  run_id: 'run_1', step_id: 's2', arguments: { to: 'fleet@example.com', subject: 'October rotation plan' }, payload_hash: FULL_HASH, expires_at: null,
  target: 'smtp: example.com', required_approvals: 1, approvers: [], routing: null, suppressed: [], ...over,
});
const task = (id: string, title: string, status = 'running', agent = 'agent_fleet') => ({ id, title, prompt: `${title}.`, status, tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: stamp, updated_at: stamp, agent_id: agent, created_by: 'user_local' });

/** Desk work, tasks, runs, approvals and agent meetings, answered like the API. Registered last, so it answers first. */
async function mockWork(page: Page): Promise<Work> {
  const state: Work = { created: [], decisions: [], taskQueries: [], approvals: [approval('ap_1', 'task_plan'), approval('ap_send', 't_send', { action: 'send_whatsapp', arguments: { to: '+971500000000', text: 'Your quote is ready.' } })] };
  const room = createRoomMock();
  await page.route(/http:\/\/localhost:8000\/api\/v1\/office\/workstations\/[^/]+\/work$/, async (route) => {
    const desk = new URL(route.request().url()).pathname.split('/')[5];
    const base = workFixture(desk) as ReturnType<typeof workFixture> & { tasks: unknown[] };
    const fresh = state.created.filter((c) => desk === 'Alpha-desk-0' && c.agent_id === 'agent_fleet').map((c) => ({
      id: c.id, title: c.title, status: 'running', priority: 'medium', due_at: null, created_at: stamp, updated_at: stamp, links: {},
      run: { id: `run_${c.id}`, status: 'running', phase: 'planning', checkpoint_version: 1, last_step_id: null, approval_id: null, updated_at: stamp, steps: [] },
    }));
    return json(route, { ...base, tasks: [...fresh, ...base.tasks] });
  });
  await page.route(/http:\/\/localhost:8000\/api\/v1\/tasks(\/[^/?]+)?(\/runs)?(\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    const parts = url.pathname.split('/');
    if (url.pathname === '/api/v1/tasks') {
      state.taskQueries.push(url.search);
      const agent = url.searchParams.get('agent_id');
      const rows = [task('task_plan', 'Plan October tyre rotation'), task('task_old', 'Count spare rims', 'completed'), task('task_other', 'Answer a lead', 'running', 'agent_leads')];
      return json(route, rows.filter((r) => !agent || r.agent_id === agent));
    }
    const id = parts[4];
    if (id.startsWith('task_new')) return route.fallback();
    if (url.pathname.endsWith('/runs')) return json(route, [{ id: 'run_1', task_id: id, status: id === 't_send' ? 'waiting_approval' : 'running', steps: [{ id: 's1', type: 'retrieval', summary: 'Read the fleet tyre data', created_at: stamp }], result: null, created_at: stamp, updated_at: stamp, checkpoint: { phase: 'executing' } }]);
    return json(route, id === 't_send' ? task('t_send', 'Send fleet quote to customer', 'waiting_approval', 'agent_care') : task(id, 'Plan October tyre rotation'));
  });
  await page.route(/http:\/\/localhost:8000\/api\/v1\/approvals(\/.*)?(\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    const paged = approvalPagesResponse(url, state.approvals);
    if (paged !== undefined) return json(route, paged);
    const decide = /^\/api\/v1\/approvals\/([^/]+)\/(approve|reject)$/.exec(url.pathname);
    if (decide && route.request().method() === 'POST') {
      const raw = route.request().postData();
      state.decisions.push({ path: url.pathname, body: raw ? JSON.parse(raw) : null });
      const row = state.approvals.find((a) => a.id === decide[1])!;
      Object.assign(row, { status: decide[2] === 'approve' ? 'approved' : 'rejected', decided_at: stamp, decided_by: 'user_local' });
      return json(route, { task: task(String(row.task_id), 'Plan October tyre rotation', decide[2] === 'approve' ? 'running' : 'cancelled'), run: null, approval: row });
    }
    const one = state.approvals.find((a) => `/api/v1/approvals/${a.id}` === url.pathname);
    return one ? json(route, one) : json(route, { detail: 'Not found' }, 404);
  });
  // The live meeting room's own routes (mtg_1), as e2e/meeting-room.spec.ts serves them.
  await page.route(/http:\/\/localhost:8000\/api\/v1\/agent-meetings(\/mtg_1(\/.*)?|\/budget)?(\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/v1/agent-meetings' && url.searchParams.get('status') === 'active') return route.fallback();
    if (await room.handle(route)) return undefined;
    return route.fallback();
  });
  return state;
}

async function open(page: Page, options: Parameters<typeof openHq>[1] = {}) {
  await openHq(page, { registry, ...options, beforeGoto: async (p) => {
    flow = await mockFlow(p); assign = await mockAssign(p); work = await mockWork(p);
    assign.onCreated((t) => { work.created.push(t); flow.set([...flowItems(), { key: `task:${t.id}`, kind: 'task', id: t.id, title: t.title, stage: 'in_office', status: 'running', since: new Date().toISOString(), created_at: new Date().toISOString(),
      location: { building_id: 'Alpha-building', floor_id: 'Alpha-floor-0', room_id: 'Alpha-room-0', room_name: 'Alpha Room 1', desk_id: 'Alpha-desk-0', agent_id: t.agent_id, agent_name: 'Fleet Planner' }, location_reason: null, waiting: null, stuck: null }] as never); });
  } });
  await expect(page.locator('.hqFlowDock')).toBeVisible();
}
const hq = (page: Page) => page.locator('.hqLive');
const stayed = async (page: Page) => expect(await page.evaluate(() => location.hash)).toBe(HASH);
async function openRoom(page: Page, name: RegExp) {
  await hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click();
  await hq(page).getByRole('button', { name: 'Open Al Noor Fleet HQ' }).click();
  await hq(page).locator('.hqRoomCard').filter({ hasText: name }).click();
}
async function openDesk(page: Page) {
  await openRoom(page, /^Alpha Room 1/);
  await hq(page).locator('.hqDeskList button[data-desk="Alpha-desk-0"]').click();
  await expect(hq(page).locator('.hqSide').getByRole('heading', { level: 2 })).toBeVisible();
}
const drawer = (page: Page, name: string) => page.getByRole('complementary', { name });

test('assign from a desk: the URL stays in the office and the new task appears on that desk and in the flow', async ({ page }) => {
  await open(page);
  await openDesk(page);
  await hq(page).getByRole('button', { name: /^Assign work to / }).click();
  const d = page.locator('.hqAsDialog[role=dialog]');
  await d.getByLabel('Title').fill('Check the spare tyres');
  await d.getByLabel('What should the agent do?').fill('Count every spare tyre and report the sizes.');
  await d.getByRole('button', { name: 'Assign and start' }).click();
  await expect(d).toContainText('Task created: Check the spare tyres');
  expect(assign.posts('/api/v1/office/assignments')[0].body).toMatchObject({ agent_id: 'agent_fleet', source: 'desk', run: true });
  await stayed(page);
  // The desk's own work record (read again after the assignment) lists it in the side panel, beside the scene.
  await d.getByRole('button', { name: 'Close' }).last().click();
  await expect(hq(page).locator('.hqSide .hqTaskList').first()).toContainText('Check the spare tyres', { timeout: 10_000 });
  await page.getByRole('button', { name: /Live flow/ }).first().click();
  await expect(page.locator('.hqFlowDock').getByRole('listitem').filter({ hasText: 'Check the spare tyres' })).toBeVisible({ timeout: 10_000 });
  await stayed(page);
});

test('a desk task opens in a drawer with its run; Escape closes it and focus goes back to the desk', async ({ page }) => {
  await open(page);
  await openDesk(page);
  await hq(page).locator('.hqTaskList summary').first().click();
  const opener = hq(page).getByTestId('desk-open-task').first();
  await opener.click();
  const side = drawer(page, 'Task details');
  await expect(side.getByRole('heading', { level: 2 })).toHaveText('Plan October tyre rotation');
  await expect(side).toContainText('Read the fleet tyre data');
  await expect(side.getByRole('link', { name: 'Open full page' })).toHaveAttribute('href', '#tasks');
  await stayed(page);
  await page.keyboard.press('Escape');
  await expect(side).toHaveCount(0);
  await expect(opener).toBeFocused();
  await stayed(page);
});

test('an approval is decided inside the office: the exact payload and hash, and the hash is sent back', async ({ page }) => {
  await open(page);
  await openDesk(page);
  await hq(page).getByTestId('desk-review-approval').first().click();
  const side = drawer(page, 'Approval');
  await expect(side).toContainText('send_email');
  await expect(side).toContainText('fleet@example.com');
  await expect(side).toContainText('October rotation plan');
  await expect(side.locator('.approvalHash')).toHaveAttribute('title', FULL_HASH);
  await expect(side).toContainText('smtp: example.com');
  await side.getByLabel(/reason/i).fill('Checked the plan.');
  await side.getByRole('button', { name: 'Approve' }).click();
  await expect(side.getByRole('status').filter({ hasText: 'Approved send_email' })).toBeVisible();
  expect(work.decisions).toHaveLength(1);
  expect(work.decisions[0].path).toBe('/api/v1/approvals/ap_1/approve');
  expect(work.decisions[0].body).toMatchObject({ payload_hash: FULL_HASH });
  await expect(side).toContainText('Approved'); // the card reads the record again: now decided
  await stayed(page);
  await side.getByRole('button', { name: 'Close Approval' }).click();
  await expect(side).toHaveCount(0);
});

test('the flow dock decides a waiting task in a drawer; the task opens from it', async ({ page }) => {
  await open(page);
  await page.getByRole('button', { name: /Live flow/ }).first().click();
  await page.locator('.hqFlowDock').getByRole('list', { name: 'Stuck work' }).getByRole('button', { name: 'Decide the approval' }).click();
  const side = drawer(page, 'Approvals waiting');
  await expect(side.getByRole('heading', { level: 2 })).toHaveText('Approvals for this task');
  await expect(side).toContainText('send_whatsapp');
  await side.getByRole('button', { name: 'Open the task' }).first().click();
  await expect(drawer(page, 'Task details')).toContainText('Send fleet quote to customer');
  await expect(drawer(page, 'Task details')).toContainText('This task waits for a person to decide an approval.');
  await stayed(page);
});

test('an agent work log opens from the team panel and each task opens in place', async ({ page }) => {
  await open(page);
  await hq(page).getByRole('button', { name: /Office tools/ }).click();
  await hq(page).getByRole('button', { name: /^Team$/ }).click();
  await page.getByRole('dialog', { name: 'Team' }).getByRole('button', { name: 'Work and results of Fleet Planner' }).click();
  const side = drawer(page, 'Agent work');
  await expect(side.getByRole('heading', { level: 2 })).toHaveText('Fleet Planner: work and results');
  await expect(side.getByRole('list', { name: 'Work log' }).getByRole('listitem')).toHaveCount(2);
  expect(work.taskQueries.some((q) => q.includes('agent_id=agent_fleet'))).toBe(true);
  await side.getByRole('button', { name: 'Open Count spare rims' }).click();
  await expect(drawer(page, 'Task details').getByRole('heading', { level: 2 })).toHaveText('Plan October tyre rotation');
  await stayed(page);
});

test('a meeting opens and is joined inside the office, then closes back to the room', async ({ page }) => {
  await open(page);
  await openRoom(page, /^Alpha Room 2/);
  await hq(page).locator('.hqSide [data-testid="room-module"]').click();
  const side = drawer(page, 'Meeting');
  await side.getByRole('button', { name: /^Open/ }).first().click();
  const room = side.getByRole('region', { name: 'Meeting room' });
  await expect(room.getByRole('heading', { level: 2, name: 'Launch planning' })).toBeVisible();
  await room.getByRole('button', { name: 'Join meeting' }).click();
  await expect(room.getByRole('button', { name: 'Leave meeting' })).toBeVisible();
  await stayed(page);
  await page.keyboard.press('Escape');
  await expect(side).toHaveCount(0);
  await stayed(page);
});

const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
const SHOTS = process.env.HQ_SHOTS_DIR;
for (const locale of ['en', 'ar'] as const) {
  for (const [w, h] of SIZES) {
    test(`${locale} ${w}x${h}: task and approval drawers fit, read right to left in Arabic, and pass axe`, async ({ page }) => {
      test.setTimeout(240_000);
      await page.setViewportSize({ width: w, height: h });
      await open(page, { locale });
      // The office opens the drawer for any card that asks (the event every card uses); asked again until the office has mounted.
      const side = page.locator('.hqWd');
      await expect(async () => {
        await page.evaluate(() => { window.dispatchEvent(new CustomEvent('anum:hq-work-panel', { detail: { kind: 'approval', id: 'ap_1' } })); });
        await expect(side).toBeVisible({ timeout: 1500 });
      }).toPass({ timeout: 30_000 });
      await expect(side.locator('.approvalHash')).toBeVisible();
      if (locale === 'ar') await expect(side).toHaveAttribute('dir', 'rtl');
      const fits = async (label: string) => {
        const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: window.innerWidth }));
        expect(m.sw, `${label}: page scroll width`).toBeLessThanOrEqual(m.iw);
        const box = await side.boundingBox();
        expect(box, label).not.toBeNull();
        expect(box!.x).toBeGreaterThanOrEqual(-1); expect(box!.x + box!.width).toBeLessThanOrEqual(w + 1); expect(box!.y + box!.height).toBeLessThanOrEqual(h + 1);
        const inner = await side.evaluate((el) => [...el.querySelectorAll('button, a, input, textarea')].filter((c) => { const r = c.getBoundingClientRect(); return r.width > 0 && (r.right > el.getBoundingClientRect().right + 1 || r.left < el.getBoundingClientRect().left - 1); }).length);
        expect(inner, `${label}: controls clipped sideways`).toBe(0);
      };
      await fits('approval');
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/stay-approval-${locale}-${w}x${h}.png`, animations: 'disabled', timeout: 120_000 });
      const axe = await new AxeBuilder({ page }).include('.hqWd').withTags(['wcag2a', 'wcag2aa']).analyze();
      expect(axe.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
      await side.getByRole('button', { name: locale === 'ar' ? 'فتح المهمة' : 'Open the task' }).first().click();
      await expect(side.getByTestId('hq-task-status')).toBeVisible();
      await fits('task');
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/stay-task-${locale}-${w}x${h}.png`, animations: 'disabled', timeout: 120_000 });
      const axe2 = await new AxeBuilder({ page }).include('.hqWd').withTags(['wcag2a', 'wcag2aa']).analyze();
      expect(axe2.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
      await page.keyboard.press('Escape');
      await expect(side).toHaveCount(0);
      await stayed(page);
    });
  }
}
