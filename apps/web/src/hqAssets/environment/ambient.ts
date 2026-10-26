/**
 * Ambient motion plan for the campus: what moves for the scene's own sake, and nothing else.
 *
 * Everything here is scenery. Vehicles drive fixed loops on the ring road, water ripples spread from
 * fixed points, trees and flags sway, clouds drift, a rooftop beacon blinks. None of it is read from a
 * record and none of it means a person, an agent, work or a count: a vehicle is never "someone driving
 * to a building", it only keeps the road from looking dead. Agent and person movement is a different
 * system, backed by presence records.
 *
 * This file is pure (no three.js, no DOM, no clock): positions are functions of a clock value the
 * renderer passes in, so the same clock gives the same picture and a unit test can pin it down. The
 * sampling functions write into a caller owned object, so a frame allocates nothing.
 */
import { hex, seeded, type Rgb } from '../../lib/hqGeometry.ts';
import { CAR_PALETTE } from './vehicles.ts';
import { lengths, roundedRectPath, type P2 } from './paths.ts';

/** A closed loop a vehicle drives round: x,z pairs and the cumulative length at each point. */
export type Lane = Readonly<{ pts: Float32Array; cum: Float32Array; total: number }>;

/** 0 sedan, 1 SUV, 2 van: only the proportions differ. */
export type VehicleKind = 0 | 1 | 2;

export type AmbientVehicle = Readonly<{ lane: number; start: number; speed: number; color: Rgb; kind: VehicleKind }>;

export type AmbientPlan = Readonly<{
  lanes: readonly Lane[];
  vehicles: readonly AmbientVehicle[];
  /** x, z, radius per water surface that shows ripples (at most `MAX_RIPPLES`). */
  ripples: Float32Array;
}>;

export const MAX_RIPPLES = 4;
/** Lane centre distance from the road centre line, and the vehicle speeds in metres per second (a calm 30 to 40 km/h). */
export const LANE_OFFSET = 2.3;
export const LANE_SPEEDS: readonly [number, number] = [8.2, 9.4];
/** Vehicles are at least this far apart along a lane (centre to centre), so a loop never has two on top of each other. */
export const MIN_GAP = 17;

export const EMPTY_PLAN: AmbientPlan = { lanes: [], vehicles: [], ripples: new Float32Array(0) };

export function laneOf(path: readonly P2[]): Lane {
  const { at, total } = lengths(path, true);
  const pts = new Float32Array(path.length * 2);
  const cum = new Float32Array(path.length + 1);
  path.forEach((p, i) => { pts[i * 2] = p[0]; pts[i * 2 + 1] = p[1]; cum[i] = at[i]; });
  cum[path.length] = total;
  return { pts, cum, total };
}

export type RingGeometry = Readonly<{ cx: number; cz: number; halfW: number; halfD: number; cornerRadius?: number }>;

export type TrafficOptions = Readonly<{ seed?: string; count?: number }>;

/**
 * Two loops on the ring road, keeping to the right as the country does: the outer lane runs
 * counter-clockwise on screen and the inner one clockwise. Vehicles are spread evenly with a small
 * seeded jitter; every vehicle of a lane has the lane's speed, so none ever catches up with another.
 */
export function trafficPlan(ring: RingGeometry, o: TrafficOptions = {}): Pick<AmbientPlan, 'lanes' | 'vehicles'> {
  const r = ring.cornerRadius ?? 16;
  const outer = roundedRectPath(ring.cx, ring.cz, ring.halfW + LANE_OFFSET, ring.halfD + LANE_OFFSET, r + LANE_OFFSET, 6).slice().reverse();
  const inner = roundedRectPath(ring.cx, ring.cz, ring.halfW - LANE_OFFSET, ring.halfD - LANE_OFFSET, Math.max(2, r - LANE_OFFSET), 6);
  const lanes = [laneOf(outer), laneOf(inner)];
  const rnd = seeded(`ambient-traffic:${o.seed ?? 'campus'}`);
  const palette = [...CAR_PALETTE, hex('#e8ebee'), hex('#2f4b66')];
  const wanted = o.count ?? Math.round((lanes[0].total + lanes[1].total) / 70);
  const count = Math.max(2, Math.min(18, wanted));
  const vehicles: AmbientVehicle[] = [];
  lanes.forEach((lane, li) => {
    const n = Math.max(1, Math.min(Math.floor(lane.total / MIN_GAP), li === 0 ? Math.ceil(count / 2) : Math.floor(count / 2)));
    const gap = lane.total / n;
    const speed = LANE_SPEEDS[li] + rnd() * 0.4;
    const phase = rnd() * gap;
    for (let i = 0; i < n; i++) {
      const jitter = n > 1 ? (rnd() - 0.5) * Math.min(4, gap - MIN_GAP) : 0;
      const roll = rnd();
      vehicles.push({
        lane: li, start: (phase + i * gap + Math.max(-2, jitter) + lane.total) % lane.total, speed,
        color: palette[Math.floor(rnd() * palette.length)], kind: roll < 0.55 ? 0 : roll < 0.88 ? 1 : 2,
      });
    }
  });
  return { lanes, vehicles };
}

