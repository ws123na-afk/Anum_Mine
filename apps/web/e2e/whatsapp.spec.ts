import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { useSavedLocale } from './support/workspaceApi';
import { hash, mockWhatsApp as mockApi, notConnected } from './support/whatsappApi';

// WhatsApp inbox (docs/whatsapp.md): conversations, the 24-hour window, drafts with provenance,
// approve-to-send with the payload hash, templates outside the window, setup, and every state.

async function openInbox(page: Page) {
  await page.goto('/#whatsapp');
  await expect(page.getByRole('heading', { level: 1, name: 'WhatsApp' })).toBeVisible();
}

test('an owner drafts a reply, sends it for approval and approves it with the payload hash', async ({ page }) => {
  const sent = await mockApi(page);
  await openInbox(page);
  await expect(page.locator('.waConversation')).toHaveCount(2);
  await page.locator('.waConversation').filter({ hasText: 'أحمد' }).click();
  const thread = page.locator('.waThread');
  await expect(thread.getByRole('heading', { name: 'أحمد' })).toBeVisible();
  await expect(thread.locator('.waWindow')).toContainText('Free-form replies for 23 more hours');
  await expect(thread.locator('.waText').first()).toHaveAttribute('dir', 'auto');

  // The AI suggestion sits in the rail next to the thread on wide screens, inside it on narrow ones.
  const inbox = page.locator('.waInbox');
  await inbox.getByRole('button', { name: 'Suggest a reply' }).click();
  await expect(inbox.locator('.waDraftText')).toContainText('شكراً لسؤالك');
  await expect(inbox.locator('.waProvenance')).toContainText('Rule-based draft');
  await inbox.getByRole('button', { name: 'Use this draft' }).click();
  await expect(thread.getByLabel('Your reply')).toHaveValue(/شكراً لسؤالك/);
  await thread.getByLabel('Your reply').fill('نعم متوفر، السعر 350 ريال.');
  await thread.getByRole('button', { name: 'Send for approval' }).click();
  await expect.poll(() => sent.sends).toEqual([{ text: 'نعم متوفر، السعر 350 ريال.' }]);

  const card = thread.locator('.waApproval');
  await expect(card).toContainText('graph.facebook.com');
  await expect(card).toContainText('5d5d5d5d5d5d');
  await card.getByRole('button', { name: 'Approve & send' }).click();
  await expect.poll(() => sent.decisions).toEqual([{ payload_hash: hash }]);
  await expect(thread.locator('.waBubble.outbound .statusBadge')).toHaveText('sent');
  await expect(thread.getByRole('status').filter({ hasText: 'Approved. The reply was sent.' })).toBeVisible();
});

test('outside the 24-hour window only a template can be sent, with its values filled in', async ({ page }) => {
  const sent = await mockApi(page);
  await openInbox(page);
  await page.locator('.waConversation').filter({ hasText: 'Sara Trading' }).click();
  const thread = page.locator('.waThread');
  await expect(thread.locator('.waWindow')).toHaveText('24-hour window closed: templates only');
  await thread.getByRole('button', { name: 'Message', exact: true }).click();
  await expect(thread.getByRole('note')).toContainText('More than 24 hours passed');
  await thread.getByRole('button', { name: 'Choose a template' }).click();
  const select = thread.getByRole('combobox', { name: 'Template' });
  await expect(select.locator('option')).toHaveCount(2); // the placeholder and the one approved template
  await select.selectOption('order_update|en_US');
  await thread.getByLabel('Value 1').fill('Sara');
  await thread.getByRole('button', { name: 'Send for approval' }).click();
  await expect(thread.getByRole('alert')).toContainText('Fill in every value');
  await thread.getByLabel('Value 2').fill('A-1001');
  await expect(thread.locator('.waTemplatePreview')).toContainText('Hi Sara, your order A-1001 is on its way.');
  await thread.getByRole('button', { name: 'Send for approval' }).click();
  await expect.poll(() => sent.sends).toEqual([{ template: { name: 'order_update', language: 'en_US', parameters: ['Sara', 'A-1001'] } }]);
});

test('a support ticket is created from a conversation and links to it', async ({ page }) => {
  const sent = await mockApi(page);
  await openInbox(page);
  await page.locator('.waConversation').filter({ hasText: 'أحمد' }).click();
  const thread = page.locator('.waThread');
  await thread.getByRole('button', { name: 'Create ticket' }).click();
  const dialog = page.getByRole('dialog', { name: 'Create a support ticket' });
  await expect(dialog.getByLabel('Title')).toHaveValue('');
  await expect(dialog.getByLabel('Title')).toHaveAttribute('placeholder', /For example/);
  await dialog.getByRole('button', { name: 'Create ticket' }).click();
  await expect(dialog).toHaveCount(0);
  await expect(thread.getByRole('status').filter({ hasText: 'Ticket T-4D5E6F created.' })).toBeVisible();
  await expect(thread.getByRole('link', { name: 'Open ticket' })).toHaveAttribute('href', '#business-support/bst_wa_1');
  expect(sent.tickets).toEqual([{ source: 'whatsapp', ref_id: 'wacv_ahmed', severity: 'normal' }]);
});

