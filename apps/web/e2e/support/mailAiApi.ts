// A fake of AI on the Email Center (/api/v1/mail/ai, docs/email.md#ai-on-email) for the e2e specs.
// Register it after mockMailApi: Playwright asks the newest route first, and everything that is not
// an AI call (or an AI-written draft) falls back to the mail mock. Plainly fake fixtures only.
import type { Page, Route } from '@playwright/test';
import { mailAccount } from './mailApi';

export interface MailAiMockOptions {
  /** No model set up for the workspace. */
  unavailable?: boolean;
  /** The next summary answers 503 once. */
  failSummaryOnce?: boolean;
  /** Drafting answers 403 (the mailbox is shared read only). */
  denyDraft?: boolean;
  /** The opened message tries to instruct the AI. */
  injection?: boolean;
  /** The approved export download answers 410 (expired). */
  exportExpired?: boolean;
}

export interface MailAiCalls { summaries: unknown[]; labels: unknown[]; drafts: unknown[]; pages: { uids: number[] }[]; archives: { uids: number[] }[]; exports: { uids: number[] }[]; approvals: unknown[]; downloads: number }

export const archiveHash = 'ef'.repeat(32);
export const exportHash = '12'.repeat(32);
/** Categories the fake labels with, by UID: every third message is a newsletter. */
export const fakeCategory = (uid: number) => (uid % 3 === 0 ? 'newsletter' : uid % 3 === 1 ? 'customer' : 'order');

export const aiReply = 'Hello Sara,\n\nThank you, the order is confirmed for next week.\n\nBest regards';

