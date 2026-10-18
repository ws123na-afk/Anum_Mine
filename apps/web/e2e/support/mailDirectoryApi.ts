// A stateful fake of the Email Center phase 3 part A routes (docs/email.md#email-center): a second
// mailbox shared with the caller, shares, sender identities and contacts. Registered after
// mockMailApi, so it answers these paths first and passes everything else on. Plainly fake,
// low-entropy fixtures only.
import type { Page, Route } from '@playwright/test';
import { mailAccount } from './mailApi';

export interface DirectoryOptions {
  /** A second mailbox shared with the caller, to read or to send. */
  shared?: 'read' | 'send';
  /** How many contacts the address book starts with. */
  contacts?: number;
  /** The contacts list answers 503 once. */
  failContacts?: boolean;
}

export interface DirectoryCalls {
  grants: unknown[];
  grantPatches: unknown[];
  grantDeletes: string[];
  identities: unknown[];
  contacts: unknown[];
  contactPatches: unknown[];
  contactDeletes: string[];
  contactQueries: string[];
  draftCreates: unknown[];
}

export const sharedAccount = {
  id: 'mailbox_shared', address: 'team@example.com', display_name: 'Team', status: 'connected', last_error: null, last_checked_at: '2026-10-14T08:00:00Z',
  created_at: '2026-10-02T08:00:00Z', owner_user_id: 'owner_two', shared_with: 0, grant_id: 'mailshare_mine',
};

const stamp = '2026-10-15T08:00:00Z';
const contact = (index: number) => ({
  id: `mailcontact_${String(index).padStart(3, '0')}`, name: `Buyer ${String(index).padStart(2, '0')}`, email: `buyer${String(index).padStart(2, '0')}@shop.example`,
  company: 'Shop Example', phone: '', notes: '', created_at: stamp, updated_at: stamp,
});

