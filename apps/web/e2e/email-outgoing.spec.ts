import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockMailApi, sendHash, type MailMockOptions } from './support/mailApi';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Email Center outgoing mail (docs/email.md#attachments, #scheduled-send): files attached to a new
// message (checked before upload, refused by the scanner, not scanned on a server without one),
// shown in the approval, and a message scheduled for later, approved before it is due, listed under
// Scheduled and cancelled again.

const PDF = { name: 'quote.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 example') };
const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

async function compose(page: Page, options: MailMockOptions = {}) {
  await mockWorkspaceApi(page);
  const mock = await mockMailApi(page, options);
  await page.goto('/#email');
  await expect(page.getByRole('heading', { level: 1, name: 'Email' })).toBeVisible();
  await page.getByRole('button', { name: 'Write', exact: true }).first().click();
  const composer = page.getByRole('article', { name: 'New message' });
  await composer.getByLabel('To').fill('sara@example.com');
  await composer.getByLabel('Subject').fill('Quote for next week');
  await composer.getByLabel('Message').fill('The quote is attached.');
  return { ...mock, composer };
}

test('files are checked, attached, shown in the approval and removed', async ({ page }) => {
  const { composer, calls } = await compose(page, { scannerRefuses: ['bad.pdf'] });
  const files = composer.getByRole('region', { name: 'Attachments' });
  await expect(files.getByText(/Up to 10 files, 10\.0 MB each and 20\.0 MB in all\. Accepted: \.csv, \.docx/)).toBeVisible();
  await files.getByLabel('Choose files').setInputFiles([PDF, { name: 'tool.exe', mimeType: 'application/octet-stream', buffer: Buffer.from('MZ example') }]);
  await expect(files.getByText('quote.pdf')).toBeVisible();
  await expect(files.getByText('tool.exe is a type email attachments do not accept.')).toBeVisible();
  await expect(files.getByText('Attached 1 file.')).toBeVisible();
  expect(calls.uploads).toEqual(['quote.pdf']);
  await files.getByLabel('Choose files').setInputFiles([{ ...PDF, name: 'bad.pdf' }]);
  await expect(files.getByRole('alert')).toContainText('The malware scanner refused bad.pdf: Example.Test.Signature');
  await expect(files.getByText('1 file, 16 B')).toBeVisible();

  await composer.getByRole('button', { name: 'Send', exact: true }).click();
  const approval = composer.getByRole('group', { name: 'Approve sending' });
  await expect(approval).toContainText('With 1 file.');
  await expect(approval.locator('code')).toHaveAttribute('title', sendHash);
  // Removing a file withdraws the approval.
  await files.getByRole('button', { name: 'Remove quote.pdf' }).click();
  await expect(files.getByText('Removed quote.pdf.')).toBeVisible();
  await expect(approval).toHaveCount(0);
  expect(calls.removals).toEqual(['mailfile_1']);
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('a server without a malware scanner marks files not scanned', async ({ page }) => {
  const { composer } = await compose(page, { noScanner: true });
  await composer.getByLabel('Choose files').setInputFiles([PDF]);
  const files = composer.getByRole('region', { name: 'Attachments' });
  await expect(files.getByText('Not scanned', { exact: true })).toBeVisible();
  await expect(files.getByText(/no malware scanner, so ANUM checked the file type only/)).toBeVisible();
});

test('a message is scheduled, approved before it is due, listed and cancelled', async ({ page, isMobile }) => {
  const { composer, calls } = await compose(page);
  await composer.getByRole('button', { name: 'Schedule', exact: true }).click();
  const picker = composer.getByRole('group', { name: 'Send later' });
  await expect(picker.getByText(/Your time zone: /)).toBeVisible();
  await picker.getByLabel('Send at').fill('2020-01-01T08:00');
  await picker.getByRole('button', { name: 'Schedule', exact: true }).click();
  await expect(picker.getByRole('alert')).toContainText('Pick a time at least a minute from now');
  await picker.getByRole('button', { name: /^Tomorrow, / }).click();
  const approval = composer.getByRole('group', { name: 'Approve sending' });
  await expect(approval).toContainText('Approving schedules it for');
  const sent = calls.sends.at(-1) as { idempotency_key: string; send_at: string };
  expect(sent.idempotency_key).toMatch(/^send-/);
  const due = new Date(sent.send_at);
  expect(due.getTime()).toBeGreaterThan(Date.now());
  expect(due.getHours()).toBe(8);
  await expect(composer.getByText(/Goes at .* once an owner approves it/)).toBeVisible();
  await approval.getByRole('button', { name: 'Approve schedule' }).click();
  await expect(composer.getByText(/^Scheduled for /).first()).toBeVisible();
  await expect(composer.locator('.statusBadge').first()).toHaveText('Scheduled');
  expect(calls.approvals).toEqual([{ payload_hash: sendHash }]);
  await expect(composer.getByRole('button', { name: 'Send', exact: true })).toHaveCount(0);

  // On a phone the composer covers the rail: go back to the list, then open the message from Scheduled.
  if (isMobile) await composer.getByRole('button', { name: 'Back to the list' }).click();
  const rail = page.locator('details.mailoRail');
  if (!(await rail.evaluate((el) => (el as HTMLDetailsElement).open))) await rail.locator('summary').click();
  await expect(rail.locator('.mailOutboxCount')).toHaveText('1');
  await rail.getByRole('button', { name: /Quote for next week/ }).click();
  await expect(composer.getByText(/^Scheduled for /).first()).toBeVisible();

  await composer.getByRole('button', { name: 'Cancel schedule' }).click();
  await expect(composer.getByText('Schedule cancelled. The message is a draft again.')).toBeVisible();
  expect(calls.cancels).toHaveLength(1);
  expect(await noHorizontalOverflow(page)).toBe(true);
  if (isMobile) await composer.getByRole('button', { name: 'Back to the list' }).click();
  await expect(rail.getByText('Nothing scheduled. In a message, choose Schedule to send it later.')).toBeVisible();
});

test('members schedule but an owner approves', async ({ page }) => {
  const { composer } = await compose(page, { role: 'member' });
  await composer.getByRole('button', { name: 'Schedule', exact: true }).click();
  await composer.getByRole('group', { name: 'Send later' }).getByRole('button', { name: /^Monday, / }).click();
  const approval = composer.getByRole('group', { name: 'Approve sending' });
  await expect(approval).toContainText('An owner approves sending.');
  await expect(approval.getByRole('button', { name: 'Approve schedule' })).toHaveCount(0);
});

test('the scheduled list says when it cannot load and offers Try again', async ({ page }) => {
  await mockWorkspaceApi(page);
  await mockMailApi(page, { scheduledFails: true });
  await page.goto('/#email');
  const rail = page.locator('details.mailoRail');
  if (!(await rail.evaluate((el) => (el as HTMLDetailsElement).open))) await rail.locator('summary').click();
  await expect(rail.getByText('Scheduled messages could not load.')).toBeVisible();
  await expect(rail.getByRole('button', { name: 'Try again' })).toBeVisible();
});

for (const mode of ['en', 'ar', 'light'] as const) {
  test(`attachments and the send-later picker have no serious axe violations (${mode})`, async ({ page }) => {
    if (mode === 'ar') await useSavedLocale(page, 'ar');
    if (mode === 'light') await page.addInitScript(() => { try { localStorage.setItem('anum.theme', 'light'); } catch { /* storage blocked */ } });
    await mockWorkspaceApi(page);
    await mockMailApi(page);
    await page.goto('/#email');
    await page.locator('.mailRow').first().waitFor();
    await page.locator('button:has(svg.lucide-pen-line)').first().click();
    const composer = page.locator('article.mailComposer');
    await composer.locator('input[dir=ltr]').first().fill('sara@example.com');
    await composer.locator('input[type=file]').setInputFiles([PDF]);
    await expect(composer.locator('.mailoFile')).toHaveCount(1);
    await composer.locator('.mailoSchedule, button:has(svg.lucide-calendar-clock)').first().click();
    await expect(composer.locator('input[type=datetime-local]')).toBeVisible();
    await composer.locator('.mailoQuick button').first().click();
    await expect(composer.locator('.mailApproval')).toBeVisible();
    const violations = (await new AxeBuilder({ page }).include('article.mailComposer').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze()).violations
      .filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(', ')}`);
    expect(violations).toEqual([]);
  });
}
