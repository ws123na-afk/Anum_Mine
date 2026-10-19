import { expect, test } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Accessibility checks that need a person's path through the page: the skip link and the landmarks.
// The axe gate over every page, size, language and the light theme is e2e/ui-gate.spec.ts
// (docs/web-localization.md#accessibility).

test('keyboard: the skip link moves focus to the main content, past the navigation', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#tasks');
  await expect(page.locator('.recordButton').first()).toBeVisible();
  await page.keyboard.press('Tab');
  const skip = page.getByRole('link', { name: 'Skip to content' });
  await expect(skip).toBeFocused();
  await expect(skip).toBeInViewport();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('main')).toBeFocused();
  await page.keyboard.press('Tab');
  // The next stop is inside the page, not the sidebar navigation.
  expect(await page.evaluate(() => Boolean(document.activeElement?.closest('main')))).toBe(true);
});

test('landmarks: one main region named by the page title, and a labelled primary navigation', async ({ page }) => {
  await mockWorkspaceApi(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#approvals');
  await expect(page.getByRole('main')).toHaveCount(1);
  await expect(page.getByRole('main', { name: 'الموافقات' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'التنقل الرئيسي' })).toBeAttached();
});
