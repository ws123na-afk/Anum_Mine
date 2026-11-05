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
  AdditiveBlending, BoxGeometry, BufferGeometry, CanvasTexture, Color, DoubleSide, Float32BufferAttribute, InstancedBufferAttribute, InstancedMesh, LinearFilter,
  Material, Matrix4, MeshBasicMaterial, MeshLambertMaterial, MeshStandardMaterial, Object3D, PlaneGeometry, Quaternion, ShaderMaterial, Texture,
  Vector3, type IUniform, type Scene,
} from 'three';
import { ModelCache, keysNeeded, resolvePlacements } from './hqModelCache.ts';
import { isFigureKey, specOf, type LegacyKey, type ModelGroup, type ModelKey, type ModelPlacement } from './hqModels.ts';
import type { BakedModel, FigureAnchors } from './hqModelLoad.ts';

/** Fetch, parse and bake one model, or build a person with the character kit: separate chunks, loaded on first use. */
const loadBaked = async (key: ModelKey) => (isFigureKey(key) ? (await import('../hqAssets/characters/bake.ts')).bakeCharacter(key) : (await import('./hqModelLoad.ts')).loadBaked(key as LegacyKey));

/** People are drawn from their shape code; a busy scene uses a lighter version of each figure (`:l`, then `:f`), so the triangles stay bounded. */
const shaped = (list: readonly ModelPlacement[], cheap: boolean): readonly ModelPlacement[] => {
  const n = list.length, lod = n > (cheap ? 60 : 120) ? ':f' : n > (cheap ? 18 : 40) ? ':l' : '';
  return list.map((p) => (p.shape ? { ...p, key: `${p.shape}${lod}` as ModelKey } : p));
};

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

/** The tinted parts of a person, as instanced vertex attributes read by the patched shader (see `patchRoles`). */
const ROLE_ATTRS = ['iSkin', 'iHair', 'iTop', 'iBottom', 'iTrim', 'iAccent'] as const;

/**
 * Patches a standard or Lambert material so each vertex picks its colour from a per instance attribute chosen by its
 * role (`aRole`): 0 skin, 1 hair, 2 top, 3 bottom, 4 trim, 5 accent, otherwise the vertex colour itself. The vertex colour
 * of a tinted part carries the painted shade. One draw call still serves every figure of one model.
 */
