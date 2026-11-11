import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { useSavedLocale } from './support/workspaceApi';

// The Approvals settings page (docs/approvals-and-risk.md#approvals-settings): policy, rules (read
// only, with the way to Governance), routing from the rules and policy, reminders; each with its own
// state. #policy and #approvalSettings/<section> open it.
const now = '2026-10-18T10:00:00.000Z';
type Json = Record<string, unknown>;
const envelope = (code: string, message: string) => ({ error: { code, message, correlation_id: 'corr_e2e', details: [] } });

const rules: Json[] = [
  { id: 'rule_big', name: 'Refunds above 250', action_pattern: 'commerce.refund', minimum_approvers: 1, required_roles: ['owner'], enabled: true, created_at: now, conditions: { amount_above: 250 }, approver_steps: [{ approvers: ['owner'] }] },
  { id: 'rule_small', name: 'Small refunds', action_pattern: 'commerce.refund', minimum_approvers: 1, required_roles: ['owner'], enabled: true, created_at: now, conditions: { amount_at_most: 250 }, approver_steps: [{ approvers: ['user:care_lead'] }] },
  { id: 'rule_off', name: 'Old quote rule', action_pattern: 'business.quote', minimum_approvers: 1, required_roles: ['owner'], enabled: false, created_at: now, conditions: { amount_above: 5000 }, approver_steps: [{ approvers: ['owner'] }] },
];

