// Team work, notifications, assistants and organisations over the live API
// (docs/team-work.md, docs/customer-organisations.md). Every permission is the API's answer.
import type {
  ActivityPage,
  AssistantBody,
  AssistantProfile,
  AssistantTemplate,
  ChecklistEntry,
  CommandPreview,
  CreatedOrganisation,
  InboundMessageBody,
  MemberProfileBody,
  MyOrganisation,
  NotificationPage,
  OrganisationBranding,
  OrganisationCreateBody,
  OrganisationPlan,
  OrganisationSummary,
  ReissuedInvitation,
  RouteResult,
  Task,
  TeamPerson,
  UserNotification,
  WorkAssignee,
  WorkAttachment,
  WorkComment,
  WorkItemCreateBody,
  WorkItemDetail,
  WorkItemPage,
  WorkItemUpdateBody,
  WorkPriority,
  Workload,
} from '@anum/contracts';
import { apiBaseUrl, apiRequest, apiRequestVoid, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';

const id = encodeURIComponent;
const get = <T>(path: string) => apiRequest<T>(path, { method: 'GET' });
const send = <T>(method: string, path: string, body?: unknown) => apiRequest<T>(path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });

// work items
export const listWork = (path: string) => get<WorkItemPage>(path);
export const getWork = (itemId: string) => get<WorkItemDetail>(`/api/v1/work-items/${id(itemId)}`);
export const createWork = (body: WorkItemCreateBody) => send<WorkItemDetail>('POST', '/api/v1/work-items', body);
export const updateWork = (itemId: string, body: WorkItemUpdateBody) => send<WorkItemDetail>('PATCH', `/api/v1/work-items/${id(itemId)}`, body);
export const deleteWork = (itemId: string) => apiRequestVoid(`/api/v1/work-items/${id(itemId)}`, { method: 'DELETE' });
export const addComment = (itemId: string, body: string) => send<WorkComment>('POST', `/api/v1/work-items/${id(itemId)}/comments`, { body });
export const deleteComment = (itemId: string, commentId: string) => apiRequestVoid(`/api/v1/work-items/${id(itemId)}/comments/${id(commentId)}`, { method: 'DELETE' });
export const addChecklist = (itemId: string, label: string) => send<ChecklistEntry>('POST', `/api/v1/work-items/${id(itemId)}/checklist`, { label });
export const setChecklist = (itemId: string, entryId: string, done: boolean) => send<ChecklistEntry>('PATCH', `/api/v1/work-items/${id(itemId)}/checklist/${id(entryId)}`, { done });
export const deleteChecklist = (itemId: string, entryId: string) => apiRequestVoid(`/api/v1/work-items/${id(itemId)}/checklist/${id(entryId)}`, { method: 'DELETE' });
export const attachFile = (itemId: string, fileId: string) => send<WorkAttachment>('POST', `/api/v1/work-items/${id(itemId)}/attachments`, { file_id: fileId });
export const detachFile = (itemId: string, attachmentId: string) => apiRequestVoid(`/api/v1/work-items/${id(itemId)}/attachments/${id(attachmentId)}`, { method: 'DELETE' });
export const handOff = (itemId: string) => send<{ work_item: WorkItemDetail; task: Task & { assistant_id?: string | null } }>('POST', `/api/v1/work-items/${id(itemId)}/hand-off`);
export const getWorkload = () => get<Workload>('/api/v1/work-items/workload');
export const getActivity = (cursor?: string | null, limit = 30) => get<ActivityPage>(`/api/v1/team/activity?limit=${limit}${cursor ? `&cursor=${id(cursor)}` : ''}`);
export const getPeople = () => get<TeamPerson[]>('/api/v1/team/people');
export const saveProfile = (userId: string, body: MemberProfileBody) => send<TeamPerson>('PUT', `/api/v1/team/people/${id(userId)}`, body);
export const parseCommand = (text: string) => send<CommandPreview>('POST', '/api/v1/team/commands/parse', { text, timezone: browserTimeZone() });
export const runCommand = (text: string, assignee: WorkAssignee | null, priority: WorkPriority = 'normal') =>
  send<WorkItemDetail>('POST', '/api/v1/team/commands', { text, timezone: browserTimeZone(), priority, ...(assignee ? { assignee } : {}) });

/** This browser's IANA time zone, so "by Thursday" means Thursday here. */
export function browserTimeZone(): string | null {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || null; } catch { return null; }
}

// notifications
export const getNotifications = (cursor?: string | null, limit = 20) => get<NotificationPage>(`/api/v1/notifications?limit=${limit}${cursor ? `&cursor=${id(cursor)}` : ''}`);
export const readNotification = (notificationId: string) => send<UserNotification>('POST', `/api/v1/notifications/${id(notificationId)}/read`);
export const readAllNotifications = () => send<{ updated: number }>('POST', '/api/v1/notifications/read-all');

// assistants
export const getAssistants = () => get<AssistantProfile[]>('/api/v1/assistants');
export const getAssistantTemplates = () => get<AssistantTemplate[]>('/api/v1/assistants/templates');
export const createAssistant = (body: AssistantBody) => send<AssistantProfile>('POST', '/api/v1/assistants', body);
export const updateAssistant = (assistantId: string, body: AssistantBody) => send<AssistantProfile>('PUT', `/api/v1/assistants/${id(assistantId)}`, body);
export const deleteAssistant = (assistantId: string) => apiRequestVoid(`/api/v1/assistants/${id(assistantId)}`, { method: 'DELETE' });
export const routeMessage = (body: InboundMessageBody) => send<RouteResult>('POST', '/api/v1/assistants/route', body);

// organisations
export const getBranding = () => get<OrganisationBranding>('/api/v1/organization/branding');
export const setBrandingName = (displayName: string) => send<OrganisationBranding>('PUT', '/api/v1/organization/branding', { display_name: displayName });
export const deleteLogo = () => send<OrganisationBranding>('DELETE', '/api/v1/organization/branding/logo');
export async function uploadLogo(file: File): Promise<OrganisationBranding> {
  return apiRequest<OrganisationBranding>('/api/v1/organization/branding/logo', { method: 'PUT', body: file, headers: { 'content-type': file.type || 'application/octet-stream' } });
}
/** The logo as an object URL (it needs the auth headers, so an <img src> to the API would not work). */
export async function logoObjectUrl(): Promise<string | null> {
  const response = await fetch(`${apiBaseUrl}/api/v1/organization/branding/logo`, { headers: await authHeaders() });
  if (response.status === 404) return null;
  if (!response.ok) throw await apiErrorFromResponse(response);
  return URL.createObjectURL(await response.blob());
}
export const getMyOrganisations = () => get<MyOrganisation[]>('/api/v1/me/organizations');
export const getOperatedOrganisations = () => get<OrganisationSummary[]>('/api/v1/platform/organizations');
export const createOrganisation = (body: OrganisationCreateBody) => send<CreatedOrganisation>('POST', '/api/v1/platform/organizations', body);
export const setOrganisationPlan = (tenantId: string, plan: OrganisationPlan) => send<OrganisationSummary>('PATCH', `/api/v1/platform/organizations/${id(tenantId)}`, { plan });
export const reissueInvitation = (tenantId: string) => send<ReissuedInvitation>('POST', `/api/v1/platform/organizations/${id(tenantId)}/invitation`);
