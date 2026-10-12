// The /api/v1/autonomy routes (docs/autonomy.md). Who may do what is decided by the API:
// reads for every member, settings, rules and proposals for owners, guardrails for organization
// administrators, "always ask" for anyone who runs tasks, undo for owners and the requester.
import type { AutonomousAction, AutonomousActionPage, AutonomyLevel, AutonomyOverview, AutonomyProposal, AutonomyRule, OrganizationGuardrails } from '@anum/contracts';
import { apiRequest } from './api';
import {
  feedQuery, mapAction, mapActionPage, mapGuardrails, mapOverview, mapProposal, mapRule,
  type ApiAction, type ApiActionPage, type ApiGuardrails, type ApiOverview, type ApiProposal, type ApiRule, type ApiSettings, type FeedFilters,
} from './autonomy';

const id = encodeURIComponent;
const base = '/api/v1/autonomy';

export async function getAutonomy(): Promise<AutonomyOverview> {
  return mapOverview(await apiRequest<ApiOverview>(`${base}/settings`, { method: 'GET' }));
}

export async function saveAutonomy(body: Omit<ApiSettings, 'updated_by' | 'updated_at'>): Promise<AutonomyOverview> {
  return mapOverview(await apiRequest<ApiOverview>(`${base}/settings`, { method: 'PUT', body: JSON.stringify(body) }));
}

export async function saveGuardrails(body: Omit<ApiGuardrails, 'updated_by' | 'updated_at'>): Promise<OrganizationGuardrails> {
  return mapGuardrails(await apiRequest<ApiGuardrails>(`${base}/guardrails`, { method: 'PUT', body: JSON.stringify(body) }));
}

export async function getRules(): Promise<AutonomyRule[]> {
  return (await apiRequest<ApiRule[]>(`${base}/rules`, { method: 'GET' })).map(mapRule);
}

export async function createRule(tool: string, template: string | null, level: AutonomyLevel): Promise<AutonomyRule> {
  return mapRule(await apiRequest<ApiRule>(`${base}/rules`, { method: 'POST', body: JSON.stringify({ tool, template, level }) }));
}

export async function deleteRule(ruleId: string): Promise<void> {
  await apiRequest<unknown>(`${base}/rules/${id(ruleId)}`, { method: 'DELETE' }).catch((error: unknown) => {
    // 204 has no JSON body; only a real failure is rethrown.
    if (error instanceof SyntaxError) return;
    throw error;
  });
}

export async function getProposals(): Promise<AutonomyProposal[]> {
  return (await apiRequest<ApiProposal[]>(`${base}/proposals`, { method: 'GET' })).map(mapProposal);
}

export async function decideProposal(proposalId: string, decision: 'accept' | 'decline'): Promise<void> {
  await apiRequest<unknown>(`${base}/proposals/${id(proposalId)}/${decision}`, { method: 'POST' });
}

export async function getActions(filters: FeedFilters, cursor: string | null): Promise<AutonomousActionPage> {
  return mapActionPage(await apiRequest<ApiActionPage>(`${base}/actions?${feedQuery(filters, cursor)}`, { method: 'GET' }));
}

export async function getAction(actionId: string): Promise<AutonomousAction> {
  return mapAction(await apiRequest<ApiAction>(`${base}/actions/${id(actionId)}`, { method: 'GET' }));
}

export async function undoAction(action: AutonomousAction): Promise<AutonomousAction> {
  return mapAction(await apiRequest<ApiAction>(`${base}/actions/${id(action.id)}/undo`, { method: 'POST', body: JSON.stringify({ undo_token: action.undoToken }) }));
}

export async function tightenAction(actionId: string): Promise<AutonomyRule> {
  return mapRule(await apiRequest<ApiRule>(`${base}/actions/${id(actionId)}/tighten`, { method: 'POST' }));
}
