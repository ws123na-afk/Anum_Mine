import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';

// Setup plan (docs/setup-plan.md): preview, confirm, apply, result; permission denied, errors and
// the responsive gate. The preview fixture is the API's real answer for the pilot (memory backend,
// an owner who is also organization administrator); apply and "apply all" are derived from it.
type Json = Record<string, any>;
const pilot: Json = JSON.parse(readFileSync(new URL('./support/setupPlanFixture.json', import.meta.url), 'utf8'));
const checksFixture: Json = JSON.parse(readFileSync(new URL('./support/setupPlanChecks.json', import.meta.url), 'utf8'));
const now = new Date().toISOString();
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });
const denied = 'Only a workspace owner or an organization administrator can preview and apply a setup plan, because it creates agents, automations and organization policy.';

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value));
const outcomeOf: Record<string, string> = { create: 'created', update: 'updated', present: 'present', kept: 'kept', skipped: 'skipped' };

function applied(preview: Json, failKey?: string): Json {
  const result = clone(preview);
  const outcomes: Record<string, number> = {};
  for (const item of result.items) {
    item.outcome = outcomeOf[item.action] ?? 'not_applied';
    if (item.key === failKey) { item.outcome = 'failed'; item.error = 'Knowledge store unavailable'; }
    outcomes[item.outcome] = (outcomes[item.outcome] ?? 0) + 1;
  }
  return { ...result, outcomes, applied_at: now };
}

function afterApply(preview: Json): Json {
  const next = clone(preview);
  for (const item of next.items) if (item.action === 'create' || item.action === 'update') { item.action = 'present'; item.reason_code = 'already_applied'; item.reason = 'Already applied.'; }
  next.plan.applied_items = 147;
  return next;
}

interface Options { listStatus?: number; previewFails?: boolean; applyFails?: number; failKey?: string; empty?: boolean; slowFirstPreviewMs?: number }

async function mockApi(page: Page, options: Options = {}) {
  const calls: { method: string; path: string; body?: Json }[] = [];
  let current = clone(pilot);
  let previews = 0;
  const checks = clone(checksFixture);
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    calls.push({ method, path, body: method === 'POST' || method === 'PUT' ? request.postDataJSON() : undefined });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals') return json([]);
    if (path === '/api/v1/workspace-memberships/current') {
      return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: options.listStatus === 403 ? 'member' : 'owner', active: true, created_at: now, updated_at: now });
    }
    if (path === '/api/v1/setup-plans') {
      if (options.listStatus === 403) return json(envelope('forbidden', denied), 403);
      if (options.listStatus) return json(envelope('internal_error', 'Plan store unavailable'), options.listStatus);
      return json(options.empty ? [] : [current.plan]);
    }
    if (path.endsWith('/checks') && method === 'GET') return json(checks);
    if (path.includes('/checks/') && method === 'PUT') {
      const id = path.split('/').pop()!;
      const body = request.postDataJSON() as Json;
      const check = checks.checks.find((c: Json) => c.id === id);
      check.result = { plan_id: checks.plan_id, check_id: id, ...body, updated_by: 'user_local', updated_at: now };
      checks.passed = checks.checks.filter((c: Json) => c.result?.status === 'pass').length;
      checks.failed = checks.checks.filter((c: Json) => c.result?.status === 'fail').length;
      checks.not_run = checks.total - checks.passed - checks.failed;
      checks.done = checks.passed === checks.total;
      return json(checks);
    }
    if (path.endsWith('/preview')) {
      if (options.previewFails) return json(envelope('internal_error', 'Preview failed'), 500);
      const phase = url.searchParams.get('phase');
      const preview = clone(current);
      preview.phase = phase;
      preview.overwrite_edited = url.searchParams.get('overwrite_edited') === 'true';
      if (previews++ === 0 && options.slowFirstPreviewMs) {
        // The first answer arrives after a later one, as on a slow network.
        await new Promise((resolve) => setTimeout(resolve, options.slowFirstPreviewMs));
        await json(preview);
        calls.push({ method: 'SERVED', path });
        return;
      }
      if (phase === 'all') {
        preview.budget_aed = 1200; preview.budget_usd = 326.75;
        for (const item of preview.items) if (item.action === 'later') { item.action = 'create'; item.reason_code = 'will_create'; item.reason = 'A new, empty workspace in this organization.'; }
      }
      return json(preview);
    }
    if (path.endsWith('/apply') && method === 'POST') {
      if (options.applyFails) return json(envelope(options.applyFails === 409 ? 'conflict' : 'internal_error', 'Apply failed'), options.applyFails);
      const result = applied(current, options.failKey);
      current = afterApply(current);
      return json(result);
    }
    return route.abort();
  });
  return calls;
}

