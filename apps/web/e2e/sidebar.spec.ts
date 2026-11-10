import { expect, test, type Locator, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// The sidebar's reach (docs/design/anum360.md#web-command-center). The owner reported that the
// sidebar "scrolls half" and the lower entries did not work: the main list scrolled in its own box
// and every group below it was squeezed into a sliver with a scroll box of its own. The nav is now
// the one scroll area between the brand and the stream status, so the last entry of the last group
// can be scrolled to and clicked on a short window, and in the phone drawer.

/** The element at the centre of `item` is the entry itself (nothing covers it). */
async function hitsItself(item: Locator): Promise<boolean> {
  return item.evaluate((el) => {
    const box = el.getBoundingClientRect();
    const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    return hit !== null && el.contains(hit);
  });
}

async function openHome(page: Page) {
  await mockWorkspaceApi(page);
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1, name: 'Operations overview' })).toBeVisible();
}

/** The last entry of the last sidebar group (Admin, for an owner who runs the platform). */
const lastItem = (page: Page) => page.locator('.sidebar nav .navItem').last();

test('on a short window the last Admin entry scrolls into sight and opens its page', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'the sidebar is a column on desktop; phones get a drawer');
  await page.setViewportSize({ width: 1280, height: 650 });
  await openHome(page);
  for (const toggle of await page.locator('.sidebar .navGroupToggle[aria-expanded="false"]').all()) await toggle.click();

  const nav = page.locator('.sidebar nav');
  const item = lastItem(page);
  await expect(item).toHaveText('Billing console');
  await expect(item.locator('xpath=ancestor::*[contains(@class,"navGroup-admin")]')).toHaveCount(1);
  // The nav is the one scroll area: no group scrolls inside a box of its own.
  const scrollers = await page.locator('.sidebar').evaluate((sidebar) => [...sidebar.querySelectorAll('*')]
    .filter((el) => el.scrollHeight > el.clientHeight + 1 && /(auto|scroll)/.test(getComputedStyle(el).overflowY)).map((el) => el.tagName));
  expect(scrollers).toEqual(['NAV']);

  // Scroll to the end with the mouse wheel over the sidebar.
  const navBox = await nav.boundingBox();
  if (!navBox) throw new Error('the sidebar nav has no box');
  await page.mouse.move(navBox.x + navBox.width / 2, navBox.y + navBox.height / 2);
  for (let turn = 0; turn < 10; turn += 1) await page.mouse.wheel(0, 400);
  await expect.poll(() => nav.evaluate((el) => Math.ceil(el.scrollTop + el.clientHeight) >= el.scrollHeight)).toBe(true);

  // The entry sits inside the window, above the stream status, and nothing covers it.
  await expect(item).toBeInViewport({ ratio: 1 });
  const itemBox = await item.boundingBox();
  const statusBox = await page.locator('.sidebar .systemState').boundingBox();
  expect(itemBox && statusBox && itemBox.y + itemBox.height <= statusBox.y).toBe(true);
  expect(await hitsItself(item)).toBe(true);

  await item.click();
  await expect(item).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('main.workspace')).toHaveClass(/view-platformBilling/);

  // Keyboard: focus moving to an entry that is out of sight brings it into the nav's view.
  await page.locator('main.workspace').focus();
  await nav.evaluate((el) => { el.scrollTop = 0; });
  await expect(item).not.toBeInViewport();
  await item.focus();
  await expect(item).toBeInViewport({ ratio: 1 });
  expect(await hitsItself(item)).toBe(true);

  // A view opened some other way brings its entry into sight too.
  await page.evaluate(() => { location.hash = '#developer'; });
  await page.reload();
  const developer = page.locator('.sidebar nav .navItem', { hasText: 'Developer' });
  await expect(developer).toHaveAttribute('aria-current', 'page');
  await expect(developer).toBeInViewport({ ratio: 1 });
});

// Phones: the navigation is a drawer behind the menu button in the brand row (it was one sideways
// strip of every view, about sixteen screens wide, with no groups and no menu button).
async function openDrawer(page: Page) {
  await page.getByRole('button', { name: 'Open menu' }).click();
  const drawer = page.getByRole('dialog', { name: 'Menu' });
  await expect(drawer).toBeVisible();
  return drawer;
}

