import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { mockFlow } from './support/hqFlowApi';
import { mockProvisioning } from './support/hqProvisioningApi';
import { mockStaffDesks, type StaffDesksMock } from './support/hqStaffDesksApi';

// "Staff my campus" (docs/ANUM_MINE_STAFF_DESKS.md). Everything shown comes from the mocked API (the default 220 desk campus); the
// tests check that a preview writes nothing, that only a click with the shown hash confirms one building at a time, that the 200 agent
// limit and the run cap are explained, and that every state says what to do. Set HQ_SHOTS_DIR to keep a screenshot of every state.
const populated = hqRegistry(TENANT_A, { rooms: 4, desks: 4, agentIds: ['agent_fleet', 'agent_leads', 'agent_care', 'agent_invoice'] });
let staff: StaffDesksMock;

async function open(page: Page, options: Parameters<typeof openHq>[1] = {}) {
  await openHq(page, { registry: populated, ...options, beforeGoto: async (p) => { await mockFlow(p); await mockProvisioning(p); staff = await mockStaffDesks(p); } });
  await expect(tools(page)).toBeVisible({ timeout: 20_000 });
}
const tools = (page: Page) => page.locator('.hqLive').getByRole('button', { name: /Office tools|أدوات المكتب/ });
const dialog = (page: Page) => page.locator('.hqSdDialog[role=dialog]');
async function openDialog(page: Page, ready = true) {
  await tools(page).click();
  await page.locator('.hqLive').getByRole('button', { name: /Staff my campus|تعيين وكلاء لحرمي/ }).click();
  // The dialog is a lazy chunk (kept out of the headquarters chunk budget): allow its first load.
  await expect(dialog(page)).toBeVisible({ timeout: 20_000 });
  if (ready) await expect(dialog(page).locator('.hqSdBuilding').first()).toBeVisible({ timeout: 20_000 });
}
async function shot(page: Page, name: string) {
  const dir = process.env.HQ_SHOTS_DIR;
  if (!dir) return;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: join(dir, `${name}.png`) });
}
async function noOverflow(page: Page) {
  const problems = await page.evaluate(() => {
    const out: string[] = [];
    if (document.documentElement.scrollWidth > window.innerWidth) out.push(`page scrolls sideways (${document.documentElement.scrollWidth} > ${window.innerWidth})`);
    const d = document.querySelector('.hqSdDialog') as HTMLElement | null;
    if (!d) return ['no dialog'];
    const box = d.getBoundingClientRect();
    if (box.right > window.innerWidth + 1 || box.bottom > window.innerHeight + 1 || box.left < -1 || box.top < -1) out.push('dialog leaves the screen');
    if (d.scrollWidth > d.clientWidth + 1) out.push('dialog content is wider than the dialog');
    const body = d.querySelector('.hqSdBody') as HTMLElement;
    if (body.scrollWidth > body.clientWidth + 1) out.push('dialog body scrolls sideways');
    for (const el of d.querySelectorAll<HTMLElement>('button, input, select, label')) {
      const r = el.getBoundingClientRect();
      if (r.width && r.height && (r.right > box.right + 1 || r.left < box.left - 1)) out.push(`clipped: ${el.textContent?.slice(0, 30)}`);
    }
    return out;
  });
  expect(problems).toEqual([]);
}

test('the default campus plan shows 200 desks to staff, 20 left empty by the limit, the cost and the gaps; a preview writes nothing', async ({ page }) => {
  await open(page);
  await openDialog(page);
  const d = dialog(page);
  await expect(d.getByRole('heading', { name: 'Staff my campus' })).toBeVisible();
  await expect(d.locator('.hqSdBuilding')).toHaveCount(8);
  const stats = d.locator('.hqSdStats');
  await expect(stats.locator('li').filter({ hasText: 'Will get an agent' })).toContainText('200');
  await expect(d.getByText('Desks that stay empty because a workspace can have at most 200 agents: 20.')).toBeVisible();
  await expect(d.getByText('If all agents (200) were turned on and each used its whole cap: USD 1,000 a month.')).toBeVisible();
  await expect(d.getByText('This workspace has no monthly budget set.')).toBeVisible();
  await expect(d.getByText('Limited').first()).toBeVisible();
  await expect(d.getByText('Operations Dispatcher: A tool that moves an existing task to another person or agent')).toBeVisible();
  await expect(d.getByText('Every agent is created paused')).toBeVisible();
  await shot(page, 'plan-1440');
  expect(staff.requests.every((r) => r.path === '/preview')).toBe(true);
  expect(staff.state.staffed.size).toBe(0);
});

