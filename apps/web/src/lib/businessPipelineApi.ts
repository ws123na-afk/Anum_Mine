// The Business prospecting pipeline and outreach outbox over the live API
// (/api/v1/business/pipeline and /api/v1/business/outreach, docs/business.md#prospecting-pipeline).
import type {
  BusinessDraft, BusinessPage, BusinessSignal, LeadProfile, OutboxItem, OutboxStatus, OutreachOverview, PipelineRun, PipelineRunDetail,
  PipelineSettings, ReadyDraft, ReplyEvent, RunKind, SignalStatus, TargetProfile, TargetProfileInput,
} from '@anum/contracts';
import { apiRequest } from './api';

const PIPE = '/api/v1/business/pipeline';
const OUT = '/api/v1/business/outreach';
const json = (method: string, body?: unknown): RequestInit => ({ method, headers: { 'content-type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
const id = (value: string) => encodeURIComponent(value);
const get = { method: 'GET' } as const;

export const listSignals = () => apiRequest<BusinessSignal[]>(`${PIPE}/signals`, get);
export const decideSignals = (decisions: Record<string, SignalStatus>) => apiRequest<BusinessSignal[]>(`${PIPE}/signals`, json('PUT', { decisions }));
export const getPipelineSettings = () => apiRequest<PipelineSettings>(`${PIPE}/settings`, get);
export const savePipelineSettings = (body: Partial<Pick<PipelineSettings, 'fetches_per_day' | 'pages_per_lead' | 'sending_enabled' | 'daily_quota' | 'max_bounce_rate_pct' | 'follow_up_max' | 'follow_up_min_days'>>) =>
  apiRequest<PipelineSettings>(`${PIPE}/settings`, json('PUT', body));
export const listTargets = () => apiRequest<TargetProfile[]>(`${PIPE}/targets`, get);
export const saveTarget = (productId: string, body: TargetProfileInput) => apiRequest<TargetProfile>(`${PIPE}/products/${id(productId)}/target`, json('PUT', body));

export interface RunRequest { kind: RunKind; product_id?: string | null; terms?: string[]; places?: string[]; max_queries?: number; max_new_leads?: number; lead_ids?: string[] }
export const estimateRun = (body: RunRequest) => apiRequest<PipelineRun>(`${PIPE}/runs/estimate`, json('POST', body));
export const confirmRun = (runId: string) => apiRequest<PipelineRun>(`${PIPE}/runs/${id(runId)}/confirm`, json('POST'));
export const stopRun = (runId: string) => apiRequest<PipelineRun>(`${PIPE}/runs/${id(runId)}/stop`, json('POST'));
export const listRuns = (limit = 20, offset = 0) => apiRequest<BusinessPage<PipelineRun>>(`${PIPE}/runs?limit=${limit}&offset=${offset}`, get);
export const getRun = (runId: string) => apiRequest<PipelineRunDetail>(`${PIPE}/runs/${id(runId)}`, get);
export const getLeadProfile = (leadId: string) => apiRequest<LeadProfile>(`${PIPE}/leads/${id(leadId)}/profile`, get);

export const getOutreach = (days = 30) => apiRequest<OutreachOverview>(`${OUT}?days=${days}`, get);
export const listOutbox = (q: { status?: OutboxStatus | ''; limit: number; offset: number }) =>
  apiRequest<BusinessPage<OutboxItem>>(`${OUT}/items?limit=${q.limit}&offset=${q.offset}${q.status ? `&status=${q.status}` : ''}`, get);
export const listReady = () => apiRequest<BusinessPage<ReadyDraft>>(`${OUT}/ready?limit=50`, get);
export const queueDraft = (draftId: string, body: { follow_up_of?: string | null; send_after_days?: number | null } = {}) =>
  apiRequest<OutboxItem>(`${OUT}/drafts/${id(draftId)}/queue`, json('POST', body));
export const requestApproval = (itemId: string) => apiRequest<OutboxItem>(`${OUT}/items/${id(itemId)}/request-approval`, json('POST'));
export const cancelItem = (itemId: string, reason: string) => apiRequest<OutboxItem>(`${OUT}/items/${id(itemId)}/cancel`, json('POST', { reason }));
export const followUpDraft = (itemId: string) => apiRequest<BusinessDraft>(`${OUT}/items/${id(itemId)}/follow-up`, json('POST'));
export const recordReply = (leadId: string, body: { channel: string; kind: 'reply' | 'bounce'; text: string }) =>
  apiRequest<ReplyEvent>(`${OUT}/leads/${id(leadId)}/replies`, json('POST', body));
export const setPause = (on: boolean, reason = '') => apiRequest<{ paused: boolean; paused_reason: string; paused_at: string | null }>(`${OUT}/pause`, json('POST', { on, reason }));
