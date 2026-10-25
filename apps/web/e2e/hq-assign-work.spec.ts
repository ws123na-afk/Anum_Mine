import { expect, test, type Page } from '@playwright/test';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { flowItems, mockFlow, type FlowMock } from './support/hqFlowApi';
import { mockAssign, mockBuildingAssistant, type AssignMock } from './support/hqAssignApi';

// Assign work from inside the headquarters, and add a building from the assistant (docs/ANUM_MINE_ASSIGN_WORK.md).
// The API is mocked; the tests check that the person never leaves the office, that one governed request is sent
// with an idempotency key, that every state says what to do, and that only a click confirms a building.
const registry = hqRegistry(TENANT_A, { rooms: 4, desks: 4, agentIds: ['agent_fleet', 'agent_leads', 'agent_care', 'agent_invoice'] });
let flow: FlowMock;
let api: AssignMock;
const HASH = '#office-preview';

async function open(page: Page, options: Parameters<typeof openHq>[1] = {}) {
  await openHq(page, { registry, ...options, beforeGoto: async (p) => { flow = await mockFlow(p); api = await mockAssign(p); await mockBuildingAssistant(p, api); } });
  await expect(page.locator('.hqFlowDock')).toBeVisible();
}
const hq = (page: Page) => page.locator('.hqLive');
const dialog = (page: Page) => page.locator('.hqAsDialog[role=dialog]');
async function openTeam(page: Page) {
  await hq(page).getByRole('button', { name: /Office tools|أدوات المكتب/ }).click();
  await hq(page).getByRole('button', { name: /^Team$|^الفريق$/ }).click();
}
async function openRoom(page: Page, name = /^Alpha Room 1/) {
  await hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click();
  await hq(page).getByRole('button', { name: 'Open Al Noor Fleet HQ' }).click();
  await hq(page).locator('.hqRoomCard').filter({ hasText: name }).click();
}
const stayed = async (page: Page) => expect(await page.evaluate(() => location.hash)).toBe(HASH);

test('assign from an agent card in the team panel: no navigation, one governed request with a key, toast, side panel, flow dock', async ({ page }) => {
  await open(page);
  api.onCreated((task) => flow.set([...flowItems(), { key: `task:${task.id}`, kind: 'task', id: task.id, title: task.title, stage: 'in_office', status: 'running', since: new Date().toISOString(), created_at: new Date().toISOString(),
    location: { building_id: 'Alpha-building', floor_id: 'Alpha-floor-0', room_id: 'Alpha-room-0', room_name: 'Alpha Room 1', desk_id: 'Alpha-desk-0', agent_id: task.agent_id, agent_name: 'Fleet Planner' }, location_reason: null, waiting: null, stuck: null }] as never));
  await openTeam(page);
  await page.getByRole('dialog', { name: 'Team' }).getByRole('button', { name: 'Assign work to Fleet Planner' }).click();
  const d = dialog(page);
  await expect(d).toBeVisible();
  await expect(d.getByRole('combobox', { name: 'Agent', exact: true })).toHaveValue('agent_fleet');
  await expect(d).toContainText('Change things inside ANUM: asks a person first');
  await expect(d).toContainText('High risk tools it may use: send_email');
  await d.getByLabel('Title').fill('Count spare tyres');
  await d.getByLabel('What should the agent do?').fill('Count every spare tyre in the yard.');
  await d.getByLabel('Expected result (optional)').fill('One number per size');
  await d.getByLabel('Priority').selectOption('high');
  await d.getByRole('button', { name: 'Assign and start' }).click();
  await expect(d).toContainText('Task created: Count spare tyres');
  await expect(d).toContainText('It has started.');
  const post = api.posts('/api/v1/office/assignments');
  expect(post).toHaveLength(1);
  expect(post[0].headers['idempotency-key']).toMatch(/^assign-/);
  expect(post[0].body).toMatchObject({ agent_id: 'agent_fleet', title: 'Count spare tyres', instructions: 'Count every spare tyre in the yard.', expected_result: 'One number per size', priority: 'high', run: true, source: 'team_panel' });
  await stayed(page);
  await d.getByRole('button', { name: 'Open the task' }).click();
  const side = page.getByRole('complementary', { name: 'Task details' });
  await expect(side).toContainText('Count spare tyres');
  await expect(side).toContainText('Count every spare tyre in the yard.');
  await expect(side).toContainText('Planning the work');
  await stayed(page);
  await page.keyboard.press('Escape'); // the side panel closes with Escape
  await expect(side).toHaveCount(0);
  await page.getByRole('button', { name: /Live flow/ }).first().click();
  await expect(page.locator('.hqFlowDock').getByRole('listitem').filter({ hasText: 'Count spare tyres' })).toBeVisible({ timeout: 10000 });
});

