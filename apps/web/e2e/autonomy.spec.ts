import { expect, test, type Page } from '@playwright/test';

// Activity feed, Undo, "Always ask for this", suggestions, autonomy settings and guardrails, and the
// "ANUM acted" notification, against a mocked API (docs/autonomy.md).
const now = new Date().toISOString();
const soon = new Date(Date.now() + 10 * 60_000).toISOString();
type Json = Record<string, unknown>;
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });

const action = (id: string, extra: Json = {}): Json => ({
  id, task_id: 'task_1', tool: 'email.archive', category: 'email', action_kind: 'archive', level: 'act_and_notify', pattern_key: 'email.archive',
  template: null, summary: `Archived email ${id}`, target: null, status: 'done', requested_by: 'user_local', created_at: now, reversible: true,
  undo_available: true, undo_expires_at: soon, undo_token: `undo_token_${id}`, can_undo: true, undone_by: null, undone_at: null, undo_error: null,
  pattern_level: null, ...extra,
});
const silent = action('auto_read', { tool: 'anum.respond', category: 'general', action_kind: 'read', level: 'act_silently', pattern_key: 'anum.respond', summary: 'Answered the weekly sales question', reversible: false, undo_available: false, undo_expires_at: null, undo_token: null });

const overview = (opts: { owner?: boolean; admin?: boolean } = {}): Json => ({
  settings: {
    category_levels: {}, amount_threshold: 0, recipients_threshold: 5, price_change_percent_threshold: 0, daily_caps: {}, quiet_hours: null,
    blocked_recipients: [], blocked_domains: [], approved_templates: ['order_status'], proposal_after: 3, updated_by: null, updated_at: null,
  },
  guardrails: {
    spending_limit: null, max_auto_amount: null, max_auto_recipients: 8, max_auto_price_change_percent: null, category_ceilings: { commerce: 'ask_always' },
    daily_caps: {}, quiet_hours: null, blocked_recipients: [], blocked_domains: [], updated_by: null, updated_at: null,
  },
  effective: {},
  categories: ['email', 'whatsapp', 'commerce', 'developer', 'files', 'tasks', 'general'].map((category) => ({ category, level: 'act_silently', ceiling: category === 'commerce' ? 'ask_always' : null, effective: category === 'commerce' ? 'ask_always' : 'act_silently' })),
  recommendations: [
    { kind: 'price_change', recommended: 'ask_always', protected: true, note: '' }, { kind: 'stock_change', recommended: 'ask_always', protected: true, note: '' },
    { kind: 'delete', recommended: 'ask_always', protected: true, note: '' }, { kind: 'payment', recommended: 'ask_always', protected: true, note: '' },
    { kind: 'merge_code', recommended: 'ask_always', protected: true, note: '' },
  ],
  pending_proposals: 1, can_manage_workspace: opts.owner ?? true, can_manage_guardrails: opts.admin ?? false, can_tighten: true,
});

interface Options { owner?: boolean; admin?: boolean; empty?: boolean; failFeed?: boolean; floor422?: boolean; sse?: string }

