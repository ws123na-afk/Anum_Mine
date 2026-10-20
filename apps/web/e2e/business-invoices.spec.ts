import { expect, test, type Page } from '@playwright/test';
import { mockInvoices, PORTAL_TOKEN } from './support/businessInvoicesApi';
import { mockBusiness } from './support/businessApi';
import { GATE_SIZES } from './support/gatePages';
import { sweep } from './support/sweep';
import { useSavedLocale } from './support/workspaceApi';

// Business invoices (docs/business.md#invoices) and the owner's side of the customer portal
// (docs/business-portal.md) against a mocked API: a draft from milestones and a manual line, the
// approval request that an owner answers in Approvals, payments by hand until it is paid, void,
// the printable copy, the billing profile, a portal link shown once, a customer's decision on a
// proposal, and the denied, empty, failed, member and Arabic states.

const invoiceId = (page: Page) => /#business-invoices\/([^/]+)$/.exec(page.url())![1];

test('draft from milestones, ask for approval, send, record payments until paid', async ({ page }) => {
  const mock = await mockInvoices(page);
  await page.goto('/#business-invoices');
  await expect(page.getByRole('heading', { name: 'No invoices yet' })).toBeVisible();
  await page.getByRole('button', { name: 'New invoice' }).first().click();

  const form = page.getByRole('form', { name: 'New invoice' });
  await form.getByLabel('Client account').selectOption({ label: 'Acme Trading' });
  await form.getByLabel('Project').selectOption({ label: 'Website rebuild' });
  await expect(form.getByText('Change order 1')).toBeVisible();
  await expect(form.getByRole('checkbox', { name: /Launch/ })).toBeDisabled();
  await expect(form.getByText('Already marked paid.')).toBeVisible();
  // Nothing chosen yet: the form says so before it asks the server.
  await form.getByRole('button', { name: 'Create draft' }).click();
  await expect(form.getByText(/Choose something to invoice/)).toBeVisible();

  await form.getByRole('checkbox', { name: /Design approved/ }).check();
  await form.getByRole('checkbox', { name: /Extra pages/ }).check();
  await form.getByRole('button', { name: 'Add a line' }).click();
  const line = form.getByRole('group', { name: 'Line 1' });
  await line.getByLabel('Description').fill('Travel to site');
  await line.getByLabel('Unit price', { exact: true }).fill('100');
  await form.getByLabel('Customer reference').fill('PO 4471');
  await form.getByRole('button', { name: 'Create draft' }).click();

  const drawer = page.getByRole('dialog');
  await expect(drawer.getByText('Draft', { exact: true }).first()).toBeVisible();
  await expect(drawer.getByRole('row', { name: /Design approved/ })).toBeVisible();
  const totals = drawer.getByRole('group', { name: 'Invoice totals' }).or(drawer.locator('dl[aria-label="Invoice totals"]'));
  await expect(totals).toContainText('SAR 1,600.00');
  await expect(totals).toContainText('SAR 240.00');
  await expect(totals).toContainText('SAR 1,840.00');
  expect(mock.calls.find((call) => call.method === 'POST' && call.path === '/invoices')?.body).toMatchObject({ project_id: 'bprj_1', milestone_ids: ['bmil_1', 'bmil_2'], customer_reference: 'PO 4471' });
  const id = invoiceId(page);

  // The owner approves the send in Approvals, not here.
  await drawer.getByRole('button', { name: 'Ask for approval to send' }).click();
  await expect(drawer.getByText('Waiting for an owner to approve sending this invoice')).toBeVisible();
  await expect(drawer.getByRole('link', { name: 'Open Approvals' })).toHaveAttribute('href', '#approvals');
  await expect(drawer.getByRole('button', { name: 'Cancel the request' })).toBeVisible();
  mock.approveSend(id);
  await page.reload();
  const sent = page.getByRole('dialog');
  await expect(sent.getByRole('heading', { name: 'INV-0001' })).toBeVisible();
  await expect(sent.getByText('Sent', { exact: true }).first()).toBeVisible();
  await expect(sent.getByRole('button', { name: 'Edit draft' })).toHaveCount(0);

  // Payments are recorded by hand; a payment above the balance is refused.
  await sent.getByRole('button', { name: 'Record a payment' }).click();
  const pay = page.getByRole('form', { name: 'Record a payment' });
  await expect(pay.getByLabel('Amount')).toHaveValue('1840.00');
  await pay.getByLabel('Amount').fill('2000');
  await pay.getByRole('button', { name: 'Save payment' }).click();
  await expect(pay.getByText(/more than the balance of SAR.1,840\.00/)).toBeVisible();
  await pay.getByLabel('Amount').fill('840');
  await pay.getByLabel('Reference').fill('TT-100');
  await pay.getByRole('button', { name: 'Save payment' }).click();
  await expect(sent.getByText('Partly paid')).toBeVisible();
  await expect(sent.getByText('TT-100')).toBeVisible();
  await sent.getByRole('button', { name: 'Record a payment' }).click();
  const rest = page.getByRole('form', { name: 'Record a payment' });
  await expect(rest.getByLabel('Amount')).toHaveValue('1000.00');
  await rest.getByRole('button', { name: 'Save payment' }).click();
  await expect(sent.getByText('Payment recorded. The invoice is paid in full.')).toBeVisible();
  await expect(sent.getByText('Paid in full', { exact: true })).toBeVisible();
  await expect(sent.getByRole('button', { name: 'Record a payment' })).toHaveCount(0);
  await sent.getByRole('button', { name: 'Close' }).first().click();

  const row = page.getByRole('row', { name: /INV-0001/ });
  await expect(row).toContainText('Paid');
  const figures = page.getByRole('group', { name: 'Figures in SAR' });
  await expect(figures).toContainText('Collected this month');
  await expect(figures).toContainText('SAR 1,840.00');
});

