// Workspace members, invitations and model budgets over the live API.
// Routes: docs/identity.md (Invitations and Membership Management), docs/model-gateway.md (Monthly budgets).
import type {
  AcceptedInvitation,
  BudgetScope,
  CreatedInvitation,
  InvitationRequest,
  ModelBudgetLimits,
  ModelBudgetOverview,
  WorkspaceInvitation,
  WorkspaceMember,
  WorkspaceRole,
} from '@anum/contracts';
import { apiRequest } from './api';
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

export async function getInvitations(): Promise<WorkspaceInvitation[]> {
  return (await apiRequest<ApiInvitation[]>('/api/v1/workspace-invitations', { method: 'GET' })).map(mapInvitation);
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
export async function acceptInvitation(token: string, workspaceId?: string | null): Promise<AcceptedInvitation> {
  return mapAcceptedInvitation(await apiRequest<ApiInvitationAccepted>('/api/v1/workspace-invitations/accept', {
    method: 'POST',
    body: JSON.stringify({ token }),
    ...(workspaceId ? { headers: { 'x-workspace-id': workspaceId } } : {}),
  }));
}

export async function getModelBudgets(): Promise<ModelBudgetOverview> {
  return mapBudgetOverview(await apiRequest<ApiBudgetOverview>('/api/v1/model-budgets', { method: 'GET' }));
}

export async function setModelBudget(scope: BudgetScope, limits: ModelBudgetLimits): Promise<ModelBudgetOverview> {
  return mapBudgetOverview(await apiRequest<ApiBudgetOverview>(`/api/v1/model-budgets/${scope}`, { method: 'PUT', body: JSON.stringify(limitsBody(limits)) }));
}
