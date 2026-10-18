// Email Center phase 6 over the live API (docs/email.md#mailbox-settings): mailbox settings,
// forwarding rules and the background unread counts. Every call matches
// services/api/anum_api/mailbox/settings_api.py.
import { apiRequest, apiRequestVoid } from './api';
import type { MailRule, MailRuleInput, MailRuleResult, MailSettings, MailSettingsValues, MailUnread, MailWaiting } from './mailRules';

const base = '/api/v1/mail';
const id = encodeURIComponent;

export const getMailSettings = (accountId: string): Promise<MailSettings> => apiRequest(`${base}/accounts/${id(accountId)}/settings`, { method: 'GET' });
/** Ask to change the settings: answers with the approval an owner decides with the payload hash shown. */
export const askMailSettings = (accountId: string, values: MailSettingsValues, baseVersion: number): Promise<MailWaiting> =>
  apiRequest(`${base}/accounts/${id(accountId)}/settings`, { method: 'POST', body: JSON.stringify({ ...values, base_version: baseVersion }) });
export const withdrawMailSettings = (accountId: string): Promise<void> => apiRequestVoid(`${base}/accounts/${id(accountId)}/settings/pending`, { method: 'DELETE' });

export const listMailRules = (accountId: string): Promise<MailRule[]> => apiRequest(`${base}/accounts/${id(accountId)}/rules`, { method: 'GET' });
export const createMailRule = (accountId: string, input: MailRuleInput): Promise<MailRuleResult> =>
  apiRequest(`${base}/accounts/${id(accountId)}/rules`, { method: 'POST', body: JSON.stringify(input) });
export const updateMailRule = (ruleId: string, input: MailRuleInput): Promise<MailRuleResult> =>
  apiRequest(`${base}/rules/${id(ruleId)}`, { method: 'PATCH', body: JSON.stringify(input) });
export const pauseMailRule = (ruleId: string): Promise<MailRule> => apiRequest(`${base}/rules/${id(ruleId)}/pause`, { method: 'POST' });
export const resumeMailRule = (ruleId: string): Promise<MailRuleResult> => apiRequest(`${base}/rules/${id(ruleId)}/resume`, { method: 'POST' });
export const deleteMailRule = (ruleId: string): Promise<void> => apiRequestVoid(`${base}/rules/${id(ruleId)}`, { method: 'DELETE' });

export const getMailUnread = (): Promise<MailUnread> => apiRequest(`${base}/unread`, { method: 'GET' });