test('a draft is edited, a server refusal shows its sentence, and a rejected request returns with its reason', async ({ page }) => {
  const mock = await mockInvoices(page);
  await page.goto('/#business-invoices');
  await page.getByRole('button', { name: 'New invoice' }).first().click();
  const form = page.getByRole('form', { name: 'New invoice' });
  await form.getByLabel('Client account').selectOption({ label: 'Acme Trading' });
  await form.getByRole('checkbox', { name: /Domain renewal/ }).check();
  await form.getByLabel('Markup on costs (%)').fill('25');
  await form.getByRole('button', { name: 'Create draft' }).click();
  const drawer = page.getByRole('dialog');
  await expect(drawer.getByRole('row', { name: /Domain renewal/ })).toBeVisible();
  await expect(drawer.locator('dl[aria-label="Invoice totals"]')).toContainText('SAR 115.00');

  await drawer.getByRole('button', { name: 'Edit draft' }).click();
  const edit = drawer.getByRole('form', { name: 'Edit draft' });
  await edit.getByLabel('Name', { exact: true }).fill('');
  await edit.getByRole('button', { name: 'Save changes' }).click();
  await expect(edit.getByText(/Name the customer the invoice is made out to/)).toBeVisible();
  await edit.getByLabel('Name', { exact: true }).fill('Acme Trading LLC');
  await edit.getByLabel('Customer reference').fill('PO 9');
  await edit.getByRole('button', { name: 'Add a line' }).click();
  await edit.getByLabel('Description').fill('Setup');
  await edit.getByLabel('Unit price', { exact: true }).fill('200');
  await edit.getByRole('button', { name: 'Save changes' }).click();
  await expect(drawer.getByText('Changes saved.')).toBeVisible();
  await expect(drawer.getByText('Acme Trading LLC')).toBeVisible();
  await expect(drawer.locator('dl[aria-label="Invoice totals"]')).toContainText('SAR 345.00');

  const id = invoiceId(page);
  await drawer.getByRole('button', { name: 'Ask for approval to send' }).click();
  await expect(drawer.getByText('Waiting for an owner to approve sending this invoice')).toBeVisible();
  mock.rejectSend(id, 'Wrong tax number');
  await page.reload();
  const again = page.getByRole('dialog');
  await expect(again.getByText(/The last request was not approved\. Rejected by user_owner: Wrong tax number/)).toBeVisible();
  await expect(again.getByRole('button', { name: 'Ask for approval to send' })).toBeVisible();

  // A draft can be deleted after a confirmation; nothing was sent.
  await again.getByRole('button', { name: 'Delete draft' }).click();
  const confirm = page.getByRole('alertdialog', { name: 'Delete this draft?' });
  await confirm.getByRole('button', { name: 'Delete draft' }).click();
  await expect(page.getByRole('heading', { name: 'No invoices yet' })).toBeVisible();
});

