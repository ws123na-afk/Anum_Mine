/**
 * Which part of a person a vertex of the CC0 character models belongs to (skin, hair, top, bottom, shirt trim,
 * accent, or "keep the original colour"), so the renderer can tint every figure with its own colours.
 * Pure: the loader (`hqModelLoad.ts`) feeds it the swatch cell of each vertex and its height in the rest pose.
 *
 * All five Kenney characters share one 256 px colour map: a 16 x 16 grid of 16 px cells, each swatch two cells wide
 * (the odd column is used), with four shades per swatch from light to dark. A vertex's UV points at one cell.
 */
export const ROLE = { skin: 0, hair: 1, top: 2, bottom: 3, trim: 4, accent: 5, keep: 6 } as const;
export type Role = (typeof ROLE)[keyof typeof ROLE];

/** The colour map as sampled from the shipped files: `SWATCH[row][column]`, rows 0..3 the bright block, 4..7 the neutral block. */
export const SWATCH: readonly (readonly string[])[] = [
  ['#fce1c1', '#57c186', '#ffcc5e', '#ff9744', '#f96a42', '#658bd6', '#c2ddfa', '#9e6ee1'],
  ['#f9d9b4', '#45af7e', '#ffbc4f', '#ff8d44', '#ec6246', '#607bce', '#a8c9f1', '#8a5fd5'],
  ['#f7d3a8', '#339d75', '#ffab42', '#ff8144', '#e15c49', '#5d6cc7', '#8cb2e7', '#764fc9'],
  ['#f4cc9c', '#218a6c', '#ff9a33', '#ff7544', '#d5564e', '#585bbf', '#739ede', '#633fbc'],
  ['#45454f', '#7f849c', '#505463', '#bbc3ea', '#f5f5f9', '#e89066', '#a95e41', '#ecb690'],
  ['#404048', '#73788f', '#4a4d5b', '#a7aed1', '#e4e4ee', '#d7815b', '#9e5b41', '#e2a781'],
  ['#3b3b41', '#676c84', '#444754', '#9299b7', '#d4d4e3', '#c87450', '#935841', '#d79770'],
  ['#36363a', '#5b6079', '#3e414d', '#80859d', '#c2c2d8', '#b76646', '#875541', '#cd8860'],
];

/** The swatch column and row (0..7) of a UV coordinate. */
export function cellOf(u: number, v: number): { k: number; r: number } {
  const cu = Math.max(0, Math.min(15, Math.floor(u * 16)));
  const cv = Math.max(0, Math.min(15, Math.floor(v * 16)));
  return { k: cu >> 1, r: Math.max(0, Math.min(7, cv - 8)) };
}

/** The original colour of a cell (used for the parts that keep it: shoes, eyes, glasses). */
export const swatchColour = (k: number, r: number): string => SWATCH[r][k];

/** Shade of a row inside its block, light to dark: keeps the hand painted shading when a part is tinted. */
export const rowShade = (r: number): number => [1.06, 1.0, 0.94, 0.88][r % 4];

export type RoleInput = Readonly<{
  /** `head` for the head mesh, `body` for the rest. */
  mesh: 'head' | 'body';
  k: number; r: number;
  /** Height in the rest pose: 0 at the feet, 1 at the top of the head. */
  restY: number;
  /** A tiny front facing dark triangle on the face plane (eyes, mouth). */
  face: boolean;
  model: string;
}>;

const SHOE_TOP = 0.055;
const HIP = 0.285;
const LEG_TOP = 0.25;

export function roleOf(i: RoleInput): Role {
  const neutral = i.r >= 4;
  if (neutral && i.k === 7) return ROLE.skin;
  // The blue "gloves" of one model are hands.
  if (i.model === 'person_3' && !neutral && i.k === 5 && i.mesh === 'body') return ROLE.skin;
  if (i.mesh === 'head') {
    if (i.face) return ROLE.keep;
    if (neutral && (i.k === 3 || i.k === 4)) return ROLE.keep; // glasses and the small white details
    return ROLE.hair;
  }
  if (i.restY < SHOE_TOP) return ROLE.keep;
  if (neutral && (i.k === 4 || i.k === 3)) return ROLE.trim;
  if (!neutral) return i.restY < LEG_TOP ? ROLE.bottom : ROLE.accent;
  return i.restY >= HIP ? ROLE.top : ROLE.bottom;
}
