import { expect, test, type Page } from '@playwright/test';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { mockFlow } from './support/hqFlowApi';
import { mockProvisioning, type ProvisioningMock } from './support/hqProvisioningApi';

// Organisation gaps and governed agent provisioning (docs/ANUM_MINE_AGENT_PROVISIONING.md). Everything
// shown comes from the mocked provisioning API; the tests check that nothing is created without a click on
// the review dialog, that the dialog says exactly what will be created, and that every state says what to do.
const registry = hqRegistry(TENANT_A, { rooms: 4, desks: 4, agentIds: ['agent_fleet', 'agent_leads', 'agent_care', 'agent_invoice'] });
let prov: ProvisioningMock;

async function open(page: Page, options: Parameters<typeof openHq>[1] = {}) {
  await openHq(page, { registry, ...options, beforeGoto: async (p) => { await mockFlow(p); prov = await mockProvisioning(p); } });
  await expect(page.getByRole('button', { name: /Organisation gaps|فجوات المؤسسة/ })).toBeVisible();
}
const openPanel = async (page: Page) => { await page.getByRole('button', { name: /Organisation gaps|فجوات المؤسسة/ }).click(); };
const dialog = (page: Page) => page.locator('.hqProvDialog[role=dialog]');

test('gaps come from the API, show their evidence and create nothing', async ({ page }) => {
  await open(page);
  await expect(page.getByRole('button', { name: 'Organisation gaps (2)' })).toBeVisible();
  await openPanel(page);
  const panel = page.getByRole('region', { name: 'Work and departments nobody handles yet' });
  const list = panel.getByRole('list', { name: 'Gaps' });
  await expect(list.locator(':scope > li')).toHaveCount(2);
  await expect(list).toContainText('Open orders with no agent or office: 3. Overdue to ship: 1.');
  await expect(list).toContainText('No active agent works in the finance department, but these offices are linked to it: Alpha Room 3.');
  await expect(panel).toContainText('Agents in this workspace: 4 of 200');
  await expect(panel.getByText('Not connected', { exact: true })).toBeVisible();
  expect(prov.requests.every((r) => r.method === 'GET')).toBe(true);
});

test('the review dialog lists exactly what will be created, with tools present and missing', async ({ page }) => {
  await open(page);
  await openPanel(page);
  await page.getByRole('button', { name: 'Review the proposal for Orders Desk' }).click();
  const d = dialog(page);
  await expect(d).toBeVisible();
  await expect(d).toContainText('One agent: Orders Desk, department operations');
  await expect(d).toContainText('A monthly cost limit of 5 USD');
  await expect(d).toContainText('Created paused: it does not chat or run tasks until an owner turns it on.');
  await expect(d.getByRole('list', { name: 'Tools it gets' })).toContainText('commerce.list_orders');
  await expect(d.getByRole('list', { name: 'Tools it gets' })).toContainText('Missing: not in this deployment');
  await expect(d.getByRole('list', { name: 'Missing capabilities' })).toContainText('A carrier tracking lookup for shipping follow-up');
  await expect(d).toContainText('Limited');
  await expect(d).toContainText('It takes the vacant desk Orders Desk in Alpha Room 1.');
  await expect(d).toContainText('Open orders show in Alpha Room 1 under this agent.');
  await expect(d).toContainText('Only a click on this button confirms. A voice command or a typed message cannot.');
  expect(prov.requests.some((r) => r.method === 'POST')).toBe(false);
});

test('confirm sends the shown hash with input mode click, then offers to turn the paused agent on', async ({ page }) => {
  await open(page);
  await openPanel(page);
  await page.getByRole('button', { name: 'Review the proposal for Orders Desk' }).click();
  await dialog(page).getByRole('button', { name: 'Create paused agent' }).click();
  await expect(dialog(page)).toContainText('Created Orders Desk');
  await expect(dialog(page)).toContainText('It is paused. Check it, then turn it on.');
  await expect(dialog(page)).toContainText('It is marked limited: 2 capabilities are missing.');
  const post = prov.requests.find((r) => r.method === 'POST')!;
  expect(post.path).toBe('/proposals/orders_desk/confirm');
  expect(post.body).toEqual({ payload_hash: 'ab'.repeat(32), input_mode: 'click', lang: 'en' });
  await dialog(page).getByRole('button', { name: 'Turn it on' }).click();
  await expect(dialog(page)).toContainText('It is on.');
  expect(prov.requests.find((r) => r.path === '/agents/agent_new')!.body).toEqual({ status: 'active', version: 1 });
  await dialog(page).getByRole('button', { name: 'Close' }).last().click();
  await expect(page.getByRole('list', { name: 'Gaps' }).locator(':scope > li')).toHaveCount(1); // the orders gap is gone
});

test('a stale proposal is refused and must be reviewed again; a refusal says why', async ({ page }) => {
  await open(page);
  await openPanel(page);
  await page.getByRole('button', { name: 'Review the proposal for Orders Desk' }).click();
  prov.setConfirmFailure(409, 'What would be created has changed since you opened it. Review the proposal again.');
  await dialog(page).getByRole('button', { name: 'Create paused agent' }).click();
  await expect(dialog(page).getByRole('alert')).toContainText('The agent was not created. What would be created has changed');
  await expect(dialog(page).getByRole('button', { name: 'Review again' })).toBeVisible();
  prov.setConfirmFailure(403, 'Confirm this on screen with a click.');
  await dialog(page).getByRole('button', { name: 'Create paused agent' }).click();
  await expect(dialog(page).getByRole('alert')).toContainText('The agent was not created. Confirm this on screen');
});

