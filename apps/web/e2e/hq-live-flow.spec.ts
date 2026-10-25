import { expect, test, type Page } from '@playwright/test';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { flowItems, mockFlow, type FlowMock } from './support/hqFlowApi';

// Live flow, stuck work and office teams in the headquarters (docs/ANUM_MINE_HQ_LIVE_FLOW.md).
// Everything shown comes from the mocked /office/flow, /office/team and event stream; the tests
// check that a marker moves only when the answer moves, and that every state says what to do.
const registry = hqRegistry(TENANT_A, { rooms: 4, desks: 4, agentIds: ['agent_fleet', 'agent_leads', 'agent_care', 'agent_invoice'] });
let flow: FlowMock;

async function open(page: Page, options: Parameters<typeof openHq>[1] = {}) {
  await openHq(page, { registry, ...options, beforeGoto: async (p) => { flow = await mockFlow(p); } });
  await expect(page.locator('.hqFlowDock')).toBeVisible();
}
const dock = (page: Page) => page.locator('.hqFlowDock');
const wholeFloor = (page: Page) => page.getByRole('button', { name: 'Reset camera' }).click();

test('the dock summarises real records and lists what is stuck with a reason and a next step', async ({ page }) => {
  await open(page);
  await expect(dock(page)).toContainText('4 in progress, 1 waiting for a person, 3 stuck');
  await page.getByRole('button', { name: /Live flow/ }).first().click();
  const stuck = dock(page).getByRole('list', { name: 'Stuck work' });
  await expect(stuck.getByRole('listitem')).toHaveCount(3);
  await expect(stuck).toContainText('The last run failed. Supplier API returned 500');
  await expect(stuck).toContainText('Waiting for a person to decide an approval for 6 h. The limit is 4 h.');
  await expect(stuck).toContainText('A new lead has had no owner for 5 h. The limit is 4 h.');
  await expect(stuck).toContainText('No owner yet, so no office');
  await expect(dock(page).getByText('Commerce orders: Not connected.')).toBeVisible();
  await stuck.getByRole('button', { name: 'Decide the approval' }).click();
  await expect(page).toHaveURL(/#approvals$/);
});

test('rooms carry a badge with the real counts and selecting it filters the dock to that office', async ({ page }) => {
  await open(page);
  await wholeFloor(page);
  const badge = page.getByRole('button', { name: /Alpha Room 3: 1 in progress, 1 stuck, 1 waiting for a person/ });
  await expect(badge).toBeVisible();
  await expect(page.locator('.hqFlowBadge[data-room="Alpha-room-0"]')).toHaveAttribute('data-worst', 'none');
  await badge.click();
  await expect(dock(page)).toContainText('Showing Alpha Room 3 only');
  await expect(dock(page).getByRole('list', { name: 'Stuck work' }).getByRole('listitem')).toHaveCount(1);
  await expect(dock(page)).toContainText('Send fleet quote to customer');
  await dock(page).getByRole('button', { name: 'Show all offices' }).click();
  await expect(dock(page).getByRole('list', { name: 'Stuck work' }).getByRole('listitem')).toHaveCount(3);
});

test('a marker moves only because the server moved the item, and the move is announced', async ({ page }) => {
  await open(page);
  await wholeFloor(page);
  await page.waitForTimeout(1500);
  await expect(page.locator('.hqFlowMarker')).toHaveCount(0); // nothing travels for the first answer
  const moved = flowItems().map((i) => i.id === 't_plan'
    ? { ...i, location: { ...i.location!, room_id: 'Alpha-room-1', room_name: 'Alpha Room 2', desk_id: 'Alpha-desk-1' } } : i);
  flow.set(moved);
  flow.emit('task.updated');
  const marker = page.locator('.hqFlowMarker');
  await expect(marker).toHaveCount(1, { timeout: 8000 });
  await expect(marker).toContainText('Plan October tyre rotation');
  await expect(marker).toHaveAttribute('data-travel', 'yes');
  await expect(page.locator('.hqFlowLive')).toContainText('Plan October tyre rotation moved to Alpha Room 2.');
  await expect(marker).toHaveCount(0, { timeout: 9000 }); // gone after a few seconds, never idling on screen
});

test('reduced motion shows the arrival without travelling', async ({ page }) => {
  await open(page, { reducedMotion: true });
  await wholeFloor(page);
  await page.waitForTimeout(1500);
  const moved = flowItems().map((i) => i.id === 't_plan' ? { ...i, location: { ...i.location!, room_id: 'Alpha-room-1', room_name: 'Alpha Room 2' } } : i);
  flow.set(moved);
  flow.emit('task.updated');
  const marker = page.locator('.hqFlowMarker');
  await expect(marker).toHaveCount(1, { timeout: 8000 });
  await expect(marker).toHaveAttribute('data-travel', 'no');
});

test('a task that gets stuck is announced and appears at the top of the stuck list', async ({ page }) => {
  await open(page);
  await page.getByRole('button', { name: /Live flow/ }).first().click();
  await page.waitForTimeout(1500);
  const next = flowItems().map((i) => i.id === 't_plan'
    ? { ...i, stuck: { code: 'run_silent', severity: 'warning', since: new Date().toISOString(), age_seconds: 4000, threshold_seconds: 3600, facts: {}, action: 'open_task' } } : i);
  flow.set(next as never);
  flow.emit('agent_run.checkpointed');
  await expect(dock(page).getByRole('list', { name: 'Stuck work' }).getByRole('listitem')).toHaveCount(4, { timeout: 8000 });
  await expect(page.locator('.hqFlowLive')).toContainText('Plan October tyre rotation is now stuck.');
  await expect(dock(page)).toContainText('The run has not reported for 66 min. The limit is 60 min.');
});

test('the history of an item is read from stored events when asked', async ({ page }) => {
  await open(page);
  await page.getByRole('button', { name: /Live flow/ }).first().click();
  const card = dock(page).getByRole('listitem').filter({ hasText: 'Reorder wiper blades' });
  await card.getByText('History').click();
  await expect(card).toContainText('task.created');
  await expect(card).toContainText('agent_run.failed');
});

test('the office team shows each real agent with role, state, skills and the stuck reason', async ({ page }) => {
  await open(page);
  // The room inspector is hidden below 950 px; there the same panel is reached from the Team overlay.
  const inspectorShown = await page.getByRole('complementary', { name: 'Room details and operations' }).isVisible();
  let team = page.getByRole('region', { name: 'Agents working in this office' });
  if (inspectorShown) {
    const inspector = page.getByRole('complementary', { name: 'Room details and operations' });
    await inspector.locator('.hqRoomList button').filter({ hasText: 'Alpha Room 1' }).click();
    team = inspector.getByRole('region', { name: 'Agents working in this office' });
  } else {
    await page.getByRole('button', { name: 'Team', exact: true }).click();
    team = page.getByRole('dialog', { name: 'Team' }).getByRole('region', { name: 'Agents working in this office' });
  }
  const card = team.getByRole('listitem').filter({ hasText: 'Fleet Planner' });
  await expect(card).toContainText('Role: Operations and warehouse');
  await expect(card).toContainText('Working');
  await expect(card).toContainText('Current work: Plan October tyre rotation');
  await expect(card).toContainText('Tyre planning');
  await expect(card).toContainText('Ready');
  const stuckCard = team.getByRole('listitem').filter({ hasText: 'Invoice Checker' });
  if (inspectorShown) {
    const inspector = page.getByRole('complementary', { name: 'Room details and operations' });
    await inspector.locator('.hqRoomList button').filter({ hasText: 'Alpha Room 4' }).click();
  }
  await expect(stuckCard).toContainText('Stuck');
  await expect(stuckCard).toContainText('Supplier API returned 500');
  await expect(team.getByText('Agents without a workstation (1)')).toBeVisible();
});

test('the building team overlay lists every office team and returns to the office on click', async ({ page }) => {
  await open(page);
  await page.getByRole('button', { name: 'Team', exact: true }).click();
  const modal = page.getByRole('dialog', { name: 'Team' });
  await expect(modal.getByRole('listitem').filter({ hasText: 'Lead Finder' })).toContainText('Web search');
  await modal.getByRole('button', { name: 'Alpha Room 3' }).click();
  await expect(modal).toHaveCount(0);
});

test('no permission and a failing service each say what happened and how to continue', async ({ page }) => {
  await open(page);
  flow.failWith(403);
  await page.getByRole('button', { name: /Live flow/ }).first().click();
  flow.emit('task.updated');
  await expect(dock(page)).toContainText('You cannot read work in this workspace. Ask a workspace owner for the Member or Viewer role.', { timeout: 8000 });
  await expect(dock(page).getByRole('list', { name: 'Stuck work' })).toHaveCount(0);
});

test('a failing service keeps the last answer visible and offers a retry', async ({ page }) => {
  await open(page);
  await page.getByRole('button', { name: /Live flow/ }).first().click();
  await expect(dock(page)).toContainText('Reorder wiper blades');
  flow.failWith(500);
  flow.emit('task.updated');
  await expect(dock(page)).toContainText('Offline. Showing the last answer', { timeout: 8000 });
  await expect(dock(page)).toContainText('Reorder wiper blades');
  flow.failWith(null);
  await dock(page).getByRole('button', { name: 'Try again' }).first().click();
  await expect(dock(page)).not.toContainText('Offline. Showing the last answer');
});

test('Arabic reads right to left without horizontal overflow', async ({ page }) => {
  await open(page, { locale: 'ar' });
  await page.getByRole('button', { name: /التدفق المباشر/ }).first().click();
  await expect(dock(page)).toContainText('عمل متعثر');
  await expect(dock(page)).toContainText(/فشلت آخر عملية تشغيل\..*Supplier API returned 500/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('without WebGL the 2D floor plan still carries the badges', async ({ page }) => {
  await open(page, { noWebgl: true });
  await wholeFloor(page);
  await expect(page.locator('.hqFlowBadge[data-room="Alpha-room-2"]')).toBeVisible();
});

const sizes = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const [width, height] of sizes) {
  test(`fits ${width}x${height} with the dock open and no horizontal overflow`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await open(page);
    await page.getByRole('button', { name: /Live flow/ }).first().click();
    await expect(dock(page).getByRole('list', { name: 'Stuck work' })).toBeVisible();
    await expect(page.locator('.hqBreadcrumb strong')).toHaveText('Alpha Ground');
    const issues = await page.evaluate(() => {
      const doc = document.documentElement;
      const dockBox = document.querySelector('.hqFlowDock')!.getBoundingClientRect();
      const bad: string[] = [];
      if (doc.scrollWidth > window.innerWidth) bad.push('page overflows horizontally');
      if (dockBox.right > window.innerWidth + 1 || dockBox.left < -1) bad.push('dock outside the window');
      if (dockBox.bottom > window.innerHeight + 1) bad.push('dock below the window');
      const body = document.querySelector('.hqFlowBody') as HTMLElement;
      if (body.scrollWidth > body.clientWidth + 1) bad.push('dock body scrolls horizontally');
      return bad;
    });
    expect(issues).toEqual([]);
    const dir = process.env.HQ_FLOW_SHOTS;
    if (dir) await page.screenshot({ path: `${dir}/flow-${width}x${height}.png` });
  });
}
