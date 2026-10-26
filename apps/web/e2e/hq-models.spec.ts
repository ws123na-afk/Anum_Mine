import { expect, test, type Page } from '@playwright/test';
import { mockHq, type HqMockOptions } from './support/hqCampusApi';

// Loadable (glTF) furniture and seated people in the building cutaway (docs/ANUM_MINE_HQ_EXTENSION_POINTS.md,
// "Loadable models"). A person is drawn only at a desk with a registry assignment; a failed download keeps the procedural
// furniture. Everything below is plainly fake data served by the usual API mock.
const hq = (page: Page) => page.locator('.hqLive');
const canvas = (page: Page) => hq(page).locator('canvas.hqCanvas');

async function enterFleetBuilding(page: Page, options: HqMockOptions = {}) {
  await page.addInitScript(() => { try { localStorage.setItem('anum.hq.light', 'day'); } catch { /* storage blocked */ } });
  await mockHq(page, { campus: 'api', commerce: 'connected', meetings: 'live', ...options });
  await page.goto('/#office-preview');
  await expect(canvas(page)).toBeVisible();
  await hq(page).getByRole('button', { name: /^Al Noor Fleet HQ\./ }).click();
  await hq(page).getByRole('button', { name: 'Open Al Noor Fleet HQ' }).click();
  await expect(hq(page).getByRole('heading', { level: 2, name: 'Al Noor Fleet HQ' })).toBeVisible();
  // These specs count the people of the open floor. The cutaway draws every floor by default, so switch to the open floor alone.
  await hq(page).getByRole('checkbox', { name: /All floors/ }).uncheck();
}

test('the building draws loaded sofas and one seated person per assigned desk, within the byte budget', async ({ page }) => {
  await enterFleetBuilding(page);
  // Ground floor fixture: Customer Care desk 1 (agent) and Tyre Analysis desk 1 (agent) are assigned, and the presence record puts one
  // person at Customer Care desk 2. The workshop draws the desk it is given, so the Tyre Analysis person is drawn too: three.
  await expect.poll(() => canvas(page).getAttribute('data-model-figures'), { timeout: 60_000 }).toBe('3');
  const loaded = await canvas(page).getAttribute('data-models-loaded');
  expect(loaded).toMatch(/^(\d+)\/\1$/);
  expect(await canvas(page).getAttribute('data-model-fallbacks')).toBe('');
  expect(Number(await canvas(page).getAttribute('data-models-placed'))).toBeGreaterThan(2);
  expect(Number(await canvas(page).getAttribute('data-model-bytes'))).toBeLessThanOrEqual(700_000);
});

test('a desk without an assignment draws no one', async ({ page }) => {
  await enterFleetBuilding(page, { extraDesks: [{ id: 'desk_e1', roomId: 'room_care', label: 'Desk 3', agentId: null }, { id: 'desk_e2', roomId: 'room_care', label: 'Desk 4', agentId: null }] });
  await expect.poll(() => canvas(page).getAttribute('data-models-loaded'), { timeout: 60_000 }).toMatch(/^(\d+)\/\1$/);
  expect(await canvas(page).getAttribute('data-model-figures')).toBe('3');
});

test('a new assignment adds one person and nothing else changes the head count', async ({ page }) => {
  await enterFleetBuilding(page, { extraDesks: [{ id: 'desk_e1', roomId: 'room_care', label: 'Desk 3', agentId: 'agent_extra' }] });
  await expect.poll(() => canvas(page).getAttribute('data-model-figures'), { timeout: 60_000 }).toBe('4');
});

test('when the model files cannot be downloaded the procedural furniture and people stay', async ({ page }) => {
  await page.route('**/hq/models/*.glb', (route) => route.abort());
  await enterFleetBuilding(page);
  await expect.poll(() => canvas(page).getAttribute('data-model-fallbacks'), { timeout: 60_000 }).toContain('sofa');
  expect(await canvas(page).getAttribute('data-model-fallbacks')).toContain('figure');
  expect(await canvas(page).getAttribute('data-model-figures')).toBe('0');
  // The scene still draws (the stand-ins are in it) and the page is still usable.
  expect(Number(await canvas(page).getAttribute('data-triangles'))).toBeGreaterThan(20_000);
  await expect(hq(page).getByRole('button', { name: /^Customer Care/ })).toBeVisible();
});
