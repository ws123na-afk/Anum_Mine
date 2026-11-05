/**
 * Fetches and bakes one glTF model. Its own chunk (with GLTFLoader and the meshopt decoder), imported by
 * `hqModelLayer.ts` only when a scene asks for a model, so the renderer chunk keeps its size budget.
 */
import { AnimationMixer, Box3, BufferAttribute, BufferGeometry, Color, Mesh, Object3D, SkinnedMesh, Vector3, type AnimationClip, type Material } from 'three';
import { ROLE, cellOf, roleOf, rowShade, swatchColour, type Role } from './hqFigureRoles.ts';
import type { LoadOutcome } from './hqModelCache.ts';
import { MODEL_SPECS, type LegacyKey, type ModelGroup } from './hqModels.ts';

/** `material` is null for a figure: the layer draws it with the role tint material (per instance colours). */
export type Part = Readonly<{ geometry: BufferGeometry; material: Material | null }>;
/** Where accessories sit on a baked figure (same units and origin as the geometry): the head box and the chest front. */
export type FigureAnchors = Readonly<{
  head: Readonly<{ x: number; y: number; z: number; w: number; h: number; d: number }>;
  chest: Readonly<{ x: number; y: number; z: number }>;
}>;
export type BakedModel = Readonly<{ parts: readonly Part[]; size: Vector3; anchors?: FigureAnchors }>;

const baseUrl = (): string => {
  const env = (import.meta as unknown as { env?: { BASE_URL?: string } }).env;
  return env?.BASE_URL ?? '/';
};

/** Turn a loaded glTF scene into static parts with the origin at the bottom middle (the seat or the feet on y = 0). */
function bake(root: Object3D, group: ModelGroup, clip: AnimationClip | undefined): BakedModel {
  // Lights that ship inside sample files are never wanted.
  const lights: Object3D[] = [];
  root.traverse((o) => { if ((o as unknown as { isLight?: boolean }).isLight) lights.push(o); });
  for (const l of lights) l.removeFromParent();
  if (clip) {
    const mixer = new AnimationMixer(root);
    mixer.clipAction(clip).play();
    mixer.setTime(clip.duration * 0.5);
  }
  root.updateMatrixWorld(true);
  const raw: Array<{ geometry: BufferGeometry; material: Material }> = [];
  const v = new Vector3();
  root.traverse((o) => {
    const mesh = o as Mesh;
    if (!mesh.isMesh) return;
    const source = mesh.geometry;
    const count = source.getAttribute('position').count;
    const position = new Float32Array(count * 3);
    const skinned = o as SkinnedMesh;
    if (skinned.isSkinnedMesh) {
      skinned.skeleton.update();
      for (let i = 0; i < count; i++) { skinned.getVertexPosition(i, v); v.applyMatrix4(skinned.matrixWorld); position.set([v.x, v.y, v.z], i * 3); }
    } else {
      for (let i = 0; i < count; i++) { v.fromBufferAttribute(source.getAttribute('position'), i).applyMatrix4(mesh.matrixWorld); position.set([v.x, v.y, v.z], i * 3); }
    }
    const geometry = new BufferGeometry();
    geometry.setAttribute('position', new BufferAttribute(position, 3));
    const uv = source.getAttribute('uv');
    if (uv) {
      const out = new Float32Array(count * 2);
      for (let i = 0; i < count; i++) out.set([uv.getX(i), uv.getY(i)], i * 2);
      geometry.setAttribute('uv', new BufferAttribute(out, 2));
    }
    if (source.index) geometry.setIndex(new BufferAttribute(Uint32Array.from(source.index.array as ArrayLike<number>), 1));
    geometry.computeVertexNormals();
    const material = Array.isArray(mesh.material) ? mesh.material[0] : mesh.material;
    raw.push({ geometry, material });
  });
  const box = new Box3();
  for (const p of raw) { p.geometry.computeBoundingBox(); box.union(p.geometry.boundingBox!); }
  // Normalise the size: a person to a seated height of 0.95 m, furniture to a width of 1 (the placement scale is then metres).
  const first = box.getSize(new Vector3());
  const k = group === 'figure' ? 0.95 / first.y : 1 / first.x;
  for (const p of raw) p.geometry.scale(k, k, k);
  box.makeEmpty();
  for (const p of raw) { p.geometry.computeBoundingBox(); box.union(p.geometry.boundingBox!); }
  const size = box.getSize(new Vector3());
  // A person is centred on the hips (the slot is the hip centre); furniture on its middle.
  const cx = (box.min.x + box.max.x) / 2, cz = group === 'figure' ? box.min.z + size.z * 0.3 : (box.min.z + box.max.z) / 2;
  for (const p of raw) p.geometry.translate(-cx, -box.min.y, -cz);
  return { parts: raw, size };
}