async function mockApi(page: Page, options: Options = {}) {
  const calls: { method: string; path: string; search: string; body: unknown }[] = [];
  let items: Json[] = options.empty ? [] : [action('auto_1'), silent, ...Array.from({ length: 25 }, (_, i) => action(`auto_p${i}`, { summary: `Paused listing ${i}`, tool: 'commerce.pause_listing', category: 'commerce', action_kind: 'pause', pattern_key: 'commerce.pause_listing' }))];
  let proposals: Json[] = options.empty ? [] : [{ id: 'aprop_1', pattern_key: 'whatsapp.send_template|template=shipping', tool: 'whatsapp.send_template', template: 'shipping', category: 'whatsapp', action_kind: 'send', proposed_level: 'act_and_notify', approvals: 3, status: 'pending', created_at: now }];
  let settings = overview(options);
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    calls.push({ method, path, search: url.search, body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (path === '/api/v1/events/stream') {
      return options.sse ? route.fulfill({ status: 200, contentType: 'text/event-stream', body: options.sse }) : route.fulfill({ status: 204 });
    }
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals') return json([]);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: options.owner === false ? 'member' : 'owner', active: true });
    if (path === '/api/v1/autonomy/settings' && method === 'GET') return json(settings);
    if (path === '/api/v1/autonomy/settings' && method === 'PUT') {
      if (options.floor422) return json(envelope('validation_error', "Above the organization's guardrails: the recipients threshold can be at most 8. Lower these values, or ask an organization administrator to change the guardrails."), 422);
      settings = { ...settings, settings: { ...(body as Json), updated_by: 'user_local', updated_at: now } };
      return json(settings);
    }
    if (path === '/api/v1/autonomy/guardrails' && method === 'PUT') return json({ ...(body as Json), updated_by: 'user_local', updated_at: now });
    if (path === '/api/v1/autonomy/rules' && method === 'GET') return json([{ id: 'arule_1', pattern_key: 'email.delete', tool: 'email.delete', template: null, category: 'email', action_kind: 'delete', level: 'ask_always', source: 'tighten', created_by: 'user_local', created_at: now }]);
    if (path === '/api/v1/autonomy/rules' && method === 'POST') {
      const b = body as Json;
      if (b.level !== 'ask_always' && b.tool === 'commerce.update_price') return json(envelope('validation_error', 'ANUM always asks before money, prices, stock, deleting and merging code; no rule can change that.'), 422);
      return json({ id: 'arule_2', pattern_key: b.template ? `${b.tool}|template=${b.template}` : b.tool, tool: b.tool, template: b.template, category: 'whatsapp', action_kind: 'send', level: b.level, source: 'manual', created_by: 'user_local', created_at: now }, 201);
    }
    if (path === '/api/v1/autonomy/proposals') return json(proposals);
    if (path === '/api/v1/autonomy/proposals/aprop_1/accept') { proposals = []; return json({ id: 'arule_3' }); }
    if (path === '/api/v1/autonomy/actions' && method === 'GET') {
      if (options.failFeed) return json(envelope('internal_error', 'Database unavailable'), 500);
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      const category = url.searchParams.get('category');
      const limit = Number(url.searchParams.get('limit') ?? 25);
      let list = items.filter((i) => (!category || i.category === category) && (!q || String(i.summary).toLowerCase().includes(q)));
      const cursor = url.searchParams.get('cursor');
      if (cursor) list = list.slice(list.findIndex((i) => i.id === cursor) + 1);
      return json({ items: list.slice(0, limit), next_cursor: list.length > limit ? list[limit - 1].id : null });
    }
    const one = /^\/api\/v1\/autonomy\/actions\/([^/]+)(\/(undo|tighten))?$/.exec(path);
    if (one) {
      const found = items.find((i) => i.id === one[1]);
      if (!found) return json(envelope('not_found', 'Action not found'), 404);
      if (!one[3]) return json(found);
      if (one[3] === 'undo') {
        const next = { ...found, status: 'undone', undone_by: 'user_local', undone_at: now, undo_available: false, undo_token: null };
        items = items.map((i) => i.id === found.id ? next : i);
        return json(next);
      }
      return json({ id: 'arule_t', pattern_key: found.pattern_key, tool: found.tool, template: null, category: found.category, action_kind: found.action_kind, level: 'ask_always', source: 'tighten', created_by: 'user_local', created_at: now }, 201);
    }
    return route.abort();
  });
  return calls;
}

