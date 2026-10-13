import { expect, test, type Page } from '@playwright/test';

// Approval routing by AED amount (docs/approvals-and-risk.md#approval-routing): the Approvals cards
// say who may approve each item and why, only eligible people get Approve, and organization
// administrators manage the rules (AED bands, a second step) on the Governance page. API mocked.
const now = new Date().toISOString();
const later = new Date(Date.now() + 6 * 3600_000).toISOString();
const hash = '5c'.repeat(32);
type Json = Record<string, unknown>;

const task = { id: 'task_refund', title: 'Refund TEST-ORDER-003', prompt: 'Refund AED 300', status: 'waiting_approval', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: now, updated_at: now };
const base = {
  task_id: 'task_refund', risk_level: 'high', status: 'pending', reason: 'Payment actions always need a person.', created_at: now,
  run_id: 'run_refund', step_id: 'step_1', payload_hash: hash, expires_at: later, decided_at: null, decided_by: null, required_approvals: 1, approvers: [],
};
const largeRefund = {
  ...base, id: 'approval_large', action: 'commerce.own_store.refund_order', arguments: { order: 'TEST-ORDER-003', amount: 300, currency: 'AED' },
  routing: {
    routed: true, rules: ['Refunds above AED 250'], steps: [{ approvers: [['owner']], approved_by: null }], current_step: 0, fallback: false,
    reasons: [{ code: 'amount_above', rule: 'Refunds above AED 250', applies: true, currency: 'AED', amount: '300', total: '300', threshold: '250', related: 0 }],
    can_approve: false, can_reject: false, refusal: 'not_listed', summary: 'Refunds above AED 250: AED 300 > AED 250: owners',
  },
};
const smallRefund = {
  ...base, id: 'approval_small', action: 'commerce.own_store.refund_order', arguments: { order: 'TEST-ORDER-001', amount: 200, currency: 'AED' },
  routing: {
    routed: true, rules: ['Refunds up to AED 250'], steps: [{ approvers: [['owner', 'user:user_local']], approved_by: null }], current_step: 0, fallback: false,
    reasons: [{ code: 'amount_at_most', rule: 'Refunds up to AED 250', applies: true, currency: 'AED', amount: '200', total: '200', threshold: '250', related: 0 }],
    can_approve: true, can_reject: true, refusal: null, summary: '',
  },
};
const commitment = {
  ...base, id: 'approval_commit', action: 'commerce.own_store.purchase_stock', arguments: { supplier: 'Test Supplier', amount: 6000 }, required_approvals: 2,
  approvers: [{ user_id: 'finance_reviewer', approved_at: now, reason: null }],
  suppressed: [{ recipient: '+971•••4567', channel: 'whatsapp', purpose: 'all' }],
  routing: {
    routed: true, rules: ['Commitments above AED 5,000'], current_step: 1, fallback: false,
    steps: [{ approvers: [['user:finance_reviewer']], approved_by: 'finance_reviewer' }, { approvers: [['owner']], approved_by: null }],
    reasons: [{ code: 'amount_above', rule: 'Commitments above AED 5,000', applies: true, currency: 'AED', amount: '6000', total: '6000', threshold: '5000', related: 0 }],
    can_approve: true, can_reject: true, refusal: null, summary: '',
  },
};

async function mockApprovals(page: Page, approvals: Json[]) {
  const decisions: { path: string; body: unknown }[] = [];
  let list = approvals;
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    if (path === '/api/v1/tasks') return json([task]);
    if (path === '/api/v1/approvals') return json(list);
    if (path.startsWith('/api/v1/approvals/') && request.method() === 'POST') {
      decisions.push({ path, body: request.postDataJSON() });
      const id = path.split('/')[4];
      const approved = { ...(list.find((a) => a.id === id) as Json), status: 'approved', decided_by: 'user_local', decided_at: now, routing: null };
      list = list.map((a) => (a.id === id ? approved : a));
      return json({ approval: approved, task: { ...task, status: 'completed' }, run: { id: 'run_refund', task_id: 'task_refund', status: 'completed', steps: [] } });
    }
    return route.abort();
  });
  return decisions;
}

test('a refund above AED 250 says owners only, and someone else sees it read only', async ({ page }) => {
  const decisions = await mockApprovals(page, [largeRefund]);
  await page.goto('/#approvals');
  const card = page.getByRole('article', { name: 'Approval for commerce.own_store.refund_order' });
  const who = card.getByRole('region', { name: 'Who can approve' });
  await expect(who).toContainText('Owners only');
  await expect(who).toContainText('Refunds above AED 250: AED 300 is more than AED 250');
  await expect(card.getByRole('note').filter({ hasText: 'Read only: Owners only can approve this.' })).toBeVisible();
  await expect(card.getByRole('button', { name: 'Approve' })).toBeDisabled();
  await expect(card.getByRole('button', { name: 'Reject' })).toBeDisabled();
  expect(decisions).toEqual([]);
});

