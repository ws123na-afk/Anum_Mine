// Email Center phase 2 (docs/email.md#email-center): the pure helpers behind flags, moves, delete for
// good, search and the background refresh of unread counts. They follow the API contract of
// /api/v1/mail: POST /accounts/{id}/flags {folder, uids, flag: seen|flagged, value},
// POST /accounts/{id}/move {folder, uids, to: role or folder id}, POST /accounts/{id}/delete {folder, uids}
// (Trash and Junk only, answered with a paused approval), and GET /accounts/{id}/messages with
// q, unread and flagged. No browser APIs at import time, so the unit tests run these directly.

export type MailRole = 'inbox' | 'drafts' | 'sent' | 'archive' | 'junk' | 'trash';
/** The bulk and single-message actions the UI offers. */
export type MailAction = 'read' | 'unread' | 'star' | 'unstar' | 'archive' | 'junk' | 'notJunk' | 'trash' | 'restore' | 'toInbox' | 'deleteForever';

/** Most uids one flag, move or delete call accepts (MoveBody/FlagBody/DeleteBody `max_length`). */
export const MAX_UIDS_PER_CALL = 200;
/** Background refresh of folders and unread counts while the page is visible. */
export const SYNC_INTERVAL_MS = 60_000;
export const SYNC_MAX_INTERVAL_MS = 5 * 60_000;
export const MAX_QUERY_CHARS = 200;

export interface FolderLike { id: string; name: string; role: string | null; total: number; unread: number }
export interface MessageLike { uid: number; seen: boolean; flagged: boolean }

const MOVE_TARGET: Partial<Record<MailAction, MailRole>> = {
  archive: 'archive', junk: 'junk', notJunk: 'inbox', trash: 'trash', restore: 'inbox', toInbox: 'inbox',
};

/** The folder role a move action sends messages to, or null when the action is not a move. */
export function moveTarget(action: MailAction): MailRole | null {
  return MOVE_TARGET[action] ?? null;
}

export function isMove(action: MailAction): boolean { return moveTarget(action) !== null; }
export function isFlag(action: MailAction): boolean { return action === 'read' || action === 'unread' || action === 'star' || action === 'unstar'; }

/**
 * Which moves make sense in a folder. Trash and Junk offer "delete for good" (the API refuses it
 * anywhere else); Trash offers Restore, Junk offers "Not junk", Archive offers "Move to inbox".
 */
export function moveActionsFor(role: string | null | undefined): MailAction[] {
  switch (role) {
    case 'inbox': return ['archive', 'junk', 'trash'];
    case 'archive': return ['toInbox', 'junk', 'trash'];
    case 'junk': return ['notJunk', 'trash', 'deleteForever'];
    case 'trash': return ['restore', 'deleteForever'];
    case 'drafts': return ['trash'];
    case 'sent': return ['archive', 'trash'];
    default: return ['toInbox', 'archive', 'trash'];
  }
}

/** True when the folder holds a role the mailbox has, so a move to it can succeed. */
export function hasRole(folders: FolderLike[], role: MailRole): boolean {
  return folders.some((f) => f.role === role);
}

/** Moves offered in a folder, without those whose target folder the mailbox does not have. */
export function availableMoves(role: string | null | undefined, folders: FolderLike[]): MailAction[] {
  return moveActionsFor(role).filter((action) => {
    const target = moveTarget(action);
    return target === null || hasRole(folders, target);
  });
}

/**
 * Star or read toggles follow the selection: when any picked message lacks the flag, the action
 * sets it on all of them; when all have it, the action clears it (as most mail apps do).
 */
export function flagToggle(items: MessageLike[], selected: ReadonlySet<number>, flag: 'seen' | 'flagged'): { flag: 'seen' | 'flagged'; value: boolean; uids: number[] } {
  const picked = items.filter((m) => selected.has(m.uid));
  const has = (m: MessageLike) => (flag === 'seen' ? m.seen : m.flagged);
  return { flag, value: picked.some((m) => !has(m)), uids: picked.map((m) => m.uid) };
}

