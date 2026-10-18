import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockCallsApi, type CallsMockOptions } from './support/callsApi';
import { useSavedLocale } from './support/workspaceApi';

// Phone calls (docs/calls.md): the call log with search and filters, the dialpad and its tel: link
// with the log dialog that follows, notes, the AI summary and the follow-up task, contacts with
// favourites and AI call consent, AI calls waiting for approval, the provider settings, and every
// page state (denied, empty, error, no provider).

const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

async function openCalls(page: Page, options: CallsMockOptions = {}, hash = 'calls') {
  const mock = await mockCallsApi(page, options);
  // A tel: link would leave the test browser; the app's own click handler still runs.
  await page.addInitScript(() => document.addEventListener('click', (event) => {
    const link = (event.target as Element | null)?.closest?.('a[href^="tel:"]');
    if (link) { event.preventDefault(); (window as unknown as { lastTel?: string }).lastTel = link.getAttribute('href') ?? ''; }
  }, true));
  await page.goto(`/#${hash}`);
  await expect(page.getByRole('heading', { level: 1, name: 'Phone calls' })).toBeVisible();
  return mock;
}

test('the call log lists calls, filters them and opens one with its notes', async ({ page }) => {
  const { calls } = await openCalls(page);
  const list = page.getByRole('region', { name: 'Recents' });
  await expect(list.locator('.callsRow')).toHaveCount(2);
  await list.getByLabel('Outcome').selectOption('busy');
  await expect(list.locator('.callsRow')).toHaveCount(1);
  expect(calls.listQueries.at(-1)).toContain('outcome=busy');
  await list.getByLabel('Outcome').selectOption('');
  await list.getByRole('searchbox').fill('nothing here');
  await list.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(list.getByRole('heading', { name: 'No call matches' })).toBeVisible();
  await list.getByRole('button', { name: 'Clear search and filters' }).click();
  await list.getByRole('button', { name: 'Open the call with Sara Example' }).click();
  const detail = page.locator('.callsDetail');
  await expect(detail.getByRole('heading', { level: 2, name: 'Sara Example' })).toBeVisible();
  await expect(detail.getByLabel('Notes about the call')).toHaveValue('Wants a quote for 40 tyres by Friday.');
  await expect(detail.getByRole('link', { name: 'Call on your phone' })).toHaveAttribute('href', 'tel:+971501234567');
  expect(await noHorizontalOverflow(page)).toBe(true);
});

test('a call summary suggests a follow-up that is saved as a task', async ({ page }) => {
  const { calls } = await openCalls(page, {}, 'calls?call=call_quote');
  const detail = page.locator('.callsDetail');
  await detail.getByRole('button', { name: 'Summarize' }).click();
  await expect(detail.getByText('Sara wants a quote for 40 tyres by Friday.', { exact: true })).toBeVisible();
  await expect(detail.getByText('AI generated · test-model')).toBeVisible();
  await expect(detail.getByLabel('Next step')).toHaveValue('Send Sara the tyre quote');
  await detail.getByLabel('Next step').fill('Send the quote today');
  await detail.getByRole('button', { name: 'Save follow-up' }).click();
  await expect(page.getByText('Follow-up saved.')).toBeVisible();
  await detail.getByRole('button', { name: 'Save as task' }).click();
  await expect(detail.getByText('This follow-up is a task.')).toBeVisible();
  expect(calls.summaries).toEqual(['call_quote']);
  expect(calls.tasks).toEqual(['call_quote']);
});

test('the dialpad dials through the phone and asks to log the call', async ({ page }) => {
  const { calls } = await openCalls(page, {}, 'calls-dial');
  const pad = page.getByRole('group', { name: 'Dialpad' });
  await pad.getByRole('button', { name: '0 +' }).dispatchEvent('pointerdown');
  await page.waitForTimeout(700);
  await pad.getByRole('button', { name: '0 +' }).dispatchEvent('pointerup');
  for (const key of ['9', '7', '1', '5', '0 +']) await pad.getByRole('button', { name: key, exact: true }).click();
  await expect(page.getByLabel('Number to call')).toHaveValue('+97150');
  await page.getByRole('button', { name: 'Delete the last digit' }).click();
  await page.getByLabel('Number to call').fill('+971 50 123 4567');
  await expect(page.getByText('Calling Sara Example')).toBeVisible();
  await page.getByRole('link', { name: 'Call on your phone' }).click();
  expect(await page.evaluate(() => (window as unknown as { lastTel?: string }).lastTel)).toBe('tel:+971501234567');
  const dialog = page.getByRole('dialog', { name: 'Call with Sara Example' });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel('No answer').check();
  await dialog.getByLabel('Duration (minutes:seconds)').fill('later');
  await dialog.getByRole('button', { name: 'Save to the log' }).click();
  await expect(dialog.getByText('Write the duration as minutes:seconds, for example 2:30.')).toBeVisible();
  await dialog.getByLabel('Duration (minutes:seconds)').fill('0:20');
  await dialog.getByLabel('Notes').fill('Left a message');
  await dialog.getByRole('button', { name: 'Save to the log' }).click();
  await expect(page.getByText('Call logged.')).toBeVisible();
  expect(calls.logged[0]).toMatchObject({ contact_id: 'contact_sara', outcome: 'no_answer', duration_seconds: 20, notes: 'Left a message', direction: 'outbound' });
  await expect(page.getByRole('tab', { name: 'Recents' })).toHaveAttribute('aria-selected', 'true');
});

