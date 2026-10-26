import { apiBaseUrl, authHeaders } from './api';
import type { AuditFilters } from '@anum/contracts';
import { auditQuery } from './audit';
import { apiErrorFromResponse } from './errors';
import { parseExportRows, rowsToCsv, type ExportRow } from './hqHistoryExport';

/**
 * Reads the audited audit export as JSON, keeps the rows `keep` accepts and saves them as CSV.
 * The server records `audit.export` before it streams, so this download is audited like any other.
 */
export async function downloadNarrowedExport(filters: AuditFilters, keep: (row: ExportRow) => boolean, name: string): Promise<{ name: string; rows: number }> {
  const response = await fetch(`${apiBaseUrl}/api/v1/audit/export?${auditQuery(filters, { format: 'json' })}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response, 'Audit export failed');
  const rows = parseExportRows(await response.json()).filter(keep);
  if (rows.length === 0) return { name: '', rows: 0 };
  const url = URL.createObjectURL(new Blob([rowsToCsv(rows)], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return { name, rows: rows.length };
}
