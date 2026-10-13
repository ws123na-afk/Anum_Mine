// The Call page's "Recent calls" (docs/voice.md#recent-calls): the caller's own calls and Voice
// sessions from `GET /api/v1/voice/sessions`, newest first, paged with an opaque cursor. Pure, so
// `pnpm test:unit` runs it.
export type CallRetention = 'session' | '30_days' | 'permanent';
export type CallStatus = 'active' | 'completed' | 'cancelled';

export interface CallSummaryRow {
  id: string; locale: string; retention: CallRetention; assistant_name: string; status: CallStatus;
  created_at: string; updated_at: string; expires_at: string | null; segment_count: number; first_line: string | null;
}
export interface CallPage { items: CallSummaryRow[]; next_cursor: string | null }

export const CALL_PAGE_SIZE = 10;

/** The list path for one page; a cursor only ever comes from the previous page. */
export function callsPath(cursor: string | null, limit = CALL_PAGE_SIZE, status: CallStatus | '' = ''): string {
  const query = new URLSearchParams({ limit: String(limit) });
  if (cursor) query.set('cursor', cursor);
  if (status) query.set('status', status);
  return `/api/v1/voice/sessions?${query}`;
}

/** Appends a page, dropping rows already shown (a call that started between two pages moves them). */
export function mergeCalls(shown: readonly CallSummaryRow[], page: readonly CallSummaryRow[]): CallSummaryRow[] {
  const seen = new Set(shown.map((row) => row.id));
  return [...shown, ...page.filter((row) => !seen.has(row.id))];
}

/** Minutes the call lasted (created to last update), at least 1 for a call that ended. */
export function callMinutes(row: Pick<CallSummaryRow, 'created_at' | 'updated_at' | 'status'>): number | null {
  if (row.status === 'active') return null;
  const ms = Date.parse(row.updated_at) - Date.parse(row.created_at);
  return Number.isFinite(ms) ? Math.max(1, Math.round(ms / 60_000)) : null;
}

/**
 * Why a call shows no transcript: erased when it ended ("session" retention), expired after 30
 * days, or simply nothing said. Null when lines are stored.
 */
export function transcriptGone(row: Pick<CallSummaryRow, 'retention' | 'status' | 'segment_count' | 'expires_at'>, now = new Date()): 'erased' | 'expired' | 'empty' | null {
  if (row.segment_count > 0) return null;
  if (row.retention === '30_days' && row.expires_at && Date.parse(row.expires_at) <= now.getTime()) return 'expired';
  if (row.retention === 'session' && row.status !== 'active') return 'erased';
  return 'empty';
}
