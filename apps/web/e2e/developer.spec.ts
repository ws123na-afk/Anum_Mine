import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { useSavedLocale } from './support/workspaceApi';

// Developer mode (docs/developer-mode.md): owners add projects (GitHub repositories), test them and
// install the workflow, ask for a change in a project, follow it to a pull request and approve the
// merge with the payload hash shown; chat requests resolve the project and always confirm it. Everyone
// else sees who can use it.
const now = '2026-10-11T09:00:00.000Z';
const later = new Date(Date.now() + 6 * 3600_000).toISOString();
const hash = '7c'.repeat(32);
const stages = ['queued', 'coding', 'pr_opened', 'ci', 'approval', 'merged'];
const labels: Record<string, string> = { queued: 'Queued', coding: 'Coding', pr_opened: 'Pull request opened', ci: 'CI checks', approval: 'Waiting for approval', merged: 'Merged' };
const timeline = (active: string, failed = false) => stages.map((id, index) => ({
  id, label: labels[id],
  state: active === 'all' || index < stages.indexOf(active) ? 'done' : index === stages.indexOf(active) ? (failed ? 'failed' : 'active') : 'pending',
}));

const setupReady = {
  configured: true, enabled_here: true, repository: null, workflow: 'anum-developer.yml', base_branch: 'main', missing: [], message: 'Ready',
  max_requests_per_day: 10, requests_today: 0, projects: 0, ready_projects: 0, max_projects: 100, environment_token_configured: false,
};

type Project = Record<string, unknown> & { id: string; name: string; repository: string };

function makeProject(id: string, name: string, repository: string, extra: Record<string, unknown> = {}): Project {
  const [owner, repo] = repository.split('/');
  return {
    id, name, github_owner: owner, github_repo: repo, repository, default_branch: 'main', description: '', enabled: true, protected_paths: [],
    workflow_file: 'anum-developer.yml', token_source: 'project', token_configured: true, token_updated_at: now, last_check: null,
    install_pr_number: null, install_pr_url: null, ready: true, problems: [], created_by: 'user_local', created_at: now, updated_at: now, ...extra,
  };
}

const anum = () => makeProject('devproj_anum', 'ANUM', 'acme/anum', { description: 'The ANUM agent platform' });
const tyre = () => makeProject('devproj_tyre', 'TyrePulse', 'acme/tyrepulse', { description: 'Tyre pressure monitoring for fleets' });

function devRequest(id: string, text: string, status: string, project: Project, extra: Record<string, unknown> = {}) {
  return {
    id, created_by: 'user_local', text, source: 'text', status, allow_protected_paths: false, branch: `anum-dev/${id}`,
    project_id: project.id, project_name: project.name, repository: project.repository,
    run_id: null, run_url: null, pr_number: null, pr_url: null, pr_title: null, pr_draft: null, head_sha: null, checks: null,
    failure_reason: null, merge_error: null, approval_id: null, merge_commit_sha: null, created_at: now, updated_at: now,
    merged_at: null, synced_at: null, timeline: timeline(status === 'queued' ? 'queued' : 'coding'), summary: 'Queued: waiting for the developer workflow to start.', approval: null, ...extra,
  };
}

const approval = {
  id: 'approval_dev', task_id: 'task_merge', action: 'developer.merge_pull_request', risk_level: 'high', status: 'pending',
  reason: 'External or high-impact actions require explicit approval. Proposed action: Squash-merge pull request #7',
  created_at: now, run_id: 'run_merge', step_id: 'step_merge', payload_hash: hash, expires_at: later, target: 'api.github.com',
  arguments: { request_id: 'dev_one', project_id: 'devproj_anum', repository: 'acme/anum', pull_number: 7, head_sha: 'a'.repeat(40), base: 'main', merge_method: 'squash', title: 'Add a dark/light toggle to settings' },
};

const ref = (project: Project) => ({ id: project.id, name: project.name, repository: project.repository, description: String(project.description) });

