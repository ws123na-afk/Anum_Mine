import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Accessibility gate (docs/web-localization.md#accessibility): axe-core over the main views in
// English and Arabic. Serious and critical violations fail the run; the message lists each rule,
// its help text and the offending selectors so the fix is clear from the CI log.
const views: { hash: string; ready: (page: Page) => Promise<void> }[] = [
  { hash: 'home', ready: (page) => expect(page.locator('.commandPanel .listRow').first()).toBeVisible() },
  { hash: 'assistant', ready: (page) => expect(page.locator('.chatTurn.assistant').first()).toBeVisible() },
  { hash: 'inbox', ready: async (page) => { await page.locator('.inboxRow').first().click(); await expect(page.locator('.inboxDraftCard').first()).toBeVisible(); } },
  { hash: 'tasks', ready: (page) => expect(page.locator('.recordButton').first()).toBeVisible() },
  { hash: 'approvals', ready: (page) => expect(page.locator('.approvalDetail').first()).toBeVisible() },
  { hash: 'memory', ready: (page) => expect(page.locator('.memoryRow').first()).toBeVisible() },
  { hash: 'meetings', ready: (page) => expect(page.locator('.meetingRows li').first()).toBeVisible() },
  { hash: 'meetings?meeting=meeting_pmv', ready: (page) => expect(page.locator('.transcriptLines li').first()).toBeVisible() },
  { hash: 'files', ready: (page) => expect(page.locator('.filesTable tbody tr').first()).toBeVisible() },
  { hash: 'commerce', ready: (page) => expect(page.locator('.cmChart svg')).toBeVisible() },
  { hash: 'settings', ready: (page) => expect(page.locator('.chatMemorySetting input[type="checkbox"]')).toBeVisible() },
  { hash: 'notifications', ready: (page) => expect(page.locator('.phoneList .phoneRow')).toHaveCount(1) },
  { hash: 'language', ready: (page) => expect(page.locator('.languageChoices')).toBeVisible() },
  { hash: 'privacy', ready: (page) => expect(page.locator('#privacy-stored')).toBeVisible() },
  { hash: 'call', ready: (page) => expect(page.locator('.callStart')).toBeVisible() },
  { hash: 'team', ready: (page) => expect(page.locator('.workRow').first()).toBeVisible() },
  { hash: 'assistants', ready: (page) => expect(page.locator('.assistantCard').first()).toBeVisible() },
  { hash: 'organisations', ready: (page) => expect(page.locator('.orgCard').first()).toBeVisible() },
  { hash: 'audit', ready: (page) => expect(page.locator('.auditTable')).toBeVisible() },
  { hash: 'integrations', ready: (page) => expect(page.locator('li.intRow').first()).toBeVisible() },
  { hash: 'skills', ready: (page) => expect(page.locator('article.catalogCard').first()).toBeVisible() },
  { hash: 'agents', ready: (page) => expect(page.locator('.agentRow').first()).toBeVisible() },
  { hash: 'favorites', ready: (page) => expect(page.locator('.libraryRow').first()).toBeVisible() },
  { hash: 'knowledge', ready: (page) => expect(page.locator('.kbCollectionButton').first()).toBeVisible() },
  { hash: 'reports', ready: (page) => expect(page.locator('.reportRow').first()).toBeVisible() },
  { hash: 'reports/report_tyre', ready: (page) => expect(page.locator('.reportTable')).toBeVisible() },
  { hash: 'enterprise', ready: (page) => expect(page.locator('.marketRow').first()).toBeVisible() },
  { hash: 'automation', ready: (page) => expect(page.locator('.autoRows')).toBeVisible() },
  { hash: 'activity', ready: (page) => expect(page.locator('.autonomyRow').first()).toBeVisible() },
  { hash: 'autonomy', ready: (page) => expect(page.locator('.autonomyPanel').first()).toBeVisible() },
  { hash: 'learning', ready: (page) => expect(page.locator('.learningRoutine').first()).toBeVisible() },
  { hash: 'members', ready: (page) => expect(page.locator('.memberRow').first()).toBeVisible() },
  { hash: 'budgets', ready: (page) => expect(page.locator('.budgetCard').first()).toBeVisible() },
  { hash: 'policy', ready: (page) => expect(page.locator('.policySwitch').first()).toBeVisible() },
  { hash: 'governance', ready: (page) => expect(page.locator('.govRow').first()).toBeVisible() },
  { hash: 'voice', ready: (page) => expect(page.locator('.voiceSpaceTalk')).toBeVisible() },
];

async function seriousViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
  return results.violations
    .filter((violation) => violation.impact === 'serious' || violation.impact === 'critical')
    .map((violation) => `${violation.id} (${violation.impact}): ${violation.help}\n  ${violation.nodes.slice(0, 5).map((node) => node.target.join(' ')).join('\n  ')}`);
}

// The light theme (Settings > Appearance, docs/design/anum360.md#theme) gets the same gate in English.
test.describe('light theme', () => {
  for (const view of views) {
    test(`#${view.hash} has no serious or critical axe violations`, async ({ page }) => {
      await mockWorkspaceApi(page);
      await page.addInitScript(() => { try { localStorage.setItem('anum.theme', 'light'); } catch { /* storage blocked */ } });
      await page.goto(`/#${view.hash}`);
      await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
      await view.ready(page);
      expect(await seriousViolations(page)).toEqual([]);
    });
  }
});

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