const worldOf = (mesh: Mesh, i: number, v: Vector3): Vector3 => {
  const skinned = mesh as SkinnedMesh;
  if (skinned.isSkinnedMesh) { skinned.skeleton.update(); skinned.getVertexPosition(i, v); } else v.fromBufferAttribute(mesh.geometry.getAttribute('position'), i);
  return v.applyMatrix4(mesh.matrixWorld);
};

/**
 * A seated person, baked into ONE geometry with two extra vertex attributes: `aRole` (which part of the person the
 * vertex is, see `hqFigureRoles.ts`) and `color` (the painted shade of a part that is tinted, or the original
 * colour of a part that keeps it). The renderer tints the roles per instance.
 */
function bakeFigure(root: Object3D, clip: AnimationClip | undefined, key: string): BakedModel {
  const lights: Object3D[] = [];
  root.traverse((o) => { if ((o as unknown as { isLight?: boolean }).isLight) lights.push(o); });
  for (const l of lights) l.removeFromParent();
  root.updateMatrixWorld(true);
  const meshes: Mesh[] = [];
  root.traverse((o) => { if ((o as Mesh).isMesh) meshes.push(o as Mesh); });
  const v = new Vector3();
  // Rest pose first: heights and the face triangles are judged there, then the seated pose is baked.
  const rest = meshes.map((mesh) => {
    const count = mesh.geometry.getAttribute('position').count;
    const out = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) { worldOf(mesh, i, v); out.set([v.x, v.y, v.z], i * 3); }
    return out;
  });
  let lo = Infinity, hi = -Infinity;
  for (const r of rest) for (let i = 1; i < r.length; i += 3) { lo = Math.min(lo, r[i]); hi = Math.max(hi, r[i]); }
  const H = hi - lo || 1;
  if (clip) { const mixer = new AnimationMixer(root); mixer.clipAction(clip).play(); mixer.setTime(clip.duration * 0.5); }
  root.updateMatrixWorld(true);
  const positions: number[] = [], colours: number[] = [], roles: number[] = [], indices: number[] = [], headIdx: number[] = [], torsoIdx: number[] = [];
  const tint = new Color();
  meshes.forEach((mesh, mi) => {
    const kind: 'head' | 'body' = /head/i.test(mesh.name) ? 'head' : 'body';
    const geo = mesh.geometry, uv = geo.getAttribute('uv'), idx = geo.index!;
    const count = uv.count, base = positions.length / 3, r0 = rest[mi];
    // Face features: tiny, front facing, dark triangles of the head, in the middle of the face.
    const face = new Uint8Array(count);
    if (kind === 'head') {
      const a = new Vector3(), b = new Vector3(), c = new Vector3(), n = new Vector3();
      for (let t = 0; t < idx.count; t += 3) {
        const ids = [idx.getX(t), idx.getX(t + 1), idx.getX(t + 2)];
        a.fromArray(r0, ids[0] * 3); b.fromArray(r0, ids[1] * 3); c.fromArray(r0, ids[2] * 3);
        n.subVectors(b, a).cross(c.clone().sub(a));
        const area = n.length() / 2 / (H * H);
        n.normalize();
        const cell = cellOf(uv.getX(ids[0]), uv.getY(ids[0]));
        const yFrac = ((a.y + b.y + c.y) / 3 - lo) / H;
        if (cell.r >= 4 && cell.k === 0 && n.z > 0.97 && area < 0.0025 && yFrac > 0.5 && yFrac < 0.92) for (const i of ids) face[i] = 1;
      }
    }
    for (let i = 0; i < count; i++) {
      worldOf(mesh, i, v);
      positions.push(v.x, v.y, v.z);
      const { k, r } = cellOf(uv.getX(i), uv.getY(i));
      const restY = (r0[i * 3 + 1] - lo) / H;
      const role: Role = roleOf({ mesh: kind, k, r, restY, face: face[i] === 1, model: key });
      if (role === ROLE.keep) { tint.set(swatchColour(k, r)); colours.push(tint.r, tint.g, tint.b); } else { const sh = rowShade(r); colours.push(sh, sh, sh); }
      roles.push(role);
      if (kind === 'head' && role === ROLE.skin) headIdx.push(base + i);
      else if (restY > 0.3 && restY < 0.5 && (role === ROLE.top || role === ROLE.trim || role === ROLE.accent)) torsoIdx.push(base + i);
    }
    for (let t = 0; t < idx.count; t++) indices.push(base + idx.getX(t));
  });
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(new Float32Array(positions), 3));
  geometry.setAttribute('color', new BufferAttribute(new Float32Array(colours), 3));
  geometry.setAttribute('aRole', new BufferAttribute(new Float32Array(roles), 1));
  geometry.setIndex(new BufferAttribute(Uint32Array.from(indices), 1));
  geometry.computeBoundingBox();
  const box = geometry.boundingBox!;
  const first = box.getSize(new Vector3());
  const k = 0.95 / first.y;
  geometry.scale(k, k, k);
  geometry.computeBoundingBox();
  const b2 = geometry.boundingBox!, size = b2.getSize(new Vector3());
  const cx = (b2.min.x + b2.max.x) / 2, cz = b2.min.z + size.z * 0.3;
  geometry.translate(-cx, -b2.min.y, -cz);
  geometry.computeVertexNormals();
  const pos = geometry.getAttribute('position');
  const extent = (ids: number[]) => {
    const e = new Box3();
    for (const i of ids) e.expandByPoint(v.fromBufferAttribute(pos, i));
    return e;
  };
  const hb = extent(headIdx), tb = extent(torsoIdx.length ? torsoIdx : headIdx);
  const hs = hb.getSize(new Vector3());
  const anchors: FigureAnchors = {
    head: { x: (hb.min.x + hb.max.x) / 2, y: (hb.min.y + hb.max.y) / 2, z: (hb.min.z + hb.max.z) / 2, w: hs.x, h: hs.y, d: hs.z },
    chest: { x: 0, y: (tb.min.y + tb.max.y) / 2, z: tb.max.z },
  };
  return { parts: [{ geometry, material: null }], size, anchors };
}

