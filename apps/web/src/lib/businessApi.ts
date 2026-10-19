// Business over the live API (/api/v1/business, docs/business.md). Every route checks the caller's
// role on the server; a 403 carries the API's own sentence naming who may do it.
import type {
  BusinessAccountDetail, BusinessDraft, BusinessDuplicate, BusinessKpi, BusinessLead, BusinessLeadDetail, BusinessOpportunity, BusinessPage,
  BusinessProduct, BusinessProductDetail, BusinessProject, BusinessProposal, BusinessSettings, BusinessSetup, ConsentBasis, Criterion, DraftChannel,
  ImportResult, LeadState, MilestoneStatus, OpportunityStage, ProjectStatus, ProposalContent, BusinessContact, BusinessChangeOrder, BusinessProfitability,
  BusinessUsageEntry, ChangeOrderInput, UsageKind, BusinessAccountRow, ResolutionCode,
  SupportAssignee, SupportPolicy, SupportSetup, SupportSummary, SupportTicket, TicketDetail, TicketFields, TicketReplyRequest, TicketStatus,
} from '@anum/contracts';
import { apiBaseUrl, apiRequest, apiRequestVoid, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import { ticketQuery, type TicketListQuery } from './businessSupport';

const BASE = '/api/v1/business';
const json = (method: string, body?: unknown): RequestInit => ({ method, headers: { 'content-type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
const id = (value: string) => encodeURIComponent(value);

function query(values: Record<string, string | number | readonly string[] | null | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value === null || value === undefined || value === '') continue;
    if (Array.isArray(value)) value.forEach((item) => params.append(key, item));
    else params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : '';
}

export const getSetup = () => apiRequest<BusinessSetup>(`${BASE}/setup`, { method: 'GET' });
export const saveSettings = (body: { research_runs_per_day: number; research_searches_per_day: number }) => apiRequest<BusinessSettings>(`${BASE}/settings`, json('PUT', body));
export const getKpi = (days: number) => apiRequest<BusinessKpi>(`${BASE}/kpi${query({ days })}`, { method: 'GET' });

// catalog
export const listProducts = (q: { q?: string; limit: number; offset: number }) => apiRequest<BusinessPage<BusinessProduct>>(`${BASE}/products${query(q)}`, { method: 'GET' });
export const getProduct = (productId: string) => apiRequest<BusinessProductDetail>(`${BASE}/products/${id(productId)}`, { method: 'GET' });
export const createProduct = (body: Record<string, unknown>) => apiRequest<BusinessProductDetail>(`${BASE}/products`, json('POST', body));
export const updateProduct = (productId: string, body: Record<string, unknown>) => apiRequest<BusinessProductDetail>(`${BASE}/products/${id(productId)}`, json('PATCH', body));
export const deleteProduct = (productId: string) => apiRequestVoid(`${BASE}/products/${id(productId)}`, { method: 'DELETE' });
export const addVersion = (productId: string, body: Record<string, unknown>) => apiRequest<BusinessProductDetail>(`${BASE}/products/${id(productId)}/versions`, json('POST', body));
export const updateVersion = (offerId: string, body: Record<string, unknown>) => apiRequest<BusinessProductDetail>(`${BASE}/offers/${id(offerId)}`, json('PATCH', body));
export const activateVersion = (offerId: string) => apiRequest<BusinessProductDetail>(`${BASE}/offers/${id(offerId)}/activate`, json('POST'));
export const retireVersion = (offerId: string) => apiRequest<BusinessProductDetail>(`${BASE}/offers/${id(offerId)}/retire`, json('POST'));

// leads
export const listLeads = (q: { q?: string; state?: readonly LeadState[]; sort?: string; limit: number; offset: number }) =>
  apiRequest<BusinessPage<BusinessLead>>(`${BASE}/leads${query(q)}`, { method: 'GET' });
export const getLead = (leadId: string) => apiRequest<BusinessLeadDetail>(`${BASE}/leads/${id(leadId)}`, { method: 'GET' });
export const createLead = (body: Record<string, unknown>) => apiRequest<BusinessLeadDetail>(`${BASE}/leads`, json('POST', body));
export const updateLead = (leadId: string, body: Record<string, unknown>) => apiRequest<BusinessLeadDetail>(`${BASE}/leads/${id(leadId)}`, json('PATCH', body));
export const deleteLead = (leadId: string) => apiRequestVoid(`${BASE}/leads/${id(leadId)}`, { method: 'DELETE' });
export const duplicates = (q: { name?: string; website?: string; email?: string; product_id?: string }) => apiRequest<BusinessDuplicate[]>(`${BASE}/duplicates${query(q)}`, { method: 'GET' });
export const scoreLead = (leadId: string, criteria: Partial<Record<Criterion, { score: number | null; reason: string; evidence_ids: string[] }>>) =>
  apiRequest<BusinessLeadDetail>(`${BASE}/leads/${id(leadId)}/score`, json('PUT', { criteria }));
export const transitionLead = (leadId: string, to: LeadState, reason = '') => apiRequest<BusinessLeadDetail>(`${BASE}/leads/${id(leadId)}/transition`, json('POST', { to, reason }));
export const researchLead = (leadId: string) => apiRequest<BusinessLeadDetail>(`${BASE}/leads/${id(leadId)}/research`, json('POST'));
export const addEvidence = (leadId: string, body: { url: string; title: string; excerpt: string; published_on: string | null; confidence: string }) =>
  apiRequest<BusinessLeadDetail>(`${BASE}/leads/${id(leadId)}/evidence`, json('POST', body));
export const removeEvidence = (evidenceId: string) => apiRequest<BusinessLeadDetail>(`${BASE}/evidence/${id(evidenceId)}`, { method: 'DELETE' });
export const reviewInference = (inferenceId: string, status: 'accepted' | 'rejected') => apiRequest<BusinessLeadDetail>(`${BASE}/inferences/${id(inferenceId)}/review`, json('POST', { status }));

/** A note or a contact made outside ANUM; the key makes a retried click record once. */
export function logActivity(leadId: string, kind: string, summary: string, key: string) {
  return apiRequest(`${BASE}/leads/${id(leadId)}/activities`, { ...json('POST', { kind, summary }), headers: { 'content-type': 'application/json', 'idempotency-key': key } });
}

export const updateContact = (contactId: string, body: { consent_basis?: ConsentBasis; role_title?: string }) => apiRequest<BusinessContact>(`${BASE}/contacts/${id(contactId)}`, json('PATCH', body));
export const contactDoNotContact = (contactId: string, on: boolean, reason: string) => apiRequest<BusinessContact>(`${BASE}/contacts/${id(contactId)}/do-not-contact`, json('POST', { on, reason }));
export const addContact = (accountId: string, body: Record<string, unknown>) => apiRequest<BusinessAccountDetail>(`${BASE}/accounts/${id(accountId)}/contacts`, json('POST', body));

export const createDraft = (leadId: string, channel: DraftChannel, language: 'en' | 'ar') =>
  apiRequest<{ lead: BusinessLeadDetail; draft: BusinessDraft; note: string | null }>(`${BASE}/leads/${id(leadId)}/drafts`, json('POST', { channel, language }));
export const updateDraft = (draftId: string, subject: string, body: string) => apiRequest<BusinessDraft>(`${BASE}/drafts/${id(draftId)}`, json('PATCH', { subject, body }));
export const approveDraft = (draftId: string) => apiRequest<BusinessDraft>(`${BASE}/drafts/${id(draftId)}/approve`, json('POST'));
export const discardDraft = (draftId: string) => apiRequest<BusinessDraft>(`${BASE}/drafts/${id(draftId)}/discard`, json('POST'));

export const importLeads = (csv: string, dryRun: boolean, productId?: string) =>
  apiRequest<ImportResult>(`${BASE}/import/leads`, json('POST', { csv, dry_run: dryRun, product_id: productId || null }));

// pipeline
export const listOpportunities = (q: { q?: string; stage?: readonly OpportunityStage[]; limit: number; offset: number }) =>
  apiRequest<BusinessPage<BusinessOpportunity>>(`${BASE}/opportunities${query(q)}`, { method: 'GET' });
export const getOpportunity = (opportunityId: string) => apiRequest<BusinessOpportunity>(`${BASE}/opportunities/${id(opportunityId)}`, { method: 'GET' });
export const createOpportunity = (body: Record<string, unknown>) => apiRequest<BusinessOpportunity>(`${BASE}/opportunities`, json('POST', body));
export const updateOpportunity = (opportunityId: string, body: Record<string, unknown>) => apiRequest<BusinessOpportunity>(`${BASE}/opportunities/${id(opportunityId)}`, json('PATCH', body));
export const moveOpportunity = (opportunityId: string, to: OpportunityStage, reason = '') => apiRequest<BusinessOpportunity>(`${BASE}/opportunities/${id(opportunityId)}/stage`, json('POST', { to, reason }));

// proposals
export const listProposals = (q: { q?: string; status?: readonly string[]; limit: number; offset: number }) =>
  apiRequest<BusinessPage<BusinessProposal>>(`${BASE}/proposals${query(q)}`, { method: 'GET' });
export const getProposal = (proposalId: string) => apiRequest<BusinessProposal>(`${BASE}/proposals/${id(proposalId)}`, { method: 'GET' });
export const createProposal = (opportunityId: string, title: string) => apiRequest<BusinessProposal>(`${BASE}/proposals`, json('POST', { opportunity_id: opportunityId, title }));
export const saveProposal = (proposalId: string, content: ProposalContent) => apiRequest<BusinessProposal>(`${BASE}/proposals/${id(proposalId)}/content`, json('PUT', content));
export const requestAcceptance = (proposalId: string) => apiRequest<BusinessProposal>(`${BASE}/proposals/${id(proposalId)}/request-acceptance`, json('POST'));
export const withdrawProposal = (proposalId: string, reason: string) => apiRequest<BusinessProposal>(`${BASE}/proposals/${id(proposalId)}/withdraw`, json('POST', { reason }));

/** Downloads the proposal as Markdown and returns the file name. */
export async function exportProposal(proposalId: string): Promise<string> {
  const response = await fetch(`${apiBaseUrl}${BASE}/proposals/${id(proposalId)}/export`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const match = /filename="([A-Za-z0-9._-]{1,120})"/.exec(response.headers.get('content-disposition') ?? '');
  const name = match?.[1] ?? 'proposal.md';
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return name;
}

// projects
export const listProjects = (q: { q?: string; limit: number; offset: number }) => apiRequest<BusinessPage<BusinessProject>>(`${BASE}/projects${query(q)}`, { method: 'GET' });
export const getProject = (projectId: string) => apiRequest<BusinessProject>(`${BASE}/projects/${id(projectId)}`, { method: 'GET' });
export const createProject = (proposalId: string, startOn: string | null) => apiRequest<BusinessProject>(`${BASE}/projects`, json('POST', { proposal_id: proposalId, start_on: startOn }));
export const updateProject = (projectId: string, body: { status?: ProjectStatus; start_on?: string | null }) => apiRequest<BusinessProject>(`${BASE}/projects/${id(projectId)}`, json('PATCH', body));
export const updateMilestone = (milestoneId: string, body: { status?: MilestoneStatus; paid_amount?: number | null; paid_on?: string | null; due_on?: string | null }) =>
  apiRequest<BusinessProject>(`${BASE}/milestones/${id(milestoneId)}`, json('PATCH', body));

// change orders (an owner approves with the content hash they were shown)
export const listChangeOrders = (projectId: string, q: { limit: number; offset: number }) =>
  apiRequest<BusinessPage<BusinessChangeOrder>>(`${BASE}/projects/${id(projectId)}/change-orders${query(q)}`, { method: 'GET' });
export const createChangeOrder = (projectId: string, body: ChangeOrderInput) => apiRequest<BusinessChangeOrder>(`${BASE}/projects/${id(projectId)}/change-orders`, json('POST', body));
export const updateChangeOrder = (changeId: string, body: Partial<ChangeOrderInput>) => apiRequest<BusinessChangeOrder>(`${BASE}/change-orders/${id(changeId)}`, json('PATCH', body));
export const submitChangeOrder = (changeId: string) => apiRequest<BusinessChangeOrder>(`${BASE}/change-orders/${id(changeId)}/submit`, json('POST'));
export const approveChangeOrder = (changeId: string, contentHash: string) => apiRequest<BusinessChangeOrder>(`${BASE}/change-orders/${id(changeId)}/approve`, json('POST', { content_hash: contentHash }));
export const rejectChangeOrder = (changeId: string, reason: string) => apiRequest<BusinessChangeOrder>(`${BASE}/change-orders/${id(changeId)}/reject`, json('POST', { reason }));
export const withdrawChangeOrder = (changeId: string, reason: string) => apiRequest<BusinessChangeOrder>(`${BASE}/change-orders/${id(changeId)}/withdraw`, json('POST', { reason }));

// usage and cost ledger, profitability
export const listUsage = (q: { project_id?: string; account_id?: string; kind?: readonly UsageKind[]; limit: number; offset: number }) =>
  apiRequest<BusinessPage<BusinessUsageEntry>>(`${BASE}/usage${query(q)}`, { method: 'GET' });
export const createUsage = (body: Record<string, unknown>) => apiRequest<BusinessUsageEntry>(`${BASE}/usage`, json('POST', body));
export const updateUsage = (entryId: string, body: Record<string, unknown>) => apiRequest<BusinessUsageEntry>(`${BASE}/usage/${id(entryId)}`, json('PATCH', body));
export const deleteUsage = (entryId: string) => apiRequestVoid(`${BASE}/usage/${id(entryId)}`, { method: 'DELETE' });
export const importAiUsage = (body: Record<string, unknown>) => apiRequest<BusinessUsageEntry>(`${BASE}/usage/import-ai`, json('POST', body));
export const getProfitability = (projectId: string) => apiRequest<BusinessProfitability>(`${BASE}/projects/${id(projectId)}/profitability`, { method: 'GET' });

// accounts (pickers)
export const listAccounts = (q: { q?: string; limit: number; offset: number }) => apiRequest<BusinessPage<BusinessAccountRow>>(`${BASE}/accounts${query(q)}`, { method: 'GET' });
export const getAccount = (accountId: string) => apiRequest<BusinessAccountDetail>(`${BASE}/accounts/${id(accountId)}`, { method: 'GET' });

// support tickets (/api/v1/business/support, docs/business.md#support-tickets)
const SUPPORT = `${BASE}/support`;
const ticketPath = (ticketId: string) => `${SUPPORT}/tickets/${id(ticketId)}`;

export const getSupportSetup = () => apiRequest<SupportSetup>(`${SUPPORT}/setup`, { method: 'GET' });
export const saveSupportPolicy = (policy: SupportPolicy) => apiRequest<SupportPolicy>(`${SUPPORT}/policy`, json('PUT', policy));
export const listAssignees = () => apiRequest<SupportAssignee[]>(`${SUPPORT}/assignees`, { method: 'GET' });
export const getSupportSummary = () => apiRequest<SupportSummary>(`${SUPPORT}/summary`, { method: 'GET' });
export const listTickets = (q: TicketListQuery) => apiRequest<BusinessPage<SupportTicket>>(`${SUPPORT}/tickets${ticketQuery(q)}`, { method: 'GET' });
export const createTicket = (body: TicketFields & { title: string }) => apiRequest<TicketDetail>(`${SUPPORT}/tickets`, json('POST', body));
export const createTicketFromMessage = (body: TicketFields & { source: 'email' | 'whatsapp'; ref_id: string }) => apiRequest<TicketDetail>(`${SUPPORT}/tickets/from-message`, json('POST', body));
export const getTicket = (ticketId: string) => apiRequest<TicketDetail>(ticketPath(ticketId), { method: 'GET' });
export const updateTicket = (ticketId: string, body: TicketFields) => apiRequest<TicketDetail>(ticketPath(ticketId), json('PATCH', body));
export const changeTicketStatus = (ticketId: string, body: { to: TicketStatus; resolution_code?: ResolutionCode | null; resolution_summary?: string | null; note?: string | null }) =>
  apiRequest<TicketDetail>(`${ticketPath(ticketId)}/status`, json('POST', body));
export const setEscalation = (ticketId: string, escalated: boolean, reason: string) => apiRequest<TicketDetail>(`${ticketPath(ticketId)}/escalation`, json('POST', { escalated, reason }));
export const addTicketNote = (ticketId: string, text: string, responded: boolean) => apiRequest<TicketDetail>(`${ticketPath(ticketId)}/notes`, json('POST', { text, responded }));
export const deleteTicket = (ticketId: string) => apiRequestVoid(ticketPath(ticketId), { method: 'DELETE' });
export const addReply = (ticketId: string, body: string) => apiRequest<TicketDetail>(`${ticketPath(ticketId)}/replies`, json('POST', { body }));
export const editReply = (ticketId: string, replyId: string, body: string) => apiRequest<TicketDetail>(`${ticketPath(ticketId)}/replies/${id(replyId)}`, json('PATCH', { body }));
export const discardReply = (ticketId: string, replyId: string) => apiRequest<TicketDetail>(`${ticketPath(ticketId)}/replies/${id(replyId)}`, { method: 'DELETE' });
export const requestReplyApproval = (ticketId: string, replyId: string) => apiRequest<TicketReplyRequest>(`${ticketPath(ticketId)}/replies/${id(replyId)}/request-approval`, json('POST'));
