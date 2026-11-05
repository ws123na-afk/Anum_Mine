import { apiBaseUrl, authHeaders } from './api';
import type { AuditFilters } from '@anum/contracts';
import { apiErrorFromResponse } from './errors';
import { narrowedExportQuery, parseExportRows, rowsToCsv, rowsToJson, type ExportRow } from './hqHistoryExport';

/**
 * Reads the audited audit export as JSON (narrowed on the server to the records that name `mentions` when given),
 * keeps the rows `keep` accepts and saves them as CSV or JSON. The server records `audit.export`, with the
 * mentioned ids, before it streams, so this download is audited like any other.
 */
export async function downloadNarrowedExport(filters: AuditFilters, keep: (row: ExportRow) => boolean, name: string,
  options: { mentions?: readonly string[] | null; format?: 'csv' | 'json' } = {}): Promise<{ name: string; rows: number }> {
  const response = await fetch(`${apiBaseUrl}/api/v1/audit/export?${narrowedExportQuery(filters, options.mentions ?? null)}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response, 'Audit export failed');
  const rows = parseExportRows(await response.json()).filter(keep);
  if (rows.length === 0) return { name: '', rows: 0 };
  const json = options.format === 'json';
  const url = URL.createObjectURL(new Blob([json ? rowsToJson(rows) : rowsToCsv(rows)], { type: json ? 'application/json' : 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return { name, rows: rows.length };
}