export async function loadBaked(key: LegacyKey): Promise<LoadOutcome<BakedModel>> {
  const spec = MODEL_SPECS[key];
  const response = await fetch(`${baseUrl()}hq/models/${spec.file}`);
  if (!response.ok) throw new Error(`model ${key}: HTTP ${response.status}`);
  const buffer = await response.arrayBuffer();
  // The loader and the meshopt decoder are their own chunk, fetched only when a scene asks for a model (the renderer chunk has a size budget).
  const [{ GLTFLoader }, { MeshoptDecoder }] = await Promise.all([
    import('three/addons/loaders/GLTFLoader.js'),
    import('three/addons/libs/meshopt_decoder.module.js'),
  ]);
  const loader = new GLTFLoader();
  loader.setMeshoptDecoder(MeshoptDecoder);
  const gltf = await loader.parseAsync(buffer, `${baseUrl()}hq/models/`);
  const clip = spec.seatedClip ? gltf.animations.find((a) => a.name === spec.seatedClip) : undefined;
  if (spec.seatedClip && !clip) throw new Error(`model ${key}: clip ${spec.seatedClip} missing`);
  const model = spec.group === 'figure' ? bakeFigure(gltf.scene, clip, key) : bake(gltf.scene, spec.group, clip);
  return { model, bytes: buffer.byteLength };
}

