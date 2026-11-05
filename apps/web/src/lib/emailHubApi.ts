// One Email module over the live API (docs/email.md#one-email-module): the unified read layer. Every
// action (read, reply, AI, send for approval) goes to the back end that owns the mailbox.
import { apiRequest } from './api';
import { unifiedQuery, type UnifiedMailboxes, type UnifiedPage } from './emailHub';

const base = '/api/v1/email/unified';

export const listUnifiedMailboxes = (): Promise<UnifiedMailboxes> => apiRequest(`${base}/mailboxes`, { method: 'GET' });
export const listUnifiedMessages = (opts: { q?: string; unread?: boolean; cursor?: string | null; limit?: number }): Promise<UnifiedPage> =>
  apiRequest(`${base}/messages?${unifiedQuery(opts)}`, { method: 'GET' });
