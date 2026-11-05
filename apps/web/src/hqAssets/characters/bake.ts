/**
 * The three.js side of the character kit: builds the geometry of a shape code (`fig:...`) and hands it to the model
 * layer in the same form as a baked glTF person (one geometry with a role per vertex, tinted per instance). Its own lazy
 * chunk: `hqModelLayer.ts` imports it only when a scene has people, so the renderer chunk keeps its size budget.
 */
import { BufferAttribute, BufferGeometry, Color, SRGBColorSpace, Vector3 } from 'three';
import type { BakedModel } from '../../lib/hqModelLoad.ts';
import type { LoadOutcome } from '../../lib/hqModelCache.ts';
import { ROLE } from './mesh.ts';
import { buildFromCode } from './figure.ts';

export async function bakeCharacter(key: string): Promise<LoadOutcome<BakedModel>> {
  const data = buildFromCode(key);
  if (!data) throw new Error(`character: unknown shape ${key}`);
  const colors = new Float32Array(data.colors.length);
  const c = new Color();
  for (let v = 0; v < data.roles.length; v++) {
    if (data.roles[v] === ROLE.keep) {
      // A part that keeps its colour (eyes, shoes, a safety vest): sRGB in the kit, linear for the renderer.
      c.setRGB(data.colors[v * 3], data.colors[v * 3 + 1], data.colors[v * 3 + 2], SRGBColorSpace);
      colors[v * 3] = c.r; colors[v * 3 + 1] = c.g; colors[v * 3 + 2] = c.b;
    } else { colors[v * 3] = data.colors[v * 3]; colors[v * 3 + 1] = data.colors[v * 3 + 1]; colors[v * 3 + 2] = data.colors[v * 3 + 2]; }
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(data.positions, 3));
  geometry.setAttribute('normal', new BufferAttribute(data.normals, 3));
  geometry.setAttribute('color', new BufferAttribute(colors, 3));
  geometry.setAttribute('aRole', new BufferAttribute(data.roles, 1));
  geometry.computeBoundingBox();
  const size = geometry.boundingBox!.getSize(new Vector3());
  return { model: { parts: [{ geometry, material: null }], size }, bytes: 0 };
}
