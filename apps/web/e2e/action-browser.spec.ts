import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type Route } from '@playwright/test';
import { logoPng } from './support/pngFixture';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// The action browser in the web app (docs/action-browser.md): saved logins and cards under
// Integrations, the live view in the task detail (picture, steps, Stop, Needs you input) and the
// approval card of a web.act step, against a mocked API. Desktop and mobile projects.
const now = new Date().toISOString();
const later = new Date(Date.now() + 15 * 60_000).toISOString();
const hash = '7c'.repeat(32);
const DEMO = process.env.ANUM_ACTION_BROWSER_DEMO_DIR;
type Json = Record<string, unknown>;

const task = { id: 'task_act', title: 'Book a seat to Riyadh', prompt: 'Book one seat on https://shop.example/login and pay with my main card', status: 'waiting_approval', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: now, updated_at: now };
const run = {
  id: 'run_act', task_id: 'task_act', status: 'waiting_approval', created_at: now, updated_at: now, result: null,
  steps: [{ id: 'step_1', type: 'tool_proposal', summary: 'Proposed tool call: web.act', created_at: now, metadata: { tool: 'web.act', risk_level: 'high', policy_outcome: 'require_approval' } }],
  checkpoint: { phase: 'waiting_approval', version: 3 },
};
const step = (seq: number, kind: string, summary: string, detail: Json = {}) => ({ seq, kind, summary, url: 'https://shop.example/pay', detail, created_at: now });
const sessionBase = {
  id: 'abs_1', task_id: 'task_act', run_id: 'run_act', goal: task.prompt, start_url: 'https://shop.example/login', sites: ['shop.example'], logins: ['shop'], cards: ['main'],
  step_count: 6, current_url: 'https://shop.example/pay', current_title: 'Payment', outcome: null, message: '', live: true, can_watch: true, can_stop: true,
  created_at: now, updated_at: now, ended_at: null,
  steps: [
    step(1, 'started', 'Started: Book one seat'), step(2, 'opened', 'Opened shop.example', { host: 'shop.example' }),
    step(3, 'typed', 'Typed the saved login shop (password) into “Password”', { label: '“Password”', handle: '{{login:shop.password}}' }),
    step(4, 'clicked', 'Clicked “Sign in”', { label: '“Sign in”' }),
    step(5, 'approval', 'Waiting for your approval: press “Pay AED 1,240” (AED 1,240) on shop.example.', { amount_text: 'AED 1,240' }),
  ],
};
const payApproval = {
  id: 'approval_pay', task_id: 'task_act', action: 'web.act', risk_level: 'high', status: 'pending', run_id: 'run_act', step_id: 'step_9',
  reason: 'Waiting for your approval: press “Pay AED 1,240” (AED 1,240) on shop.example. (External or high-impact actions require explicit approval.)',
  created_at: now, expires_at: later, payload_hash: hash, decided_at: null, decided_by: null,
  arguments: {
    resume: 'step', kind: 'binding_button', site: 'shop.example', url: 'https://shop.example/pay', target: '“Pay AED 1,240”',
    action: { type: 'click', ref: 'e2' }, shown_action: { type: 'click', ref: 'e2' }, fields: [{ label: 'Card number', type: 'text' }],
    fingerprint: 'ab'.repeat(32), amount: 1240, currency: 'AED', amount_text: 'AED 1,240', session_id: '[REDACTED]', pending_id: 'abp_1',
  },
};

