import { expect, test, type Page } from '@playwright/test';
import { ARABIC_NAMES_NONE } from './support/hqArabicNamesApi';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { mockFlow } from './support/hqFlowApi';
import { mockProvisioning } from './support/hqProvisioningApi';
import { openBuilder } from './support/hqBuilderApi';

// Departments that open a real module, and live meetings tied to their office room
// (docs/ANUM_MINE_DEPARTMENTS_AND_MEETING_ROOMS.md). Names are plainly fake test data.
test.use({ viewport: { width: 1280, height: 800 } });

// [department, room name, module hash]
const MODULES: Array<[string, string, string]> = [
  ['marketing', 'Alpha Studio', '#marketing'],
  ['technology', 'Alpha Build', '#developer'],
  ['research', 'Alpha Lab', '#knowledge'],
  ['legal', 'Alpha Counsel', '#files'],
  ['compliance', 'Alpha Rules', '#governance'],
  ['security', 'Alpha Guard', '#audit'],
  ['hr', 'Alpha People', '#members'],
];
const NO_MODULE: Array<[string, string]> = [['procurement', 'Alpha Buying'], ['custom', 'Alpha Misc']];

function registry() {
  const base = hqRegistry(TENANT_A, { rooms: 12, desks: 3 });
  const rooms = base.rooms.map((r, i) => {
    if (i < MODULES.length) return { ...r, name: MODULES[i][1], kind: 'office', team_department: MODULES[i][0] };
    const j = i - MODULES.length;
    if (j < NO_MODULE.length) return { ...r, name: NO_MODULE[j][1], kind: 'office', team_department: NO_MODULE[j][0] };
    if (j === 2) return { ...r, name: 'Alpha Boardroom', kind: 'meeting', team_department: null };
    if (j === 3) return { ...r, name: 'Alpha Quiet Room', kind: 'meeting', team_department: null };
    return { ...r, name: 'Alpha Annex', kind: 'meeting', team_department: 'legal' };
  });
  return { ...base, rooms };
}
const BOARDROOM_ID = 'Alpha-room-9';

async function open(page: Page, options: Parameters<typeof openHq>[1] = {}) {
  const mock = await openHq(page, { registry: registry(), meetingRoomId: BOARDROOM_ID, ...options });
  await expect(page.locator('.hqLive')).toBeVisible();
  return mock;
}
const listView = async (page: Page, locale: 'en' | 'ar' = 'en') =>
  page.locator('.hqLive').getByRole('button', { name: locale === 'ar' ? 'قائمة' : 'List', exact: true }).click();
async function openRoom(page: Page, name: string, locale: 'en' | 'ar' = 'en', options: Parameters<typeof openHq>[1] = {}) {
  const mock = await open(page, { locale, ...options });
  await listView(page, locale);
  await page.locator('.hqListRoom', { hasText: name }).click();
  return mock;
}

for (const [department, room, hash] of MODULES) {
  test(`${department} room opens ${hash}: in the inspector and in the list`, async ({ page }) => {
    await open(page);
    await listView(page);
    await expect(page.locator('.hqListRooms li', { hasText: room }).locator('[data-testid="room-module"]')).toHaveAttribute('href', hash);
    await page.locator('.hqListRoom', { hasText: room }).click();
    const link = page.locator('.hqSide [data-testid="room-module"]');
    await expect(link).toHaveAttribute('href', hash);
    await expect(page.locator('.hqSide [data-testid="room-module-hint"]')).toHaveCount(0);
    await link.focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => page.evaluate(() => location.hash)).toBe(hash);
  });
}

for (const [department, room] of NO_MODULE) {
  test(`${department} room says no module is connected and has no dead button`, async ({ page }) => {
    await openRoom(page, room);
    await expect(page.locator('.hqSide [data-testid="room-module"]')).toHaveCount(0);
    await expect(page.locator('.hqSide [data-testid="room-module-none"]')).toContainText('No module connected for this department');
    await page.locator('.hqSide').getByRole('button', { name: 'Organisation gaps' }).click();
    await expect(page.getByRole('dialog', { name: 'Organisation gaps' })).toBeVisible();
  });
}

