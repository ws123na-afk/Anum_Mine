/** Types shared by the stage and the WebGL renderer. Pure: no three.js and no DOM, so the unit runner can import them. */
import type { Aabb } from './hqMath.ts';
import type { Vec3 } from './headquartersProjection.ts';

export type RenderItem = Readonly<{ id: string; vertices: Float32Array; bounds: Aabb; cull?: boolean }>;

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
}>;

export type RenderStats = {
  frames: number; lastDrawMs: number; avgDrawMs: number; drawn: number; culled: number; triangles: number; dpr: number;
  /** Shadow map size in pixels (0 = off) and whether the night glow pass is on. */
  shadowSize: number; bloom: boolean;
};

export type RendererCallbacks = Readonly<{
  onLost?: () => void; onRestored?: () => void; onError?: (error: Error) => void; onFrame?: (vp: Float32Array, aspect: number) => void;
}>;