async function mockApi(page: Page, options: { role?: string; rules?: Json[] | 'fail'; reminder?: number | null } = {}) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  let policy: Json = { two_person_rule: true, medium_risk_requires_approval: false, updated_by: 'user_local', updated_at: now };
  let reminder: Json = { reminder_hours: options.reminder ?? null, updated_by: null, updated_at: null };
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname, method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    calls.push({ method, path, body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return json({}, 404);
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals') return json([]);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: options.role ?? 'owner', active: true, created_at: now, updated_at: now });
    if (path === '/api/v1/workspace-members') return json([{ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: 'owner', active: true, created_at: now, updated_at: now }, { tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_two', role: 'owner', active: true, created_at: now, updated_at: now }]);
    if (path === '/api/v1/approval-policy' && method === 'GET') return json(policy);
    if (path === '/api/v1/approval-policy' && method === 'PUT') { policy = { ...(body as Json), updated_by: 'user_local', updated_at: now }; return json(policy); }
    if (path === '/api/v1/organization/approval-rules') return options.rules === 'fail' ? json(envelope('internal_error', 'The database is not reachable'), 500) : json(options.rules ?? rules);
    if (path === '/api/v1/approval-reminders/settings' && method === 'GET') return json(reminder);
    if (path === '/api/v1/approval-reminders/settings' && method === 'PUT') { reminder = { reminder_hours: (body as Json).reminder_hours, updated_by: 'user_local', updated_at: now }; return json(reminder); }
    if (path === '/api/v1/organization/governance') return json({ tenant_id: 'tenant_local', policy_packs: 0, active_policy_packs: 0, role_templates: 0, approval_rules: 3, memory_governance_configured: false, can_manage_organization_policy: false });
    if (path === '/api/v1/policy-packs' || path === '/api/v1/role-templates') return json([]);
    return route.abort();
  });
  return calls;
}

test('the four sections, the routing read from the rules, and the way to Governance', async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto('/#approvalSettings');
  await expect(page.getByRole('heading', { level: 1, name: 'Approvals settings' })).toBeVisible();
  const sections = page.getByRole('navigation', { name: 'Sections' });
  await expect(sections.getByRole('link')).toHaveText(['Policy', 'Rules', 'Routing', 'Reminders']);

  // Policy: in force, owner editable.
  const policy = page.getByRole('region', { name: 'Approval policy settings' });
  await expect(policy.getByRole('switch', { name: 'Two-person rule for high-risk actions' })).toBeChecked();

  // Rules: read only, with the count and the band of each.
  const rulesRegion = page.getByRole('region', { name: 'Approval rules' });
  await expect(rulesRegion.getByText('3 rules, 2 on')).toBeVisible();
  await expect(rulesRegion.getByText('Refunds above 250')).toBeVisible();
  await expect(rulesRegion.getByText(/Applies to commerce\.refund/).first()).toBeVisible();
  await expect(rulesRegion.getByText('Rules are shown here and changed on the Governance page by an organization administrator.')).toBeVisible();

  // Routing: the steps, the two bands in force, the one rule that is off, the policy's effect.
  const routing = page.getByRole('region', { name: 'Routing' });
  await expect(routing.getByRole('list').first().getByRole('listitem')).toHaveCount(5);
  const bands = routing.getByRole('heading', { name: 'Bands in force' }).locator('xpath=following-sibling::ul[1]');
  await expect(bands.getByRole('listitem')).toHaveCount(2);
  await expect(bands).toContainText('Refunds above 250');
  await expect(bands).toContainText('care_lead');
  await expect(routing.getByText('1 rule is switched off and does not route.')).toBeVisible();
  await expect(routing.getByText(/Two-person rule on/)).toBeVisible();
  await expect(routing.getByText('Medium-risk actions run without approval.')).toBeVisible();

  // Turning the medium switch on changes the routing words before saving; saving sends the PUT.
  await policy.getByRole('switch', { name: 'Approval for medium-risk actions' }).check();
  await expect(routing.getByText('Medium-risk actions pause for approval.')).toBeVisible();
  await policy.getByRole('button', { name: 'Save policy' }).click();
  await expect(policy.getByText(/Approval policy saved/)).toBeVisible();
  expect(calls.find((c) => c.method === 'PUT' && c.path === '/api/v1/approval-policy')?.body).toEqual({ two_person_rule: true, medium_risk_requires_approval: true });

  // Reminders save on their own.
  const reminders = page.getByRole('region', { name: 'Remind approvers' });
  await reminders.getByLabel('Send a reminder after').selectOption('6');
  await reminders.getByRole('button', { name: 'Save reminder' }).click();
  await expect(reminders.getByText('Approvals now remind owners after 6 hours.')).toBeVisible();
  expect(calls.find((c) => c.method === 'PUT' && c.path === '/api/v1/approval-reminders/settings')?.body).toEqual({ reminder_hours: 6 });

  // The rules are edited on Governance.
  await rulesRegion.getByRole('button', { name: 'Edit rules in Governance' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Governance' })).toBeVisible();
});

test('a section link opens the page on its section and a member reads everything read only', async ({ page }) => {
  await mockApi(page, { role: 'member' });
  await page.goto('/#approvalSettings/rules');
  await expect(page.getByRole('heading', { level: 1, name: 'Approvals settings' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Owner access required' })).toBeVisible();
  await expect(page.getByRole('switch', { name: 'Two-person rule for high-risk actions' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Save policy' })).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Approval rules' }).getByText('Refunds above 250')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Remind approvers' }).getByRole('button', { name: 'Save reminder' })).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Approval rules' })).toBeInViewport();
});

test('no rules and a failed rules load each explain themselves', async ({ page }) => {
  await mockApi(page, { rules: [] });
  await page.goto('/#policy');
  await expect(page.getByText('No organization rules yet: every action that pauses goes to the workspace owners.')).toBeVisible();
  await expect(page.getByText('No rule routes by amount yet: workspace owners approve everything that pauses.')).toBeVisible();

  await mockApi(page, { rules: 'fail' });
  await page.goto('/#policy');
  await page.reload();
  const rulesRegion = page.getByRole('region', { name: 'Approval rules' });
  await expect(rulesRegion.getByRole('alert')).toContainText('The approval rules could not be loaded.');
  await expect(rulesRegion.getByRole('button', { name: 'Retry' })).toBeVisible();
});

for (const locale of ['en', 'ar'] as const) {
  for (const size of [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 }]) {
    test(`the page fits at ${size.width}x${size.height} in ${locale} with no serious axe finding`, async ({ page }, testInfo) => {
      await page.setViewportSize(size);
      await useSavedLocale(page, locale);
      await mockApi(page);
      await page.goto('/#approvalSettings');
      await expect(page.getByRole('region', { name: locale === 'ar' ? 'التوجيه' : 'Routing' })).toBeVisible();
      await expect(page.getByRole('region', { name: locale === 'ar' ? 'ذكّر من يوافق' : 'Remind approvers' }).getByRole('combobox')).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 'overflow').toBeLessThanOrEqual(0);
      const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
      expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
      if (process.env.ANUM_SCREENSHOTS) await page.screenshot({ path: testInfo.outputPath(`approvals-${locale}-${size.width}.png`), fullPage: true });
    });
  }
}