test('the delegated approver approves a small refund with the displayed hash', async ({ page }) => {
  const decisions = await mockApprovals(page, [smallRefund]);
  await page.goto('/#approvals');
  const card = page.getByRole('article', { name: 'Approval for commerce.own_store.refund_order' });
  await expect(card.getByRole('region', { name: 'Who can approve' })).toContainText('Owners or user_local');
  await expect(card.getByText('Refunds up to AED 250: AED 200 is within AED 250')).toBeVisible();
  await card.getByRole('button', { name: 'Approve' }).click();
  await expect.poll(() => decisions).toEqual([{ path: '/api/v1/approvals/approval_small/approve', body: { payload_hash: hash } }]);
});

test('a two-step commitment shows the finance review done and the owner step next, and an opted-out recipient', async ({ page }) => {
  await mockApprovals(page, [commitment]);
  await page.goto('/#approvals');
  const card = page.getByRole('article', { name: 'Approval for commerce.own_store.purchase_stock' });
  const who = card.getByRole('region', { name: 'Who can approve' });
  await expect(who).toContainText('Step 2 of 2');
  await expect(who.getByRole('listitem').filter({ hasText: 'Step 1 of 2' })).toContainText('Approved by finance_reviewer');
  await expect(who.getByRole('listitem').filter({ hasText: 'Step 2 of 2' })).toContainText('Waiting');
  await expect(card.getByText(/Opted out: \+971•••4567 asked not to be messaged/)).toBeVisible();
  await expect(card.getByRole('button', { name: 'Approve' })).toBeEnabled();
});

test('the routing explanation reads in Arabic', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('anum.locale', 'ar'));
  await mockApprovals(page, [largeRefund]);
  await page.goto('/#approvals');
  await expect(page.getByRole('region', { name: 'من يمكنه الموافقة' })).toContainText('المالكون فقط');
  await expect(page.getByText(/300 درهم أكثر من 250 درهم/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');
});

// ---------------------------------------------------------------- Governance: managing the rules

const legacyRule = { id: 'rule_legacy', tenant_id: 'tenant_local', name: 'Publishing', action_pattern: 'marketing.schedule_post', minimum_approvers: 1, required_roles: ['owner'], enabled: true, created_at: now, conditions: { currency: 'AED', disputed: 'any' }, approver_steps: [] };
const bandRule = { id: 'rule_small', tenant_id: 'tenant_local', name: 'Refunds up to AED 250', action_pattern: '*refund*', minimum_approvers: 1, required_roles: ['owner'], enabled: true, created_at: now, conditions: { currency: 'AED', disputed: 'no', amount_at_most: 250 }, approver_steps: [{ approvers: ['owner', 'user:care_lead'] }] };

async function mockGovernance(page: Page, canManage: boolean) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  let rules: Json[] = [legacyRule, bandRule];
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals' || path === '/api/v1/policy-packs' || path === '/api/v1/role-templates') return json([]);
    if (path === '/api/v1/organization/governance') return json({ tenant_id: 'tenant_local', policy_packs: 0, active_policy_packs: 0, role_templates: 0, approval_rules: rules.length, memory_governance_configured: false, can_manage_organization_policy: canManage });
    if (path === '/api/v1/organization/approval-rules' && method === 'GET') return json(rules);
    if (path.startsWith('/api/v1/organization/approval-rules')) {
      const body = method === 'DELETE' ? null : request.postDataJSON() as Json;
      calls.push({ method, path, body });
      if (method === 'POST') { const saved = { ...body, id: 'rule_new', tenant_id: 'tenant_local', created_at: now }; rules = [...rules, saved]; return json(saved, 201); }
      const id = path.split('/').pop();
      if (method === 'PUT') { const saved = { ...rules.find((r) => r.id === id), ...body }; rules = rules.map((r) => (r.id === id ? saved : r)); return json(saved); }
      if (method === 'DELETE') { rules = rules.filter((r) => r.id !== id); return route.fulfill({ status: 204 }); }
    }
    return route.abort();
  });
  return calls;
}

test('an organization administrator adds a two-step rule with an AED band', async ({ page }) => {
  const calls = await mockGovernance(page, true);
  await page.goto('/#governance');
  const rules = page.getByRole('region', { name: 'Approval rules' });
  await expect(rules.getByText('Up to AED 250 · Not disputed')).toBeVisible();
  await expect(rules.getByText('Owners or care_lead')).toBeVisible();
  await rules.getByRole('button', { name: 'Add approval rule' }).click();
  const form = page.getByRole('form', { name: 'Approval rule form' });
  await form.getByLabel('Rule name').fill('Commitments above AED 5,000');
  await form.getByLabel('Actions it applies to').fill('*.purchase_*');
  await form.getByLabel('Applies above (AED)').fill('5,000');
  await form.getByLabel('Applies up to and including (AED)').fill('1,000');
  await form.getByRole('button', { name: 'Save rule' }).click();
  await expect(form.getByText('The "up to" amount must be larger than the "above" amount.')).toBeVisible();
  await form.getByLabel('Applies up to and including (AED)').fill('');
  await form.getByRole('group', { name: 'Who approves' }).getByLabel('Workspace owners').uncheck();
  await form.getByRole('group', { name: 'Who approves' }).getByLabel('Named people (user ids, comma separated)').fill('finance_reviewer');
  await form.getByLabel('Needs a second approval, by a different person, after the first').check();
  await form.getByRole('button', { name: 'Save rule' }).click();
  await expect(page.getByText('Approval rule Commitments above AED 5,000 created.')).toBeVisible();
  expect(calls[0]).toEqual({ method: 'POST', path: '/api/v1/organization/approval-rules', body: {
    name: 'Commitments above AED 5,000', action_pattern: '*.purchase_*', minimum_approvers: 1, required_roles: ['owner'], enabled: true,
    conditions: { currency: 'AED', disputed: 'any', amount_above: 5000 }, approver_steps: [{ approvers: ['user:finance_reviewer'] }, { approvers: ['owner'] }],
  } });
  await expect(rules.getByText('Step 1: finance_reviewer')).toBeVisible();
});

