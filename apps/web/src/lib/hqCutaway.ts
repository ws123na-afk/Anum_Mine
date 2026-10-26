/**
 * The dollhouse section of the building view (docs/ANUM_MINE_HQ_CUTAWAY.md): which two outer walls face the camera and are
 * taken away, with a dead band so a slow orbit never makes a wall flicker, the 90 degree snaps of the rotate buttons, and
 * the small description of the cut that other layers (labels, tests, assistive text) read. Pure: no DOM, no three.js.
 */
import { HOME_YAW } from './hqCamera.ts';

export type Side = 'north' | 'south' | 'east' | 'west';
export type Facade = 'cutaway' | 'closed';
export const SIDES: readonly Side[] = ['north', 'south', 'east', 'west'];

/** The elevation of the section view: low enough to look into every floor, high enough to read the floor plans. */
export const CUT_PITCH = 32;
/** Buildings up to this many floors open with every floor drawn; a taller tower opens on the open floor alone (the stack costs a rebuild per floor), and "All floors" turns it on. */
export const STACK_AUTO_MAX = 8;
/** A wall changes only once the camera has gone this far (degrees of azimuth) past the line where it was edge on. */
export const CUT_BAND = 9;

const rad = (d: number) => (d * Math.PI) / 180;

/**
 * The outer walls that face the camera at this azimuth: one of north/south and one of east/west (`yaw` as in
 * `viewDirection`: 0 puts the eye south of the building, 90 east of it). `prev` is the pair chosen before; an axis whose
 * component is inside the dead band keeps its previous wall, so orbiting across a diagonal does not flicker.
 */
export function facingSides(yaw: number, prev: readonly Side[] = []): readonly [Side, Side] {
  const band = Math.sin(rad(CUT_BAND));
  const pick = (v: number, pos: Side, neg: Side): Side => {
    const was = prev.includes(pos) ? pos : prev.includes(neg) ? neg : null;
    return was && Math.abs(v) < band ? was : v >= 0 ? pos : neg;
  };
  return [pick(Math.cos(rad(yaw)), 'south', 'north'), pick(Math.sin(rad(yaw)), 'east', 'west')];
}

/** The next diagonal view (HOME_YAW + a multiple of 90) in a direction, from any yaw; stays unwrapped so a tween turns the short way. */
export function snapYaw(yaw: number, dir: 1 | -1): number {
  const k = (yaw - HOME_YAW) / 90;
  const near = Math.round(k);
  return HOME_YAW + (Math.abs(k - near) < 0.02 ? near + dir : dir > 0 ? Math.ceil(k) : Math.floor(k)) * 90;
}

/** What is cut right now: the walls and the roof, and how many floors are drawn. Exposed on the stage (`data-cut-*`) for the label layer. */
export type CutawayInfo = Readonly<{ facade: Facade; cutSides: readonly Side[]; roofOpen: boolean; floors: number; activeIndex: number }>;

export function cutawayInfo(facade: Facade, sides: readonly Side[], floors: number, activeIndex: number): CutawayInfo {
  const open = facade === 'cutaway';
  return { facade, cutSides: open ? sides : [], roofOpen: open, floors, activeIndex };
}

/** Lift targets of the wall and roof groups (see `RenderScene.lifts`): a cut wall collapses to its floor, a kept one stands. */
export function cutLifts(info: CutawayInfo): Record<string, { to: number }> {
  const out: Record<string, { to: number }> = { 'cut:roof': { to: info.roofOpen ? 0 : 1 } };
  for (const s of SIDES) out[`cut:${s}`] = { to: info.cutSides.includes(s) ? 0 : 1 };
  return out;
}