test('assign from a room: the agent seated there is preselected and the office stays open', async ({ page }) => {
  await open(page);
  await openRoom(page);
  await hq(page).getByRole('button', { name: 'Assign work in this office' }).click();
  await expect(dialog(page).getByRole('combobox', { name: 'Agent', exact: true })).toHaveValue('agent_fleet');
  await expect(dialog(page).locator('option[value=agent_fleet]')).toContainText('sits in');
  await dialog(page).getByRole('button', { name: 'Cancel' }).click();
  await expect(dialog(page)).toHaveCount(0);
  await stayed(page);
  expect(api.posts('/api/v1/office/assignments')).toHaveLength(0);
});

test('assign from a desk opens the agent card with an Assign button and sends the desk as the source', async ({ page }) => {
  await open(page);
  await openRoom(page);
  await hq(page).locator('.hqDeskList button').first().click();
  await hq(page).getByRole('button', { name: /^Assign work to / }).click();
  const d = dialog(page);
  await d.getByLabel('Title').fill('Check the desk');
  await d.getByLabel('What should the agent do?').fill('Look at it.');
  await d.getByLabel('Start it now').uncheck();
  await d.getByRole('button', { name: 'Assign', exact: true }).click();
  await expect(d).toContainText('It waits in Tasks until someone runs it.');
  expect(api.posts('/api/v1/office/assignments')[0].body).toMatchObject({ source: 'desk', run: false });
});

test('validation shows what is missing and sends nothing', async ({ page }) => {
  await open(page);
  await openTeam(page);
  await page.getByRole('button', { name: 'Assign work to Fleet Planner' }).click();
  const d = dialog(page);
  await d.getByRole('button', { name: 'Assign and start' }).click();
  await expect(d.getByText('Give the task a title.')).toBeVisible();
  await expect(d.getByText('Say what the agent should do.')).toBeVisible();
  await expect(d.getByRole('status').filter({ hasText: 'Fix 2 things above' })).toBeVisible();
  await d.getByLabel('Title').fill('T'); await d.getByLabel('What should the agent do?').fill('Do it');
  await d.getByLabel('Due (optional)').fill('2020-01-01T09:00');
  await d.getByRole('button', { name: 'Assign and start' }).click();
  await expect(d.getByText('The due time is in the past.')).toBeVisible();
  expect(api.posts('/api/v1/office/assignments')).toHaveLength(0);
});

test('a paused agent says to turn it on first, and turning it on makes the dialog ready', async ({ page }) => {
  await open(page);
  await openTeam(page);
  await page.getByRole('button', { name: 'Assign work to Fleet Planner' }).click();
  const d = dialog(page);
  await d.getByRole('combobox', { name: 'Agent', exact: true }).selectOption('agent_paused');
  await expect(d).toContainText('This agent is paused: turn it on first.');
  await expect(d.getByRole('button', { name: 'Assign and start' })).toBeDisabled();
  await d.getByRole('button', { name: 'Turn it on' }).click();
  await expect(d.getByRole('button', { name: 'Assign and start' })).toBeEnabled();
  await expect(d).not.toContainText('turn it on first');
  expect(api.requests.some((r) => r.method === 'PATCH' && r.path.endsWith('/agent_paused') && (r.body as { status: string }).status === 'active')).toBe(true);
});

