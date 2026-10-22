import { expect, test, type Page } from '@playwright/test';
import { mockHq, type HqMockOptions } from './support/hqCampusApi';
import { useSavedLocale } from './support/workspaceApi';

// ANUM MINE headquarters (docs/ANUM_MINE_HQ_EXTENSION_POINTS.md): the campus of workspaces, the
// building cutaway, the accessible list, the no-WebGL fallback and the navigation command. The API is
// mocked at http://localhost:8000 like the other specs; every name and number below is plainly fake.
const hq = (page: Page) => page.locator('.hqLive');
const wide = (page: Page) => (page.viewportSize()?.width ?? 0) >= 1024;

async function openOffice(page: Page, options: HqMockOptions = { campus: 'api', commerce: 'connected', meetings: 'live' }) {
  await page.addInitScript(() => { try { localStorage.setItem('anum.hq.light', 'day'); } catch { /* storage blocked */ } });
  await mockHq(page, options);
  await page.goto('/#office-preview');
  await expect(hq(page).getByRole('heading', { level: 1 })).toBeVisible();
}

async function enterFleetBuilding(page: Page) {
  await hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click();
  await hq(page).getByRole('button', { name: 'Open Al Noor Fleet HQ' }).click();
  await expect(hq(page).getByRole('heading', { level: 2, name: 'Al Noor Fleet HQ' })).toBeVisible();
}

test('the campus shows one building per workspace with real counts, and opens a building', async ({ page }) => {
  await openOffice(page);
  const canvas = hq(page).locator('canvas.hqCanvas');
  await expect(canvas).toBeVisible();
  for (const name of ['Executive HQ', 'Al Noor Fleet HQ', 'Tyre Pulse HQ', 'ANUM360 Commerce', 'Personal Office', 'Legal and Compliance']) {
    await expect(hq(page).getByRole('button', { name: new RegExp('^' + name + '\\.') })).toBeAttached();
  }
  // Counts come from the campus data; a building without presence says so.
  await expect(hq(page).getByRole('button', { name: /^Executive HQ\. 5 agents, 2 working/ })).toBeAttached();
  await expect(hq(page).getByRole('button', { name: /^Tyre Pulse HQ\. Presence unavailable/ })).toBeAttached();
  const totals = hq(page).getByRole('region', { name: 'Total agents' });
  await expect(totals).toContainText('4 of 6 buildings report presence');
  await expect(totals.getByRole('img')).toHaveAccessibleName('22 agents by status');
  // The concept toggle stays, visibly separate.
  await expect(page.getByRole('group', { name: 'Office presentation mode' })).toBeVisible();

  await hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click();
  const detail = hq(page).getByRole('complementary', { name: 'About Al Noor Fleet HQ' });
  await expect(detail).toContainText('9 agents, 4 working, 1 in meetings');
  await detail.getByRole('button', { name: 'Open Al Noor Fleet HQ' }).click();
  await expect(hq(page).getByRole('button', { name: 'Back to campus' })).toBeVisible();
  await hq(page).getByRole('button', { name: 'Back to campus' }).click();
  await expect(hq(page).getByRole('button', { name: /^Tyre Pulse HQ\./ })).toBeAttached();
});

