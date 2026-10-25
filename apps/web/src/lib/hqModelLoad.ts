/**
 * Fetches and bakes one glTF model. Its own chunk (with GLTFLoader and the meshopt decoder), imported by
 * `hqModelLayer.ts` only when a scene asks for a model, so the renderer chunk keeps its size budget.
 */
import { AnimationMixer, Box3, BufferAttribute, BufferGeometry, Mesh, Object3D, SkinnedMesh, Vector3, type AnimationClip, type Material } from 'three';
import type { LoadOutcome } from './hqModelCache.ts';
import { MODEL_SPECS, type ModelGroup, type ModelKey } from './hqModels.ts';

export type Part = Readonly<{ geometry: BufferGeometry; material: Material }>;
export type BakedModel = Readonly<{ parts: readonly Part[]; size: Vector3 }>;

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

export async function loadBaked(key: ModelKey): Promise<LoadOutcome<BakedModel>> {
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
  return { model: bake(gltf.scene, spec.group, clip), bytes: buffer.byteLength };
}

