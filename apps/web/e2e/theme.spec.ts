import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// The theme (docs/design/anum360.md#theme): Dark by default, Light or the device's setting from
// Settings > Appearance, kept in this browser and applied before the first paint.
const background = (page: Page) => page.evaluate(() => getComputedStyle(document.body).backgroundColor);

test('dark is the default and the page never starts white', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#tasks');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content', '#070b1a');
  expect(await background(page)).toBe('rgb(7, 11, 26)');
});

test('Settings > Appearance switches to Light at once, keeps it after a reload, and goes back to Dark', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#appearance');
  const light = page.getByRole('radio', { name: /^Light/ });
  await expect(page.getByRole('radio', { name: /^Dark/ })).toBeChecked();
  await light.check();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page.getByRole('status').filter({ hasText: 'Showing the light theme.' })).toBeVisible();
  expect(await background(page)).toBe('rgb(244, 246, 251)');
  expect(await page.evaluate(() => localStorage.getItem('anum.theme'))).toBe('light');

  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content', '#f4f6fb');
  await expect(page.getByRole('radio', { name: /^Light/ })).toBeChecked();

  await page.getByRole('radio', { name: /^Dark/ }).check();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  expect(await page.evaluate(() => localStorage.getItem('anum.theme'))).toBeNull();
});

test('"Match my device" follows the device setting, also when it changes', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.emulateMedia({ colorScheme: 'light' });
  await page.goto('/#appearance');
  await page.getByRole('radio', { name: /^Match my device/ }).check();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
});

test('the user menu opens Appearance, in Arabic too', async ({ page }) => {
  await mockWorkspaceApi(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#tasks');
  await page.locator('.userButton').click();
  await page.getByRole('button', { name: 'المظهر' }).click();
  await expect(page.getByRole('heading', { name: 'السمة' })).toBeVisible();
  await expect(page.getByRole('radio', { name: /^داكنة/ })).toBeChecked();
});

test('light theme: no horizontal overflow on the main views at every checked size', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.addInitScript(() => { try { localStorage.setItem('anum.theme', 'light'); } catch { /* storage blocked */ } });
  for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]]) {
    await page.setViewportSize({ width, height });
    for (const view of ['home', 'tasks', 'appearance', 'policy']) {
      await page.goto(`/#${view}`);
      await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
      await expect(page.getByRole('main')).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), `#${view} at ${width}x${height}`).toBeLessThanOrEqual(0);
    }
  }
});