test('inside a building: floors, rooms, desks, agent cards and the module panels', async ({ page }) => {
  await openOffice(page);
  await enterFleetBuilding(page);
  const counters = hq(page).getByRole('list', { name: 'Building totals' });
  await expect(counters).toContainText('2Floors');
  await expect(counters).toContainText('9Rooms');
  await expect(counters).toContainText('1Live meetings');
  // Rooms of the saved layout, an agent card per assigned desk with its real status.
  await expect(hq(page).getByRole('button', { name: /^Tyre Analysis/ })).toBeVisible();
  // Agent cards sit on the scene on wide screens; a phone-sized stage hides the ones that would not fit, and the room panel lists the same desks.
  if (wide(page)) {
    await expect(hq(page).getByRole('button', { name: /Fleet Planner/ })).toContainText('Working');
    await expect(hq(page).getByRole('button', { name: /WhatsApp Concierge/ })).toContainText('In meetings');
    // A private agent shows no name.
    await expect(hq(page).getByRole('button', { name: /Private agent/ })).toContainText('Unknown');
  }
  // A room opens its panel; an agent opens the workstation panel with the open task.
  await hq(page).getByRole('button', { name: /^Tyre Analysis/ }).click();
  const room = hq(page).getByRole('complementary', { name: 'Room Tyre Analysis' });
  await expect(room).toContainText('Capacity 4');
  await room.getByRole('button', { name: /Fleet Planner/ }).click();
  const agent = hq(page).getByRole('complementary', { name: 'Agent Fleet Planner' });
  // The desk's real work record: task, run phase and checkpoint, steps, approval, error and document.
  await expect(agent.getByText('Plan October tyre rotation')).toBeVisible();
  await agent.getByText('Plan October tyre rotation').click();
  await expect(agent).toContainText('Run running, phase executing');
  await expect(agent).toContainText('checkpoint 3');
  await expect(agent).toContainText('Prepared the rotation plan');
  await expect(agent.getByRole('heading', { name: 'Waiting for approval' })).toBeVisible();
  await expect(agent).toContainText('send_email');
  await expect(agent).toContainText('The tyre data source timed out.');
  await expect(agent).toContainText('Fleet tyre register');
  await expect(agent.getByRole('link', { name: 'Open agent profile' })).toHaveAttribute('href', /agents\/team\/agent\/agent_fleet/);
  await agent.getByRole('button', { name: 'Close panel' }).click();
  // Module panels: commerce is connected, tyre is not.
  await expect(hq(page).getByRole('region', { name: 'Live operations' })).toContainText('17Orders today');
  await expect(hq(page).getByRole('region', { name: 'Current flow' })).toContainText('7Waiting to ship');
  const tyre = hq(page).getByRole('region', { name: 'Tyre fleet' });
  await expect(tyre).toContainText('Not connected');
  await expect(tyre.getByRole('link', { name: 'Set it up' })).toHaveAttribute('href', '#tyre');
  await expect(hq(page).getByRole('region', { name: 'Team status' })).toContainText('Working');
  // Floors.
  await hq(page).getByLabel('Choose a floor').selectOption({ label: 'First floor' });
  await expect(hq(page).getByRole('button', { name: /^Finance/ })).toBeVisible();
  await expect(hq(page).getByRole('button', { name: /^Tyre Analysis/ })).toHaveCount(0);
  // Evidence older than the server's window reads as stale; a queued task reads as queued, never as working.
  if (wide(page)) {
    await expect(hq(page).getByRole('button', { name: /Lead Finder/ })).toContainText('(stale)');
    await expect(hq(page).getByRole('button', { name: /Invoice Checker/ })).toContainText('Queued');
  }
});

test('without the campus endpoint only the open workspace has counts, and nothing is invented', async ({ page }) => {
  await openOffice(page, { campus: 'missing', commerce: 'none', meetings: 'none' });
  await expect(hq(page).getByRole('button', { name: /^Tyre Pulse HQ\. Presence unavailable/ })).toBeAttached();
  await expect(hq(page).getByRole('button', { name: /^ANUM360 Commerce\. Presence unavailable/ })).toBeAttached();
  await expect(hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\. \d+ agents/ })).toBeAttached();
  await expect(hq(page).getByRole('region', { name: 'Total agents' })).toContainText('1 of 4 buildings report presence');
  await enterFleetBuilding(page);
  await expect(hq(page).getByRole('region', { name: 'Live operations' })).toContainText('Not connected');
});

test('a private agent shows no name and no work, and says why', async ({ page }) => {
  await openOffice(page);
  await enterFleetBuilding(page);
  await hq(page).getByRole('button', { name: /^Customer Care/ }).click();
  const room = hq(page).getByRole('complementary', { name: 'Room Customer Care' });
  await room.getByRole('button', { name: /Private agent/ }).click();
  const panel = hq(page).getByRole('complementary', { name: 'Agent Private agent' });
  await expect(panel).toContainText('This agent is private to someone else, so its work is not shown.');
  await expect(panel.getByRole('link', { name: 'Open agent profile' })).toHaveCount(0);
});

