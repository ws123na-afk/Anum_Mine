/**
 * Camera fly-to for the headquarters: an eased move between two camera states that rises over the
 * ground when it has far to go and never puts the eye inside a building. Pure maths, no DOM: the unit
 * runner covers the path, the duration and the reduced-motion jump.
 */
import { cameraEye, type CameraState } from '../lib/headquartersSpatial.ts';
import type { Aabb } from '../lib/hqMath.ts';

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const lerp = (a: number, b: number, w: number) => a + (b - a) * w;
export const easeInOutCubic = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

export const MIN_DURATION_MS = 450;
export const MAX_DURATION_MS = 1800;
const SAMPLES = 48;

/** World distance a flight covers: the target's travel plus half the change in distance. */
export function travelOf(from: CameraState, to: CameraState): number {
  return Math.hypot(to.target.x - from.target.x, to.target.y - from.target.y, to.target.z - from.target.z) + Math.abs(to.distance - from.distance) * 0.5;
}

/** Longer trips take longer, but with a square-root curve so a cross-campus move is not slow. Reduced motion: no time at all. */
export function flightDuration(travel: number, reducedMotion: boolean): number {
  if (reducedMotion || !(travel > 0.5)) return 0;
  return Math.round(clamp(MIN_DURATION_MS + 70 * Math.sqrt(travel), MIN_DURATION_MS, MAX_DURATION_MS));
}

/** Shortest way round for a yaw in degrees. */
function yawPath(from: number, to: number): number {
  const d = ((((to - from) % 360) + 540) % 360) - 180;
  return from + d;
}

const inside = (p: { x: number; y: number; z: number }, box: Aabb, margin: number) =>
  p.x > box.min.x - margin && p.x < box.max.x + margin && p.z > box.min.z - margin && p.z < box.max.z + margin && p.y < box.max.y + margin;

export type FlightPlan = Readonly<{
  /** Milliseconds; 0 means jump straight to the end. */
  duration: number;
  /** The camera at progress 0..1 of the time (the easing is applied inside). */
  at: (progress: number) => CameraState;
}>;

export type FlightOptions = Readonly<{
  obstacles?: readonly Aabb[];
  reducedMotion?: boolean;
  /** Space kept between the eye and an obstacle, in world units. */
  margin?: number;
}>;

/**
 * Plans a flight. Along the way the distance rises by up to a quarter of the trip (a gentle arc over the
 * ground), and any sample whose eye would sit inside a building box (plus a margin) gets extra height until it clears.
 * The extra height is smoothed, so the path stays one curve. The first and last camera are exactly `from` and `to`.
 */
export function planFlight(from: CameraState, to: CameraState, options: FlightOptions = {}): FlightPlan {
  const travel = travelOf(from, to);
  const duration = flightDuration(travel, options.reducedMotion === true);
  if (duration === 0) return { duration: 0, at: () => to };
  const margin = options.margin ?? 1.5;
  const obstacles = options.obstacles ?? [];
  const yawTo = yawPath(from.yaw, to.yaw);
  const maxD = Math.max(from.maxDistance, to.maxDistance);
  const base = (e: number, lift: number, extra: number): CameraState => {
    const pitch = clamp(lerp(from.pitch, to.pitch, e) + extra, 3, 87);
    return {
      target: { x: lerp(from.target.x, to.target.x, e), y: lerp(from.target.y, to.target.y, e), z: lerp(from.target.z, to.target.z, e) },
      distance: clamp(lerp(from.distance, to.distance, e) + lift, Math.min(from.minDistance, to.minDistance), maxD),
      yaw: lerp(from.yaw, yawTo, e), pitch, fov: lerp(from.fov, to.fov, e),
      minDistance: Math.min(from.minDistance, to.minDistance), maxDistance: maxD,
    };
  };
  const arc = Math.min(travel * 0.25, 120);
  // Extra pitch per sample that lifts the eye clear of buildings; found once, then smoothed.
  const extra = new Array<number>(SAMPLES + 1).fill(0);
  for (let i = 1; i < SAMPLES; i++) {
    const e = easeInOutCubic(i / SAMPLES);
    const lift = arc * Math.sin(Math.PI * e);
    let add = 0;
    for (let guard = 0; guard < 40; guard++) {
      const eye = cameraEye(base(e, lift, add));
      if (!obstacles.some((b) => inside(eye, b, margin))) break;
      add += 4;
      if (clamp(lerp(from.pitch, to.pitch, e) + add, 3, 87) >= 87) break;
    }
    extra[i] = add;
  }
  const smooth = extra.map((_, i) => {
    if (i === 0 || i === SAMPLES) return 0;
    const window = [extra[i - 1], extra[i], extra[i + 1]];
    return Math.max(extra[i], window.reduce((s, v) => s + v, 0) / 3);
  });
  return {
    duration,
    at: (progress) => {
      const p = clamp(progress, 0, 1);
      if (p <= 0) return from;
      if (p >= 1) return to;
      const e = easeInOutCubic(p);
      const f = p * SAMPLES, i = Math.floor(f);
      const add = lerp(smooth[i], smooth[Math.min(SAMPLES, i + 1)], f - i);
      return base(e, arc * Math.sin(Math.PI * e), add);
    },
  };
}

/** True when the camera's eye is inside any of the boxes (plus a margin). Used by tests and the bridge to check a destination. */
export function eyeInside(camera: CameraState, obstacles: readonly Aabb[], margin = 0): boolean {
  const eye = cameraEye(camera);
  return obstacles.some((b) => inside(eye, b, margin));
}