test('an owner voids a sent invoice with a reason', async ({ page }) => {
  const mock = await mockInvoices(page);
  const id = mock.seedSent();
  await page.goto(`/#business-invoices/${id}`);
  const drawer = page.getByRole('dialog');
  await expect(drawer.getByRole('heading', { name: 'INV-0001' })).toBeVisible();
  await drawer.getByRole('button', { name: 'Void invoice' }).click();
  const reason = page.getByRole('form', { name: 'Void invoice INV-0001' });
  await reason.getByRole('button', { name: 'Void invoice' }).click();
  await expect(reason.getByText(/reason/i)).toBeVisible();
  await reason.getByLabel('Why is it being voided?').fill('Issued to the wrong company');
  await reason.getByRole('button', { name: 'Void invoice' }).click();
  await expect(drawer.getByText('The invoice was voided. Its lines can be invoiced again.')).toBeVisible();
  await expect(drawer.getByText(/Voided on .*: Issued to the wrong company/)).toBeVisible();
  await expect(drawer.getByRole('button', { name: 'Void invoice' })).toHaveCount(0);
  await drawer.getByRole('button', { name: 'Close' }).first().click();
  await expect(page.getByRole('row', { name: /INV-0001/ })).toContainText('Void');
});

test('the printable copy opens in a new window through a blob URL, in the chosen language', async ({ page }) => {
  const mock = await mockInvoices(page);
  const id = mock.seedSent();
  await page.goto(`/#business-invoices/${id}`);
  const drawer = page.getByRole('dialog');
  await drawer.getByLabel('Language').selectOption('ar');
  const popup = page.waitForEvent('popup');
  await drawer.getByRole('button', { name: 'Print' }).click();
  const printable = await popup;
  await printable.waitForURL(/^blob:/);
  await expect(printable.getByRole('heading', { name: 'Invoice INV-0001' })).toBeVisible();
  expect(mock.calls.some((call) => call.path === `/invoices/${id}/print`)).toBe(true);
  await expect(drawer.getByText('The printable copy opened in a new window.')).toBeVisible();
});

test('overdue invoices are flagged, filtered and counted per currency', async ({ page }) => {
  const mock = await mockInvoices(page);
  mock.seedSent({ customer_reference: 'late one', issue_date: '2026-01-01', due_date: '2026-01-15' });
  mock.seedSent({ customer_reference: 'on time' });
  await page.goto('/#business-invoices');
  const late = page.getByRole('row', { name: /INV-0001/ });
  await expect(late).toContainText(/Overdue by \d+ days?/);
  await expect(page.getByRole('group', { name: 'Figures in SAR' })).toContainText('1 invoice past its due date');
  await page.getByLabel('Overdue only').check();
  await expect(page.getByRole('row', { name: /INV-0001/ })).toBeVisible();
  await expect(page.getByRole('row', { name: /INV-0002/ })).toHaveCount(0);
  await page.getByLabel('Overdue only').uncheck();
  await page.getByRole('searchbox', { name: 'Search by number, customer or reference' }).fill('on time');
  await page.getByRole('button', { name: 'Find' }).click();
  await expect(page.getByRole('row', { name: /INV-0002/ })).toBeVisible();
  await expect(page.getByRole('row', { name: /INV-0001/ })).toHaveCount(0);
  await page.getByLabel('Status').selectOption('paid');
  await expect(page.getByRole('heading', { name: 'Nothing matches' })).toBeVisible();
});

