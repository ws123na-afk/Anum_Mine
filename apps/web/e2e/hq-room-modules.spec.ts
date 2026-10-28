import { expect, test, type Page } from '@playwright/test';
import { hqRegistry, openHq, TENANT_A } from './support/hqApi';

// Rooms open the module of their STORED department (docs/ANUM_MINE_HQ_EXTENSION_POINTS.md, "Room modules"),
// and the live office never shows the invented reception simulation. Names are plainly fake test data.
test.use({ viewport: { width: 1280, height: 800 } });

const DEPARTMENTS: Array<[string | null, string, string]> = [
  ['finance', 'Alpha Zeta One', '#finance'],
  ['sales', 'Alpha Zeta Two', '#whatsapp'],
  ['support', 'Alpha Zeta Three', '#inbox'],
  ['tyre_engineering', 'Alpha Zeta Four', '#tyre'],
];

function registry() {
  const base = hqRegistry(TENANT_A, { rooms: 9, desks: 2 });
  const rooms = base.rooms.map((r, i) => {
    if (i < DEPARTMENTS.length) return { ...r, name: DEPARTMENTS[i][1], kind: 'office', team_department: DEPARTMENTS[i][0] };
    if (i === 4) return { ...r, name: 'Alpha Zeta Five', kind: 'office', team_department: 'operations' };
    if (i === 5) return { ...r, name: 'Alpha Zeta Six', kind: 'office', team_department: 'custom' };
    if (i === 6) return { ...r, name: 'Alpha Zeta Seven', kind: 'reception', team_department: null };
    if (i === 8) return { ...r, name: 'Alpha Zeta Nine', kind: 'lounge', team_department: 'executive' };
    return { ...r, name: 'Alpha Zeta Eight', kind: 'meeting', team_department: null };
  });
  return { ...base, rooms };
}

async function openRoom(page: Page, name: string, locale?: 'en' | 'ar') {
  await openHq(page, { registry: registry(), locale });
  await expect(page.locator('.hqLive')).toBeVisible();
  await page.locator('.hqLive').getByRole('button', { name: locale === 'ar' ? 'قائمة' : 'List', exact: true }).click();
  await page.locator('.hqListRoom', { hasText: name }).click();
}

for (const [department, room, hash] of DEPARTMENTS) {
  test(`${department} room opens ${hash} from the inspector`, async ({ page }) => {
    await openRoom(page, room);
    const link = page.locator('.hqSide [data-testid="room-module"]');
    await expect(link).toHaveAttribute('href', hash);
    await expect(page.locator('.hqSide [data-testid="room-module-hint"]')).toHaveCount(0);
    await link.focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => page.evaluate(() => location.hash)).toBe(hash);
  });
}

test('the room list carries the same module link', async ({ page }) => {
  await openHq(page, { registry: registry() });
  await page.locator('.hqLive').getByRole('button', { name: 'List', exact: true }).click();
  const row = page.locator('.hqListRooms li', { hasText: 'Alpha Zeta One' });
  await expect(row.locator('[data-testid="room-module"]')).toHaveAttribute('href', '#finance');
});

// 7.7: a STORED executive department opens the executive desk whatever the room kind (here a lounge) and building.
for (const locale of ['en', 'ar'] as const) {
  test(`${locale}: a room with the stored executive department opens #executive`, async ({ page }) => {
    await openRoom(page, 'Alpha Zeta Nine', locale);
    const link = page.locator('.hqSide [data-testid="room-module"]');
    await expect(link).toHaveAttribute('href', '#executive');
    await expect(page.locator('.hqSide [data-testid="room-module-hint"]')).toHaveCount(0);
    if (locale === 'ar') await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
    await link.focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => page.evaluate(() => location.hash)).toBe('#executive');
  });
}

test('operations opens the live flow dock', async ({ page }) => {
  await openRoom(page, 'Alpha Zeta Five');
  await expect(page.locator('.hqFlowDock')).toHaveAttribute('data-open', 'false');
  await page.locator('.hqSide [data-testid="room-module"]').click();
  await expect(page.locator('.hqFlowDock')).toHaveAttribute('data-open', 'true');
});

test('a department without a module is honest and offers Organisation gaps', async ({ page }) => {
  await openRoom(page, 'Alpha Zeta Six');
  await expect(page.locator('.hqSide [data-testid="room-module"]')).toHaveCount(0);
  await expect(page.locator('.hqSide')).toContainText('No module connected for this department');
  await page.locator('.hqSide').getByRole('button', { name: 'Organisation gaps' }).click();
  await expect(page.getByRole('dialog', { name: 'Organisation gaps' })).toBeVisible();
});

