import { expect, test, type Locator, type Page } from '@playwright/test';
import { mockWorkspaceApi } from './support/workspaceApi';

// The sidebar's reach (docs/design/anum360.md#web-command-center). The owner reported that the
// sidebar "scrolls half" and the lower entries did not work: the main list scrolled in its own box
// and every group below it was squeezed into a sliver with a scroll box of its own. The nav is now
// the one scroll area between the brand and the stream status, so the last entry of the last group
// can be scrolled to and clicked on a short window and on a phone.

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
  await expect(page.getByRole('heading', { level: 1, name: /^Good (morning|afternoon|evening)/ })).toBeVisible();
}

/** The last entry of the last sidebar group (Admin, for an owner who runs the platform). */
const lastItem = (page: Page) => page.locator('.sidebar nav .navItem').last();

test('on a short window the last Admin entry scrolls into sight and opens its page', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'the sidebar is a column on desktop; phones get a strip');
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

test('on a phone the last entry of the nav strip scrolls into sight and opens its page', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'the phone layout');
  await page.setViewportSize({ width: 390, height: 844 });
  await openHome(page);
  const item = lastItem(page);
  await expect(item).toHaveText('Billing console');
  await item.scrollIntoViewIfNeeded();
  // The strip snaps after it scrolls; wait until the entry stands still in the window.
  await expect(item).toBeInViewport({ ratio: 1 });
  await expect.poll(() => hitsItself(item)).toBe(true);
  // The page itself never scrolls sideways.
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

  await item.tap();
  await expect(item).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('main.workspace')).toHaveClass(/view-platformBilling/);
});