async function mockApi(page: Page, { role = 'owner', setup = setupReady, projects: initial = [anum()] }: { role?: string; setup?: Record<string, unknown>; projects?: Project[] } = {}) {
  const sent: { creates: Record<string, unknown>[]; decisions: unknown[]; asks: number; projectWrites: Record<string, unknown>[]; resolves: string[]; deletes: string[] } = {
    creates: [], decisions: [], asks: 0, projectWrites: [], resolves: [], deletes: [],
  };
  let items: Record<string, unknown>[] = [];
  let projects = [...initial];
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const error = (status: number, message: string) => json({ error: { code: 'conflict', message, correlation_id: 'c' } }, status);
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/auth/local/session') return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role, active: true });
    if (path === '/api/v1/tasks' || path === '/api/v1/approvals' || path === '/api/v1/conversations') return json([]);
    if (path.startsWith('/api/v1/developer/') && role !== 'owner') return json({ error: { code: 'forbidden', message: 'Workspace permission denied', correlation_id: 'c' } }, 403);
    if (path === '/api/v1/developer/setup') return json({ ...setup, projects: projects.length, ready_projects: projects.filter((p) => p.ready).length });
    if (path === '/api/v1/developer/projects' && method === 'GET') return json(projects);
    if (path === '/api/v1/developer/projects' && method === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>;
      sent.projectWrites.push(body);
      if (projects.some((p) => p.name.toLowerCase() === String(body.name).toLowerCase())) return error(409, 'Another project already has this name. Choose another name.');
      const created = makeProject(`devproj_${String(body.github_repo)}`, String(body.name), `${body.github_owner}/${body.github_repo}`, {
        description: body.description, protected_paths: body.protected_paths, default_branch: body.default_branch, token_configured: Boolean(body.token),
        ready: Boolean(body.token), problems: body.token ? [] : ['No GitHub token: add a fine-grained token for this repository.'],
      });
      projects = [...projects, created].sort((a, b) => a.name.localeCompare(b.name));
      return json(created, 201);
    }
    const projectMatch = /^\/api\/v1\/developer\/projects\/([^/]+)(?:\/(test|install-workflow))?$/.exec(path);
    if (projectMatch) {
      const project = projects.find((p) => p.id === projectMatch[1]);
      if (!project) return error(404, 'Project not found');
      if (projectMatch[2] === 'test') {
        const tested = { ...project, last_check: { ok: false, checked_at: now, repository_reachable: true, default_branch_exists: true, workflow_present: false, actions_readable: false, github_default_branch: 'main', message: `.github/workflows/anum-developer.yml is not on main yet. Use Install workflow, merge that pull request in GitHub, then test again.` }, ready: false, problems: ['.github/workflows/anum-developer.yml is not on main yet.'] };
        projects = projects.map((p) => p.id === project.id ? tested : p);
        return json(tested);
      }
      if (projectMatch[2] === 'install-workflow') {
        const installed = { ...project, install_pr_number: 51, install_pr_url: `https://github.com/${project.repository}/pull/51` };
        projects = projects.map((p) => p.id === project.id ? installed : p);
        return json({ project: installed, pull_request_number: 51, pull_request_url: installed.install_pr_url, created: true });
      }
      if (method === 'PATCH') {
        const body = request.postDataJSON() as Record<string, unknown>;
        sent.projectWrites.push(body);
        const updated = { ...project, ...body, ...(body.enabled === false ? { ready: false, problems: ['The project is turned off. Turn it on to send requests to it.'] } : {}), ...(body.enabled === true ? { ready: true, problems: [] } : {}) };
        projects = projects.map((p) => p.id === project.id ? updated : p);
        return json(updated);
      }
      if (method === 'DELETE') {
        sent.deletes.push(project.id);
        projects = projects.filter((p) => p.id !== project.id);
        return route.fulfill({ status: 204 });
      }
      return json(project);
    }
    if (path === '/api/v1/developer/workflow-template') return json({ file_name: 'anum-developer.yml', path: '.github/workflows/anum-developer.yml', content: 'name: ANUM developer\n', secrets: [], variables: [] });
    if (path === '/api/v1/developer/resolve') {
      const text = String((request.postDataJSON() as { text: string }).text);
      sent.resolves.push(text);
      const named = projects.find((p) => text.toLowerCase().endsWith(` in ${p.name.toLowerCase()}`));
      if (named) return json({ status: 'resolved', text: text.slice(0, -(` in ${named.name}`.length)), project: ref(named), candidates: [], matched_by: 'name' });
      if (projects.length === 1) return json({ status: 'resolved', text, project: ref(projects[0]), candidates: [], matched_by: 'only_project' });
      return json({ status: 'ambiguous', text, project: null, candidates: projects.map(ref), matched_by: null });
    }
    if (path === '/api/v1/developer/requests' && method === 'GET') {
      const only = url.searchParams.get('project_id');
      return json(only ? items.filter((item) => item.project_id === only) : items);
    }
    if (path === '/api/v1/developer/requests' && method === 'POST') {
      const body = request.postDataJSON() as { text: string; source: string; project_id?: string };
      sent.creates.push(body);
      const project = projects.find((p) => p.id === body.project_id) ?? projects[0];
      const created = devRequest('dev_one', body.text, 'queued', project, { source: body.source });
      items = [created];
      return json(created, 201);
    }
    if (path === '/api/v1/developer/requests/dev_one' && method === 'GET') return json(items[0]);
    if (path === '/api/v1/developer/requests/dev_one/merge-approval') {
      items = [{ ...items[0], approval_id: approval.id, approval }];
      return json({ request: items[0], approval });
    }
    if (path === '/api/v1/approvals/approval_dev/approve') {
      sent.decisions.push(request.postDataJSON());
      items = [{ ...items[0], status: 'merged', merge_commit_sha: 'm'.repeat(40), approval: null, timeline: timeline('all'), summary: 'Merged: Add a dark/light toggle to settings.' }];
      return json({ approval: { ...approval, status: 'approved', decided_by: 'user_local', decided_at: now }, task: { id: 'task_merge', title: 'Merge', prompt: 'Merge', status: 'completed', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: now, updated_at: now }, run: { id: 'run_merge', task_id: 'task_merge', status: 'completed', steps: [], result: 'Merged pull request #7 into main.' } });
    }
    if (/\/ask$/.test(path)) sent.asks += 1;
    return route.abort();
  });
  return {
    sent,
    makeReady() {
      items = [devRequest('dev_one', 'Add a dark/light toggle to settings', 'waiting_approval', projects[0], {
        run_id: 1001, run_url: 'https://github.com/acme/anum/actions/runs/1001', pr_number: 7, pr_url: 'https://github.com/acme/anum/pull/7',
        pr_title: 'Add a dark/light toggle to settings', pr_draft: true, head_sha: 'a'.repeat(40), checks: { total: 4, passed: 4, failed: 0, pending: 0, failing: [] },
        timeline: timeline('approval'), summary: 'The change is ready for review: Add a dark/light toggle to settings. Approve to merge.',
      })];
    },
  };
}

