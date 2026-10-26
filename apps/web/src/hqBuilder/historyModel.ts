// Pure helpers of the layout history view: the sentences, the mini plan geometry and the error mapping.
// No React and no network, so the unit tests run in plain Node.
import type { OfficeLayoutBox, OfficeLayoutDiff, OfficeLayoutDiffSummary, OfficeLayoutRevision, OfficeLayoutRoomStatus } from '@anum/contracts';
import { ApiError } from '../lib/errors.ts';
import { joinList, t, type MessageKey } from '../lib/i18n.ts';

/** "2 rooms moved, 1 desk added", or the plain "no visible change" when only details differ. */
export function summaryText(summary: OfficeLayoutDiffSummary): string {
  if (summary.baseline) return t('hqh.sum.totals', { rooms: summary.rooms, desks: summary.desks });
  const parts: string[] = [];
  const add = (count: number, key: MessageKey) => { if (count > 0) parts.push(t(key, { count })); };
  add(summary.rooms_added, 'hqh.sum.roomsAdded');
  add(summary.rooms_removed, 'hqh.sum.roomsRemoved');
  add(summary.rooms_moved, 'hqh.sum.roomsMoved');
  add(summary.rooms_changed, 'hqh.sum.roomsChanged');
  add(summary.desks_added, 'hqh.sum.desksAdded');
  add(summary.desks_removed, 'hqh.sum.desksRemoved');
  add(summary.desks_moved, 'hqh.sum.desksMoved');
  if (summary.floor_resized) parts.push(t('hqh.sum.resized'));
  return parts.length ? joinList(parts) : t('hqh.sum.none');
}

export function revisionTitle(revision: OfficeLayoutRevision): string {
  if (revision.summary.baseline || revision.action === 'baseline') return t('hqh.action.baseline');
  if (revision.restored_from != null) return t('hqh.action.restore', { from: revision.restored_from });
  const key = `hqh.action.${revision.action}` as MessageKey;
  const text = t(key);
  return text === key ? t('hqh.action.other') : text;
}

/** The number of changes a restore would make; zero means the floor already looks like that revision. */
export function changeCount(summary: OfficeLayoutDiffSummary): number {
  return summary.rooms_added + summary.rooms_removed + summary.rooms_moved + summary.rooms_changed
    + summary.desks_added + summary.desks_removed + summary.desks_moved + (summary.floor_resized ? 1 : 0);
}

export interface MiniView { minX: number; minZ: number; width: number; depth: number }

/** The mini plan frame: the floor outline plus every room, so a removed room far away still shows. */
export function miniView(diff: OfficeLayoutDiff, floor: { width_m: number | null; depth_m: number | null }): MiniView {
  let maxX = floor.width_m ?? 0;
  let maxZ = floor.depth_m ?? 0;
  let minX = 0;
  let minZ = 0;
  const reach = (b: OfficeLayoutBox | null) => {
    if (!b) return;
    minX = Math.min(minX, b.x); minZ = Math.min(minZ, b.z);
    maxX = Math.max(maxX, b.x + b.width); maxZ = Math.max(maxZ, b.z + b.depth);
  };
  for (const room of diff.rooms) { reach(room.box); reach(room.from_box); }
  return { minX, minZ, width: Math.max(1, maxX - minX), depth: Math.max(1, maxZ - minZ) };
}

export const STATUS_ORDER: OfficeLayoutRoomStatus[] = ['added', 'removed', 'moved', 'changed'];

export interface HistoryProblem { kind: 'denied' | 'unavailable' | 'stale' | 'unassign' | 'gone' | 'failed'; text: string }

/** Plain sentences for every way a history call fails. Server text is never shown as the only explanation. */
export function historyProblem(error: unknown): HistoryProblem {
  if (!(error instanceof ApiError)) return { kind: 'failed', text: t('hqh.err.network') };
  const codes = error.details.map((d) => d.type ?? '');
  if (error.status === 403) return { kind: 'denied', text: t('hqh.err.denied') };
  if (error.status === 503) return { kind: 'unavailable', text: t('hqh.err.unavailable') };
  if (codes.includes('STALE_LAYOUT')) return { kind: 'stale', text: t('hqh.err.stale') };
  if (codes.includes('UNASSIGN_NOT_CONFIRMED')) return { kind: 'unassign', text: t('hqh.err.unassign') };
  if (codes.includes('PAYLOAD_HASH_MISMATCH')) return { kind: 'stale', text: t('hqh.err.payload') };
  if (codes.includes('RESTORE_MISMATCH')) return { kind: 'failed', text: t('hqh.err.mismatch') };
  if (codes.includes('TEMPLATE_NAME_TAKEN')) return { kind: 'failed', text: t('hqh.err.nameTaken') };
  if (codes.includes('TEMPLATE_LIMIT')) return { kind: 'failed', text: t('hqh.err.limit') };
  if (codes.includes('ROOM_NAME_TAKEN') || codes.includes('FLOOR_LEVEL_TAKEN')) return { kind: 'failed', text: t('hqh.err.levelTaken') };
  if (error.status === 404) return { kind: 'gone', text: t('hqh.err.gone') };
  if (error.status === 409) return { kind: 'failed', text: t('hqh.err.conflict') };
  return { kind: 'failed', text: t('hqh.err.generic') };
}
