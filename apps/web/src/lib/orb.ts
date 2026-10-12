// The voice assistant's 3D presence (docs/voice.md#the-orb): colours per state from the design
// tokens in styles.css, the motion each state drives, and the faceted core's geometry. Pure and
// DOM-free so `pnpm test:unit` covers it; Orb3D.tsx turns it into a WebGL scene.

export type OrbState = 'idle' | 'listening' | 'thinking' | 'speaking';
export const ORB_STATES: readonly OrbState[] = ['idle', 'listening', 'thinking', 'speaking'];

export type Rgb = [number, number, number];

/** The design tokens the orb is painted with (styles.css :root), and their shipped values. */
export const ORB_TOKENS = {
  sky: ['--d-sky', '#7dd3fc'],
  violet: ['--d-violet', '#a78bfa'],
  rose: ['--d-rose', '#f9a8d4'],
  markA: ['--d-mark-a', '#4f8bff'],
  markB: ['--d-mark-b', '#7a6bff'],
  markC: ['--d-mark-c', '#b56bff'],
  alert: ['--d-alert-rim', '#ffb547'],
} as const satisfies Record<string, readonly [string, string]>;
export type OrbTokenName = keyof typeof ORB_TOKENS;
export type OrbColors = Record<OrbTokenName, Rgb>;

