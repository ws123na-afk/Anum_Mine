import { expect as baseExpect, test, type Page } from '@playwright/test';
const expect = baseExpect.configure({ timeout: 15_000 });
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { mockFlow } from './support/hqFlowApi';
import { mockProvisioning } from './support/hqProvisioningApi';
import { MASTER_TOTALS, mockFirstRun, type FirstRunMock } from './support/hqFirstRunApi';

// First run of the headquarters: a workspace with no buildings offers to create the campus inside the scene, and the campus, its
// floors and its rooms appear without a reload (docs/ANUM_MINE_STARTER_CAMPUS.md#first-run, docs/ANUM_MINE_FLOOR_OVERVIEW.md).
// The mock starts EMPTY and a confirm fills the registry with the real master list (8 buildings, 18 floors, 62 rooms, 220 desks).
// Set HQ_SHOTS_DIR to keep a screenshot of every step.
test.use({ actionTimeout: 30_000 });
test.describe.configure({ timeout: 120_000 });
test.beforeEach(() => { /* the headquarters scene is heavy: give every expectation room on a busy runner */ });
const empty = { ...hqRegistry(TENANT_A, { rooms: 0, desks: 0 }), buildings: [], floors: [] };
let first: FirstRunMock;
let streamEvents: string[] = [];

async function open(page: Page, options: { locale?: 'en' | 'ar'; prebuilt?: boolean; viewport?: { width: number; height: number } } = {}) {
  streamEvents = [];
  if (options.viewport) await page.setViewportSize(options.viewport);
  // The shell's one event stream: it answers with whatever events the test has queued, then ends (the page reconnects).
  await page.route('**/api/v1/events/stream', (route) => route.fulfill({
    status: 200, headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
    body: streamEvents.map((type, i) => `id: ${i + 1}\ndata: ${JSON.stringify({ id: `evt_${i + 1}`, type, tenant_id: 'tenant_local', workspace_id: 'workspace_foundation' })}\n\n`).join(''),
  }));
  await openHq(page, {
    registry: empty, locale: options.locale,
    beforeGoto: async (p) => { await mockFlow(p); await mockProvisioning(p); first = await mockFirstRun(p, { prebuilt: options.prebuilt }); },
  });
  await expect(page.locator('.hqLive').getByRole('heading', { level: 1 })).toBeVisible();
}
const hq = (page: Page) => page.locator('.hqLive');
const panel = (page: Page) => page.getByTestId('hq-first-run');
const createButton = (page: Page) => page.getByTestId('hq-first-run-create');
const posts = () => first.requests.filter((r) => r.method === 'POST');
async function shot(page: Page, name: string) {
  const dir = process.env.HQ_SHOTS_DIR;
  if (!dir) return;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: join(dir, `${name}.png`) });
}
async function noOverflow(page: Page, selector: string | null = '.hqFirstRun') {
  const problems = await page.evaluate((sel) => {
    const out: string[] = [];
    if (document.documentElement.scrollWidth > window.innerWidth) out.push(`page scrolls sideways (${document.documentElement.scrollWidth} > ${window.innerWidth})`);
    const root = sel ? document.querySelector(sel) as HTMLElement | null : null;
    if (!root) return out;
    const box = root.getBoundingClientRect();
    if (box.left < -1 || box.right > window.innerWidth + 1) out.push('the panel leaves the screen sideways');
    for (const el of root.querySelectorAll<HTMLElement>('button, li, h2, p')) {
      const r = el.getBoundingClientRect();
      if (r.width && (r.right > box.right + 1 || r.left < box.left - 1)) out.push(`clipped: ${el.textContent?.slice(0, 30)}`);
    }
    return out;
  }, selector);
  expect(problems).toEqual([]);
}
async function enterHq(page: Page, name = 'ANUM HQ') {
  // Building names are stored in one language (the registry has no Arabic column), so the name is the same in both.
  // dispatchEvent: on a narrow stage the campus map overlay can sit over the label, which is only a pointer matter.
  await hq(page).getByRole('button', { name: new RegExp(`^${name}\\.`) }).dispatchEvent('click');
  await hq(page).getByRole('button', { name: new RegExp(`^(Open|فتح) \u2068?${name}\u2069?$`) }).click();
  await expect(hq(page).getByRole('heading', { level: 2, name })).toBeVisible();
}