test('the activity feed lists what ANUM did, undoes it, tightens it and accepts a suggestion', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#activity');
  await expect(page.getByRole('heading', { level: 1, name: 'Activity' })).toBeVisible();
  const feed = page.getByRole('list', { name: 'Activity' });
  await expect(feed.getByText('Archived email auto_1')).toBeVisible();
  await expect(feed.getByText('Answered the weekly sales question')).toBeVisible();
  await expect(feed.getByText(/^Undo until /).first()).toBeVisible();

  await page.getByRole('button', { name: 'Undo: Archived email auto_1' }).click();
  await expect(page.getByText('Undone: Archived email auto_1')).toBeVisible();
  await expect(feed.getByText('Undone by user_local')).toBeVisible();
  expect(calls.find((c) => c.path === '/api/v1/autonomy/actions/auto_1/undo')?.body).toEqual({ undo_token: 'undo_token_auto_1' });

  await page.getByRole('button', { name: 'Always ask before anum.respond' }).click();
  await expect(page.getByText('ANUM will ask before anum.respond from now on.')).toBeVisible();
  await expect(feed.getByText('Asks from now on')).toBeVisible();

  const suggestions = page.getByRole('region', { name: 'Suggestions to ask less' });
  await expect(suggestions.getByText(/You approved it 3 times/)).toBeVisible();
  await suggestions.getByRole('button', { name: 'Accept' }).click();
  await expect(page.getByText(/now acts and notifies you/)).toBeVisible();
  await expect(suggestions).toHaveCount(0);
});

test('filters, search, paging, and the empty filtered state', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#activity');
  const feed = page.getByRole('list', { name: 'Activity' });
  await expect(feed.getByRole('listitem')).toHaveCount(25);
  await page.getByRole('button', { name: 'Load more' }).click();
  await expect(feed.getByRole('listitem')).toHaveCount(27);
  expect(calls.some((c) => c.search.includes('cursor='))).toBe(true);

  await page.getByLabel('Category').selectOption('email');
  await expect(feed.getByRole('listitem')).toHaveCount(1);
  expect(calls.some((c) => c.path === '/api/v1/autonomy/actions' && c.search.includes('category=email'))).toBe(true);
  await page.getByRole('searchbox').fill('nothing like this');
  await page.getByRole('button', { name: 'Search the activity' }).click();
  await expect(page.getByRole('heading', { name: 'No matching actions' })).toBeVisible();
  await page.getByRole('button', { name: 'Clear filters' }).first().click();
  await expect(feed.getByRole('listitem')).toHaveCount(25);
});