/** `#rgb` or `#rrggbb` as 0..1 channels; null for anything else. */
export function hexToRgb(value: string): Rgb | null {
  const hex = value.trim().replace(/^#/, '');
  const full = hex.length === 3 ? hex.split('').map((c) => c + c).join('') : hex;
  if (!/^[0-9a-f]{6}$/i.test(full)) return null;
  return [0, 2, 4].map((at) => parseInt(full.slice(at, at + 2), 16) / 255) as Rgb;
}

/** Token colours, read through `read` (the computed style of :root); a missing token keeps its default. */
export function orbColors(read: (name: string) => string = () => ''): OrbColors {
  const out = {} as OrbColors;
  for (const [key, [token, fallback]] of Object.entries(ORB_TOKENS) as [OrbTokenName, readonly [string, string]][]) {
    out[key] = hexToRgb(read(token)) ?? (hexToRgb(fallback) as Rgb);
  }
  return out;
}

/** What each part of the scene is painted with in one state. */
export interface OrbPalette {
  /** The faceted glass core. */
  core: Rgb;
  /** The light inside the core. */
  heart: Rgb;
  /** The Fresnel rim and the fill light. */
  rim: Rgb;
  /** The inner and outer orbit rings. */
  ringA: Rgb;
  ringB: Rgb;
  /** The orbiting particles. */
  spark: Rgb;
}

/**
 * Each state has its own hue family so it reads at a glance, not only by motion: idle is the
 * brand violet, listening turns to sky, thinking to deep purple, speaking warms to rose.
 */
export function orbPalette(state: OrbState, c: OrbColors): OrbPalette {
  switch (state) {
    case 'listening': return { core: c.markA, heart: c.sky, rim: c.sky, ringA: c.sky, ringB: c.markA, spark: c.sky };
    case 'thinking': return { core: c.markC, heart: c.violet, rim: c.violet, ringA: c.markC, ringB: c.rose, spark: c.violet };
    case 'speaking': return { core: c.markB, heart: c.rose, rim: c.rose, ringA: c.rose, ringB: c.markC, spark: c.rose };
    default: return { core: c.markB, heart: c.markC, rim: c.sky, ringA: c.markA, ringB: c.violet, spark: c.violet };
  }
}

/** How a state moves. Values ease toward the target each frame, so states morph instead of jumping. */
export interface OrbMotion {
  /** Core turns per second (radians). */
  spin: number;
  /** Ring turns per second (radians), inner and outer turn opposite ways. */
  ringSpeed: number;
  /** Particle orbit speed multiplier. */
  orbit: number;
  /** Inner light, 0..1.5. */
  energy: number;
  /** Breathing amplitude of the core (scale). */
  pulse: number;
  /** Breathing rate (radians per second). */
  pulseRate: number;
  /** How far the rings tilt toward the viewer (radians). */
  tilt: number;
  /** Facet shimmer speed. */
  shimmer: number;
  /** Halo strength, 0..1.5. */
  glow: number;
}

export const ORB_MOTION: Record<OrbState, OrbMotion> = {
  idle: { spin: 0.12, ringSpeed: 0.18, orbit: 0.35, energy: 0.55, pulse: 0.015, pulseRate: 1.2, tilt: 0.0, shimmer: 0.6, glow: 0.65 },
  listening: { spin: 0.2, ringSpeed: 0.35, orbit: 0.7, energy: 0.85, pulse: 0.02, pulseRate: 2.4, tilt: 0.45, shimmer: 1.2, glow: 0.95 },
  thinking: { spin: 0.65, ringSpeed: 1.6, orbit: 1.9, energy: 0.75, pulse: 0.012, pulseRate: 3.2, tilt: 0.2, shimmer: 4.5, glow: 0.8 },
  speaking: { spin: 0.24, ringSpeed: 0.5, orbit: 0.9, energy: 1.15, pulse: 0.05, pulseRate: 6.0, tilt: 0.3, shimmer: 1.6, glow: 1.15 },
};

/** The microphone level drives listening only, and never more than the scene has room for. */
export function voiceLevel(state: OrbState, level: number): number {
  if (state !== 'listening' || !Number.isFinite(level)) return 0;
  return Math.min(1, Math.max(0, level));
}

/** Fewer particles on small canvases (phones), never zero. */
export function particleCount(sizePx: number): number {
  return Math.round(Math.min(220, Math.max(70, sizePx * 0.6)));
}

/** Device pixel ratio for the canvas: sharp on HiDPI, capped so phones keep their battery. */
export function orbDpr(devicePixelRatio: number): number {
  return Math.min(Number.isFinite(devicePixelRatio) && devicePixelRatio > 0 ? devicePixelRatio : 1, 1.5);
}

/** The canvas is larger than the button so the rings and halo can reach past it (no layout change). */
export const ORB_CANVAS_SCALE = 1.5;

/**
 * A faceted crystal: an icosahedron subdivided once (80 faces), each vertex pushed in or out a
 * little by a fixed hash so the facets have uneven sizes, like a cut stone. Returned unindexed
 * with one normal per face (flat shading), barycentric coordinates (facet edges) and a seed per
 * face (shimmer), ready for `gl.drawArrays`.
 */
export function crystalGeometry(radius = 1, irregularity = 0.06): { position: Float32Array; normal: Float32Array; bary: Float32Array; seed: Float32Array; faces: number } {
  const t = (1 + Math.sqrt(5)) / 2;
  let verts: number[][] = [[-1, t, 0], [1, t, 0], [-1, -t, 0], [1, -t, 0], [0, -1, t], [0, 1, t], [0, -1, -t], [0, 1, -t], [t, 0, -1], [t, 0, 1], [-t, 0, -1], [-t, 0, 1]];
  let faces = [[0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11], [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8], [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9], [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1]];
  const unit = (v: number[]) => { const l = Math.hypot(v[0], v[1], v[2]); return [v[0] / l, v[1] / l, v[2] / l]; };
  verts = verts.map(unit);
  const cache = new Map<string, number>();
  const mid = (a: number, b: number) => {
    const key = a < b ? `${a}:${b}` : `${b}:${a}`;
    const hit = cache.get(key);
    if (hit !== undefined) return hit;
    verts.push(unit([(verts[a][0] + verts[b][0]) / 2, (verts[a][1] + verts[b][1]) / 2, (verts[a][2] + verts[b][2]) / 2]));
    cache.set(key, verts.length - 1);
    return verts.length - 1;
  };
  faces = faces.flatMap(([a, b, c]) => { const ab = mid(a, b), bc = mid(b, c), ca = mid(c, a); return [[a, ab, ca], [b, bc, ab], [c, ca, bc], [ab, bc, ca]]; });
  // A fixed hash per vertex: the same stone every time, no Math.random.
  const scaled = verts.map((v, i) => { const h = Math.sin(i * 12.9898 + 78.233) * 43758.5453; const k = 1 + (h - Math.floor(h) - 0.5) * 2 * irregularity; return v.map((x) => x * radius * k); });
  const position = new Float32Array(faces.length * 9), normal = new Float32Array(faces.length * 9), bary = new Float32Array(faces.length * 9), seed = new Float32Array(faces.length * 3);
  faces.forEach(([a, b, c], f) => {
    const [p, q, r] = [scaled[a], scaled[b], scaled[c]];
    const u = [q[0] - p[0], q[1] - p[1], q[2] - p[2]], v = [r[0] - p[0], r[1] - p[1], r[2] - p[2]];
    let n = unit([u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]);
    const centre = [(p[0] + q[0] + r[0]) / 3, (p[1] + q[1] + r[1]) / 3, (p[2] + q[2] + r[2]) / 3];
    if (n[0] * centre[0] + n[1] * centre[1] + n[2] * centre[2] < 0) n = n.map((x) => -x);
    [p, q, r].forEach((point, k) => {
      position.set(point, f * 9 + k * 3);
      normal.set(n, f * 9 + k * 3);
      bary.set([k === 0 ? 1 : 0, k === 1 ? 1 : 0, k === 2 ? 1 : 0], f * 9 + k * 3);
      const h = Math.sin(f * 4.1414 + 1.7) * 9301.17;
      seed[f * 3 + k] = h - Math.floor(h);
    });
  });
  return { position, normal, bary, seed, faces: faces.length };
}

/**
 * Orbit seeds for the particles: radius, phase, speed and inclination, from a fixed hash so the
 * constellation is stable between renders.
 */
export function particleSeeds(count: number): Float32Array {
  const out = new Float32Array(count * 4);
  const rand = (i: number, k: number) => { const h = Math.sin(i * 127.1 + k * 311.7) * 43758.5453; return h - Math.floor(h); };
  for (let i = 0; i < count; i += 1) {
    out.set([1.15 + rand(i, 1) * 0.42, rand(i, 2) * Math.PI * 2, 0.35 + rand(i, 3) * 0.9, (rand(i, 4) - 0.5) * Math.PI], i * 4);
  }
  return out;
}
