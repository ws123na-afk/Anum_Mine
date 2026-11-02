import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// The controls the setup plan's gaps added (docs/setup-plan.md): consent records under Contact opt-outs,
// loop and retry limits under Agent controls, the approval reminder under Approval policy and Arrange
// Home. API mocked at localhost:8000; every request body is checked.
const now = new Date().toISOString();
type Json = Record<string, any>;
const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);
const serious = async (page: Page) => (await new AxeBuilder({ page }).include('main').analyze()).violations.filter((v) => v.impact === 'critical' || v.impact === 'serious');
const SECTIONS = ['glance', 'decisions', 'focus', 'activity', 'meetings', 'alerts', 'insights', 'pinned', 'goto'];

async function mockGaps(page: Page) {
  await mockWorkspaceApi(page);
  const sent: { method: string; path: string; body?: Json }[] = [];
  const consents: Json[] = [];
  let rule = false;
  let reminder: number | null = null;
  let layout: string[] | null = null;
  const loops: Json[] = [
    { agent_id: 'agent_care', name: 'Gallery: Customer Care', status: 'active', max_runs_per_task: null, max_step_attempts: null, updated_by: null, updated_at: null },
    { agent_id: 'agent_fin', name: 'Group: Finance Controller', status: 'paused', max_runs_per_task: 5, max_step_attempts: 3, updated_by: 'user_local', updated_at: now },
  ];
  await page.route('http://localhost:8000/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname, method = request.method();
    const body = method === 'POST' || method === 'PUT' ? (request.postDataJSON() as Json) : undefined;
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (path.startsWith('/api/v1/contact-consents')) {
      sent.push({ method, path, body });
      if (path === '/api/v1/contact-consents' && method === 'GET') {
        const state = url.searchParams.get('state');
        const items = consents.filter((c) => !state || c.status === state);
        return json({ items, total: items.length, active: consents.filter((c) => c.status === 'active').length, withdrawn: consents.filter((c) => c.status === 'withdrawn').length,
          settings: { require_for_marketing: rule, updated_by: null, updated_at: null }, can_manage: true });
      }
      if (path === '/api/v1/contact-consents' && method === 'POST') {
        const record = { id: `consent_${consents.length + 1}`, masked: '+971•••4567', kind: 'phone', channel: body!.channel, lawful_basis: body!.lawful_basis, source: body!.source,
          consented_at: body!.consented_at, note: body!.note, status: 'active', withdrawn_at: null, withdrawn_reason: null, withdrawn_by: null, created_by: 'user_local', created_at: now, updated_at: now };
        consents.unshift(record);
        return json(record, 201);
      }
      if (path.endsWith('/withdraw')) {
        const record = consents.find((c) => path.includes(c.id))!;
        Object.assign(record, { status: 'withdrawn', withdrawn_at: now, withdrawn_reason: body!.reason, withdrawn_by: 'user_local' });
        return json(record);
      }
      if (path.endsWith('/settings') && method === 'PUT') { rule = body!.require_for_marketing; return json({ require_for_marketing: rule, updated_by: 'user_local', updated_at: now }); }
    }
    if (path.startsWith('/api/v1/agent-controls/loop-limits')) {
      sent.push({ method, path, body });
      if (method === 'GET') return json({ can_manage: true, total: loops.length, items: loops });
      const row = loops.find((l) => path.endsWith(l.agent_id))!;
      Object.assign(row, { max_runs_per_task: body!.max_runs_per_task, max_step_attempts: body!.max_step_attempts, updated_by: 'user_local', updated_at: now });
      return json(row);
    }
    if (path === '/api/v1/approval-reminders/settings') {
      sent.push({ method, path, body });
      if (method === 'PUT') reminder = body!.reminder_hours;
      return json({ reminder_hours: reminder, updated_by: reminder === null ? null : 'user_local', updated_at: reminder === null ? null : now });
    }
    if (path === '/api/v1/home/layout') {
      sent.push({ method, path, body });
      if (method === 'PUT') layout = body!.sections;
      if (method === 'DELETE') layout = null;
      const sections = layout ?? SECTIONS;
      return json({ sections, hidden: SECTIONS.filter((s) => !sections.includes(s)), default: SECTIONS, customized: layout !== null });
    }
    return route.fallback();
  });
  return sent;
}

test('an owner records a consent, turns on the rule and withdraws it with a reason', async ({ page }) => {
  const sent = await mockGaps(page);
  await page.goto('/#suppressions');
  const panel = page.getByRole('region', { name: 'Consent records' });
  await expect(panel.getByRole('heading', { name: 'No consent recorded yet' })).toBeVisible();
  await panel.getByLabel('Phone number or e-mail address').fill('ahmed@example.com');
  await panel.getByRole('button', { name: 'Record consent' }).click();
  await expect(panel.getByText('An e-mail address consents on e-mail')).toBeVisible();
  await panel.getByLabel('Phone number or e-mail address').fill('+971 50 123 4567');
  await panel.getByLabel('Where it came from').fill('Website sign-up form');
  await panel.getByRole('button', { name: 'Record consent' }).click();
  await expect(panel.getByText('Consent recorded for +971•••4567.')).toBeVisible();
  const post = sent.find((c) => c.method === 'POST' && c.path === '/api/v1/contact-consents');
  expect(post?.body).toMatchObject({ recipient: '+971 50 123 4567', channel: 'whatsapp', lawful_basis: 'consent', source: 'Website sign-up form', note: null });
  const ruleSwitch = panel.getByRole('switch', { name: /Marketing sends need a consent record/ });
  await expect(ruleSwitch).toBeEnabled();
  await ruleSwitch.click();
  await expect(panel.getByText('Marketing sends now need a consent record.')).toBeVisible();
  await expect(ruleSwitch).toBeChecked();
  await panel.getByRole('button', { name: 'Withdraw the consent of +971•••4567' }).click();
  const dialog = page.getByRole('alertdialog');
  await expect(dialog.getByRole('button', { name: 'Withdraw' })).toBeDisabled();
  await dialog.getByLabel('Reason (3 to 300 characters)').fill('Asked by phone');
  await dialog.getByRole('button', { name: 'Withdraw' }).click();
  await expect(panel.getByText(/Withdrawn .*Asked by phone/)).toBeVisible();
  expect(await serious(page)).toEqual([]);
});