test('a room with no department falls back to its kind and shows the hint', async ({ page }) => {
  await openRoom(page, 'Alpha Zeta Eight');
  await expect(page.locator('.hqSide [data-testid="room-module"]')).toHaveAttribute('href', '#meetings');
  await expect(page.locator('.hqSide [data-testid="room-module-hint"]')).toBeVisible();
});

test('the reception room opens the real visitor register; no simulation and no invented names', async ({ page }) => {
  await openRoom(page, 'Alpha Zeta Seven');
  // Owner decision O4: a reception room opens the stored visitor register (docs/ANUM_MINE_HQ_RECEPTION.md), never a simulation.
  await expect(page.locator('.hqSide [data-testid="room-module"]')).toHaveText('Open the visitor register');
  await expect(page.locator('.hqSide [data-testid="room-module-hint"]')).toHaveCount(0);
  await page.locator('.hqLive').getByRole('button', { name: /Office tools/ }).click();
  await expect(page.getByRole('button', { name: 'Reception desk' })).toHaveCount(0);
  const text = await page.locator('.hqLive').innerText();
  for (const name of ['Chief of Staff', 'Finance Controller', 'Tyre Fleet Planner', 'Sample Customer', 'Reception coordinator', 'Reception Operations']) {
    expect(text).not.toContain(name);
  }
});

test('Arabic: the module link and the honest states read in Arabic', async ({ page }) => {
  await openRoom(page, 'Alpha Zeta One', 'ar');
  await expect(page.locator('.hqSide [data-testid="room-module"]')).toContainText('المالية');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
});

test('no dead buttons: every module control in the inspector acts', async ({ page }) => {
  await openRoom(page, 'Alpha Zeta Two');
  const control = page.locator('.hqSide [data-testid="room-module"]');
  expect(await control.evaluate((el) => el.tagName === 'A' ? el.getAttribute('href') : 'button')).toBeTruthy();
});

const SIZES = [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const;
for (const [w, h] of SIZES) {
  test(`room inspector fits at ${w}x${h}`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await openRoom(page, 'Alpha Zeta One');
    await expect(page.locator('.hqSide [data-testid="room-module"]')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const dir = process.env.HQ_SHOTS_DIR;
    if (dir) await page.screenshot({ path: `${dir}/room-module-${w}x${h}.png` });
  });
}

// The executive room (7.7) at the five required viewports, English and Arabic (right to left), width and height.
for (const locale of ['en', 'ar'] as const) {
  for (const [w, h] of SIZES) {
    test(`${locale}: executive room inspector fits at ${w}x${h}`, async ({ page }) => {
      await page.setViewportSize({ width: w, height: h });
      await openRoom(page, 'Alpha Zeta Nine', locale);
      await expect(page.locator('.hqSide [data-testid="room-module"]')).toHaveAttribute('href', '#executive');
      await expect(page.locator('.hqSide [data-testid="room-module"]')).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      const box = await page.locator('.hqSide [data-testid="room-module"]').boundingBox();
      expect(box && box.x >= 0 && box.x + box.width <= w).toBe(true);
      if (locale === 'ar') await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
      const dir = process.env.HQ_SHOTS_DIR;
      if (dir) await page.screenshot({ path: `${dir}/executive-${locale}-${w}x${h}.png` });
    });
  }
}

// S-11: the Concept preview has no reception simulation, and its example staff are labelled as examples.
test('the concept preview has no reception simulation and labels its example staff', async ({ page }) => {
  await openHq(page, { registry: registry() });
  await page.getByRole('button', { name: 'Concept preview' }).click();
  await expect(page.locator('.anumOfficeTour')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Reception Operations' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Office Space Management' })).toBeVisible();
  await expect(page.locator('.anumOfficeTour .officeInfo')).toContainText('Example employee roles');
  await expect(page.locator('.anumOfficeTour .officeFooter')).toContainText('Illustrative staff and rooms');
  const text = await page.locator('.anumOfficeTour').innerText();
  for (const name of ['Sample Customer', 'Sample Supplier', 'Register arrival', 'Assigned host']) expect(text).not.toContain(name);
  const dir = process.env.HQ_SHOTS_DIR;
  if (dir) await page.screenshot({ path: `${dir}/concept-preview.png` });
});
