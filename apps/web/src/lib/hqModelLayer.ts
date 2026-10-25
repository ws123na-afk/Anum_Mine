/**
 * The three.js side of the loadable models: a glTF loader with a shared cache, baked static parts drawn
 * with instancing, soft contact shadows under every placed model, and the warm night halos.
 * Loaded only inside the renderer's dynamic import, so it never reaches another chunk or the unit runner.
 *
 * Models are loaded lazily (only the keys the current scene asks for), fetched once, baked to static
 * geometry (a seated person is the "sit" clip of the file, skinned once on the CPU, so there is no per frame
 * skinning cost) and drawn as one InstancedMesh per part. A failed or slow load leaves the procedural
 * stand-in in place (see `hqModelCache.ts`).
 */
import {
  AdditiveBlending, CanvasTexture, Color, DoubleSide, InstancedBufferAttribute, InstancedMesh, LinearFilter,
  Material, Matrix4, MeshBasicMaterial, MeshLambertMaterial, MeshStandardMaterial, Object3D, PlaneGeometry, Quaternion, ShaderMaterial, Texture,
  Vector3, type IUniform, type Scene,
} from 'three';
import { ModelCache, keysNeeded, resolvePlacements } from './hqModelCache.ts';
import { MODEL_SPECS, type ModelGroup, type ModelKey, type ModelPlacement } from './hqModels.ts';
import type { BakedModel } from './hqModelLoad.ts';

/** Fetch, parse and bake one model: a separate chunk, loaded on first use. */
const loadBaked = async (key: ModelKey) => (await import('./hqModelLoad.ts')).loadBaked(key);

/** Height of the seat of a person above the floor, in metres (the slot's y is the floor). */
const SEAT_HEIGHT = 0.46;

function radialTexture(): Texture {
  const c = document.createElement('canvas');
  c.width = c.height = 64;
  const g = c.getContext('2d')!;
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, 'rgba(0,0,0,0.55)');
  grad.addColorStop(0.55, 'rgba(0,0,0,0.22)');
  grad.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = grad; g.fillRect(0, 0, 64, 64);
  const t = new CanvasTexture(c);
  t.magFilter = LinearFilter;
  return t;
}

const HALO_VERT = `
  attribute vec4 iColor;
  varying vec4 vColor;
  varying vec2 vUv;
  void main() {
    vColor = iColor;
    vUv = uv;
    vec4 centre = modelViewMatrix * instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0);
    float radius = length(instanceMatrix[0].xyz);
    centre.xy += position.xy * radius * 2.0;
    gl_Position = projectionMatrix * centre;
  }`;
const HALO_FRAG = `
  uniform float uNight;
  varying vec4 vColor;
  varying vec2 vUv;
  void main() {
    float d = length(vUv - 0.5) * 2.0;
    float a = pow(clamp(1.0 - d, 0.0, 1.0), 2.4) * vColor.a * uNight;
    gl_FragColor = vec4(vColor.rgb * a * 0.9, a);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }`;

export type ModelStats = { placed: number; figures: number; loaded: number; asked: number; bytes: number; loadMs: number; fallbacks: string[] };

export class ModelLayer {
  readonly stats: ModelStats = { placed: 0, figures: 0, loaded: 0, asked: 0, bytes: 0, loadMs: 0, fallbacks: [] };
  private readonly cache = new ModelCache<BakedModel>(loadBaked);
  private readonly root = new Object3D();
  private readonly uNight: IUniform<number> = { value: 0 };
  private shadowTexture: Texture | null = null;
  private halos: InstancedMesh | null = null;
  private haloMaterial: ShaderMaterial | null = null;
  private generation = 0;
  private disposed = false;
  private readonly cheap: boolean;
  private readonly scene: Scene;
  private readonly onChange: (ready: ReadonlySet<ModelGroup>) => void;
  private readonly onFrameNeeded: () => void;
  private readonly swapped = new Map<Material, Material>();

  constructor(scene: Scene, cheap: boolean, onChange: (ready: ReadonlySet<ModelGroup>) => void, onFrameNeeded: () => void) {
    this.scene = scene; this.cheap = cheap; this.onChange = onChange; this.onFrameNeeded = onFrameNeeded;
    this.root.name = 'hq-models';
    scene.add(this.root);
  }

  /** Groups whose models are already loaded: the renderer hides their procedural stand-ins at once on a rebuild. */
  readyNow(placements: readonly ModelPlacement[]): ReadonlySet<ModelGroup> {
    const loaded = new Set(keysNeeded(placements).filter((k) => this.cache.peek(k)));
    return resolvePlacements(placements, loaded).readyGroups;
  }

  setNight(night: number): void { this.uNight.value = night; }

  /** Replace the placed models. Loads what is missing, then draws; a failure keeps the fallbacks and says so in `stats`. */
  async setPlacements(placements: readonly ModelPlacement[]): Promise<void> {
    const generation = ++this.generation;
    const keys = keysNeeded(placements);
    this.stats.asked = keys.length;
    const started = performance.now();
    if (keys.some((k) => !this.cache.peek(k))) await Promise.all(keys.map((k) => this.cache.get(k)));
    if (this.disposed || generation !== this.generation) return;
    const loaded = new Set(keys.filter((k) => this.cache.peek(k)));
    const resolved = resolvePlacements(placements, loaded);
    this.stats.loaded = loaded.size;
    this.stats.bytes = this.cache.bytes;
    this.stats.loadMs = Math.max(this.stats.loadMs, Math.round(performance.now() - started));
    this.stats.fallbacks = [...resolved.fallbackGroups];
    this.draw(resolved.placements);
    this.onChange(resolved.readyGroups);
    this.onFrameNeeded();
  }

