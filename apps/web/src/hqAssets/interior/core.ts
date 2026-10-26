/**
 * The service core of a cut-away floor: a lift bank with call panels and floor indicators, a straight stair flight
 * with a glass balustrade, a stone lobby floor and a floor sign. It is architecture, not data: no lift car carries
 * anyone and nothing here implies a person. The indicator lights are ambient scenery (`ambient.ts`): they step
 * through the stops on their own, never to show what the building is doing.
 */
import { MAT, hex, scale, type MeshBuilder } from '../../lib/hqGeometry.ts';
import type { SignCell } from '../../lib/hqTextures.ts';
import { WARM } from '../../lib/hqProps.ts';
import { ambientTag } from './ambient.ts';
import { WALL_HEIGHT } from './shell.ts';

export const CORE_WIDTH = 4.6;
export const CORE_DEPTH = 9.0;

const STEEL = hex('#aeb8c2');
const DARK = hex('#26313b');
const STONE = hex('#e3dccb');

export type CoreOptions = Readonly<{
  /** Minimum corner of the core on the plan. */
  x: number; z: number; y: number;
  /** The sign atlas cell with the floor's name; null draws no sign. */
  plate: SignCell | null;
  ghost: boolean;
}>;

export function drawCore(opaque: MeshBuilder, glass: MeshBuilder, amb: MeshBuilder, o: CoreOptions): void {
  const { x, z, y, ghost } = o;
  const W = CORE_WIDTH, D = CORE_DEPTH;
  opaque.withMat(MAT.tile, () => opaque.box(x, y, z, W, 0.06, D, ghost ? scale(STONE, 0.8) : STONE, { skip: ['bottom'], shade: false, emissive: ghost ? 0.05 : 0.6 }));
  // The lift shaft: a cream block on the east side of the north half, full height with a cornice.
  const sx = x + 1.9, sw = W - 1.9, sd = 4.0;
  opaque.withMat(MAT.concrete, () => opaque.box(sx, y + 0.06, z, sw, WALL_HEIGHT - 0.06, sd, hex('#e9e4d6'), { emissive: 0.25, grad: 0.15 }));
  opaque.box(sx - 0.04, y + WALL_HEIGHT, z - 0.04, sw + 0.08, 0.1, sd + 0.08, hex('#45d0b5'), { emissive: 0.5, shade: false });
  // The stair well on the south half: a back wall, a flight of steps rising towards +z and a glass balustrade on the open side.
  opaque.withMat(MAT.concrete, () => opaque.box(sx, y + 0.06, z + sd + 0.6, sw, WALL_HEIGHT - 0.06, 0.12, hex('#e9e4d6'), { emissive: 0.25, grad: 0.15 }));
  if (ghost) return;
  for (const lx of [0, 1]) {
    const dx = sx - 0.01, dz = z + 0.55 + lx * 1.5;
    // Door frame, two steel leaves with a seam, a call panel and the position strip above.
    opaque.box(dx - 0.05, y + 0.06, dz - 0.06, 0.07, 2.2, 1.22, DARK);
    opaque.withMat(MAT.metal, () => {
      opaque.box(dx - 0.07, y + 0.06, dz, 0.04, 2.12, 0.55, STEEL, { grad: 0.1 });
      opaque.box(dx - 0.07, y + 0.06, dz + 0.59, 0.04, 2.12, 0.55, scale(STEEL, 0.94), { grad: 0.1 });
    });
    opaque.box(dx - 0.07, y + 0.06, dz + 0.553, 0.045, 2.12, 0.012, DARK);
    opaque.box(dx - 0.06, y + 1.0, dz + 1.23, 0.03, 0.2, 0.08, DARK);
    opaque.box(dx - 0.075, y + 1.1, dz + 1.255, 0.012, 0.04, 0.03, WARM, { emissive: 0.9, shade: false });
    // Position strip: a dark slot with a light that steps through the stops, then back (ambient scenery).
    opaque.box(dx - 0.06, y + 2.2, dz + 0.4, 0.03, 0.38, 0.32, DARK);
    amb.tagged(ambientTag('lift', 14 + lx * 3, 0.24, 'y', lx * 0.3), () => {
      amb.box(dx - 0.082, y + 2.24, dz + 0.5, 0.012, 0.07, 0.12, hex('#ffc65c'), { emissive: 1, shade: false });
    });
  }
  // Steps: 14 steps rising 0.19 each, 0.28 deep, from z + sd + 0.7 towards +z, ending in a landing.
  const stepX = x + 2.4, stepW = 1.9;
  const z0 = z + sd + 0.7;
  opaque.withMat(MAT.concrete, () => {
    for (let i = 0; i < 12; i++) opaque.box(stepX, y + 0.06, z0 + i * 0.28 - 0.0, stepW, (i + 1) * 0.19, 0.28, i % 2 ? hex('#dcd5c4') : hex('#d4ccb9'), { grad: 0.1 });
  });
  // Handrail and a glass balustrade on the open (west) side of the flight.
  glass.box(stepX - 0.04, y + 0.06 + 0.4, z0, 0.03, 1.0, 12 * 0.28, hex('#9fd4e6'), { shade: false });
  opaque.withMat(MAT.metal, () => {
    opaque.box(stepX - 0.06, y + 0.06 + 1.4, z0, 0.06, 0.05, 12 * 0.28, STEEL);
    for (let i = 0; i <= 3; i++) opaque.box(stepX - 0.06, y + 0.06 + 0.4 + i * 0.5, z0 + i * 1.12 - 0.0, 0.05, 1.0, 0.05, STEEL);
  });
  if (o.plate) {
    // The floor sign above the lift doors, on the west face of the shaft, facing the corridor (-x).
    const c = o.plate;
    const px = sx - 0.12, w = 1.4, h = 0.35, py = y + 2.62;
    opaque.box(px - 0.02, py - 0.03, z + 0.4, 0.14, h + 0.06, w + 0.08, hex('#0e1b26'));
    opaque.withMat(MAT.sign, () => {
      const f = (zz: number, yy: number): number[] => [px - 0.025, yy, zz];
      // Seen from -x the right-hand side is +z.
      opaque.quadUV(f(z + 0.44, py), f(z + 0.44 + w, py), f(z + 0.44 + w, py + h), f(z + 0.44, py + h), [c.u0, c.v0], [c.u1, c.v0], [c.u1, c.v1], [c.u0, c.v1], [1, 1, 1], 0.9);
    });
  }
}