test('a member drafts and asks, but cannot void, remove payments or edit the billing profile', async ({ page }) => {
  const mock = await mockInvoices(page, { role: 'member' });
  const id = mock.seedSent();
  await page.goto(`/#business-invoices/${id}`);
  const drawer = page.getByRole('dialog');
  await expect(drawer.getByRole('button', { name: 'Record a payment' })).toBeVisible();
  await expect(drawer.getByRole('button', { name: 'Void invoice' })).toHaveCount(0);
  await expect(drawer.getByText('Only an owner can void a sent invoice.')).toBeVisible();
  await drawer.getByRole('button', { name: 'Close' }).first().click();
  await expect(page.getByRole('button', { name: 'New invoice' }).first()).toBeVisible();

  await page.goto('/#business-settings');
  const profile = page.getByRole('form', { name: 'Billing profile' });
  await expect(profile.getByLabel('Legal name')).toBeDisabled();
  await expect(profile.getByLabel('Legal name')).toHaveValue('Najd Digital Co.');
  await expect(page.getByText('Only an owner can change the billing profile.')).toBeVisible();
  await expect(profile.getByRole('button', { name: 'Save billing profile' })).toHaveCount(0);
});

test('an owner edits the billing profile and is told a sent invoice keeps the old one', async ({ page }) => {
  const mock = await mockInvoices(page);
  await page.goto('/#business-settings');
  const profile = page.getByRole('form', { name: 'Billing profile' });
  await expect(page.getByText(/A sent invoice keeps the profile it was sent with/)).toBeVisible();
  await profile.getByLabel('Bank details').fill('Bank: Other Bank\nIBAN: SA1111');
  await profile.getByLabel('Number prefix').fill('TOO-LONG-PREFIX');
  await profile.getByRole('button', { name: 'Save billing profile' }).click();
  await expect(profile.getByText(/1 to 8 letters or digits/)).toBeVisible();
  await profile.getByLabel('Number prefix').fill('NJD');
  await profile.getByRole('button', { name: 'Save billing profile' }).click();
  await expect(profile.getByText('Billing profile saved. It applies to invoices sent from now on.')).toBeVisible();
  expect(mock.calls.find((call) => call.method === 'PUT' && call.path === '/billing-profile')?.body).toMatchObject({ invoice_prefix: 'NJD', default_due_days: 14, bank_details: 'Bank: Other Bank\nIBAN: SA1111' });
});

test('a member without access gets the API sentence and no retry loop', async ({ page }) => {
  await mockInvoices(page, { listStatus: 403 });
  await page.goto('/#business-invoices');
  await expect(page.getByText(/Business is for workspace owners and members/).first()).toBeVisible();
});

test('a failed list offers a retry', async ({ page }) => {
  await mockInvoices(page, { listStatus: 500 });
  await page.goto('/#business-invoices');
  await expect(page.getByText(/could not reach its database/).first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' }).first()).toBeVisible();
});

test('a contract with no milestones is invoiced by a share of the contract', async ({ page }) => {
  await mockInvoices(page, { withContractShare: true });
  await page.goto('/#business-invoices');
  await page.getByRole('button', { name: 'New invoice' }).first().click();
  const form = page.getByRole('form', { name: 'New invoice' });
  await form.getByLabel('Client account').selectOption({ label: 'Acme Trading' });
  await form.getByLabel('Project').selectOption({ label: 'Website rebuild' });
  await expect(form.getByText(/This project has no milestones\. Invoice a share of the contract/)).toBeVisible();
  await form.getByLabel('Share of the contract to invoice').fill('9000');
  await form.getByRole('button', { name: 'Create draft' }).click();
  await expect(form.getByText(/Only .*5,750\.00 of the contract is left/)).toBeVisible();
  await form.getByLabel('Share of the contract to invoice').fill('2300');
  await form.getByRole('button', { name: 'Create draft' }).click();
  await expect(page.getByRole('dialog').locator('dl[aria-label="Invoice totals"]')).toContainText('SAR 2,300.00');
});

test('the invoices tab reads in Arabic, right to left', async ({ page }) => {
  const mock = await mockInvoices(page);
  mock.seedSent();
  await page.addInitScript(() => window.localStorage.setItem('anum.locale', 'ar'));
  await page.goto('/#business-invoices');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('row', { name: /INV-0001/ })).toContainText('مرسلة');
  await expect(page.getByRole('button', { name: 'فاتورة جديدة' }).first()).toBeVisible();
  await page.getByRole('button', { name: 'INV-0001' }).click();
  await expect(page.getByRole('dialog').getByRole('button', { name: 'تسجيل دفعة' })).toBeVisible();
});