test('a person who may not run this agent is told who can, and nothing is sent', async ({ page }) => {
  await open(page);
  await openTeam(page);
  await page.getByRole('button', { name: 'Assign work to Fleet Planner' }).click();
  await dialog(page).getByRole('combobox', { name: 'Agent', exact: true }).selectOption('agent_locked');
  await expect(dialog(page)).toContainText('You cannot assign work to this agent.');
  await expect(dialog(page)).toContainText('Ask a workspace owner.');
  await expect(dialog(page).getByRole('button', { name: 'Assign and start' })).toBeDisabled();
});

test('a refused request keeps the text, says why, and a retry reuses the same key (no second task)', async ({ page }) => {
  await open(page);
  await openTeam(page);
  await page.getByRole('button', { name: 'Assign work to Fleet Planner' }).click();
  const d = dialog(page);
  await d.getByLabel('Title').fill('Keep my words'); await d.getByLabel('What should the agent do?').fill('Long text I typed');
  api.failNext(500, 'The service failed.');
  await d.getByRole('button', { name: 'Assign and start' }).click();
  await expect(d.getByRole('alert').filter({ hasText: 'The work was not assigned. The service failed.' })).toBeVisible();
  await expect(d.getByLabel('Title')).toHaveValue('Keep my words');
  await d.getByRole('button', { name: 'Assign and start' }).click();
  await expect(d).toContainText('Task created: Keep my words');
  const posts = api.posts('/api/v1/office/assignments');
  expect(posts).toHaveLength(2);
  expect(posts[0].headers['idempotency-key']).toBe(posts[1].headers['idempotency-key']);
});

test('a server refusal for a paused agent is explained and offered the fix', async ({ page }) => {
  await open(page);
  await openTeam(page);
  await page.getByRole('button', { name: 'Assign work to Fleet Planner' }).click();
  const d = dialog(page);
  await d.getByLabel('Title').fill('T'); await d.getByLabel('What should the agent do?').fill('Do');
  api.failNext(409, 'Fleet Planner is paused. Resume it in Agents to run tasks with it.');
  await d.getByRole('button', { name: 'Assign and start' }).click();
  await expect(d).toContainText('The agent was paused while you wrote. Turn it on, then send again.');
  await expect(d.getByRole('button', { name: 'Turn it on' })).toBeVisible();
});

test('offline: the dialog says so and does not send', async ({ page, context }) => {
  await open(page);
  await openTeam(page);
  await page.getByRole('button', { name: 'Assign work to Fleet Planner' }).click();
  await dialog(page).getByLabel('Title').fill('T'); await dialog(page).getByLabel('What should the agent do?').fill('Do');
  await context.setOffline(true);
  await expect(dialog(page)).toContainText('You are offline.');
  await expect(dialog(page).getByRole('button', { name: 'Assign and start' })).toBeDisabled();
  await context.setOffline(false);
  await expect(dialog(page).getByRole('button', { name: 'Assign and start' })).toBeEnabled();
});

test('keyboard: Escape closes the dialog and focus returns to the button that opened it', async ({ page }) => {
  await open(page);
  await openTeam(page);
  const opener = page.getByRole('button', { name: 'Assign work to Fleet Planner' });
  await opener.focus(); await page.keyboard.press('Enter');
  await expect(dialog(page)).toBeVisible();
  await expect(dialog(page).locator(':focus')).toHaveCount(1);
  await page.keyboard.press('Escape');
  await expect(dialog(page)).toHaveCount(0);
  await expect(opener).toBeFocused();
});

test('the dock next step opens a stuck task beside the office instead of navigating', async ({ page }) => {
  await open(page);
  await page.getByRole('button', { name: /Live flow/ }).first().click();
  await page.locator('.hqFlowDock').getByRole('list', { name: 'Stuck work' }).getByRole('button', { name: /Retry|retry/ }).first().click();
  await expect(page.getByRole('complementary', { name: 'Task details' })).toBeVisible();
  await stayed(page);
});

test('Arabic: right to left dialog with Arabic words', async ({ page }) => {
  await open(page, { locale: 'ar' });
  await hq(page).getByRole('button', { name: /أدوات المكتب/ }).click();
  await hq(page).getByRole('button', { name: /^الفريق$/ }).click();
  await page.getByRole('button', { name: /^إسناد عمل إلى / }).first().click();
  const d = page.locator('.hqAsBackdrop');
  await expect(d).toHaveAttribute('dir', 'rtl');
  await expect(dialog(page)).toContainText('ماذا يجب أن يفعل الوكيل؟');
  await dialog(page).getByRole('button', { name: 'إسناد وبدء' }).click();
  await expect(dialog(page)).toContainText('اكتب عنوانًا للمهمة.');
});

