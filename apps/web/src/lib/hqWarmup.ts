/**
 * Shader warm-up for the headquarters renderer (docs/ANUM_MINE_HQ_GPU_CHECK.md, "Hitches and warm-up"). Its own lazy chunk: the renderer
 * imports it after the scene has settled on a graphics card, and the Graphics check before a benchmark, so the renderer path every tier
 * downloads does not carry it.
 *
 * three.js compiles a program the first time a material is drawn with a new combination of settings. A tier switch (shadows on or off), a
 * governor step or the first glow frame of the evening therefore stalled the frame that needed it: half a second or more on an integrated
 * GPU in the first real report. Here every variant the reachable tiers use is compiled ahead with `renderer.compile`, which only starts the
 * work, and then waited for with `KHR_parallel_shader_compile` (the browser compiles on other threads; the page keeps drawing). Without that
 * extension, each new program is finished in its own animation frame, so the cost is spread out instead of landing on one frame.
 */
import { Group, HalfFloatType, Mesh, MeshBasicMaterial, WebGLRenderTarget, type Camera, type DirectionalLight, type Material, type Scene, type WebGLRenderer } from 'three';
import type { ProgramVariant } from './hqQualityTiers.ts';

/** The renderer's own fields the warm-up reads, reached through a cast (see the note in hqThreeRenderer.ts). */
export type WarmHost = {
  renderer: WebGLRenderer; scene: Scene; camera: Camera; sun: DirectionalLight; materials: Map<string, Material>; meshes: readonly Mesh[];
  disposed: boolean; lost: boolean; shadowDirty: boolean;
};

/** What a warm-up did: programs it created, how long it took, whether the browser compiled in parallel. */
export type WarmResult = Readonly<{ programs: number; ms: number; parallel: boolean }>;

type ProgramLike = { isReady(): boolean; getUniforms(): unknown };

const frame = (): Promise<void> => new Promise((resolve) => {
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => resolve()); else setTimeout(resolve, 16);
});

/**
 * Compile every variant in `variants` for every material in the scene, plus, when `boards` is true, the billboards' other material (the
 * lowest tier draws them with one flat material, the others with the lit kit ones). The renderer's real state is restored after each
 * synchronous compile call, so the frames drawn meanwhile are unchanged.
 */
export async function warmPrograms(h: WarmHost, variants: readonly ProgramVariant[], boards: boolean): Promise<WarmResult> {
  const r = h.renderer;
  const started = performance.now();
  const parallel = r.extensions.has('KHR_parallel_shader_compile');
  const list = r.info.programs as unknown as ProgramLike[] | null;
  const before = list?.length ?? 0;
  const extra = new Group();
  if (boards) {
    const flat = h.materials.get('boardFlat');
    for (const mesh of h.meshes) {
      const cls = mesh.userData.boardCls as string | undefined;
      const alt = cls ? (mesh.material === flat ? h.materials.get(cls) : flat) : undefined;
      if (!alt) continue;
      const twin = new Mesh(mesh.geometry, alt);
      twin.matrixAutoUpdate = false;
      extra.add(twin);
    }
  }
  let target: WebGLRenderTarget | null = null;
  performance.mark('hq:warm:start');
  try {
    for (const v of variants) {
      if (h.disposed || h.lost) break;
      const shadow = r.shadowMap.enabled, cast = h.sun.castShadow, keep = r.getRenderTarget();
      r.shadowMap.enabled = v.shadows;
      h.sun.castShadow = v.shadows;
      if (v.glow) { target ??= new WebGLRenderTarget(1, 1, { type: HalfFloatType }); r.setRenderTarget(target); }
      try {
        r.compile(h.scene, h.camera);
        if (extra.children.length) r.compile(extra, h.camera, h.scene);
        if (v.shadows && !shadow) {
          // The shadow pass has depth programs of its own, which `compile` does not make: when the tier in force has no shadows, draw one
          // shadow pass now, with the main pass reduced to one plain material into a 1 pixel target (nothing reaches the screen).
          const plain = new MeshBasicMaterial();
          const keepOverride = h.scene.overrideMaterial;
          target ??= new WebGLRenderTarget(1, 1, { type: HalfFloatType });
          h.scene.overrideMaterial = plain;
          r.setRenderTarget(target);
          r.shadowMap.needsUpdate = true;
          try { r.render(h.scene, h.camera); } finally { h.scene.overrideMaterial = keepOverride; plain.dispose(); h.shadowDirty = true; }
        }
      } finally {
        r.shadowMap.enabled = shadow; h.sun.castShadow = cast; r.setRenderTarget(keep);
      }
      await frame();
    }
    // Wait for the new programs: polled without blocking where the browser compiles in parallel; otherwise each one is finished in a frame of its own.
    const fresh = list ? list.slice(before) : [];
    for (let i = 0; i < 600 && parallel && fresh.some((p) => !p.isReady()); i++) await frame();
    for (const program of fresh) {
      if (h.disposed || h.lost) break;
      program.getUniforms();
      if (!parallel) await frame();
    }
  } finally {
    target?.dispose();
    performance.mark('hq:warm:end');
    try { performance.measure('hq:warm', 'hq:warm:start', 'hq:warm:end'); } catch { /* marks cleared */ }
  }
  return { programs: (list?.length ?? before) - before, ms: performance.now() - started, parallel };
}