test('without the presence endpoint the browser reads tasks and meetings, and says what it cannot tell', async ({ page }) => {
  await openOffice(page, { campus: 'missing', presence: 'missing', meetings: 'failing', commerce: 'none' });
  await enterFleetBuilding(page);
  const team = hq(page).getByRole('region', { name: 'Team status' });
  await expect(team).toContainText('Working');
  // Meeting presence could not be read, so it shows "-" and never a zero.
  await expect(team.locator('li', { hasText: 'In meetings' })).toContainText('-');
  await hq(page).getByRole('button', { name: /^Tyre Analysis/ }).click();
  await hq(page).getByRole('complementary', { name: 'Room Tyre Analysis' }).getByRole('button', { name: /Fleet Planner/ }).click();
  const agent = hq(page).getByRole('complementary', { name: 'Agent Fleet Planner' });
  await expect(agent).toContainText('Plan October tyre rotation');
  await expect(agent).toContainText('The detailed work record is not available on this server yet.');
});

test('an owner sets the building type, a stale save is explained', async ({ page }) => {
  await page.addInitScript(() => { try { localStorage.setItem('anum.hq.light', 'day'); } catch { /* storage blocked */ } });
  const mock = await mockHq(page, { campus: 'api', commerce: 'none', profile: 'ok' });
  await page.goto('/#office-preview');
  await hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click();
  const detail = hq(page).getByRole('complementary', { name: 'About Al Noor Fleet HQ' });
  const form = detail.getByRole('form', { name: 'Building type' });
  await form.getByLabel('Type').selectOption('executive');
  await form.getByLabel('Icon').selectOption('landmark');
  await form.getByRole('button', { name: 'Save building type' }).click();
  await expect(form.getByText('Saved.')).toBeVisible();
  expect(mock.profileCalls).toEqual([{ kind: 'executive', icon: 'landmark', expected_revision: 1 }]);
  // A building nobody classified says so, and only an owner of that workspace can set it.
  await hq(page).getByRole('button', { name: 'Close details' }).click();
  await hq(page).getByRole('button', { name: 'Reset view' }).click();
  await hq(page).getByRole('button', { name: /^Personal Office\./ }).click();
  await expect(hq(page).getByRole('complementary', { name: 'About Personal Office' })).toContainText('Building type not set yet');
  await expect(hq(page).getByRole('complementary', { name: 'About Personal Office' })).toContainText('Only a workspace owner can set the building type.');
});

test('a stale building type save asks the owner to reopen the card', async ({ page }) => {
  await page.addInitScript(() => { try { localStorage.setItem('anum.hq.light', 'day'); } catch { /* storage blocked */ } });
  await mockHq(page, { campus: 'api', commerce: 'none', profile: 'conflict' });
  await page.goto('/#office-preview');
  await hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click();
  await hq(page).getByRole('form', { name: 'Building type' }).getByRole('button', { name: 'Save building type' }).click();
  await expect(hq(page).getByRole('alert')).toContainText('Someone changed this just now');
});

