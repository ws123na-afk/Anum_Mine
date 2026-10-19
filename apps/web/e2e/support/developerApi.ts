import type { Page } from '@playwright/test';
import { mockWorkspaceApi } from './workspaceApi';

// Developer mode (docs/developer-mode.md) for the page gates and the journey spec: the workspace mock
// plus a ready setup with one project, which can be added, changed and removed. developer.spec.ts
// keeps its own, fuller mock for the request lifecycle.
const now = '2026-10-11T09:00:00.000Z';

function project(id: string, name: string, repository: string, extra: Record<string, unknown> = {}) {
  const [owner, repo] = repository.split('/');
  return {
    id, name, github_owner: owner, github_repo: repo, repository, default_branch: 'main', description: '', enabled: true, protected_paths: [] as string[],
    workflow_file: 'anum-developer.yml', token_source: 'project', token_configured: true, token_updated_at: now, last_check: null,
    install_pr_number: null, install_pr_url: null, ready: true, problems: [] as string[], created_by: 'user_local', created_at: now, updated_at: now, ...extra,
  };
}

export interface DeveloperMock { projects: ReturnType<typeof project>[]; writes: { method: string; path: string; body: unknown }[] }

export async function mockDeveloper(page: Page): Promise<DeveloperMock> {
  await mockWorkspaceApi(page);
  const state: DeveloperMock = { projects: [project('devproj_anum', 'ANUM', 'acme/anum', { description: 'The ANUM agent platform' })], writes: [] };
  await page.route('http://localhost:8000/api/v1/developer/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const body = request.postData() ? request.postDataJSON() as Record<string, unknown> : null;
    if (method !== 'GET') state.writes.push({ method, path, body });
    if (path === '/api/v1/developer/setup') {
      return json({
        configured: true, enabled_here: true, repository: null, workflow: 'anum-developer.yml', base_branch: 'main', missing: [], message: 'Ready',
        max_requests_per_day: 10, requests_today: 0, projects: state.projects.length, ready_projects: state.projects.filter((p) => p.ready).length, max_projects: 100, environment_token_configured: false,
      });
    }
    if (path === '/api/v1/developer/projects' && method === 'GET') return json(state.projects);
    if (path === '/api/v1/developer/projects' && method === 'POST' && body) {
      const created = project(`devproj_${String(body.github_repo)}`, String(body.name), `${String(body.github_owner)}/${String(body.github_repo)}`, {
        description: body.description ?? '', default_branch: body.default_branch ?? 'main', protected_paths: body.protected_paths ?? [], token_configured: Boolean(body.token),
        ready: Boolean(body.token), problems: body.token ? [] : ['No GitHub token: add a fine-grained token for this repository.'],
      });
      state.projects = [...state.projects, created].sort((a, b) => a.name.localeCompare(b.name));
      return json(created, 201);
    }
    const match = /^\/api\/v1\/developer\/projects\/([^/]+)$/.exec(path);
    if (match) {
      const found = state.projects.find((p) => p.id === match[1]);
      if (!found) return json({ error: { code: 'not_found', message: 'Project not found', correlation_id: 'c' } }, 404);
      if (method === 'PATCH' && body) {
        const updated = { ...found, ...body };
        state.projects = state.projects.map((p) => (p.id === found.id ? updated : p));
        return json(updated);
      }
      if (method === 'DELETE') {
        state.projects = state.projects.filter((p) => p.id !== found.id);
        return route.fulfill({ status: 204 });
      }
      return json(found);
    }
    if (path === '/api/v1/developer/workflow-template') return json({ file_name: 'anum-developer.yml', path: '.github/workflows/anum-developer.yml', content: 'name: ANUM developer\n', secrets: [], variables: [] });
    if (path === '/api/v1/developer/requests' && method === 'GET') return json([]);
    return route.abort();
  });
  return state;
}
