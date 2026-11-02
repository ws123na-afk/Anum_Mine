import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockGreenConcreteKit, type KitMockOptions } from './support/greenConcreteKitApi';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Green Concrete agents kit (docs/ANUM_MINE_GREEN_CONCRETE_AGENTS.md): in the Green Concrete office drawer an owner sees
// the role skills and the data each needs, the gaps, the shared knowledge, the routines and each agent's memory; sets up
// the collection and a routine (created off) with a click, turns the routine on, and adds, edits and deletes a memory.
const SIZES = [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 }];
const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);
const serious = async (page: Page, include: string) => (await new AxeBuilder({ page }).include(include).analyze()).violations
  .filter((v) => v.impact === 'critical' || v.impact === 'serious').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);

async function openGreenConcrete(page: Page, options: KitMockOptions = {}, locale: 'en' | 'ar' = 'en') {
  await mockWorkspaceApi(page);
  if (locale === 'ar') await useSavedLocale(page, 'ar');
  const mock = await mockGreenConcreteKit(page, options);
  await page.goto('/#executive');
  await page.getByRole('tab', { name: locale === 'ar' ? 'المكاتب والوكلاء' : 'Offices and agents' }).click();
  await page.locator('.osCard').filter({ hasText: 'Green Concrete' }).getByRole('button', { name: locale === 'ar' ? /فتح/ : 'Open Green Concrete' }).click();
  const drawer = page.getByRole('dialog', { name: 'Green Concrete' });
  await expect(drawer).toBeVisible();
  return { mock, drawer };
}

test('an owner sees skills, data, gaps and routines, and sets up the kit with a click', async ({ page }) => {
  const { mock, drawer } = await openGreenConcrete(page);
  const kit = drawer.locator('.gcKit');
  await expect(kit.getByRole('heading', { name: 'Skills (1 of 2 have their data)' })).toBeVisible();
  await expect(kit.locator('[data-skill="gc.daily-ops-brief"]')).toContainText('Data connected');
  const tyre = kit.locator('[data-skill="gc.tyre-plan"]');
  await expect(tyre).toContainText('Not connected');
  await expect(tyre).toContainText('Tyre Pulse fleet sync (Not connected)');
  await expect(tyre).toContainText('read only, low risk');
  await tyre.locator('summary').click();
  await expect(tyre).toContainText('A purchase is a proposal.');
  await expect(kit.locator('[data-gap="fuel"]')).toContainText('Fuel use and fuel cost');
  await expect(kit.locator('[data-routine="daily_ops_brief"]')).toContainText('Every day at 06:00 (Asia/Dubai)');
  await expect(kit.locator('[data-routine="daily_ops_brief"]')).toContainText('This agent is paused');
  expect(mock.setups).toHaveLength(0);
  await kit.getByRole('checkbox', { name: 'Create the Green Concrete knowledge collection' }).check();
  await kit.getByRole('checkbox', { name: 'Add this routine (turned off)' }).check();
  await kit.getByRole('button', { name: 'Review 2 steps' }).click();
  const confirm = page.getByRole('alertdialog', { name: 'Set up the Green Concrete agents kit' });
  await expect(confirm).toContainText('Routines are created turned off');
  expect(mock.setups).toHaveLength(0);
  await confirm.getByRole('button', { name: 'Set up' }).click();
  await expect(kit.getByRole('status').filter({ hasText: '2 steps done.' })).toBeVisible();
  expect(mock.setups).toEqual([{ input_mode: 'click', items: [{ id: 'knowledge', hash: 'd'.repeat(64) }, { id: 'routine:daily_ops_brief', hash: 'e'.repeat(64) }] }]);
  await expect(kit).toContainText('is ready but holds nothing yet');
  await expect(kit).toContainText('Everything in the kit is set up.');
  await kit.getByRole('button', { name: 'Turn on' }).click();
  await page.getByRole('alertdialog', { name: 'Turn on Green Concrete: daily operations brief?' }).getByRole('button', { name: 'Turn on' }).click();
  await expect(kit.locator('[data-routine="daily_ops_brief"]')).toContainText('On');
  expect(mock.switches).toEqual([{ input_mode: 'click', enabled: true }]);
  expect(await serious(page, '[role="dialog"]')).toEqual([]);
});

