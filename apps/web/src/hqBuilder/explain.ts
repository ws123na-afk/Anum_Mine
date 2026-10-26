// Turns the office API's error envelope (docs/ANUM_MINE_OFFICE_REGISTRY_BACKEND.md, Errors) into
// plain sentences. Every stable code in `details[].type` has its own message in English and Arabic;
// a code this client does not know falls back to the server's own text, never to a bare status.
import { ApiError } from '../lib/errors.ts';
import { t, type MessageKey } from '../lib/i18n.ts';
import type { IssueCode } from './validate.ts';

export interface Explained {
  /** One short headline. */
  title: string;
  /** One line per problem. */
  lines: string[];
  /** A revision check failed: the draft was made on records that changed. */
  stale: boolean;
  /** The caller may not edit the office (403). */
  forbidden: boolean;
  /** The office persistence is not available (503). */
  unavailable: boolean;
}

const KNOWN = new Set([
  'ROOM_OVERLAP', 'ROOM_OUT_OF_BOUNDS', 'DESK_OUT_OF_ROOM', 'ROOM_NOT_PLACED', 'ROOM_NOT_ON_FLOOR', 'DESK_NOT_ON_FLOOR',
  'STALE_REVISION', 'NO_FREE_SPACE', 'ROOM_NAME_TAKEN', 'FLOOR_LEVEL_TAKEN', 'DESK_LABEL_TAKEN', 'AGENT_ALREADY_SEATED',
  'KIND_FORBIDS_DESKS', 'CAPACITY_BELOW_DESKS', 'BUILDING_NOT_EMPTY', 'FLOOR_NOT_EMPTY', 'ROOM_NOT_EMPTY',
]);
// FLOOR_PRESENT, ROOM_PRESENT and DESK_PRESENT lines name what blocks a delete: the server's text is the sentence.
const BLOCKER_TITLE: Record<string, MessageKey> = {
  FLOOR_PRESENT: 'hqb.err.BUILDING_NOT_EMPTY',
  ROOM_PRESENT: 'hqb.err.FLOOR_NOT_EMPTY',
  DESK_PRESENT: 'hqb.err.ROOM_NOT_EMPTY',
};

/** The id after `rooms.` or `desks.` in a detail location. */
export function locationId(location: string | null): string | null {
  const match = /^(?:rooms|desks|floors|buildings)\.(.+)$/.exec(location ?? '');
  return match ? match[1] : null;
}

export type NameOf = (id: string) => string;

export function explainCode(code: string, name: string, other: string, fallback: string): string {
  if (!KNOWN.has(code)) return fallback;
  return t(`hqb.err.${code}` as MessageKey, { name, other });
}

/** The room a live-check issue is about, in words (the same sentences as the server's codes). */
export function explainIssue(code: IssueCode, name: string, other = ''): string {
  return explainCode(code, name, other, name);
}

export function explainError(error: unknown, nameOf: NameOf): Explained {
  if (!(error instanceof ApiError)) {
    return { title: t('hqb.err.network'), lines: [], stale: false, forbidden: false, unavailable: false };
  }
  const forbidden = error.status === 403;
  const unavailable = error.status === 503;
  if (forbidden) return { title: t('hqb.err.forbidden'), lines: [], stale: false, forbidden, unavailable: false };
  if (unavailable) return { title: t('hqb.err.unavailable'), lines: [], stale: false, forbidden: false, unavailable };
  const lines: string[] = [];
  let stale = false;
  let title = '';
  for (const detail of error.details) {
    const code = detail.type ?? '';
    const id = locationId(detail.location);
    const other = /overlaps room (\S+)/.exec(detail.message)?.[1];
    if (code === 'STALE_REVISION') stale = true;
    if (!title && BLOCKER_TITLE[code]) title = t(BLOCKER_TITLE[code]);
    lines.push(explainCode(code, id ? nameOf(id) : detail.message, other ? nameOf(other) : '', detail.message));
  }
  if (!lines.length && error.detail) lines.push(error.detail);
  if (!title) {
    title = stale ? t('hqb.err.stale') : error.status === 409 ? t('hqb.err.conflict') : error.status === 404 ? t('hqb.err.notFound') : t('hqb.err.invalid');
  }
  return { title, lines: [...new Set(lines)], stale, forbidden, unavailable };
}