test('empty and error states say what to do next', async ({ page }) => {
  await mockApi(page, { empty: true });
  await page.goto('/#activity');
  await expect(page.getByRole('heading', { name: 'Nothing done on its own yet' })).toBeVisible();
  await page.locator('.emptyState').getByRole('button', { name: 'Autonomy settings' }).click();
  await expect(page).toHaveURL(/#autonomy$/);

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockApi(page, { failFeed: true });
  await page.goto('/#activity');
  await page.reload();
  await expect(page.getByRole('alert').filter({ hasText: 'The activity could not be loaded. The server could not reach its database' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('owners save autonomy settings; floors and refusals are shown in place', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#autonomy');
  await expect(page.getByRole('heading', { level: 1, name: 'Autonomy' })).toBeVisible();
  await expect(page.getByText('Organization limit: Ask always')).toBeVisible();
  await page.getByLabel('Email', { exact: true }).selectOption('act_and_notify');
  await page.getByLabel('Recipients threshold', { exact: true }).fill('3');
  await page.getByLabel('Approved templates').fill('order_status\nshipping');
  await page.getByRole('button', { name: 'Save autonomy' }).click();
  await expect(page.getByText(/Autonomy saved/)).toBeVisible();
  const put = calls.find((c) => c.method === 'PUT' && c.path === '/api/v1/autonomy/settings')?.body as Json;
  expect(put.recipients_threshold).toBe(3);
  expect(put.approved_templates).toEqual(['order_status', 'shipping']);
  expect((put.category_levels as Json).email).toBe('act_and_notify');

  await page.getByLabel('Recipients threshold', { exact: true }).fill('12');
  await expect(page.getByText('Recipients threshold is above the organization limit of 8.')).toBeVisible();

  await page.getByLabel('Tool').fill('commerce.update_price');
  await page.getByLabel('Level', { exact: true }).selectOption('act_and_notify');
  await page.getByRole('button', { name: 'Add rule' }).click();
  await expect(page.getByText(/no rule can change that/)).toBeVisible();

  const guard = page.getByRole('region', { name: 'Organization guardrails' });
  await expect(guard.getByText('Organization administrator required')).toBeVisible();
  await expect(guard.getByLabel('Spending limit')).toBeDisabled();
});

test('the API refusing a save above the floor is shown, and members see settings read-only', async ({ page }) => {
  await mockApi(page, { floor422: true });
  await page.goto('/#autonomy');
  await page.getByRole('button', { name: 'Save autonomy' }).click();
  await expect(page.getByText(/the recipients threshold can be at most 8/)).toBeVisible();

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await mockApi(page, { owner: false });
  await page.reload();
  await expect(page.getByRole('region', { name: 'Owner access required' })).toBeVisible();
  await expect(page.getByLabel('Email', { exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Save autonomy' })).toHaveCount(0);
  await expect(page.getByText('Members can add "Ask always" rules; only owners can let an action run on its own.')).toBeVisible();
});

test('organization administrators save guardrails', async ({ page }) => {
  const calls = await mockApi(page, { admin: true });
  await page.goto('/#autonomy');
  const guard = page.getByRole('region', { name: 'Organization guardrails' });
  await guard.getByLabel('Spending limit').fill('5000');
  await guard.getByLabel('Blocked domains').fill('competitor.example');
  await guard.getByRole('button', { name: 'Save guardrails' }).click();
  await expect(page.getByText(/Guardrails saved for every workspace/)).toBeVisible();
  const put = calls.find((c) => c.path === '/api/v1/autonomy/guardrails')?.body as Json;
  expect(put.spending_limit).toBe(5000);
  expect(put.blocked_domains).toEqual(['competitor.example']);
  expect(put.category_ceilings).toEqual({ commerce: 'ask_always' });
});

test('an "ANUM acted" notification offers Undo while the window is open', async ({ page }) => {
  const event = { id: 'evt_1', type: 'autonomy.action_taken', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', subject: 'auto_1', correlation_id: 'task_1', created_at: now, payload: { action_id: 'auto_1', summary: 'Archived email auto_1', tool: 'email.archive', undo_until: soon } };
  const calls = await mockApi(page, { sse: `data: ${JSON.stringify(event)}\n\n` });
  await page.goto('/#tasks');
  const notices = page.getByRole('region', { name: 'Actions ANUM took' });
  await expect(notices.getByText('Archived email auto_1')).toBeVisible();
  await notices.getByRole('button', { name: 'Undo' }).click();
  await expect(notices.getByText('Undone.')).toBeVisible();
  expect(calls.some((c) => c.path === '/api/v1/autonomy/actions/auto_1/undo')).toBe(true);
  await notices.getByRole('button', { name: 'Dismiss' }).click();
  await expect(notices).toHaveCount(0);
});

for (const size of [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 }]) {
  test(`Activity and Autonomy fit ${size.width}x${size.height} without horizontal overflow`, async ({ page }) => {
    await mockApi(page);
    await page.setViewportSize(size);
    for (const hash of ['activity', 'autonomy']) {
      await page.goto('about:blank');
      await page.goto(`/#${hash}`);
      await expect(page.locator(hash === 'activity' ? '.autonomyRow' : '.autonomyPanel').first()).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      const clipped = await page.evaluate(() => [...document.querySelectorAll('main button, main select, main input')].filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && (r.right > window.innerWidth + 1 || r.left < -1);
      }).length);
      expect(clipped).toBe(0);
    }
  });
}