test('a portal link is shown once, can be copied, listed and turned off by an owner', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => undefined);
  const mock = await mockInvoices(page);
  await page.goto('/#business-leads');
  await page.getByRole('button', { name: 'New lead' }).first().click();
  const leadForm = page.getByRole('form', { name: 'New lead' });
  await leadForm.getByLabel('Company name').fill('Gulf Mixers LLC');
  await leadForm.getByLabel('Name', { exact: true }).fill('Sara Khan');
  await leadForm.getByLabel('Consent basis').selectOption('inbound_inquiry');
  await leadForm.getByLabel('Lead title').fill('Fleet reporting');
  await leadForm.getByRole('button', { name: 'Add lead' }).click();
  const lead = page.getByRole('dialog', { name: 'Gulf Mixers LLC' });
  await expect(lead.getByRole('heading', { name: 'Customer portal' })).toBeVisible();
  await expect(lead.getByText('No link has been created for this client.')).toBeVisible();

  await lead.getByRole('button', { name: 'Create a portal link' }).click();
  const create = page.getByRole('form', { name: 'Create a portal link' });
  await expect(create.getByRole('option', { name: /Omar Ali/ })).toHaveAttribute('disabled', '');
  await create.getByLabel('Valid for (days, 1 to 365)').fill('400');
  await create.getByRole('button', { name: 'Create link' }).click();
  await expect(create.getByText('Enter whole days between 1 and 365.')).toBeVisible();
  await create.getByLabel('Valid for (days, 1 to 365)').fill('30');
  await create.getByLabel('Allow this person to approve or decline').uncheck();
  await create.getByRole('button', { name: 'Create link' }).click();

  const shown = page.getByRole('dialog', { name: 'Portal link for Sara Khan' });
  await expect(shown.getByText(/This link is a credential/)).toBeVisible();
  const address = shown.getByLabel('Portal link');
  await expect(address).toHaveValue(new RegExp(`/portal#access=${PORTAL_TOKEN}$`));
  expect(mock.calls.find((call) => call.method === 'POST' && call.path === '/portal/access')?.body).toMatchObject({ expires_in_days: 30, can_decide: false });
  await shown.getByRole('button', { name: 'Copy link' }).click();
  await expect(shown.getByRole('status')).toContainText(/Link copied|could not copy/);
  await shown.getByRole('button', { name: 'I have copied it' }).click();

  // The link is gone from the page once closed; the list shows who has access, never the token.
  await expect(page.getByText(PORTAL_TOKEN)).toHaveCount(0);
  await expect(lead.getByText('Sara Khan').first()).toBeVisible();
  await expect(lead.getByText('Read only')).toBeVisible();
  await expect(lead.getByText('Never used')).toBeVisible();
  await lead.getByRole('button', { name: 'Turn off' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Turn off' }).click();
  await expect(lead.getByText('Turned off', { exact: true })).toBeVisible();
  await expect(lead.getByRole('button', { name: 'Turn off' })).toHaveCount(0);
});

test('a member sees who has access but cannot create or turn off a link', async ({ page }) => {
  const mock = await mockInvoices(page, { role: 'member' });
  mock.accessLinks.push({ id: 'bpacc_7', account_id: 'x', contact_id: 'bcon_1', contact_name: 'Sara Khan', contact_email: null, status: 'active', expired: false, can_decide: true,
    expires_at: new Date(Date.now() + 30 * 86_400_000).toISOString(), last_used_at: new Date().toISOString(), created_at: new Date().toISOString(), created_by: 'user_owner', revoked_at: null });
  await page.goto('/#business-leads');
  await page.getByRole('button', { name: 'New lead' }).first().click();
  const leadForm = page.getByRole('form', { name: 'New lead' });
  await leadForm.getByLabel('Company name').fill('Gulf Mixers LLC');
  await leadForm.getByLabel('Lead title').fill('Fleet reporting');
  await leadForm.getByRole('button', { name: 'Add lead' }).click();
  const lead = page.getByRole('dialog', { name: 'Gulf Mixers LLC' });
  await expect(lead.getByText('Can approve')).toBeVisible();
  await expect(lead.getByText(/Last used/)).toBeVisible();
  await expect(lead.getByRole('button', { name: 'Create a portal link' })).toHaveCount(0);
  await expect(lead.getByRole('button', { name: 'Turn off' })).toHaveCount(0);
  await expect(lead.getByText('Only an owner can create or turn off a link.')).toBeVisible();
});

