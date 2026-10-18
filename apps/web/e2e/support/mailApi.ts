// A stateful fake of the Email Center API (/api/v1/mail, docs/email.md#email-center) for the e2e
// specs: folders with unread counts, paged lists, IMAP-style search, flags, moves and delete for good
// that pauses for an approval with a payload hash, attached files (checked and scanned) and scheduled
// send. Plainly fake, low-entropy fixtures only.
import type { Page, Route } from '@playwright/test';

export type MailRole = 'inbox' | 'drafts' | 'sent' | 'archive' | 'junk' | 'trash' | null;
export interface FakeMessage { uid: number; folder: string; subject: string; sender: { name: string; address: string }; date: string; seen: boolean; flagged: boolean; text: string }

export interface MailMockOptions {
  role?: 'owner' | 'member' | 'viewer';
  enabled?: boolean;
  /** No mailbox connected yet. */
  noAccount?: boolean;
  /** How many messages the inbox starts with (paging shows past 25). */
  inboxCount?: number;
  /** Folder list answers 503 (mail server offline). */
  offline?: boolean;
  /** A move or flag answers this status once. */
  failNextAction?: number;
  /** The server has no malware scanner (attached files are marked not scanned). */
  noScanner?: boolean;
  /** File names the fake malware scanner refuses. */
  scannerRefuses?: string[];
  /** The scheduled list answers 503. */
  scheduledFails?: boolean;
}

export interface MailCalls {
  flags: { folder: string; uids: number[]; flag: string; value: boolean }[];
  moves: { folder: string; uids: number[]; to: string }[];
  deletes: { folder: string; uids: number[] }[];
  approvals: unknown[];
  listQueries: string[];
  drafts: unknown[];
  patches: unknown[];
  sends: unknown[];
  uploads: string[];
  removals: string[];
  cancels: string[];
  folderLoads: number;
}

export const mailAccount = { id: 'mailbox_one', address: 'admin@example.com', display_name: 'Admin', status: 'connected', last_error: null, last_checked_at: '2026-10-14T08:00:00Z', created_at: '2026-10-01T08:00:00Z' };
const FOLDERS: { id: string; name: string; role: MailRole }[] = [
  { id: 'INBOX', name: 'Inbox', role: 'inbox' },
  { id: 'Drafts', name: 'Drafts', role: 'drafts' },
  { id: 'Sent', name: 'Sent', role: 'sent' },
  { id: 'Archive', name: 'Archive', role: 'archive' },
  { id: 'Junk', name: 'Junk', role: 'junk' },
  { id: 'Trash', name: 'Trash', role: 'trash' },
  { id: 'Projects', name: 'Projects', role: null },
];
export const deleteHash = 'ab'.repeat(32);
export const sendHash = 'cd'.repeat(32);

function seed(count: number): FakeMessage[] {
  const people = [{ name: 'Sara Example', address: 'sara@example.com' }, { name: 'Omar Example', address: 'omar@example.com' }, { name: 'Supplier Example', address: 'orders@supplier.example' }];
  const items: FakeMessage[] = [];
  for (let i = 0; i < count; i += 1) {
    const uid = 100 + count - i;
    items.push({
      uid, folder: 'INBOX', subject: i === 0 ? 'Tyre order for next week' : i === 1 ? 'Invoice question' : `Update number ${count - i}`,
      sender: people[i % people.length], date: new Date(Date.UTC(2026, 9, 14, 9, 0) - i * 3600_000).toISOString(),
      seen: i % 3 !== 0, flagged: i === 1, text: `Message body number ${count - i}.`,
    });
  }
  items.push({ uid: 7, folder: 'Trash', subject: 'Old newsletter', sender: people[2], date: '2026-10-10T08:00:00Z', seen: true, flagged: false, text: 'Old.' });
  items.push({ uid: 8, folder: 'Junk', subject: 'You won a prize', sender: { name: 'Prize Example', address: 'prize@spam.example' }, date: '2026-10-11T08:00:00Z', seen: false, flagged: false, text: 'Spam.' });
  return items;
}

