// A stateful fake of the Email Center phase 6 routes (docs/email.md#mailbox-settings): mailbox
// settings with a change that waits for an approval bound to its payload hash, forwarding rules
// (each new, edited or resumed rule waits for an approval) and the background unread counts.
// Registered after mockMailApi, so it answers these paths first and passes everything else on.
// Plainly fake, low-entropy fixtures only.
import type { Page, Route } from '@playwright/test';
import { mailAccount } from './mailApi';

export interface RulesOptions {
  role?: 'owner' | 'member';
  /** The settings answer 503 once. */
  failSettings?: boolean;
  /** The rules list answers 503 once. */
  failRules?: boolean;
  /** Someone else changes the settings before the first ask (the ask answers 409). */
  staleOnce?: boolean;
  /** Rules the mailbox starts with. */
  rules?: 'none' | 'some';
  /** What GET /mail/unread answers. */
  unreadTotal?: number;
  syncRunning?: boolean;
}

export interface RulesCalls { asks: unknown[]; withdraws: number; creates: unknown[]; patches: unknown[]; pauses: string[]; resumes: string[]; deletes: string[]; approvals: string[]; unreadReads: number }

export const settingsHash = 'ef'.repeat(32);
export const ruleHash = '12'.repeat(32);
const stamp = '2026-10-16T08:00:00Z';