test('contacts: search, add, favourite and record AI call consent', async ({ page }) => {
  const { calls } = await openCalls(page, { extraContacts: 30 }, 'calls-contacts');
  const list = page.getByRole('region', { name: 'Contacts' });
  await expect(list.locator('.callsContactItem')).toHaveCount(25);
  await expect(list.getByText('1 to 25 of 32')).toBeVisible();
  await list.getByRole('button', { name: 'Next' }).click();
  await expect(list.locator('.callsContactItem')).toHaveCount(7);
  await list.getByRole('searchbox').fill('noor');
  await list.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(list.locator('.callsContactItem')).toHaveCount(1);
  await list.getByRole('button', { name: 'Open Sara Example' }).click();
  const detail = page.locator('.callsDetail');
  await expect(detail.getByText('No consent to AI calls. ANUM never makes AI cold calls.')).toBeVisible();
  await detail.getByRole('button', { name: 'Record consent' }).click();
  const consent = page.getByRole('dialog', { name: 'Record consent' });
  await expect(consent.getByRole('button', { name: 'Record consent' })).toBeDisabled();
  await consent.getByLabel('How they agreed').selectOption('written');
  await consent.getByLabel('Sara Example agreed to be called by the AI assistant.').check();
  await consent.getByRole('button', { name: 'Record consent' }).click();
  await expect(page.getByText('Consent recorded.')).toBeVisible();
  expect(calls.consents).toEqual(['contact_sara']);
  await page.getByRole('button', { name: 'Add contact' }).first().click();
  const form = page.getByRole('dialog', { name: 'Add contact' });
  await form.getByLabel('Name').fill('Nadia Example');
  await form.getByLabel('Phone number').fill('call me');
  await form.getByRole('button', { name: 'Save contact' }).click();
  await expect(form.getByText(/Enter a phone number/)).toBeVisible();
  await form.getByLabel('Phone number').fill('+971 55 444 1212');
  await form.getByRole('button', { name: 'Save contact' }).click();
  await expect(page.getByText('Added Nadia Example.')).toBeVisible();
  expect(calls.contacts[0]).toMatchObject({ name: 'Nadia Example', phone: '+971 55 444 1212' });
});

test('an AI call is sent for approval and can be cancelled; the provider tab shows the steps', async ({ page }) => {
  const { calls } = await openCalls(page, { provider: true }, 'calls-ai');
  const form = page.getByRole('region', { name: 'Ask the AI to call' });
  await expect(form.getByLabel('Contact')).toHaveValue('contact_omar');
  await form.getByLabel('Purpose').fill('Confirm the delivery');
  await form.getByLabel('Script').fill('Ask whether Thursday morning suits.');
  await form.getByRole('button', { name: 'Send for approval' }).click();
  await expect(page.getByText('Sent. The call waits for an owner’s approval.')).toBeVisible();
  expect(calls.aiRequests[0]).toMatchObject({ contact_id: 'contact_omar', language: 'ar', max_seconds: 120 });
  const list = page.getByRole('region', { name: 'AI calls' });
  await expect(list.getByText('Waiting for approval')).toBeVisible();
  await list.getByRole('button', { name: 'Cancel request' }).click();
  await expect(list.getByText('Cancelled')).toBeVisible();
  await page.getByRole('tab', { name: 'Provider' }).click();
  await expect(page.getByText('Connected', { exact: true })).toBeVisible();
  await expect(page.getByText('https://api.example.test/api/v1/calls/webhooks/twilio/route0001/voice')).toBeVisible();
  await expect(page.getByRole('list').filter({ hasText: 'Create a Twilio account' }).locator('li')).toHaveCount(6);
});

test('the owner connects the provider; a refused token says why', async ({ page }) => {
  const { calls } = await openCalls(page, {}, 'calls-provider');
  await expect(page.getByText('Not connected. Until a provider is connected, calls go through your phone’s dialer.')).toBeVisible();
  await page.getByLabel('Account SID').fill('ACexample0001');
  await page.getByLabel('Auth token').fill('wrongtoken0000000');
  await page.getByLabel('Provider number').fill('+97145550100');
  await page.getByLabel('Business name the AI says').fill('ANUM Tyres');
  await page.getByRole('button', { name: 'Check and save' }).click();
  await expect(page.getByText(/Twilio refused the credentials/)).toBeVisible();
  await page.getByLabel('Auth token').fill('righttoken0000000');
  await page.getByRole('button', { name: 'Check and save' }).click();
  await expect(page.getByText('Provider saved and checked.')).toBeVisible();
  expect(calls.providerSaves.at(-1)).toMatchObject({ account_sid: 'ACexample0001', auth_token: 'righttoken0000000', from_number: '+97145550100' });
  expect(calls.providerSaves.at(-1)).not.toHaveProperty('api_key_secret');
});

