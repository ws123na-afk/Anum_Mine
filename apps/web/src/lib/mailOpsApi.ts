// The platform operator's mail health over the live API (docs/email.md#operator-view).
import { apiRequest } from './api';
import type { MailOperatorView, MailServerView } from './mailOps';

export const getMailHealth = (days: number): Promise<MailOperatorView> =>
  apiRequest(`/api/v1/platform/mail/health?days=${encodeURIComponent(String(days))}`, { method: 'GET' });
export const getMailServer = (): Promise<MailServerView> => apiRequest('/api/v1/platform/mail/server', { method: 'GET' });
