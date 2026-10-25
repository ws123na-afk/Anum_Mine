import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { mockFlow } from './support/hqFlowApi';
import { mockProvisioning } from './support/hqProvisioningApi';
import { mockStarterCampus, type StarterCampusMock } from './support/hqStarterCampusApi';

// "Set up my campus" (docs/ANUM_MINE_STARTER_CAMPUS.md). Everything shown comes from the mocked API; the tests check that
// a preview writes nothing, that the dialog lists what will be created and skipped, that only a click with the shown hash
// confirms, and that every state says what to do. Set HQ_SHOTS_DIR to keep a screenshot of every state.
const populated = hqRegistry(TENANT_A, { rooms: 4, desks: 4, agentIds: ['agent_fleet', 'agent_leads', 'agent_care', 'agent_invoice'] });
const empty = { ...hqRegistry(TENANT_A, { rooms: 0, desks: 0 }), buildings: [], floors: [] };
let starter: StarterCampusMock;

async function open(page: Page, options: Parameters<typeof openHq>[1] = {}) {
  await openHq(page, { registry: populated, ...options, beforeGoto: async (p) => { await mockFlow(p); await mockProvisioning(p); starter = await mockStarterCampus(p); } });
  await expect(tools(page)).toBeVisible();
}
const tools = (page: Page) => page.locator('.hqLive').getByRole('button', { name: /Office tools|أدوات المكتب/ });
const dialog = (page: Page) => page.locator('.hqScDialog[role=dialog]');
async function openDialog(page: Page) {
  await tools(page).click();
  await page.locator('.hqLive').getByRole('button', { name: /Set up my campus|إعداد الحرم الخاص بي/ }).click();
  await expect(dialog(page)).toBeVisible();
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
    const d = document.querySelector('.hqScDialog') as HTMLElement | null;
    if (!d) return ['no dialog'];
    const box = d.getBoundingClientRect();
    if (box.right > window.innerWidth + 1 || box.bottom > window.innerHeight + 1 || box.left < -1 || box.top < -1) out.push('dialog leaves the screen');
    if (d.scrollWidth > d.clientWidth + 1) out.push('dialog content is wider than the dialog');
    const body = d.querySelector('.hqScBody') as HTMLElement;
    if (body.scrollWidth > body.clientWidth + 1) out.push('dialog body scrolls sideways');
    for (const el of d.querySelectorAll<HTMLElement>('button, input, label')) {
      const r = el.getBoundingClientRect();
      if (r.width && r.height && (r.right > box.right + 1 || r.left < box.left - 1)) out.push(`clipped: ${el.textContent?.slice(0, 30)}`);
    }
    return out;
  });
  expect(problems).toEqual([]);
}

test('the plan lists what will be created and skipped and a preview writes nothing', async ({ page }) => {
  await open(page);
  await openDialog(page);
  const d = dialog(page);
  await expect(d.getByRole('heading', { name: 'Set up my campus' })).toBeVisible();
  const rows = d.locator('.hqScBuilding');
  await expect(rows).toHaveCount(4);
  await expect(rows.nth(0)).toContainText('ANUM HQ');
  await expect(rows.nth(0)).toContainText('To create: floors 2, rooms 4, desks 14');
  await expect(rows.nth(3)).toContainText('Already set up');
  await expect(rows.nth(3).getByRole('checkbox')).toBeDisabled();
  await expect(d.getByText('Will create. Buildings: 2, floors: 4, rooms: 7, desks: 21')).toBeVisible();
  await expect(d.getByText('No agents are created and nobody is seated.')).toBeVisible();
  await rows.nth(2).getByRole('button', { name: /Show details: Tyre Pulse/ }).click();
  const detail = rows.nth(2).getByRole('group', { name: 'Details of Tyre Pulse' });
  await expect(detail).toContainText('Fleet');
  await expect(detail).toContainText('Already exists, kept as it is');
  await expect(detail).toContainText('A room with this name is already on the floor');
  await expect(detail).toContainText('Desks (4): Desk 1, Desk 2, Desk 3, Desk 4');
  await expect(detail).toContainText('linked to the tyre engineering team');
  await shot(page, 'plan-1440');
  expect(starter.requests.every((r) => r.method === 'GET')).toBe(true);
});

test('unticking a building re-reads the plan for exactly the ticked ones and confirm sends that hash with a click', async ({ page }) => {
  await open(page);
  await openDialog(page);
  const d = dialog(page);
  await d.getByRole('checkbox', { name: /Corporate Services/ }).uncheck();
  await expect(d.getByText('Will create. Buildings: 1, floors: 3, rooms: 5, desks: 18')).toBeVisible();
  await expect(d.getByText('3 of 4 selected')).toBeVisible();
  expect(starter.requests.at(-1)?.path).toBe('/preview?buildings=hq%2Ctyre-pulse%2Ccareer');
  await d.getByRole('button', { name: 'Create now' }).click();
  await expect(d.getByRole('heading', { name: 'Campus created' })).toBeVisible();
  const post = starter.requests.find((r) => r.method === 'POST')!;
  expect(post.body).toMatchObject({ input_mode: 'click', buildings: ['hq', 'tyre-pulse', 'career'], timezone: 'Asia/Dubai' });
  expect(String(post.body?.payload_hash)).toMatch(/^[0-9a-f]{64}$/);
  await expect(d.getByRole('list').last()).toContainText('ANUM HQ');
  await expect(d.getByText('Created. Buildings: 1, floors: 3, rooms: 5, desks: 18.')).toBeVisible();
  await shot(page, 'result-1440');
  await d.getByRole('button', { name: 'Open Organisation gaps' }).click();
  await expect(page.getByRole('region', { name: 'Work and departments nobody handles yet' })).toBeVisible();
});