test('the builder offers every new department and saves the one chosen', async ({ page }) => {
  const mock = await openBuilder(page);
  await page.locator('.hqbSvg [data-room-id="r-sales"] .hqbRoomBody').click();
  const team = page.locator('label.hqbField', { hasText: 'Linked team' }).locator('select');
  await expect(team.locator('option')).toHaveText(['No team linked', 'Sales', 'Support', 'Operations', 'Finance', 'Tyre engineering', 'Marketing', 'Technology', 'Research',
    'Legal', 'Compliance and governance', 'Security', 'People (HR)', 'Procurement', 'Executive', 'Custom']);
  await team.selectOption('legal');
  await page.getByRole('button', { name: 'Save details' }).click();
  await expect.poll(() => mock.requests.find((r) => r.method === 'PATCH' && r.path === '/rooms/r-sales')?.body).toMatchObject({ team_department: 'legal' });
});

// ------------------------------------------------------------------ meetings tied to rooms

test('a live meeting shows "In a meeting" on its room in the list and the inspector, with its real participants', async ({ page }) => {
  await open(page);
  await listView(page);
  await expect(page.locator('.hqListRooms li', { hasText: 'Alpha Boardroom' }).locator('[data-testid="list-in-meeting"]')).toHaveText('In a meeting');
  await expect(page.locator('.hqListRooms li', { hasText: 'Alpha Quiet Room' }).locator('[data-testid="list-in-meeting"]')).toHaveCount(0);
  await expect(page.locator('.hqListRooms li', { hasText: 'Alpha Boardroom' }).locator('[data-testid="room-module"]')).toHaveAttribute('href', '#office/meeting/meeting_live');
  await page.locator('.hqListRoom', { hasText: 'Alpha Boardroom' }).click();
  const section = page.locator('.hqSide [data-testid="room-meeting"]');
  await expect(section).toContainText('In a meeting');
  await expect(section).toContainText('Weekly sync');
  // Only the agent stored as a participant; the person who started it is a count, never a name.
  await expect(section.locator('[data-testid="room-meeting-agent"]')).toHaveCount(1);
  await expect(section.locator('[data-testid="room-meeting-agent"]')).toContainText('WhatsApp Concierge');
  const meetingLink = page.locator('.hqSide [data-testid="room-module"]');
  await expect(meetingLink).toHaveAttribute('href', '#office/meeting/meeting_live');
  await expect(meetingLink).toContainText('Open the live meeting');
  // The room that holds no meeting is not marked and offers to start one.
  await page.locator('.hqSide').getByRole('button', { name: 'Close panel' }).click();
  await listView(page);
  await page.locator('.hqListRoom', { hasText: 'Alpha Quiet Room' }).click();
  await expect(page.locator('.hqSide [data-testid="room-meeting"]')).toHaveCount(0);
  await expect(page.locator('.hqSide [data-testid="room-start-meeting"]')).toBeVisible();
});

test('a room with another department still reaches its live meeting', async ({ page }) => {
  await openRoom(page, 'Alpha Annex', 'en', { meetingRoomId: 'Alpha-room-11' });
  await expect(page.locator('.hqSide [data-testid="room-module"]')).toHaveAttribute('href', '#files');
  await expect(page.locator('.hqSide [data-testid="room-live-meeting"]')).toHaveAttribute('href', '#office/meeting/meeting_live');
});

