import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockWorkspaceApi, useSavedLocale } from './support/workspaceApi';

// Agent controls (docs/agent-controls.md): an owner turns the organization's emergency stop on with a
// reason, sees what it stops and the tasks it cancelled, caps an agent's monthly cost, and the
// organization owner turns the stop off. Members see the state and who can change it. API mocked.
const now = new Date().toISOString();
const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);
const usage = (cost: number, calls: number) => ({ input_tokens: calls * 100, output_tokens: calls * 50, estimated_cost_usd: cost, calls, unpriced_calls: 0 });

interface Sent { stops: unknown[]; caps: { agent: string; body: unknown }[] }

async function mockControls(page: Page, { role = 'owner', orgAdmin = true }: { role?: string; orgAdmin?: boolean } = {}): Promise<Sent> {
  await mockWorkspaceApi(page);
  const sent: Sent = { stops: [], caps: [] };
  let stop = { engaged: false, reason: null as string | null, changed_by: null as string | null, changed_by_you: false, changed_at: null as string | null, engaged_at: null as string | null };
  const caps = [
    { agent_id: 'assistant', name: 'ANUM assistant', is_default: true, status: 'active', sharing: 'workspace', monthly_cost_limit_usd: null, monthly_token_limit: null, usage: usage(0.2, 5), total_tokens: 750, percent_used: null, token_percent_used: null, exceeded: false, updated_by: null, updated_at: null },
    { agent_id: 'agent_outreach', name: 'Outreach desk', status: 'active', sharing: 'workspace', monthly_cost_limit_usd: 2, usage: usage(1.8, 12), percent_used: 90, exceeded: false, updated_by: 'user_local', updated_at: now },
    { agent_id: 'agent_fleet', name: 'Fleet desk', status: 'active', sharing: 'private', monthly_cost_limit_usd: null, usage: usage(0.4, 3), percent_used: null, exceeded: false, updated_by: null, updated_at: null },
  ];
  const owner = role === 'owner';
  await page.route('http://localhost:8000/api/v1/agent-controls**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname, method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const view = (cancelled = 0) => ({ emergency_stop: stop, can_engage: owner, can_release: owner && orgAdmin, cancelled_tasks: cancelled });
    if (path === '/api/v1/agent-controls' && method === 'GET') return json(view());
    if (path === '/api/v1/agent-controls/emergency-stop' && method === 'PUT') {
      const body = request.postDataJSON() as { engaged: boolean; reason: string };
      sent.stops.push(body);
      stop = { engaged: body.engaged, reason: body.reason, changed_by: 'user_local', changed_by_you: true, changed_at: now, engaged_at: body.engaged ? now : null };
      return json(view(body.engaged ? 2 : 0));
    }
    if (path === '/api/v1/agent-controls/held' && method === 'GET') {
      if (!owner) return json({ error: { code: 'forbidden', message: 'Owners only', correlation_id: 'c', details: [] } }, 403);
      const offset = Number(url.searchParams.get('offset') ?? '0');
      const all = Array.from({ length: 23 }, (_, index) => ({ kind: index === 0 ? 'mail' : 'approval', id: `held_${index}`, title: index === 0 ? 'October invoices' : `external.action ${index}`, detail: index === 0 ? '3 recipients' : 'api.example.com', at: now, by: 'user_local' }));
      return json({ engaged: stop.engaged, total: all.length, limit: 20, offset, items: all.slice(offset, offset + 20) });
    }
    if (path === '/api/v1/agent-controls/agent-caps' && method === 'GET') {
      if (!owner) return json({ error: { code: 'forbidden', message: 'Only a workspace owner or the organization administrator can see agent cost caps.', correlation_id: 'c', details: [] } }, 403);
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      const items = caps.filter((item) => !q || item.name.toLowerCase().includes(q));
      return json({ period_start: '2026-10-01', resets_on: '2026-11-01', can_manage: true, total: items.length, items });
    }
    const capMatch = /^\/api\/v1\/agent-controls\/agent-caps\/([^/]+)$/.exec(path);
    if (capMatch && method === 'PUT') {
      const body = request.postDataJSON() as { monthly_cost_limit_usd: number | null };
      sent.caps.push({ agent: capMatch[1], body });
      const index = caps.findIndex((item) => item.agent_id === capMatch[1]);
      caps[index] = { ...caps[index], monthly_cost_limit_usd: body.monthly_cost_limit_usd, monthly_token_limit: (body as { monthly_token_limit?: number | null }).monthly_token_limit ?? null, percent_used: body.monthly_cost_limit_usd ? (caps[index].usage.estimated_cost_usd / body.monthly_cost_limit_usd) * 100 : null, updated_at: now };
      return json(caps[index]);
    }
    return json({ error: { code: 'not_found', message: `unmocked ${method} ${path}`, correlation_id: 'c', details: [] } }, 404);
  });
  return sent;
}