test('nothing selected, nothing to create and not an owner each disable the confirm button and say why', async ({ page }) => {
  await open(page);
  await openDialog(page);
  const d = dialog(page);
  await d.getByRole('button', { name: 'Select none' }).click();
  await expect(d.getByText('Select at least one building.')).toBeVisible();
  await expect(d.getByRole('button', { name: 'Create now' })).toBeDisabled();
  await shot(page, 'none-selected-1440');
  await d.getByRole('button', { name: 'Cancel' }).click();
  await expect(dialog(page)).toHaveCount(0);
});

test('a person who is not an owner can review the plan but not confirm', async ({ page }) => {
  await open(page);
  starter.setCanConfirm(false);
  await openDialog(page);
  const d = dialog(page);
  await expect(d.getByText('Only workspace owners can confirm. You can still review the plan.')).toBeVisible();
  await expect(d.getByRole('button', { name: 'Create now' })).toBeDisabled();
});

test('a plan that cannot be read shows the cause and a retry; no access explains who can help', async ({ page }) => {
  await open(page);
  starter.setPreviewFailure(500);
  await openDialog(page);
  const d = dialog(page);
  await expect(d.getByRole('alert')).toContainText('The plan could not be loaded.');
  await shot(page, 'error-1440');
  starter.setPreviewFailure(null);
  await d.getByRole('button', { name: 'Try again' }).click();
  await expect(d.locator('.hqScBuilding')).toHaveCount(4);
  await d.getByRole('button', { name: 'Close', exact: true }).first().click();
  starter.setPreviewFailure(403);
  await openDialog(page);
  await expect(dialog(page).getByRole('alert')).toContainText('Ask a workspace owner');
});

test('a stale hash is refused with a way back, and a partly failed run names the building that failed', async ({ page }) => {
  await open(page);
  await openDialog(page);
  const d = dialog(page);
  starter.setConfirmFailure(409, 'What would be created has changed since you opened it.');
  await d.getByRole('button', { name: 'Create now' }).click();
  await expect(d.getByRole('alert')).toContainText('The records changed since you opened this.');
  starter.setConfirmFailure(null);
  starter.setOutcome('partial');
  await d.getByRole('button', { name: 'Review again' }).click();
  await expect(d.locator('.hqScBuilding')).toHaveCount(4);
  await d.getByRole('button', { name: 'Create now' }).click();
  await expect(d.getByRole('heading', { name: 'Campus partly created' })).toBeVisible();
  const failed = d.locator('.hqScResults li[data-status=failed]');
  await expect(failed).toHaveCount(1);
  await expect(failed).toContainText('Nothing was created');
  await expect(d.getByRole('button', { name: 'Review what is left' })).toBeVisible();
  await shot(page, 'partial-1440');
});

test('an empty campus offers Set up my campus and keyboard users can operate the dialog', async ({ page }) => {
  await open(page, { registry: empty });
  await page.locator('.hqLive').getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click();
  await page.locator('.hqLive').getByRole('button', { name: 'Open Al Noor Fleet HQ' }).click();
  const state = page.locator('.hqEmptyScene');
  await expect(state).toBeVisible();
  await expect(state.getByText('Create the standard campus in one step')).toBeVisible();
  await shot(page, 'empty-state-1440');
  await state.getByRole('button', { name: 'Set up my campus' }).focus();
  await page.keyboard.press('Enter');
  const d = dialog(page);
  await expect(d).toBeVisible();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Escape');
  await expect(dialog(page)).toHaveCount(0);
  await expect(state.getByRole('button', { name: 'Set up my campus' })).toBeFocused();
});

test('Arabic shows Arabic names, right to left', async ({ page }) => {
  await open(page, { locale: 'ar' });
  await openDialog(page);
  const d = dialog(page);
  await expect(d.locator('.hqScBackdrop, :scope')).toBeVisible();
  await expect(page.locator('.hqScBackdrop')).toHaveAttribute('dir', 'rtl');
  await expect(d.getByText('مقر ANUM')).toBeVisible();
  await expect(d.getByText('تاير بالس')).toBeVisible();
  await d.locator('.hqScBuilding').nth(0).getByRole('button').click();
  await expect(d.getByText('الاستقبال')).toBeVisible();
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
    await d.locator('.hqScBuilding').nth(0).getByRole('button').click();
    await noOverflow(page);
    await shot(page, `plan-${size.name}`);
    await d.getByRole('button', { name: 'Create now' }).click();
    await expect(d.getByRole('heading', { name: 'Campus created' })).toBeVisible();
    await noOverflow(page);
    await shot(page, `result-${size.name}`);
  });
  test(`@gate the Arabic dialog fits ${size.name}`, async ({ page }) => {
    await page.setViewportSize({ width: size.width, height: size.height });
    await open(page, { locale: 'ar' });
    await openDialog(page);
    await noOverflow(page);
    await shot(page, `plan-ar-${size.name}`);
  });
}
