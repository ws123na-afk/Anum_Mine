/**
 * The walking figure of an agent (docs/ANUM_MINE_HQ_AGENT_WALKERS.md): the same people as at the desks (`figures.ts`: same look from the same
 * seed, head, torso and colours) cut into five parts so a layer can swing the legs and arms. Parts are built around their pivots (hip, shoulder)
 * with the feet on y = 0 and the face towards +z. Each part is a vertex buffer in the shared 13-float layout.
 */
import { MeshBuilder, hex, type Rgb } from '../../lib/hqGeometry.ts';
import { Local } from './local.ts';
import { head, lookFor, torso } from './figures.ts';
import type { FigureLook } from '../../lib/hqAvatar.ts';

export const HIP_Y = 0.92;
export const SHOULDER_Y = 1.43;
const SHOES = hex('#14181d');

export type WalkerParts = Readonly<{
  body: Float32Array;
  legL: Float32Array; legR: Float32Array; armL: Float32Array; armR: Float32Array;
  /** Pivot of each swinging part in the figure's frame (x, y, z). */
  pivots: Readonly<{ legL: readonly [number, number, number]; legR: readonly [number, number, number]; armL: readonly [number, number, number]; armR: readonly [number, number, number] }>;
}>;

const part = (draw: (L: Local) => void): Float32Array => { const b = new MeshBuilder(); draw(new Local(b, 0, 0, 0, 0)); return b.toFloat32(); };

export function walkerParts(seed: string, styled: FigureLook): WalkerParts {
  const look = lookFor(seed, styled);
  const body = part((L) => {
    L.box(-0.18, HIP_Y - 0.06, -0.1, 0.36, 0.16, 0.22, look.trousers);
    torso(L, look, [0, HIP_Y + 0.04, 0], [0, 1.44, 0], 1.48, 0);
    head(L, look, 1.7, 0.02, styled.headset);
  });
  const leg = (sx: -1 | 1): Float32Array => part((L) => {
    const trousers: Rgb = look.trousers;
    L.limb([0, 0, 0], [0, -(HIP_Y - 0.08), 0], 0.15, 0.15, 0.09, 0.09, trousers);
    L.box(-0.055, -HIP_Y, -0.06, 0.11, 0.07, 0.27, SHOES);
    void sx;
  });
  const arm = (): Float32Array => part((L) => {
    const sleeve = look.jacket ?? look.shirt;
    L.limb([0, 0, 0], [0, -0.26, 0.01], 0.085, 0.085, 0.07, 0.07, sleeve);
    L.limb([0, -0.26, 0.01], [0, -0.5, 0.05], 0.07, 0.07, 0.055, 0.055, sleeve, { cap: true });
    L.limb([0, -0.5, 0.05], [0, -0.58, 0.06], 0.06, 0.035, 0.05, 0.03, look.skin, { cap: true });
  });
  return { body, legL: leg(-1), legR: leg(1), armL: arm(), armR: arm(), pivots: { legL: [-0.09, HIP_Y, 0], legR: [0.09, HIP_Y, 0], armL: [-0.24, SHOULDER_Y, 0], armR: [0.24, SHOULDER_Y, 0] } };
}