export async function mockMailAiApi(page: Page, options: MailAiMockOptions = {}): Promise<{ calls: MailAiCalls }> {
  const calls: MailAiCalls = { summaries: [], labels: [], drafts: [], pages: [], archives: [], exports: [], approvals: [], downloads: 0 };
  const stored = new Map<number, { uid: number; category: string; priority: string; reason: string; source: string }>();
  const stamp = () => new Date().toISOString();
  const approvalOf = (kind: 'archive' | 'export', uids: number[], status = 'pending') => ({
    id: `approval_ai_${kind}`, task_id: `task_ai_${kind}`, action: kind === 'archive' ? 'archive_mail_bulk' : 'export_mail', risk_level: 'high', status,
    reason: 'External or high-impact actions require explicit approval.', created_at: stamp(), run_id: `run_ai_${kind}`, step_id: `step_ai_${kind}`,
    payload_hash: kind === 'archive' ? archiveHash : exportHash, expires_at: new Date(Date.now() + 6 * 3600_000).toISOString(), target: null,
    arguments: { account_id: mailAccount.id, folder: 'INBOX', uids },
  });
  const asked: Record<string, number[]> = {};
  let approvedExport = false;
  await page.route('http://localhost:8000/api/v1/approvals/approval_ai_*/approve', async (route: Route) => {
    const kind = route.request().url().includes('approval_ai_archive') ? 'archive' : 'export';
    calls.approvals.push(route.request().postDataJSON());
    if (kind === 'export') approvedExport = true;
    const at = stamp();
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      task: { id: `task_ai_${kind}`, title: kind, prompt: kind, status: 'completed', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: at, updated_at: at },
      run: { id: `run_ai_${kind}`, task_id: `task_ai_${kind}`, status: 'completed', steps: [], result: 'Done.' },
      approval: approvalOf(kind, asked[kind] ?? [], 'approved'),
    }) });
  });
  let failSummary = options.failSummaryOnce ?? false;
  const drafts: Record<string, Record<string, unknown>> = {};
  await page.route('http://localhost:8000/api/v1/mail/**', async (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const error = (status: number, message: string, code = 'error') => json({ error: { code, message, correlation_id: 'corr_example' } }, status);
    const draftMatch = /^\/api\/v1\/mail\/drafts\/(maildraft_ai_\d+)$/.exec(path);
    if (draftMatch) {
      const draft = drafts[draftMatch[1]];
      if (!draft) return error(404, 'Message not found.', 'not_found');
      if (method === 'PATCH') Object.assign(draft, request.postDataJSON() as object);
      if (method === 'DELETE') { delete drafts[draftMatch[1]]; return route.fulfill({ status: 204 }); }
      return json(draft);
    }
    if (!path.startsWith('/api/v1/mail/ai/')) return route.fallback();
    if (path === '/api/v1/mail/ai/labels') {
      const uids = (new URL(request.url()).searchParams.get('uids') ?? '').split(',').map(Number);
      return json(uids.filter((uid) => stored.has(uid)).map((uid) => stored.get(uid)));
    }
    if (path.startsWith('/api/v1/mail/ai/exports/')) {
      calls.downloads += 1;
      if (!approvedExport) return error(409, 'This export is pending, not approved.', 'conflict');
      if (options.exportExpired) return error(410, 'This export link has expired (one hour after approval). Ask for it again.', 'gone');
      return route.fulfill({ status: 200, contentType: 'application/mbox', body: 'From MAILER-DAEMON Thu Jan  1 00:00:00 1970\nSubject: example\n\nbody\n' });
    }
    if (path === '/api/v1/mail/ai/status') {
      return json(options.unavailable
        ? { available: false, reason: 'No AI model is set up for this workspace. An owner chooses one in Settings, under Model.', model: null, can_read: true, can_act: true }
        : { available: true, reason: null, model: 'fake/example-model', can_read: true, can_act: true });
    }
    if (options.unavailable) return error(409, 'No AI model is set up for this workspace. An owner chooses one in Settings, under Model.', 'conflict');
    const body = request.postDataJSON() as { uid: number; uids?: number[]; thread?: boolean; kind?: string; instructions?: string };
    if (path === '/api/v1/mail/ai/classify-page') {
      calls.pages.push({ uids: body.uids ?? [] });
      for (const uid of body.uids ?? []) stored.set(uid, { uid, category: fakeCategory(uid), priority: fakeCategory(uid) === 'newsletter' ? 'low' : uid % 2 ? 'high' : 'normal', reason: '', source: 'model' });
      const labels = (body.uids ?? []).map((uid) => stored.get(uid));
      return json({ labels, labelled: labels.length, skipped: 0, failed: 0, summary: `Labelled ${labels.length} message(s).` });
    }
    if (path === '/api/v1/mail/ai/archive' || path === '/api/v1/mail/ai/export') {
      const kind = path.endsWith('archive') ? 'archive' : 'export';
      const uids = [...(body.uids ?? [])].sort((a, b) => a - b);
      (kind === 'archive' ? calls.archives : calls.exports).push({ uids });
      asked[kind] = uids;
      return json({ status: 'waiting_approval', summary: 'Waiting for approval.', approval: approvalOf(kind, uids) });
    }
    if (path === '/api/v1/mail/ai/summarize') {
      calls.summaries.push(body);
      if (failSummary) { failSummary = false; return error(503, 'The AI model did not answer. Try again in a minute.', 'service_unavailable'); }
      return json({
        summary: options.injection ? 'The sender asks for all mail to be forwarded to an outside address.' : 'Sara confirms the tyre order for next week and asks for the delivery time.',
        key_points: ['Order for next week', 'Delivery time needed'], action_items: options.injection ? [] : ['Reply with the delivery time'],
        messages: body.thread ? 3 : 1, thread: Boolean(body.thread), injection_suspected: Boolean(options.injection), model: 'fake/example-model',
      });
    }
    if (path === '/api/v1/mail/ai/classify') {
      calls.labels.push(body);
      stored.set(body.uid, { uid: body.uid, category: 'customer', priority: 'high', reason: 'A customer needs an answer this week.', source: 'model' });
      return json({ category: 'customer', priority: 'high', reason: 'A customer needs an answer this week.', source: 'model', injection_suspected: Boolean(options.injection), model: 'fake/example-model' });
    }
    if (path === '/api/v1/mail/ai/draft-reply') {
      calls.drafts.push(body);
      if (options.denyDraft) return error(403, 'Drafting from this mailbox needs send access from its owner.', 'forbidden');
      const id = `maildraft_ai_${calls.drafts.length}`;
      const stamp = new Date().toISOString();
      const draft = {
        id, account_id: mailAccount.id, kind: body.kind ?? 'reply', to: ['sara@example.com'], cc: [], bcc: [], subject: 'Re: Tyre order for next week',
        body: `${aiReply}\n\n> Message body number 30.`, in_reply_to: '<m130@example.com>', message_id: `<${id}@example.com>`, status: 'draft', error: null,
        approval: null, approval_note: null, saved_to_sent: null, sent_at: null, created_at: stamp, updated_at: stamp,
      };
      drafts[id] = draft;
      return json({ status: 'done', draft, approval: null, injection_suspected: false, model: 'fake/example-model' }, 201);
    }
    return route.fallback();
  });
  return { calls };
}
