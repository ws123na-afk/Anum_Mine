/** Types shared by the stage and the WebGL renderer. Pure: no three.js and no DOM, so the unit runner can import them. */
import type { Aabb } from './hqMath.ts';
import type { Vec3 } from './headquartersProjection.ts';
import type { ModelGroup, ModelPlacement } from './hqModels.ts';
import type { SceneryPlan } from '../hqAssets/environment/scenery.ts';
import type { AmbientMotion } from '../hqAssets/interior/ambient.ts';
import type { QualityState } from './hqQualityTiers.ts';

/** `fallbackOf`: the item is the procedural stand-in for a model group and is drawn only while that group could not be loaded. */
export type RenderItem = Readonly<{
  id: string; vertices: Float32Array; bounds: Aabb; cull?: boolean; fallbackOf?: ModelGroup;
  /** Part of a group the stage can ease in and out (a floor of the cut-away). `y` is the level the group collapses towards. */
  lift?: Readonly<{ group: string; y: number }>;
  /** Its lettered faces (the sign class) take the live board atlas (`HqRenderer.setBoards`) instead of the sign atlas: the building billboards. */
  board?: boolean;
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
/** `fontsReady` only changes the sign key: the atlas is drawn again once the Arabic face has loaded (a canvas does not wait for a web font). */
export type SignText = Readonly<{
  title: string; subtitle: string; fontsReady?: boolean;
  /** The owner's brand sign (docs/ANUM_MINE_HQ_LOGO_SIGNS.md): `brand` is part of the sign key, `paint` draws the whole 512 x 128 cell at (x, y), so the lazy sign painter never loads into the renderer. */
  brand?: string; paint?: (ctx: CanvasRenderingContext2D, x: number, y: number, k: number) => void;
}>;

/** A scenery piece the renderer moves on its own (a fan, a light glint, a lift indicator); see `hqAssets/interior/ambient.ts`. */
export type AmbientItem = RenderItem & Readonly<{ motion: AmbientMotion }>;

export type RenderScene = Readonly<{
  opaque: readonly RenderItem[];
  glass: readonly RenderItem[];
  glassAlpha?: number;
  /** Moving scenery. Frozen under reduced motion and while the tab is hidden; never drawn for a person or for work. */
  ambient?: readonly AmbientItem[];
  /** Sign lettering, in sign-cell order (see `signAtlasLayout`). */
  signs?: readonly SignText[];
  /** The region that casts and receives shadows (a sphere round the buildings); without it there are no shadows. */
  shadow?: Readonly<{ center: Vec3; radius: number }>;
  /** Loadable models (glTF) to place. Only slots a record backs are ever in here (see `hqModels.ts`). */
  models?: readonly ModelPlacement[];
  /** Soft light halos for the night glow: x, y, z, radius, r, g, b, strength per halo. */
  halos?: Float32Array;
  /** Exterior scenery in motion (traffic loops, water ripples). Drawn only while the scenery level is above 0. */
  scenery?: SceneryPlan;
  /** Targets of the lift groups named by `RenderItem.lift`. A group missing here stays where it is. */
  lifts?: Readonly<Record<string, LiftSpec>>;
}>;

export type RenderStats = {
  frames: number; lastDrawMs: number; avgDrawMs: number; drawn: number; culled: number; triangles: number; dpr: number;
  /** Shadow map size in pixels (0 = off) and whether the night glow pass is on. */
  shadowSize: number; bloom: boolean;
  /** Ambient parts in the scene, and whether they are moving right now (false under reduced motion, in a hidden tab, or at the lowest quality). */
  ambient: number; ambientMoving: boolean;
  /** The quality tier in use (0 basic to 4 photo, see `hqQualityTier.ts`) and the effects on right now, a comma list. */
  tier: number; fx: string;
  /** Revision of the plan atlas on the GPU ('' when none). */
  glassPlan?: string;
  /** Model layer: placements drawn, models loaded of those asked for, bytes fetched, load time in ms, groups that fell back. */
  /** Exterior scenery motion: level 0 to 2 (0 = still), frames drawn by its loop, the scene clock in seconds and the vehicles placed. */
  scenery: { level: number; frames: number; clock: number; vehicles: number };
  models: { placed: number; figures: number; /** People built by the character kit (not loaded from a file). */ kit?: number; loaded: number; asked: number; bytes: number; loadMs: number; fallbacks: string[]; looks: string[] };
};

export type RendererCallbacks = Readonly<{
  /** The tier, its reason or the governor's rung changed (also once at start). */
  onQuality?: (state: QualityState) => void;
  onLost?: () => void; onRestored?: () => void; onError?: (error: Error) => void; onFrame?: (vp: Float32Array, aspect: number) => void;
}>;
