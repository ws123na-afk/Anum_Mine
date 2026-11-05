/**
 * History CSV and JSON export for views the audit export cannot express by its plain filters (room view,
 * agent view, the "Other" type). The panel asks the existing audited export (`GET /audit/export`,
 * permission `audit:export`) for the broad filters plus, for a room or agent view, the server's
 * `mention` filter (the room and its desks, or the agent), then keeps only the rows the view shows,
 * using the same rules as the timeline (`relatesTo`, the action prefixes). Nothing is added: the rows are a subset of
 * what that export returns, so a viewer never gets more than the permission already gives.
 * Pure functions only (unit tested); the network call lives in hqHistoryExportApi.ts.
 */
import type { AuditFilters } from '@anum/contracts';
import { auditQuery } from './audit.ts';
import { CATEGORY_ACTION_PREFIX, relatesTo, type HistoryFilters, type HistoryScope } from './hqHistory.ts';

/** The export query: the audit filters, the JSON format and one `mention` per id (the server's room or agent filter). */
export function narrowedExportQuery(filters: AuditFilters, mentions: readonly string[] | null): string {
  const params = new URLSearchParams(auditQuery(filters, { format: 'json' }));
  for (const id of mentions ?? []) params.append('mention', id);
  return params.toString();
}

export const EXPORT_COLUMNS = ['id', 'created_at', 'actor', 'action', 'target', 'outcome', 'correlation_id', 'tenant_id', 'workspace_id', 'metadata'] as const;

export interface ExportRow {
  id: string; created_at: string; actor: string; action: string; target: string; outcome: string;
  correlation_id: string | null; tenant_id: string; workspace_id: string | null; metadata: Record<string, unknown>;
}

/** True when the view needs the rows narrowed on the device (room, agent, Other). */
export function needsNarrowing(filters: HistoryFilters, scope: HistoryScope): boolean {
  return scope.kind === 'room' || scope.kind === 'agent' || (scope.kind === 'office' && filters.category === 'other');
}

/** The predicate that keeps the rows of the view, or null when the server filters already match. */
export function exportNarrowing(filters: HistoryFilters, scope: HistoryScope): ((row: ExportRow) => boolean) | null {
  if (!needsNarrowing(filters, scope)) return null;
  const prefixes = Object.values(CATEGORY_ACTION_PREFIX);
  return (row) => {
    if (filters.category === 'other' && prefixes.some((prefix) => row.action.startsWith(prefix))) return false;
    return scope.kind === 'room' || scope.kind === 'agent' ? relatesTo(row, scope) : true;
  };
}

const SECRET_OR_TEXT_KEY = /prompt|transcript|turn|message|content|result|body|secret|token|password|credential|authorization|api[_-]?key/i;

/** Metadata with every key that could hold a secret or conversation text dropped, at any depth. */
export function safeMetadata(value: unknown, depth = 0): unknown {
  if (depth > 8) return null;
  if (Array.isArray(value)) return value.map((entry) => safeMetadata(entry, depth + 1));
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const [key, entry] of Object.entries(value as Record<string, unknown>)) {
      if (!SECRET_OR_TEXT_KEY.test(key)) out[key] = safeMetadata(entry, depth + 1);
    }
    return out;
  }
  return value;
}

/** A spreadsheet must not run a cell as a formula; mirrors the server's CSV rule. */
function cell(raw: unknown): string {
  let text = raw === null || raw === undefined ? '' : String(raw);
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(', ')}]`;
  if (value && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return `{${entries.map(([key, entry]) => `${JSON.stringify(key)}: ${stableJson(entry)}`).join(', ')}}`;
  }
  return JSON.stringify(value) ?? 'null';
}

/** CSV with the same columns as the audit export, newest first as received. */
export function rowsToCsv(rows: readonly ExportRow[]): string {
  const lines = [EXPORT_COLUMNS.join(',')];
  for (const row of rows) {
    lines.push(EXPORT_COLUMNS.map((column) => cell(column === 'metadata' ? stableJson(safeMetadata(row.metadata)) : row[column])).join(','));
  }
  return `${lines.join('\r\n')}\r\n`;
}

/** JSON with the same fields as the audit export (an array, newest first as received), secret or text keys dropped. */
export function rowsToJson(rows: readonly ExportRow[]): string {
  return `${JSON.stringify(rows.map((row) => ({ ...row, metadata: safeMetadata(row.metadata) })), null, 2)}\n`;
}

/** The file name for a narrowed export; only letters, digits, dot and dash. */
export function narrowedFilename(scope: HistoryScope, day: string, format: 'csv' | 'json' = 'csv'): string {
  const part = scope.kind === 'room' ? 'room' : scope.kind === 'agent' ? 'agent' : 'other';
  return `history-${part}-${day.replace(/[^0-9-]/g, '')}.${format}`;
}

export function parseExportRows(raw: unknown): ExportRow[] {
  if (!Array.isArray(raw)) throw new Error('The export did not return a list of records.');
  return raw.filter((r): r is ExportRow => !!r && typeof r === 'object' && typeof (r as ExportRow).id === 'string' && typeof (r as ExportRow).action === 'string');
}
