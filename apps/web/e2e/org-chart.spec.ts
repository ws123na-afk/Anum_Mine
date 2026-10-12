import { readFile } from 'node:fs/promises';
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mockOrgChart, type OrgMockOptions } from './support/orgChartApi';
import { useSavedLocale } from './support/workspaceApi';

// The AI team org chart on the Agents page (docs/org-chart.md) against a mocked API: the tree with
// live status, counts, skills and tools; the member drawer with the full profile and its actions;
// the list view with filters; export image and print; empty, error, denied and partial states;
// Arabic; and phones.
async function openChart(page: Page, options: OrgMockOptions = {}) {
  const api = await mockOrgChart(page, options);
  await page.addInitScript(() => { try { localStorage.setItem('anum.agents.tab', 'org'); localStorage.removeItem('anum.orgChart.layout'); } catch { /* storage blocked */ } });
  await page.goto('/#agents');
  await expect(page.getByRole('heading', { name: /Your AI team|فريقك/ })).toBeVisible({ timeout: 15_000 });
  return api;
}

/** Phones start with departments folded; open them all. */
async function expandAll(page: Page) {
  await expect(page.locator('.orgDeptHead button').first()).toBeVisible();
  const folded = page.locator('.orgDeptHead button[aria-expanded="false"]');
  while (await folded.count() > 0) await folded.first().click();
}

test('the chart shows the owner, ANUM, KPIs, departments and every agent with live work and tools', async ({ page }) => {
  await openChart(page);
  await expandAll(page);
  const tree = page.getByRole('group', { name: 'AI team org chart' });
  await expect(tree.getByRole('heading', { name: 'Sara Haddad' })).toBeVisible();
  await expect(tree.getByRole('heading', { name: /^ANUM/ })).toBeVisible();
  await expect(tree.getByText('Chief of Staff, one center')).toBeVisible();
  const strip = page.locator('.orgStrip');
  await expect(strip.getByText('You approve risky actions.')).toBeVisible();
  await expect(strip.getByText('3 waiting for you')).toBeVisible();
  const kpi = (label: string) => tree.locator('.orgKpi', { has: page.getByText(label, { exact: true }) }).locator('dd');
  await expect(kpi('Agents')).toHaveText('12');
  await expect(kpi('Running now')).toHaveText('2');
  await expect(kpi('Waiting for approval')).toHaveText('3');
  await expect(kpi('Failed today')).toHaveText('1');
  await expect(kpi('Connections not working')).toHaveText('3');
  await expect(kpi('Tasks without an agent')).toHaveText('2');
  for (const name of ['Sales', 'Operations and warehouse', 'Support', 'Finance', 'Marketing & Content', 'No department yet']) {
    await expect(tree.locator('.orgDeptName', { hasText: new RegExp(`^${name}$`) })).toBeVisible();
  }
  const sales = tree.locator('.orgDept', { has: page.locator('.orgDeptName', { hasText: /^Sales$/ }) });
  await expect(sales.getByText('2 waiting for approval').first()).toBeVisible();
  await expect(sales.getByText('2 agents · 1 assistant')).toBeVisible();
  await expect(sales.locator('.orgLead').getByRole('button', { name: 'Sales desk' })).toBeVisible();
  const outreach = tree.locator('.orgMember', { hasText: 'Outreach Writer' });
  await expect(outreach.getByText('Needs approval')).toBeVisible();
  await expect(outreach.locator('.orgMeters')).toContainText('2 waiting');
  await expect(outreach.getByText('Brand voice')).toBeVisible();
  await expect(outreach.getByRole('button', { name: 'Email: Connected. Open Integrations' })).toBeVisible();
  const fleet = tree.locator('.orgMember', { hasText: 'Fleet Planner' });
  await expect(fleet.getByText('Running', { exact: true })).toBeVisible();
  await expect(fleet.getByRole('button', { name: 'Fleet and tyre data: Connected. Open Integrations' })).toBeVisible();
  const care = tree.locator('.orgMember', { hasText: 'WhatsApp Concierge' });
  await expect(care.getByText('Needs attention')).toBeVisible();
  await expect(care.locator('.orgMeters')).toContainText('1 failed today');
  await expect(care.getByRole('button', { name: 'WhatsApp Business: Connection failing. Open Integrations' })).toBeVisible();
  await expect(tree.locator('.orgDept', { hasText: 'Support' }).locator('.orgHealth')).toHaveText('1 needs attention');
  // Department assistants and people sit in their departments as compact rows.
  await expect(tree.locator('.orgCompact li', { hasText: 'Sales desk' }).getByText('Department assistant · Suggest, a person approves')).toBeVisible();
  await expect(tree.locator('.orgCompact li', { hasText: 'Aisha Rahman' }).getByText('Fleet dispatcher · member')).toBeVisible();
  // Five marketing agents collapse to four with "Show all".
  const marketing = tree.locator('.orgDept', { hasText: 'Marketing & Content' });
  await expect(marketing.locator('.orgMember')).toHaveCount(4);
  await marketing.getByRole('button', { name: 'Show all 5' }).click();
  await expect(marketing.locator('.orgMember')).toHaveCount(5);
  await marketing.getByRole('button', { name: /^Marketing & Content/ }).click();
  await expect(marketing.locator('.orgMember').first()).toBeHidden();
});

