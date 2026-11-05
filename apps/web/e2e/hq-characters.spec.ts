import AxeBuilder from '@axe-core/playwright';
import { expect as baseExpect, test, type Page } from '@playwright/test';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { mockFlow } from './support/hqFlowApi';
import { mockProvisioning } from './support/hqProvisioningApi';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { agentAvatars as teamAvatars } from './support/orgChartApi';
import { greenRegistry, mockGreenAction, mockGreenWork, newState } from './support/hqGreenConcreteApi';

const expect = baseExpect.configure({ timeout: 20_000 });

// The people of the headquarters (docs/ANUM_MINE_HQ_PEOPLE_CHARACTERS.md): detailed figures in three looks (masculine, feminine, neutral)
// that come ONLY from the look the owner chose, a clear Look control in the Team panel with a small preview, and the same optional
// choice per person in the "Add Green Concrete" preview. The API is mocked at http://localhost:8000/**; nothing here reads a name.
test.use({ actionTimeout: Number(process.env.HQ_ACTION_TIMEOUT ?? 30_000), deviceScaleFactor: 1, isMobile: false, hasTouch: false });
test.describe.configure({ timeout: 240_000 });

const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
const AGENTS = ['agent_fleet', 'agent_leads', 'agent_care'];
const registry = hqRegistry(TENANT_A, { rooms: 1, desks: 3, agentIds: AGENTS });
const stamp = '2026-10-25T08:00:00Z';
const NOON = new Date(Date.UTC(2026, 9, 26, 8, 0, 0));
const hq = (page: Page) => page.locator('.hqLive');
/** HQ_SHOTS_DIR keeps review screenshots. */
async function shot(page: Page, name: string) {
  const dir = process.env.HQ_SHOTS_DIR;
  if (!dir) return;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: join(dir, `${name}.png`) });
}
const canvas = (page: Page) => page.locator('canvas.hqCanvas');
const looks = async (page: Page): Promise<string[]> => ((await canvas(page).getAttribute('data-figure-looks')) ?? '').split(' ').filter(Boolean);

/** Answers the PATCH of an agent the way the server does and keeps what was saved, so the roster reports it. */
async function mockSaves(page: Page, patches: Array<{ id: string; body: Record<string, unknown> }>) {
  await page.route(/\/api\/v1\/agents\/([^/]+)$/, (route) => {
    const request = route.request();
    if (request.method() !== 'PATCH') return route.fallback();
    const id = new URL(request.url()).pathname.split('/').pop()!;
    const avatar = request.postDataJSON().avatar;
    teamAvatars[id] = avatar;
    patches.push({ id, body: request.postDataJSON() });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      id, tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', owner_user_id: 'user_local', status: 'active', created_at: stamp, updated_at: stamp, version: 2,
      can_edit: true, can_run: true, unavailable_tools: [], unavailable_skills: [], name: 'Fleet Planner', icon: 'truck', color: '#0f766e', category: 'work', department: 'Operations and warehouse',
      description: '', instructions: '', model_route: 'capable', allowed_skills: [], allowed_tools: [], autonomy: { read: 'auto', write: 'ask', external: 'ask' },
      knowledge_mode: 'none', knowledge_sources: [], sharing: 'workspace', template_id: null, avatar,
    }) });
  });
}

async function openOffice(page: Page, options: { locale?: 'en' | 'ar'; patches?: Array<{ id: string; body: Record<string, unknown> }> } = {}) {
  for (const key of Object.keys(teamAvatars)) delete teamAvatars[key];
  await openHq(page, { registry, locale: options.locale, beforeGoto: async (p) => { await mockFlow(p); if (options.patches) await mockSaves(p, options.patches); } });
}

async function enterBuilding(page: Page, ar = false) {
  await hq(page).getByRole('button', { name: ar ? /Al Noor Fleet HQ/ : /^Al Noor Fleet HQ\./ }).first().click();
  await hq(page).getByRole('button', { name: ar ? /Al Noor Fleet HQ/ : 'Open Al Noor Fleet HQ' }).last().click();
  await expect(hq(page).getByRole('heading', { level: 2, name: 'Al Noor Fleet HQ' })).toBeVisible();
}

async function openTeam(page: Page) {
  await hq(page).getByRole('button', { name: /Office tools|أدوات المكتب/ }).click();
  await hq(page).getByRole('button', { name: /^(Team|الفريق)$/ }).click();
}

