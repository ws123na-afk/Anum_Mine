import { expect, test, type Page } from '@playwright/test';
import { mockMailApi } from './support/mailApi';
import { mockMailDirectory } from './support/mailDirectoryApi';
import { mockMailRules, ruleHash, settingsHash, type RulesOptions } from './support/mailRulesApi';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Email Center phase 6 (docs/email.md#mailbox-settings): retention and background sync changes and
// forwarding rules that wait for an owner's approval with the payload hash, every state of both
// panels, the sidebar badge from the background sync, and the layout at five window sizes.

async function openSettings(page: Page, options: RulesOptions = {}, title = 'Mailbox settings') {
  await mockWorkspaceApi(page);
  await mockMailApi(page, { role: options.role });
  await mockMailDirectory(page);
  const rules = await mockMailRules(page, options);
  await page.goto('/#email');
  await expect(page.locator('.mailRow').first()).toBeVisible();
  await page.getByRole('button', { name: title }).click();
  const drawer = page.getByRole('dialog', { name: title });
  return { ...rules, drawer, keep: drawer.getByRole('region', { name: 'Retention and sync' }), forwarding: drawer.getByRole('region', { name: 'Forwarding rules' }) };
}

const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test('a retention change waits for approval with its payload hash, then applies', async ({ page }) => {
  const { keep, calls } = await openSettings(page);
  await expect(keep).toContainText('Checked');
  await expect(keep).toContainText('12 unread of 30 in the inbox.');
  await expect(keep.getByRole('button', { name: 'Ask to change' })).toBeDisabled();
  await keep.getByLabel('Sent messages written in ANUM').selectOption({ label: 'Delete after 30 days' });
  await keep.getByLabel('AI labels').selectOption({ label: 'Delete after 90 days' });
  await keep.getByRole('button', { name: 'Ask to change' }).click();
  const card = keep.getByRole('group', { name: 'Settings change waiting for approval' });
  await expect(card).toContainText('Sent messages: Delete after 30 days. Drafts: Keep while the mailbox is connected. AI labels: Delete after 90 days. Background check: on.');
  await expect(card.locator('code')).toHaveAttribute('title', settingsHash);
  expect(calls.asks).toEqual([{ sent_retention_days: 30, draft_retention_days: null, label_retention_days: 90, background_sync: true, base_version: 0 }]);
  await card.getByRole('button', { name: 'Approve' }).click();
  await expect(keep).toContainText('The new settings apply now.');
  await expect(card).toHaveCount(0);
  await expect(keep.getByLabel('Sent messages written in ANUM')).toHaveValue('30');
  expect(calls.approvals).toEqual(['approval_settings']);
});

test('a change asked after someone else changed the settings says so and shows them again', async ({ page }) => {
  const { keep } = await openSettings(page, { staleOnce: true });
  await keep.getByLabel('Unsent drafts').selectOption({ label: 'Delete after 7 days' });
  await keep.getByRole('button', { name: 'Ask to change' }).click();
  await expect(keep.getByRole('alert')).toContainText('The settings changed since you opened them.');
  await expect(keep.getByLabel('Sent messages written in ANUM')).toHaveValue('365');
});

test('a waiting change can be withdrawn, and members wait for an owner', async ({ page }) => {
  const { keep, calls } = await openSettings(page, { role: 'member' });
  await keep.getByLabel('Check this mailbox in the background').uncheck();
  await keep.getByRole('button', { name: 'Ask to change' }).click();
  const card = keep.getByRole('group', { name: 'Settings change waiting for approval' });
  await expect(card).toContainText('Background check: off.');
  await expect(card).toContainText('An owner of this workspace approves this.');
  await expect(card.getByRole('button', { name: 'Approve' })).toHaveCount(0);
  await card.getByRole('button', { name: 'Withdraw this change' }).click();
  await expect(keep).toContainText('The change was withdrawn. Nothing changed.');
  expect(calls.withdraws).toBe(1);
});

test('settings that fail to load say why and load again', async ({ page }) => {
  const { keep } = await openSettings(page, { failSettings: true });
  await expect(keep.getByRole('alert')).toContainText('The mailbox settings could not load');
  await keep.getByRole('button', { name: 'Try again' }).click();
  await expect(keep.getByLabel('Unsent drafts')).toBeVisible();
});

test('a forwarding rule: checks, approval with the hash, pause, resume and remove', async ({ page }) => {
  const { forwarding, calls } = await openSettings(page);
  await expect(forwarding).toContainText('No forwarding rules yet.');
  await forwarding.getByRole('button', { name: 'New rule' }).click();
  const form = forwarding.getByRole('form', { name: 'New forwarding rule' });
  await form.getByRole('button', { name: 'Ask for approval' }).click();
  await expect(form.getByRole('alert')).toContainText('Give the rule a name.');
  await form.getByLabel('Name').fill('Orders to partner');
  await form.getByRole('button', { name: 'Ask for approval' }).click();
  await expect(form.getByRole('alert')).toContainText('Say which mail to forward');
  await form.getByLabel('Sender contains').fill('client.example');
  await form.getByLabel('Forward to').fill('admin@example.com');
  await form.getByRole('button', { name: 'Ask for approval' }).click();
  await expect(form.getByRole('alert')).toContainText("cannot forward this mailbox's mail to itself");
  await form.getByLabel('Forward to').fill('partner@elsewhere.example, partner@elsewhere.example');
  await form.getByRole('button', { name: 'Ask for approval' }).click();
  await expect(forwarding).toContainText('The rule Orders to partner waits for an owner');
  expect(calls.creates).toEqual([{ name: 'Orders to partner', from_contains: 'client.example', subject_contains: '', forward_to: ['partner@elsewhere.example'] }]);
  const row = forwarding.getByRole('listitem').filter({ hasText: 'Orders to partner' });
  await expect(row).toContainText('Waiting for approval');
  const card = row.getByRole('group', { name: 'Waiting for approval' });
  await expect(card.locator('code')).toHaveAttribute('title', ruleHash);
  await card.getByRole('button', { name: 'Approve' }).click();
  await expect(forwarding).toContainText('The rule Orders to partner is on.');
  await expect(row).toContainText('Forwarding');
  await row.getByRole('button', { name: 'Pause the rule Orders to partner' }).click();
  await expect(row).toContainText('Paused');
  await row.getByRole('button', { name: 'Resume the rule Orders to partner' }).click();
  await expect(row).toContainText('Waiting for approval');
  expect(calls.resumes).toHaveLength(1);
  page.once('dialog', (dialog) => void dialog.accept());
  await row.getByRole('button', { name: 'Remove the rule Orders to partner' }).click();
  await expect(forwarding).toContainText('The rule Orders to partner was removed.');
  await expect(forwarding).toContainText('No forwarding rules yet.');
});

test('editing a forwarding rule asks again, and a failed list loads again', async ({ page }) => {
  const { forwarding, calls } = await openSettings(page, { rules: 'some', failRules: true });
  await expect(forwarding.getByRole('alert')).toContainText('Forwarding rules could not load');
  await forwarding.getByRole('button', { name: 'Try again' }).click();
  const row = forwarding.getByRole('listitem').filter({ hasText: 'Supplier orders' });
  await expect(row).toContainText('Forwarded 4 messages.');
  await row.getByRole('button', { name: 'Edit the rule Supplier orders' }).click();
  const form = forwarding.getByRole('form', { name: 'Edit the rule' });
  await expect(form.getByLabel('Forward to')).toHaveValue('team@example.com');
  await form.getByLabel('Subject contains').fill('Order');
  await form.getByRole('button', { name: 'Ask for approval' }).click();
  await expect(row).toContainText('Waiting for approval');
  expect(calls.patches).toEqual([{ name: 'Supplier orders', from_contains: 'supplier.example', subject_contains: 'Order', forward_to: ['team@example.com'] }]);
});

test('the sidebar badge shows the background count while the Email page is closed', async ({ page }) => {
  await mockWorkspaceApi(page);
  await mockMailApi(page);
  const { calls } = await mockMailRules(page, { unreadTotal: 9 });
  await page.goto('/#home');
  const email = page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: /Email/ }).first();
  await expect(email.locator('.navCount')).toHaveText('9');
  expect(calls.unreadReads).toBeGreaterThan(0);
});

