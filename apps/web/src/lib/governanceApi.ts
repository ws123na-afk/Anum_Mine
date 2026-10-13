// Governance, enterprise routing and integration configuration over the live API.
// docs/governance-and-scale.md, docs/tools-and-integrations.md. Every call names the current
// tenant and workspace through the shared request's headers.
import { apiRequest, apiRequestVoid } from './api';
import type { ApprovalRuleBody } from './approvalRules';
import type {
  ApprovalRule, EnterpriseOperations, GovernanceSummary, IntegrationConfiguration, IntegrationConfigurationView,
  PolicyPack, RoleTemplate, RoutingTarget,
} from './governance';

const get = <T>(path: string) => apiRequest<T>(path, { method: 'GET' });
const send = <T>(method: string, path: string, body: unknown = {}) => apiRequest<T>(path, { method, body: JSON.stringify(body) });

export const getGovernanceSummary = () => get<GovernanceSummary>('/api/v1/organization/governance');
export const getPolicyPacks = () => get<PolicyPack[]>('/api/v1/policy-packs');
export const createPolicyPack = (body: { name: string; description: string; rules: PolicyPack['rules'] }) => send<PolicyPack>('POST', '/api/v1/policy-packs', body);
export const setPolicyPackState = (id: string, action: 'activate' | 'archive') => send<PolicyPack>('POST', `/api/v1/policy-packs/${encodeURIComponent(id)}/${action}`);
export const getRoleTemplates = () => get<RoleTemplate[]>('/api/v1/role-templates');
export const createRoleTemplate = (body: { name: string; permissions: string[] }) => send<RoleTemplate>('POST', '/api/v1/role-templates', body);
export const getApprovalRules = () => get<ApprovalRule[]>('/api/v1/organization/approval-rules');
export const createApprovalRule = (body: ApprovalRuleBody) => send<ApprovalRule>('POST', '/api/v1/organization/approval-rules', body);
export const updateApprovalRule = (id: string, body: ApprovalRuleBody) => send<ApprovalRule>('PUT', `/api/v1/organization/approval-rules/${encodeURIComponent(id)}`, body);
export const deleteApprovalRule = (id: string) => apiRequestVoid(`/api/v1/organization/approval-rules/${encodeURIComponent(id)}`, { method: 'DELETE' });

export const getEnterpriseOperations = () => get<EnterpriseOperations>('/api/v1/enterprise/operations');
export const getRoutingTargets = () => get<RoutingTarget[]>('/api/v1/routing/targets');
export const saveRoutingTarget = (target: RoutingTarget) => send<RoutingTarget>('PUT', `/api/v1/routing/targets/${encodeURIComponent(target.id)}`, target);

export const getIntegrationConfiguration = (id: string) => get<IntegrationConfigurationView>(`/api/v1/integrations/${encodeURIComponent(id)}/configuration`);
export const saveIntegrationConfiguration = (id: string, body: IntegrationConfiguration) => send<IntegrationConfigurationView>('PUT', `/api/v1/integrations/${encodeURIComponent(id)}/configuration`, body);
