// Workspace members, invitations, model budgets and the approval policy over the live API.
// Routes: docs/identity.md (Invitations and Membership Management), docs/model-gateway.md (Monthly budgets),
// docs/approvals-and-risk.md (Workspace Approval Policy).
import type {
  AcceptedInvitation,
  AuditFilters,
  AuditPage,
  AuditRecord,
  MemberPage,
  MembersSummary,
  WorkspaceInfo,
  WorkspacePermissions,
  BudgetScope,
  CreatedInvitation,
  InvitationRequest,
  ModelBudgetLimits,
  ModelBudgetOverview,
  WorkspaceApprovalPolicy,
  WorkspaceInvitation,
  WorkspaceMember,
  WorkspaceRole,
} from '@anum/contracts';
import { apiBaseUrl, apiRequest, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import {
  auditQuery,
  exportFilename,
  mapAuditPage,
  mapAuditRecord,
  mapMembersSummary,
  mapPermissions,
  mapWorkspace,
  memberQuery,
  type ApiAuditPage,
  type ApiAuditRecord,
  type ApiMembersSummary,
  type ApiWorkspace,
  type ApiWorkspacePermissions,
  type InvitationStatusFilter,
  type MemberStatusFilter,
} from './audit';
import {
  invitationBody,
  limitsBody,
  mapAcceptedInvitation,
  mapBudgetOverview,
  mapCreatedInvitation,
  mapInvitation,
  mapMember,
  type ApiBudgetOverview,
  type ApiInvitation,
  type ApiInvitationAccepted,
  type ApiInvitationCreated,
  type ApiMember,
} from './admin';
import { approvalPolicyBody, mapApprovalPolicy, type ApiApprovalPolicy, type PolicyDraft } from './policy';

/** Any member may read the workspace approval policy (docs/approvals-and-risk.md). */
export async function getApprovalPolicy(): Promise<WorkspaceApprovalPolicy> {
  return mapApprovalPolicy(await apiRequest<ApiApprovalPolicy>('/api/v1/approval-policy', { method: 'GET' }));
}

/** Owners only: the API answers 403 for anyone else and audits every change. */
export async function setApprovalPolicy(draft: PolicyDraft): Promise<WorkspaceApprovalPolicy> {
  return mapApprovalPolicy(await apiRequest<ApiApprovalPolicy>('/api/v1/approval-policy', { method: 'PUT', body: JSON.stringify(approvalPolicyBody(draft)) }));
}

const id = encodeURIComponent;

export async function getMembers(): Promise<WorkspaceMember[]> {
  return (await apiRequest<ApiMember[]>('/api/v1/workspace-members', { method: 'GET' })).map(mapMember);
}

export async function changeMemberRole(userId: string, role: WorkspaceRole): Promise<WorkspaceMember> {
  return mapMember(await apiRequest<ApiMember>(`/api/v1/workspace-members/${id(userId)}/role`, { method: 'PUT', body: JSON.stringify({ role }) }));
}

export async function setMemberActive(userId: string, active: boolean): Promise<WorkspaceMember> {
  return mapMember(await apiRequest<ApiMember>(`/api/v1/workspace-members/${id(userId)}/${active ? 'reactivate' : 'deactivate'}`, { method: 'POST' }));
}

/** One page of members (search, status and role filters); the next cursor comes from `X-Next-Cursor`. */
export async function getMembersPage(options: { q: string; status: MemberStatusFilter; role: WorkspaceRole | 'all'; cursor?: string | null }): Promise<MemberPage> {
  const response = await fetch(`${apiBaseUrl}/api/v1/workspace-members?${memberQuery(options)}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const items = (await response.json() as ApiMember[]).map(mapMember);
  return { items, nextCursor: response.headers.get('x-next-cursor') || null };
}

export async function getMembersSummary(): Promise<MembersSummary> {
  return mapMembersSummary(await apiRequest<ApiMembersSummary>('/api/v1/workspace-members/summary', { method: 'GET' }));
}

export async function getInvitations(status: InvitationStatusFilter = 'all'): Promise<WorkspaceInvitation[]> {
  const query = status === 'all' ? '' : `?status=${status}`;
  return (await apiRequest<ApiInvitation[]>(`/api/v1/workspace-invitations${query}`, { method: 'GET' })).map(mapInvitation);
}

/** New token for a pending or expired invitation; the old token stops working at once. */
export async function resendInvitation(invitationId: string, ttlHours?: number): Promise<CreatedInvitation> {
  return mapCreatedInvitation(await apiRequest<ApiInvitationCreated>(`/api/v1/workspace-invitations/${id(invitationId)}/resend`, { method: 'POST', body: JSON.stringify(ttlHours ? { ttl_hours: ttlHours } : {}) }));
}

// ------------------------------------------------------------------ audit log

export async function getAuditPage(filters: AuditFilters, cursor?: string | null, limit?: number): Promise<AuditPage> {
  return mapAuditPage(await apiRequest<ApiAuditPage>(`/api/v1/audit-records?${auditQuery(filters, { cursor, limit })}`, { method: 'GET' }));
}

export async function getAuditActions(): Promise<string[]> {
  return (await apiRequest<{ actions: string[] }>('/api/v1/audit-records/actions', { method: 'GET' })).actions;
}

export async function getAuditRecord(recordId: string): Promise<AuditRecord> {
  return mapAuditRecord(await apiRequest<ApiAuditRecord>(`/api/v1/audit-records/${id(recordId)}`, { method: 'GET' }));
}

/** Download the filtered audit log; resolves to the saved file name. */
export async function downloadAuditExport(filters: AuditFilters, format: 'csv' | 'json'): Promise<string> {
  const response = await fetch(`${apiBaseUrl}/api/v1/audit/export?${auditQuery(filters, { format })}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response, 'Audit export failed');
  const name = exportFilename(response.headers.get('content-disposition'), format);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return name;
}

// ------------------------------------------------------------------ workspace lifecycle

export async function getCurrentWorkspace(): Promise<WorkspaceInfo> {
  return mapWorkspace(await apiRequest<ApiWorkspace>('/api/v1/workspaces/current', { method: 'GET' }));
}

export async function getWorkspacePermissions(): Promise<WorkspacePermissions> {
  return mapPermissions(await apiRequest<ApiWorkspacePermissions>('/api/v1/workspaces/current/permissions', { method: 'GET' }));
}

export async function renameWorkspace(name: string): Promise<WorkspaceInfo> {
  return mapWorkspace(await apiRequest<ApiWorkspace>('/api/v1/workspaces/current', { method: 'PATCH', body: JSON.stringify({ name }) }));
}

export async function archiveWorkspace(confirmName: string): Promise<WorkspaceInfo> {
  return mapWorkspace(await apiRequest<ApiWorkspace>('/api/v1/workspaces/current/archive', { method: 'POST', body: JSON.stringify({ confirm_name: confirmName }) }));
}

export async function restoreWorkspace(): Promise<WorkspaceInfo> {
  return mapWorkspace(await apiRequest<ApiWorkspace>('/api/v1/workspaces/current/restore', { method: 'POST' }));
}

/** A new workspace in this organization, with the caller as its first owner. */
export async function createWorkspace(name: string, workspaceId: string | null): Promise<WorkspaceInfo> {
  return mapWorkspace(await apiRequest<ApiWorkspace>('/api/v1/organization/workspaces', { method: 'POST', body: JSON.stringify(workspaceId ? { name, workspace_id: workspaceId } : { name }) }));
}

export async function createInvitation(request: InvitationRequest): Promise<CreatedInvitation> {
  return mapCreatedInvitation(await apiRequest<ApiInvitationCreated>('/api/v1/workspace-invitations', { method: 'POST', body: JSON.stringify(invitationBody(request)) }));
}

export async function revokeInvitation(invitationId: string): Promise<WorkspaceInvitation> {
  return mapInvitation(await apiRequest<ApiInvitation>(`/api/v1/workspace-invitations/${id(invitationId)}/revoke`, { method: 'POST' }));
}

/**
 * Redeem a token. The API accepts it into the workspace named by `x-workspace-id`, so an
 * invitation link's workspace overrides the one this client normally selects.
 */
export async function acceptInvitation(token: string, workspaceId?: string | null, tenantId?: string | null): Promise<AcceptedInvitation> {
  const headers: Record<string, string> = { ...(workspaceId ? { 'x-workspace-id': workspaceId } : {}), ...(tenantId ? { 'x-tenant-id': tenantId } : {}) };
  return mapAcceptedInvitation(await apiRequest<ApiInvitationAccepted>('/api/v1/workspace-invitations/accept', {
    method: 'POST',
    body: JSON.stringify({ token }),
    ...(Object.keys(headers).length ? { headers } : {}),
  }));
}

export async function getModelBudgets(): Promise<ModelBudgetOverview> {
  return mapBudgetOverview(await apiRequest<ApiBudgetOverview>('/api/v1/model-budgets', { method: 'GET' }));
}

export async function setModelBudget(scope: BudgetScope, limits: ModelBudgetLimits): Promise<ModelBudgetOverview> {
  return mapBudgetOverview(await apiRequest<ApiBudgetOverview>(`/api/v1/model-budgets/${scope}`, { method: 'PUT', body: JSON.stringify(limitsBody(limits)) }));
}
