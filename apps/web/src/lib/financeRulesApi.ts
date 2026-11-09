// Rules, learning, merchant spellings, subcategories, the suggestion queue and automation policies over the live API
// (docs/finance.md#rules-and-learning, #suggestions, #automation-policies). Every record is the caller's own; the server
// checks finance:read and finance:manage. Approving and applying are separate, explicit owner steps.
import type {
  FinanceAiSuggestion, FinanceAutomation, FinanceAutomationPolicy, FinanceMerchantAlias, FinanceRuleBatchPage, FinanceRuleContent, FinanceRuleDetail,
  FinanceRuleBatch, FinanceRulePage, FinanceRulePreview, FinanceRuleUndo, FinanceRuleVersion, FinanceSimilar, FinanceSubcategory,
  FinanceSuggestionDecision, FinanceSuggestionQueue,
} from '@anum/contracts';
import { apiRequest, apiRequestVoid } from './api';
import { query } from './financeApi';

const BASE = '/api/v1/finance';
const id = encodeURIComponent;
const get = <T>(path: string) => apiRequest<T>(`${BASE}${path}`, { method: 'GET' });
const send = <T>(method: string, path: string, body?: unknown) => apiRequest<T>(`${BASE}${path}`, {
  method, ...(body === undefined ? {} : { body: JSON.stringify(body), headers: { 'content-type': 'application/json' } }),
});

export const listSuggestions = (p: { kind?: string | null; limit: number; offset: number }) => get<FinanceSuggestionQueue>(`/suggestions${query({ ...p })}`);
export const acceptSuggestion = (key: string, payloadHash: string) => send<FinanceSuggestionDecision>('POST', '/suggestions/accept', { key, payload_hash: payloadHash });
export const rejectSuggestion = (key: string) => send<FinanceSuggestionDecision>('POST', '/suggestions/reject', { key });
export const restoreSuggestion = (key: string) => send<FinanceSuggestionDecision>('POST', '/suggestions/restore', { key });

export const listRules = (p: { state?: string; q?: string; limit: number; offset: number }) => get<FinanceRulePage>(`/rules${query({ ...p })}`);
export const getRule = (ruleId: string) => get<FinanceRuleDetail>(`/rules/${id(ruleId)}`);
export const proposeRule = (content: FinanceRuleContent, sourceTransactionId?: string | null) =>
  send<FinanceRuleVersion>('POST', '/rules', { content, ...(sourceTransactionId ? { source_transaction_id: sourceTransactionId } : {}) });
export const editRule = (ruleId: string, content: FinanceRuleContent) => send<FinanceRuleVersion>('POST', `/rules/${id(ruleId)}/versions`, { content });
export const approveRule = (v: Pick<FinanceRuleVersion, 'rule_id' | 'version' | 'payload_hash'>) =>
  send<FinanceRuleVersion>('POST', `/rules/${id(v.rule_id)}/approve`, { version: v.version, payload_hash: v.payload_hash });
export const rejectRule = (ruleId: string, version: number) => send<FinanceRuleVersion>('POST', `/rules/${id(ruleId)}/reject`, { version });
export const revertRule = (ruleId: string, toVersion: number) => send<FinanceRuleVersion>('POST', `/rules/${id(ruleId)}/revert`, { to_version: toVersion });
export const previewContent = (content: FinanceRuleContent) => send<FinanceRulePreview>('POST', '/rules/preview', { content });
export const previewRule = (ruleId: string) => send<FinanceRulePreview>('POST', `/rules/${id(ruleId)}/preview`);
export const applyRule = (ruleId: string, previewHash: string) => send<FinanceRuleBatch>('POST', `/rules/${id(ruleId)}/apply`, { preview_hash: previewHash });
export const listBatches = (p: { limit: number; offset: number }) => get<FinanceRuleBatchPage>(`/rule-batches${query({ ...p })}`);
export const undoBatch = (batchId: string) => send<FinanceRuleUndo>('POST', `/rule-batches/${id(batchId)}/undo`);
export const similar = (transactionId: string, scope: 'all' | 'account') => send<FinanceSimilar>('POST', `/transactions/${id(transactionId)}/similar`, { scope });
export const aiSuggestion = (transactionId: string) => send<FinanceAiSuggestion>('POST', `/transactions/${id(transactionId)}/ai-suggestion`);

export const listAliases = () => get<FinanceMerchantAlias[]>('/merchants/aliases');
export const saveAlias = (body: { alias: string; merchant: string }, aliasId?: string) =>
  send<FinanceMerchantAlias>(aliasId ? 'PATCH' : 'POST', aliasId ? `/merchants/aliases/${id(aliasId)}` : '/merchants/aliases', body);
export const deleteAlias = (aliasId: string) => apiRequestVoid(`${BASE}/merchants/aliases/${id(aliasId)}`, { method: 'DELETE' });
export const listSubcategories = () => get<FinanceSubcategory[]>('/subcategories');
export const saveSubcategory = (body: Omit<FinanceSubcategory, 'id'>, subId?: string) =>
  send<FinanceSubcategory>(subId ? 'PATCH' : 'POST', subId ? `/subcategories/${id(subId)}` : '/subcategories', body);

export const getAutomation = () => get<FinanceAutomation>('/automation');
export const putAutomation = (policy: FinanceAutomationPolicy) => send<FinanceAutomation>('PUT', '/automation', policy);

/** Save a rule as a proposal and approve it with the payload hash the owner just saw (two explicit steps on the server). */
export async function saveAndApprove(content: FinanceRuleContent, opts: { ruleId?: string; sourceTransactionId?: string | null } = {}): Promise<FinanceRuleVersion> {
  const proposed = opts.ruleId ? await editRule(opts.ruleId, content) : await proposeRule(content, opts.sourceTransactionId);
  return approveRule(proposed);
}
