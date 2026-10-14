import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';
import { mockMarketing } from './support/marketingApi';
import { mockWhatsApp } from './support/whatsappApi';

// The shared page layout (docs/design/anum360.md#page-layout): summary bands from the API, split
// views that open on the first unread or newest record, the AI Chat context rail, share links in the
// Documents preview, and no horizontal overflow on any of these pages at the gate's five sizes.
const overflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
const wide = (page: Page) => (page.viewportSize()?.width ?? 0) >= 1024;

test('AI Chat shows what the open conversation used and saved, next to the thread', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#assistant');
  await expect(page.getByRole('heading', { level: 1, name: 'AI Chat' })).toBeVisible();
  await expect(page.locator('.chatTurn.assistant').first()).toBeVisible();
  // The agent from GET /api/v1/agents is offered under "Talk to".
  await expect(page.getByLabel('Talk to')).toContainText('Fleet desk');
  if ((page.viewportSize()?.width ?? 0) < 1280) await page.getByRole('button', { name: 'Show what this chat used' }).click();
  const rail = page.locator('.chatRail');
  await expect(rail.getByText('1 asked · 1 answered')).toBeVisible();
  // The memory remembered from this conversation (provenance source_id conv_1) is listed and opens the Knowledge Base.
  await rail.getByRole('button', { name: /The user prefers short answers/ }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Knowledge Base' })).toBeVisible();
});

test('Documents: the band counts files from the API, and the preview makes a share link', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#files');
  const band = page.getByRole('region', { name: 'Documents at a glance' });
  await expect(band.getByText('Indexed for search')).toBeVisible();
  await expect(band.locator('.pageFigure').nth(2).locator('strong')).toHaveText('1');
  await page.getByRole('button', { name: 'Show details of release-notes.md' }).click();
  await page.getByRole('button', { name: 'Share link' }).click();
  await expect(page.getByRole('textbox', { name: 'Download link (no sign-in needed)' })).toHaveValue('http://localhost:8000/api/v1/files/links/example-token');
  if (!wide(page)) await page.getByRole('button', { name: 'Close file detail' }).click();
  await page.getByLabel('File type').selectOption('image');
  await expect(page.getByRole('region', { name: 'Files' }).locator('tbody tr')).toHaveCount(1);
});

test('a figure whose request fails says Unavailable and can be loaded again, never 0', async ({ page }) => {
  await mockWorkspaceApi(page);
  let fail = true;
  await page.route('http://localhost:8000/api/v1/retrieval/status', (route) => fail
    ? route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'unavailable', message: 'Index offline' } }) })
    : route.fallback());
  await page.goto('/#files');
  const failed = page.getByRole('region', { name: 'Documents at a glance' }).locator('.pageFigure.error');
  await expect(failed).toHaveCount(2);
  await expect(failed.first()).toContainText('Unavailable');
  fail = false;
  await page.getByRole('button', { name: 'Load the figures again' }).click();
  await expect(failed).toHaveCount(0);
});

test('Inbox opens on the first unread message and lists the drafts waiting', async ({ page }) => {
  await mockWorkspaceApi(page);
  await page.goto('/#inbox');
  await expect(page.getByRole('region', { name: 'Inbox at a glance' }).getByText('Drafts waiting')).toBeVisible();
  if (wide(page)) await expect(page.getByRole('heading', { level: 2, name: 'Do you have 205/55R16 in stock?' })).toBeVisible();
  const queue = page.locator('.inboxQueue');
  if ((await queue.getAttribute('open')) === null) await queue.locator('summary').click();
  await queue.getByRole('button', { name: /Re: Do you have 205\/55R16 in stock\?/ }).click();
  await expect(page.getByRole('heading', { level: 2, name: 'Do you have 205/55R16 in stock?' })).toBeVisible();
});

test('WhatsApp opens on the first unread conversation on a wide screen', async ({ page }) => {
  await mockWhatsApp(page);
  await page.goto('/#whatsapp');
  await expect(page.getByRole('heading', { level: 1, name: 'WhatsApp' })).toBeVisible();
  if (wide(page)) await expect(page.locator('.waThread').getByRole('heading', { name: 'أحمد' })).toBeVisible();
  else await expect(page.locator('.waConversation')).toHaveCount(2);
});

const pages: { hash: string; mock: (page: Page) => Promise<unknown>; ready: string }[] = [
  { hash: 'assistant', mock: mockWorkspaceApi, ready: '.chatTurn.assistant' },
  { hash: 'memory', mock: mockWorkspaceApi, ready: '.memoryRow' },
  { hash: 'knowledge', mock: mockWorkspaceApi, ready: '.kbCollectionButton' },
  { hash: 'files', mock: mockWorkspaceApi, ready: '.filesTable tbody tr' },
  { hash: 'inbox', mock: mockWorkspaceApi, ready: '.inboxRow' },
  { hash: 'whatsapp', mock: mockWhatsApp, ready: '.waConversation' },
  { hash: 'meetings', mock: mockWorkspaceApi, ready: '.meetingRows li' },
  { hash: 'marketing', mock: mockMarketing, ready: '.mkPostTable tbody tr' },
  { hash: 'marketing-accounts', mock: mockMarketing, ready: '.mkAccountTable tbody tr' },
  { hash: 'finance', mock: mockWorkspaceApi, ready: '.finChart svg' },
  { hash: 'finance-transactions', mock: mockWorkspaceApi, ready: '.finTxTable tbody tr' },
];
for (const size of [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 }]) {
  test.describe(`at ${size.width}x${size.height}`, () => {
    test.use({ viewport: size });
    test('every page in the shared layout fits without horizontal overflow, in English and Arabic', async ({ page }) => {
      // Twenty-two page loads in one test: give it room on a busy runner.
      test.setTimeout(120_000);
      for (const locale of ['en', 'ar'] as const) {
        for (const item of pages) {
          const tab = await page.context().newPage();
          await item.mock(tab);
          if (locale === 'ar') await useSavedLocale(tab, 'ar');
          await tab.goto(`/#${item.hash}`);
          await expect(tab.locator(item.ready).first(), `${item.hash} ${locale}`).toBeVisible();
          expect(await overflow(tab), `${item.hash} ${locale}`).toBeLessThanOrEqual(0);
          await tab.close();
        }
      }
    });
  });
}