const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

async function seriousViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
  return results.violations
    .filter((violation) => violation.impact === 'serious' || violation.impact === 'critical')
    .map((violation) => `${violation.id} (${violation.impact}): ${violation.help}\n  ${violation.nodes.slice(0, 5).map((node) => node.target.join(' ')).join('\n  ')}`);
}

test('an owner asks for a change in a project, follows it and approves the merge with the payload hash', async ({ page }) => {
  const api = await mockApi(page);
  await page.goto('/#developer');
  await expect(page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Developer' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'No developer requests yet' })).toBeVisible();
  // The only ready project is preselected.
  await expect(page.getByLabel('Project')).toHaveValue('devproj_anum');

  await page.getByRole('button', { name: 'Add a dark/light toggle to Settings' }).click();
  await expect(page.getByLabel('What should change?')).toHaveValue('Add a dark/light toggle to Settings');
  await page.getByRole('button', { name: 'Ask the developer' }).click();
  await expect.poll(() => api.sent.creates).toEqual([{ text: 'Add a dark/light toggle to Settings', allow_protected_paths: false, project_id: 'devproj_anum', source: 'text' }]);
  await expect(page.getByText('Sent to the developer for ANUM.', { exact: false })).toBeVisible();
  const card = page.getByRole('article', { name: /Developer request: Add a dark\/light toggle/ });
  await expect(card.getByText('Queued', { exact: true }).first()).toBeVisible();
  await expect(card.getByText('ANUM', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Ask the developer' })).toBeDisabled();
  await expect(page.getByText(/One request at a time per project/)).toBeVisible();

  api.makeReady();
  await card.getByRole('button', { name: 'Check now' }).click();
  await expect(card.getByText('Waiting for approval').first()).toBeVisible();
  await expect(card.getByRole('link', { name: /Pull request #7/ })).toHaveAttribute('href', 'https://github.com/acme/anum/pull/7');
  await expect(card.getByText('4 of 4 checks passed')).toBeVisible();

  await card.getByRole('button', { name: 'Approve & merge' }).click();
  const review = card.getByRole('region', { name: 'Merge approval' });
  await expect(review.getByText('7c7c7c7c7c7c…7c7c7c')).toBeVisible();
  await expect(review.getByText('api.github.com')).toBeVisible();
  await expect(review.getByText('acme/anum', { exact: true })).toBeVisible();
  await review.getByRole('button', { name: 'Confirm merge' }).click();
  await expect.poll(() => api.sent.decisions).toEqual([{ payload_hash: hash }]);
  await expect(page.getByText('Merged pull request #7.')).toBeVisible();
  await expect(card.getByText('Merged', { exact: true }).first()).toBeVisible();
});

test('an owner adds a project, tests it, installs the workflow, turns it off and deletes it', async ({ page }) => {
  const api = await mockApi(page, { projects: [] });
  await page.goto('/#developer');
  await expect(page.getByRole('heading', { name: 'Add a project first' })).toBeVisible();
  await page.getByRole('button', { name: 'Add a project' }).click();
  await expect(page.getByRole('navigation', { name: 'Developer sections' }).getByRole('button', { name: /Projects/ })).toHaveAttribute('aria-current', 'page');

  await page.getByRole('button', { name: 'Add a project' }).first().click();
  const form = page.getByRole('form', { name: 'Add a project' });
  await expect(form.getByLabel('Name')).toBeFocused();
  await form.getByLabel('Name').fill('TyrePulse');
  await form.getByLabel('GitHub repository').fill('not a repository');
  await form.getByRole('button', { name: 'Save project' }).click();
  await expect(form.getByRole('alert')).toHaveText('Enter the repository as owner/repo, for example acme/tyrepulse.');
  await expect(form.getByLabel('GitHub repository')).toHaveAttribute('aria-invalid', 'true');
  expect(api.sent.projectWrites).toEqual([]);

  await form.getByLabel('GitHub repository').fill('https://github.com/acme/tyrepulse');
  await form.getByLabel('Short description').fill('Tyre pressure monitoring for fleets');
  await form.getByLabel('Protected paths (optional)').fill('docs/legal/\nsrc/billing.py');
  await form.getByLabel('Fine-grained token').fill('example-fine-grained-value');
  await form.getByRole('button', { name: 'Save project' }).click();
  await expect.poll(() => api.sent.projectWrites).toEqual([{
    name: 'TyrePulse', github_owner: 'acme', github_repo: 'tyrepulse', default_branch: 'main', description: 'Tyre pressure monitoring for fleets',
    protected_paths: ['docs/legal/', 'src/billing.py'], enabled: true, token_source: 'project', workflow_file: 'anum-developer.yml', token: 'example-fine-grained-value',
  }]);
  await expect(page.getByText(/Added TyrePulse\. Next: Install workflow/)).toBeVisible();
  const card = page.getByRole('article', { name: 'Project TyrePulse' });
  await expect(card.getByText('acme/tyrepulse')).toBeVisible();
  await expect(card.getByText('src/billing.py')).toBeVisible();
  await expect(card.getByText('Not tested', { exact: true })).toBeVisible();
  // The token is never shown back.
  await expect(page.getByText('example-fine-grained-value')).toHaveCount(0);

  await card.getByRole('button', { name: 'Test connection' }).click();
  await expect(card.getByText('Needs attention')).toBeVisible();
  await expect(page.getByRole('status').filter({ hasText: /TyrePulse: .*Install workflow/ })).toBeVisible();
  await card.getByRole('button', { name: 'Install workflow' }).click();
  await expect(page.getByText(/Opened pull request #51 in acme\/tyrepulse/)).toBeVisible();
  await expect(card.getByRole('link', { name: /Workflow pull request #51/ })).toHaveAttribute('href', 'https://github.com/acme/tyrepulse/pull/51');

  await card.getByRole('button', { name: 'Turn off' }).click();
  await expect(card.getByText('Turned off', { exact: true })).toBeVisible();
  page.once('dialog', (dialog) => void dialog.accept());
  await card.getByRole('button', { name: 'Delete' }).click();
  await expect.poll(() => api.sent.deletes).toEqual(['devproj_tyrepulse']);
  await expect(page.getByRole('heading', { name: 'No projects yet' })).toBeVisible();
});

test('a duplicate project name is refused with the reason, and editing keeps the stored token', async ({ page }) => {
  const api = await mockApi(page, { projects: [anum(), tyre()] });
  await page.goto('/#developer');
  await page.getByRole('navigation', { name: 'Developer sections' }).getByRole('button', { name: /Projects/ }).click();
  await page.getByRole('button', { name: 'Add a project' }).click();
  const form = page.getByRole('form', { name: 'Add a project' });
  await form.getByLabel('Name').fill('anum');
  await form.getByLabel('GitHub repository').fill('acme/other');
  await form.getByRole('button', { name: 'Save project' }).click();
  await expect(form.getByRole('alert')).toHaveText('Another project already has this name. Choose another name.');
  await form.getByRole('button', { name: 'Cancel' }).click();

  await page.getByRole('article', { name: 'Project TyrePulse' }).getByRole('button', { name: 'Edit' }).click();
  const edit = page.getByRole('form', { name: 'Edit TyrePulse' });
  await expect(edit.getByText(/A token is stored\. Leave this empty to keep it/)).toBeVisible();
  await edit.getByLabel('Branch to build on').fill('develop');
  await edit.getByRole('button', { name: 'Save project' }).click();
  await expect(page.getByText('Saved TyrePulse.')).toBeVisible();
  const patch = api.sent.projectWrites.at(-1) ?? {};
  expect(patch.default_branch).toBe('develop');
  expect('token' in patch || 'clear_token' in patch).toBe(false);
});

test('non-owners do not see Developer and get an explanation when they open it', async ({ page }) => {
  await mockApi(page, { role: 'member' });
  await page.goto('/#developer');
  await expect(page.getByRole('heading', { name: 'Developer mode is for workspace owners' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Developer' })).toHaveCount(0);
});

test('the setup state names the missing settings', async ({ page }) => {
  await mockApi(page, { setup: { ...setupReady, configured: false, enabled_here: false, missing: ['ANUM_DEVELOPER_TENANT_ID'], message: 'Developer mode needs these API settings: ANUM_DEVELOPER_TENANT_ID.' } });
  await page.goto('/#developer');
  await expect(page.getByRole('heading', { name: 'Set up developer mode' })).toBeVisible();
  await expect(page.getByText('ANUM_DEVELOPER_TENANT_ID', { exact: true })).toBeVisible();
  await expect(page.getByLabel('What should change?')).toHaveCount(0);
  await expect(page.getByRole('navigation', { name: 'Developer sections' })).toHaveCount(0);
});

test('a chat request naming a project is confirmed with that project before it starts', async ({ page }) => {
  const api = await mockApi(page, { projects: [anum(), tyre()] });
  await page.goto('/#assistant');
  const box = page.getByLabel('Message the assistant');
  await box.fill('build a login page in TyrePulse');
  await box.press('Enter');
  const assist = page.getByRole('region', { name: 'Developer' });
  await expect(assist.getByText('I’ll ask the developer to: a login page in TyrePulse. Start?')).toBeVisible();
  await expect(assist.getByText('acme/tyrepulse')).toBeVisible();
  expect(api.sent.creates).toEqual([]);
  await assist.getByRole('button', { name: 'Start' }).click();
  await expect.poll(() => api.sent.creates).toEqual([{ text: 'a login page', allow_protected_paths: false, project_id: 'devproj_tyre', source: 'text' }]);
  await expect(assist.getByText(/Started in TyrePulse\. The developer is on it/)).toBeVisible();
  expect(api.sent.asks).toBe(0);
});

test('an unclear chat request asks which project, then confirms the choice', async ({ page }) => {
  const api = await mockApi(page, { projects: [anum(), tyre()] });
  await page.goto('/#assistant');
  const box = page.getByLabel('Message the assistant');
  await box.fill('build a dark/light toggle in settings');
  await box.press('Enter');
  const assist = page.getByRole('region', { name: 'Developer' });
  await expect(assist.getByText('Which project is this for: ANUM or TyrePulse?')).toBeVisible();
  await assist.getByRole('group', { name: 'Choose the project' }).getByRole('button', { name: 'ANUM' }).click();
  await expect(assist.getByText('I’ll ask the developer to: a dark/light toggle in settings in ANUM. Start?')).toBeVisible();
  // Typing the answer works too: "no" cancels without sending.
  await box.fill('no');
  await box.press('Enter');
  await expect(assist.getByText('Okay, I won’t start it.')).toBeVisible();
  expect(api.sent.creates).toEqual([]);
  expect(api.sent.asks).toBe(0);
});

test.describe('Arabic', () => {
  test('the Developer view and its projects read in Arabic, right to left, without overflow or axe violations', async ({ page }) => {
    const api = await mockApi(page, { projects: [anum(), tyre()] });
    api.makeReady();
    await useSavedLocale(page, 'ar');
    await page.goto('/#developer');
    await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
    await expect(page.getByRole('heading', { name: 'اطلب تغييرًا' })).toBeVisible();
    await expect(page.getByLabel('المشروع')).toBeVisible();
    const card = page.getByRole('article', { name: /طلب مطوّر/ });
    await expect(card.getByText('بانتظار الموافقة').first()).toBeVisible();
    await expect(card.getByText('نجح 4 من 4 فحوص')).toBeVisible();
    expect(await noHorizontalOverflow(page)).toBe(true);
    expect(await seriousViolations(page)).toEqual([]);

    await page.getByRole('navigation', { name: 'أقسام المطوّر' }).getByRole('button', { name: /المشاريع/ }).click();
    const project = page.getByRole('article', { name: /المشروع .?TyrePulse/ });
    await expect(project.getByRole('button', { name: 'اختبار الاتصال' })).toBeVisible();
    await expect(project.getByText('لم يُختبر', { exact: true })).toBeVisible();
    expect(await noHorizontalOverflow(page)).toBe(true);
    expect(await seriousViolations(page)).toEqual([]);
  });

  test('the chat confirmation names the project in Arabic', async ({ page }) => {
    await mockApi(page, { projects: [anum(), tyre()] });
    await useSavedLocale(page, 'ar');
    await page.goto('/#assistant');
    const box = page.getByRole('textbox', { name: 'راسل المساعد' });
    await box.fill('build a login page in TyrePulse');
    await box.press('Enter');
    const assist = page.getByRole('region', { name: 'المطوّر' });
    await expect(assist.getByText(/سأطلب من المطوّر: .*a login page.* في .*TyrePulse.*\. هل أبدأ؟/)).toBeVisible();
    await expect(assist.getByRole('button', { name: 'ابدأ' })).toBeVisible();
  });
});

test('both Developer sections have no serious axe violations in English', async ({ page }) => {
  const api = await mockApi(page, { projects: [anum(), tyre()] });
  api.makeReady();
  await page.goto('/#developer');
  await expect(page.getByRole('article', { name: /Developer request/ })).toBeVisible();
  expect(await seriousViolations(page)).toEqual([]);
  await page.getByRole('navigation', { name: 'Developer sections' }).getByRole('button', { name: /Projects/ }).click();
  await page.getByRole('button', { name: 'Add a project' }).click();
  await expect(page.getByRole('form', { name: 'Add a project' })).toBeVisible();
  expect(await seriousViolations(page)).toEqual([]);
});

for (const size of [{ width: 1440, height: 900 }, { width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 768, height: 1024 }, { width: 390, height: 844 }]) {
  test(`the Developer view and Projects fit ${size.width}x${size.height} without horizontal overflow`, async ({ page }) => {
    const api = await mockApi(page, { projects: [anum(), tyre()] });
    api.makeReady();
    await page.setViewportSize(size);
    await page.goto('/#developer');
    await expect(page.getByRole('article', { name: /Developer request/ })).toBeVisible();
    expect(await noHorizontalOverflow(page)).toBe(true);
    await page.getByRole('navigation', { name: 'Developer sections' }).getByRole('button', { name: /Projects/ }).click();
    await page.getByRole('button', { name: 'Add a project' }).click();
    await expect(page.getByRole('form', { name: 'Add a project' })).toBeVisible();
    expect(await noHorizontalOverflow(page)).toBe(true);
  });
}
