/**
 * Ambient motion for the interiors: scenery only. Nothing here moves because someone works, and
 * nothing implies a person: a lift indicator, a glint sweeping along a ceiling light, server rack
 * status lights, leaves in a draught and a spinning fan. Desks, screens and figures never get a
 * motion (a vacant desk stays vacant), and the renderer freezes every part under reduced motion and
 * while the tab is hidden (`lib/hqThreeRenderer.ts`).
 *
 * The kits mark the triangles of a moving piece with a tag made by `ambientTag`; the scene splits
 * them out (`splitAmbient`) into one part per tag so the renderer can move a part as a whole. The
 * pivot of a part is the middle of its bounds, so a tag stays valid when a room is turned or scaled.
 */
import type { MeshBuilder } from '../../lib/hqGeometry.ts';
import type { Aabb } from '../../lib/hqMath.ts';

export type AmbientKind =
  /** Turn about the vertical axis through the part's middle (a fan, a vent). */
  | 'spin'
  /** Drift a few centimetres in a small loop (leaves in a draught). */
  | 'sway'
  /** Travel back and forth along `axis` by `amp` metres (a glint along a ceiling light). */
  | 'slide'
  /** Step between a few heights with a pause at each (a lift indicator). */
  | 'lift'
  /** Visible for half of each period; `phase` 0 and 1 are opposite (rack status lights). */
  | 'blink';

export type AmbientMotion = Readonly<{
  kind: AmbientKind;
  /** Seconds for one full cycle. */
  period: number;
  /** Metres for slide, lift and sway; unused for spin and blink. */
  amp: number;
  axis: 'x' | 'y' | 'z';
  /** 0 or 1 for blink; a fraction of the period for the others. */
  phase: number;
}>;

export type AmbientPart = Readonly<{
  id: string;
  motion: AmbientMotion;
  vertices: Float32Array;
  bounds: Aabb;
}>;

const PREFIX = 'amb|';

export function ambientTag(kind: AmbientKind, period: number, amp = 0, axis: 'x' | 'y' | 'z' = 'y', phase = 0): string {
  return `${PREFIX}${kind}|${period}|${amp}|${axis}|${phase}`;
}

export function parseAmbientTag(tag: string | null): AmbientMotion | null {
  if (!tag || !tag.startsWith(PREFIX)) return null;
  const [kind, period, amp, axis, phase] = tag.slice(PREFIX.length).split('|');
  if (kind !== 'spin' && kind !== 'sway' && kind !== 'slide' && kind !== 'lift' && kind !== 'blink') return null;
  if (axis !== 'x' && axis !== 'y' && axis !== 'z') return null;
  const p = Number(period), a = Number(amp), f = Number(phase);
  if (!Number.isFinite(p) || p <= 0 || !Number.isFinite(a) || !Number.isFinite(f)) return null;
  return { kind, period: p, amp: a, axis, phase: f };
}

/** The ambient tags present in a builder, in first-seen order. */
export function ambientTagsOf(b: MeshBuilder): string[] {
  const seen = new Set<string>();
  for (const tag of b.triangleTags()) if (tag && tag.startsWith(PREFIX)) seen.add(tag);
  return [...seen];
}

function boundsOf(v: Float32Array, stride: number): Aabb {
  let x0 = Infinity, y0 = Infinity, z0 = Infinity, x1 = -Infinity, y1 = -Infinity, z1 = -Infinity;
  for (let i = 0; i < v.length; i += stride) {
    x0 = Math.min(x0, v[i]); y0 = Math.min(y0, v[i + 1]); z0 = Math.min(z0, v[i + 2]);
    x1 = Math.max(x1, v[i]); y1 = Math.max(y1, v[i + 1]); z1 = Math.max(z1, v[i + 2]);
  }
  return { min: { x: x0, y: y0, z: z0 }, max: { x: x1, y: y1, z: z1 } };
}

/** Parts for the tags of `split.byTag` (the tags `ambientTagsOf` returned), merged by tag. `prefix` keeps ids unique per floor. */
export function ambientParts(byTag: ReadonlyMap<string, Float32Array>, stride: number, prefix: string): AmbientPart[] {
  const out: AmbientPart[] = [];
  let i = 0;
  for (const [tag, vertices] of byTag) {
    const motion = parseAmbientTag(tag);
    if (!motion || vertices.length === 0) continue;
    out.push({ id: `${prefix}-${motion.kind}-${i++}`, motion, vertices, bounds: boundsOf(vertices, stride) });
  }
  return out;
}

/**
 * The pose of a part at time `t` (seconds), as a rotation about the vertical axis,
 * a translation and a visibility. Pure so a test can check the motion without a renderer; `frozen`
 * (reduced motion) returns the rest pose.
 */
export type AmbientPose = Readonly<{ spin: number; dx: number; dy: number; dz: number; visible: boolean }>;
export const REST_POSE: AmbientPose = { spin: 0, dx: 0, dy: 0, dz: 0, visible: true };

export function poseAt(m: AmbientMotion, t: number, frozen: boolean): AmbientPose {
  if (frozen) return m.kind === 'blink' ? { ...REST_POSE, visible: m.phase === 0 } : REST_POSE;
  const u = (((t / m.period) + m.phase) % 1 + 1) % 1;
  switch (m.kind) {
    case 'spin': return { ...REST_POSE, spin: u * Math.PI * 2 };
    case 'sway': return { ...REST_POSE, dx: Math.sin(u * Math.PI * 2) * m.amp, dz: Math.cos(u * Math.PI * 2) * m.amp * 0.6 };
    case 'slide': {
      const tri = u < 0.5 ? u * 2 : 2 - u * 2;
      const d = (tri - 0.5) * m.amp;
      return { ...REST_POSE, dx: m.axis === 'x' ? d : 0, dy: m.axis === 'y' ? d : 0, dz: m.axis === 'z' ? d : 0 };
    }
    case 'lift': {
      // Four stops up and down with a pause at each stop.
      const stops = 4, seg = u < 0.5 ? u * 2 : 2 - u * 2;
      const step = Math.min(stops - 1, Math.floor(seg * stops));
      return { ...REST_POSE, dy: (step / (stops - 1)) * m.amp };
    }
    case 'blink': {
      const on = u < 0.5;
      return { ...REST_POSE, visible: m.phase === 0 ? on : !on };
    }
  }
}
