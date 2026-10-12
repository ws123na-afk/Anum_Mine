// Tyre planning over the live API (docs/tyre-planning.md#api). The server checks every role.
import { apiBaseUrl, apiRequest, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import { forecastQuery, scheduleBody, type AlertPage, type Assumptions, type Dashboard, type DeliveryResult, type DiscoveryView, type ForecastPage, type Page, type PreviewResult, type PurchasePlan, type Report, type ReportKind, type ReportSchedule, type ReportSummary, type ScheduleDraft, type TyreAlert, type TyreMapping, type TyreSetup } from './tyre';

const BASE = '/api/v1/tyre';
const json = (body: unknown) => JSON.stringify(body);

export const getSetup = () => apiRequest<TyreSetup>(`${BASE}/setup`, { method: 'GET' });
export const saveConnection = (body: Record<string, string>) => apiRequest<TyreSetup>(`${BASE}/connection`, { method: 'PUT', body: json(body) });
export const testConnection = () => apiRequest<{ ok: boolean; message: string; setup: TyreSetup }>(`${BASE}/connection/test`, { method: 'POST' });
export async function deleteConnection(): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${BASE}/connection`, { method: 'DELETE', headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
}
export const discover = () => apiRequest<DiscoveryView>(`${BASE}/discover`, { method: 'POST' });
export const getPreset = (name: string) => apiRequest<TyreMapping>(`${BASE}/presets/${encodeURIComponent(name)}`, { method: 'GET' });
export const saveMapping = (mapping: TyreMapping) => apiRequest<TyreSetup>(`${BASE}/mapping`, { method: 'PUT', body: json(mapping) });
export const previewMapping = (mapping: TyreMapping) => apiRequest<PreviewResult>(`${BASE}/mapping/preview`, { method: 'POST', body: json(mapping) });
export const saveAssumptions = (value: Assumptions) => apiRequest<TyreSetup>(`${BASE}/assumptions`, { method: 'PUT', body: json(value) });
export const sync = () => apiRequest<{ setup: TyreSetup; new_alerts: number }>(`${BASE}/sync`, { method: 'POST' });

export const getDashboard = () => apiRequest<Dashboard>(`${BASE}/dashboard`, { method: 'GET' });
export const getForecasts = (filters: Parameters<typeof forecastQuery>[0]) => apiRequest<ForecastPage>(`${BASE}/forecasts?${forecastQuery(filters)}`, { method: 'GET' });
export const getPlan = (months: number) => apiRequest<PurchasePlan>(`${BASE}/purchase-plan?months=${months}`, { method: 'GET' });

export function getAlerts(filters: { status: string; kind?: string; q?: string; limit: number; offset: number }): Promise<AlertPage> {
  const params = new URLSearchParams({ status: filters.status, limit: String(filters.limit), offset: String(filters.offset) });
  if (filters.kind) params.set('kind', filters.kind);
  if (filters.q?.trim()) params.set('q', filters.q.trim());
  return apiRequest<AlertPage>(`${BASE}/alerts?${params.toString()}`, { method: 'GET' });
}
export const acknowledgeAlert = (id: string) => apiRequest<TyreAlert>(`${BASE}/alerts/${encodeURIComponent(id)}/acknowledge`, { method: 'POST' });
export const reopenAlert = (id: string) => apiRequest<TyreAlert>(`${BASE}/alerts/${encodeURIComponent(id)}/reopen`, { method: 'POST' });

export const createReport = (kind: ReportKind, from?: string, to?: string) =>
  apiRequest<Report>(`${BASE}/reports`, { method: 'POST', body: json({ kind, period_from: from || null, period_to: to || null }) });
export const listReports = (limit: number, offset: number) => apiRequest<Page<ReportSummary>>(`${BASE}/reports?limit=${limit}&offset=${offset}`, { method: 'GET' });
export async function deleteReport(id: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${BASE}/reports/${encodeURIComponent(id)}`, { method: 'DELETE', headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
}
export const deliverReport = (id: string, channel: string, to: string, format: 'pdf' | 'csv') =>
  apiRequest<DeliveryResult>(`${BASE}/reports/${encodeURIComponent(id)}/deliver`, { method: 'POST', body: json({ channel, to, format }) });

export const listSchedules = () => apiRequest<ReportSchedule[]>(`${BASE}/report-schedules`, { method: 'GET' });
export const createSchedule = (draft: ScheduleDraft) => apiRequest<ReportSchedule>(`${BASE}/report-schedules`, { method: 'POST', body: json(scheduleBody(draft)) });
export async function deleteSchedule(id: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${BASE}/report-schedules/${encodeURIComponent(id)}`, { method: 'DELETE', headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
}

/** Download a server-rendered file (report export, plan or forecast CSV) with the caller's auth. */
export async function download(path: string, fallbackName: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${BASE}${path}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const blob = await response.blob();
  const match = /filename="([^"]+)"/.exec(response.headers.get('content-disposition') ?? '');
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = match?.[1] ?? fallbackName; document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