async function mockActionBrowser(page: Page, options: { waiting?: 'approval' | 'handoff'; member?: boolean; empty?: boolean } = {}) {
  await mockWorkspaceApi(page);
  const calls: { method: string; path: string; body: unknown }[] = [];
  let session: Json = { ...sessionBase, status: 'waiting', waiting: options.waiting ?? 'approval', pending: { kind: 'binding_button', amount_text: 'AED 1,240' }, can_watch: !options.member };
  let credentials: Json[] = options.empty ? [] : [{ id: 'abcred_1', kind: 'login', handle: 'shop', site: 'shop.example', hint: 'o***@example.com on shop.example', created_by: 'user_local', created_at: now, updated_by: 'user_local', updated_at: now }];
  let approval: Json = { ...payApproval };
  const picture = logoPng(1280, 800, [40, 90, 160]);
  await page.route('http://localhost:8000/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (path.startsWith('/api/v1/action-browser')) calls.push({ method, path, body });
    if (path === '/api/v1/workspace-memberships/current' && options.member) return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'member', active: true, created_at: now, updated_at: now });
    if (path === '/api/v1/integrations') return json([{ id: 'action-browser', name: 'Action browser', kind: 'tool', status: 'connected', endpoint: 'http://browser:8090', latency_ms: 30, detail: 'Health check passed.', enabled: true, available: true, environment_variable: 'ANUM_ACTION_BROWSER_ENABLED', remediation: null, credentials: { configured: true, source: 'environment', scopes: ['web.act:write'], expires_at: null }, accepts_workspace_credential: false, workspace_credential: null }]);
    if (path === '/api/v1/integrations/action-browser/configuration') return json({ id: 'action-browser', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', enabled: true, endpoint: 'http://browser:8090' });
    if (path === '/api/v1/action-browser/credentials' && method === 'GET') return options.member ? json({ detail: 'Only a workspace owner manages saved logins and cards.' }, 403) : json(credentials);
    if (path === '/api/v1/action-browser/credentials' && method === 'POST') {
      const sent = body as Json;
      const saved = { id: `abcred_${credentials.length + 2}`, kind: sent.kind, handle: sent.handle, site: sent.site ?? null, hint: sent.kind === 'card' ? 'Visa ending 4242, expires 08/29' : 'n***@example.com on portal.example', created_by: 'user_local', created_at: now, updated_by: 'user_local', updated_at: now };
      credentials = [...credentials, saved];
      return json(saved, 201);
    }
    if (path.startsWith('/api/v1/action-browser/credentials/') && method === 'DELETE') { credentials = credentials.filter((c) => !path.endsWith(String(c.id))); return route.fulfill({ status: 204 }); }
    if (path.startsWith('/api/v1/action-browser/credentials/') && method === 'PUT') {
      credentials = credentials.map((c) => (path.endsWith(String(c.id)) ? { ...c, hint: 'n***@example.com on shop.example' } : c));
      return json(credentials.find((c) => path.endsWith(String(c.id))));
    }
    if (path === '/api/v1/action-browser/sessions') return json([session]);
    if (path === '/api/v1/action-browser/sessions/abs_1/frame') return options.member ? json({ detail: 'owner only' }, 403) : route.fulfill({ status: 200, contentType: 'image/png', body: picture });
    if (path === '/api/v1/action-browser/sessions/abs_1/stop') {
      session = { ...session, status: 'stopped', waiting: null, live: false, can_stop: false, message: 'Stopped by the owner.', ended_at: now };
      return json(session);
    }
    if (path === '/api/v1/action-browser/sessions/abs_1/input') return json({ outcome: 'done' });
    if (path === '/api/v1/tasks' && method === 'GET') return json([task]);
    if (path === '/api/v1/tasks/summary') return json({ total: 1, archived: 0, by_status: { waiting_approval: 1 }, creators: ['user_local'] });
    if (path === '/api/v1/tasks/task_act') return json(task);
    if (path === '/api/v1/tasks/task_act/runs') return json([run]);
    if (path === '/api/v1/tasks/task_act/latest-run') return json(run);
    if (path === '/api/v1/tasks/task_act/events') return json([]);
    if (path === '/api/v1/approvals') return json([approval]);
    if (path.startsWith('/api/v1/approvals/page')) return json({ items: [approval], next_cursor: null });
    if (path === '/api/v1/approvals/summary') return json({ pending: approval.status === 'pending' ? 1 : 0, approved: approval.status === 'approved' ? 1 : 0, rejected: 0, expired: 0 });
    if (path === '/api/v1/approvals/approval_pay') return json(approval);
    if (path === '/api/v1/approvals/approval_pay/approve') {
      calls.push({ method, path, body });
      approval = { ...approval, status: 'approved', decided_by: 'user_local', decided_at: now };
      return json({ approval, task: { ...task, status: 'running' }, run: { ...run, status: 'running' } });
    }
    return route.fallback();
  });
  return calls;
}