test('a participant who left is listed as left, never as in the meeting', async ({ page }) => {
  const mock = await openRoom(page, 'Alpha Boardroom');
  const section = page.locator('.hqSide [data-testid="room-meeting"]');
  await expect(section.locator('[data-testid="room-meeting-agent"]')).toHaveCount(1);
  mock.meeting.left.push(
    { id: 'p2', session_id: 'meeting_live', actor_id: 'agent_invoice', kind: 'agent', left_at: '2026-10-28T09:30:00Z' },
    { id: 'p3', session_id: 'meeting_live', actor_id: 'user_left', kind: 'human', left_at: '2026-10-28T09:31:00Z' },
  );
  await page.locator('.hqLive').getByRole('button', { name: 'Refresh the headquarters' }).click();
  await expect(section.locator('[data-testid="room-meeting-left"]')).toHaveCount(1);
  await expect(section.locator('[data-testid="room-meeting-left"]')).toContainText('Invoice Checker');
  await expect(section.locator('[data-testid="room-meeting-left"]')).toContainText('Left');
  // Still only the agent who is there; the person who left is a count, never a name, and never "joined".
  await expect(section.locator('[data-testid="room-meeting-agent"]')).toHaveCount(1);
  await expect(section.locator('[data-testid="room-meeting-agent"]')).toContainText('WhatsApp Concierge');
  await expect(section.locator('[data-testid="room-meeting-people-left"]')).toHaveText('People who left: 1');
  await expect(section).not.toContainText('People who joined');
  await expect(section).not.toContainText('user_left');
});

test('the badge disappears when the meeting finishes', async ({ page }) => {
  const mock = await openRoom(page, 'Alpha Boardroom');
  await expect(page.locator('.hqSide [data-testid="room-meeting"]')).toBeVisible();
  mock.meeting.active = false;
  await page.locator('.hqLive').getByRole('button', { name: 'Refresh the headquarters' }).click();
  await expect(page.locator('.hqSide [data-testid="room-meeting"]')).toHaveCount(0);
  await expect(page.locator('.hqSide [data-testid="room-module"]')).toHaveAttribute('href', '#meetings');
  await page.locator('.hqSide').getByRole('button', { name: 'Close panel' }).click();
  await listView(page);
  await expect(page.locator('.hqListRooms li', { hasText: 'Alpha Boardroom' }).locator('[data-testid="list-in-meeting"]')).toHaveCount(0);
});

test('starting a meeting from a room stores that room on the meeting', async ({ page }) => {
  const mock = await openRoom(page, 'Alpha Quiet Room');
  await page.locator('.hqSide [data-testid="room-start-meeting"]').click();
  const sessions = page.getByRole('region', { name: 'Agent meeting sessions' });
  await expect(sessions).toBeVisible();
  await expect(sessions.getByRole('combobox', { name: 'Room (optional)' })).toHaveValue('Alpha-room-10');
  await sessions.getByRole('textbox', { name: 'New meeting title' }).fill('Plan the quarter');
  await sessions.getByRole('button', { name: 'Create meeting' }).click();
  await expect.poll(() => mock.meeting.created.length).toBe(1);
  expect(mock.meeting.created[0]).toMatchObject({ title: 'Plan the quarter', room_id: 'Alpha-room-10' });
  await expect(sessions.getByTestId('meeting-room-link')).toContainText('Alpha Quiet Room');
});

test('a meeting can be created without a room, or in a chosen meeting room', async ({ page }) => {
  const mock = await open(page);
  await page.locator('.hqLive').getByRole('button', { name: /Office tools/ }).click();
  await page.locator('.hqLive').getByRole('button', { name: /meeting/i }).first().click();
  const sessions = page.getByRole('region', { name: 'Agent meeting sessions' });
  const select = sessions.getByRole('combobox', { name: 'Room (optional)' });
  await expect(select.locator('option')).toHaveText(['No room', 'Alpha Annex', 'Alpha Boardroom', 'Alpha Quiet Room']);
  await sessions.getByRole('textbox', { name: 'New meeting title' }).fill('No room needed');
  await sessions.getByRole('button', { name: 'Create meeting' }).click();
  await expect.poll(() => mock.meeting.created.length).toBe(1);
  expect(mock.meeting.created[0].room_id).toBeNull();
});

test('without WebGL the list still marks the room and opens the live meeting', async ({ page }) => {
  await openRoom(page, 'Alpha Boardroom', 'en', { noWebgl: true });
  await expect(page.locator('.hqNotice', { hasText: /WebGL|3D/i })).toBeVisible();
  await expect(page.locator('.hqSide [data-testid="room-meeting"]')).toBeVisible();
  await expect(page.locator('.hqSide [data-testid="room-module"]')).toHaveAttribute('href', '#office/meeting/meeting_live');
});

