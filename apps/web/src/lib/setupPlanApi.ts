// Setup plans over the live API (docs/setup-plan.md). Every call names the current tenant and
// workspace through the shared request's headers.
import type { PilotCheckUpdate, PilotChecks, SetupPlanApplyRequest, SetupPlanApplyResult, SetupPlanPhase, SetupPlanPreview, SetupPlanSummary } from '@anum/contracts';
import { apiRequest } from './api';

export const listSetupPlans = () => apiRequest<SetupPlanSummary[]>('/api/v1/setup-plans', { method: 'GET' });

export const previewSetupPlan = (id: string, phase: SetupPlanPhase, overwriteEdited: boolean) =>
  apiRequest<SetupPlanPreview>(`/api/v1/setup-plans/${encodeURIComponent(id)}/preview?phase=${phase}&overwrite_edited=${overwriteEdited}`, { method: 'GET' });

export const applySetupPlan = (id: string, body: SetupPlanApplyRequest) =>
  apiRequest<SetupPlanApplyResult>(`/api/v1/setup-plans/${encodeURIComponent(id)}/apply`, { method: 'POST', body: JSON.stringify(body) });

export const getPilotChecks = (id: string) => apiRequest<PilotChecks>(`/api/v1/setup-plans/${encodeURIComponent(id)}/checks`, { method: 'GET' });

export const recordPilotCheck = (id: string, checkId: string, body: PilotCheckUpdate) =>
  apiRequest<PilotChecks>(`/api/v1/setup-plans/${encodeURIComponent(id)}/checks/${encodeURIComponent(checkId)}`, { method: 'PUT', body: JSON.stringify(body) });