test('the owner sees the customer decision on a proposal, and whether it is about the current version', async ({ page }) => {
  await mockBusiness(page);
  const content = { summary: 'Rebuild', scope: 'All pages', deliverables: ['Site'], exclusions: [], assumptions: [], acceptance_criteria: ['Signed off'], line_items: [{ description: 'Build', kind: 'service', quantity: 1, unit_price: 5000 }],
    milestones: [], tax_rate_pct: 15, tax_note: '', payment_terms: '', valid_until: null, currency: 'SAR', internal_cost: 3000, version: 2, locked: true, content_hash: 'a'.repeat(64), subtotal: 5000, external_costs: 0, tax: 750, total: 5750, milestone_total: 0, margin: 2000, margin_pct: 40, issues: [], warnings: [], created_at: new Date().toISOString() };
  const proposal = (current: boolean) => ({
    id: 'bprop_1', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), opportunity_id: 'bopp_1', account_id: 'bacc_1', title: 'Website rebuild', status: 'pending_approval',
    current_version: 2, accepted_version: null, accepted_by: null, accepted_at: null, requested_version: 2, requested_hash: 'a'.repeat(64), approval_id: null, sent_at: new Date().toISOString(), decision_note: '', created_by: 'user_local', account_name: 'Acme Trading', opportunity_name: 'Rebuild',
    current: content, versions: [{ version: 2, content_hash: 'a'.repeat(64), locked: true, total: 5750, created_at: new Date().toISOString() }], approval_state: 'none', approval: null, project_id: null,
    portal_decision: { decision: 'approved', signer_name: 'Sara Khan', note: 'Go ahead, please start in November.', decided_at: '2026-11-03T10:00:00Z', version: current ? 2 : 1, content_hash: 'a'.repeat(64), current },
  });
  let current = true;
  await page.route('http://localhost:8000/api/v1/business/proposals**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = path.endsWith('/proposals') ? { items: [proposal(current)], total: 1, limit: 25, offset: 0 } : proposal(current);
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('/#business-proposals/bprop_1');
  const drawer = page.getByRole('dialog', { name: 'Website rebuild' });
  const decision = drawer.getByRole('group', { name: 'Customer decision' });
  await expect(decision).toContainText('Approved by Sara Khan in the portal on');
  await expect(decision).toContainText('Go ahead, please start in November.');
  await expect(decision).toContainText('This is about the version waiting now.');
  await expect(decision).toContainText('A record for you. The real approval is still yours.');
  current = false;
  await page.reload();
  await expect(page.getByRole('dialog', { name: 'Website rebuild' }).getByRole('group', { name: 'Customer decision' })).toContainText('This was about version 1, not the version waiting now.');
});