test('a conversation that already has an open ticket links to it instead', async ({ page }) => {
  await mockApi(page, { ticketConflict: true });
  await openInbox(page);
  await page.locator('.waConversation').filter({ hasText: 'أحمد' }).click();
  await page.locator('.waThread').getByRole('button', { name: 'Create ticket' }).click();
  const dialog = page.getByRole('dialog', { name: 'Create a support ticket' });
  await dialog.getByLabel('Title').fill('Price question for 205/55R16');
  await dialog.getByRole('button', { name: 'Create ticket' }).click();
  await expect(dialog.getByRole('alert')).toContainText('already linked to open ticket T-9Z8Y7X');
  await expect(dialog.getByRole('link', { name: 'Open T-9Z8Y7X' })).toHaveAttribute('href', '#business-support/bst_wa_open');
});

test('search, filters, and a failed load that can be retried', async ({ page }) => {
  await mockApi(page, { failList: true });
  await openInbox(page);
  await expect(page.getByRole('alert')).toContainText('The database is not reachable.');
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.locator('.waConversation')).toHaveCount(2);
  await page.getByRole('button', { name: 'Unread', exact: true }).click();
  await expect(page.locator('.waConversation')).toHaveCount(1);
  await page.getByRole('button', { name: 'All', exact: true }).click();
  await page.getByRole('searchbox', { name: 'Search conversations' }).fill('nothing like this');
  await page.getByRole('searchbox', { name: 'Search conversations' }).press('Enter');
  await expect(page.getByText('No matching conversations')).toBeVisible();
  await page.getByRole('button', { name: 'Show all conversations' }).click();
  await expect(page.locator('.waConversation')).toHaveCount(2);
});

test('an owner connects the number from Setup; the form says what is missing', async ({ page }) => {
  const sent = await mockApi(page, { account: notConnected, conversations: [] });
  await openInbox(page);
  await expect(page.getByText('WhatsApp is not connected yet')).toBeVisible();
  await page.getByRole('button', { name: 'Connect WhatsApp' }).click();
  await expect(page.locator('.waWebhook code')).toHaveText('http://localhost:8000/api/v1/whatsapp/webhook');
  await page.getByLabel('Phone number ID').fill('106540352242922');
  await page.getByLabel('WhatsApp Business Account ID').fill('102290129340398');
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('enter the access token, the app secret and a verify token');
  await page.getByLabel('Permanent access token (system user)').fill('example-token');
  await page.getByLabel('App secret').fill('example-secret');
  await page.getByRole('button', { name: 'Generate' }).click();
  await page.getByLabel('Your own WhatsApp number (for the test)').fill('+966 500 000 999');
  const retention = page.getByLabel('Keep message history');
  await expect(retention.locator('option').first()).toHaveText('Server default (365 days)');
  await retention.selectOption('90');
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Connected as Tyre Shop' })).toBeVisible();
  expect(sent.saves).toHaveLength(1);
  const saved = sent.saves[0];
  expect(saved).toMatchObject({ phone_number_id: '106540352242922', business_account_id: '102290129340398', access_token: 'example-token', app_secret: 'example-secret', owner_test_number: '966500000999', message_retention_days: 90 });
  expect(String(saved.verify_token)).toMatch(/^[A-Za-z0-9_-]{32}$/);
  await expect(page.getByLabel('Permanent access token (system user)')).toHaveValue('');
});

test('an owner puts a template on the auto-send list', async ({ page }) => {
  const sent = await mockApi(page);
  await openInbox(page);
  await page.getByRole('button', { name: 'Templates', exact: true }).click();
  await expect(page.locator('.waTemplateCard')).toHaveCount(2);
  await page.locator('.waTemplateCard').filter({ hasText: 'order_update' }).getByRole('checkbox').click();
  await expect(page.locator('.waTemplateCard').filter({ hasText: 'order_update' }).getByRole('checkbox')).toBeChecked();
  await expect(page.getByText('order_update is now sent without asking.')).toBeVisible();
  expect(sent.saves[0]).toMatchObject({ auto_send_templates: ['order_update'] });
  expect(sent.saves[0]).not.toHaveProperty('access_token');
});

test('members read and ask to send but cannot approve or change the setup; viewers are told who can', async ({ page }) => {
  await mockApi(page, { role: 'member' });
  await openInbox(page);
  await page.getByRole('button', { name: 'Setup', exact: true }).click();
  await expect(page.getByText('Only owners change the connection')).toBeVisible();
  await page.goto('about:blank');
  const viewer = await page.context().newPage();
  await mockApi(viewer, { role: 'viewer' });
  await viewer.goto('/#whatsapp');
  await expect(viewer.getByRole('heading', { name: 'WhatsApp is for owners and members' })).toBeVisible();
});

for (const locale of ['en', 'ar'] as const) {
  test(`the inbox has no serious axe violations (${locale})`, async ({ page }) => {
    await mockApi(page);
    await useSavedLocale(page, locale);
    await page.goto('/#whatsapp');
    await expect(page.locator('html')).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr');
    await page.locator('.waConversation').first().click();
    await expect(page.locator('.waBubble').first()).toBeVisible();
    const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
    const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
    expect(serious).toEqual([]);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
}

test('in Arabic the inbox reads right to left and customer text keeps its own direction', async ({ page }) => {
  await mockApi(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#whatsapp');
  await expect(page.getByRole('heading', { level: 1, name: 'واتساب' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'بانتظار الرد' })).toBeVisible();
  await page.locator('.waConversation').filter({ hasText: 'Sara Trading' }).click();
  await expect(page.locator('.waWindow')).toHaveText('انتهت نافذة الـ24 ساعة: القوالب فقط');
  await expect(page.locator('.waText').first()).toHaveAttribute('dir', 'auto');
});