export async function mockMailDirectory(page: Page, options: DirectoryOptions = {}): Promise<{ calls: DirectoryCalls }> {
  const calls: DirectoryCalls = { grants: [], grantPatches: [], grantDeletes: [], identities: [], contacts: [], contactPatches: [], contactDeletes: [], contactQueries: [], draftCreates: [] };
  const candidates = [
    { user_id: 'member_one', email: 'nadia@example.com', role: 'member' },
    { user_id: 'member_two', email: 'omar@example.com', role: 'member' },
  ];
  const grants: Record<string, unknown>[] = [];
  const identities: Record<string, unknown>[] = [];
  let contacts = Array.from({ length: options.contacts ?? 0 }, (_, i) => contact(i));
  let failContacts = options.failContacts ?? false;
  let shared = options.shared ?? null;

  await page.route('http://localhost:8000/api/v1/mail/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const error = (status: number, message: string) => json({ error: { code: 'error', message } }, status);
    const accountBase = `/api/v1/mail/accounts/${mailAccount.id}`;
    const sharedBase = `/api/v1/mail/accounts/${sharedAccount.id}`;

    if (path === '/api/v1/mail/setup') {
      const accounts: unknown[] = [{ ...mailAccount, access: 'owner', owner_user_id: 'user_local', shared_with: grants.length, grant_id: null }];
      if (shared) accounts.push({ ...sharedAccount, access: shared });
      return json({
        enabled: true, configured: true, tenant_allowed: true, imap_host: 'mail.example.com', imap_port: 993, smtp_host: 'mail.example.com', smtp_port: 587,
        smtp_security: 'starttls', allowed_domains: ['example.com'], can_read: true, can_act: true, max_recipients: 50, send_per_hour: 30, accounts,
      });
    }
    // Shares of the caller's own mailbox.
    if (path === `${accountBase}/share-candidates`) {
      return json(candidates.map((c) => {
        const grant = grants.find((g) => g.grantee_user_id === c.user_id);
        return { ...c, grant_id: grant?.id ?? null, access: grant?.access ?? null };
      }));
    }
    if (path === `${accountBase}/grants` && method === 'GET') return json(grants);
    if (path === `${accountBase}/grants` && method === 'POST') {
      const body = request.postDataJSON() as { grantee_user_id: string; access: string };
      calls.grants.push(body);
      const who = candidates.find((c) => c.user_id === body.grantee_user_id);
      const grant = { id: `mailshare_${grants.length + 1}`, account_id: mailAccount.id, grantee_user_id: body.grantee_user_id, grantee_email: who?.email ?? null, access: body.access, created_at: stamp, updated_at: stamp };
      grants.push(grant);
      return json(grant, 201);
    }
    const grantMatch = /^\/api\/v1\/mail\/grants\/([^/]+)$/.exec(path);
    if (grantMatch) {
      if (grantMatch[1] === 'mailshare_mine' && method === 'DELETE') { calls.grantDeletes.push('mailshare_mine'); shared = null; return route.fulfill({ status: 204 }); }
      const grant = grants.find((g) => g.id === grantMatch[1]);
      if (!grant) return error(404, 'That share is gone.');
      if (method === 'PATCH') { calls.grantPatches.push(request.postDataJSON()); Object.assign(grant, request.postDataJSON() as object); return json(grant); }
      if (method === 'DELETE') { calls.grantDeletes.push(grant.id as string); grants.splice(grants.indexOf(grant), 1); return route.fulfill({ status: 204 }); }
    }
    // Sender identities.
    if (path === `${accountBase}/identities` && method === 'GET') return json(identities);
    if (path === `${accountBase}/identities` && method === 'POST') {
      const body = request.postDataJSON() as { address: string; display_name?: string; signature?: string; is_default?: boolean };
      calls.identities.push(body);
      if (!body.address.endsWith('@example.com')) return error(422, 'Send-as addresses must be on example.com.');
      if (body.is_default) for (const i of identities) i.is_default = false;
      const identity = { id: `mailident_${identities.length + 1}`, account_id: mailAccount.id, address: body.address.toLowerCase(), display_name: body.display_name ?? null, signature: body.signature ?? '', is_default: Boolean(body.is_default), created_at: stamp, updated_at: stamp };
      identities.push(identity);
      return json(identity, 201);
    }
    const identityMatch = /^\/api\/v1\/mail\/identities\/([^/]+)$/.exec(path);
    if (identityMatch) {
      const identity = identities.find((i) => i.id === identityMatch[1]);
      if (!identity) return error(404, 'That sender identity is gone.');
      if (method === 'PATCH') { Object.assign(identity, request.postDataJSON() as object); return json(identity); }
      if (method === 'DELETE') { identities.splice(identities.indexOf(identity), 1); return route.fulfill({ status: 204 }); }
    }
    // The shared mailbox: read only unless shared to send.
    if (path === `${sharedBase}/identities`) return json([]);
    if (path === `${sharedBase}/folders`) return json([{ id: 'INBOX', name: 'Inbox', role: 'inbox', total: 2, unread: 1 }, { id: 'Trash', name: 'Trash', role: 'trash', total: 0, unread: 0 }]);
    if (path === `${sharedBase}/messages`) {
      const items = [1, 2].map((n) => ({
        uid: n, folder: 'INBOX', subject: n === 1 ? 'Team rota for next week' : 'Supplier visit', sender: { name: 'Sara Example', address: 'sara@example.com' }, to: [{ name: '', address: sharedAccount.address }],
        cc: [], date: `2026-10-1${n}T08:00:00Z`, message_id: `<s${n}@example.com>`, seen: n !== 1, flagged: false, answered: false, size: 900, maybe_attachments: false,
      }));
      return json({ items, total: 2, next_before: null });
    }
    if (path.startsWith(sharedBase) && method === 'POST') return error(403, 'This mailbox is shared with you to read only. Ask its owner to let you send from it.');
    // Contacts.
    if (path === '/api/v1/mail/contacts' && method === 'GET') {
      calls.contactQueries.push(url.search);
      if (failContacts) { failContacts = false; return error(503, 'The ANUM server did not answer. Try again in a minute.'); }
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      const limit = Number(url.searchParams.get('limit') ?? 25);
      const after = url.searchParams.get('cursor');
      const sorted = contacts.filter((c) => !q || `${c.name} ${c.email} ${c.company}`.toLowerCase().includes(q)).sort((a, b) => (a.name || a.email).localeCompare(b.name || b.email));
      const start = after ? sorted.findIndex((c) => c.id === after) + 1 : 0;
      const items = sorted.slice(start, start + limit);
      return json({ items, total: sorted.length, next_cursor: start + limit < sorted.length ? items[items.length - 1].id : null });
    }
    if (path === '/api/v1/mail/contacts' && method === 'POST') {
      const body = request.postDataJSON() as { name: string; email: string; company: string; phone: string; notes: string };
      calls.contacts.push(body);
      if (contacts.some((c) => c.email === body.email.toLowerCase())) return error(409, 'You already have a contact with this address.');
      const created = { ...body, email: body.email.toLowerCase(), id: `mailcontact_new_${contacts.length}`, created_at: stamp, updated_at: stamp };
      contacts = [...contacts, created];
      return json(created, 201);
    }
    const contactMatch = /^\/api\/v1\/mail\/contacts\/([^/]+)$/.exec(path);
    if (contactMatch) {
      const found = contacts.find((c) => c.id === contactMatch[1]);
      if (!found) return error(404, 'That contact is gone.');
      if (method === 'PATCH') { calls.contactPatches.push(request.postDataJSON()); Object.assign(found, request.postDataJSON() as object); return json(found); }
      if (method === 'DELETE') { calls.contactDeletes.push(found.id); contacts = contacts.filter((c) => c.id !== found.id); return route.fulfill({ status: 204 }); }
      return json(found);
    }
    if (path === '/api/v1/mail/drafts' && method === 'POST') calls.draftCreates.push(request.postDataJSON());
    return route.fallback();
  });
  return { calls };
}