test('the 3D label card says "In a meeting" and drops it when the meeting ends', async ({ page }) => {
  const mock = await open(page);
  await page.locator('.hqLive').getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click();
  await page.locator('.hqLive').getByRole('button', { name: 'Open Al Noor Fleet HQ' }).click();
  await expect(page.locator('.hqLive .hqLabelText', { hasText: 'In a meeting' })).toHaveCount(1, { timeout: 20000 });
  mock.meeting.active = false;
  await page.locator('.hqLive').getByRole('button', { name: 'Refresh the headquarters' }).click();
  await expect(page.locator('.hqLive .hqLabelText', { hasText: 'In a meeting' })).toHaveCount(0);
});

// ------------------------------------------------------------------ Arabic and sizes

test('Arabic: a new department module and the meeting indicator read in Arabic', async ({ page }) => {
  await openRoom(page, 'Alpha Studio', 'ar');
  await expect(page.locator('.hqSide [data-testid="room-module"]')).toContainText('التسويق');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  if (process.env.HQ_SHOTS_DIR) await page.screenshot({ path: `${process.env.HQ_SHOTS_DIR}/department-ar.png` });
  await page.locator('.hqSide').getByRole('button', { name: /إغلاق/ }).click();
  await listView(page, 'ar');
  await page.locator('.hqListRoom', { hasText: 'Alpha Boardroom' }).click();
  await expect(page.locator('.hqSide [data-testid="room-meeting"]')).toContainText('في اجتماع');
  await expect(page.locator('.hqSide [data-testid="room-module"]')).toContainText('فتح الاجتماع المباشر');
  if (process.env.HQ_SHOTS_DIR) await page.screenshot({ path: `${process.env.HQ_SHOTS_DIR}/meeting-room-ar.png` });
});

// ------------------------------------------------------------------ agent templates in the review dialog

const detail = (id: string, name: string, department: string, limited: boolean) => ({
  version: 1, payload_hash: 'cd'.repeat(32),
  content: {
    template_id: id, catalog_version: 1, lang: 'en', initial_status: 'paused', work_kinds: [],
    agent: { name, icon: 'sparkles', color: '#be185d', category: 'work', department, description: `${name} for the ${department} room.`,
      instructions: 'You support marketing. What you hand back: a weekly note. You never: publish a post.', model_route: 'fast',
      allowed_skills: ['anum.brevo'], allowed_tools: ['marketing.summary'], autonomy: { read: 'auto', write: 'ask', external: 'off' },
      knowledge_mode: 'workspace', sharing: 'workspace', template_id: `hq:${id}` },
    missing: [], limited, budget: { monthly_cost_limit_usd: 6 }, placement: { mode: 'none', room_id: null, desk_id: null, desk_label: null, reason: 'no_linked_room' },
  },
  template: { id, department, room_department: department, name: { en: name, ar: name }, description: { en: 'd', ar: 'd' }, work_kinds: [], icon: 'sparkles', color: '#be185d',
    model_route: 'fast', autonomy: { read: 'auto', write: 'ask', external: 'off' }, monthly_cost_limit_usd: 6, knowledge_mode: 'workspace',
    tools: [{ name: 'marketing.summary', state: 'present', reason: null, risk: 'low', category: 'marketing', detail: null }],
    skills: [{ id: 'anum.brevo', name: 'Email campaigns', state: 'present', reason: null, source: 'builtin' }],
    limited, missing: [{ kind: 'capability', id: 'advertising_reports', reason: 'no_tool_exists', en: 'Advertising account reports (spend and results)', ar: 'تقارير الإعلانات' }] },
  names: { en: name, ar: name },
  placement: { mode: 'none', reason: 'no_linked_room', room_id: null, room_name: null, floor_id: null, building_id: null, candidates: [] },
  gap: { key: `gap:${id}`, template_id: id, severity: 'warning', flow_based: false, reasons: [{ code: 'empty_department', department, rooms: [{ id: 'Alpha-room-0', name: 'Alpha Studio' }] }] },
  coverage: { template_id: id, work_kinds: [], state: 'gap', handler: null }, can_confirm: true,
  creates: { agent: 1, desk: 0, seat: 0, budget_cap: 1, ledger_row: 1 },
});