export async function mockMailRules(page: Page, options: RulesOptions = {}): Promise<{ calls: RulesCalls }> {
  const calls: RulesCalls = { asks: [], withdraws: 0, creates: [], patches: [], pauses: [], resumes: [], deletes: [], approvals: [], unreadReads: 0 };
  const role = options.role ?? 'owner';
  let failSettings = options.failSettings ?? false;
  let failRules = options.failRules ?? false;
  let stale = options.staleOnce ?? false;
  const settings: Record<string, unknown> = {
    account_id: mailAccount.id, access: 'owner', can_change: true, sent_retention_days: null, draft_retention_days: null, label_retention_days: null,
    background_sync: true, version: 0, updated_at: null, retention_choices: [7, 30, 90, 180, 365, 730], max_retention_days: 3650,
    sync_running: options.syncRunning ?? true, sync_interval_seconds: 300,
    sync: { status: 'ok', inbox_unread: 12, inbox_total: 30, checked_at: '2026-10-16T07:55:00Z', next_sync_at: '2026-10-16T08:00:00Z', last_error: null },
    approval: null, pending: null,
  };
  const approval = (id: string, action: string, hash: string, args: Record<string, unknown>) => ({
    id, task_id: `task_${id}`, action, risk_level: 'high', status: 'pending', reason: 'Needs approval.', created_at: new Date().toISOString(),
    run_id: `run_${id}`, step_id: `step_${id}`, payload_hash: hash, expires_at: new Date(Date.now() + 6 * 3600_000).toISOString(), target: null, arguments: args,
  });
  const rules: Record<string, unknown>[] = options.rules === 'some' ? [{
    id: 'mrule_one', account_id: mailAccount.id, name: 'Supplier orders', from_contains: 'supplier.example', subject_contains: '', forward_to: ['team@example.com'],
    status: 'active', approval: null, approval_note: null, approved_at: stamp, last_run_at: stamp, last_error: null, forwarded_count: 4, created_at: stamp, updated_at: stamp,
  }] : [];
  let next = 1;
  const ask = (rule: Record<string, unknown>) => {
    const id = `approval_rule_${next++}`;
    Object.assign(rule, { status: 'pending_approval', approval: approval(id, 'activate_mail_forward_rule', ruleHash, { name: rule.name, forward_to: rule.forward_to }), updated_at: new Date().toISOString() });
  };

  await page.route('http://localhost:8000/api/v1/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const error = (status: number, message: string) => json({ error: { code: 'error', message } }, status);
    const base = `/api/v1/mail/accounts/${mailAccount.id}`;
    const body = () => (request.postData() ? JSON.parse(request.postData()!) : {});

    if (path === '/api/v1/mail/unread') {
      calls.unreadReads += 1;
      return json({ enabled: true, sync_running: options.syncRunning ?? true, total: options.unreadTotal ?? 0, checked_at: stamp, mailboxes: [] });
    }
    if (path === `${base}/settings` && method === 'GET') {
      if (failSettings) { failSettings = false; return error(503, 'The mail server cannot be reached right now.'); }
      return json(settings);
    }
    if (path === `${base}/settings` && method === 'POST') {
      const wanted = body();
      calls.asks.push(wanted);
      if (stale) { stale = false; settings.version = 1; settings.sent_retention_days = 365; return error(409, 'The settings changed since you opened them.'); }
      const { base_version: version, ...values } = wanted;
      if (version !== settings.version) return error(409, 'The settings changed since you opened them.');
      settings.pending = values;
      settings.approval = approval('approval_settings', 'change_mail_settings', settingsHash, { settings: values, base_version: version });
      return json({ status: 'waiting_approval', summary: 'Waiting for approval.', approval: settings.approval });
    }
    if (path === `${base}/settings/pending` && method === 'DELETE') {
      calls.withdraws += 1;
      settings.pending = null; settings.approval = null;
      return route.fulfill({ status: 204 });
    }
    if (path === '/api/v1/approvals/approval_settings/approve' && method === 'POST') {
      calls.approvals.push('approval_settings');
      if (role !== 'owner') return error(403, 'Only an owner can approve this.');
      if (body().payload_hash !== settingsHash) return error(409, 'The payload hash does not match.');
      Object.assign(settings, settings.pending as object, { version: (settings.version as number) + 1, pending: null, approval: null, updated_at: new Date().toISOString() });
      return json({ task: { id: 'task_settings', status: 'completed' }, run: { id: 'run_settings', status: 'completed', steps: [], result: 'Changed.' }, approval: { id: 'approval_settings', status: 'approved' } });
    }
    const approved = path.match(/^\/api\/v1\/approvals\/(approval_rule_\d+)\/approve$/);
    if (approved && method === 'POST') {
      calls.approvals.push(approved[1]);
      if (role !== 'owner') return error(403, 'Only an owner can approve this.');
      const rule = rules.find((r) => (r.approval as { id?: string } | null)?.id === approved[1]);
      if (!rule || body().payload_hash !== ruleHash) return error(409, 'This approval is stale.');
      Object.assign(rule, { status: 'active', approval: null, approved_at: new Date().toISOString() });
      return json({ task: { id: 'task_rule', status: 'completed' }, run: { id: 'run_rule', status: 'completed', steps: [], result: 'Turned on.' }, approval: { id: approved[1], status: 'approved' } });
    }
    if (path === `${base}/rules` && method === 'GET') {
      if (failRules) { failRules = false; return error(503, 'The rules could not be read right now.'); }
      return json(rules);
    }
    if (path === `${base}/rules` && method === 'POST') {
      const input = body();
      calls.creates.push(input);
      const rule = { id: `mrule_${next}`, account_id: mailAccount.id, ...input, approved_at: null, last_run_at: null, last_error: null, approval_note: null, forwarded_count: 0, created_at: new Date().toISOString() };
      ask(rule);
      rules.push(rule);
      return json({ rule, approval: rule.approval }, 201);
    }
    const one = path.match(/^\/api\/v1\/mail\/rules\/(mrule_\w+)(\/pause|\/resume)?$/);
    if (one) {
      const rule = rules.find((r) => r.id === one[1]);
      if (!rule) return error(404, 'Rule not found.');
      if (one[2] === '/pause') { calls.pauses.push(rule.id as string); Object.assign(rule, { status: 'paused', approval: null }); return json(rule); }
      if (one[2] === '/resume') { calls.resumes.push(rule.id as string); ask(rule); return json({ rule, approval: rule.approval }); }
      if (method === 'PATCH') { const input = body(); calls.patches.push(input); Object.assign(rule, input); ask(rule); return json({ rule, approval: rule.approval }); }
      if (method === 'DELETE') { calls.deletes.push(rule.id as string); rules.splice(rules.indexOf(rule), 1); return route.fulfill({ status: 204 }); }
    }
    return route.fallback();
  });
  return { calls };
}