/** The canvas as pixels: how many pixels differ between two shots (a figure that really changed shows many). */
async function pixelDiff(page: Page, a: Buffer, b: Buffer): Promise<number> {
  return page.evaluate(async ([x, y]) => {
    const load = async (b64: string) => { const i = new Image(); i.src = `data:image/png;base64,${b64}`; await i.decode(); const c = document.createElement('canvas'); c.width = i.width; c.height = i.height; const g = c.getContext('2d')!; g.drawImage(i, 0, 0); return g.getImageData(0, 0, c.width, c.height).data; };
    const [p, q] = await Promise.all([load(x), load(y)]);
    let n = 0;
    for (let i = 0; i < Math.min(p.length, q.length); i += 4) if (Math.abs(p[i] - q[i]) + Math.abs(p[i + 1] - q[i + 1]) + Math.abs(p[i + 2] - q[i + 2]) > 40) n++;
    return n;
  }, [a.toString('base64'), b.toString('base64')] as const);
}

async function noOverflow(page: Page, root: string) {
  const problems = await page.evaluate((selector) => {
    const out: string[] = [];
    if (document.documentElement.scrollWidth > window.innerWidth) out.push(`page scrolls sideways (${document.documentElement.scrollWidth} > ${window.innerWidth})`);
    const scope = document.querySelector<HTMLElement>(selector);
    if (!scope) return [...out, `no ${selector}`];
    const box = scope.getBoundingClientRect();
    if (box.left < -1 || box.right > window.innerWidth + 1) out.push('the panel leaves the screen sideways');
    return out;
  }, root);
  expect(problems).toEqual([]);
}

// ------------------------------------------------------------------------------------------------ the figures

test('masculine, feminine and neutral figures are drawn from the look the owner chose, by the character kit', async ({ page }) => {
  for (const key of Object.keys(teamAvatars)) delete teamAvatars[key];
  teamAvatars.agent_fleet = { avatar_style: 'masculine' };
  teamAvatars.agent_leads = { avatar_style: 'feminine' };
  teamAvatars.agent_care = { avatar_style: 'neutral' };
  await openHq(page, { registry, beforeGoto: async (p) => { await mockFlow(p); } });
  await enterBuilding(page);
  await expect.poll(async () => (await looks(page)).length, { timeout: 90_000 }).toBe(3);
  await expect(canvas(page)).toHaveAttribute('data-figure-styles', 'feminine:1,masculine:1,neutral:1');
  await expect(canvas(page)).toHaveAttribute('data-model-figures', '3');
  // The kit built them in the browser: nothing fell back to the stand-in boxes.
  await expect(canvas(page)).toHaveAttribute('data-model-fallbacks', '');
  await expect(canvas(page)).toHaveAttribute('data-model-kit', '3');
  const codes = await looks(page);
  expect(codes.map((c) => c.split('/')[0]).sort()).toEqual(['feminine', 'masculine', 'neutral']);
  expect(new Set(codes).size).toBe(3);
});

test('the owner picks a look in the Team panel: the figure in the room changes, the choice is the only input', async ({ page }) => {
  const patches: Array<{ id: string; body: Record<string, unknown> }> = [];
  for (const key of Object.keys(teamAvatars)) delete teamAvatars[key];
  teamAvatars.agent_fleet = { avatar_style: 'neutral' };
  teamAvatars.agent_leads = { avatar_style: 'neutral' };
  teamAvatars.agent_care = { avatar_style: 'neutral' };
  await openHq(page, { registry, beforeGoto: async (p) => { await mockFlow(p); await mockSaves(p, patches); } });
  await enterBuilding(page);
  await expect.poll(async () => (await looks(page)).length, { timeout: 90_000 }).toBe(3);
  await expect(canvas(page)).toHaveAttribute('data-figure-styles', 'feminine:0,masculine:0,neutral:3');
  await expect(canvas(page)).toHaveAttribute('data-model-kit', '3');
  // Zoom in so each person is big enough for a pixel comparison.
  for (let i = 0; i < 4; i++) { await hq(page).getByRole('button', { name: /Zoom in/ }).first().click(); await page.waitForTimeout(250); }
  await page.waitForTimeout(2500);
  const before = await canvas(page).screenshot();
  await openTeam(page);
  const group = hq(page).locator('[data-look-for="agent_fleet"]');
  await expect(group).toBeVisible();
  await expect(group.getByText('Look', { exact: true })).toBeVisible();
  await expect(group.getByRole('radio', { name: /Neutral/ })).toBeChecked();
  await shot(page, 'team-look-before');
  await group.getByRole('radio', { name: /^Feminine/ }).click();
  await expect.poll(() => patches.length).toBe(1);
  await shot(page, 'team-look-after');
  expect(patches[0].id).toBe('agent_fleet');
  expect((patches[0].body.avatar as Record<string, unknown>).avatar_style).toBe('feminine');
  await expect(canvas(page)).toHaveAttribute('data-figure-styles', 'feminine:1,masculine:0,neutral:2', { timeout: 60_000 });
  await page.waitForTimeout(2500);
  const after = await canvas(page).screenshot();
  expect(await pixelDiff(page, before, after)).toBeGreaterThan(80);
  // The other two keep the look they had.
  expect((await looks(page)).filter((c) => c.startsWith('neutral/')).length).toBe(2);
});