test('confirm stages one building at a time with a click and its own hash, then lists every desk with paging and offers Turn on', async ({ page }) => {
  await open(page);
  await openDialog(page);
  const d = dialog(page);
  await expect(d.getByRole('button', { name: 'Staff desks (200)' })).toBeEnabled();
  await d.getByRole('button', { name: 'Staff desks (200)' }).click();
  await expect(d.getByRole('heading', { name: 'Desks staffed' })).toBeVisible({ timeout: 20_000 });
  const posts = staff.requests.filter((r) => r.path === '/confirm');
  expect(posts.map((p) => p.body?.building_id)).toEqual(['campus-01-hq', 'campus-02-commerce', 'campus-03-tyre-pulse', 'campus-04-corporate-services', 'campus-05-knowledge-ai', 'campus-06-new-ventures']);
  expect(posts.every((p) => p.body?.input_mode === 'click' && /^[0-9a-f]{64}$/.test(String(p.body?.payload_hash)))).toBe(true);
  expect(new Set(posts.map((p) => p.body?.payload_hash)).size).toBe(6);
  await expect(d.getByText('Created 200, seated unfinished 0, skipped 0, failed 0. All new agents are paused.')).toBeVisible();
  await expect(d.getByText('Page 1 of 8')).toBeVisible();
  await d.getByRole('button', { name: 'Next' }).click();
  await expect(d.getByText('Page 2 of 8')).toBeVisible();
  await shot(page, 'result-1440');
  await d.getByRole('button', { name: 'Turn agents on' }).first().click();
  await expect(d.getByText('0 of 200 selected')).toBeVisible();
  await d.getByRole('button', { name: 'Select this page' }).click();
  await d.getByRole('button', { name: 'Review cost before turning on' }).click();
  await expect(d.getByText('Turning on the selected agents (25) allows up to USD 125 a month in total, the sum of their caps.')).toBeVisible();
  await shot(page, 'turn-on-1440');
  await d.getByRole('button', { name: 'Turn on selected (25)' }).click();
  await expect(d.getByText('Turned on: 25. Could not turn on: 0.')).toBeVisible();
  const on = staff.requests.find((r) => r.path === '/turn-on')!;
  expect(on.body).toMatchObject({ input_mode: 'click' });
  expect(on.body?.agent_ids).toHaveLength(25);
  await expect(d.getByText('0 of 175 selected')).toBeVisible();
});

test('one agent can be turned on alone', async ({ page }) => {
  await open(page);
  await openDialog(page);
  const d = dialog(page);
  await d.getByRole('button', { name: 'Staff desks (200)' }).click();
  await expect(d.getByRole('heading', { name: 'Desks staffed' })).toBeVisible({ timeout: 20_000 });
  await d.getByRole('button', { name: 'Turn agents on' }).first().click();
  await d.getByRole('button', { name: /^Turn on Facilities Office|^Turn on Operations Dispatcher, Facilities Office 1/ }).first().click();
  await expect(d.getByText('Turned on: 1. Could not turn on: 0.')).toBeVisible();
  expect(staff.requests.filter((r) => r.path === '/turn-on')).toHaveLength(1);
});

test('leaving out a building or a room and capping the run re-reads the plan and changes the hashes', async ({ page }) => {
  await open(page);
  await openDialog(page);
  const d = dialog(page);
  const before = staff.requests.length;
  await d.getByRole('checkbox', { name: /Knowledge & AI/ }).uncheck();
  await expect(d.getByText('7 of 8 buildings selected')).toBeVisible();
  await expect(d.locator('.hqSdStats li').filter({ hasText: 'Will get an agent' })).toContainText('200', { timeout: 10_000 });
  await expect.poll(() => staff.requests.at(-1)?.body?.buildings?.length).toBe(7);
  expect(staff.requests.length).toBeGreaterThan(before);
  await d.getByRole('button', { name: /Show rooms: ANUM HQ/ }).click();
  await d.getByRole('checkbox', { name: /Command Center/ }).uncheck();
  await expect.poll(() => staff.requests.at(-1)?.body?.exclude_rooms).toEqual(['campus-01-hq-command-center']);
  await d.getByLabel('Create at most this many agents in this run').fill('10');
  await expect(d.getByText('Desks that stay empty because you limited this run to 10 agents:')).toBeVisible();
  await expect(d.getByRole('button', { name: 'Staff desks (10)' })).toBeEnabled();
  await shot(page, 'cap-1440');
});

