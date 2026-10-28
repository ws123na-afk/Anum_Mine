/**
 * Who stands where in a building (docs/ANUM_MINE_HQ_PEOPLE_CHARACTERS.md, "People at their posts"). Pure: no DOM, no three.js.
 *
 * DATA TRUTH. A figure is drawn only where a stored record puts a person:
 *  - at a post (a desk of the office registry) when the desk has an assigned agent, or a presence record for the desk,
 *    AND the person is on shift now (the desk's shift and the building's shift hours, `lib/hqShifts.ts`);
 *  - in a meeting room when a live meeting record holds the room and lists the person as a participant.
 * No record means no figure: nobody is placed at an empty desk, nobody is invented for a meeting, and a person seated in a
 * live meeting on this building is not drawn a second time at the desk.
 *
 * The look is the one the owner set on the profile; a person without one is drawn with the neutral build (`postLook`).
 * Nothing here reads a name.
 */
import type { AvatarAppearance } from '@anum/contracts';
import type { SceneDesk, SceneFloor } from './headquartersProjection.ts';
import { presenceForDesk, type HqPresenceIndex, type RoomMeeting } from './hqPresence.ts';
import { figureLook, roleFamily, type FigureLook } from './hqAvatar.ts';

export type Posture = 'seated' | 'standing';

/** The room kits that draw their own desks with a chair (the person sits); in every other kit a person at a post stands. */
const SEATED_KITS: ReadonlySet<string> = new Set(['office', 'customer-care', 'operations', 'warehouse', 'workshop', 'executive', 'server', 'finance', 'sales']);

/** How a person at a post of this interior kind is drawn: seated at a workstation, or standing (the reception counter, a meeting or lounge post). */
export function postureFor(interiorKind: string | null): Posture {
  return interiorKind === null || SEATED_KITS.has(interiorKind) ? 'seated' : 'standing';
}

/** True when the desk's person is off shift now: the stored seat shift is not the shift in force, or the off-shift index says so. */
export function deskOffShift(desk: Pick<SceneDesk, 'id' | 'agentId' | 'shiftInfo'>, offShift?: ReadonlySet<string>): boolean {
  if (desk.shiftInfo && !desk.shiftInfo.onShift) return true;
  if (!offShift) return false;
  return offShift.has(`desk:${desk.id}`) || (desk.agentId !== null && offShift.has(desk.agentId));
}

export type PostArgs = Readonly<{
  presence: HqPresenceIndex | null;
  /** Agent ids and `desk:<id>` keys of people off shift now (`offShiftKeys`). */
  offShift?: ReadonlySet<string>;
  /** People seated in a live meeting drawn in this building: their desk stays empty while the meeting runs. */
  inMeeting?: ReadonlySet<string>;
}>;

/**
 * The desks that show a person, by desk id, with the seed of the figure (the agent id, else the desk key of a private
 * agent). A desk is left out when nobody is assigned and no presence record names it, when its person is off shift, or
 * when its person is seated in a live meeting.
 */
export function postOccupants(floors: readonly SceneFloor[], args: PostArgs): Map<string, string> {
  const out = new Map<string, string>();
  for (const f of floors) for (const d of f.desks) {
    const record = presenceForDesk(args.presence, d);
    if (!d.agentId && !record) continue;
    if (deskOffShift(d, args.offShift)) continue;
    const seed = d.agentId ?? record?.agentId ?? d.id;
    if (args.inMeeting?.has(seed)) continue;
    out.set(d.id, seed);
  }
  return out;
}

/** The look of a figure: the owner's chosen style over the neutral build. A person with no chosen look is never given a gendered build. */
export function postLook(seed: string, avatar: AvatarAppearance | null | undefined, role: Parameters<typeof roleFamily>[0]): FigureLook {
  const choice: AvatarAppearance = { ...(avatar ?? {}), avatar_style: avatar?.avatar_style ?? 'neutral' };
  return figureLook(seed, choice, roleFamily(role));
}

export type Attendee = Readonly<{ seed: string; kind: 'agent' | 'human'; look: FigureLook }>;

/** Chairs of the meeting kit for a table of `tableWidth` metres: two long sides of `seats` each and one at each end (`roomsFront.ts`). */
export function meetingChairs(tableWidth: number): number {
  const seats = Math.max(2, Math.min(8, Math.floor((tableWidth - 0.4) / 0.72)));
  return seats * 2 + 2;
}

/**
 * The participants of a room's live meeting as the room seats them: the stored agent participants, then the stored human
 * participants, each once, in the stored order, at most `chairs`. No meeting, or a meeting with nobody listed, seats nobody.
 */
export function meetingAttendees(
  meeting: RoomMeeting | null | undefined, styles: ReadonlyMap<string, Readonly<{ avatar: AvatarAppearance | null; department: string | null }>> | undefined, chairs: number,
): Attendee[] {
  if (!meeting) return [];
  const seen = new Set<string>();
  const out: Attendee[] = [];
  const add = (seed: string, kind: Attendee['kind']) => {
    if (!seed || seen.has(seed) || out.length >= chairs) return;
    seen.add(seed);
    const info = kind === 'agent' ? styles?.get(seed) : undefined;
    out.push({ seed, kind, look: postLook(seed, info?.avatar ?? null, { roomKind: 'meeting', department: info?.department ?? null }) });
  };
  for (const id of meeting.agentIds) add(id, 'agent');
  for (const id of meeting.humanIds ?? []) add(id, 'human');
  return out;
}

/** Everyone seated in a live meeting held in one of these rooms (a meeting in a room of another building does not empty a desk here). */
export function seatedInMeetings(roomIds: Iterable<string>, meetings: ReadonlyMap<string, RoomMeeting> | null | undefined): Set<string> {
  const out = new Set<string>();
  if (!meetings) return out;
  for (const id of roomIds) { const m = meetings.get(id); if (m) for (const a of [...m.agentIds, ...(m.humanIds ?? [])]) out.add(a); }
  return out;
}

/** The most chairs the meeting kit ever sets (eight a side and the two heads); a smaller table seats fewer, in the same order. */
export const MAX_MEETING_CHAIRS = meetingChairs(5.2);
