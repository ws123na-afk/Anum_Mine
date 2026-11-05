/**
 * How long this workspace keeps meeting transcripts and visitor records (owner decision O5, docs/record-retention.md).
 * The API owns the numbers (`GET/PUT /api/v1/workspace/record-retention`): the periods in force, the standard ones and
 * the range an owner may choose. Nothing here invents a period; an answer that does not parse is treated as an error.
 */
export type RetentionPeriod = { days: number; isDefault: boolean; defaultDays: number; minDays: number; maxDays: number };
export type RecordRetention = {
  transcripts: RetentionPeriod;
  visitors: RetentionPeriod;
  revision: number;
  updatedBy: string | null;
  updatedAt: string | null;
  snapshotsStored: boolean;
  canManage: boolean;
};
export type RetentionKind = 'transcripts' | 'visitors';
export type RetentionSave = { transcript_days: number | null; visitor_days: number | null; expected_revision: number; input_mode: 'click' };

const URL = '/api/v1/workspace/record-retention';
const whole = (value: unknown): value is number => typeof value === 'number' && Number.isInteger(value);

function parsePeriod(raw: unknown): RetentionPeriod | null {
  if (!raw || typeof raw !== 'object') return null;
  const p = raw as Record<string, unknown>;
  if (!whole(p.days) || !whole(p.default_days) || !whole(p.min_days) || !whole(p.max_days) || typeof p.is_default !== 'boolean') return null;
  if (p.min_days > p.max_days || p.days < p.min_days || p.days > p.max_days) return null;
  return { days: p.days, isDefault: p.is_default, defaultDays: p.default_days, minDays: p.min_days, maxDays: p.max_days };
}

/** The API's answer, or null when it is not one (the card then says the setting could not be read). */
export function parseRetention(raw: unknown): RecordRetention | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const transcripts = parsePeriod(r.transcripts), visitors = parsePeriod(r.visitors);
  if (!transcripts || !visitors || !whole(r.revision) || r.revision < 0) return null;
  return {
    transcripts, visitors, revision: r.revision,
    updatedBy: typeof r.updated_by === 'string' ? r.updated_by : null,
    updatedAt: typeof r.updated_at === 'string' ? r.updated_at : null,
    snapshotsStored: r.snapshots_stored === true,
    canManage: r.can_manage === true,
  };
}

export type DaysInput = { ok: true; value: number } | { ok: false; reason: 'empty' | 'notWhole' | 'range' };

/** A typed number of days: whole, inside the period's range. Western or Arabic-Indic digits are both read. */
export function parseDays(text: string, period: RetentionPeriod): DaysInput {
  const normalised = text.trim().replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660));
  if (!normalised) return { ok: false, reason: 'empty' };
  if (!/^\d+$/.test(normalised)) return { ok: false, reason: 'notWhole' };
  const value = Number(normalised);
  if (value < period.minDays || value > period.maxDays) return { ok: false, reason: 'range' };
  return { ok: true, value };
}

/**
 * The body of a save: the edited period changes, the other keeps what the owner has (null when it is the standard).
 * `value: null` returns the edited period to the standard one.
 */
export function saveBody(current: RecordRetention, kind: RetentionKind, value: number | null): RetentionSave {
  const keep = (p: RetentionPeriod) => (p.isDefault ? null : p.days);
  return {
    transcript_days: kind === 'transcripts' ? value : keep(current.transcripts),
    visitor_days: kind === 'visitors' ? value : keep(current.visitors),
    expected_revision: current.revision,
    input_mode: 'click',
  };
}

export type Requester = <T>(path: string, init: RequestInit) => Promise<T>;

export function createRetentionClient(request: Requester) {
  const parsed = (raw: unknown): RecordRetention => {
    const value = parseRetention(raw);
    if (!value) throw new Error('The retention setting the server sent could not be read.');
    return value;
  };
  return {
    async get(): Promise<RecordRetention> { return parsed(await request<unknown>(URL, { method: 'GET' })); },
    async save(body: RetentionSave): Promise<RecordRetention> {
      return parsed(await request<unknown>(URL, { method: 'PUT', body: JSON.stringify(body) }));
    },
  };
}
