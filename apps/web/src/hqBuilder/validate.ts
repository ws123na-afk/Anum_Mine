// Live checks on the draft, with the same rules (and the same codes) as the server's layout save,
// so a problem is shown in red while the owner drags, not after a refused save.
// Like the server: a problem that involves a room being saved blocks the save (error); an old
// overlap between rooms nobody is changing is shown as a warning and never blocks.
import { overlaps, pointInRoom, withinBounds, type Bounds, type Rect } from './geometry.ts';
import { changedDeskIds, changedRoomIds, rectOf, type Snapshot } from './draftModel.ts';

export type IssueCode = 'ROOM_OVERLAP' | 'ROOM_OUT_OF_BOUNDS' | 'DESK_OUT_OF_ROOM';
export interface BuilderIssue {
  code: IssueCode;
  severity: 'error' | 'warning';
  /** The room (or, for a desk, the room holding it) the problem is drawn on. */
  roomId: string;
  otherRoomId?: string;
  deskId?: string;
}

export function validateDraft(base: Snapshot, present: Snapshot, bounds: Bounds | null): BuilderIssue[] {
  const issues: BuilderIssue[] = [];
  const changed = new Set(changedRoomIds(base, present));
  const placed: Rect[] = Object.values(present.rooms).filter((room) => room.placed).map(rectOf);

  for (const rect of placed) {
    if (!withinBounds(rect, bounds)) {
      issues.push({ code: 'ROOM_OUT_OF_BOUNDS', severity: changed.has(rect.id) ? 'error' : 'warning', roomId: rect.id });
    }
  }

  // Sweep along x so 500 rooms stay cheap: a room is only compared with rooms that start inside its x range.
  const sorted = [...placed].sort((a, b) => a.x - b.x);
  for (let i = 0; i < sorted.length; i += 1) {
    const first = sorted[i];
    for (let j = i + 1; j < sorted.length && sorted[j].x < first.x + first.width; j += 1) {
      const second = sorted[j];
      if (overlaps(first, second)) {
        const touched = changed.has(first.id) || changed.has(second.id);
        issues.push({ code: 'ROOM_OVERLAP', severity: touched ? 'error' : 'warning', roomId: first.id, otherRoomId: second.id });
      }
    }
  }

  const changedDesks = new Set(changedDeskIds(base, present));
  const roomsWithChangedDesks = new Set<string>();
  for (const id of changedDesks) roomsWithChangedDesks.add(present.desks[id].roomId);
  for (const desk of Object.values(present.desks)) {
    if (desk.posX == null || desk.posZ == null) continue;
    const room = present.rooms[desk.roomId];
    if (!room || !room.placed) continue;
    if (!pointInRoom(room, desk.posX, desk.posZ)) {
      // The server checks every desk of a room that is saved or holds a saved desk.
      const checked = changed.has(room.id) || roomsWithChangedDesks.has(room.id);
      issues.push({ code: 'DESK_OUT_OF_ROOM', severity: checked ? 'error' : 'warning', roomId: room.id, deskId: desk.id });
    }
  }
  return issues;
}

export const hasBlocking = (issues: readonly BuilderIssue[]): boolean => issues.some((issue) => issue.severity === 'error');

/** Which room ids carry an error or warning, for colouring. */
export function roomSeverity(issues: readonly BuilderIssue[]): Map<string, 'error' | 'warning'> {
  const map = new Map<string, 'error' | 'warning'>();
  const mark = (id: string, severity: 'error' | 'warning') => {
    if (map.get(id) !== 'error') map.set(id, severity);
  };
  for (const issue of issues) {
    mark(issue.roomId, issue.severity);
    if (issue.otherRoomId) mark(issue.otherRoomId, issue.severity);
  }
  return map;
}