function patchRoles(material: Material): void {
  material.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>
        attribute float aRole;
        ${ROLE_ATTRS.map((a) => `attribute vec3 ${a};`).join('\n')}`)
      .replace('#include <color_vertex>', `
        vec3 roleColour = color.rgb;
        float roleShade = color.r;
        if (aRole < 0.5) roleColour = iSkin * roleShade;
        else if (aRole < 1.5) roleColour = iHair * roleShade;
        else if (aRole < 2.5) roleColour = iTop * roleShade;
        else if (aRole < 3.5) roleColour = iBottom * roleShade;
        else if (aRole < 4.5) roleColour = iTrim * roleShade;
        else if (aRole < 5.5) roleColour = iAccent * roleShade;
        vColor = vec4(roleColour, 1.0);`);
  };
  material.customProgramCacheKey = () => 'hq-figure-roles';
}

const hexLinear = (hex: string, out: Float32Array, at: number, c: Color): void => { c.set(hex); out[at] = c.r; out[at + 1] = c.g; out[at + 2] = c.b; };

/** Merge axis aligned boxes (centre x, y, z and size w, h, d) into one flat shaded geometry. */
function boxesGeometry(boxes: readonly (readonly [number, number, number, number, number, number])[]): BufferGeometry {
  const pos: number[] = [], nor: number[] = [];
  for (const [x, y, z, w, h, d] of boxes) {
    const g = new BoxGeometry(w, h, d).toNonIndexed().translate(x, y, z);
    pos.push(...g.getAttribute('position').array); nor.push(...g.getAttribute('normal').array);
    g.dispose();
  }
  const out = new BufferGeometry();
  out.setAttribute('position', new Float32BufferAttribute(pos, 3));
  out.setAttribute('normal', new Float32BufferAttribute(nor, 3));
  return out;
}

/** A call centre headset in head units (head width = 1): band, side posts, ear cups and a microphone boom. */
const headsetGeometry = (headHeight: number): BufferGeometry => boxesGeometry([
  [0, headHeight * 0.44, 0, 0.98, 0.08, 0.2],
  [-0.5, headHeight * 0.2, 0, 0.07, headHeight * 0.5, 0.14],
  [0.5, headHeight * 0.2, 0, 0.07, headHeight * 0.5, 0.14],
  [-0.54, -headHeight * 0.02, 0, 0.12, 0.3, 0.26],
  [0.54, -headHeight * 0.02, 0, 0.12, 0.3, 0.26],
  [-0.46, -headHeight * 0.24, 0.2, 0.05, 0.05, 0.36],
  [-0.3, -headHeight * 0.27, 0.38, 0.1, 0.07, 0.07],
]);

/** A lanyard with a badge, in metres at the model's normalised size. */
const badgeGeometry = (): BufferGeometry => boxesGeometry([
  [-0.03, 0.07, -0.004, 0.012, 0.15, 0.01],
  [0.03, 0.07, -0.004, 0.012, 0.15, 0.01],
  [0, -0.02, 0.004, 0.075, 0.095, 0.014],
]);

export type ModelStats = { placed: number; figures: number; kit: number; loaded: number; asked: number; bytes: number; loadMs: number; fallbacks: string[]; looks: string[] };

export class ModelLayer {
  readonly stats: ModelStats = { placed: 0, figures: 0, kit: 0, loaded: 0, asked: 0, bytes: 0, loadMs: 0, fallbacks: [], looks: [] };
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
  readyNow(asked: readonly ModelPlacement[]): ReadonlySet<ModelGroup> {
    const placements = shaped(asked, this.cheap);
    const loaded = new Set(keysNeeded(placements).filter((k) => this.cache.peek(k)));
    return resolvePlacements(placements, loaded).readyGroups;
  }

  setNight(night: number): void { this.uNight.value = night; }

  /** Replace the placed models. Loads what is missing, then draws; a failure keeps the fallbacks and says so in `stats`. */
  async setPlacements(asked: readonly ModelPlacement[]): Promise<void> {
    const placements = shaped(asked, this.cheap);
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
    for (const mat of this.accessoryMat) mat.dispose();
    this.accessoryMat.length = 0;
    for (const child of [...this.root.children]) {
      if (child === this.halos) continue;
      this.root.remove(child);
      if ((child as InstancedMesh).isInstancedMesh) {
        (child as InstancedMesh).dispose();
        if (child.userData.ownGeometry) (child as InstancedMesh).geometry.dispose();
      }
    }
  }

  private draw(placements: readonly ModelPlacement[]): void {
    this.clear();
    this.stats.placed = placements.length;
    this.stats.figures = placements.filter((p) => p.group === 'figure').length;
    this.stats.kit = placements.filter((p) => isFigureKey(p.key)).length;
    this.stats.looks = placements.filter((p) => p.group === 'figure' && p.look).map((p) => p.look!.code);
    const byKey = new Map<ModelKey, ModelPlacement[]>();
    for (const p of placements) { const list = byKey.get(p.key) ?? []; list.push(p); byKey.set(p.key, list); }
    const m = new Matrix4(), q = new Quaternion(), pos = new Vector3(), s = new Vector3(), up = new Vector3(0, 1, 0);
    for (const [key, list] of byKey) {
      const baked = this.cache.peek(key)!.model;
      const spec = specOf(key);
      for (const part of baked.parts) {
        const figure = part.material === null;
        const geometry = figure ? part.geometry.clone() : part.geometry;
        const mesh = new InstancedMesh(geometry, figure ? this.roleMaterial() : this.materialFor(part.material!), list.length);
        if (figure) mesh.userData.ownGeometry = true;
        list.forEach((p, i) => {
          q.setFromAxisAngle(up, p.yaw + spec.yawOffset);
          pos.set(p.x, p.y + (spec.group === 'figure' && !spec.kit ? SEAT_HEIGHT : 0), p.z);
          const k = p.look?.scale ?? [1, 1, 1];
          s.set(p.scale * k[0], p.scale * k[1], p.scale * k[2]);
          m.compose(pos, q, s);
          mesh.setMatrixAt(i, m);
        });
        if (figure) this.tintInstances(geometry, list);
        mesh.instanceMatrix.needsUpdate = true;
        mesh.castShadow = true; mesh.receiveShadow = true;
        mesh.frustumCulled = false;
        this.root.add(mesh);
      }
      if (baked.anchors) this.accessories(key, list, baked.anchors, SEAT_HEIGHT);
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

  private roleMat: Material | null = null;
  private readonly accessoryMat: Material[] = [];

  /** The one shared material of every figure: vertex colours chosen by role, lit like the rest of the scene. */
  private roleMaterial(): Material {
    if (!this.roleMat) {
      this.roleMat = this.cheap
        ? new MeshLambertMaterial({ vertexColors: true, side: DoubleSide })
        : new MeshStandardMaterial({ vertexColors: true, roughness: 0.82, metalness: 0, side: DoubleSide });
      patchRoles(this.roleMat);
    }
    return this.roleMat;
  }

  /** One colour per role per instance, from the look of each placed figure. */
  private tintInstances(geometry: BufferGeometry, list: readonly ModelPlacement[]): void {
    const c = new Color();
    const arrays = ROLE_ATTRS.map(() => new Float32Array(list.length * 3));
    list.forEach((p, i) => {
      const look = p.look;
      const hexes = look ? [look.skin, look.hair, look.top, look.bottom, look.trim, look.accent] : ['#d29b6c', '#3b2a1f', '#2b3340', '#2b3340', '#f1f2f6', '#223f78'];
      hexes.forEach((hex, r) => hexLinear(hex, arrays[r], i * 3, c));
    });
    ROLE_ATTRS.forEach((name, r) => geometry.setAttribute(name, new InstancedBufferAttribute(arrays[r], 3)));
  }

  /** Role cues that are objects: a headset (reception, operations, care) and a lanyard badge (security, finance). One instanced mesh each. */
  private accessories(key: ModelKey, list: readonly ModelPlacement[], a: FigureAnchors, seat: number): void {
    const m = new Matrix4(), place = new Matrix4(), local = new Matrix4(), q = new Quaternion(), pos = new Vector3(), s = new Vector3(), up = new Vector3(0, 1, 0);
    const c = new Color();
    const build = (pick: (p: ModelPlacement) => boolean, geometry: BufferGeometry, anchor: (mm: Matrix4) => void, colour: (p: ModelPlacement) => string): void => {
      const chosen = list.filter(pick);
      if (!chosen.length) { geometry.dispose(); return; }
      const mat = this.cheap ? new MeshLambertMaterial({ side: DoubleSide }) : new MeshStandardMaterial({ roughness: 0.6, metalness: 0.1, side: DoubleSide });
      this.accessoryMat.push(mat);
      const mesh = new InstancedMesh(geometry, mat, chosen.length);
      mesh.userData.ownGeometry = true;
      chosen.forEach((p, i) => {
        q.setFromAxisAngle(up, p.yaw);
        pos.set(p.x, p.y + seat, p.z);
        const k = p.look?.scale ?? [1, 1, 1];
        s.set(p.scale * k[0], p.scale * k[1], p.scale * k[2]);
        place.compose(pos, q, s);
        anchor(local);
        m.multiplyMatrices(place, local);
        mesh.setMatrixAt(i, m);
        mesh.setColorAt(i, c.set(colour(p)));
      });
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
      mesh.castShadow = true; mesh.frustumCulled = false;
      this.root.add(mesh);
    };
    void key;
    build((p) => p.look?.headset === true, headsetGeometry(a.head.h / a.head.w), (mm) => mm.compose(new Vector3(a.head.x, a.head.y, a.head.z), new Quaternion(), new Vector3(a.head.w, a.head.w, a.head.w)), () => '#20262d');
    build((p) => p.look?.badge === true, badgeGeometry(), (mm) => mm.compose(new Vector3(a.chest.x, a.chest.y, a.chest.z + 0.004), new Quaternion(), new Vector3(1, 1, 1)), (p) => p.look?.accent ?? '#e3b341');
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
    this.roleMat?.dispose();
    for (const mat of this.accessoryMat) mat.dispose();
    this.scene.remove(this.root);
    for (const m of this.swapped.values()) m.dispose();
  }
}