test('the Approvals page opens the approval rules', async ({ page }) => {
  await mockGovernance(page, false);
  await page.goto('/#approvals');
  await expect(page.getByText('Who approves what, by AED amount, is set in the approval rules.')).toBeVisible();
  await page.getByRole('button', { name: 'Open approval rules' }).click();
  await expect(page).toHaveURL(/#governance$/);
  await expect(page.getByRole('region', { name: 'Approval rules' }).getByText('Refunds up to AED 250')).toBeVisible();
});

test('an organization administrator edits, turns off and deletes a rule', async ({ page }) => {
  const calls = await mockGovernance(page, true);
  await page.goto('/#governance');
  await page.getByRole('button', { name: 'Edit approval rule Refunds up to AED 250' }).click();
  const form = page.getByRole('form', { name: 'Approval rule form' });
  await expect(form.getByLabel('Applies up to and including (AED)')).toHaveValue('250');
  await form.getByLabel('Applies up to and including (AED)').fill('300');
  await form.getByRole('button', { name: 'Save rule' }).click();
  await expect(page.getByText('Approval rule Refunds up to AED 250 saved.')).toBeVisible();
  expect(calls[0].method).toBe('PUT');
  expect((calls[0].body as Json).conditions).toEqual({ currency: 'AED', disputed: 'no', amount_at_most: 300 });

  await page.getByRole('button', { name: 'Turn off approval rule Publishing' }).click();
  await expect(page.getByText('Approval rule Publishing is off.')).toBeVisible();
  expect(calls[1].body).toMatchObject({ enabled: false, approver_steps: [], action_pattern: 'marketing.schedule_post' });

  await page.getByRole('button', { name: 'Delete approval rule Publishing' }).click();
  const dialog = page.getByRole('alertdialog', { name: 'Delete the approval rule Publishing?' });
  await dialog.getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByText('Approval rule Publishing deleted.')).toBeVisible();
  expect(calls[2]).toEqual({ method: 'DELETE', path: '/api/v1/organization/approval-rules/rule_legacy', body: null });
});

test('anyone else reads the rules with their bands but cannot change them', async ({ page }) => {
  const calls = await mockGovernance(page, false);
  await page.goto('/#governance');
  await expect(page.getByText('Refunds up to AED 250')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Add approval rule' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Edit approval rule/ })).toHaveCount(0);
  expect(calls).toEqual([]);
});

for (const size of [
  { width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 },
]) {
  const overflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

  test(`approval routing cards fit at ${size.width}x${size.height}`, async ({ page }, testInfo) => {
    await page.setViewportSize(size);
    await mockApprovals(page, [commitment, largeRefund]);
    await page.goto('/#approvals');
    await expect(page.getByRole('region', { name: 'Who can approve' }).first()).toBeVisible();
    expect(await overflow(page)).toBeLessThanOrEqual(0);
    for (const button of await page.getByRole('button', { name: 'Approve' }).all()) {
      const box = (await button.boundingBox())!;
      expect(box.x + box.width).toBeLessThanOrEqual(size.width);
    }
    if (process.env.ANUM_SCREENSHOTS) await page.screenshot({ path: testInfo.outputPath(`routing-${size.width}.png`), fullPage: true });
  });

  test(`the approval rule editor fits at ${size.width}x${size.height}`, async ({ page }, testInfo) => {
    await page.setViewportSize(size);
    await mockGovernance(page, true);
    await page.goto('/#governance');
    await page.getByRole('button', { name: 'Add approval rule' }).click();
    await page.getByLabel('Needs a second approval, by a different person, after the first').check();
    await expect(page.getByRole('form', { name: 'Approval rule form' })).toBeVisible();
    expect(await overflow(page)).toBeLessThanOrEqual(0);
    for (const name of ['Save rule', 'Edit approval rule Refunds up to AED 250', 'Delete approval rule Refunds up to AED 250']) {
      const box = (await page.getByRole('button', { name }).boundingBox())!;
      expect(box.x, name).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width, name).toBeLessThanOrEqual(size.width);
    }
    if (process.env.ANUM_SCREENSHOTS) await page.screenshot({ path: testInfo.outputPath(`rules-${size.width}.png`), fullPage: true });
  });
}
