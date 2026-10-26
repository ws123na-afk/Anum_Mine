/** Types shared by the stage and the WebGL renderer. Pure: no three.js and no DOM, so the unit runner can import them. */
import type { Aabb } from './hqMath.ts';
import type { Vec3 } from './headquartersProjection.ts';
import type { ModelGroup, ModelPlacement } from './hqModels.ts';

/** `fallbackOf`: the item is the procedural stand-in for a model group and is drawn only while that group could not be loaded. */
export type RenderItem = Readonly<{
  id: string; vertices: Float32Array; bounds: Aabb; cull?: boolean; fallbackOf?: ModelGroup;
  /** Part of a group the stage can ease in and out (a floor of the cut-away). `y` is the level the group collapses towards. */
  lift?: Readonly<{ group: string; y: number }>;
}>;

/**
 * Where a lift group should be: 1 is fully there, 0 is collapsed flat and hidden. `from` starts the motion at a value
 * (a floor that was just on screen as the active floor); without it the group eases from where it is now, and a group the
 * renderer has not seen before jumps straight to `to`.
 */
export type LiftSpec = Readonly<{ to: number; from?: number }>;
/** Length of the cut-away motion in milliseconds, and its easing (ease in and out, cubic). Exported so tests pin them. */
export const LIFT_MS = 360;
export const easeLift = (t: number): number => { const x = Math.max(0, Math.min(1, t)); return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; };

/** The text of one sign (the real building name over the ANUM MINE wordmark). Index matches the sign cell the scene was built with. */
export type SignText = Readonly<{ title: string; subtitle: string }>;

export type RenderScene = Readonly<{
  opaque: readonly RenderItem[];
  glass: readonly RenderItem[];
  glassAlpha?: number;
  /** Sign lettering, in sign-cell order (see `signAtlasLayout`). */
  signs?: readonly SignText[];
  /** The region that casts and receives shadows (a sphere round the buildings); without it there are no shadows. */
  shadow?: Readonly<{ center: Vec3; radius: number }>;
  /** Loadable models (glTF) to place. Only slots a record backs are ever in here (see `hqModels.ts`). */
  models?: readonly ModelPlacement[];
  /** Soft light halos for the night glow: x, y, z, radius, r, g, b, strength per halo. */
  halos?: Float32Array;
  /** Targets of the lift groups named by `RenderItem.lift`. A group missing here stays where it is. */
  lifts?: Readonly<Record<string, LiftSpec>>;
}>;

export type RenderStats = {
  frames: number; lastDrawMs: number; avgDrawMs: number; drawn: number; culled: number; triangles: number; dpr: number;
  /** Shadow map size in pixels (0 = off) and whether the night glow pass is on. */
  shadowSize: number; bloom: boolean;
  /** Model layer: placements drawn, models loaded of those asked for, bytes fetched, load time in ms, groups that fell back. */
  models: { placed: number; figures: number; loaded: number; asked: number; bytes: number; loadMs: number; fallbacks: string[]; looks: string[] };
};

export type RendererCallbacks = Readonly<{
  onLost?: () => void; onRestored?: () => void; onError?: (error: Error) => void; onFrame?: (vp: Float32Array, aspect: number) => void;
}>;