// The UI quality gate on the states the page-level gate cannot reach: the invoice drawer, the new invoice
// dialog with every source, the payment dialog and the portal block with the link dialog. The sizes are
// set here, so the mobile project leaves them out (@gate, playwright.config.ts).
for (const size of GATE_SIZES) {
  test.describe(`invoice states at ${size.width}x${size.height} @gate`, () => {
    test.use({ viewport: size });
    test('drawer, dialogs and portal block: layout and axe, in English and Arabic', async ({ page }) => {
      test.setTimeout(120_000);
      for (const locale of ['en', 'ar'] as const) {
        const tab = await page.context().newPage();
        const mock = await mockInvoices(tab);
        const sent = mock.seedSent({ customer_reference: 'PO 17, a long customer reference to check wrapping', issue_date: '2026-01-01', due_date: '2026-01-15', amount_paid: 400,
          payments: [{ id: 'bpay_x', invoice_id: 'x', amount: 400, paid_on: '2026-01-20', method: 'bank_transfer', reference: 'TT-5', note: 'Part payment', recorded_by: 'user_local' }], lines: [
            { description: 'A long line description that should wrap inside the table without pushing the drawer wider than the screen', kind: 'service', quantity: 3, unit_price: 1234.5, tax_rate_pct: 15, source: 'manual', source_id: null, gross_total: null, net: 0, tax: 0, total: 0 }] });
        await useSavedLocale(tab, locale);
        await tab.goto(`/#business-invoices/${sent}`);
        const drawer = tab.getByRole('dialog');
        await expect(drawer.locator('dl[aria-label]').first()).toBeVisible();
        await sweep(tab, `${locale} drawer`);
        await drawer.getByRole('button', { name: locale === 'ar' ? 'تسجيل دفعة' : 'Record a payment' }).click();
        await sweep(tab, `${locale} payment dialog`);
        await tab.keyboard.press('Escape');
        await tab.keyboard.press('Escape');
        await tab.goto('/#business-invoices');
        await tab.getByRole('button', { name: locale === 'ar' ? 'فاتورة جديدة' : 'New invoice' }).first().click();
        const form = tab.getByRole('form', { name: locale === 'ar' ? 'فاتورة جديدة' : 'New invoice' });
        await form.getByLabel(locale === 'ar' ? 'حساب العميل' : 'Client account').selectOption({ label: 'Acme Trading' });
        await form.getByLabel(locale === 'ar' ? 'المشروع' : 'Project').selectOption({ label: 'Website rebuild' });
        await form.getByRole('button', { name: locale === 'ar' ? 'إضافة بند' : 'Add a line' }).click();
        await expect(form.getByRole('checkbox').first()).toBeVisible();
        await sweep(tab, `${locale} new invoice`);
        await tab.close();
      }
    });
  });
}

for (const size of GATE_SIZES) {
  test.describe(`portal block at ${size.width}x${size.height} @gate`, () => {
    test.use({ viewport: size });
    test('the customer portal block and the link dialog: layout and axe, in English and Arabic', async ({ page }) => {
      test.setTimeout(120_000);
      for (const locale of ['en', 'ar'] as const) {
        const tab = await page.context().newPage();
        const mock = await mockInvoices(tab);
        mock.accessLinks.push({ id: 'bpacc_7', account_id: 'x', contact_id: 'bcon_1', contact_name: 'Sara Khan', contact_email: null, status: 'active', expired: false, can_decide: true,
          expires_at: new Date(Date.now() + 30 * 86_400_000).toISOString(), last_used_at: new Date().toISOString(), created_at: new Date().toISOString(), created_by: 'user_owner', revoked_at: null });
        await useSavedLocale(tab, locale);
        await tab.goto('/');
        const leadId = await tab.evaluate(async () => {
          const response = await fetch('http://localhost:8000/api/v1/business/leads', { method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ account: { name: 'Gulf Mixers LLC', website: null }, contact: { name: 'Sara Khan', email: null, consent_basis: 'inbound_inquiry' }, title: 'Fleet reporting', product_id: null }) });
          return (await response.json()).lead.id as string;
        });
        await tab.goto(`/#business-leads/${leadId}`);
        const lead = tab.getByRole('dialog');
        await expect(lead.getByRole('heading', { name: locale === 'ar' ? 'بوابة العميل' : 'Customer portal' })).toBeVisible();
        await expect(lead.getByText('Sara Khan').first()).toBeVisible();
        await sweep(tab, `${locale} lead drawer with the portal block`);
        await lead.getByRole('button', { name: locale === 'ar' ? 'إنشاء رابط بوابة' : 'Create a portal link' }).click();
        await sweep(tab, `${locale} create link dialog`);
        await tab.getByRole('button', { name: locale === 'ar' ? 'إنشاء الرابط' : 'Create link' }).click();
        await expect(tab.getByRole('textbox', { name: locale === 'ar' ? 'رابط البوابة' : 'Portal link' })).toBeVisible();
        await sweep(tab, `${locale} link shown dialog`);
        await tab.close();
      }
    });
  });
}
