import { expect, test, type Page } from '@playwright/test';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { mockFlow } from './support/hqFlowApi';
import { agentAvatars as teamAvatars } from './support/orgChartApi';

// Avatar styles for the people of the headquarters (docs/ANUM_MINE_HQ_REALISM_ASSETS.md, "Avatar styles").
// Six real assigned agents sit in one office; the scene draws a different style for each, the owner changes one
// from the Team panel, and the scene, the list chip and the dataset follow. Nothing here is read from a name.
const AGENTS = ['agent_leads', 'agent_outreach', 'agent_fleet', 'agent_maint', 'agent_care', 'agent_invoice'];
const registry = hqRegistry(TENANT_A, { rooms: 1, desks: 6, agentIds: AGENTS });
const stamp = '2026-10-25T08:00:00Z';

/** One stateful agent behind GET and PATCH, so the agent page keeps what the owner saves (the shared mocks answer a canned agent). */
async function mockLeadFinder(page: Page): Promise<void> {
  let agent: Record<string, unknown> = {
    id: 'agent_leads', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', owner_user_id: 'user_local', status: 'active', created_at: stamp, updated_at: stamp, version: 1,
    can_edit: true, can_run: true, unavailable_tools: [], unavailable_skills: [], name: 'Lead Finder', icon: 'search', color: '#2563eb', category: 'work', department: 'Sales & Outreach',
    description: 'Finds fleet operators.', instructions: '', model_route: 'capable', allowed_skills: [], allowed_tools: [], autonomy: { read: 'auto', write: 'ask', external: 'ask' },
    knowledge_mode: 'none', knowledge_sources: [], sharing: 'workspace', template_id: null, avatar: null,
  };
  await page.route(/\/api\/v1\/agents\/agent_leads$/, (route) => {
    const request = route.request();
    if (request.method() === 'PATCH') {
      const body = request.postDataJSON() as Record<string, unknown>;
      agent = { ...agent, avatar: body.avatar ?? null, version: Number(agent.version) + 1 };
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(agent) });
  });
}
const hq = (page: Page) => page.locator('.hqLive');
const canvas = (page: Page) => page.locator('canvas.hqCanvas');

async function openOffice(page: Page, options: Parameters<typeof openHq>[1] = {}) {
  await openHq(page, { registry, ...options, beforeGoto: async (p) => { await mockFlow(p); await options.beforeGoto?.(p); } });
  await hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click();
  await hq(page).getByRole('button', { name: 'Open Al Noor Fleet HQ' }).click();
  await expect(hq(page).getByRole('heading', { level: 2, name: 'Al Noor Fleet HQ' })).toBeVisible();
}
const looks = async (page: Page): Promise<string[]> => ((await canvas(page).getAttribute('data-figure-looks')) ?? '').split(' ').filter(Boolean);

test('six assigned agents are drawn with six different styles, deterministic from their ids', async ({ page }) => {
  await openOffice(page);
  await expect.poll(async () => (await looks(page)).length, { timeout: 60_000 }).toBe(6);
  const codes = await looks(page);
  expect(new Set(codes).size).toBe(6);
  // No look chosen: every build is neutral (owner rule); the six still differ in skin, hair and outfit.
  expect(codes.map((c) => c.split('/')[0])).toEqual(Array(6).fill('neutral'));
  await expect(canvas(page)).toHaveAttribute('data-figure-styles', 'feminine:0,masculine:0,neutral:6');
  await expect(canvas(page)).toHaveAttribute('data-model-figures', '6');
  // Reloading shows the same styles: nothing random.
  await page.reload();
  await hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click();
  await hq(page).getByRole('button', { name: 'Open Al Noor Fleet HQ' }).click();
  await expect.poll(async () => (await looks(page)).sort().join(' '), { timeout: 60_000 }).toBe([...codes].sort().join(' '));
});