test('an owner previews the pilot, confirms and sees what was created', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#setup');
  await expect(page.getByRole('heading', { name: 'Setup plan', exact: true }).first()).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Shahzeb Business Group' })).toBeVisible();
  // The rollout and the honest gaps are shown before anything is applied.
  await expect(page.getByRole('heading', { name: 'Week by week' })).toBeVisible();
  await expect(page.getByText('Gallery: Customer Care').first()).toBeVisible();
  await expect(page.getByRole('heading', { name: 'What ANUM cannot do yet' })).toBeVisible();
  await expect(page.getByText('Banking, bank reconciliation and money movement')).toBeVisible();
  await expect(page.getByText('AED 340', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Apply pilot' }).click();
  const dialog = page.getByRole('alertdialog');
  await expect(dialog).toContainText('Apply the Pilot to this workspace?');
  await expect(dialog).toContainText('Automations are added turned off');
  await dialog.getByRole('button', { name: 'Apply now' }).click();
  await expect(page.getByRole('heading', { name: /^Applied at / })).toBeVisible();
  await expect(page.locator('.planOutcomes')).toContainText('Created');
  const apply = calls.find((c) => c.method === 'POST' && c.path.endsWith('/apply'));
  expect(apply?.body).toEqual({ phase: 'pilot', overwrite_edited: false, plan_version: 2 });
  // The fresh preview says there is nothing left to change.
  await expect(page.getByRole('button', { name: 'Nothing to change' })).toBeDisabled();
});

test('the whole plan and "Replace my changes" are sent as chosen, and items can be searched', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#setup');
  await page.getByRole('button', { name: 'Whole plan' }).click();
  await expect(page.getByText('AED 1,200', { exact: true })).toBeVisible();
  await page.getByLabel('Replace my changes').check();
  await expect.poll(() => calls.some((c) => c.path.endsWith('/preview') && c.method === 'GET')).toBe(true);
  await page.getByRole('searchbox', { name: 'Search items' }).fill('Morning Brief');
  await expect(page.locator('.planRow')).toHaveCount(2); // the automation and the saved command
  await page.getByRole('searchbox', { name: 'Search items' }).fill('zzz-nothing');
  await expect(page.getByText('No item matches.')).toBeVisible();
  await page.getByRole('searchbox', { name: 'Search items' }).fill('');
  await page.getByLabel('Section').selectOption('workspaces');
  await expect(page.locator('.planRow').first()).toContainText('Will create');
  await page.getByRole('button', { name: 'Apply whole plan' }).click();
  await expect(page.getByRole('alertdialog')).toContainText('Replace my changes is on');
  await page.getByRole('alertdialog').getByRole('button', { name: 'Apply now' }).click();
  await expect(page.getByRole('heading', { name: /^Applied at / })).toBeVisible();
  const apply = calls.find((c) => c.method === 'POST' && c.path.endsWith('/apply'));
  expect(apply?.body).toEqual({ phase: 'all', overwrite_edited: true, plan_version: 2 });
});

test('a late answer for an earlier choice never replaces the newest preview, and apply sends what is shown', async ({ page }) => {
  const calls = await mockApi(page, { slowFirstPreviewMs: 1500 });
  await page.goto('/#setup');
  await expect(page.getByRole('button', { name: 'Apply pilot' })).toBeDisabled(); // no preview yet
  await page.getByRole('button', { name: 'Whole plan' }).click();
  await expect(page.getByText('AED 1,200', { exact: true })).toBeVisible();
  // The pilot's answer, asked first, arrives last and is dropped.
  await expect.poll(() => calls.some((c) => c.method === 'SERVED')).toBe(true);
  await expect(page.getByText('AED 1,200', { exact: true })).toBeVisible();
  await expect(page.getByText('AED 340', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Apply whole plan' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Apply now' }).click();
  await expect(page.getByRole('heading', { name: /^Applied at / })).toBeVisible();
  const apply = calls.find((c) => c.method === 'POST' && c.path.endsWith('/apply'));
  expect(apply?.body).toEqual({ phase: 'all', overwrite_edited: false, plan_version: 2 });
});