export function flagFor(action: MailAction): { flag: 'seen' | 'flagged'; value: boolean } | null {
  if (action === 'read') return { flag: 'seen', value: true };
  if (action === 'unread') return { flag: 'seen', value: false };
  if (action === 'star') return { flag: 'flagged', value: true };
  if (action === 'unstar') return { flag: 'flagged', value: false };
  return null;
}

/** The list after a flag change the server accepted. */
export function applyFlag<T extends MessageLike>(items: T[], uids: number[], flag: 'seen' | 'flagged', value: boolean): T[] {
  const set = new Set(uids);
  return items.map((m) => (set.has(m.uid) ? { ...m, [flag]: value } : m));
}

/** The list after messages left the folder (moved). */
export function removeUids<T extends MessageLike>(items: T[], uids: number[]): T[] {
  const set = new Set(uids);
  return items.filter((m) => !set.has(m.uid));
}

/** Folder counts after a flag or move, so badges move at once and the next refresh corrects them. */
export function adjustFolders<T extends FolderLike>(folders: T[], change: { folderId: string; unreadDelta?: number; totalDelta?: number; targetRole?: MailRole | null; movedUnread?: number; moved?: number }): T[] {
  return folders.map((f) => {
    if (f.id === change.folderId) {
      return { ...f, unread: Math.max(0, f.unread + (change.unreadDelta ?? 0)), total: Math.max(0, f.total + (change.totalDelta ?? 0)) };
    }
    if (change.targetRole && f.role === change.targetRole) {
      return { ...f, unread: f.unread + (change.movedUnread ?? 0), total: f.total + (change.moved ?? 0) };
    }
    return f;
  });
}

/** Split a selection into calls the API accepts. */
export function chunkUids(uids: number[], size = MAX_UIDS_PER_CALL): number[][] {
  const unique = [...new Set(uids)].sort((a, b) => a - b);
  const out: number[][] = [];
  for (let i = 0; i < unique.length; i += size) out.push(unique.slice(i, i + size));
  return out;
}

// -- selection ------------------------------------------------------------------------------------

/** Toggle one message; with `range` (shift+click) select every message between the anchor and it. */
export function toggleSelection(items: MessageLike[], selected: ReadonlySet<number>, uid: number, anchor: number | null, range: boolean): Set<number> {
  const next = new Set(selected);
  if (range && anchor !== null) {
    const a = items.findIndex((m) => m.uid === anchor);
    const b = items.findIndex((m) => m.uid === uid);
    if (a >= 0 && b >= 0) {
      const [from, to] = a < b ? [a, b] : [b, a];
      for (const m of items.slice(from, to + 1)) next.add(m.uid);
      return next;
    }
  }
  if (next.has(uid)) next.delete(uid); else next.add(uid);
  return next;
}

export type SelectAllState = 'none' | 'some' | 'all';
export function selectAllState(items: MessageLike[], selected: ReadonlySet<number>): SelectAllState {
  const count = items.filter((m) => selected.has(m.uid)).length;
  if (count === 0) return 'none';
  return count === items.length ? 'all' : 'some';
}

/** The select-all checkbox: selects every loaded message, or clears when all are picked. */
export function toggleAll(items: MessageLike[], selected: ReadonlySet<number>): Set<number> {
  return selectAllState(items, selected) === 'all' ? new Set() : new Set(items.map((m) => m.uid));
}

/** Keep only selections still in the list (after a reload or a move). */
export function pruneSelection(items: MessageLike[], selected: ReadonlySet<number>): Set<number> {
  const present = new Set(items.map((m) => m.uid));
  return new Set([...selected].filter((uid) => present.has(uid)));
}

// -- search ---------------------------------------------------------------------------------------

export interface MailFilters { q: string; unread: boolean; flagged: boolean }
export const NO_FILTERS: MailFilters = { q: '', unread: false, flagged: false };