test('an owner sets an agent\'s loop and retry limits, and a wrong number is explained', async ({ page }) => {
  const sent = await mockGaps(page);
  await page.goto('/#agentControls');
  const panel = page.getByRole('region', { name: 'Loop and retry limits' });
  await expect(panel.getByText('Gallery: Customer Care')).toBeVisible();
  await panel.getByLabel('Runs per task for Gallery: Customer Care').fill('25');
  await panel.getByRole('button', { name: 'Save limits for Gallery: Customer Care' }).click();
  await expect(panel.getByText('Enter a whole number from 1 to 20, or leave it empty.')).toBeVisible();
  await panel.getByLabel('Runs per task for Gallery: Customer Care').fill('5');
  await panel.getByLabel('Automation step attempts for Gallery: Customer Care').fill('3');
  await panel.getByRole('button', { name: 'Save limits for Gallery: Customer Care' }).click();
  await expect(panel.getByText('Limits saved for Gallery: Customer Care.')).toBeVisible();
  expect(sent.find((c) => c.method === 'PUT')?.body).toEqual({ max_runs_per_task: 5, max_step_attempts: 3 });
  expect(await serious(page)).toEqual([]);
});

test('an owner turns on the approval reminder', async ({ page }) => {
  const sent = await mockGaps(page);
  await page.goto('/#policy');
  const card = page.getByRole('region', { name: 'Remind approvers' });
  await expect(card.getByRole('button', { name: 'Save reminder' })).toBeDisabled();
  await card.getByLabel('Send a reminder after').selectOption('4');
  await card.getByRole('button', { name: 'Save reminder' }).click();
  await expect(card.getByText('Approvals now remind owners after 4 hours.')).toBeVisible();
  expect(sent.find((c) => c.method === 'PUT')?.body).toEqual({ reminder_hours: 4 });
  expect(await serious(page)).toEqual([]);
});

test('a person arranges their Home and goes back to the default', async ({ page }) => {
  const sent = await mockGaps(page);
  await page.goto('/#home');
  await page.getByRole('button', { name: 'Arrange Home' }).click();
  const dialog = page.getByRole('alertdialog', { name: 'Arrange your Home' });
  await dialog.getByRole('button', { name: 'Move Waiting for your decision up' }).click();
  await dialog.getByLabel('Go to', { exact: true }).uncheck();
  await dialog.getByRole('button', { name: 'Save order' }).click();
  await expect(dialog).toBeHidden();
  const put = sent.find((c) => c.method === 'PUT' && c.path === '/api/v1/home/layout');
  expect(put?.body).toEqual({ sections: ['decisions', 'glance', 'focus', 'activity', 'meetings', 'alerts', 'insights', 'pinned'] });
  await expect(page.locator('.homeFlowItem').first()).toContainText('Waiting for your decision');
  await expect(page.getByRole('navigation', { name: 'Go to' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Arrange Home' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Back to the default order' }).click();
  await expect(page.getByRole('navigation', { name: 'Go to' })).toBeVisible();
});

for (const locale of ['en', 'ar'] as const) {
  for (const size of [
    { width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 },
  ]) {
    test(`the new controls fit at ${size.width}x${size.height} in ${locale}`, async ({ page }, testInfo) => {
      await page.setViewportSize(size);
      await useSavedLocale(page, locale);
      await mockGaps(page);
      for (const view of ['suppressions', 'agentControls', 'policy', 'home']) {
        await page.goto(`/#${view}`);
        await expect(page.locator('main h1, main h2').first()).toBeVisible();
        if (view === 'suppressions') await expect(page.locator('.consentPanel')).toBeVisible();
        if (view === 'agentControls') await expect(page.locator('.capsPanel').nth(1)).toBeVisible();
        if (view === 'policy') await expect(page.locator('.policyPanel').nth(1)).toBeVisible();
        expect(await noOverflow(page), `${view} overflows`).toBe(true);
        if (process.env.ANUM_SCREENSHOTS) {
          const part = { suppressions: '.consentPanel', agentControls: '.capsPanel >> nth=1', policy: '.policyPanel >> nth=1', home: '.homeHeader' }[view]!;
          await page.locator(part).screenshot({ path: testInfo.outputPath(`${view}-${locale}-${size.width}.png`) });
        }
      }
      if (locale === 'ar') expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
      if (size.width === 390) expect(await serious(page)).toEqual([]);
    });
  }
}
