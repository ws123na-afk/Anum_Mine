import { expect, test, type Page } from '@playwright/test';
import { aiReply, mockMailAiApi, type MailAiMockOptions } from './support/mailAiApi';
import { mockMailApi } from './support/mailApi';
import { mockWorkspaceApi } from './support/workspaceApi';

// AI on the Email Center (docs/email.md#ai-on-email): summarise, label chips and "Draft reply with
// AI" in the reader, with every state: checking, AI not set up, failure with retry, permission
// denied, and the warning when a message tries to instruct an AI. Nothing here sends mail.

async function openMessage(page: Page, options: MailAiMockOptions = {}) {
  await mockWorkspaceApi(page);
  const mail = await mockMailApi(page);
  const ai = await mockMailAiApi(page, options);
  await page.goto('/#email');
  await expect(page.getByRole('heading', { level: 1, name: 'Email' })).toBeVisible();
  await page.locator('.mailRowOpen').first().click();
  const reader = page.getByRole('article');
  await expect(reader.getByRole('heading', { name: 'Tyre order for next week' })).toBeVisible();
  return { mail, ai, reader, panel: reader.getByRole('region', { name: 'AI assistant' }) };
}

const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test('summarise and label a message; nothing is sent', async ({ page }) => {
  const { mail, ai, panel } = await openMessage(page);
  await expect(panel).toContainText('never sends, deletes or forwards on its own');
  await panel.getByRole('button', { name: 'Summarise', exact: true }).click();
  const summary = panel.getByRole('region', { name: 'Summary' });
  await expect(summary).toContainText('Sara confirms the tyre order for next week');
  await expect(summary.getByRole('listitem')).toHaveCount(3);
  await expect(summary).toContainText('Written by AI (fake/example-model)');
  await panel.getByRole('button', { name: 'Label' }).click();
  const chips = panel.getByRole('list', { name: 'Labels' });
  await expect(chips).toContainText('Customer');
  await expect(chips).toContainText('High priority');
  await panel.getByRole('button', { name: 'Summarise thread' }).click();
  await expect(summary).toContainText('From 3 messages in this thread');
  expect(ai.calls.summaries).toEqual([{ account_id: 'mailbox_one', folder: 'INBOX', uid: 130, thread: false }, { account_id: 'mailbox_one', folder: 'INBOX', uid: 130, thread: true }]);
  expect(mail.calls.sends).toEqual([]);
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('a message that tries to instruct the AI is flagged and only summarised', async ({ page }) => {
  const { mail, panel } = await openMessage(page, { injection: true });
  await panel.getByRole('button', { name: 'Summarise', exact: true }).click();
  await expect(panel.getByText('This message contains text that tries to instruct an AI')).toBeVisible();
  await expect(panel.getByRole('region', { name: 'Summary' })).toContainText('Nothing is asked of you.');
  expect(mail.calls.sends).toEqual([]);
  expect(mail.calls.approvals).toEqual([]);
});

test('Draft reply with AI opens the draft for review; sending stays the approved send', async ({ page }) => {
  const { ai, panel } = await openMessage(page);
  await panel.getByRole('button', { name: 'Draft reply with AI' }).click();
  await panel.getByLabel('What should the reply say? (optional)').fill('Confirm the order.');
  await panel.getByRole('button', { name: 'Write draft' }).click();
  const composer = page.getByRole('article', { name: 'Reply' });
  await expect(composer.getByLabel('To')).toHaveValue('sara@example.com');
  await expect(composer.getByLabel('Message')).toHaveValue(new RegExp(aiReply.split('\n')[0]));
  await expect(composer.getByRole('button', { name: 'Send' })).toBeVisible();
  expect(ai.calls.drafts).toEqual([{ account_id: 'mailbox_one', folder: 'INBOX', uid: 130, kind: 'reply', instructions: 'Confirm the order.' }]);
});

test('without a model the panel says so and offers no AI actions', async ({ page }) => {
  const { panel } = await openMessage(page, { unavailable: true });
  await expect(panel).toContainText('No AI model is set up for this workspace');
  await expect(panel).toContainText('Reading and replying by hand still work.');
  await expect(panel.getByRole('button')).toHaveCount(0);
});

test('a failed summary says what failed and Try again works', async ({ page }) => {
  const { panel } = await openMessage(page, { failSummaryOnce: true });
  await panel.getByRole('button', { name: 'Summarise', exact: true }).click();
  await expect(panel.getByRole('alert')).toContainText('The AI model did not answer');
  await panel.getByRole('button', { name: 'Try again' }).click();
  await expect(panel.getByRole('region', { name: 'Summary' })).toBeVisible();
  await expect(panel.getByRole('alert')).toHaveCount(0);
});

test('drafting from a mailbox without send access explains who can allow it', async ({ page }) => {
  const { panel } = await openMessage(page, { denyDraft: true });
  await panel.getByRole('button', { name: 'Draft reply with AI' }).click();
  await panel.getByRole('button', { name: 'Write draft' }).click();
  await expect(panel.getByRole('alert')).toContainText('Drafting from this mailbox needs send access from its owner.');
  await expect(panel.getByRole('button', { name: 'Try again' })).toHaveCount(0);
});

for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const) {
  test(`the assistant fits at ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    const { panel } = await openMessage(page);
    await panel.getByRole('button', { name: 'Summarise', exact: true }).click();
    await panel.getByRole('button', { name: 'Label' }).click();
    await expect(panel.getByRole('list', { name: 'Labels' })).toBeVisible();
    expect(await noHorizontalOverflow(page)).toBe(true);
    const box = await panel.boundingBox();
    expect(box && box.x >= 0 && box.x + box.width <= width + 1).toBe(true);
    for (const name of ['Summarise thread', 'Draft reply with AI']) {
      const button = await panel.getByRole('button', { name }).boundingBox();
      expect(button && button.x >= 0 && button.x + button.width <= width + 1 && button.height >= 40).toBe(true);
    }
  });
}
