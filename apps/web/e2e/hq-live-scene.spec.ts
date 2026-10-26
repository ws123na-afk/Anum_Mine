import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';
import { presenceItem, sseDelta } from './support/hqCampusApi';
import { flowItems, mockFlow, type FlowMock } from './support/hqFlowApi';

// The live scene of the headquarters (docs/ANUM_MINE_HQ_LIVE_SCENE.md): the presence stream with reconnects and a polling
// fallback, a visible link status, the eight states, the operations wall with real numbers, the room-filtered flow dock
// and the Inside camera. The API is mocked at http://localhost:8000; every name is plainly fake.
test.use({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, isMobile: false, hasTouch: false });
test.describe.configure({ timeout: 240_000 });

const SHOTS = process.env.HQ_LIVE_SHOTS;
const hq = (page: Page) => page.locator('.hqLive');
const chip = (page: Page) => page.getByTestId('hq-live-link');
let flow: FlowMock;

function registry() {
  const base = hqRegistry(TENANT_A, { rooms: 4, desks: 4, agentIds: ['agent_fleet', 'agent_leads', 'agent_care', 'agent_invoice'] });
  const rooms = base.rooms.map((r, i) => (i === 0 ? { ...r, name: 'Alpha Command Room', kind: 'office', team_department: 'operations' } : r));
  return { ...base, rooms };
}

type Open = Parameters<typeof openHq>[1] & { flowSources?: Record<string, string>; flowFail?: number | null };
async function open(page: Page, options: Open = {}) {
  const mock = await openHq(page, {
    registry: registry(), ...options,
    beforeGoto: async (p) => { flow = await mockFlow(p); if (options.flowSources) flow.set(flowItems(), options.flowSources); if (options.flowFail !== undefined) flow.failWith(options.flowFail); },
  });
  await expect(hq(page).getByRole('heading', { level: 1 })).toBeVisible();
  return mock;
}
async function enterBuilding(page: Page) {
  await expect(hq(page).locator('canvas.hqCanvas')).toBeVisible();
  await hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click({ timeout: 60_000 });
  await hq(page).getByRole('button', { name: 'Open Al Noor Fleet HQ' }).click();
  await expect(hq(page).getByRole('heading', { level: 2, name: 'Al Noor Fleet HQ' })).toBeVisible();
}
const agentCard = (page: Page, name: string) => hq(page).locator('.hqAgentCard', { hasText: name });
const noOverflow = async (page: Page) => expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);

test('live: one stream, the status says Live, and a delta changes an agent without a reload', async ({ page }) => {
  const mock = await open(page, { stream: 'live' });
  await expect(chip(page)).toHaveText('Live');
  await enterBuilding(page);
  await expect(agentCard(page, 'Fleet Planner')).toContainText('Working');
  await page.evaluate(() => { (window as unknown as { __kept: number }).__kept = 1; });
  await mock.stream.deliver({ changed: [presenceItem('agent_fleet', 'Fleet Planner', 'blocked')] });
  await expect(agentCard(page, 'Fleet Planner')).toContainText('Blocked');
  expect(await page.evaluate(() => (window as unknown as { __kept?: number }).__kept)).toBe(1);
  // The stream ended on purpose; the page came back with the last event id and there is still one stream at a time.
  await expect.poll(() => mock.stream.requests.at(-1)?.lastEventId).toBe('evt_2');
  expect(mock.stream.requests[0].lastEventId).toBeNull();
  expect(mock.stream.open()).toBeLessThanOrEqual(1);
  await expect(chip(page)).toHaveText('Live');
  if (SHOTS) { mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: `${SHOTS}/live-1440x900.png` }); }
});

test('a repeated delta changes nothing; a skipped one makes the page ask for a new snapshot', async ({ page }) => {
  const mock = await open(page, { stream: 'live' });
  await enterBuilding(page);
  const paused = presenceItem('agent_fleet', 'Fleet Planner', 'paused');
  await mock.stream.deliver({ changed: [paused], raw: sseDelta(1, [paused], 'evt_2') + sseDelta(1, [presenceItem('agent_fleet', 'Fleet Planner', 'working')], 'evt_2') });
  await expect(agentCard(page, 'Fleet Planner')).toContainText('Paused');
  // Delta 3 follows delta 0 of the next connection: a frame was lost. The page keeps what it has, reconnects and takes the new snapshot.
  const before = mock.stream.requests.length;
  await mock.stream.deliver({ changed: [presenceItem('agent_fleet', 'Fleet Planner', 'queued')], sequence: 3 });
  await expect(agentCard(page, 'Fleet Planner')).toContainText('Queued');
  expect(mock.stream.requests.length).toBeGreaterThan(before);
  await expect(chip(page)).toHaveText('Live');
});