test('a call through ANUM needs the recording consent before it starts', async ({ page }) => {
  const { calls } = await openCalls(page, { provider: true }, 'calls-contacts?contact=contact_sara');
  await page.locator('.callsDetail').getByRole('button', { name: 'Call through ANUM' }).click();
  const dialog = page.getByRole('dialog', { name: 'Call through ANUM' });
  await expect(dialog.getByText(/costs about \$0.05 a minute/)).toBeVisible();
  await dialog.getByLabel('Record this call').check();
  await expect(dialog.getByText('Please note: this call is recorded by ANUM Tyres.')).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Start call' })).toBeDisabled();
  await dialog.getByLabel(/I confirm the other side hears this notice/).check();
  await dialog.getByRole('button', { name: 'Start call' }).click();
  await expect(dialog.getByText(/The call failed/)).toBeVisible();
  expect(calls.prepared[0]).toMatchObject({ contact_id: 'contact_sara', record: true, recording_consent: true });
});

test('states: denied for viewers, empty with a next action, an error with a retry, no provider for AI calls', async ({ page }) => {
  await mockCallsApi(page, { role: 'viewer' });
  await page.goto('/#calls');
  await expect(page.getByRole('heading', { name: 'Phone calls are not part of your role' })).toBeVisible();
  await expect(page.getByText(/Ask a workspace owner to make you a member/)).toBeVisible();

  const empty = await page.context().newPage();
  await mockCallsApi(empty, { empty: true });
  await empty.goto('/#calls');
  await expect(empty.getByRole('heading', { name: 'No calls logged yet' })).toBeVisible();
  await empty.getByRole('button', { name: 'Dial a number' }).click();
  await expect(empty.getByRole('heading', { name: 'Dial a number' })).toBeVisible();
  await empty.getByRole('tab', { name: 'AI calls' }).click();
  await expect(empty.getByText('No voice provider is connected.', { exact: true })).toBeVisible();
  await empty.close();

  const failing = await page.context().newPage();
  await mockCallsApi(failing, { failLog: 1 });
  await failing.goto('/#calls');
  await expect(failing.getByText(/Could not load calls: The database did not answer/)).toBeVisible();
  await failing.getByRole('button', { name: 'Retry' }).click();
  await expect(failing.locator('.callsRow')).toHaveCount(2);
  await failing.close();
});

test('Arabic: the page is right to left and numbers stay left to right', async ({ page }) => {
  await mockCallsApi(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#calls-contacts?contact=contact_omar');
  await expect(page.getByRole('heading', { level: 1, name: 'المكالمات الهاتفية' })).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.locator('.callsDetail bdi[dir="ltr"]').first()).toHaveText('+971502223333');
  await expect(page.getByText(/وافق على مكالمات الذكاء الاصطناعي/)).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);
});

// The accessibility gate for this page (as e2e/accessibility.spec.ts does for the others, with this
// page's own API fake): no serious or critical axe violation in English, Arabic and the light theme.
const axeViews: { hash: string; options?: CallsMockOptions; ready: string }[] = [
  { hash: 'calls', ready: '.callsRow' },
  { hash: 'calls?call=call_quote', ready: '.callsDetail textarea' },
  { hash: 'calls-contacts?contact=contact_omar', ready: '.callsDetail .callsMeta' },
  { hash: 'calls-dial', ready: '.callsKeypad' },
  { hash: 'calls-ai', options: { provider: true }, ready: '.callsAiNew textarea' },
  { hash: 'calls-provider', options: { provider: true }, ready: '.callsUrl code' },
];
for (const mode of ['en', 'ar', 'light'] as const) {
  for (const view of axeViews) {
    test(`axe (${mode}): #${view.hash} has no serious or critical violations`, async ({ page }) => {
      await mockCallsApi(page, view.options);
      if (mode === 'ar') await useSavedLocale(page, 'ar');
      if (mode === 'light') await page.addInitScript(() => { try { localStorage.setItem('anum.theme', 'light'); } catch { /* storage blocked */ } });
      await page.goto(`/#${view.hash}`);
      await expect(page.locator(view.ready).first()).toBeVisible();
      const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
      const serious = results.violations.filter((item) => item.impact === 'serious' || item.impact === 'critical')
        .map((item) => `${item.id}: ${item.help} ${item.nodes.slice(0, 3).map((node) => node.target.join(' ')).join(', ')}`);
      expect(serious).toEqual([]);
    });
  }
}