test('the assistant opens the same dialog pre-filled instead of navigating', async ({ page }) => {
  await open(page);
  await page.locator('#pa-input').fill('ask Fleet Planner to count the spare tyres');
  await page.locator('#pa-input').press('Enter');
  const d = dialog(page);
  await expect(d).toBeVisible();
  await expect(d.getByRole('combobox', { name: 'Agent', exact: true })).toHaveValue('agent_fleet');
  await expect(d.getByLabel('What should the agent do?')).toHaveValue('count the spare tyres');
  await stayed(page);
  expect(api.posts('/api/v1/office/assignments')).toHaveLength(0); // opening is not assigning
  expect(api.posts('/api/v1/executive-pa/invoke')).toHaveLength(0);
});

// ---- add a building ----------------------------------------------------------------------

const ask = async (page: Page, text: string) => { await page.locator('#pa-input').fill(text); await page.locator('#pa-input').press('Enter'); };
const card = (page: Page) => page.getByRole('region', { name: 'Building proposal' });

test('a building request becomes a proposal card; nothing is created until Confirm is clicked', async ({ page }) => {
  await open(page);
  await ask(page, 'add a building called North Depot with 3 floors');
  await expect(card(page)).toContainText('Proposal: add the building North Depot');
  await expect(card(page)).toContainText('3 floors, each 60 by 40 metres');
  await expect(card(page)).toContainText('6 template rooms');
  await expect(card(page)).toContainText('Ground floor: Reception, Open office');
  await expect(card(page)).toContainText('No desks and no agents are added.');
  expect(api.posts('/api/v1/office/building-proposals/confirm')).toHaveLength(0);
  const proposeCall = api.posts('/api/v1/executive-pa/invoke')[0].body as { tool: string; input_mode: string; confirm_hash?: string };
  expect(proposeCall).toMatchObject({ tool: 'propose_add_building', input_mode: 'typed' });
  expect(proposeCall.confirm_hash).toBeUndefined();
  await card(page).getByRole('button', { name: 'Confirm and add the building' }).click();
  await expect(card(page)).toContainText('Added North Depot: 3 floors and 6 rooms.');
  const confirm = api.posts('/api/v1/office/building-proposals/confirm');
  expect(confirm).toHaveLength(1);
  expect(confirm[0].body).toEqual({ name: 'North Depot', floors: 3, lang: 'en', payload_hash: 'cd'.repeat(32), input_mode: 'click' });
  expect(confirm[0].headers['idempotency-key']).toMatch(/^assign-/);
});

test('Cancel creates nothing', async ({ page }) => {
  await open(page);
  await ask(page, 'add a building called North Depot with 2 floors');
  await card(page).getByRole('button', { name: 'Cancel' }).click();
  await expect(card(page)).toContainText('Cancelled. Nothing was created.');
  expect(api.posts('/api/v1/office/building-proposals/confirm')).toHaveLength(0);
});

test('a missing name or floor count gets one short question, then the proposal', async ({ page }) => {
  await open(page);
  await ask(page, 'add a building called North Depot');
  await expect(page.locator('.paLog')).toContainText('How many floors does North Depot have?');
  expect(api.posts('/api/v1/executive-pa/invoke')).toHaveLength(0); // asked before any call
  await ask(page, 'two');
  await expect(card(page)).toContainText('2 floors, each 60 by 40 metres');
  await ask(page, 'add a building with 4 floors');
  await expect(page.locator('.paLog')).toContainText('What is the building called?');
  await ask(page, 'South Yard');
  await expect(card(page).last()).toContainText('Proposal: add the building South Yard');
});

test('a person who is not an owner can read the proposal but Confirm is disabled', async ({ page }) => {
  await open(page);
  api.proposalState.canConfirm = false;
  await ask(page, 'add a building called North Depot with 1 floors');
  await expect(card(page)).toContainText('Only a workspace owner can confirm.');
  await expect(card(page).getByRole('button', { name: 'Confirm and add the building' })).toBeDisabled();
});