test('the eight server states each show their own label and explanation', async ({ page }) => {
  const mock = await open(page, { stream: 'live' });
  await enterBuilding(page);
  await agentCard(page, 'Fleet Planner').click();
  const panel = hq(page).getByRole('complementary', { name: /Fleet Planner/ });
  const states: Array<[string, string, RegExp]> = [
    ['working', 'Working', /run is executing/], ['queued', 'Queued', /waiting for this agent/], ['meeting', 'In meetings', /live meeting/],
    ['waiting_approval', 'Waiting for approval', /person has to approve/], ['blocked', 'Blocked', /failed/], ['idle', 'Idle', /No active run/],
    ['paused', 'Paused', /paused by an owner/], ['unknown', 'Unknown', /No record backs/],
  ];
  for (const [state, label, help] of states) {
    if (state !== 'working') await mock.stream.deliver({ changed: [presenceItem('agent_fleet', 'Fleet Planner', state)] });
    await expect(panel.locator('.hqPill').first()).toContainText(label);
    await expect(panel.getByTestId('agent-state-help')).toHaveText(help);
  }
});

test('streaming blocked by a proxy: the page polls instead and says so', async ({ page }) => {
  await open(page, { stream: 'blocked' });
  await expect(chip(page)).toHaveText('Checking every 10 s', { timeout: 20_000 });
  await enterBuilding(page);
  await expect(agentCard(page, 'Fleet Planner')).toContainText('Working');
});

test('no presence on the server, or no right to read it: Not connected, and the office still draws from tasks', async ({ page }) => {
  await open(page, { presence: 'missing' });
  await expect(chip(page)).toHaveText('Not connected');
  await enterBuilding(page);
  await expect(hq(page).locator('.hqAgentCard').first()).toBeVisible();
});

test('denied: Not connected with the reason in the tooltip', async ({ page }) => {
  await open(page, { stream: 'denied' });
  await expect(chip(page)).toHaveText('Not connected');
  await expect(chip(page)).toHaveAttribute('title', /Ask a workspace owner/);
});

test('a dead network shows Reconnecting, then Not connected, and never keeps saying Live', async ({ page }) => {
  await open(page, { stream: 'down', presence: 'api' });
  await expect(chip(page)).not.toHaveText('Live');
  await expect(chip(page)).toHaveText(/Reconnecting|Connecting|Checking every 10 s|Not connected/);
});