/** Search text as the API takes it: control characters removed, spaces collapsed, capped. */
export function normalizeQuery(raw: string): string {
  // eslint-disable-next-line no-control-regex
  return raw.replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, MAX_QUERY_CHARS);
}

export function filtersActive(filters: MailFilters): boolean {
  return Boolean(normalizeQuery(filters.q) || filters.unread || filters.flagged);
}

/** Query string for GET /api/v1/mail/accounts/{id}/messages. */
export function messageListQuery(folder: string, filters: MailFilters, before: number | null, limit = 25): string {
  const params = new URLSearchParams({ folder, limit: String(limit) });
  const q = normalizeQuery(filters.q);
  if (q) params.set('q', q);
  if (filters.unread) params.set('unread', 'true');
  if (filters.flagged) params.set('flagged', 'true');
  if (before !== null) params.set('before', String(before));
  return params.toString();
}

// -- unread counts and background refresh ----------------------------------------------------------

/** Unread messages that matter for a badge: the inbox, never Junk or Trash. */
export function inboxUnread(folders: FolderLike[]): number {
  return folders.filter((f) => f.role === 'inbox').reduce((sum, f) => sum + f.unread, 0);
}

/** A short badge: nothing for zero, "99+" past 99. */
export function badgeText(count: number): string {
  if (count <= 0) return '';
  return count > 99 ? '99+' : String(count);
}

/** Folders whose unread count went up since the last refresh (new mail arrived there). */
export function foldersWithNewMail(before: FolderLike[], after: FolderLike[]): string[] {
  const old = new Map(before.map((f) => [f.id, f.unread]));
  return after.filter((f) => f.role !== 'junk' && f.role !== 'trash' && old.has(f.id) && f.unread > (old.get(f.id) ?? 0)).map((f) => f.id);
}

/** Next refresh delay: the base interval, doubled for each failure in a row, capped. */
export function nextSyncDelay(failures: number, base = SYNC_INTERVAL_MS, cap = SYNC_MAX_INTERVAL_MS): number {
  if (failures <= 0) return base;
  return Math.min(cap, base * 2 ** Math.min(failures, 8));
}

// -- keyboard -------------------------------------------------------------------------------------

export type MailShortcut = 'next' | 'previous' | 'select' | 'archive' | 'trash' | 'star' | 'toggleRead' | 'search' | 'open';

/**
 * Single-key shortcuts of the message list (Gmail-style). Ignored while typing in a field and when a
 * modifier other than Shift is held, so screen-reader and browser keys keep working.
 */
export function shortcutFor(key: string, opts: { ctrl?: boolean; meta?: boolean; alt?: boolean; typing?: boolean }): MailShortcut | null {
  if (opts.typing || opts.ctrl || opts.meta || opts.alt) return null;
  switch (key) {
    case 'j': case 'ArrowDown': return 'next';
    case 'k': case 'ArrowUp': return 'previous';
    case 'x': return 'select';
    case 'e': return 'archive';
    case '#': case 'Delete': return 'trash';
    case 's': return 'star';
    case 'u': return 'toggleRead';
    case '/': return 'search';
    case 'Enter': case 'o': return 'open';
    default: return null;
  }
}

/** The uid next to `current` in the list, for j/k. */
export function neighbour(items: MessageLike[], current: number | null, step: 1 | -1): number | null {
  if (!items.length) return null;
  const index = current === null ? -1 : items.findIndex((m) => m.uid === current);
  if (index < 0) return step === 1 ? items[0].uid : items[items.length - 1].uid;
  const next = Math.min(items.length - 1, Math.max(0, index + step));
  return items[next].uid;
}

/** After a move or delete takes the open message away, open the one that took its place. */
export function nextAfterRemoval(items: MessageLike[], removed: number[], current: number | null): number | null {
  if (current === null || !removed.includes(current)) return current;
  const index = items.findIndex((m) => m.uid === current);
  const rest = removeUids(items, removed);
  if (!rest.length) return null;
  const after = items.slice(index + 1).find((m) => !removed.includes(m.uid));
  return after ? after.uid : rest[rest.length - 1].uid;
}