  private clear(): void {
    for (const child of [...this.root.children]) {
      if (child === this.halos) continue;
      this.root.remove(child);
      if ((child as InstancedMesh).isInstancedMesh) (child as InstancedMesh).dispose();
    }
  }

  private draw(placements: readonly ModelPlacement[]): void {
    this.clear();
    this.stats.placed = placements.length;
    this.stats.figures = placements.filter((p) => p.group === 'figure').length;
    const byKey = new Map<ModelKey, ModelPlacement[]>();
    for (const p of placements) { const list = byKey.get(p.key) ?? []; list.push(p); byKey.set(p.key, list); }
    const m = new Matrix4(), q = new Quaternion(), pos = new Vector3(), s = new Vector3(), up = new Vector3(0, 1, 0);
    for (const [key, list] of byKey) {
      const baked = this.cache.peek(key)!.model;
      const spec = MODEL_SPECS[key];
      for (const part of baked.parts) {
        const mesh = new InstancedMesh(part.geometry, this.materialFor(part.material), list.length);
        list.forEach((p, i) => {
          q.setFromAxisAngle(up, p.yaw + spec.yawOffset);
          pos.set(p.x, p.y + (spec.group === 'figure' ? SEAT_HEIGHT : 0), p.z);
          s.setScalar(p.scale);
          m.compose(pos, q, s);
          mesh.setMatrixAt(i, m);
        });
        mesh.instanceMatrix.needsUpdate = true;
        mesh.castShadow = true; mesh.receiveShadow = true;
        mesh.frustumCulled = false;
        this.root.add(mesh);
      }
    }
    // Soft contact shadows: one instanced plane for every placed model.
    if (placements.length) {
      this.shadowTexture ??= radialTexture();
      const decal = new InstancedMesh(
        new PlaneGeometry(1, 1).rotateX(-Math.PI / 2),
        new MeshBasicMaterial({ map: this.shadowTexture, transparent: true, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2, toneMapped: false, fog: false }),
        placements.length,
      );
      placements.forEach((p, i) => {
        q.setFromAxisAngle(up, p.yaw);
        pos.set(p.x, p.y + 0.03, p.z);
        s.set(p.footprint[0] * 1.7, 1, p.footprint[1] * 1.7);
        m.compose(pos, q, s);
        decal.setMatrixAt(i, m);
      });
      decal.instanceMatrix.needsUpdate = true;
      decal.frustumCulled = false; decal.renderOrder = 1;
      this.root.add(decal);
    }
  }

  private materialFor(source: Material): Material {
    if (!this.cheap) return source;
    let m = this.swapped.get(source);
    if (!m) {
      const std = source as MeshStandardMaterial;
      m = new MeshLambertMaterial({ map: std.map ?? null, color: std.color ?? new Color(1, 1, 1), side: DoubleSide });
      this.swapped.set(source, m);
    }
    return m;
  }

  /** The night halos (x, y, z, radius, r, g, b, strength per halo). One instanced billboard each, additive. */
  setHalos(data: Float32Array | undefined): void {
    if (this.halos) { this.root.remove(this.halos); this.halos.geometry.dispose(); this.halos.dispose(); this.halos = null; }
    const n = data ? Math.floor(data.length / 8) : 0;
    if (!data || n === 0) return;
    const geometry = new PlaneGeometry(1, 1);
    const colors = new Float32Array(n * 4);
    this.haloMaterial ??= new ShaderMaterial({
      uniforms: { uNight: this.uNight }, vertexShader: HALO_VERT, fragmentShader: HALO_FRAG,
      transparent: true, depthWrite: false, depthTest: true, blending: AdditiveBlending, fog: false,
    });
    const mesh = new InstancedMesh(geometry, this.haloMaterial, n);
    const m = new Matrix4();
    for (let i = 0; i < n; i++) {
      const o = i * 8;
      m.makeScale(data[o + 3], data[o + 3], data[o + 3]).setPosition(data[o], data[o + 1], data[o + 2]);
      mesh.setMatrixAt(i, m);
      colors.set([data[o + 4], data[o + 5], data[o + 6], data[o + 7]], i * 4);
    }
    geometry.setAttribute('iColor', new InstancedBufferAttribute(colors, 4));
    mesh.frustumCulled = false; mesh.renderOrder = 5;
    this.halos = mesh;
    this.root.add(mesh);
  }

  get haloCount(): number { return this.halos?.count ?? 0; }

  dispose(): void {
    this.disposed = true;
    this.clear();
    this.setHalos(undefined);
    this.haloMaterial?.dispose();
    this.shadowTexture?.dispose();
    this.scene.remove(this.root);
    for (const m of this.swapped.values()) m.dispose();
  }
}
