/** Executive guided-visit model, pure and deterministic (docs/ANUM_MINE_EXECUTIVE_PA.md#guided-tour).
 *
 * The stops are the rooms `GET /api/v1/executive-pa/tour` returns: only buildings, floors and rooms
 * the person can access, as stored in the office registry. This module invents nothing: no staff, no
 * activity and no description beyond the registry's own facts. Events carry codes and room ids, never
 * sentences, so the screen writes them in the person's language.
 */
import type { TourStop as RegistryStop } from './executivePa';

export interface TourStop {
  id: string; name: string; floor: number; floorId: string; floorName: string;
  buildingId: string; buildingName: string; kind: string; capacity: number; desks: number;
}
export type TourPhase = 'ready' | 'touring' | 'paused' | 'finished';
export type TourEventKind = 'arrival' | 'visit' | 'pause' | 'resume' | 'finish' | 'redirect';
export interface TourEvent { at: string; kind: TourEventKind; roomId: string | null }
export interface GuidedTour {
  phase: TourPhase; index: number; stops: readonly TourStop[]; visited: readonly string[];
  events: readonly TourEvent[]; currentRoomId: string | null;
}
export type TourAction =
  | { type: 'begin'; at: string } | { type: 'next'; at: string } | { type: 'pause'; at: string } | { type: 'resume'; at: string }
  | { type: 'redirect'; roomId: string; at: string } | { type: 'reset'; stops: readonly TourStop[]; at: string };

/** `code` maps to the message `gt.err.<code>`. */
export type TourErrorCode = 'invalid' | 'started' | 'notRunning' | 'notPaused' | 'resumeFirst' | 'unknownRoom' | 'finished';
export class GuidedTourError extends Error {
  code: TourErrorCode;
  constructor(code: TourErrorCode) { super(code); this.name = 'GuidedTourError'; this.code = code; }
}

export function stopsFromRegistry(stops: readonly RegistryStop[]): TourStop[] {
  return stops.map((s) => ({
    id: s.room_id, name: s.room_name, floor: s.level, floorId: s.floor_id, floorName: s.floor_name,
    buildingId: s.building_id, buildingName: s.building_name, kind: s.room_kind, capacity: s.capacity, desks: s.desks,
  }));
}

function time(at: string): string {
  const t = Date.parse(at);
  if (!Number.isFinite(t)) throw new GuidedTourError('invalid');
  return new Date(t).toISOString();
}

function log(state: GuidedTour, kind: TourEventKind, at: string, roomId: string | null = state.currentRoomId): TourEvent[] {
  const last = state.events[state.events.length - 1];
  if (last && Date.parse(at) < Date.parse(last.at)) throw new GuidedTourError('invalid');
  return [...state.events, { kind, at, roomId }];
}

export function createGuidedTour(stops: readonly TourStop[], at: string): GuidedTour {
  const t = time(at);
  const ids = new Set<string>();
  for (const stop of stops) {
    if (!stop.id || !stop.name || !Number.isInteger(stop.floor) || ids.has(stop.id)) throw new GuidedTourError('invalid');
    ids.add(stop.id);
  }
  return { phase: 'ready', index: -1, stops: [...stops], currentRoomId: null, visited: [], events: [{ at: t, kind: 'arrival', roomId: null }] };
}

function visit(state: GuidedTour, index: number, at: string, kind: 'visit' | 'redirect' = 'visit'): GuidedTour {
  const stop = state.stops[index];
  if (!stop) throw new GuidedTourError('unknownRoom');
  return {
    ...state, currentRoomId: stop.id, index, phase: 'touring',
    visited: state.visited.includes(stop.id) ? state.visited : [...state.visited, stop.id],
    events: log(state, kind, at, stop.id),
  };
}

export function changeGuidedTour(state: GuidedTour, action: TourAction): GuidedTour {
  if (action.type === 'reset') return createGuidedTour(action.stops, action.at);
  const at = time(action.at);
  switch (action.type) {
    case 'begin':
      if (state.phase !== 'ready') throw new GuidedTourError('started');
      if (!state.stops.length) return { ...state, phase: 'finished', events: log(state, 'finish', at) };
      return visit(state, 0, at);
    case 'pause':
      if (state.phase !== 'touring') throw new GuidedTourError('notRunning');
      return { ...state, phase: 'paused', events: log(state, 'pause', at) };
    case 'resume':
      if (state.phase !== 'paused') throw new GuidedTourError('notPaused');
      return { ...state, phase: 'touring', events: log(state, 'resume', at) };
    case 'redirect': {
      if (state.phase === 'finished') throw new GuidedTourError('finished');
      const index = state.stops.findIndex((stop) => stop.id === action.roomId);
      if (index < 0) throw new GuidedTourError('unknownRoom');
      return visit(state, index, at, 'redirect');
    }
    case 'next':
      if (state.phase === 'ready') return changeGuidedTour(state, { type: 'begin', at });
      if (state.phase !== 'touring') throw new GuidedTourError('resumeFirst');
      if (state.index + 1 >= state.stops.length) return { ...state, phase: 'finished', events: log(state, 'finish', at) };
      return visit(state, state.index + 1, at);
  }
}

export function tourProgress(state: GuidedTour): number {
  return state.stops.length ? Math.round((100 * state.visited.length) / state.stops.length) : 100;
}