export async function mockMailApi(page: Page, options: MailMockOptions = {}): Promise<{ calls: MailCalls; state: { messages: FakeMessage[]; nextUid: number; addInbox: (subject: string) => void } }> {
  const calls: MailCalls = { flags: [], moves: [], deletes: [], approvals: [], listQueries: [], folderLoads: 0, drafts: [], patches: [], sends: [], uploads: [], removals: [], cancels: [] };
  const state = {
    messages: seed(options.inboxCount ?? 30), nextUid: 1000,
    addInbox(subject: string) { this.messages.unshift({ uid: this.nextUid++, folder: 'INBOX', subject, sender: { name: 'Sara Example', address: 'sara@example.com' }, date: new Date().toISOString(), seen: false, flagged: false, text: 'New.' }); },
  };
  let failNext = options.failNextAction ?? 0;
  let pendingDelete: { folder: string; uids: number[] } | null = null;
  const role = options.role ?? 'owner';
  const drafts: Record<string, unknown>[] = [];
  const sendApproval = (draft: Record<string, unknown>) => ({
    id: 'approval_send', task_id: 'task_send', action: 'send_mail', risk_level: 'high', status: 'pending', reason: 'Sending mail needs approval.',
    created_at: new Date().toISOString(), run_id: 'run_send', step_id: 'step_send', payload_hash: sendHash, expires_at: new Date(Date.now() + 6 * 3600_000).toISOString(), target: null,
    arguments: { from: mailAccount.address, to: draft.to, subject: draft.subject, body: draft.body, send_at: draft.send_at ?? null, attachments: draft.attachments ?? [] },
  });
  const view = (m: FakeMessage) => ({ uid: m.uid, folder: m.folder, subject: m.subject, sender: m.sender, to: [{ name: '', address: mailAccount.address }], cc: [], date: m.date, message_id: `<m${m.uid}@example.com>`, seen: m.seen, flagged: m.flagged, answered: false, size: 1200, maybe_attachments: false });
  const approval = (uids: number[], folder: string) => ({
    id: 'approval_delete', task_id: 'task_delete', action: 'delete_mail', risk_level: 'high', status: 'pending', reason: 'Deleting mail for good needs approval.',
    created_at: new Date().toISOString(), run_id: 'run_delete', step_id: 'step_delete', payload_hash: deleteHash, expires_at: new Date(Date.now() + 6 * 3600_000).toISOString(), target: null,
    arguments: { account_id: mailAccount.id, mailbox: mailAccount.address, folder, uids },
  });

  await page.route('http://localhost:8000/api/v1/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const error = (status: number, message: string, code = 'error') => json({ error: { code, message } }, status);
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role, active: true });
    if (!path.startsWith('/api/v1/mail') && !path.startsWith('/api/v1/approvals/approval_')) return route.fallback();
    if (path === '/api/v1/mail/setup') {
      if (role === 'viewer') return error(403, 'Reading email needs the owner or member role in this workspace.', 'forbidden');
      const enabled = options.enabled ?? true;
      return json({
        enabled, configured: enabled, tenant_allowed: enabled, imap_host: enabled ? 'mail.example.com' : null, imap_port: 993, smtp_host: enabled ? 'mail.example.com' : null, smtp_port: 587,
        smtp_security: 'starttls', allowed_domains: ['example.com'], can_read: true, can_act: true, max_recipients: 50, send_per_hour: 30,
        attachment_max_bytes: 10 * 1024 * 1024, attachments_per_message: 10, attachments_total_bytes: 20 * 1024 * 1024,
        attachment_extensions: ['.csv', '.docx', '.jpg', '.pdf', '.png', '.txt', '.xlsx'], attachments_accepted: 'text, images, PDF and Office files', schedule_max_days: 30,
        accounts: options.noAccount || !enabled ? [] : [mailAccount],
      });
    }
    if (path === `/api/v1/mail/accounts/${mailAccount.id}/folders`) {
      calls.folderLoads += 1;
      if (options.offline) return error(503, 'The mail server did not answer. Try again in a minute.', 'offline');
      return json(FOLDERS.map((f) => {
        const inside = state.messages.filter((m) => m.folder === f.id);
        return { ...f, total: inside.length, unread: inside.filter((m) => !m.seen).length };
      }));
    }
    if (path === `/api/v1/mail/accounts/${mailAccount.id}/messages`) {
      calls.listQueries.push(url.search);
      const folder = url.searchParams.get('folder') ?? 'INBOX';
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      const limit = Number(url.searchParams.get('limit') ?? 25);
      const before = url.searchParams.get('before');
      let items = state.messages.filter((m) => m.folder === folder).sort((a, b) => b.uid - a.uid);
      if (q) items = items.filter((m) => `${m.subject} ${m.sender.name} ${m.sender.address} ${m.text}`.toLowerCase().includes(q));
      if (url.searchParams.get('unread') === 'true') items = items.filter((m) => !m.seen);
      if (url.searchParams.get('flagged') === 'true') items = items.filter((m) => m.flagged);
      const total = items.length;
      if (before) items = items.filter((m) => m.uid < Number(before));
      const pageItems = items.slice(0, limit);
      const more = items.length > limit;
      return json({ items: pageItems.map(view), total, next_before: more ? pageItems[pageItems.length - 1].uid : null });
    }
    if (path === `/api/v1/mail/accounts/${mailAccount.id}/message`) {
      const uid = Number(url.searchParams.get('uid'));
      const found = state.messages.find((m) => m.uid === uid && m.folder === url.searchParams.get('folder'));
      if (!found) return error(404, 'That message is gone. It may have been moved or deleted.', 'not_found');
      found.seen = true;
      return json({ message: view(found), reply_to: [], text: found.text, text_truncated: false, html: null, remote_images_blocked: 0, inline_images_hidden: 0, attachments: [] });
    }
    if (path === `/api/v1/mail/accounts/${mailAccount.id}/flags` && method === 'POST') {
      const body = request.postDataJSON() as MailCalls['flags'][number];
      calls.flags.push(body);
      if (failNext) { const status = failNext; failNext = 0; return error(status, 'The mail server did not answer. Try again in a minute.', 'offline'); }
      for (const m of state.messages) if (m.folder === body.folder && body.uids.includes(m.uid)) (m as unknown as Record<string, boolean>)[body.flag] = body.value;
      return json({ status: 'done', summary: `Updated ${body.uids.length} message(s).`, approval: null });
    }
    if (path === `/api/v1/mail/accounts/${mailAccount.id}/move` && method === 'POST') {
      const body = request.postDataJSON() as MailCalls['moves'][number];
      calls.moves.push(body);
      if (failNext) { const status = failNext; failNext = 0; return error(status, 'The mail server did not answer. Try again in a minute.', 'offline'); }
      const target = FOLDERS.find((f) => f.role === body.to || f.id === body.to);
      if (!target) return error(409, `This mailbox has no ${body.to} folder.`);
      for (const m of state.messages) if (m.folder === body.folder && body.uids.includes(m.uid)) { m.folder = target.id; m.uid = state.nextUid++; }
      return json({ status: 'done', summary: `Moved ${body.uids.length} message(s).`, approval: null });
    }
    if (path === `/api/v1/mail/accounts/${mailAccount.id}/delete` && method === 'POST') {
      const body = request.postDataJSON() as MailCalls['deletes'][number];
      calls.deletes.push(body);
      if (body.folder !== 'Trash' && body.folder !== 'Junk') return error(409, 'Move messages to Trash first. Only Trash and Junk can be emptied for good.');
      pendingDelete = body;
      return json({ status: 'waiting_approval', summary: 'Waiting for approval to delete for good.', approval: approval([...body.uids].sort((a, b) => a - b), body.folder) });
    }
    if (path === '/api/v1/approvals/approval_delete/approve' && method === 'POST') {
      calls.approvals.push(request.postDataJSON());
      if (role !== 'owner') return error(403, 'Only an owner can approve this.', 'forbidden');
      if (pendingDelete) { const done = pendingDelete; state.messages = state.messages.filter((m) => !(m.folder === done.folder && done.uids.includes(m.uid))); pendingDelete = null; }
      const at = new Date().toISOString();
      return json({ task: { id: 'task_delete', title: 'Delete', prompt: 'Delete', status: 'completed', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: at, updated_at: at }, run: { id: 'run_delete', task_id: 'task_delete', status: 'completed', steps: [], result: 'Deleted for good.' }, approval: { ...approval([], 'Trash'), status: 'approved' } });
    }
    const stamp = new Date().toISOString();
    if (path === '/api/v1/mail/drafts' && method === 'GET') return json(drafts);
    if (path === '/api/v1/mail/drafts' && method === 'POST') {
      const body = request.postDataJSON() as { kind: string; uid?: number; folder?: string };
      calls.drafts.push(body);
      const original = state.messages.find((m) => m.uid === body.uid);
      const draft = {
        id: `maildraft_${drafts.length + 1}`, account_id: mailAccount.id, kind: body.kind, to: body.kind === 'reply' || body.kind === 'reply_all' ? [original?.sender.address ?? ''] : [], cc: [], bcc: [],
        subject: original ? `${body.kind === 'forward' ? 'Fwd' : 'Re'}: ${original.subject}` : '', body: original ? `\n\n> ${original.text}` : '', in_reply_to: original ? `<m${original.uid}@example.com>` : null,
        message_id: `<d${drafts.length + 1}@example.com>`, status: 'draft', error: null, approval: null, approval_note: null, saved_to_sent: null, sent_at: null, created_at: stamp, updated_at: stamp,
        send_at: null, attachments: [], attachment_note: null,
      };
      drafts.unshift(draft);
      return json(draft, 201);
    }
    if (path === '/api/v1/mail/scheduled' && method === 'GET') {
      if (options.scheduledFails) return error(503, 'The ANUM server did not answer. Try again in a minute.', 'unavailable');
      return json(drafts.filter((d) => d.send_at && (d.status === 'scheduled' || d.status === 'pending_approval')).sort((a, b) => String(a.send_at).localeCompare(String(b.send_at))));
    }
    const fileMatch = /^\/api\/v1\/mail\/drafts\/([^/]+)\/attachments(?:\/([^/]+))?$/.exec(path);
    if (fileMatch) {
      const draft = drafts.find((d) => d.id === fileMatch[1]);
      if (!draft) return error(404, 'Message not found.', 'not_found');
      const files = draft.attachments as { id: string; filename: string; size: number }[];
      const unapprove = () => { if (draft.status === 'pending_approval' || draft.status === 'scheduled') Object.assign(draft, { status: 'draft', approval: null }); };
      if (!fileMatch[2] && method === 'POST') {
        const name = url.searchParams.get('name') ?? '';
        calls.uploads.push(name);
        if (name.endsWith('.exe')) return error(415, 'This workspace does not accept .exe files.', 'unsupported_media_type');
        if (options.scannerRefuses?.includes(name)) return error(422, `The malware scanner refused ${name}: Example.Test.Signature`, 'validation_error');
        unapprove();
        const file = { id: `mailfile_${files.length + 1}`, filename: name, content_type: name.endsWith('.pdf') ? 'application/pdf' : 'text/plain', size: request.postDataBuffer()?.length ?? 0, sha256: 'ef'.repeat(32), scanned: !options.noScanner, created_at: stamp };
        files.push(file);
        return json(file, 201);
      }
      const file = files.find((f) => f.id === fileMatch[2]);
      if (!file) return error(404, 'That file is not attached to this message.', 'not_found');
      if (method === 'DELETE') { calls.removals.push(file.id); unapprove(); files.splice(files.indexOf(file), 1); return route.fulfill({ status: 204 }); }
      return route.fulfill({ status: 200, contentType: 'application/octet-stream', body: 'example file body' });
    }
    const cancelMatch = /^\/api\/v1\/mail\/drafts\/([^/]+)\/cancel-schedule$/.exec(path);
    if (cancelMatch && method === 'POST') {
      const draft = drafts.find((d) => d.id === cancelMatch[1]);
      if (!draft) return error(404, 'Message not found.', 'not_found');
      calls.cancels.push(draft.id as string);
      if (!draft.send_at) return error(409, 'This message is not scheduled.');
      Object.assign(draft, { status: 'draft', approval: null, send_at: null, updated_at: stamp });
      return json(draft);
    }
    const draftMatch = /^\/api\/v1\/mail\/drafts\/([^/]+)(\/send)?$/.exec(path);
    if (draftMatch) {
      const draft = drafts.find((d) => d.id === draftMatch[1]);
      if (!draft) return error(404, 'Message not found.', 'not_found');
      if (draftMatch[2] && method === 'POST') {
        const body = request.postDataJSON() as { send_at?: string };
        calls.sends.push(body);
        Object.assign(draft, { send_at: body.send_at ? new Date(body.send_at).toISOString() : null });
        Object.assign(draft, { status: 'pending_approval', approval: sendApproval(draft) });
        return json({ draft, approval: draft.approval });
      }
      if (method === 'PATCH') { calls.patches.push(request.postDataJSON()); Object.assign(draft, request.postDataJSON() as object, { status: 'draft', approval: null, updated_at: stamp }); return json(draft); }
      if (method === 'DELETE') { drafts.splice(drafts.indexOf(draft), 1); return route.fulfill({ status: 204 }); }
      return json(draft);
    }
    if (path === '/api/v1/approvals/approval_send/approve' && method === 'POST') {
      calls.approvals.push(request.postDataJSON());
      const draft = drafts.find((d) => d.status === 'pending_approval');
      if (draft && draft.send_at) Object.assign(draft, { status: 'scheduled', approval: null });
      else if (draft) Object.assign(draft, { status: 'sent', approval: null, saved_to_sent: true, sent_at: stamp });
      return json({ task: { id: 'task_send', title: 'Send', prompt: 'Send', status: 'completed', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: stamp, updated_at: stamp }, run: { id: 'run_send', task_id: 'task_send', status: 'completed', steps: [], result: 'Sent.' }, approval: { ...sendApproval(draft ?? {}), status: 'approved' } });
    }
    return route.fallback();
  });
  return { calls, state };
}