const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
async function serious(page: Page, selector: string) {
  const result = await new AxeBuilder({ page }).include(selector).analyze();
  return result.violations.filter((item) => item.impact === 'critical' || item.impact === 'serious').map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join(', ')}`);
}

async function openVault(page: Page) {
  await page.goto('/#integrations');
  const services = page.getByRole('button', { name: /^Platform services/ });
  if (await services.getAttribute('aria-expanded') === 'false') await services.click();
  const row = page.locator('li.intRow', { has: page.getByRole('heading', { name: 'Action browser', exact: true }) });
  await expect(row.getByText(/does tasks on sites for you/)).toBeVisible();
  await row.getByRole('button', { name: /Action browser/ }).click();
  return page.getByRole('dialog', { name: 'Action browser' });
}

test('an owner adds, edits and deletes saved logins and cards; secrets never show again', async ({ page }) => {
  const calls = await mockActionBrowser(page);
  const drawer = await openVault(page);
  await expect(drawer.getByText(/Agent tool web\.act: available/)).toBeVisible();
  const vault = drawer.locator('[data-action-browser-vault]');
  await expect(vault.getByRole('heading', { name: 'Saved logins and cards' })).toBeVisible();
  await expect(vault.getByText('o***@example.com on shop.example')).toBeVisible();

  await vault.getByRole('button', { name: 'Add card' }).click();
  const form = vault.getByRole('form', { name: 'Add card' });
  await form.getByLabel('Handle').fill('main');
  await form.getByLabel('Name on card').fill('Owner Name');
  await form.getByLabel('Card number').fill('4242 4242 4242 4241');
  await form.getByLabel('Expiry (MM/YY)').fill('08/29');
  await form.getByLabel('Security code (CVC)').fill('123');
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(form.getByRole('alert')).toHaveText('The card number is not valid.');
  await form.getByLabel('Card number').fill('4242 4242 4242 4242');
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(vault.getByText('Saved main.')).toBeVisible();
  await expect(vault.getByText('Visa ending 4242, expires 08/29')).toBeVisible();
  expect(await page.content()).not.toContain('4242 4242 4242 4242');
  const posted = calls.find((c) => c.method === 'POST' && c.path === '/api/v1/action-browser/credentials');
  expect(posted?.body).toEqual({ kind: 'card', handle: 'main', name: 'Owner Name', number: '4242 4242 4242 4242', expiry: '08/29', cvc: '123' });

  await vault.getByRole('button', { name: 'Edit shop' }).click();
  const edit = vault.getByRole('form', { name: 'Edit shop' });
  await expect(edit.getByText('Leave a secret field empty to keep the saved one.')).toBeVisible();
  await edit.getByLabel('User name or email').fill('new@example.com');
  await edit.getByRole('button', { name: 'Save' }).click();
  await expect(vault.getByText('n***@example.com on shop.example')).toBeVisible();
  expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ kind: 'login', handle: 'shop', site: 'shop.example', username: 'new@example.com' });

  await vault.getByRole('button', { name: 'Delete main' }).click();
  await vault.getByRole('button', { name: 'Delete main' }).click();
  await expect(vault.getByText('Deleted main.')).toBeVisible();
  expect(await serious(page, '[data-action-browser-vault]')).toEqual([]);
  expect(await noOverflow(page)).toBe(true);
});

test('the vault is empty with a next step', async ({ page }) => {
  await mockActionBrowser(page, { empty: true });
  const vault = (await openVault(page)).locator('[data-action-browser-vault]');
  await expect(vault.getByText('Nothing saved yet')).toBeVisible();
  await expect(vault.getByRole('button', { name: 'Add login' })).toBeVisible();
});

test('a member does not see saved logins and cards', async ({ page }) => {
  const calls = await mockActionBrowser(page, { member: true });
  const drawer = await openVault(page);
  await expect(drawer.getByText(/Agent tool web\.act/)).toBeVisible();
  await expect(drawer.locator('[data-action-browser-vault]')).toHaveCount(0);
  expect(calls.some((c) => c.path === '/api/v1/action-browser/credentials')).toBe(false);
});

test('the approval card shows the amount and the picture, and the live view shows the steps and Stop', async ({ page }, info) => {
  const calls = await mockActionBrowser(page);
  const demo = DEMO && info.project.name === 'desktop' ? DEMO : null;
  await page.goto('/#approvals');
  const card = page.getByRole('article', { name: 'Approval for web.act' });
  const extra = card.locator('[data-action-browser-approval]');
  await expect(extra.getByText('Press a button that commits')).toBeVisible();
  await expect(extra.getByText('AED 1,240', { exact: true })).toBeVisible();
  await expect(extra.getByText('https://shop.example/pay')).toBeVisible();
  await expect(extra.getByRole('img', { name: /Live picture of the page/ })).toBeVisible();
  expect(await serious(page, '[data-action-browser-approval]')).toEqual([]);
  expect(await noOverflow(page)).toBe(true);
  if (demo) await card.screenshot({ path: `${demo}/5-approval-card-pay.png` });
  await card.getByRole('button', { name: 'Approve' }).click();
  await expect.poll(() => calls.find((c) => c.path === '/api/v1/approvals/approval_pay/approve')?.body).toEqual({ payload_hash: hash });

  await page.goto('/#tasks');
  await page.locator('.recordButton', { hasText: 'Book a seat to Riyadh' }).first().click();
  const live = page.locator('[data-action-browser-live]');
  await expect(live.getByText('Waiting for your approval').first()).toBeVisible();
  await expect(live.getByRole('img', { name: /Live picture of the page at https:\/\/shop\.example\/pay/ })).toBeVisible();
  await expect(live.getByText('Typed the saved login shop (password) into \u201cPassword\u201d')).toBeVisible();
  await expect(live.getByText('Passwords and card numbers are hidden in the picture.')).toBeVisible();
  await expect(live.getByText('shop.example', { exact: true })).toBeVisible();
  expect(await serious(page, '[data-action-browser-live]')).toEqual([]);
  expect(await noOverflow(page)).toBe(true);
  if (demo) await live.screenshot({ path: `${demo}/4-live-view-waiting.png` });

  await live.getByRole('button', { name: 'Stop' }).click();
  await live.getByRole('button', { name: 'Stop and cancel' }).click();
  await expect(live.getByText('Stopped by the owner.')).toBeVisible();
  await expect(live.getByText('No live picture: the browser session has ended.')).toBeVisible();
  expect(calls.some((c) => c.path === '/api/v1/action-browser/sessions/abs_1/stop')).toBe(true);
  if (demo) await live.screenshot({ path: `${demo}/6-live-view-stopped.png` });
});

test('needs you: an owner clicks on the picture and types into the page', async ({ page }) => {
  const calls = await mockActionBrowser(page, { waiting: 'handoff' });
  await page.goto('/#tasks');
  await page.locator('.recordButton', { hasText: 'Book a seat to Riyadh' }).first().click();
  const live = page.locator('[data-action-browser-live]');
  await expect(live.getByRole('heading', { name: 'Needs you' })).toBeVisible();
  await live.getByRole('button', { name: /Live picture of the page/ }).click();
  await live.getByLabel('Text to type on the page').fill('482913');
  await live.getByRole('button', { name: 'Type it' }).click();
  await live.getByRole('button', { name: 'Press Enter' }).click();
  await expect.poll(() => calls.filter((c) => c.path.endsWith('/input')).map((c) => (c.body as Json).kind)).toEqual(['click', 'text', 'key']);
  const click = calls.find((c) => c.path.endsWith('/input'))?.body as Json;
  expect(Number(click.x)).toBeGreaterThanOrEqual(0);
  expect(Number(click.x)).toBeLessThanOrEqual(1280);
  expect(await serious(page, '[data-action-browser-live]')).toEqual([]);
});

test('in Arabic, and for a member the picture is owner only', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await mockActionBrowser(page, { member: true });
  await page.goto('/#tasks');
  await page.locator('.recordButton', { hasText: 'Book a seat to Riyadh' }).first().click();
  const live = page.locator('[data-action-browser-live]');
  await expect(live.getByText('بانتظار موافقتك').first()).toBeVisible();
  await expect(live.getByText('مالك مساحة العمل فقط يرى الصورة المباشرة.')).toBeVisible();
  await expect(live.getByText('فتح shop.example')).toBeVisible();
  expect(await serious(page, '[data-action-browser-live]')).toEqual([]);
  expect(await noOverflow(page)).toBe(true);
});