test('an owner stops every agent with a reason, caps an agent, and the organization owner turns it off', async ({ page }) => {
  const sent = await mockControls(page);
  await page.goto('/#agentControls');
  await expect(page.getByRole('heading', { name: 'Agent controls', level: 2 })).toBeVisible();
  const card = page.getByRole('region', { name: 'Emergency stop for all agents and sends' });
  await expect(card.getByText('Off: agents are running')).toBeVisible();

  // A reason is required before anything happens.
  await card.getByRole('button', { name: 'Stop all agents and sends' }).click();
  await expect(card.getByText('Say why in at least 3 characters.')).toBeVisible();
  expect(sent.stops).toEqual([]);

  await card.getByLabel('Reason (kept in the audit log)').fill('Outreach agent is sending duplicates');
  await card.getByRole('button', { name: 'Stop all agents and sends' }).click();
  const confirm = page.getByRole('alertdialog', { name: 'Stop all agents and sends?' });
  await expect(confirm).toBeVisible();
  await confirm.getByRole('button', { name: 'Stop all agents and sends' }).click();
  await expect(card.getByText('The emergency stop is on. 2 running tasks were cancelled.')).toBeVisible();
  await expect(card.getByText('On: everything is paused')).toBeVisible();
  await expect(card.getByText('Reason: Outreach agent is sending duplicates')).toBeVisible();

  // What the stop holds, paged.
  const held = page.getByRole('list', { name: 'Held sends and actions' });
  await expect(held.getByText('October invoices')).toBeVisible();
  await expect(held.getByText('Scheduled email')).toBeVisible();
  await expect(page.getByText('1 to 20 of 23')).toBeVisible();
  await page.getByRole('navigation', { name: 'Held item pages' }).getByRole('button', { name: 'Next' }).click();
  await expect(page.getByText('21 to 23 of 23')).toBeVisible();
  expect(sent.stops).toEqual([{ engaged: true, reason: 'Outreach agent is sending duplicates' }]);

  // Caps: usage against the cap, a warning near it, and a new cap saved.
  const caps = page.getByRole('list', { name: 'Agents with their monthly cost cap and usage' });
  await expect(caps.getByText('Outreach desk')).toBeVisible();
  await expect(caps.getByText('Near the cap')).toBeVisible();
  await expect(caps.getByText('Private')).toBeVisible();
  await page.getByLabel('Monthly cap in USD for Fleet desk').fill('ten');
  await page.getByRole('button', { name: 'Save cap for Fleet desk' }).click();
  await expect(page.getByText('Enter an amount in US dollars, 0 or more.')).toBeVisible();
  await page.getByLabel('Monthly cap in USD for Fleet desk').fill('5');
  await page.getByRole('button', { name: 'Save cap for Fleet desk' }).click();
  await expect(page.getByText('Saved the cap for Fleet desk.')).toBeVisible();
  expect(sent.caps).toEqual([{ agent: 'agent_fleet', body: { monthly_cost_limit_usd: 5, monthly_token_limit: null } }]);
  // The default assistant (chat, voice) is listed and capped by tokens too.
  await expect(caps.getByText('Default assistant')).toBeVisible();
  await page.getByLabel('Monthly token cap for ANUM assistant').fill('1.5');
  await page.getByRole('button', { name: 'Save cap for ANUM assistant' }).click();
  await expect(page.getByText('Enter a whole number of tokens, 0 or more.')).toBeVisible();
  await page.getByLabel('Monthly token cap for ANUM assistant').fill('200000');
  await page.getByRole('button', { name: 'Save cap for ANUM assistant' }).click();
  await expect(page.getByText('Saved the cap for ANUM assistant.')).toBeVisible();
  expect(sent.caps[1]).toEqual({ agent: 'assistant', body: { monthly_cost_limit_usd: null, monthly_token_limit: 200000 } });

  // Search with no match offers a way back.
  await page.getByLabel('Search agents').fill('zzz');
  await expect(page.getByText('No agent matches')).toBeVisible();
  await page.getByRole('button', { name: 'Clear search' }).click();
  await expect(caps.getByText('Fleet desk')).toBeVisible();

  const serious = (await new AxeBuilder({ page }).include('main').analyze()).violations.filter((item) => item.impact === 'critical' || item.impact === 'serious');
  expect(serious).toEqual([]);

  // Turning it off.
  await card.getByLabel('Reason (kept in the audit log)').fill('Fixed the outreach agent');
  await card.getByRole('button', { name: 'Turn the stop off' }).click();
  await page.getByRole('alertdialog', { name: 'Turn the emergency stop off?' }).getByRole('button', { name: 'Turn the stop off' }).click();
  await expect(card.getByText('The emergency stop is off. Agents are running again.')).toBeVisible();
  expect(sent.stops).toEqual([{ engaged: true, reason: 'Outreach agent is sending duplicates' }, { engaged: false, reason: 'Fixed the outreach agent' }]);

  for (const size of [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(size);
    expect(await noOverflow(page), `overflow at ${size.width}x${size.height}`).toBe(true);
  }
});

test('a workspace owner who is not the organization owner can stop but not resume', async ({ page }) => {
  await mockControls(page, { orgAdmin: false });
  await page.goto('/#agentControls');
  const card = page.getByRole('region', { name: 'Emergency stop for all agents and sends' });
  await card.getByLabel('Reason (kept in the audit log)').fill('Runaway agent');
  await card.getByRole('button', { name: 'Stop all agents and sends' }).click();
  await page.getByRole('alertdialog', { name: 'Stop all agents and sends?' }).getByRole('button', { name: 'Stop all agents and sends' }).click();
  await expect(card.getByText('Only the organization owner (organization administrator) can turn the stop off.')).toBeVisible();
  await expect(card.getByRole('button', { name: 'Turn the stop off' })).toHaveCount(0);
});

test('members see the state, who can change it, and that caps are for owners', async ({ page }) => {
  await mockControls(page, { role: 'member', orgAdmin: false });
  await page.goto('/#agentControls');
  const card = page.getByRole('region', { name: 'Emergency stop for all agents and sends' });
  await expect(card.getByText('Off: agents are running')).toBeVisible();
  await expect(card.getByText('Only a workspace owner or the organization administrator can turn the stop on.')).toBeVisible();
  await expect(card.getByLabel('Reason (kept in the audit log)')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Agent caps are for owners' })).toBeVisible();
});

test('agent controls read in Arabic, right to left, without overflow', async ({ page }) => {
  await mockControls(page);
  await useSavedLocale(page, 'ar');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/#agentControls');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { name: 'الإيقاف الطارئ' })).toBeVisible();
  expect(await noOverflow(page)).toBe(true);
});