test('an agent with no chosen look says it is automatic, and choosing one saves only the look', async ({ page }) => {
  const patches: Array<{ id: string; body: Record<string, unknown> }> = [];
  await openOffice(page, { patches });
  await enterBuilding(page);
  await openTeam(page);
  const group = hq(page).locator('[data-look-for="agent_care"]');
  await expect(group.getByText('Automatic: drawn from the record id')).toBeVisible();
  await expect(group.getByRole('radio', { checked: true })).toHaveCount(0);
  await group.getByRole('radio', { name: /^Masculine/ }).click();
  await expect.poll(() => patches.length).toBe(1);
  expect(patches[0].body).toEqual({ avatar: { avatar_style: 'masculine' } });
});

test('a look that cannot be saved says what failed and keeps the choice open', async ({ page }) => {
  await openOffice(page);
  await page.route(/\/api\/v1\/agents\/agent_fleet$/, (route) => route.request().method() === 'PATCH'
    ? route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ detail: 'Only the creator can change this agent.' }) }) : route.fallback());
  await enterBuilding(page);
  await openTeam(page);
  await hq(page).locator('[data-look-for="agent_fleet"]').getByRole('radio', { name: /^Neutral/ }).click();
  await expect(hq(page).locator('[data-look-for="agent_fleet"]').getByRole('alert')).toContainText('The look could not be saved');
});

test('Arabic: the Look control is right to left and fully translated', async ({ page }) => {
  await openOffice(page, { locale: 'ar' });
  await enterBuilding(page, true);
  await openTeam(page);
  const group = hq(page).locator('[data-look-for="agent_fleet"]');
  await expect(group).toBeVisible();
  await expect(group.getByText('المظهر', { exact: true })).toBeVisible();
  await expect(group.getByRole('radio', { name: /^ذكوري/ })).toBeVisible();
  await expect(group.getByRole('radio', { name: /^أنثوي/ })).toBeVisible();
  await expect(group.getByRole('radio', { name: /^محايد/ })).toBeVisible();
  expect(await hq(page).locator('.hqTeam').evaluate((el) => getComputedStyle(el).direction)).toBe('rtl');
});

for (const [w, h] of SIZES) {
  test(`the Look control and the figures fit at ${w}x${h}, with no accessibility violations`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await openOffice(page);
    await enterBuilding(page);
    await openTeam(page);
    const group = hq(page).locator('[data-look-for="agent_fleet"]');
    await group.scrollIntoViewIfNeeded();
    await expect(group).toBeVisible();
    await noOverflow(page, '.hqTeam');
    // Every radio of the group is inside the screen and big enough to hit.
    for (const radio of await group.getByRole('radio').all()) {
      const box = (await radio.locator('xpath=..').boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(-1);
      expect(box.x + box.width).toBeLessThanOrEqual(w + 1);
      expect(box.height).toBeGreaterThanOrEqual(24);
    }
    await shot(page, `team-look-${w}x${h}`);
    const result = await new AxeBuilder({ page }).include('.hqTeam').disableRules(['color-contrast']).analyze();
    expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  });
}

// ------------------------------------------------------------------------------------------------ Add Green Concrete

async function openGreen(page: Page, options: Parameters<typeof mockGreenAction>[2] & { locale?: 'en' | 'ar'; viewport?: readonly [number, number] } = {}) {
  if (options.viewport) await page.setViewportSize({ width: options.viewport[0], height: options.viewport[1] });
  await page.clock.setFixedTime(NOON);
  const state = newState(() => NOON.getTime());
  await openHq(page, {
    registry: greenRegistry(), locale: options.locale, noWebgl: true,
    beforeGoto: async (p) => { await mockFlow(p); await mockProvisioning(p); await mockGreenWork(p, state); await mockGreenAction(p, state, options); },
  });
  state.created = options.created ?? false;
  await expect(hq(page)).toBeVisible();
  await hq(page).getByRole('button', { name: /Office tools|أدوات المكتب/ }).click();
  await hq(page).getByRole('button', { name: /Add Green Concrete|إضافة جرين كونكريت/ }).click();
  await expect(page.getByTestId('gc-dialog')).toBeVisible();
  return state;
}
const dialog = (page: Page) => page.getByTestId('gc-dialog');