test('the owner picks an avatar style in the Team panel and the scene, chip and dataset follow', async ({ page }) => {
  for (const key of Object.keys(teamAvatars)) delete teamAvatars[key];
  await openOffice(page, { beforeGoto: async (p) => {
    // The roster reports the style the server saved, like the real one does when it is read again.
    await p.route(/\/api\/v1\/agents\/([^/]+)$/, (route) => {
      const request = route.request();
      if (request.method() !== 'PATCH') return route.fallback();
      const id = new URL(request.url()).pathname.split('/').pop()!;
      const avatar = request.postDataJSON().avatar;
      teamAvatars[id] = avatar;
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        id, tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', owner_user_id: 'user_local', status: 'active', created_at: stamp, updated_at: stamp, version: 2,
        can_edit: true, can_run: true, unavailable_tools: [], unavailable_skills: [], name: 'Fleet Planner', icon: 'truck', color: '#0f766e', category: 'work', department: 'Operations and warehouse',
        description: '', instructions: '', model_route: 'capable', allowed_skills: [], allowed_tools: [], autonomy: { read: 'auto', write: 'ask', external: 'ask' },
        knowledge_mode: 'none', knowledge_sources: [], sharing: 'workspace', template_id: null, avatar,
      }) });
    });
  } });
  await expect.poll(async () => (await looks(page)).length, { timeout: 60_000 }).toBe(6);
  const before = await looks(page);
  await hq(page).getByRole('button', { name: 'Office tools' }).click();
  await hq(page).getByRole('button', { name: 'Team', exact: true }).click();
  const chip = hq(page).locator('.hqTeam [data-avatar-chip="agent_fleet"]');
  await expect(chip).toBeVisible();
  const chipBefore = await chip.evaluate((el) => getComputedStyle(el).backgroundColor);
  await hq(page).getByRole('button', { name: 'Avatar style for Fleet Planner' }).click();
  const dialog = page.getByRole('dialog', { name: 'Avatar style: Fleet Planner' });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('svg[data-look]')).toBeVisible();
  // Keyboard: the radios are real inputs.
  await dialog.getByRole('radio', { name: 'Masculine build' }).check();
  await dialog.getByRole('radio', { name: 'Skin tone 6' }).check();
  await dialog.getByRole('radio', { name: 'Short, parted' }).check();
  await dialog.getByRole('radio', { name: 'Crimson' }).check();
  await dialog.getByRole('radio', { name: 'Uniform or workwear' }).check();
  const request = page.waitForRequest((r) => r.method() === 'PATCH' && /\/api\/v1\/agents\/agent_fleet$/.test(r.url()));
  await dialog.getByRole('button', { name: 'Save avatar style' }).click();
  expect((await request).postDataJSON()).toEqual({ avatar: { avatar_style: 'masculine', skin_tone: 'tone_6', hair_style: 'parted', outfit: 'uniform', accent: 'crimson' } });
  await expect(dialog.getByText('Avatar style saved.')).toBeVisible();
  await dialog.getByRole('button', { name: 'Close' }).click();
  await expect(chip).toHaveCSS('background-color', 'rgb(169, 42, 61)');
  expect(chipBefore).not.toBe('rgb(169, 42, 61)');
  // The scene redraws that one figure with the chosen style; the other five keep theirs.
  await expect.poll(async () => (await looks(page)).filter((c) => c === 'masculine/parted/uniform/tone_6').length, { timeout: 30_000 }).toBe(1);
  const after = await looks(page);
  expect(after.filter((c) => before.includes(c)).length).toBeGreaterThanOrEqual(5);
});

test('a style that cannot be saved says what failed and keeps the dialog open', async ({ page }) => {
  await openOffice(page, { beforeGoto: async (p) => {
    await p.route(/\/api\/v1\/agents\/agent_fleet$/, (route) => route.request().method() === 'PATCH'
      ? route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ detail: 'Only the creator can change this agent.' }) }) : route.fallback());
  } });
  await hq(page).getByRole('button', { name: 'Office tools' }).click();
  await hq(page).getByRole('button', { name: 'Team', exact: true }).click();
  await hq(page).getByRole('button', { name: 'Avatar style for Fleet Planner' }).click();
  const dialog = page.getByRole('dialog', { name: 'Avatar style: Fleet Planner' });
  await dialog.getByRole('radio', { name: 'Neutral build' }).check();
  await dialog.getByRole('button', { name: 'Save avatar style' }).click();
  await expect(dialog.getByRole('alert')).toContainText('Only the creator can change this agent.');
  await expect(dialog).toBeVisible();
});

test('the agent page has the same editor, and an owner can go back to the automatic style', async ({ page }) => {
  await openHq(page, { registry, beforeGoto: async (p) => { await mockLeadFinder(p); await p.addInitScript(() => { try { localStorage.setItem('anum.agents.tab', 'agents'); } catch { /* storage blocked */ } }); } });
  await page.goto('/#agents');
  await page.locator('.agentOpen', { hasText: 'Lead Finder' }).click();
  await page.locator('.agentTabs').getByRole('button', { name: 'Settings', exact: true }).click();
  const editor = page.locator('[data-avatar-editor="agent_leads"]');
  await expect(editor).toBeVisible();
  await editor.getByRole('radio', { name: 'Feminine build' }).check();
  await editor.getByRole('radio', { name: 'Long' }).check();
  await editor.getByRole('button', { name: 'Save avatar style' }).click();
  await expect(editor.getByText('Avatar style saved.')).toBeVisible();
  await editor.getByRole('button', { name: 'Use automatic style' }).click();
  await expect(editor.getByRole('radio', { name: 'Automatic' }).first()).toBeChecked();
  // A build that does not offer a hair shape drops it.
  await editor.getByRole('radio', { name: 'Feminine build' }).check();
  await editor.getByRole('radio', { name: 'Long' }).check();
  await editor.getByRole('radio', { name: 'Masculine build' }).check();
  await expect(editor.getByRole('radio', { name: 'Long' })).toHaveCount(0);
});

test('Arabic: the editor is right to left and fully translated', async ({ page }) => {
  await openHq(page, { registry, locale: 'ar', beforeGoto: async (p) => { await mockLeadFinder(p); await p.addInitScript(() => { try { localStorage.setItem('anum.agents.tab', 'agents'); } catch { /* storage blocked */ } }); } });
  await page.goto('/#agents');
  await page.locator('.agentOpen', { hasText: 'Lead Finder' }).click();
  await page.locator('.agentTabs button').last().click();
  const editor = page.locator('[data-avatar-editor="agent_leads"]');
  await expect(editor.getByRole('heading', { name: 'شكل الأفاتار' })).toBeVisible();
  await expect(editor.getByRole('radio', { name: 'بنية أنثوية' })).toBeVisible();
  expect(await editor.evaluate((el) => getComputedStyle(el).direction)).toBe('rtl');
});