test('desks can be searched, filtered and kept empty on purpose', async ({ page }) => {
  await open(page);
  await openDialog(page);
  const d = dialog(page);
  await expect(d.getByText('Page 1 of 9')).toBeVisible();
  await d.getByLabel('Search desks, rooms or agent names').fill('Executive Office');
  await expect(d.locator('.hqSdDesks li')).toHaveCount(3);
  await d.getByRole('button', { name: /Keep empty: Desk 2, Executive Office/ }).click();
  await expect(d.locator('.hqSdDesks li').nth(1)).toContainText('Kept empty');
  expect(staff.requests.find((r) => r.path === '/keep-empty')?.body).toEqual({ desk_ids: ['campus-01-hq-executive-office-d2'], keep_empty: true });
  await d.getByRole('button', { name: /Staff it again: Desk 2/ }).click();
  await expect(d.locator('.hqSdDesks li').nth(1)).toContainText('Will get an agent');
  await d.getByLabel('Search desks, rooms or agent names').fill('');
  await d.getByLabel('Show', { exact: true }).selectOption('over_limit');
  await expect(d.getByText('Desks: 20', { exact: true })).toBeVisible();
  await expect(d.locator('.hqSdDesks li').first()).toContainText('Stays empty: agent limit');
});

test('existing agents reduce the free places, a workspace budget is compared with the cap total', async ({ page }) => {
  await open(page);
  staff.setExistingAgents(150);
  staff.setBudget(100);
  await openDialog(page);
  const d = dialog(page);
  await expect(d.getByText('Agents in this workspace: 150 of 200. Places left: 50.')).toBeVisible();
  await expect(d.getByText('Desks that stay empty because a workspace can have at most 200 agents: 170.')).toBeVisible();
  await expect(d.getByText('This workspace budget: USD 100 a month. The total is above the budget.')).toBeVisible();
  await shot(page, 'limit-1440');
});

test('a person who is not an owner can review the plan but not confirm', async ({ page }) => {
  await open(page);
  staff.setCanConfirm(false);
  await openDialog(page);
  const d = dialog(page);
  await expect(d.getByText('Only workspace owners can staff desks or turn agents on.')).toBeVisible();
  await expect(d.getByRole('button', { name: 'Staff desks (200)' })).toBeDisabled();
  await expect(d.getByRole('button', { name: /Keep empty/ })).toHaveCount(0);
});

test('an unreadable plan shows the cause and a retry; no access explains who can help', async ({ page }) => {
  await open(page);
  staff.setPreviewFailure(500);
  await openDialog(page, false);
  const d = dialog(page);
  await expect(d.getByRole('alert')).toContainText('The plan could not be loaded.');
  await shot(page, 'error-1440');
  staff.setPreviewFailure(null);
  await d.getByRole('button', { name: 'Try again' }).click();
  await expect(d.locator('.hqSdBuilding')).toHaveCount(8);
  await d.getByRole('button', { name: 'Close', exact: true }).first().click();
  staff.setPreviewFailure(403);
  await tools(page).click();
  await page.locator('.hqLive').getByRole('button', { name: /Staff my campus/ }).click();
  await expect(dialog(page).getByRole('alert')).toContainText('Ask a workspace owner');
});

