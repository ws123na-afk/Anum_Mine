import { expect, test } from '@playwright/test';
import { REPLY_HASH, mockSupport } from './support/businessSupportApi';

// Business support tickets (docs/business.md#support-tickets): the main path against a mocked API.
// An empty queue, a ticket opened by hand, assigned and worked, then an email ticket whose reply is
// drafted and handed to Approvals (never sent from here), resolved with a resolution; plus the
// denied, error and owners-only states.

test('open, assign, work, reply through Approvals and resolve a ticket', async ({ page }) => {
  const mock = await mockSupport(page);
  await page.goto('/#business-support');
  await expect(page.getByRole('heading', { name: 'No tickets yet' })).toBeVisible();
  await expect(page.getByText(/There is no 24\/7 cover/)).toBeVisible();
  await expect(page.getByRole('link', { name: 'Open the email inbox' })).toHaveAttribute('href', '#email');

  // A ticket opened by hand.
  await page.getByRole('button', { name: 'New ticket' }).first().click();
  const form = page.getByRole('form', { name: 'New ticket' });
  await form.getByLabel('Title', { exact: true }).fill('Dashboard sync stopped');
  await form.getByLabel('What is wrong').fill('The nightly sync has not run since Monday.');
  await form.getByLabel('Severity').selectOption('high');
  await expect(form.getByText(/response target of 4 working hours/)).toBeVisible();
  await form.getByLabel('Requester name').fill('Sara Khan');
  await form.getByLabel('E-mail').fill('sara@example.com');
  await form.getByRole('button', { name: 'Open ticket' }).click();
  const ticket = page.getByRole('dialog', { name: 'Dashboard sync stopped' });
  await expect(ticket.getByText('T-1001', { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/#business-support\/btkt_1$/);
  await expect(ticket.getByText(/opened by hand, so ANUM has no conversation/)).toBeVisible();

  // Assign to me, start work, and a response made by phone.
  await ticket.getByRole('button', { name: 'Assign to me' }).click();
  await expect(ticket.getByText('Assigned to user_local.')).toBeVisible();
  await ticket.getByRole('button', { name: 'Start work' }).click();
  await expect(ticket.getByText('In progress', { exact: true }).first()).toBeVisible();
  const note = ticket.getByRole('form', { name: 'Add a note' });
  await note.getByLabel('Note', { exact: true }).fill('Called Sara, the export job is stuck.');
  await note.getByLabel(/This was a response to the requester/).check();
  await note.getByRole('button', { name: 'Add note' }).click();
  await expect(ticket.getByText('Called Sara, the export job is stuck.')).toBeVisible();
  await ticket.getByRole('button', { name: 'Close', exact: true }).first().click();
  await expect(page.getByRole('row', { name: /Dashboard sync stopped/ })).toBeVisible();

  // An email ticket: a reply is drafted and waits for an owner with its payload hash.
  const emailId = mock.seed({ title: 'Invoice export fails', source: 'email', source_ref: 'email_msg_1', requester_email: 'ops@example.com', severity: 'normal' });
  await page.goto(`/#business-support/${emailId}`);
  const email = page.getByRole('dialog', { name: 'Invoice export fails' });
  await expect(email.getByRole('link', { name: 'Open the Email conversation' })).toHaveAttribute('href', '#email');
  await email.getByLabel('Reply by Email').fill('We found the cause and will fix it today during working hours.');
  await email.getByRole('button', { name: 'Save draft' }).click();
  await expect(email.getByText('Draft', { exact: true })).toBeVisible();
  await email.getByRole('button', { name: 'Ask for approval' }).click();
  const waiting = email.getByRole('group', { name: 'Reply waiting for an owner' });
  await expect(waiting.getByText(/Nothing is sent until an owner approves/)).toBeVisible();
  await expect(waiting.getByText(REPLY_HASH.slice(0, 12), { exact: false })).toBeVisible();
  await expect(waiting.getByRole('link', { name: 'Open Approvals' })).toHaveAttribute('href', '#approvals');
  await expect(page.getByRole('button', { name: /^Send/ })).toHaveCount(0);

  // Resolve: a resolution and a sentence are required.
  await email.getByRole('button', { name: 'Resolve' }).click();
  const resolve = page.getByRole('form', { name: 'Resolve the ticket' });
  await resolve.getByLabel('How it was resolved').fill('Fixed');
  await resolve.getByRole('button', { name: 'Resolve' }).click();
  await expect(resolve.getByText('Choose a resolution.')).toBeVisible();
  await resolve.getByRole('combobox', { name: 'Resolution' }).selectOption('fixed');
  await resolve.getByRole('button', { name: 'Resolve' }).click();
  await expect(resolve.getByText(/at least 10 characters/)).toBeVisible();
  await resolve.getByLabel('How it was resolved').fill('Restarted the export worker and re-ran March.');
  await resolve.getByRole('button', { name: 'Resolve' }).click();
  await expect(email.getByText('Ticket resolved.')).toBeVisible();
  await expect(email.getByText(/Resolved as Fixed: Restarted the export worker/)).toBeVisible();
  const status = mock.calls.filter((call) => call.path.endsWith('/status')).map((call) => call.body as { to: string; resolution_code?: string });
  expect(status.at(-1)).toMatchObject({ to: 'resolved', resolution_code: 'fixed' });
  expect(mock.calls.some((call) => call.path.endsWith('/request-approval'))).toBe(true);
});

test('the customer portal conversation is read and answered in the ticket', async ({ page }) => {
  const mock = await mockSupport(page);
  mock.seed({ title: 'Totals differ in the report', account_id: 'bacc_1', created_by: 'portal:bpacc_1', portal_link: true, source: 'manual',
    portal_messages: [{ id: 'btmsg_0', ticket_id: 'x', account_id: 'bacc_1', author: 'customer', author_name: 'Sara Khan', access_id: 'bpacc_1', contact_id: 'bcon_1',
      body: 'The VAT line is missing on page 2.', client_key: 'k1', read_at: null, read_by: null, created_by: 'portal:bpacc_1', created_at: new Date().toISOString(), updated_at: new Date().toISOString() }] });
  // The ticket's account and its contact, as the accounts API answers them.
  const account = { id: 'bacc_1', name: 'Acme Trading', contacts: 1, open_leads: 0, do_not_contact: false };
  await page.route(/http:\/\/localhost:8000\/api\/v1\/business\/accounts(\/bacc_1)?(\?.*)?$/, (route) => route.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify(route.request().url().includes('/accounts/bacc_1') ? { ...account, contacts: [{ id: 'bcon_1', account_id: 'bacc_1', name: 'Sara Khan' }], leads: [] }
      : { items: [account], total: 1, limit: 100, offset: 0 }) }));
  await page.goto('/#business-support');
  await expect(page.getByText('1 new portal message')).toBeVisible();
  await page.getByRole('button', { name: 'Totals differ in the report' }).click();
  const ticket = page.getByRole('dialog', { name: 'Totals differ in the report' });
  const thread = ticket.getByRole('region', { name: 'Customer portal conversation' });
  await expect(thread.getByText('The VAT line is missing on page 2.')).toBeVisible();
  await expect.poll(() => mock.calls.some((call) => call.path.endsWith('/portal-messages/read'))).toBe(true);
  await thread.getByRole('button', { name: 'Answer in the portal' }).click();
  await expect(ticket.getByText('Write the answer first.')).toBeVisible();
  await thread.getByRole('textbox', { name: 'Answer in the portal' }).fill('Fixed, please check the report again.');
  await thread.getByRole('button', { name: 'Answer in the portal' }).click();
  await expect(ticket.getByText('Answered. The customer sees it in their portal.')).toBeVisible();
  await expect(thread.getByText('Fixed, please check the report again.')).toBeVisible();
  expect(mock.calls.find((call) => call.method === 'POST' && call.path.endsWith('/portal-messages'))?.body).toEqual({ body: 'Fixed, please check the report again.' });
});

test('a member cannot reopen a closed ticket and reads the policy', async ({ page }) => {
  const mock = await mockSupport(page, { role: 'member' });
  const closedId = mock.seed({ title: 'Old login issue', status: 'closed', resolution_code: 'fixed', resolution_summary: 'Reset the password policy.', resolved_at: new Date().toISOString() });
  await page.goto(`/#business-support/${closedId}`);
  const ticket = page.getByRole('dialog', { name: 'Old login issue' });
  await expect(ticket.getByText('Only workspace owners reopen a closed ticket.')).toBeVisible();
  await ticket.getByRole('button', { name: 'Reopen' }).click();
  await expect(ticket.getByText(/Not allowed: Only workspace owners can do this/)).toBeVisible();
  await expect(ticket.getByRole('button', { name: 'Delete ticket' })).toHaveCount(0);

  await page.goto('/#business-settings');
  const policy = page.getByRole('form', { name: 'Support policy' });
  await expect(policy.getByText(/There is no 24\/7 cover/)).toBeVisible();
  await expect(policy.getByText('Only workspace owners change the support policy.')).toBeVisible();
  await expect(policy.getByLabel('Time zone')).toBeDisabled();
});

test('an owner saves the support policy', async ({ page }) => {
  const mock = await mockSupport(page);
  await page.goto('/#business-settings');
  const policy = page.getByRole('form', { name: 'Support policy' });
  await policy.getByLabel('Friday').check();
  await policy.getByLabel('Response hours for Critical').fill('1');
  await policy.getByRole('button', { name: 'Save policy' }).click();
  await expect(policy.getByText(/Support policy saved/)).toBeVisible();
  const saved = mock.calls.find((call) => call.path === '/policy')?.body as { work_days: number[]; targets: { critical: { response_hours: number } } };
  expect(saved.work_days).toEqual([0, 1, 2, 3, 4, 6]);
  expect(saved.targets.critical.response_hours).toBe(1);
});

test('denied and failed support states explain themselves', async ({ page }) => {
  await mockSupport(page, { role: 'viewer' });
  await page.goto('/#business-support');
  await expect(page.getByRole('heading', { name: 'Business is not available to you here' })).toBeVisible();
  await expect(page.getByText(/holds contacts' personal data/)).toBeVisible();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockSupport(page, { listStatus: 500 });
  await page.reload();
  // The API's bare "Database unavailable" reads as the server's outage (lib/errors.ts describeError).
  await expect(page.getByText('The server could not reach its database')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});
