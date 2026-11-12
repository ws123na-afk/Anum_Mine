// Work attendance over the live API (docs/attendance.md#api). Every record is the caller's own; with `person`
// the caller reads a colleague's records that colleague shared. The server checks attendance:use on every route.
import type {
  AttendanceCorrection, AttendanceCorrectionInput, AttendanceDailyReport, AttendanceDailyReportInput, AttendanceDay, AttendanceDraft,
  AttendanceEvent, AttendanceEventKind, AttendancePage, AttendancePeriod, AttendanceProfile, AttendanceProfileInput, AttendanceRecordResult,
  AttendanceShare, AttendanceShares, AttendanceSite, AttendanceSiteInput, AttendanceSiteVisit, AttendanceTeam, AttendanceToday, AttendanceWorkspaceSettings,
  TeamPerson,
} from '@anum/contracts';
import { apiBaseUrl, apiRequest, apiRequestVoid, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import { fileNameFromDisposition, saveBlob } from './fileSave';

const BASE = '/api/v1/attendance';
const get = <T>(path: string) => apiRequest<T>(`${BASE}${path}`, { method: 'GET' });
const send = <T>(method: string, path: string, body?: unknown) => apiRequest<T>(`${BASE}${path}`, { method, body: body === undefined ? undefined : JSON.stringify(body) });
const id = encodeURIComponent;

export function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== null && value !== undefined && value !== '') search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : '';
}

export const getProfile = (person?: string | null) => get<AttendanceProfile>(`/profile${query({ person })}`);
export const saveProfile = (body: AttendanceProfileInput) => send<AttendanceProfile>('PUT', '/profile', body);
export const setTracking = (action: 'consent' | 'withdraw' | 'pause' | 'resume') => send<AttendanceProfile>('POST', '/tracking', { action });

export const listSites = (person?: string | null) => get<AttendanceSite[]>(`/sites${query({ person })}`);
export const createSite = (body: AttendanceSiteInput) => send<AttendanceSite>('POST', '/sites', body);
export const updateSite = (siteId: string, body: Partial<AttendanceSiteInput> & { archived?: boolean }) => send<AttendanceSite>('PATCH', `/sites/${id(siteId)}`, body);
export const archiveSite = (siteId: string) => apiRequestVoid(`${BASE}/sites/${id(siteId)}`, { method: 'DELETE' });

export const getToday = (person?: string | null) => get<AttendanceToday>(`/today${query({ person })}`);
export const check = (kind: AttendanceEventKind, idempotencyKey: string, siteId?: string | null) =>
  send<AttendanceRecordResult>('POST', '/check', { kind, site_id: siteId ?? null, idempotency_key: idempotencyKey });
export const listEvents = (from: string, to: string, person?: string | null, includeChanged = false) =>
  get<AttendanceEvent[]>(`/events${query({ from, to, person, include_changed: includeChanged || null })}`);
export const listDays = (from: string, to: string, person?: string | null) => get<AttendanceDay[]>(`/days${query({ from, to, person })}`);
export const getSummary = (period: 'day' | 'week' | 'month', date: string, person?: string | null) =>
  get<AttendancePeriod>(`/summary${query({ period, date, person })}`);
export const siteHistory = (from: string, to: string, siteId?: string | null, person?: string | null) =>
  get<AttendanceSiteVisit[]>(`/site-history${query({ from, to, site_id: siteId, person })}`);

export const addCorrection = (body: AttendanceCorrectionInput) => send<AttendanceCorrection>('POST', '/corrections', body);
export const listCorrections = (limit: number, offset: number, person?: string | null) =>
  get<AttendancePage<AttendanceCorrection>>(`/corrections${query({ limit, offset, person })}`);

export const listReports = (q: { from?: string; to?: string; q?: string; limit: number; offset: number; person?: string | null }) =>
  get<AttendancePage<AttendanceDailyReport>>(`/daily-reports${query({ ...q })}`);
export const getReport = (day: string, person?: string | null) => get<AttendanceDailyReport>(`/daily-reports/${id(day)}${query({ person })}`);
export const saveReport = (day: string, body: AttendanceDailyReportInput) => send<AttendanceDailyReport>('PUT', `/daily-reports/${id(day)}`, body);
export const deleteReport = (day: string) => apiRequestVoid(`${BASE}/daily-reports/${id(day)}`, { method: 'DELETE' });
export const draftSummary = (period: 'day' | 'week', date: string, language: 'en' | 'ar') =>
  send<AttendanceDraft>('POST', '/daily-reports/draft', { period, date, language });

export const listShares = () => get<AttendanceShares>('/shares');
export const createShare = (viewerUserId: string, includeReports: boolean) =>
  send<AttendanceShare>('POST', '/shares', { viewer_user_id: viewerUserId, include_reports: includeReports });
export const revokeShare = (shareId: string) => apiRequestVoid(`${BASE}/shares/${id(shareId)}`, { method: 'DELETE' });
/** Active colleagues (Team people, readable by every member) for the share picker. */
export const listPeople = () => apiRequest<TeamPerson[]>('/api/v1/team/people', { method: 'GET' });

/** Downloads a CSV or PDF report and returns the saved file name. */
export async function downloadReport(format: 'csv' | 'pdf', from: string, to: string, person?: string | null): Promise<string> {
  const response = await fetch(`${apiBaseUrl}${BASE}/export.${format}${query({ from, to, person })}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const name = fileNameFromDisposition(response.headers.get('content-disposition'), `attendance-${from}-${to}.${format}`);
  return saveBlob(await response.blob(), name);
}

/** The workspace manager view's switch (docs/attendance.md#manager-view): every member may read it. */
export const getWorkspaceSettings = () => get<AttendanceWorkspaceSettings>('/workspace-settings');
export const saveWorkspaceSettings = (managerView: boolean, expectedVersion: number) =>
  send<AttendanceWorkspaceSettings>('PUT', '/workspace-settings', { manager_view: managerView, expected_version: expectedVersion });
/** Everyone who keeps attendance in the workspace, one day, a page at a time (owners and organisation administrators). */
export const getManagerView = (date: string | null, limit: number, offset: number) => get<AttendanceTeam>(`/manager${query({ date, limit, offset })}`);

/** Downloads the whole workspace's days as one CSV and returns the saved file name. */
export async function downloadManagerCsv(from: string, to: string): Promise<string> {
  const response = await fetch(`${apiBaseUrl}${BASE}/manager/export.csv${query({ from, to })}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const name = fileNameFromDisposition(response.headers.get('content-disposition'), `attendance-workspace-${from}-${to}.csv`);
  return saveBlob(await response.blob(), name);
}

/** The caller and each colleague who shares their record with them (docs/attendance.md#team-attendance). */
export const getTeam = (date?: string | null, limit = 50, offset = 0) => get<AttendanceTeam>(`/team${query({ date, limit, offset })}`);