test('the concept preview stays a separate, labelled mode', async ({ page }) => {
  await openOffice(page);
  await page.getByRole('button', { name: 'Concept preview' }).click();
  await expect(page.locator('.anumOfficeTour')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Authenticated office' })).toBeVisible();
  await page.getByRole('button', { name: 'Authenticated office' }).click();
  await expect(hq(page).getByRole('heading', { level: 1 })).toBeVisible();
});

test('a refused campus read explains who can fix it', async ({ page }) => {
  await openOffice(page, { campus: 'denied' });
  await expect(hq(page).getByRole('alert')).toContainText('Ask a workspace owner to give you read access');
});

test('a workspace with no saved layout says what to do next', async ({ page }) => {
  await openOffice(page, { campus: 'api', layout: 'none' });
  await hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click();
  await hq(page).getByRole('button', { name: 'Open Al Noor Fleet HQ' }).click();
  await expect(hq(page).getByRole('heading', { level: 2, name: 'This workspace has no office layout yet' })).toBeVisible();
  await expect(hq(page).getByRole('button', { name: 'Manage spaces' })).toBeVisible();
});

test('List view is an accessible list of every building and room, with search', async ({ page }) => {
  await openOffice(page);
  await hq(page).getByRole('button', { name: 'List' }).click();
  const list = hq(page).getByRole('region', { name: 'All buildings and rooms' });
  await expect(list.getByRole('heading', { level: 3 })).toHaveCount(6);
  await expect(list.getByRole('heading', { level: 3 }).first()).toHaveText('Executive HQ');
  await expect(list.getByText('This building is not available to you right now.')).toBeVisible();
  await expect(list.getByRole('heading', { level: 4, name: 'Ground floor' })).toBeVisible();
  await list.getByRole('searchbox').fill('finance');
  await expect(list.getByRole('button', { name: /Finance/ })).toBeVisible();
  await expect(list.getByRole('heading', { level: 3 })).toHaveCount(1);
  await list.getByRole('searchbox').fill('zzzz');
  await expect(list.getByText('Nothing matches your search.')).toBeVisible();
  await list.getByRole('button', { name: 'Clear search' }).click();
  // A room in the list opens it in the building view.
  await list.getByRole('button', { name: /Tyre Analysis/ }).click();
  await expect(hq(page).getByRole('complementary', { name: 'Room Tyre Analysis' })).toBeVisible();
});

test('the scene is reachable from the keyboard', async ({ page }) => {
  await openOffice(page);
  const card = hq(page).getByRole('button', { name: /^Personal Office\./ });
  await card.focus();
  await page.keyboard.press('Enter');
  await expect(hq(page).getByRole('complementary', { name: 'About Personal Office' })).toBeVisible();
  const stage = hq(page).getByRole('group', { name: 'ANUM MINE campus, 3D scene' });
  await stage.focus();
  const canvas = hq(page).locator('canvas.hqCanvas');
  await page.keyboard.press('q');
  await page.keyboard.press('+');
  await page.keyboard.press('0');
  await expect(canvas).toBeVisible();
  await expect(hq(page).getByRole('button', { name: 'Next building' })).toBeEnabled();
});

test('without WebGL the list and the 2D floor plan take over', async ({ page }) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (kind: string, ...rest: unknown[]) {
      if (kind === 'webgl' || kind === 'webgl2' || kind === 'experimental-webgl') return null;
      return (original as (...a: unknown[]) => unknown).call(this, kind, ...rest);
    } as typeof original;
  });
  await openOffice(page);
  await expect(hq(page).getByText(/3D is not available in this browser/)).toBeVisible();
  await expect(hq(page).getByRole('region', { name: 'All buildings and rooms' })).toBeVisible();
  await hq(page).getByRole('button', { name: 'Open Al Noor Fleet HQ' }).click();
  await hq(page).getByRole('button', { name: 'Top' }).click();
  await expect(hq(page).locator('.hqPlanStage canvas')).toBeVisible();
});

test('a lost GPU context is announced and the scene comes back', async ({ page }) => {
  await openOffice(page);
  const canvas = hq(page).locator('canvas.hqCanvas');
  await expect(canvas).toBeVisible();
  await page.evaluate(() => {
    const el = document.querySelector('canvas.hqCanvas') as HTMLCanvasElement;
    const gl = el.getContext('webgl') as WebGLRenderingContext;
    const ext = gl.getExtension('WEBGL_lose_context') as { loseContext(): void; restoreContext(): void };
    (window as unknown as { __hqExt: typeof ext }).__hqExt = ext;
    ext.loseContext();
  });
  await expect(hq(page).getByText('Restoring the 3D view...')).toBeVisible();
  await page.evaluate(() => (window as unknown as { __hqExt: { restoreContext(): void } }).__hqExt.restoreContext());
  await expect(hq(page).getByText('Restoring the 3D view...')).toHaveCount(0);
  await expect(hq(page).getByRole('button', { name: /^Executive HQ\./ })).toBeAttached();
});

