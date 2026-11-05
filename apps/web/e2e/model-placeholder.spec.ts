import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// "AI answers are placeholders" (docs/model-gateway.md#placeholder-answers): Home and the Assistant show the
// banner only while GET /api/v1/model-status says the placeholder model answers, in English and Arabic.
const API = 'http://localhost:8000';
const VIEWPORTS = [
  { width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 },
];
const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

async function withModelStatus(page: Page, body: { placeholder: boolean; provider: string; source: 'server' | 'workspace' }) {
  await mockWorkspaceApi(page);
  // Registered after the workspace mock, so this route answers first.
  await page.route(`${API}/api/v1/model-status`, (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) }));
}

async function serious(page: Page) {
  const results = await new AxeBuilder({ page }).include('[data-testid="model-placeholder-banner"]').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  return results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => v.id);
}

test('the placeholder model shows the banner on Home and in the Assistant, and it opens Settings', async ({ page }) => {
  await withModelStatus(page, { placeholder: true, provider: 'mock', source: 'server' });
  await page.goto('/#home');
  const banner = page.getByTestId('model-placeholder-banner');
  await expect(banner).toContainText('AI answers are placeholders: the server has no AI model set up.');
  await expect(banner).toContainText('Model gateway');
  expect(await serious(page)).toEqual([]);

  await page.goto('/#assistant');
  await expect(page.getByTestId('model-placeholder-banner')).toContainText('AI answers are placeholders');
  await page.getByRole('button', { name: 'Open Settings' }).click();
  await expect(page).toHaveURL(/#settings$/);
  await expect(page.getByRole('heading', { name: 'Model gateway' })).toBeVisible();
});

test('a workspace set to the placeholder model says so', async ({ page }) => {
  await withModelStatus(page, { placeholder: true, provider: 'mock', source: 'workspace' });
  await page.goto('/#home');
  await expect(page.getByTestId('model-placeholder-banner')).toContainText('this workspace is set to the placeholder model');
});

test('a real model shows no banner', async ({ page }) => {
  await withModelStatus(page, { placeholder: false, provider: 'anthropic', source: 'server' });
  await page.goto('/#home');
  await expect(page.getByRole('heading', { level: 1, name: /^Good (morning|afternoon|evening), Sara$/ })).toBeVisible();
  await page.goto('/#assistant');
  await expect(page.getByRole('textbox', { name: 'Message the assistant' })).toBeVisible();
  await expect(page.getByTestId('model-placeholder-banner')).toHaveCount(0);
});

test('an unreadable status claims nothing', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#home');
  await expect(page.getByRole('heading', { level: 1, name: /^Good (morning|afternoon|evening), Sara$/ })).toBeVisible();
  await expect(page.getByTestId('model-placeholder-banner')).toHaveCount(0);
});

for (const locale of ['en', 'ar'] as const) {
  test(`${locale}: the banner fits at the five viewports`, async ({ page }) => {
    await useSavedLocale(page, locale);
    await withModelStatus(page, { placeholder: true, provider: 'mock', source: 'server' });
    for (const size of VIEWPORTS) {
      await page.setViewportSize(size);
      for (const view of ['home', 'assistant']) {
        await page.goto(`/#${view}`);
        const banner = page.getByTestId('model-placeholder-banner');
        await expect(banner).toBeVisible();
        if (locale === 'ar') await expect(banner).toContainText('إجابات الذكاء الاصطناعي نصوص مؤقتة');
        expect(await noHorizontalOverflow(page), `${locale} ${view} ${size.width}x${size.height}`).toBe(true);
        const box = await banner.boundingBox();
        expect(box && box.x >= 0 && box.x + box.width <= size.width + 1, `${view} ${size.width}: banner inside the page`).toBe(true);
      }
    }
  });
}
