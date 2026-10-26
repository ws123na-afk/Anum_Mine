/**
 * What a desk shows for each state the server can report (docs/ANUM_MINE_HQ_LIVE_SCENE.md). Every presence state has its
 * own look on three channels at once, so a state never depends on colour alone:
 *   1. the screen colour and the status light on the monitor bezel,
 *   2. the pose of the seated figure,
 *   3. a small marker floating above the monitor (a shape, not a colour).
 * The same table draws the desk in the furnished rooms (`workstation`) and in the plain rooms (`buildingScene.desk`),
 * and the tests check that no two states look alike. Nothing here moves: there is no walking figure, because no
 * record places anyone anywhere but at their desk.
 */
import { hex, type Rgb } from '../../lib/hqGeometry.ts';
import type { SeatedPose } from './figures.ts';

export type VisualState = 'working' | 'queued' | 'meeting' | 'waiting' | 'blocked' | 'idle' | 'paused' | 'unknown' | 'assigned' | 'vacant';
export type DeskMarker = 'none' | 'queue' | 'bubble' | 'flag' | 'alert' | 'pause' | 'outline';

export type StateLook = Readonly<{
  screen: Rgb;
  emissive: number;
  /** The small light on the bezel; null when the desk has no one to report on. */
  light: Rgb | null;
  lightEmissive: number;
  /** The pose of the seated figure (only drawn when the registry says someone sits here). */
  pose: SeatedPose;
  marker: DeskMarker;
  /** Draw the lines of text on the screen (a dark or empty screen shows none). */
  content: boolean;
}>;

export const STATE_LOOK: Readonly<Record<VisualState, StateLook>> = {
  working: { screen: hex('#58f0cf'), emissive: 1, light: hex('#3ddc97'), lightEmissive: 1, pose: 'typing', marker: 'none', content: true },
  queued: { screen: hex('#6f8fd6'), emissive: 0.5, light: hex('#6aa7ff'), lightEmissive: 0.9, pose: 'leaning', marker: 'queue', content: true },
  meeting: { screen: hex('#5a4a8a'), emissive: 0.55, light: hex('#b48cff'), lightEmissive: 1, pose: 'talking', marker: 'bubble', content: false },
  waiting: { screen: hex('#ffc861'), emissive: 0.95, light: hex('#ffb020'), lightEmissive: 1, pose: 'reading', marker: 'flag', content: true },
  blocked: { screen: hex('#d8433f'), emissive: 0.85, light: hex('#ff3b3b'), lightEmissive: 1, pose: 'phone', marker: 'alert', content: false },
  idle: { screen: hex('#6aa7ff'), emissive: 0.45, light: hex('#7fb7a8'), lightEmissive: 0.5, pose: 'leaning', marker: 'none', content: false },
  paused: { screen: hex('#2a3038'), emissive: 0.08, light: hex('#aab4be'), lightEmissive: 0.45, pose: 'leaning', marker: 'pause', content: false },
  unknown: { screen: hex('#8b95a5'), emissive: 0.3, light: hex('#5b6572'), lightEmissive: 0.3, pose: 'leaning', marker: 'outline', content: false },
  assigned: { screen: hex('#8b95a5'), emissive: 0.25, light: hex('#5b6572'), lightEmissive: 0.25, pose: 'typing', marker: 'none', content: true },
  vacant: { screen: hex('#232c36'), emissive: 0, light: null, lightEmissive: 0, pose: 'typing', marker: 'none', content: false },
};

/** One box of a marker, relative to the top centre of the monitor: u across, v up (metres). */
export type MarkerPart = Readonly<{ u: number; v: number; w: number; h: number; color: Rgb; emissive: number }>;

const VIOLET = hex('#b48cff');
const AMBER = hex('#ffb020');
const RED = hex('#ff3b3b');
const BLUE = hex('#6aa7ff');
const GREY = hex('#aab4be');

const PARTS: Readonly<Record<DeskMarker, readonly MarkerPart[]>> = {
  none: [],
  // Three bars of rising height: work lined up and waiting for its turn.
  queue: [
    { u: -0.1, v: 0.06, w: 0.06, h: 0.06, color: BLUE, emissive: 0.9 },
    { u: 0, v: 0.06, w: 0.06, h: 0.11, color: BLUE, emissive: 0.9 },
    { u: 0.1, v: 0.06, w: 0.06, h: 0.16, color: BLUE, emissive: 0.9 },
  ],
  // A speech bubble with a tail.
  bubble: [
    { u: 0, v: 0.12, w: 0.24, h: 0.13, color: VIOLET, emissive: 0.95 },
    { u: -0.06, v: 0.06, w: 0.05, h: 0.06, color: VIOLET, emissive: 0.95 },
  ],
  // A pole with a flag: a decision is waiting on a person.
  flag: [
    { u: -0.08, v: 0.13, w: 0.02, h: 0.26, color: GREY, emissive: 0.2 },
    { u: 0.02, v: 0.2, w: 0.2, h: 0.12, color: AMBER, emissive: 1 },
  ],
  // An exclamation mark: the work cannot go on.
  alert: [
    { u: 0, v: 0.17, w: 0.05, h: 0.14, color: RED, emissive: 1 },
    { u: 0, v: 0.05, w: 0.05, h: 0.05, color: RED, emissive: 1 },
    { u: 0, v: 0.13, w: 0.15, h: 0.3, color: RED, emissive: 0.18 },
  ],
  // The pause sign: two bars.
  pause: [
    { u: -0.05, v: 0.11, w: 0.045, h: 0.16, color: GREY, emissive: 0.6 },
    { u: 0.05, v: 0.11, w: 0.045, h: 0.16, color: GREY, emissive: 0.6 },
  ],
  // An empty frame: the state is not known, so nothing is drawn inside it.
  outline: [
    { u: 0, v: 0.04, w: 0.18, h: 0.015, color: GREY, emissive: 0.4 },
    { u: 0, v: 0.2, w: 0.18, h: 0.015, color: GREY, emissive: 0.4 },
    { u: -0.0825, v: 0.12, w: 0.015, h: 0.16, color: GREY, emissive: 0.4 },
    { u: 0.0825, v: 0.12, w: 0.015, h: 0.16, color: GREY, emissive: 0.4 },
  ],
};

export function markerParts(marker: DeskMarker): readonly MarkerPart[] {
  return PARTS[marker];
}

/** The server state of an agent to the desk look it gets. A stale record is drawn as `unknown`: it no longer proves anything. */
export function visualOfStatus(status: string, stale = false): VisualState {
  if (stale) return 'unknown';
  switch (status) {
    case 'working': return 'working';
    case 'queued': return 'queued';
    case 'meeting': return 'meeting';
    case 'waiting_approval': return 'waiting';
    case 'blocked': return 'blocked';
    case 'idle': return 'idle';
    case 'paused': return 'paused';
    default: return 'unknown';
  }
}

/** Two looks are told apart when at least two of the three channels differ. */
export function lookSignature(state: VisualState): string {
  const l = STATE_LOOK[state];
  return [l.screen.join(','), l.light ? l.light.join(',') : 'none', l.pose, l.marker].join('|');
}