test('office tools open the existing panels and Escape closes them', async ({ page }) => {
  await openOffice(page);
  await hq(page).getByRole('button', { name: 'Office tools' }).click();
  await hq(page).getByRole('button', { name: 'Manage spaces' }).click();
  const dialog = hq(page).getByRole('dialog', { name: 'Office tools' });
  await expect(dialog.getByRole('heading', { name: 'Office Space Management' })).toBeVisible();
  // One close control, not two stacked on each other.
  await expect(dialog.getByRole('button', { name: /^Close/ })).toHaveCount(1);
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
});

test('the assistant can move the view with a navigation command', async ({ page }) => {
  await openOffice(page);
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('anum:hq-navigate', { detail: { building: 'Al Noor Fleet HQ', floor: 'First floor', room: 'Finance' } })));
  await expect(hq(page).getByRole('complementary', { name: 'Room Finance' })).toBeVisible();
  await expect(hq(page).getByLabel('Choose a floor')).toHaveValue('flr_1');
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('anum:hq-navigate', { detail: { agent: 'Fleet Planner' } })));
  await expect(hq(page).getByRole('complementary', { name: 'Agent Fleet Planner' })).toBeVisible();
  await expect(hq(page).getByLabel('Choose a floor')).toHaveValue('flr_0');
});

test('Arabic: the headquarters reads right to left', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await openOffice(page);
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('region', { name: 'مقر ANUM MINE' })).toBeVisible();
  await expect(hq(page).getByRole('button', { name: 'قائمة' })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const [width, height] of SIZES) {
  test(`@gate headquarters fits ${width}x${height} on the campus and in a building`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await openOffice(page);
    await expect(hq(page).locator('.hqBuildingCard').first()).toBeAttached();
    const shot = process.env.HQ_SHOTS;
    const overflow = async () => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(await overflow()).toBeLessThanOrEqual(0);
    if (shot) await page.screenshot({ path: `${shot}/campus-${width}x${height}.png` });
    await enterFleetBuilding(page);
    await expect(hq(page).locator('.hqRoomCard').first()).toBeAttached();
    expect(await overflow()).toBeLessThanOrEqual(0);
    // No control is clipped by the scene: every visible toolbar button sits inside the viewport width.
    const clipped = await hq(page).locator('.hqToolbar button, .hqTools button').evaluateAll((els) => els.filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && (r.right > window.innerWidth + 1 || r.left < -1); }).length);
    expect(clipped).toBe(0);
    if (shot) await page.screenshot({ path: `${shot}/building-${width}x${height}.png` });
    await hq(page).getByRole('button', { name: /^Tyre Analysis/ }).click();
    if (shot) await page.screenshot({ path: `${shot}/room-${width}x${height}.png` });
    await hq(page).getByRole('button', { name: 'List' }).click();
    expect(await overflow()).toBeLessThanOrEqual(0);
    if (shot) await page.screenshot({ path: `${shot}/list-${width}x${height}.png` });
  });
}

test('draw time stays low while the camera moves (measured on this machine)', async ({ page }) => {
  await openOffice(page);
  const canvas = hq(page).locator('canvas.hqCanvas');
  await expect(canvas).toBeVisible();
  const box = (await canvas.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  const frames = await page.evaluate(async () => {
    const el = document.querySelector('canvas.hqCanvas') as HTMLCanvasElement;
    const times: number[] = [];
    let last = performance.now();
    for (let i = 0; i < 90; i++) {
      el.dispatchEvent(new WheelEvent('wheel', { deltaY: i % 2 ? 40 : -40, bubbles: true, cancelable: true }));
      await new Promise((r) => requestAnimationFrame(r));
      const now = performance.now();
      times.push(now - last);
      last = now;
    }
    return { avg: times.reduce((a, b) => a + b, 0) / times.length, max: Math.max(...times), drawMs: el.dataset.drawMs, triangles: el.dataset.triangles, drawn: el.dataset.drawn, culled: el.dataset.culled, dpr: el.dataset.dpr };
  });
  console.log('HQ frame measurement', JSON.stringify(frames));
  expect(Number(frames.triangles)).toBeGreaterThan(1000);
  expect(Number(frames.drawMs)).toBeLessThan(120);
});
