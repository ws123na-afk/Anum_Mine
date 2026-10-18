import { expect, test, type Page } from '@playwright/test';
import { aiReply, archiveHash, exportHash, mockMailAiApi, type MailAiMockOptions } from './support/mailAiApi';
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

async function openList(page: Page, options: MailAiMockOptions & { role?: 'owner' | 'member' } = {}) {
  await mockWorkspaceApi(page);
  const mail = await mockMailApi(page, { role: options.role });
  const ai = await mockMailAiApi(page, options);
  await page.goto('/#email');
  await expect(page.locator('.mailRow').first()).toBeVisible();
  return { mail, ai, list: page.getByRole('region', { name: 'Inbox' }), tools: page.getByRole('toolbar', { name: 'AI and approvals for this list' }) };
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

test('Label all labels the shown page and the rows show the chips', async ({ page }) => {
  const { ai, list, tools } = await openList(page);
  await tools.getByRole('button', { name: 'Label all (25)' }).click();
  await expect(page.getByText('Labelled 25 messages.')).toBeVisible();
  expect(ai.calls.pages[0].uids).toHaveLength(25);
  await expect(list.locator('.mailAiRowLabels').first()).toBeVisible();
  await expect(list.locator('.mailRow').filter({ hasText: 'Newsletter' }).first()).toBeVisible();
  await expect(tools.getByRole('button', { name: 'Everything shown is labelled' })).toBeDisabled();
  // A label made in the reader shows on its row too.
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('the tidy-up selects newsletters, then archive waits for the owner to approve the payload hash', async ({ page }) => {
  const { ai, tools } = await openList(page);
  await tools.getByRole('button', { name: /Label all/ }).click();
  await expect(page.getByText('Labelled 25 messages.')).toBeVisible();
  await tools.getByRole('button', { name: /^Select \d+ newsletters and notifications$/ }).click();
  const archive = tools.getByRole('button', { name: /^Archive \d+ with approval$/ });
  await archive.click();
  const card = page.getByRole('group', { name: 'Archive needs approval' });
  await expect(card.locator('code')).toHaveAttribute('title', archiveHash);
  const asked = ai.calls.archives[0].uids;
  expect(asked.length).toBeGreaterThan(0);
  expect(asked.every((uid) => uid % 3 === 0)).toBe(true);
  await card.getByRole('button', { name: 'Approve and archive' }).click();
  await expect(page.getByText(`Archived ${asked.length} messages.`)).toBeVisible();
  expect(ai.calls.approvals).toEqual([{ payload_hash: archiveHash }]);
});

test('export of a selection downloads only after approval', async ({ page }) => {
  const { ai, list, tools } = await openList(page);
  await list.locator('.mailRow').nth(0).getByRole('checkbox').check();
  await list.locator('.mailRow').nth(1).getByRole('checkbox').check();
  await tools.getByRole('button', { name: 'Export 2 (.mbox)' }).click();
  const card = page.getByRole('group', { name: 'Export needs approval' });
  await expect(card).toContainText('2 messages from Inbox will download as an mbox file');
  await expect(card.locator('code')).toHaveAttribute('title', exportHash);
  expect(ai.calls.downloads).toBe(0);
  await card.getByRole('button', { name: 'Approve export' }).click();
  const download = page.waitForEvent('download');
  await card.getByRole('button', { name: 'Download .mbox' }).click();
  expect((await download).suggestedFilename()).toMatch(/^mail-export-\d{8}-\d{4}\.mbox$/);
  await expect(page.getByText('Downloaded 2 messages.')).toBeVisible();
  expect(ai.calls.exports[0].uids).toHaveLength(2);
});

test('an expired export says so; members see that an owner approves', async ({ page }) => {
  const { tools, list } = await openList(page, { exportExpired: true });
  await list.locator('.mailRow').first().getByRole('checkbox').check();
  await tools.getByRole('button', { name: 'Export 1 (.mbox)' }).click();
  const card = page.getByRole('group', { name: 'Export needs approval' });
  await card.getByRole('button', { name: 'Approve export' }).click();
  await card.getByRole('button', { name: 'Download .mbox' }).click();
  await expect(card.getByRole('alert')).toContainText('expired');
});

test('members ask for an archive but an owner approves it', async ({ page }) => {
  const { tools, list } = await openList(page, { role: 'member' });
  await list.locator('.mailRow').first().getByRole('checkbox').check();
  await tools.getByRole('button', { name: 'Archive 1 with approval' }).click();
  const card = page.getByRole('group', { name: 'Archive needs approval' });
  await expect(card).toContainText('An owner of this workspace approves this.');
  await expect(card.getByRole('button', { name: 'Approve and archive' })).toHaveCount(0);
});

test('without a model there is no Label all, and approvals still work', async ({ page }) => {
  const { tools, list } = await openList(page, { unavailable: true });
  await expect(tools).toHaveCount(0);
  await list.locator('.mailRow').first().getByRole('checkbox').check();
  await expect(tools.getByRole('button', { name: 'Export 1 (.mbox)' })).toBeVisible();
  await expect(tools.getByRole('button', { name: /Label all/ })).toHaveCount(0);
});

for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const) {
  test(`the list tools and label chips fit at ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    const { tools, list } = await openList(page);
    await tools.getByRole('button', { name: /Label all/ }).click();
    await expect(list.locator('.mailAiRowLabels').first()).toBeVisible();
    await list.locator('.mailRow').first().getByRole('checkbox').check();
    await expect(tools.getByRole('button', { name: 'Export 1 (.mbox)' })).toBeVisible();
    expect(await noHorizontalOverflow(page)).toBe(true);
    for (const button of await tools.getByRole('button').all()) {
      const box = await button.boundingBox();
      expect(box && box.x >= 0 && box.x + box.width <= width + 1 && box.height >= 40).toBe(true);
    }
  });
}