test('an empty workspace shows the first-run panel with the real numbers, and nothing is written before the click', async ({ page }) => {
  await open(page);
  const p = panel(page);
  await expect(p.getByRole('heading', { name: 'Create your campus' })).toBeVisible({ timeout: 20_000 });
  await expect(p.getByTestId('fr-buildings')).toHaveText(String(MASTER_TOTALS.buildings));
  await expect(p.getByTestId('fr-floors')).toHaveText(String(MASTER_TOTALS.floors));
  await expect(p.getByTestId('fr-rooms')).toHaveText(String(MASTER_TOTALS.rooms));
  await expect(p.getByTestId('fr-desks')).toHaveText(String(MASTER_TOTALS.desks));
  expect([MASTER_TOTALS.buildings, MASTER_TOTALS.floors, MASTER_TOTALS.rooms, MASTER_TOTALS.desks]).toEqual([8, 18, 62, 220]);
  await expect(p).toContainText('8 buildings, 18 floors, 62 rooms and 220 empty desks');
  await expect(p.getByTestId('fr-hash')).toContainText('Plan fingerprint:');
  await expect(p).toContainText('Only a click on the button confirms this');
  await p.getByRole('button', { name: 'Preview the list' }).click();
  const list = p.getByRole('list', { name: 'Preview the list' });
  await expect(list.getByRole('listitem')).toHaveCount(8);
  await expect(list.getByRole('listitem').first()).toContainText('ANUM HQ');
  await expect(list.getByRole('listitem').first()).toContainText('Floors 5, rooms 17, desks 39');
  // Typing or pressing keys is not a confirmation.
  await page.keyboard.type('yes create it');
  await page.keyboard.press('Enter');
  expect(posts()).toHaveLength(0);
  expect(first.requests.every((r) => r.method === 'GET')).toBe(true);
  await shot(page, 'first-run-1440');
});