test('Add Green Concrete: every person defaults to the neutral look, nobody is guessed from a name', async ({ page }) => {
  const state = await openGreen(page);
  const table = dialog(page).getByTestId('gc-people');
  await expect(table.locator('tbody tr')).toHaveCount(10);
  await expect(dialog(page)).toContainText('nothing is guessed from a name');
  for (const row of await table.locator('tbody tr').all()) await expect(row.getByRole('combobox')).toHaveValue('neutral');
  // The plan was asked for with the neutral default and no personal choice; every name (Mai, Raed, Badea, ...) gets the same default.
  expect(state.previews![0]).toEqual({ default_look: 'neutral', looks: {} });
  await dialog(page).getByTestId('gc-confirm').click();
  await expect(dialog(page).getByTestId('gc-done')).toContainText('Created');
  expect(state.confirms[0]).toMatchObject({ input_mode: 'click', default_look: 'neutral', appearance: {} });
});

test('Add Green Concrete: the owner picks a look per person, the plan is made again for it and the confirm carries it', async ({ page }) => {
  const state = await openGreen(page);
  const table = dialog(page).getByTestId('gc-people');
  await table.locator('tr[data-person="raed"]').getByRole('combobox').selectOption('masculine');
  await expect.poll(() => state.previews!.length).toBeGreaterThan(1);
  await table.locator('tr[data-person="mai"]').getByRole('combobox').selectOption('feminine');
  await expect.poll(() => state.previews!.at(-1)?.looks.mai).toBe('feminine');
  expect(state.previews!.at(-1)).toEqual({ default_look: 'neutral', looks: { raed: 'masculine', mai: 'feminine' } });
  await expect(table.locator('tr[data-person="raed"]').getByRole('combobox')).toHaveValue('masculine');
  await expect(table.locator('tr[data-person="hazrat"]').getByRole('combobox')).toHaveValue('neutral');
  // The small preview next to each choice follows it.
  await expect(table.locator('tr[data-person="raed"] svg[data-look^="masculine/"]')).toHaveCount(1);
  await expect(table.locator('tr[data-person="mai"] svg[data-look^="feminine/"]')).toHaveCount(1);
  await expect(table.locator('tr[data-person="hazrat"] svg[data-look^="neutral/"]')).toHaveCount(1);
  await expect(dialog(page).getByTestId('gc-confirm')).toBeEnabled();
  await shot(page, 'green-preview-looks');
  await dialog(page).getByTestId('gc-confirm').click();
  await expect(dialog(page).getByTestId('gc-done')).toContainText('Created');
  // The confirm is for the looks that were shown: the hash matched (a mismatch would have been a 409).
  expect(state.confirms[0]).toMatchObject({ input_mode: 'click', default_look: 'neutral', appearance: { raed: 'masculine', mai: 'feminine' } });
});

test('Add Green Concrete: people who already exist keep their look, and a person who cannot confirm only reads', async ({ page }) => {
  await openGreen(page, { created: true });
  await expect(dialog(page).getByTestId('gc-people').getByRole('combobox')).toHaveCount(0);
  await expect(dialog(page).getByTestId('gc-people')).toContainText('look unchanged');
});

test('Add Green Concrete: a viewer sees the neutral look as text, not a control', async ({ page }) => {
  await openGreen(page, { viewer: true });
  await expect(dialog(page).getByTestId('gc-people').getByRole('combobox')).toHaveCount(0);
  await expect(dialog(page).getByTestId('gc-confirm')).toBeDisabled();
});

test('Add Green Concrete, Arabic: the look column is right to left and translated', async ({ page }) => {
  await openGreen(page, { locale: 'ar' });
  expect(await dialog(page).evaluate((el) => getComputedStyle(el.parentElement!).direction)).toBe('rtl');
  const first = dialog(page).getByTestId('gc-people').locator('tr[data-person="raed"]');
  await expect(dialog(page).getByTestId('gc-people').getByRole('columnheader', { name: 'المظهر' })).toBeVisible();
  await expect(first.getByRole('combobox')).toHaveValue('neutral');
  await expect(first.getByRole('option', { name: 'محايد' })).toHaveCount(1);
  await expect(first.getByRole('option', { name: 'ذكوري' })).toHaveCount(1);
  await expect(first.getByRole('option', { name: 'أنثوي' })).toHaveCount(1);
  await shot(page, 'green-preview-looks-ar');
});

for (const [w, h] of SIZES) {
  test(`Add Green Concrete with the look choices fits at ${w}x${h}, with no accessibility violations`, async ({ page }) => {
    await openGreen(page, { viewport: [w, h] });
    await expect(dialog(page).getByTestId('gc-people').locator('tbody tr')).toHaveCount(10);
    await noOverflow(page, '[data-testid="gc-dialog"]');
    const select = dialog(page).getByTestId('gc-people').locator('tr[data-person="raed"]').getByRole('combobox');
    await select.scrollIntoViewIfNeeded();
    const box = (await select.boundingBox())!;
    expect(box.height).toBeGreaterThanOrEqual(30);
    await shot(page, `green-preview-looks-${w}x${h}`);
    const result = await new AxeBuilder({ page }).include('[data-testid="gc-dialog"]').disableRules(['color-contrast']).analyze();
    expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  });
}