test('the review dialog of a new role shows its instructions, knowledge access, limits and cost cap', async ({ page }) => {
  await openHq(page, { registry: registry(), beforeGoto: async (p) => {
    await mockFlow(p);
    await mockProvisioning(p);
    await p.route(/http:\/\/localhost:8000\/api\/v1\/office\/provisioning\/(gaps|proposals\/marketing_specialist)(\?.*)?$/, async (route) => {
      const url = new URL(route.request().url());
      const body = url.pathname.endsWith('/gaps')
        ? { version: 1, workspace_id: 'workspace_foundation', as_of: new Date().toISOString(), sources: { tasks: 'connected', orders: 'not_connected' },
          gaps: [{ key: 'gap:marketing_specialist', template_id: 'marketing_specialist', severity: 'warning', flow_based: false,
            reasons: [{ code: 'empty_department', department: 'marketing', rooms: [{ id: 'Alpha-room-0', name: 'Alpha Studio' }] }] }],
          coverage: [{ template_id: 'marketing_specialist', work_kinds: [], state: 'gap', handler: null }],
          limits: { agents: 2, max_agents: 200 }, can_confirm: true, templates: [] }
        : detail('marketing_specialist', 'Marketing Specialist', 'marketing', true);
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    });
  } });
  await page.locator('.hqLive').getByRole('button', { name: /Office tools/ }).click();
  await page.locator('.hqLive').getByRole('button', { name: /Organisation gaps/ }).click();
  await page.getByRole('button', { name: 'Review the proposal for Marketing Specialist' }).click();
  const d = page.locator('.hqProvDialog[role=dialog]');
  await expect(d).toContainText('A monthly cost limit of 6 USD');
  await expect(d).toContainText('Created paused');
  await expect(d).toContainText('Limited');
  await expect(d.getByRole('list', { name: 'Missing capabilities' })).toContainText('Advertising account reports');
  await expect(d).toContainText('Knowledge base: it may read this workspace knowledge base, nothing else.');
  const instructions = d.getByTestId('prov-instructions');
  await expect(instructions.locator('p')).toBeHidden();
  await instructions.locator('summary').click();
  await expect(instructions.locator('p')).toContainText('You never: publish a post.');
  if (process.env.HQ_SHOTS_DIR) await page.screenshot({ path: `${process.env.HQ_SHOTS_DIR}/review-dialog-new-role.png` });
});

// ------------------------------------------------------------------ starter campus fills departments