test('a stale or refused confirm says so, creates nothing and offers to review again', async ({ page }) => {
  await open(page);
  await ask(page, 'add a building called North Depot with 3 floors');
  api.proposalState.failConfirm = { status: 409, detail: 'The proposal changed. Review it again.' };
  await card(page).getByRole('button', { name: 'Confirm and add the building' }).click();
  await expect(card(page).getByRole('alert')).toContainText('What would be created has changed');
  await expect(card(page).getByRole('button', { name: 'Review again' })).toBeVisible();
  api.proposalState.failConfirm = { status: 403, detail: 'Your role cannot do this.' };
  await card(page).getByRole('button', { name: 'Confirm and add the building' }).click();
  await expect(card(page).getByRole('alert')).toContainText('The building was not added, and nothing was created. Your role cannot do this.');
});

test('Command: the same request on the Command page shows the proposal card, not a task plan', async ({ page }) => {
  await open(page);
  await page.goto('/#command');
  await page.getByRole('textbox', { name: /Command|request|What/i }).first().fill('add a building called North Depot with 3 floors');
  await page.keyboard.press('Enter');
  await expect(card(page)).toContainText('Proposal: add the building North Depot');
  expect(api.posts('/api/v1/office/building-proposals/confirm')).toHaveLength(0);
});

// ---- five viewports ----------------------------------------------------------------------

const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
const SHOTS = process.env.HQ_SHOTS_DIR;
for (const [w, h] of SIZES) {
  test(`${w}x${h}: dialog, task panel and proposal card fit without horizontal overflow`, async ({ page }) => {
    test.setTimeout(240_000);
    await page.setViewportSize({ width: w, height: h });
    // The list view: the dialog and card layouts are what is checked, not the 3D scene.
    await openHq(page, { registry, noWebgl: true, beforeGoto: async (p) => { flow = await mockFlow(p); api = await mockAssign(p); await mockBuildingAssistant(p, api); } });
    await expect(hq(page).getByRole('button', { name: /Office tools/ })).toBeVisible({ timeout: 60_000 });
    const noOverflow = async (label: string) => {
      const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: window.innerWidth }));
      expect(m.sw, `${label}: page scroll width`).toBeLessThanOrEqual(m.iw);
      for (const sel of ['.hqAsDialog', '.hqAsSide', '.hqAsCard']) {
        const box = await page.locator(sel).first().boundingBox().catch(() => null);
        if (box) { expect(box.x, `${label} ${sel} left`).toBeGreaterThanOrEqual(-1); expect(box.x + box.width, `${label} ${sel} right`).toBeLessThanOrEqual(w + 1); expect(box.y + box.height, `${label} ${sel} bottom`).toBeLessThanOrEqual(h + 1); }
      }
    };
    const shot = async (name: string) => { if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}-${w}x${h}.png`, animations: 'disabled', timeout: 45_000 }); };
    await openTeam(page);
    await page.getByRole('dialog', { name: 'Team' }).getByRole('button', { name: 'Assign work to Fleet Planner' }).click();
    const d = dialog(page);
    await d.getByLabel('Title').fill('Count spare tyres'); await d.getByLabel('What should the agent do?').fill('Count every spare tyre in the yard and report the sizes.');
    await d.getByLabel('Expected result (optional)').fill('One number per size');
    await noOverflow('dialog'); await shot('assign-dialog');
    await d.getByRole('button', { name: 'Assign and start' }).click();
    await expect(d).toContainText('Task created');
    await noOverflow('done'); await shot('assign-done');
    await d.getByRole('button', { name: 'Open the task' }).click();
    await expect(page.getByRole('complementary', { name: 'Task details' })).toContainText('Planning the work');
    await noOverflow('task panel'); await shot('task-panel');
    await page.keyboard.press('Escape');
    await page.locator('#pa-input').scrollIntoViewIfNeeded().catch(() => undefined);
    await ask(page, 'add a building called North Depot with 3 floors');
    await expect(card(page)).toBeVisible();
    await card(page).scrollIntoViewIfNeeded();
    await noOverflow('card'); await shot('building-card');
  });
}
