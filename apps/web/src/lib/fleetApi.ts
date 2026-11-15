// Running a client fleet over the live API (docs/vehicles.md#fleet). The server checks vehicle:read for the costs and the
// report, and vehicle:manage (workspace owners) for the import.
import type { FleetCosts, FleetImportApply, FleetImportFile, FleetImportKind, FleetImportPreview, FleetImportResult, FleetReportDraft, FleetReportDraftInput,
  FleetReportInput, FleetReportSendInput, FleetReportSendResult } from '@anum/contracts';
import { apiBaseUrl, apiRequest, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import { fileNameFromDisposition, saveBlob } from './fileSave';
import { templateCsv } from './fleet';

const BASE = '/api/v1/vehicles/fleet';

export const fleetCosts = (month: string) => apiRequest<FleetCosts>(`${BASE}/costs?month=${encodeURIComponent(month)}`, { method: 'GET' });
export const previewImport = (body: FleetImportFile) => apiRequest<FleetImportPreview>(`${BASE}/import/preview`, { method: 'POST', body: JSON.stringify(body) });
export const applyImport = (body: FleetImportApply) => apiRequest<FleetImportResult>(`${BASE}/import/apply`, { method: 'POST', body: JSON.stringify(body) });

// The month's report kept between visits (vehicle:read to read; vehicle:write to save, write the AI summary and send).
export const reportDraft = (month: string) => apiRequest<FleetReportDraft>(`${BASE}/report/draft?month=${encodeURIComponent(month)}`, { method: 'GET' });
export const saveReportDraft = (body: FleetReportDraftInput) => apiRequest<FleetReportDraft>(`${BASE}/report/draft`, { method: 'PUT', body: JSON.stringify(body) });
export const writeReportSummary = (month: string, language: 'en' | 'ar') =>
  apiRequest<FleetReportDraft>(`${BASE}/report/summary`, { method: 'POST', body: JSON.stringify({ month, language }) });
export const removeReportSummary = (month: string, language: 'en' | 'ar') =>
  apiRequest<FleetReportDraft>(`${BASE}/report/summary?month=${encodeURIComponent(month)}&language=${language}`, { method: 'DELETE' });
/** Send the saved report through the delivery path: it waits for an approval in Approvals. */
export const sendReport = (body: FleetReportSendInput) => apiRequest<FleetReportSendResult>(`${BASE}/report/send`, { method: 'POST', body: JSON.stringify(body) });

/** Download the monthly report the server makes (PDF or Excel); the browser saves it under the server's file name. */
export async function downloadReport(body: FleetReportInput): Promise<string> {
  const response = await fetch(`${apiBaseUrl}${BASE}/report`, { method: 'POST', headers: { ...(await authHeaders()), 'content-type': 'application/json' }, body: JSON.stringify(body) });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const name = fileNameFromDisposition(response.headers.get('content-disposition'), `fleet-report-${body.month}-${body.language}.${body.format}`);
  saveBlob(await response.blob(), name);
  return name;
}

/** A CSV template of one kind's columns, for the client to fill in. */
export function saveTemplate(kind: FleetImportKind): void {
  // A byte order mark so Excel reads the file as UTF-8.
  saveBlob(new Blob(['\ufeff', templateCsv(kind)], { type: 'text/csv;charset=utf-8' }), `anum-fleet-${kind}-template.csv`);
}