test('on a phone the menu button opens the navigation as a drawer with the desktop groups', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'the phone layout');
  await page.setViewportSize({ width: 390, height: 844 });
  await openHome(page);
  const menu = page.getByRole('button', { name: 'Open menu' });
  const nav = page.getByRole('navigation', { name: 'Primary' });
  // Closed: no strip of entries under the brand row, and the page never scrolls sideways.
  await expect(nav).toBeHidden();
  await expect(menu).toHaveAttribute('aria-expanded', 'false');
  const menuBox = await menu.boundingBox();
  expect(menuBox && menuBox.width >= 44 && menuBox.height >= 44).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

  const drawer = await openDrawer(page);
  await expect(menu).toHaveAttribute('aria-expanded', 'true');
  await expect(drawer.getByRole('button', { name: 'Close menu' })).toBeFocused();
  // One column that scrolls up and down, never sideways, and the current page is in sight.
  expect(await nav.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
  await expect(nav.locator('.navItem[aria-current="page"]')).toHaveText('Home');
  await expect(nav.locator('.navItem[aria-current="page"]')).toBeInViewport({ ratio: 1 });
  // Every control in the drawer is a 44 px touch target.
  const heights = await drawer.locator('button').evaluateAll((buttons) => buttons.filter((b) => (b as HTMLElement).checkVisibility()).map((b) => Math.round(b.getBoundingClientRect().height)));
  expect(heights.length).toBeGreaterThan(20);
  expect(Math.min(...heights)).toBeGreaterThanOrEqual(44);

  // The same groups as the desktop column, and they fold.
  const admin = drawer.getByRole('button', { name: 'Admin', exact: true });
  const more = drawer.getByRole('button', { name: 'More', exact: true });
  await expect(more).toHaveAttribute('aria-expanded', 'true');
  const item = lastItem(page);
  await expect(item).toHaveText('Billing console');
  await admin.scrollIntoViewIfNeeded();
  await admin.click();
  await expect(admin).toHaveAttribute('aria-expanded', 'false');
  await expect(item).toBeHidden();
  await admin.click();
  await expect(item).toBeVisible();

  // Tab stays inside the drawer: back from the first control lands on the last one, and forward again.
  await drawer.getByRole('button', { name: 'Close menu' }).focus();
  await page.keyboard.press('Shift+Tab');
  await expect(item).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(drawer.getByRole('button', { name: 'Close menu' })).toBeFocused();

  // Escape closes it and focus returns to the menu button.
  await page.keyboard.press('Escape');
  await expect(drawer).toHaveCount(0);
  await expect(nav).toBeHidden();
  await expect(menu).toBeFocused();

  // So does a tap on the page behind it.
  await openDrawer(page);
  await page.mouse.click(375, 500);
  await expect(page.getByRole('dialog', { name: 'Menu' })).toHaveCount(0);
  await expect(menu).toBeFocused();

  // The last entry of the last group can be reached and opens its page; the drawer closes behind it.
  await openDrawer(page);
  await item.scrollIntoViewIfNeeded();
  await expect(item).toBeInViewport({ ratio: 1 });
  await expect.poll(() => hitsItself(item)).toBe(true);
  await item.click();
  await expect(page.locator('main.workspace')).toHaveClass(/view-platformBilling/);
  await expect(page.getByRole('dialog', { name: 'Menu' })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  // Opened again, the drawer shows where the person is.
  await openDrawer(page);
  await expect(item).toHaveAttribute('aria-current', 'page');
  await expect(item).toBeInViewport({ ratio: 1 });
});

test('in Arabic the phone drawer opens from the right edge', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'the phone layout');
  await page.setViewportSize({ width: 390, height: 844 });
  await mockWorkspaceApi(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/');
  const menu = page.getByRole('button', { name: 'فتح القائمة' });
  const menuBox = await menu.boundingBox();
  expect(menuBox && menuBox.x > 195).toBe(true);
  await menu.click();
  const drawer = page.getByRole('dialog', { name: 'القائمة' });
  await expect(drawer).toBeVisible();
  // The slide-in has finished once the panel stands at the edge.
  await expect.poll(async () => { const box = await drawer.boundingBox(); return box ? Math.round(box.x + box.width) : 0; }).toBe(390);
  const box = await drawer.boundingBox();
  expect(box && box.x > 0).toBe(true);
  await expect(drawer.getByRole('button', { name: 'المزيد', exact: true })).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Escape');
  await expect(menu).toBeFocused();
});

test('a tablet keeps the sidebar column and shows no menu button', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'sized by the test');
  await page.setViewportSize({ width: 768, height: 1024 });
  await openHome(page);
  await expect(page.getByRole('button', { name: 'Open menu' })).toBeHidden();
  await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible();
  const box = await page.locator('.sidebar').boundingBox();
  expect(box && Math.round(box.width)).toBe(248);
});
