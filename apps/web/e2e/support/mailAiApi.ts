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
}

export interface MailAiCalls { summaries: unknown[]; labels: unknown[]; drafts: unknown[] }

export const aiReply = 'Hello Sara,\n\nThank you, the order is confirmed for next week.\n\nBest regards';

export async function mockMailAiApi(page: Page, options: MailAiMockOptions = {}): Promise<{ calls: MailAiCalls }> {
  const calls: MailAiCalls = { summaries: [], labels: [], drafts: [] };
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
    if (path === '/api/v1/mail/ai/status') {
      return json(options.unavailable
        ? { available: false, reason: 'No AI model is set up for this workspace. An owner chooses one in Settings, under Model.', model: null, can_read: true, can_act: true }
        : { available: true, reason: null, model: 'fake/example-model', can_read: true, can_act: true });
    }
    if (options.unavailable) return error(409, 'No AI model is set up for this workspace. An owner chooses one in Settings, under Model.', 'conflict');
    const body = request.postDataJSON() as { uid: number; thread?: boolean; kind?: string; instructions?: string };
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
