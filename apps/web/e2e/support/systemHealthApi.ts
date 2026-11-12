import type { Page, Route } from '@playwright/test';

// System health (docs/system-health.md): a fake of GET /api/v1/platform/health. ``healthy`` answers with every
// check green; the default is a deployment with problems (the mock model, an off-site backup older than 26 hours,
// no restore drill, no notices). ``denied`` answers 403 as for someone who is not a platform operator.
// Register it after mockWorkspaceApi and mockBilling: Playwright tries the newest route first.
const cors = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': '*' };
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', headers: cors, body: JSON.stringify(body) });
const hoursAgo = (hours: number) => new Date(Date.now() - hours * 3600_000).toISOString();
const GB = 1024 ** 3;

export interface HealthOptions { healthy?: boolean; denied?: boolean; failing?: boolean }

export function healthReport({ healthy = false }: HealthOptions = {}) {
  const items = [
    { key: 'backup_local', state: 'green', detail: { recorded: true, ever: true, last_ok: true, last_run_at: hoursAgo(7), last_success_at: hoursAgo(7), age_hours: 7, max_age_hours: 26 } },
    healthy
      ? { key: 'backup_offsite', state: 'green', detail: { recorded: true, ever: true, last_ok: true, last_run_at: hoursAgo(7), last_success_at: hoursAgo(7), age_hours: 7, max_age_hours: 26 } }
      : { key: 'backup_offsite', state: 'red', detail: { recorded: true, ever: true, last_ok: false, last_run_at: hoursAgo(7), last_success_at: hoursAgo(31), age_hours: 31, max_age_hours: 26 } },
    healthy
      ? { key: 'restore_drill', state: 'green', detail: { recorded: true, ever: true, last_ok: true, last_run_at: hoursAgo(240), last_success_at: hoursAgo(240), age_days: 10, max_age_days: 35 } }
      : { key: 'restore_drill', state: 'amber', detail: { recorded: true, ever: false } },
    healthy
      ? { key: 'ai_model', state: 'green', detail: { provider: 'anthropic', placeholder: false, key_missing: false } }
      : { key: 'ai_model', state: 'red', detail: { provider: 'mock', placeholder: true, key_missing: false } },
    { key: 'disk', state: 'green', detail: { readable: true, free_bytes: 61 * GB, total_bytes: 96 * GB, free_pct: 63.5, scope: 'backups' } },
    { key: 'memory', state: 'green', detail: { readable: true, available_bytes: 14 * GB, total_bytes: 23 * GB, available_pct: 60.9, api_limit_bytes: 2 * GB, api_used_bytes: 0.6 * GB, api_free_pct: 70 } },
    { key: 'email', state: 'green', detail: { configured: true } },
    { key: 'scheduler', state: 'green', detail: { enabled: true, last_pass_at: hoursAgo(0.02), sweep_last_pass_at: hoursAgo(0.01), window_minutes: 5 } },
    healthy
      ? { key: 'errors', state: 'green', detail: { count_24h: 0, last_at: null, since: hoursAgo(24) } }
      : { key: 'errors', state: 'amber', detail: { count_24h: 4, last_at: hoursAgo(2), since: hoursAgo(24) } },
    { key: 'restarts', state: healthy ? 'green' : 'unknown', detail: healthy
      ? { configured: true, readable: true, started_at: hoursAgo(50), uptime_seconds: 50 * 3600, last_unexpected_at: null }
      : { configured: false, started_at: hoursAgo(50), uptime_seconds: 50 * 3600 } },
    { key: 'notices', state: healthy ? 'green' : 'amber', detail: healthy
      ? { configured: true, tenant_id: 'tenant_a', workspace_id: 'workspace_a' }
      : { configured: false, tenant_id: null, workspace_id: null } },
  ];
  const counts = { green: 0, amber: 0, red: 0, unknown: 0 } as Record<string, number>;
  for (const item of items) counts[item.state] += 1;
  return {
    checked_at: new Date().toISOString(), overall: healthy ? 'green' : 'red', started_at: hoursAgo(50), uptime_seconds: 50 * 3600,
    counts, items, you: { tenant_id: 'tenant_a', workspace_id: 'workspace_a' },
  };
}

export async function mockSystemHealth(page: Page, options: HealthOptions = {}): Promise<{ reads: number }> {
  const state = { reads: 0 };
  let failing = options.failing ?? false;
  await page.route('http://localhost:8000/api/v1/platform/health', async (route) => {
    if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
    state.reads += 1;
    if (options.denied) return json(route, { error: { code: 'forbidden', message: 'Only a platform operator can do this.' } }, 403);
    if (failing) { failing = false; return json(route, { error: { code: 'unavailable', message: 'Service unavailable' } }, 503); }
    return json(route, healthReport(options));
  });
  return state;
}
