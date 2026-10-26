/**
 * The photographic tier's light and grade decisions, as pure numbers (no three.js, no DOM): where the camera-fitted shadow sits,
 * which warm interior lights are on, where the sun is on screen for the light shafts, and the look of each light preset (day, blue
 * hour, night). The renderer only applies what this returns; `test/hqLightRig.test.ts` pins it.
 */
import { cameraEye, type CameraState } from './headquartersSpatial.ts';
import { projectToNdc, type Mat4 } from './hqMath.ts';
import { DUSK, lightingFor, type Triple } from './hqLighting.ts';

export type Fit = Readonly<{ center: { x: number; y: number; z: number }; radius: number }>;

/**
 * One shadow map fitted to what the camera looks at (the cascade the picture needs, instead of the whole campus): a sphere round the
 * look-at point whose radius follows the camera distance, never larger than the region the scene casts shadows in and never
 * smaller than `minRadius`. The centre is snapped to whole shadow texels so the edges do not crawl when the camera moves.
 */
export function fitShadow(camera: CameraState, region: Fit, mapSize: number, minRadius = 24): Fit {
  const reach = Math.max(minRadius, Math.min(region.radius, camera.distance * 0.95 + 12));
  const texel = (2 * reach) / Math.max(1, mapSize);
  const snap = (v: number) => Math.round(v / (texel * 16)) * texel * 16;
  const tx = camera.target.x, tz = camera.target.z;
  // The fitted sphere stays inside the region: pull its centre in when it would leave it.
  const dx = tx - region.center.x, dz = tz - region.center.z, d = Math.hypot(dx, dz), max = Math.max(0, region.radius - reach * 0.6);
  const k = d > max && d > 0 ? max / d : 1;
  return { center: { x: snap(region.center.x + dx * k), y: region.center.y, z: snap(region.center.z + dz * k) }, radius: reach };
}

/** Halos are x, y, z, radius, r, g, b, strength; the lights are the nearest `count` to the look-at point within `range`, strongest first on ties. */
export type PointLight = Readonly<{ x: number; y: number; z: number; color: Triple; distance: number; weight: number }>;
export const HALO_FLOATS = 8;

export function pickLights(halos: Float32Array | undefined, target: { x: number; y: number; z: number }, count: number, range = 60): PointLight[] {
  if (!halos || count <= 0) return [];
  const found: Array<PointLight & { d: number }> = [];
  for (let i = 0; i + HALO_FLOATS <= halos.length; i += HALO_FLOATS) {
    const d = Math.hypot(halos[i] - target.x, halos[i + 2] - target.z);
    if (d > range) continue;
    found.push({ x: halos[i], y: halos[i + 1], z: halos[i + 2], color: [halos[i + 4], halos[i + 5], halos[i + 6]], distance: Math.max(4, halos[i + 3] * 1.6), weight: halos[i + 7], d });
  }
  found.sort((a, b) => a.d - b.d || b.weight - a.weight || a.x - b.x || a.z - b.z);
  return found.slice(0, count).map(({ d, ...light }) => { void d; return light; });
}

/** How strongly the interior point lights shine at a blend value (0 day, DUSK, 1 night): none by day, full from the blue hour. */
export const pointLightGain = (night: number): number => { const t = Math.max(0, Math.min(1, (night - 0.15) / (DUSK - 0.15))); return t * t * (3 - 2 * t); };

/** A colour temperature (Kelvin) as linear-ish RGB, for warm interior lights (2700 K) and cool ones (4500 K). */
export function kelvin(k: number): Triple {
  const t = Math.max(1000, Math.min(12000, k)) / 100;
  const r = t <= 66 ? 255 : 329.698727446 * (t - 60) ** -0.1332047592;
  const g = t <= 66 ? 99.4708025861 * Math.log(t) - 161.1195681661 : 288.1221695283 * (t - 60) ** -0.0755148492;
  const b = t >= 66 ? 255 : t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307;
  const c = (x: number) => Math.max(0, Math.min(255, x)) / 255;
  return [c(r), c(g), c(b)];
}

/**
 * Where the sun (or moon) is on screen, for the light shafts: NDC position, whether it is in front of the camera, and a weight that
 * fades the shafts out when the light is far outside the picture or behind the camera.
 */
export function sunOnScreen(vp: Mat4, camera: CameraState, travel: Triple): Readonly<{ x: number; y: number; weight: number }> {
  const eye = cameraEye(camera);
  const p = { x: eye.x - travel[0] * 500, y: eye.y - travel[1] * 500, z: eye.z - travel[2] * 500 };
  const s = projectToNdc(vp, p);
  if (s.depth <= 1e-6) return { x: 0, y: 0, weight: 0 };
  const off = Math.max(Math.abs(s.x), Math.abs(s.y));
  return { x: s.x, y: s.y, weight: Math.max(0, Math.min(1, 1.6 - off)) };
}

/** The look of one light preset: every number is a strength the renderer feeds to its passes. */
export type Grade = Readonly<{ exposure: number; vignette: number; grain: number; shafts: number; dof: number; ao: number; saturation: number; contrast: number; contact: number }>;

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

/** Day is crisp, the blue hour is warmer and moodier with the strongest shafts, night is darker with more grain and vignette. */
export function gradeFor(night: number): Grade {
  const n = Math.max(0, Math.min(1, night));
  const [a, b, t] = n <= DUSK ? [DAY, HOUR, n / DUSK] as const : [HOUR, NIGHT, (n - DUSK) / (1 - DUSK)] as const;
  const out = {} as Record<keyof Grade, number>;
  for (const k of Object.keys(a) as Array<keyof Grade>) out[k] = lerp(a[k], b[k], t);
  // AgX has no built-in 1/0.6 exposure gain like ACES and is flatter: the grade lifts exposure, saturation and contrast to compensate.
  out.exposure *= lightingFor(n).exposure;
  return out;
}
const DAY: Grade = { exposure: 1.28, vignette: 0.24, grain: 0.025, shafts: 0.35, dof: 0.6, ao: 1.5, saturation: 1.3, contrast: 0.5, contact: 0.5 };
const HOUR: Grade = { exposure: 1.23, vignette: 0.32, grain: 0.035, shafts: 0.6, dof: 0.7, ao: 1.4, saturation: 1.3, contrast: 0.45, contact: 0.7 };
const NIGHT: Grade = { exposure: 1.2, vignette: 0.38, grain: 0.05, shafts: 0.25, dof: 0.8, ao: 1.5, saturation: 1.2, contrast: 0.4, contact: 0.9 };

/** The part of the picture kept sharp: the depth range around the focus distance, as a fraction of it (a camera at a building gets a blurred skyline). */
export function focusRange(distance: number): Readonly<{ focus: number; aperture: number; maxBlur: number }> {
  const focus = Math.max(8, distance);
  return { focus, aperture: 0.00005 * Math.min(1, 120 / focus), maxBlur: 0.004 };
}