test('a person who is not an owner can read the proposal but cannot confirm', async ({ page }) => {
  await open(page);
  prov.setCanConfirm(false);
  await openPanel(page);
  await page.getByRole('button', { name: 'Review the proposal for Orders Desk' }).click();
  await expect(dialog(page).getByRole('button', { name: 'Create paused agent' })).toBeDisabled();
  await expect(dialog(page)).toContainText('Only a workspace owner can confirm. You can read the proposal.');
});

test('with no desk available the dialog says so and lets the owner pick another room', async ({ page }) => {
  await open(page);
  await openPanel(page);
  prov.setPlacement('none');
  await page.getByRole('button', { name: 'Review the proposal for Orders Desk' }).click();
  await expect(dialog(page)).toContainText('No office is linked to this department.');
  await expect(dialog(page)).toContainText('Without a desk, this agent holds no work in any office.');
  const select = dialog(page).getByLabel('Office room');
  await expect(select.locator('option', { hasText: 'Alpha Room 4' })).toBeDisabled();
  prov.setPlacement('existing_desk');
  await select.selectOption('Alpha-room-0');
  await expect(dialog(page)).toContainText('It takes the vacant desk');
  expect(prov.requests.some((r) => r.path.includes('room_id=Alpha-room-0'))).toBe(true);
});

test('keyboard: Escape closes the dialog and focus returns to the button that opened it', async ({ page }) => {
  await open(page);
  await openPanel(page);
  const opener = page.getByRole('button', { name: 'Review the proposal for Orders Desk' });
  await opener.focus();
  await page.keyboard.press('Enter');
  await expect(dialog(page)).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog(page)).toBeHidden();
  await expect(opener).toBeFocused();
});

test('empty and denied states say what to do', async ({ page }) => {
  await open(page);
  prov.setGapsEmpty(true);
  await page.reload();
  await expect(page.getByRole('button', { name: 'Organisation gaps' })).toBeVisible();
  await openPanel(page);
  await expect(page.getByText('No gaps found.')).toBeVisible();
  await expect(page.getByText('Other roles you can add')).toBeVisible();
  await page.getByRole('button', { name: 'Close office panel' }).click();
  prov.setGapsFailure(403);
  await page.reload();
  await openPanel(page);
  await expect(page.getByRole('alert').filter({ hasText: 'You cannot read the organisation' })).toBeVisible();
});

test('error shows the detail and a retry that works', async ({ page }) => {
  await open(page);
  prov.setGapsFailure(500);
  await page.reload();
  await openPanel(page);
  await expect(page.getByRole('alert').filter({ hasText: 'The gaps could not be loaded.' })).toBeVisible();
  prov.setGapsFailure(null);
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.getByRole('list', { name: 'Gaps' })).toBeVisible();
});

test('Arabic: right to left, the Arabic role name is created and the dialog still fits', async ({ page }) => {
  await open(page, { locale: 'ar' });
  await openPanel(page);
  await page.getByRole('button', { name: /مراجعة مقترح الدور مكتب الطلبات/ }).click();
  const d = dialog(page);
  await expect(d).toContainText('وكيل واحد: مكتب الطلبات');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await d.getByRole('button', { name: 'إنشاء الوكيل متوقفا مؤقتا' }).click();
  expect(prov.requests.find((r) => r.method === 'POST')!.body).toMatchObject({ lang: 'ar', input_mode: 'click' });
  await expect(d).toContainText('تم إنشاء مكتب الطلبات');
});

const sizes = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const [width, height] of sizes) {
  test(`fits ${width}x${height}: panel and dialog without horizontal overflow or clipped controls`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await open(page);
    await openPanel(page);
    await expect(page.getByRole('list', { name: 'Gaps' })).toBeVisible();
    const dir = process.env.HQ_PROV_SHOTS;
    if (dir) await page.screenshot({ path: `${dir}/panel-${width}x${height}.png` });
    await page.getByRole('button', { name: 'Review the proposal for Orders Desk' }).click();
    const d = dialog(page);
    await expect(d).toBeVisible();
    const issues = await page.evaluate(() => {
      const doc = document.documentElement;
      const box = document.querySelector('.hqProvDialog')!.getBoundingClientRect();
      const bad: string[] = [];
      if (doc.scrollWidth > window.innerWidth) bad.push('page overflows horizontally');
      if (box.right > window.innerWidth + 1 || box.left < -1) bad.push('dialog outside the window');
      if (box.bottom > window.innerHeight + 1 || box.top < -1) bad.push('dialog outside vertically');
      const body = document.querySelector('.hqProvDlgBody') as HTMLElement;
      if (body.scrollWidth > body.clientWidth + 1) bad.push('dialog body scrolls horizontally');
      return bad;
    });
    expect(issues).toEqual([]);
    await expect(d.getByRole('button', { name: 'Create paused agent' })).toBeInViewport();
    if (dir) await page.screenshot({ path: `${dir}/dialog-${width}x${height}.png` });
  });
}