for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [768, 1024], [390, 844]] as const) {
  for (const locale of ['en', 'ar'] as const) {
    test(`mailbox settings fit ${width}x${height} in ${locale}`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      await useSavedLocale(page, locale);
      const { drawer } = await openSettings(page, { rules: 'some' }, locale === 'ar' ? 'إعدادات الصندوق' : 'Mailbox settings');
      await expect(drawer.locator('.mailrKeep select').first()).toBeVisible();
      await expect(drawer.locator('.mailrRule').first()).toBeVisible();
      // With a change waiting for approval, the card with its hash must fit too.
      await drawer.locator('.mailrKeep select').first().selectOption('30');
      await drawer.locator('.mailrKeep button[type=submit]').click();
      await expect(drawer.locator('.mailrApproval')).toBeVisible();
      expect(await noHorizontalOverflow(page)).toBe(true);
      for (const box of await drawer.locator('.mailsSection, .mailrRule, .mailrKeep select, .mailsRowActions button, .mailrApproval, .mailrApproval button').evaluateAll((items) => items.map((item) => item.getBoundingClientRect().toJSON() as DOMRect))) {
        expect(box.x).toBeGreaterThanOrEqual(-1);
        expect(box.x + box.width).toBeLessThanOrEqual(width + 1);
      }
      const sections = drawer.locator('.mailsSection');
      const count = await sections.count();
      for (const index of [count - 2, count - 1]) await sections.nth(index).screenshot({ path: `test-results/mail-rules-${locale}-${width}-${index === count - 1 ? 'rules' : 'keep'}.png` });
    });
  }
}