test('the operations wall shows real numbers from the flow and the presence list', async ({ page }) => {
  await open(page, { stream: 'live' });
  await enterBuilding(page);
  await hq(page).getByRole('button', { name: /Alpha Command Room/ }).first().click();
  const wall = hq(page).locator('[data-testid="hq-wall-panel"]');
  await expect(wall).toBeVisible();
  await expect(wall.locator('[data-tile="openTasks"] strong')).toHaveText('3');
  await expect(wall.locator('[data-tile="blocked"] strong')).toHaveText('1');
  await expect(wall.locator('[data-tile="approvals"] strong')).toHaveText('1');
  await expect(wall.locator('[data-tile="running"] strong')).toHaveText('1');
  await expect(wall.locator('[data-tile="agents"] > strong')).toHaveText('6');
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/wall-1440x900.png` });
});

test('a source that fails shows Not connected and no number; a denied read says No access', async ({ page }) => {
  await open(page, { stream: 'live', flowSources: { tasks: 'error', approvals: 'no_access' } });
  await enterBuilding(page);
  await hq(page).getByRole('button', { name: /Alpha Command Room/ }).first().click();
  const wall = hq(page).locator('[data-testid="hq-wall-panel"]');
  await expect(wall.locator('[data-tile="openTasks"]')).toContainText('Not connected');
  await expect(wall.locator('[data-tile="openTasks"] strong')).not.toHaveText(/\d/);
  await expect(wall.locator('[data-tile="approvals"]')).toContainText('No access');
  await expect(wall.locator('[data-tile="agents"] > strong')).toHaveText('6');
});

test('the flow failing entirely leaves every flow tile on Not connected', async ({ page }) => {
  await open(page, { stream: 'live', flowFail: 500 });
  await enterBuilding(page);
  await hq(page).getByRole('button', { name: /Alpha Command Room/ }).first().click();
  const wall = hq(page).locator('[data-testid="hq-wall-panel"]');
  for (const id of ['openTasks', 'blocked', 'approvals', 'running']) await expect(wall.locator(`[data-tile="${id}"]`)).toContainText('Not connected');
});

test('the operations room opens the flow dock filtered to that room', async ({ page }) => {
  await open(page, { stream: 'live' });
  await enterBuilding(page);
  await hq(page).getByRole('button', { name: /Alpha Command Room/ }).first().click();
  await page.locator('.hqSide [data-testid="room-module"]').click();
  const dock = page.locator('.hqFlowDock');
  await expect(dock).toHaveAttribute('data-open', 'true');
  await expect(dock).toContainText('Showing Alpha Command Room only');
  await expect(dock).toContainText('Plan October tyre rotation');
  await expect(dock).not.toContainText('Reorder wiper blades');
  await dock.getByRole('button', { name: 'Show all offices' }).click();
  await expect(dock).toContainText('Reorder wiper blades');
});

test('Inside: a low camera in the selected room, by button or keyboard, and Escape leaves', async ({ page }) => {
  await open(page, { stream: 'live', reducedMotion: true });
  await enterBuilding(page);
  const scene = hq(page).locator('.hqSceneWrap');
  const inside = hq(page).getByTestId('hq-inside');
  await expect(inside).toBeDisabled();
  await expect(inside).toHaveAttribute('title', /Select a room first/);
  await hq(page).getByRole('button', { name: /Alpha Command Room/ }).first().click();
  await expect(inside).toBeEnabled();
  const outside = Number(await scene.getAttribute('data-camera-distance'));
  expect(outside).toBeGreaterThan(6);
  await inside.focus();
  await page.keyboard.press('Enter');
  await expect(inside).toHaveAttribute('aria-pressed', 'true');
  await expect(scene).toHaveAttribute('data-camera-distance', '0.05');
  const eye = Number(await scene.getAttribute('data-camera-eye-y'));
  expect(eye).toBeGreaterThan(1.3);
  expect(eye).toBeLessThan(1.8);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/inside-1440x900.png` });
  // Closing the room panel to see the room does not throw the camera back out; the operations wall hangs on the wall of the room.
  await hq(page).getByRole('button', { name: 'Close panel' }).click();
  await expect(hq(page).getByRole('complementary', { name: /Room Alpha Command Room/ })).toHaveCount(0);
  await expect(inside).toHaveAttribute('aria-pressed', 'true');
  await expect(scene).toHaveAttribute('data-camera-distance', '0.05');
  await expect(hq(page).locator('[data-testid="hq-wall-scene"]')).toBeVisible();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/inside-panel-closed-1440x900.png` });
  // Rotating turns the view on the spot: the camera stays inside.
  await hq(page).getByRole('button', { name: 'Rotate right' }).click();
  await expect(scene).toHaveAttribute('data-camera-distance', '0.05');
  await page.keyboard.press('Escape');
  await expect(inside).toHaveAttribute('aria-pressed', 'false');
  expect(Number(await scene.getAttribute('data-camera-distance'))).toBeGreaterThan(6);
});

test('Inside flies there when motion is allowed, and Reset brings the overview back', async ({ page }) => {
  await open(page, { stream: 'live' });
  await enterBuilding(page);
  const scene = hq(page).locator('.hqSceneWrap');
  await hq(page).getByRole('button', { name: /Alpha Command Room/ }).first().click();
  await hq(page).getByTestId('hq-inside').click();
  await expect(scene).toHaveAttribute('data-camera-distance', '0.05', { timeout: 15_000 });
  await hq(page).getByRole('button', { name: 'Reset view' }).click();
  await expect(hq(page).getByTestId('hq-inside')).toHaveAttribute('aria-pressed', 'false');
  await expect.poll(async () => Number(await scene.getAttribute('data-camera-distance')), { timeout: 15_000 }).toBeGreaterThan(6);
});

test('Arabic: the status and the wall read right to left', async ({ page }) => {
  await open(page, { stream: 'live', locale: 'ar' });
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(chip(page)).toHaveText('مباشر');
  await hq(page).getByRole('button', { name: 'قائمة', exact: true }).click();
  await page.locator('.hqListRoom', { hasText: 'Alpha Command Room' }).click();
  const wall = hq(page).locator('[data-testid="hq-wall-panel"]');
  await expect(wall).toContainText('جدار العمليات');
  await expect(wall).toContainText('مهام مفتوحة');
  await noOverflow(page);
});

test('accessibility: the live status and the wall have no violations', async ({ page }) => {
  await open(page, { stream: 'live' });
  await enterBuilding(page);
  await hq(page).getByRole('button', { name: /Alpha Command Room/ }).first().click();
  await expect(hq(page).locator('[data-testid="hq-wall-panel"]')).toBeVisible();
  const results = await new AxeBuilder({ page }).include('.hqHeadTools').include('[data-testid="hq-wall-panel"]').analyze();
  expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([]);
});
