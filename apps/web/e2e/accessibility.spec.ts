import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';
import { GATE_PAGES, GATE_SIZES, settle, type GatePage } from './support/gatePages';

// Accessibility gate (docs/web-localization.md#accessibility): axe-core over every page the app can
// open (support/gatePages.ts) in English and Arabic at the five gate sizes, and in the light theme.
// Serious and critical violations fail the run; the message lists each rule, its help text and the
// offending selectors so the fix is clear from the CI log. The sizes are set here, so the mobile
// project leaves the @gate tests out (playwright.config.ts).

async function seriousViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
  return results.violations
    .filter((violation) => violation.impact === 'serious' || violation.impact === 'critical')
    .map((violation) => `${violation.id} (${violation.impact}): ${violation.help}\n  ${violation.nodes.slice(0, 5).map((node) => node.target.join(' ')).join('\n  ')}`);
}

/** Opens the page with its data showing; the Inbox also opens a message so its draft cards are checked. */
async function open(page: Page, item: GatePage) {
  await page.goto(`/#${item.hash}`);
  await expect(page.locator(item.ready).first(), item.hash).toBeVisible();
  if (item.hash === 'inbox') {
    await page.locator('.inboxRow').first().click();
    await expect(page.locator('.inboxDraftCard').first()).toBeVisible();
  }
  await settle(page);
}

// The light theme (Settings > Appearance, docs/design/anum360.md#theme) gets the same gate in English.
test.describe('light theme', () => {
  for (const item of GATE_PAGES) {
    test(`#${item.hash} has no serious or critical axe violations`, async ({ page }) => {
      await item.mock(page);
      await page.addInitScript(() => { try { localStorage.setItem('anum.theme', 'light'); } catch { /* storage blocked */ } });
      await open(page, item);
      await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
      expect(await seriousViolations(page)).toEqual([]);
    });
  }
});

for (const size of GATE_SIZES) {
  test.describe(`at ${size.width}x${size.height} @gate`, () => {
    test.use({ viewport: size });
    for (const item of GATE_PAGES) {
      test(`#${item.hash} has no serious or critical axe violations, in English and Arabic`, async ({ page }) => {
        test.setTimeout(90_000);
        for (const locale of ['en', 'ar'] as const) {
          const tab = await page.context().newPage();
          await item.mock(tab);
          await useSavedLocale(tab, locale);
          await open(tab, item);
          await expect(tab.locator('html')).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr');
          expect(await seriousViolations(tab), `${item.hash} ${locale}`).toEqual([]);
          await tab.close();
        }
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
