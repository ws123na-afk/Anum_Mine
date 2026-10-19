import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi } from './support/workspaceApi';
import { mockWhatsApp } from './support/whatsappApi';

// The shared page layout (docs/design/anum360.md#page-layout): summary bands from the API, split
// views that open on the first unread or newest record, the AI Chat context rail, share links in the
// Documents preview. The layout gate over every page and size is e2e/ui-gate.spec.ts.
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