test('Set up my campus offers to fill the department of existing campus rooms and confirms with a click', async ({ page }) => {
  const zero = { buildings: 0, floors: 0, rooms: 0, desks: 0 };
  const room = (key: string, en: string, to: string) => ({ key, id: `campus-04-corporate-services-${key}`, name: { en, ar: en }, kind: 'office', capacity: 6, width: 10, depth: 8,
    door_side: 'south', color: 'slate', team_department: null, equipment: [], desk_count: 4, action: 'skip', reason: 'exists', desks: [], department_set: { from: null, to } });
  const plan = { key: 'corporate-services', number: 4, id: 'campus-04-corporate-services', name: { en: 'Corporate Services', ar: 'الخدمات المؤسسية' }, timezone: 'Asia/Dubai', selected: true,
    action: 'skip', reason: 'exists', status: 'partial',
    floors: [{ key: 'l1', id: 'campus-04-corporate-services-l1', level: 1, name: { en: 'Floor 1', ar: 'الطابق 1' }, width_m: 34, depth_m: 20, action: 'skip', reason: 'exists',
      rooms: [room('legal-office', 'Legal Office', 'legal'), room('hr', 'HR', 'hr')] }],
    counts: { create: zero, skip: { buildings: 1, floors: 1, rooms: 2, desks: 0 }, update: { room_departments: 2 } } };
  const preview = { version: 1, catalog_version: 1, workspace_id: 'workspace_foundation', timezone: 'Asia/Dubai', selected: ['corporate-services'], payload_hash: 'ab'.repeat(32),
    can_confirm: true, buildings: [plan], totals: { create: zero, skip: { buildings: 1, floors: 1, rooms: 2, desks: 0 }, update: { room_departments: 2 } },
    rules: { creates_agents: false, seats_agents: false, changes_existing: false, fills_empty_departments: true } };
  let confirmed: Record<string, unknown> | null = null;
  await openHq(page, { registry: registry(), beforeGoto: async (p) => {
    await p.route(/http:\/\/localhost:8000\/api\/v1\/office\/starter-campus\/.*/, async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.includes('/arabic-names/')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(ARABIC_NAMES_NONE) });
      if (path.endsWith('/preview')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(preview) });
      confirmed = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version: 1, outcome: 'created', payload_hash: 'ab'.repeat(32), timezone: 'Asia/Dubai',
        buildings: [{ key: 'corporate-services', id: plan.id, name: plan.name, status: 'created', created: zero, skipped: null, message: 'Department set on 2 existing room(s).', departments_set: 2 }],
        totals: zero, departments_set: 2, agents_created: 0 }) });
    });
  } });
  await page.locator('.hqLive').getByRole('button', { name: /Office tools/ }).click();
  await page.locator('.hqLive').getByRole('button', { name: 'Set up my campus' }).click();
  const dialog = page.locator('.hqScDialog[role=dialog]');
  await expect(dialog.getByTestId('sc-fills')).toContainText('Existing campus rooms that get their department: 2', { timeout: 20_000 });
  await expect(dialog).toContainText('Nothing new to create.');
  const confirm = dialog.getByRole('button', { name: 'Fill departments' });
  await expect(confirm).toBeEnabled();
  await dialog.getByRole('button', { name: /Show details|Details/ }).first().click();
  await expect(dialog.getByTestId('sc-room-fill').first()).toContainText('will be linked to legal');
  if (process.env.HQ_SHOTS_DIR) await page.screenshot({ path: `${process.env.HQ_SHOTS_DIR}/starter-campus-fill-plan.png` });
  await confirm.click();
  await expect(dialog).toContainText('Departments set on existing rooms: 2.');
  if (process.env.HQ_SHOTS_DIR) await page.screenshot({ path: `${process.env.HQ_SHOTS_DIR}/starter-campus-fill-done.png` });
  expect(confirmed).toMatchObject({ input_mode: 'click', buildings: ['corporate-services'] });
});

// ------------------------------------------------------------------ sizes

const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const [w, h] of SIZES) {
  test(`meeting room inspector fits at ${w}x${h}`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await openRoom(page, 'Alpha Boardroom');
    await expect(page.locator('.hqSide [data-testid="room-meeting"]')).toBeVisible();
    await expect(page.locator('.hqSide [data-testid="room-module"]')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const box = await page.locator('.hqSide').boundingBox();
    expect(box && box.x >= -1 && box.x + box.width <= w + 1).toBe(true);
    const dir = process.env.HQ_SHOTS_DIR;
    if (dir) await page.screenshot({ path: `${dir}/meeting-room-${w}x${h}.png` });
  });
  test(`room list marks the meeting room at ${w}x${h}`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await open(page);
    await listView(page);
    const row = page.locator('.hqListRooms li', { hasText: 'Alpha Boardroom' });
    await expect(row.locator('[data-testid="list-in-meeting"]')).toBeVisible();
    await row.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const box = await row.boundingBox();
    expect(box && box.x >= -1 && box.x + box.width <= w + 1).toBe(true);
    const dir = process.env.HQ_SHOTS_DIR;
    if (dir) await page.screenshot({ path: `${dir}/list-in-meeting-${w}x${h}.png` });
  });
  test(`new department inspector fits at ${w}x${h}`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await openRoom(page, 'Alpha Studio');
    await expect(page.locator('.hqSide [data-testid="room-module"]')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const dir = process.env.HQ_SHOTS_DIR;
    if (dir) await page.screenshot({ path: `${dir}/department-${w}x${h}.png` });
  });
}