test('a quick look opens a drawer with the profile and actions; Escape closes it', async ({ page }) => {
  const api = await openChart(page);
  await expandAll(page);
  const peek = page.getByRole('button', { name: 'Quick look at Fleet Planner' });
  await peek.click();
  const drawer = page.getByRole('dialog', { name: 'Fleet Planner' });
  await expect(drawer.getByRole('heading', { name: 'What it does' })).toBeVisible();
  await expect(drawer.getByText('Plans tyre rotations and the monthly purchase order.')).toBeVisible();
  await expect(drawer.getByRole('heading', { name: 'Skills (1)' })).toBeVisible();
  await expect(drawer.getByText('Changes in ANUM: Ask first')).toBeVisible();
  await expect(drawer.getByText('Plan October tyre rotation')).toBeVisible();
  await expect(drawer.getByText(/Buy 24 tyres/)).toBeVisible();
  await drawer.getByLabel('What should Fleet Planner do?').fill('Check tyres due in November');
  await drawer.getByRole('button', { name: 'Run task' }).click();
  await expect(drawer.getByText('Rotation booked for trucks 3 and 12 on Tuesday.')).toBeVisible();
  expect(api.calls.some((c) => c.method === 'POST' && c.path === '/api/v1/tasks' && (c.body as { agent_id?: string }).agent_id === 'agent_fleet')).toBe(true);
  await drawer.getByRole('button', { name: 'Pause' }).click();
  await expect(drawer.getByText('Fleet Planner is paused. It runs no tasks until it is resumed.')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(drawer).toBeHidden();
  await expect(peek).toBeFocused();
  await page.getByRole('button', { name: 'Quick look at WhatsApp Concierge' }).click();
  const care = page.getByRole('dialog', { name: 'WhatsApp Concierge' });
  await expect(care.getByText('WhatsApp refused the message: the access token has expired.')).toBeVisible();
  await care.getByRole('button', { name: 'Open full profile' }).click();
  await expect(page).toHaveURL(/#agents\/team\/agent\/agent_care$/);
  await expect(page.getByRole('heading', { name: 'WhatsApp Concierge', level: 2 })).toBeVisible();
});

test('an agent profile page shows every section in depth, is linkable, and Back returns to the chart', async ({ page }) => {
  await openChart(page);
  await expandAll(page);
  await page.locator('.orgMember', { hasText: 'Fleet Planner' }).getByRole('button', { name: 'Fleet Planner', exact: true }).click();
  await expect(page).toHaveURL(/#agents\/team\/agent\/agent_fleet$/);
  const hero = page.locator('.orgHero');
  await expect(hero.getByRole('heading', { name: 'Fleet Planner', level: 2 })).toBeVisible();
  await expect(hero.getByText('Department: Operations and warehouse')).toBeVisible();
  // Overview: full instructions, details, approval rules per kind of action, workspace policy, budget gap.
  await expect(page.getByText('Keep answers short and cite the source.')).toBeVisible();
  const rules = page.getByRole('table', { name: 'Alone or with your approval' });
  await expect(rules.getByRole('row', { name: /Reading/ })).toContainText('Automatic');
  await expect(rules.getByRole('row', { name: /Changes in ANUM/ })).toContainText('Ask first');
  await expect(rules.getByRole('row', { name: /Changes in ANUM/ })).toContainText('This workspace asks for approval anyway.');
  await expect(page.getByText('Workspace rule: medium-risk actions also wait for approval.')).toBeVisible();
  await expect(page.getByText(/measured for the whole workspace, not per agent/)).toBeVisible();
  const tabs = page.getByRole('navigation', { name: 'Profile sections' });
  // Work: running, waiting, failed and all tasks, and assigning one.
  await tabs.getByRole('button', { name: 'Work' }).click();
  await expect(page).toHaveURL(/#agents\/team\/agent\/agent_fleet\/work$/);
  await expect(page.locator('.orgPanel', { hasText: 'Running now' }).getByText('Plan October tyre rotation')).toBeVisible();
  await expect(page.getByText('Nothing is waiting for approval.')).toBeVisible();
  await expect(page.locator('.orgPanel', { hasText: 'All tasks, newest first' }).getByText('September purchase plan')).toBeVisible();
  await page.getByLabel('What should Fleet Planner do?').fill('Plan November');
  await page.getByRole('button', { name: 'Run task' }).click();
  await expect(page.getByText('Rotation booked for trucks 3 and 12 on Tuesday.')).toBeVisible();
  // Skills and tools: each skill's tools in order, each tool's connection and fix.
  await tabs.getByRole('button', { name: 'Skills and tools' }).click();
  const skill = page.locator('.orgSkillList li', { hasText: 'Tyre planning' });
  await expect(skill.getByText('Ready')).toBeVisible();
  await expect(skill.getByRole('list', { name: 'Tools this skill uses, in order' }).getByRole('listitem')).toHaveCount(2);
  await expect(page.locator('.orgToolList li', { hasText: 'tyre_fleet_summary' }).getByText('Nothing to do')).toBeVisible();
  // Performance from real task counts, and what is not measured.
  await tabs.getByRole('button', { name: 'Performance' }).click();
  const month = page.locator('.orgPanel', { hasText: 'Last 30 days' });
  // 11 older plus the September plan and the task just assigned; one failure.
  await expect(month.locator('.orgKpi', { hasText: 'Completed' }).locator('dd').first()).toHaveText('13');
  await expect(month.locator('.orgKpi', { hasText: 'Success rate' }).locator('dd').first()).toHaveText('93%');
  await expect(month.locator('.orgKpi', { hasText: 'Average time to finish' }).locator('dd').first()).toHaveText(/^\d+(\.\d)? (hr|min)$/);
  await expect(page.getByText(/does not record cost, tokens, time saved/)).toBeVisible();
  // A reload keeps the profile and tab; Back returns to the chart.
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Fleet Planner', level: 2 })).toBeVisible();
  await expect(tabs.getByRole('button', { name: 'Performance' })).toHaveAttribute('aria-current', 'page');
  await page.getByRole('button', { name: 'AI team' }).click();
  await expect(page.getByRole('group', { name: 'AI team org chart' })).toBeVisible();
});

test('approvals and activity tabs show the agent’s own approvals, runs and last error', async ({ page }) => {
  await mockOrgChart(page);
  await page.goto('/#agents/team/agent/agent_outreach/approvals');
  const table = page.getByRole('table', { name: 'Approvals' });
  await expect(table.locator('tbody tr')).toHaveCount(2, { timeout: 15_000 });
  await expect(table.getByText('Email Al Noor Transport about the pilot')).toBeVisible();
  await expect(table.getByText('Waiting').first()).toBeVisible();
  await page.goto('/#agents/team/agent/agent_care/activity');
  await page.reload();
  await expect(page.locator('.orgPanel', { hasText: 'Last error' }).getByText('WhatsApp refused the message: the access token has expired.')).toBeVisible();
  await expect(page.getByText(/It allows acme.old-faq/).first()).toBeVisible();
  await expect(page.locator('.orgTimeline li', { hasText: 'Reply to a customer about delivery times' })).toBeVisible();
});

test('people and department assistants have profiles with their work and workload', async ({ page }) => {
  await mockOrgChart(page);
  await page.goto('/#agents/team/person/user_aisha');
  await expect(page.getByRole('heading', { name: 'Aisha Rahman', level: 2 })).toBeVisible();
  const workload = page.locator('.orgPanel', { hasText: 'Workload' });
  await expect(workload.locator('.orgKpi', { hasText: 'Open' }).locator('dd').first()).toHaveText('3');
  await expect(workload.locator('.orgKpi', { hasText: 'Load' }).locator('dd').first()).toHaveText('75%');
  await page.getByRole('navigation', { name: 'Profile sections' }).getByRole('button', { name: 'Work' }).click();
  await expect(page.getByText('Book tyre fitting for truck 7')).toBeVisible();
  await page.goto('/#agents/team/assistant/asst_sales');
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Sales desk', level: 2 })).toBeVisible();
  await expect(page.getByText('Answer price questions and draft quotes.')).toBeVisible();
  await page.getByRole('navigation', { name: 'Profile sections' }).getByRole('button', { name: 'Work' }).click();
  await expect(page.getByText('Quote for Desert Logistics')).toBeVisible();
  await page.goto('/#agents/team/agent/agent_gone');
  await page.reload();
  await expect(page.getByRole('heading', { name: 'This member is not in your AI team' })).toBeVisible();
});

test('a tool opens Integrations and the approvals strip opens Approvals', async ({ page }) => {
  await openChart(page);
  await page.locator('.orgStrip').getByRole('button', { name: 'Open approvals' }).click();
  await expect(page).toHaveURL(/#approvals$/);
  await page.goto('/#agents');
  await page.reload();
  await expandAll(page);
  await page.locator('.orgMember', { hasText: 'Lead Finder' }).getByRole('button', { name: /^Private web search/ }).click();
  await expect(page).toHaveURL(/#integrations$/);
});

test('the list view shows every member and filters by text, status, department and tool', async ({ page }) => {
  await openChart(page);
  await page.getByRole('button', { name: 'List', exact: true }).click();
  const table = page.getByRole('table', { name: /Every agent/ });
  await expect(table.locator('tbody tr')).toHaveCount(12 + 2 + 2);
  await expect(page.getByText('Showing 16 of 16')).toBeVisible();
  await page.getByLabel('Status', { exact: true }).selectOption({ label: 'Needs approval' });
  await expect(table.locator('tbody tr')).toHaveCount(1);
  await expect(table.getByText('Outreach Writer')).toBeVisible();
  await page.getByLabel('Status', { exact: true }).selectOption({ label: 'All statuses' });
  await page.getByLabel('Tool', { exact: true }).selectOption({ label: 'Social media' });
  await expect(page.getByText('Showing 4 of 16')).toBeVisible();
  await page.getByLabel('Tool', { exact: true }).selectOption({ label: 'All tools' });
  await page.getByLabel('Department', { exact: true }).selectOption({ label: 'Operations and warehouse' });
  await expect(page.getByText('Showing 3 of 16')).toBeVisible();
  await page.getByLabel('Search the team').fill('nobody like this');
  await expect(page.getByRole('heading', { name: 'No one matches these filters' })).toBeVisible();
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await expect(page.getByText('Showing 16 of 16')).toBeVisible();
  await table.getByRole('button', { name: 'Invoice Checker' }).click();
  await expect(page.getByRole('heading', { name: 'Invoice Checker', level: 2 })).toBeVisible();
});

test('export image downloads a PNG of the whole team, and print shows every profile', async ({ page }, info) => {
  await openChart(page);
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export image' }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^anum-ai-team-\d{4}-\d{2}-\d{2}\.png$/);
  const bytes = await readFile((await file.path())!);
  expect([...bytes.subarray(0, 8)]).toEqual([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  expect(bytes.length).toBeGreaterThan(20_000);
  if (process.env.ORG_CHART_EXPORT_DIR && info.project.name === 'desktop') await file.saveAs(`${process.env.ORG_CHART_EXPORT_DIR}/exported-org-chart.png`);
  await expect(page.getByText(/^Saved anum-ai-team-/)).toBeVisible();
  await page.emulateMedia({ media: 'print' });
  await expect(page.locator('.orgPrintRoot')).toBeVisible();
  await expect(page.locator('.orgPrintRoot').getByText('Social Posts · Idle')).toBeVisible();
  await expect(page.locator('.orgPrintRoot').getByText('Keep answers short and cite the source.').first()).toBeVisible();
  await expect(page.getByRole('group', { name: 'AI team org chart' })).toBeHidden();
});

test('no agents yet offers to create one', async ({ page }) => {
  await openChart(page, { empty: true });
  await expect(page.getByRole('heading', { name: 'No AI team yet' })).toBeVisible();
  await page.getByRole('button', { name: 'Create an agent' }).click();
  await expect(page.getByRole('heading', { name: 'Create an agent' })).toBeVisible();
});

test('a failure says what failed and retries', async ({ page }) => {
  await openChart(page, { listFails: true });
  await expect(page.getByText(/The org chart could not load: The database is not reachable./)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('a denied caller learns who can help', async ({ page }) => {
  await openChart(page, { denied: true });
  await expect(page.getByRole('heading', { name: 'You cannot see this workspace’s agents' })).toBeVisible();
});

test('missing connection status and approvals are said once and the chart still shows', async ({ page }) => {
  await openChart(page, { setupFails: true, approvalsDenied: true, role: 'member' });
  await expect(page.getByText('Some details could not be read, so they are missing: approvals, connection status. The rest is live.')).toBeVisible();
  await expect(page.locator('.orgStrip').getByText('Owners and approvers see what is waiting.')).toBeVisible();
  await expandAll(page);
  await expect(page.locator('.orgMember', { hasText: 'Outreach Writer' }).getByRole('button', { name: 'Email: Status unknown. Open Integrations' })).toBeVisible();
  await expect(page.getByText('Owner · You are viewing as member')).toBeVisible();
});

test('Arabic: the chart reads right to left', async ({ page }) => {
  await useSavedLocale(page, 'ar');
  await openChart(page);
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { name: 'فريقك من الذكاء الاصطناعي' })).toBeVisible();
  await expect(page.getByText('رئيس الأركان، مركز واحد').first()).toBeVisible();
  await expandAll(page);
  await expect(page.locator('.orgMember', { hasText: 'Outreach Writer' }).getByText('ينتظر موافقة')).toBeVisible();
});

test('phones get a vertical tree with collapsible departments and no sideways scroll', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openChart(page);
  const sales = page.locator('.orgDeptHead button', { hasText: /^Sales/ });
  await expect(sales).toHaveAttribute('aria-expanded', 'false');
  await sales.click();
  await expect(page.locator('.orgMember', { hasText: 'Outreach Writer' })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

for (const theme of ['dark', 'light'] as const) {
  test(`chart, list and drawer have no serious axe violations (${theme})`, async ({ page }) => {
    test.setTimeout(120_000); // axe over the chart, list, drawer and six profile tabs
    if (theme === 'light') await page.addInitScript(() => { try { localStorage.setItem('anum.theme', 'light'); } catch { /* storage blocked */ } });
    await openChart(page);
    await expandAll(page);
    const serious = async () => (await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze()).violations
      .filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(', ')}`);
    expect(await serious()).toEqual([]);
    await page.getByRole('button', { name: 'Quick look at Outreach Writer' }).click();
    await expect(page.getByRole('dialog', { name: 'Outreach Writer' }).getByRole('heading', { name: 'Current and recent tasks' })).toBeVisible();
    expect(await serious()).toEqual([]);
    await page.keyboard.press('Escape');
    await page.locator('.orgMember', { hasText: 'Outreach Writer' }).getByRole('button', { name: 'Outreach Writer', exact: true }).click();
    for (const tab of ['Overview', 'Work', 'Skills and tools', 'Approvals', 'Activity', 'Performance']) {
      await page.getByRole('navigation', { name: 'Profile sections' }).getByRole('button', { name: tab }).click();
      await expect(page.locator('.orgPanel').first()).toBeVisible();
      await expect(page.locator('.orgPanels').getByText('Loading...')).toHaveCount(0, { timeout: 15_000 });
      expect(await serious(), tab).toEqual([]);
    }
    await page.getByRole('button', { name: 'AI team' }).click();
    await page.getByRole('button', { name: 'List', exact: true }).click();
    expect(await serious()).toEqual([]);
  });
}
