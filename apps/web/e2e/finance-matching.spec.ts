import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { financeReply, financeState } from './support/financeApi';
import { GATE_SIZES, clippedControls, horizontalOverflow } from './support/gatePages';
import { useSavedLocale } from './support/workspaceApi';

// Automatic matching on Finance settings (docs/finance-matching.md): the policy is approved, a match waiting for review is
// linked, an automatic one undone, history is previewed and applied, installments and capture health show their state. The
// API is mocked at http://localhost:8000/** like the other specs; every order and amount is SYNTHETIC.

const stamp = '2026-11-01T00:00:00Z';

async function mockApi(page: Page) {
  const state = financeState();
  const sent: { method: string; path: string; body: unknown }[] = [];
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'member', active: true });
    if (path === '/api/v1/workspaces/current') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Foundation', created_at: stamp, updated_at: stamp, archived_at: null, archived_by: null });
    if (path === '/api/v1/me/workspace-memberships') return json([{ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', role: 'member', active: true }]);
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals' || path === '/api/v1/conversations') return json([]);
    const body = request.postData() ? request.postDataJSON() : null;
    if (request.method() !== 'GET') sent.push({ method: request.method(), path, body });
    const reply = financeReply(state, request.method(), url, body);
    if (reply) return reply.status === 204 ? route.fulfill({ status: 204 }) : json(reply.body, reply.status);
    return route.abort();
  });
  return { state, sent };
}

test('the owner approves the matching policy, links a waiting match, undoes an automatic one and reprocesses history', async ({ page }) => {
  const { sent } = await mockApi(page);
  await page.goto('/#finance-settings');
  const policy = page.getByRole('region', { name: /^Automatic matching/ });
  await expect(policy).toContainText('Not approved yet');
  await policy.getByLabel('Match payments automatically').check();
  await policy.getByLabel('Installments (Tamara)').check();
  await policy.getByLabel('Allow applying to past payments').check();
  await policy.getByLabel('Everyday account').check();
  await policy.getByRole('button', { name: 'Save and approve' }).click();
  await expect(policy).toContainText('Matching policy approved');
  const saved = sent.find((s) => s.method === 'PUT' && s.path.endsWith('/automation/matching'));
  expect(saved?.body).toMatchObject({ match_transactions: true, reconcile_installments: true, reprocess_history: true, source_account_ids: ['fin_acct_current'], approve: true });
  await expect(policy).toContainText('Approved policy pfm-1.r1');

  const matches = page.getByRole('region', { name: /^Automatic matches/ });
  await expect(matches.getByRole('listitem').filter({ hasText: 'Example Shop 2/4' })).toContainText('It could pay more than one bill or installment');
  await matches.getByRole('button', { name: 'Link it: Example Shop 2/4' }).click();
  await expect(matches).toContainText('Linked.');
  expect(sent.some((s) => s.path.endsWith('/matching/records/a/accept'))).toBe(true);
  const foreign = matches.getByRole('listitem').filter({ hasText: 'Sample Outlet 2/4' });
  await expect(foreign).toContainText('Paid in another currency, and the bank gave no original amount');
  await expect(foreign.getByRole('group', { name: 'Across currencies' })).toContainText('ANUM does not know how much');
  await matches.getByRole('button', { name: 'Linked automatically' }).click();
  const converted = matches.getByRole('listitem').filter({ hasText: 'Sample Outlet 1/4' }).getByRole('group', { name: 'Across currencies' });
  await expect(converted).toContainText('worked out from the two amounts');
  await expect(converted).toContainText('Bank FX fee, kept apart');
  await matches.getByRole('button', { name: 'Undo: Demo Market 3/4' }).click();
  await expect(matches).toContainText('Link removed. The payment stays in your ledger.');

  const history = page.getByRole('region', { name: /^Reprocess your history/ });
  await history.getByRole('button', { name: 'Preview' }).click();
  await expect(history).toContainText('Finished');
  await expect(history).toContainText('Waiting for you');
  await history.getByRole('button', { name: 'Apply' }).click();
  await expect(history).toContainText('Linked automatically');
  await history.getByRole('button', { name: 'Undo this run' }).click();
  await expect(history).toContainText('Links removed: 2. The payments stay.');

  const installments = page.getByRole('region', { name: /^Installments and their payments/ });
  await expect(installments).toContainText('Example Shop, order example-order-1');
  await expect(installments).toContainText('Paid, verified');
  await expect(installments).toContainText('Provisionally matched');
  const capture = page.getByRole('region', { name: /^SMS and Apple Pay capture/ });
  await expect(capture).toContainText('Capture needs attention');
  await expect(capture).toContainText('EXAMPLEBANK: 1 messages');
  await expect(capture).toContainText('Card or account ending 1234: Everyday account');
});

test('a viewer sees why matching cannot be changed', async ({ page }) => {
  const { state } = await mockApi(page);
  state.denied = true;
  await page.goto('/#finance-settings');
  await expect(page.getByRole('heading', { name: 'Finance is not available to your role' }).first()).toBeVisible();
});

for (const locale of ['en', 'ar'] as const) {
  test(`@gate automatic matching on Finance settings fits every size without sideways scrolling (${locale})`, async ({ page }) => {
    await mockApi(page);
    if (locale === 'ar') await useSavedLocale(page, 'ar');
    for (const size of GATE_SIZES) {
      await page.setViewportSize(size);
      await page.goto('/#finance-settings');
      await expect(page.locator('.fmatchPolicy')).toBeVisible();
      await expect(page.locator('.fmatchOrders')).toBeVisible();
      expect(await horizontalOverflow(page), `at ${size.width}`).toBeLessThanOrEqual(0);
      expect(await clippedControls(page), `at ${size.width}`).toEqual([]);
      const dir = process.env.FIN_SHOTS;
      if (dir) await page.screenshot({ path: `${dir}/matching-${locale}-${size.width}.png`, fullPage: true });
    }
    const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
    expect(serious).toEqual([]);
  });
}
