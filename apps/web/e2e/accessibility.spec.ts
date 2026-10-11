import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Accessibility gate (docs/web-localization.md#accessibility): axe-core over the main views in
// English and Arabic. Serious and critical violations fail the run; the message lists each rule,
// its help text and the offending selectors so the fix is clear from the CI log.
const views: { hash: string; ready: (page: Page) => Promise<void> }[] = [
  { hash: 'assistant', ready: (page) => expect(page.locator('.chatTurn.assistant').first()).toBeVisible() },
  { hash: 'tasks', ready: (page) => expect(page.locator('.recordButton').first()).toBeVisible() },
  { hash: 'approvals', ready: (page) => expect(page.locator('.approvalDetail').first()).toBeVisible() },
  { hash: 'memory', ready: (page) => expect(page.locator('.memoryRow').first()).toBeVisible() },
  { hash: 'files', ready: (page) => expect(page.locator('.filesTable .memoryRow').first()).toBeVisible() },
  { hash: 'settings', ready: (page) => expect(page.locator('.chatMemorySetting input[type="checkbox"]')).toBeVisible() },
  { hash: 'notifications', ready: (page) => expect(page.locator('.phoneList .phoneRow')).toHaveCount(1) },
  { hash: 'language', ready: (page) => expect(page.locator('.languageChoices')).toBeVisible() },
  { hash: 'privacy', ready: (page) => expect(page.locator('#privacy-stored')).toBeVisible() },
  { hash: 'audit', ready: (page) => expect(page.locator('.auditTable')).toBeVisible() },
  { hash: 'integrations', ready: (page) => expect(page.locator('.integrationFix').first()).toBeVisible() },
  { hash: 'skills', ready: (page) => expect(page.locator('article.catalogCard').first()).toBeVisible() },
  { hash: 'agents', ready: (page) => expect(page.locator('.agentRow').first()).toBeVisible() },
  { hash: 'enterprise', ready: (page) => expect(page.locator('.marketRow').first()).toBeVisible() },
  { hash: 'automation', ready: (page) => expect(page.locator('.autoRows')).toBeVisible() },
];

async function seriousViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
  return results.violations
    .filter((violation) => violation.impact === 'serious' || violation.impact === 'critical')
    .map((violation) => `${violation.id} (${violation.impact}): ${violation.help}\n  ${violation.nodes.slice(0, 5).map((node) => node.target.join(' ')).join('\n  ')}`);
}

for (const locale of ['en', 'ar'] as const) {
  test.describe(`${locale} interface`, () => {
    for (const view of views) {
      test(`#${view.hash} has no serious or critical axe violations`, async ({ page }) => {
        await mockWorkspaceApi(page);
        await useSavedLocale(page, locale);
        await page.goto(`/#${view.hash}`);
        await expect(page.locator('html')).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr');
        await view.ready(page);
        expect(await seriousViolations(page)).toEqual([]);
      });
    }
  });
}

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
