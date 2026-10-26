/**
 * Campus environment assets for the headquarters scene.
 *
 * Pure builders in the shared primitive format (`MeshBuilder` from `lib/hqGeometry.ts`): each one
 * draws into a builder you pass in and returns `{ triangles, glow, bounds }`. Seeds make every
 * output deterministic. No builder draws people or any text; nothing here encodes a name, count
 * or status.
 *
 * Conventions: x east, z south, y up; headings (`rot`) are radians from +z towards +x; paths run
 * clockwise on screen, so positive offsets point inward on a ring.
 */
export * from './common.ts';
export * from './paths.ts';
export * from './vehicles.ts';
export * from './roads.ts';
export * from './water.ts';
export * from './sports.ts';
export * from './planting.ts';
export * from './plaza.ts';
export * from './furniture.ts';
export * from './skyline.ts';
export * from './campusEnvironment.ts';
export * from './ambient.ts';
export * from './budgets.ts';