test('pilot checks: record a result with a note and evidence, and the Week 1 gate follows', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#setup');
  await expect(page.getByText(/Week 1 check: 0 of 18 passed/)).toBeVisible();
  await page.getByRole('button', { name: /Pilot checks/ }).click();
  await expect(page.getByRole('heading', { name: '0 of 18 passed' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Customer care' })).toBeVisible();
  const row = page.locator('.planCheck').filter({ hasText: 'CC05' });
  await expect(row).toContainText('Refund request AED 300');
  await expect(row).toContainText("ANUM's automatic tests already check the approval part of this case.");
  await expect(row.getByRole('button', { name: 'Save result' })).toBeDisabled();
  await row.getByRole('group', { name: 'Result CC05' }).getByRole('button', { name: 'Pass' }).click();
  await row.getByLabel('Note').fill('Approval waited; nothing paid');
  await row.getByLabel(/Proof/).fill('http://insecure.example');
  await expect(row.getByText('Proof links must start with https://')).toBeVisible();
  await expect(row.getByRole('button', { name: 'Save result' })).toBeDisabled();
  await row.getByLabel(/Proof/).fill('approval_123');
  await row.getByRole('button', { name: 'Save result' }).click();
  await expect(row.getByText('Saved CC05.')).toBeVisible();
  await expect(page.getByRole('heading', { name: '1 of 18 passed' })).toBeVisible();
  await expect(row).toContainText('Recorded by user_local');
  const put = calls.find((c) => c.method === 'PUT');
  expect(put?.path).toBe('/api/v1/setup-plans/shahzeb-business-group/checks/CC05');
  expect(put?.body).toEqual({ status: 'pass', note: 'Approval waited; nothing paid', evidence: 'approval_123' });
  // CC04 is honest about what ANUM cannot do.
  await expect(page.locator('.planCheck').filter({ hasText: 'CC04' })).toContainText('Not supported');
});

test('a failed item is listed with its reason', async ({ page }) => {
  await mockApi(page, { failKey: 'k05' });
  await page.goto('/#setup');
  await page.getByRole('button', { name: 'Apply pilot' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Apply now' }).click();
  await expect(page.getByText('1 item failed. Fix what it says, then apply again')).toBeVisible();
  await expect(page.locator('.planFailures')).toContainText('05 Finance');
  await expect(page.locator('.planFailures')).toContainText('Knowledge store unavailable');
});

test('a member sees who can apply the plan', async ({ page }) => {
  const calls = await mockApi(page, { listStatus: 403 });
  await page.goto('/#setup');
  await expect(page.getByText(/Only a workspace owner or an organization administrator can apply a setup plan/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Open Members' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Apply pilot' })).toHaveCount(0);
  expect(calls.some((c) => c.method === 'POST' && c.path.startsWith('/api/v1/setup-plans'))).toBe(false);
});

test('load errors say what failed and offer a retry; an empty server says so', async ({ page }) => {
  await mockApi(page, { listStatus: 500 });
  await page.goto('/#setup');
  await expect(page.getByText(/The setup plan could not be loaded/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('an empty server explains where plans come from', async ({ page }) => {
  await mockApi(page, { empty: true });
  await page.goto('/#setup');
  await expect(page.getByText('No setup plan is available on this server.')).toBeVisible();
});

test('a plan changed since the preview asks to review again', async ({ page }) => {
  await mockApi(page, { applyFails: 409 });
  await page.goto('/#setup');
  await page.getByRole('button', { name: 'Apply pilot' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Apply now' }).click();
  await expect(page.getByText('The plan changed since you opened this page.')).toBeVisible();
});

test('the page reads right to left in Arabic', async ({ page }) => {
  await mockApi(page);
  await page.addInitScript(() => localStorage.setItem('anum.locale', 'ar'));
  await page.goto('/#setup');
  await expect(page.getByRole('button', { name: 'طبّق التجربة' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

for (const size of [
  { width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 },
]) {
  test(`the setup plan fits at ${size.width}x${size.height}`, async ({ page }, testInfo) => {
    await page.setViewportSize(size);
    await mockApi(page);
    await page.goto('/#setup');
    await expect(page.getByRole('heading', { name: 'Week by week' })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    const apply = (await page.getByRole('button', { name: 'Apply pilot' }).boundingBox())!;
    expect(apply.x).toBeGreaterThanOrEqual(0);
    expect(apply.x + apply.width).toBeLessThanOrEqual(size.width);
    for (const selector of ['.planControls', '.planRollout', '.planItems', '.planGaps']) {
      const box = (await page.locator(selector).first().boundingBox())!;
      expect(box.x + box.width, selector).toBeLessThanOrEqual(size.width + 0.5);
    }
    if (process.env.ANUM_SCREENSHOTS) await page.screenshot({ path: testInfo.outputPath(`setup-${size.width}.png`), fullPage: false });
    await page.getByRole('button', { name: /Pilot checks/ }).click();
    const row = page.locator('.planCheck').first();
    await expect(row).toBeVisible();
    const save = (await row.getByRole('button', { name: 'Save result' }).boundingBox())!;
    expect(save.x + save.width).toBeLessThanOrEqual(size.width);
    const checksOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(checksOverflow).toBeLessThanOrEqual(0);
    if (process.env.ANUM_SCREENSHOTS) await page.screenshot({ path: testInfo.outputPath(`checks-${size.width}.png`), fullPage: false });
  });
}