export type LanePose = { x: number; z: number; /** Heading in radians from +z towards +x. */ heading: number };

/** Position and heading at arc length `s` of a lane (wraps). Writes into `out`; allocates nothing. */
export function sampleLane(lane: Lane, s: number, out: LanePose): LanePose {
  const total = lane.total;
  const ss = ((s % total) + total) % total;
  const n = lane.pts.length / 2;
  // The segment count is small (tens of points), so a scan beats a binary search in clarity and is as fast.
  let i = 0;
  while (i < n - 1 && lane.cum[i + 1] <= ss) i++;
  const j = (i + 1) % n;
  const x0 = lane.pts[i * 2], z0 = lane.pts[i * 2 + 1], x1 = lane.pts[j * 2], z1 = lane.pts[j * 2 + 1];
  const len = lane.cum[i + 1] - lane.cum[i] || 1;
  const t = (ss - lane.cum[i]) / len;
  out.x = x0 + (x1 - x0) * t; out.z = z0 + (z1 - z0) * t;
  out.heading = Math.atan2(x1 - x0, z1 - z0);
  return out;
}

/** Where vehicle `i` is at clock `t` seconds. */
export function vehiclePose(plan: Pick<AmbientPlan, 'lanes' | 'vehicles'>, i: number, t: number, out: LanePose): LanePose {
  const v = plan.vehicles[i];
  return sampleLane(plan.lanes[v.lane], v.start + v.speed * t, out);
}

/** Water surfaces that ripple: x, z, radius triples, the biggest first, capped at `MAX_RIPPLES`. */
export function ripplesOf(surfaces: readonly Readonly<{ x: number; z: number; r: number }>[]): Float32Array {
  const list = [...surfaces].filter((s) => Number.isFinite(s.x) && Number.isFinite(s.z) && s.r > 0).sort((a, b) => b.r - a.r).slice(0, MAX_RIPPLES);
  const out = new Float32Array(list.length * 3);
  list.forEach((s, i) => { out[i * 3] = s.x; out[i * 3 + 1] = s.z; out[i * 3 + 2] = s.r; });
  return out;
}

// ---------------------------------------------------------------- policy

/**
 * Ambient levels. 0 = still (the picture a software renderer, a reduced motion preference or a hidden tab gets),
 * 1 = vehicles, water and clouds at 15 frames a second, 2 = adds sway, window shimmer and the beacon at 30.
 */
export type AmbientLevel = 0 | 1 | 2;
export const AMBIENT_FPS: Readonly<Record<AmbientLevel, number>> = { 0: 0, 1: 15, 2: 30 };

export type AmbientInputs = Readonly<{
  /** A software rasteriser (SwiftShader, llvmpipe): everything stays still unless a person or a test pins a level. */
  software: boolean; reducedMotion: boolean; hidden: boolean;
  /** `localStorage["anum.hq.ambient"]` as 0 to 2, or null. Reduced motion and a hidden tab still win. */
  forced: AmbientLevel | null;
  /** Canvas pixels (device): above 4 million the top level is too heavy. */
  pixels: number;
  /** How many notches sustained slow frames have taken off so far. */
  slowSteps: number;
}>;

export function ambientLevelFor(i: AmbientInputs): AmbientLevel {
  if (i.reducedMotion || i.hidden) return 0;
  const base: AmbientLevel = i.forced !== null ? i.forced : i.software ? 0 : i.pixels > 4_000_000 ? 1 : 2;
  return Math.max(0, base - i.slowSteps) as AmbientLevel;
}

export function readAmbientOverride(storage: Pick<Storage, 'getItem'> | null): AmbientLevel | null {
  try {
    const raw = storage?.getItem('anum.hq.ambient');
    if (raw === null || raw === undefined) return null;
    const n = Number(raw);
    return n === 0 || n === 1 || n === 2 ? n : null;
  } catch { return null; }
}