test('an owner views, adds, edits and deletes an agent memory with its kind, source and date', async ({ page }) => {
  const { mock, drawer } = await openGreenConcrete(page);
  const agent = drawer.locator('.gcKit [data-agent="agent_shahzeb"]');
  await expect(agent).toContainText('1 memory');
  await agent.getByRole('button', { name: 'Show memory' }).click();
  await expect(agent).toContainText('Rotate steer tyres at 6 mm.');
  await expect(agent).toContainText('Tyre SOP, version 3');
  await expect(agent).toContainText('kept until deleted');
  await agent.getByRole('button', { name: 'Save memory' }).isDisabled();
  await agent.getByLabel('Add a memory').fill('Tyres are bought from the approved supplier list only.');
  await agent.getByLabel('Kind').first().selectOption('decision');
  await agent.getByLabel('Where it comes from').first().fill('Owner, weekly meeting');
  await agent.getByRole('button', { name: 'Save memory' }).click();
  await expect(agent).toContainText('Owner, weekly meeting');
  expect(mock.memoryWrites[0]).toMatchObject({ method: 'POST', content: 'Tyres are bought from the approved supplier list only.', kind: 'decision', source_label: 'Owner, weekly meeting' });
  await agent.getByRole('button', { name: 'Edit memory: Rotate steer tyres at 6 mm.' }).click();
  await agent.getByRole('textbox', { name: 'Memory', exact: true }).fill('Rotate steer tyres at 5 mm.');
  await agent.getByRole('button', { name: 'Save changes' }).click();
  await expect(agent).toContainText('Rotate steer tyres at 5 mm.');
  expect(mock.memoryWrites[1]).toEqual({ method: 'PATCH', id: 'mem_1', content: 'Rotate steer tyres at 5 mm.' });
  await agent.getByRole('button', { name: 'Delete memory: Rotate steer tyres at 5 mm.' }).click();
  await page.getByRole('alertdialog', { name: 'Delete this memory?' }).getByRole('button', { name: 'Delete' }).click();
  await expect(agent).not.toContainText('Rotate steer tyres at 5 mm.');
  await agent.getByLabel('Show', { exact: true }).selectOption('fact');
  await expect(agent).toContainText('No memories match.');
  expect(await serious(page, '[role="dialog"]')).toEqual([]);
});

test('a viewer reads the kit but cannot set it up or change memories', async ({ page }) => {
  const { mock, drawer } = await openGreenConcrete(page, { role: 'viewer' });
  const kit = drawer.locator('.gcKit');
  await expect(kit).toContainText('Only a workspace owner can set up the knowledge collection');
  await expect(kit.getByRole('checkbox')).toHaveCount(0);
  await expect(kit.getByRole('button', { name: 'Turn on' })).toHaveCount(0);
  await kit.getByRole('button', { name: 'Show memory' }).click();
  await expect(kit).toContainText('Only the agent\'s creator, or a workspace owner, can add or delete its memories.');
  await expect(kit.getByRole('button', { name: /Edit memory/ })).toHaveCount(0);
  expect(mock.setups).toHaveLength(0);
});

test('no access says who can help', async ({ page }) => {
  const { drawer } = await openGreenConcrete(page, { kit: 'denied' });
  await expect(drawer.locator('.gcKit')).toContainText('You cannot read the agents kit of this workspace.');
});

test('a server without PostgreSQL says so', async ({ page }) => {
  const { drawer } = await openGreenConcrete(page, { kit: 'missing' });
  await expect(drawer.locator('.gcKit')).toContainText('The agents kit needs the PostgreSQL server');
});

test('an error says what failed and Try again reloads', async ({ page }) => {
  const { drawer } = await openGreenConcrete(page, { kit: 'error' });
  const kit = drawer.locator('.gcKit');
  await expect(kit.getByRole('alert')).toContainText('The agents kit could not be loaded.');
  await kit.getByRole('button', { name: 'Try again' }).click();
  await expect(kit.getByRole('heading', { name: 'Skills (1 of 2 have their data)' })).toBeVisible();
});

test('Arabic, right to left', async ({ page }) => {
  const { drawer } = await openGreenConcrete(page, {}, 'ar');
  const kit = drawer.locator('.gcKit');
  await expect(kit.getByRole('heading', { name: 'عدة الوكلاء: المهارات والأدوات والذاكرة والمهام الدورية' })).toBeVisible();
  await expect(kit.locator('[data-skill="gc.tyre-plan"]')).toContainText('تخطيط الإطارات');
  await expect(kit.locator('[data-skill="gc.tyre-plan"]')).toContainText('غير متصل');
  await expect(kit.locator('[data-routine="daily_ops_brief"]')).toContainText('كل يوم الساعة 06:00');
  expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
  expect(await serious(page, '[role="dialog"]')).toEqual([]);
});

for (const size of SIZES) {
  test(`fits ${size.width}x${size.height} without horizontal scroll`, async ({ page }) => {
    await page.setViewportSize(size);
    const { drawer } = await openGreenConcrete(page);
    const kit = drawer.locator('.gcKit');
    await expect(kit.getByRole('heading', { name: 'Skills (1 of 2 have their data)' })).toBeVisible();
    await kit.locator('[data-agent="agent_shahzeb"]').getByRole('button', { name: 'Show memory' }).click();
    await expect(kit).toContainText('Rotate steer tyres at 6 mm.');
    expect(await noOverflow(page)).toBe(true);
    const box = await drawer.boundingBox();
    expect(box && box.x >= -1 && box.x + box.width <= size.width + 1).toBe(true);
    if (process.env.HQ_SHOTS_DIR) await page.screenshot({ path: `${process.env.HQ_SHOTS_DIR}/gc-kit-${size.width}x${size.height}.png`, fullPage: false });
  });
}