test('one click confirms exactly the plan on screen, and the campus appears without a reload', async ({ page }) => {
  await open(page);
  await expect(createButton(page)).toBeEnabled({ timeout: 20_000 });
  const shown = (await panel(page).getByTestId('fr-hash').innerText()).replace(/^.*Plan fingerprint: (\S+ \S+)\..*$/s, '$1');
  await page.evaluate(() => { (window as unknown as { __kept: boolean }).__kept = true; });
  await createButton(page).click();
  await expect(panel(page)).toHaveCount(0, { timeout: 20_000 });
  expect(posts()).toHaveLength(1);
  const body = posts()[0].body as { payload_hash: string; input_mode: string; buildings: string[]; timezone: string };
  expect(body.input_mode).toBe('click');
  expect(body.buildings).toHaveLength(8);
  expect(`${body.payload_hash.slice(0, 8)} ${body.payload_hash.slice(8, 16)}`).toBe(shown);
  // The page was not reloaded, and everything is on screen: the note, all eight buildings on the campus.
  expect(await page.evaluate(() => (window as unknown as { __kept?: boolean }).__kept)).toBe(true);
  await expect(page.getByTestId('hq-campus-note')).toContainText('Your campus is ready: 8 buildings, 18 floors, 62 rooms, 220 desks');
  for (const name of ['ANUM HQ', 'ANUM 360 Commerce', 'Tyre Pulse', 'Corporate Services', 'Knowledge & AI', 'New Ventures', 'Personal Office', 'Career & Employment']) {
    await expect(hq(page).getByRole('button', { name: new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\.`) })).toBeAttached({ timeout: 20_000 });
  }
  await shot(page, 'campus-after-create-1440');
});

test('the building view shows every room of the floor, and the floor overview labels each one', async ({ page }) => {
  await open(page, { prebuilt: true });
  await enterHq(page);
  await hq(page).getByRole('button', { name: /^Floor 2/ }).first().click();
  const stage = hq(page).locator('.hqBuilding .hqStage');
  await expect(hq(page).locator('.hqBuilding .hqRoomCard')).toHaveCount(5);
  // Every room card sits inside the picture: the camera frames the whole 38 m floor, not one room of it.
  const box = (await stage.boundingBox())!;
  for (const card of await hq(page).locator('.hqBuilding .hqRoomCard').all()) {
    const b = (await card.boundingBox())!;
    expect(b.x + b.width / 2, 'card centre inside the stage').toBeGreaterThan(box.x);
    expect(b.x + b.width / 2).toBeLessThan(box.x + box.width);
    expect(b.y + b.height / 2).toBeGreaterThan(box.y);
    expect(b.y + b.height / 2).toBeLessThan(box.y + box.height);
  }
  await shot(page, 'building-floor2-1440');
  const toggle = hq(page).getByTestId('hq-overview');
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  const cards = hq(page).locator('.hqOverviewCard');
  await expect(cards).toHaveCount(5);
  for (const name of ['Command Center', 'AI Organization', 'Operations Office', 'Assets', 'Suppliers']) {
    const card = cards.filter({ hasText: name });
    await expect(card).toBeVisible();
    await expect(card).toContainText(/Desks \d+, agents \d+/);
  }
  await expect(cards.filter({ hasText: 'AI Organization' })).toContainText('Office');
  await expect(cards.filter({ hasText: 'AI Organization' })).toContainText('Desks 8, agents 0');
  const rooms = page.getByTestId('hq-floor-rooms');
  await expect(rooms.getByRole('button')).toHaveCount(6); // five rooms and the way back
  await expect(rooms).toContainText('Rooms 5, desks 26, agents seated 0.');
  await shot(page, 'floor-overview-1440');
  // Picking a room selects it and keeps the whole floor in view.
  const distance = await hq(page).locator('.hqSceneWrap').getAttribute('data-camera-distance');
  await cards.filter({ hasText: 'Command Center' }).click();
  await expect(page.getByRole('complementary', { name: 'Room Command Center' })).toBeVisible();
  await expect(hq(page).locator('.hqSceneWrap')).toHaveAttribute('data-camera-distance', distance!);
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  // The floor rail reaches the same mode, and Escape leaves it.
  const rail = page.getByTestId('hq-rail-overview');
  await expect(rail).toHaveAttribute('aria-pressed', 'true');
  await rail.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await expect(cards).toHaveCount(0);
  await rail.click();
  await expect(cards).toHaveCount(5);
  await hq(page).locator('.hqBuilding .hqStage').focus();
  await page.keyboard.press('Escape');
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
});

test('the floor overview works with the floors, the fold control, the navigation strip and the Inside camera', async ({ page }) => {
  await open(page, { prebuilt: true });
  await enterHq(page);
  const toggle = hq(page).getByTestId('hq-overview');
  await toggle.click();
  const cards = hq(page).locator('.hqOverviewCard');
  await expect(cards).toHaveCount(3); // the basement: facilities, security, parking
  // Another floor keeps the mode and shows that floor's rooms.
  await hq(page).getByRole('button', { name: /^Floor 1/ }).first().click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await expect(cards).toHaveCount(4);
  await expect(cards.filter({ hasText: 'Reception' })).toContainText('Reception');
  // Folding the side panels gives the scene more width: the picture is framed again.
  const wrap = hq(page).locator('.hqSceneWrap');
  const before = await wrap.getAttribute('data-camera-distance');
  const fold = hq(page).getByRole('button', { name: /Hide side panels/ });
  if (await fold.count()) { await fold.click(); await expect.poll(() => wrap.getAttribute('data-camera-distance')).not.toBe(before); }
  await expect(cards).toHaveCount(4);
  // The navigation strip is still there.
  await expect(hq(page).locator('.hqStrip, [data-testid="hq-strip"], .hqStripToggle').first()).toBeAttached();
  // Inside a room leaves the overview, Escape comes back out.
  await cards.filter({ hasText: 'Meeting Room A' }).click();
  await hq(page).getByTestId('hq-inside').click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await expect(wrap).toHaveAttribute('data-camera-distance', /./);
  await shot(page, 'overview-then-inside-1440');
});

test('a person who cannot confirm sees who can, and the button is disabled', async ({ page }) => {
  await open(page);
  first.setCanConfirm(false);
  await page.getByRole('button', { name: 'Refresh' }).first().click().catch(() => undefined);
  await page.reload();
  await expect(panel(page).getByRole('heading', { name: 'Create your campus' })).toBeVisible({ timeout: 20_000 });
  await expect(createButton(page)).toBeDisabled();
  await expect(panel(page)).toContainText('Only a workspace owner can create the campus');
  await expect(panel(page)).toContainText('workspace:manage');
  await panel(page).getByRole('button', { name: 'Preview the list' }).click();
  await expect(panel(page).getByRole('list', { name: 'Preview the list' }).getByRole('listitem')).toHaveCount(8);
  expect(posts()).toHaveLength(0);
  await shot(page, 'first-run-denied-1440');
});

test('loading, a failed read with retry, and a refused read each say what to do', async ({ page }) => {
  let release: () => void = () => undefined;
  await open(page);
  first.holdPreview(new Promise<void>((resolve) => { release = resolve; }));
  await page.reload();
  await expect(panel(page).getByRole('status')).toContainText('Reading the plan...');
  await shot(page, 'first-run-loading-1440');
  first.setPreviewFailure(500);
  release();
  await expect(panel(page).getByRole('alert')).toContainText('The plan could not be loaded');
  await shot(page, 'first-run-error-1440');
  first.setPreviewFailure(null);
  await panel(page).getByRole('button', { name: 'Try again' }).click();
  await expect(createButton(page)).toBeEnabled();
  first.setPreviewFailure(403);
  await page.reload();
  await expect(panel(page).getByRole('alert')).toContainText('You cannot read the office in this workspace');
  await expect(panel(page).getByRole('button', { name: 'Manage spaces' })).toBeVisible();
});

test('a workspace that already has its campus never shows the panel', async ({ page }) => {
  await open(page, { prebuilt: true });
  await expect(hq(page).getByRole('button', { name: /^ANUM HQ\./ })).toBeAttached({ timeout: 20_000 });
  await expect(panel(page)).toHaveCount(0);
  expect(posts()).toHaveLength(0);
});

test('a stale plan is refused, the new plan is shown, and one more click creates it', async ({ page }) => {
  await open(page);
  await expect(createButton(page)).toBeEnabled({ timeout: 20_000 });
  first.staleOnce();
  await createButton(page).click();
  await expect(panel(page).getByRole('alert').first()).toContainText('The records changed since you opened this plan, so nothing was created');
  expect(first.buildingCount()).toBe(0);
  await expect(createButton(page)).toBeEnabled();
  await shot(page, 'first-run-stale-1440');
  await createButton(page).click();
  await expect(panel(page)).toHaveCount(0, { timeout: 20_000 });
  expect(first.buildingCount()).toBe(8);
  expect(posts()).toHaveLength(2);
});

test('a refused confirm says what failed and leaves the button to try again', async ({ page }) => {
  await open(page);
  await expect(createButton(page)).toBeEnabled({ timeout: 20_000 });
  first.setConfirmFailure(500, 'The service failed.');
  await createButton(page).click();
  await expect(panel(page).getByRole('alert')).toContainText('The campus could not be created. The service failed.');
  expect(first.buildingCount()).toBe(0);
  first.setConfirmFailure(null);
  await createButton(page).click();
  await expect(panel(page)).toHaveCount(0, { timeout: 20_000 });
  expect(first.buildingCount()).toBe(8);
});

test('a partly failed run keeps the panel, names the building, and a retry creates only what is missing', async ({ page }) => {
  await open(page);
  await expect(createButton(page)).toBeEnabled({ timeout: 20_000 });
  first.setFailBuilding(2);
  await createButton(page).click();
  const p = panel(page);
  await expect(p.getByRole('alert').first()).toContainText('7 of 8 buildings were created');
  await expect(p.getByRole('list', { name: 'Buildings that were not created' })).toContainText('A desk with that id already exists');
  expect(first.buildingCount()).toBe(7);
  await shot(page, 'first-run-partial-1440');
  // What exists is on the campus already; the panel stays for the retry.
  await expect(hq(page).getByRole('button', { name: /^ANUM HQ\./ })).toBeAttached();
  first.setFailBuilding(null);
  await p.getByRole('button', { name: 'Review what is left' }).click();
  await expect(p.getByTestId('fr-buildings')).toHaveText('1');
  await expect(createButton(page)).toHaveText('Create the missing buildings');
  await createButton(page).click();
  await expect(panel(page)).toHaveCount(0, { timeout: 20_000 });
  expect(first.buildingCount()).toBe(8);
  const last = posts()[posts().length - 1].body as { buildings: string[] };
  expect(posts()).toHaveLength(2);
  expect(last.buildings).toHaveLength(8); // the plan names every building; the server skips the ones that exist
});

test('another browser creating the campus shows up here without a reload', async ({ page }) => {
  await open(page);
  await expect(createButton(page)).toBeEnabled({ timeout: 20_000 });
  await page.evaluate(() => { (window as unknown as { __kept: boolean }).__kept = true; });
  first.createAllNow();
  streamEvents = ['office.layout_changed'];
  await expect(panel(page)).toHaveCount(0, { timeout: 40_000 });
  await expect(hq(page).getByRole('button', { name: /^ANUM HQ\./ })).toBeAttached({ timeout: 20_000 });
  expect(await page.evaluate(() => (window as unknown as { __kept?: boolean }).__kept)).toBe(true);
  expect(posts()).toHaveLength(0);
});

test('the same one-click flow is offered from the assistant as a card that still needs the click', async ({ page }) => {
  await open(page);
  await page.locator('#pa-input').fill('set up my campus');
  await page.locator('#pa-input').press('Enter');
  const card = page.getByTestId('campus-proposal');
  await expect(card).toBeVisible({ timeout: 20_000 });
  await expect(card).toContainText('8 buildings, 18 floors, 62 rooms and 220 empty desks');
  await expect(card).toContainText('Plan fingerprint');
  expect(posts()).toHaveLength(0);
  await page.locator('#pa-input').fill('yes');
  await page.locator('#pa-input').press('Enter');
  expect(posts()).toHaveLength(0);
  await card.getByTestId('campus-proposal-create').click();
  await expect(card).toContainText('Created 8 buildings, 18 floors, 62 rooms and 220 desks');
  expect(posts()).toHaveLength(1);
  expect((posts()[0].body as { input_mode: string }).input_mode).toBe('click');
  await expect(card.getByRole('button', { name: 'Show it in the office' })).toBeVisible();
});

test('Command: the request shows the campus card, a person who cannot confirm sees why, and Not now cancels', async ({ page }) => {
  await open(page);
  first.setCanConfirm(false);
  await page.goto('/#command');
  await page.getByRole('textbox', { name: /Command|request|What/i }).first().fill('create my campus');
  await page.keyboard.press('Enter');
  const card = page.getByTestId('campus-proposal');
  await expect(card).toBeVisible({ timeout: 20_000 });
  await expect(card.getByTestId('campus-proposal-create')).toBeDisabled();
  await expect(card).toContainText('Only a workspace owner can create the campus');
  await card.getByRole('button', { name: 'Not now' }).click();
  await expect(page.getByRole('region', { name: 'Create my campus' })).toContainText('Not created');
  expect(posts()).toHaveLength(0);
});

test('Arabic shows the panel right to left with Arabic text', async ({ page }) => {
  await open(page, { locale: 'ar' });
  const p = panel(page);
  await expect(p.getByRole('heading', { name: 'أنشئ حرمك' })).toBeVisible({ timeout: 20_000 });
  await expect(p.getByRole('button', { name: 'أنشئ حرمي' })).toBeEnabled();
  await expect(p).toContainText('220');
  await p.getByRole('button', { name: 'معاينة القائمة' }).click();
  await expect(p.getByRole('list', { name: 'معاينة القائمة' }).getByRole('listitem').first()).toContainText('مقر ANUM');
  expect(await page.evaluate(() => getComputedStyle(document.querySelector('.hqFrCard')!).direction)).toBe('rtl');
  await shot(page, 'first-run-ar-1440');
  await p.getByRole('button', { name: 'أنشئ حرمي' }).click();
  await expect(panel(page)).toHaveCount(0, { timeout: 20_000 });
  await expect(page.getByTestId('hq-campus-note')).toContainText('حرمك جاهز');
  await enterHq(page);
  await hq(page).getByTestId('hq-overview').click();
  await expect(hq(page).locator('.hqOverviewCard').first()).toContainText(/المكاتب \d+، الوكلاء \d+/);
  await shot(page, 'floor-overview-ar-1440');
});

const SIZES = [
  { name: '1440x900', width: 1440, height: 900 }, { name: '1366x768', width: 1366, height: 768 }, { name: '1024x768', width: 1024, height: 768 },
  { name: '768x1024', width: 768, height: 1024 }, { name: '390x844', width: 390, height: 844 },
] as const;
for (const size of SIZES) {
  for (const locale of ['en', 'ar'] as const) {
    test(`@gate first run, campus, building and floor overview fit ${size.name} in ${locale}`, async ({ page }) => {
      test.setTimeout(120_000);
      await open(page, { locale, viewport: size });
      await expect(createButton(page)).toBeEnabled({ timeout: 20_000 });
      await noOverflow(page);
      await shot(page, `first-run-${locale}-${size.name}`);
      await createButton(page).click();
      await expect(panel(page)).toHaveCount(0, { timeout: 20_000 });
      await expect(hq(page).locator('.hqSceneHost')).toBeVisible();
      await noOverflow(page, null);
      await shot(page, `campus-${locale}-${size.name}`);
      await enterHq(page);
      await noOverflow(page, null);
      await shot(page, `building-${locale}-${size.name}`);
      await hq(page).getByTestId('hq-overview').click();
      await expect(hq(page).locator('.hqOverviewCard').first()).toBeVisible();
      await noOverflow(page, null);
      // The stage has room: the whole floor is readable (at least 300 px of picture) on every size.
      const stage = (await hq(page).locator('.hqBuilding .hqStage').boundingBox())!;
      expect(stage.height).toBeGreaterThanOrEqual(300);
      await shot(page, `overview-${locale}-${size.name}`);
    });
  }
}