test('a stale hash stops the run with a way back, and a room that could not be seated is named', async ({ page }) => {
  await open(page);
  await openDialog(page);
  const d = dialog(page);
  staff.setConfirmFailure(409, 'What would be created has changed since you opened it.');
  await d.getByRole('button', { name: 'Staff desks (200)' }).click();
  await expect(d.getByRole('alert')).toContainText('Staffing stopped.');
  await expect(d.getByText('The plan changed since you opened it.')).toBeVisible();
  staff.setConfirmFailure(null);
  staff.failRoom('campus-01-hq-security-operations');
  await d.getByRole('button', { name: 'Review what is left' }).click();
  await expect(d.locator('.hqSdBuilding')).toHaveCount(8);
  await d.getByRole('button', { name: 'Staff desks (200)' }).click();
  await expect(d.getByRole('heading', { name: 'Desks partly staffed' })).toBeVisible({ timeout: 20_000 });
  await d.getByLabel('Show', { exact: true }).selectOption('failed');
  await expect(d.locator('.hqSdDesks li')).toHaveCount(5);
  await expect(d.locator('.hqSdDesks li').first()).toContainText('The agent was built but not seated');
  await shot(page, 'partial-1440');
});

test('Organisation gaps offers Staff my campus', async ({ page }) => {
  await open(page);
  await tools(page).click();
  await page.locator('.hqLive').getByRole('button', { name: /^Organisation gaps/ }).click();
  await page.getByRole('button', { name: 'Staff my campus' }).click();
  await expect(dialog(page)).toBeVisible({ timeout: 20_000 });
});

test('keyboard users can operate the dialog and Escape returns focus', async ({ page }) => {
  await open(page);
  await openDialog(page);
  const d = dialog(page);
  await expect(d.getByRole('tab', { name: 'Staff desks' })).toHaveAttribute('aria-selected', 'true');
  await d.getByRole('checkbox', { name: /ANUM HQ/ }).focus();
  await page.keyboard.press('Space');
  await expect(d.getByText('7 of 8 buildings selected')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog(page)).toHaveCount(0);
});

test('Arabic shows Arabic names, right to left', async ({ page }) => {
  await open(page, { locale: 'ar' });
  await openDialog(page);
  const d = dialog(page);
  await expect(page.locator('.hqSdBackdrop')).toHaveAttribute('dir', 'rtl');
  await expect(d.getByRole('heading', { name: 'تعيين وكلاء لحرمي' })).toBeVisible();
  await expect(d.getByText('مقر ANUM')).toBeVisible();
  await expect(d.getByText('الوكلاء في مساحة العمل هذه: 0 من 200')).toBeVisible();
  await d.getByRole('button', { name: /عرض الغرف: مقر ANUM/ }).click();
  await expect(d.locator('.hqSdRoom').filter({ hasText: 'مكتب المرافق' })).toBeVisible();
  await shot(page, 'arabic-1440');
});

const SIZES = [
  { name: '1440x900', width: 1440, height: 900 }, { name: '1366x768', width: 1366, height: 768 }, { name: '1024x768', width: 1024, height: 768 },
  { name: '768x1024', width: 768, height: 1024 }, { name: '390x844', width: 390, height: 844 },
] as const;
for (const size of SIZES) {
  test(`@gate the dialog fits ${size.name} in English and Arabic`, async ({ page }) => {
    await page.setViewportSize({ width: size.width, height: size.height });
    await open(page);
    await openDialog(page);
    const d = dialog(page);
    await d.getByRole('button', { name: /Show rooms: ANUM HQ/ }).click();
    await noOverflow(page);
    await shot(page, `plan-${size.name}`);
    await d.getByRole('button', { name: 'Staff desks (200)' }).click();
    await expect(d.getByRole('heading', { name: 'Desks staffed' })).toBeVisible({ timeout: 20_000 });
    await noOverflow(page);
    await shot(page, `result-${size.name}`);
    await d.getByRole('button', { name: 'Turn agents on' }).first().click();
    await d.getByRole('button', { name: 'Select this page' }).click();
    await d.getByRole('button', { name: 'Review cost before turning on' }).click();
    await expect(d.getByRole('button', { name: 'Turn on selected (25)' })).toBeVisible();
    await noOverflow(page);
    await shot(page, `turn-on-${size.name}`);
  });
  test(`@gate the Arabic dialog fits ${size.name}`, async ({ page }) => {
    await page.setViewportSize({ width: size.width, height: size.height });
    await open(page, { locale: 'ar' });
    await openDialog(page);
    await noOverflow(page);
    await shot(page, `plan-ar-${size.name}`);
  });
}
