/**
 * The three.js side of the agent walkers (docs/ANUM_MINE_HQ_AGENT_WALKERS.md). A lazy chunk: the campus fetches it only when a stored record has
 * produced a walker with a route. Each walker is one rig (the agent's own figure, cut into body, legs and arms) that follows the pure timeline in
 * `hqWalkers.ts` and is removed when that timeline ends, so nothing keeps moving once the event is over. Reduced motion draws the figure standing
 * at the destination door instead. The layer drives its own frames (about 15 a second) only while a walking rig exists.
 */
import {
  AdditiveBlending, BufferGeometry, CircleGeometry, Color, CylinderGeometry, DoubleSide, Group, Mesh, MeshBasicMaterial, MeshLambertMaterial, type Material, type Scene,
} from 'three';
import { geometryOf } from './hqLayerGeo.ts';
import { walkerParts } from '../hqAssets/interior/walkerFigure.ts';
import { WALKER_SCALE, STATIC_MS, newWalkPose, restPose, walkPoseAt, type WalkPose, type WalkRoute } from './hqWalkers.ts';
import type { FigureLook } from './hqAvatar.ts';

/** What the layer needs from the renderer (`HqRenderer.walkerHooks`). */
export type WalkerHooks = Readonly<{ scene: Scene; cheap: boolean; request: () => void; slow: () => boolean; report: (rigs: number) => void }>;

export type LayerWalker = Readonly<{ id: string; seed: string; look: FigureLook; startedMs: number; animate: boolean; route: WalkRoute }>;
export type WalkerLayerPlan = Readonly<{ walkers: readonly LayerWalker[] }>;

type Rig = { walker: LayerWalker; group: Group; frame: Group; legL: Group; legR: Group; armL: Group; armR: Group; geos: BufferGeometry[]; pose: WalkPose; seconds: number };

class Layer {
  private readonly rigs = new Map<string, Rig>();
  private timer: number | null = null;
  private readonly mats: Material[] = [];
  private readonly shared: BufferGeometry[] = [];
  private readonly body: MeshLambertMaterial;
  private readonly ring: MeshBasicMaterial;
  private readonly shadow: MeshBasicMaterial;
  private readonly disc: CircleGeometry;
  private readonly beam: CylinderGeometry;
  private readonly beamMat: MeshBasicMaterial;
  private disposed = false;

  constructor(private readonly hooks: WalkerHooks) {
    this.body = new MeshLambertMaterial({ vertexColors: true });
    this.ring = new MeshBasicMaterial({ color: new Color('#7fd6c2'), transparent: true, opacity: 0.26, blending: AdditiveBlending, depthWrite: false, toneMapped: false, side: DoubleSide, fog: false });
    this.shadow = new MeshBasicMaterial({ color: new Color('#000000'), transparent: true, opacity: 0.28, depthWrite: false, side: DoubleSide, fog: false });
    this.beamMat = new MeshBasicMaterial({ color: new Color('#7fd6c2'), transparent: true, opacity: 0.16, blending: AdditiveBlending, depthWrite: false, toneMapped: false, side: DoubleSide, fog: false });
    this.mats.push(this.body, this.ring, this.shadow, this.beamMat);
    this.beam = new CylinderGeometry(0.5, 0.5, 26, 10, 1, true);
    this.beam.translate(0, 13, 0);
    this.shared.push(this.beam);
    this.disc = new CircleGeometry(1, 20);
    this.disc.rotateX(-Math.PI / 2);
    this.shared.push(this.disc);
  }

  set(plan: WalkerLayerPlan): void {
    const wanted = new Set(plan.walkers.map((w) => w.id));
    for (const [id, rig] of this.rigs) if (!wanted.has(id)) this.drop(id, rig);
    for (const w of plan.walkers) if (!this.rigs.has(w.id)) this.build(w);
    this.tick();
  }

  private build(walker: LayerWalker): void {
    const parts = walkerParts(walker.seed, walker.look);
    const geos: BufferGeometry[] = [];
    const mesh = (data: Float32Array): Mesh => { const g = geometryOf(data); geos.push(g); const m = new Mesh(g, this.body); m.castShadow = !this.hooks.cheap; m.frustumCulled = false; return m; };
    const group = new Group(), frame = new Group();
    frame.scale.setScalar(WALKER_SCALE);
    frame.add(mesh(parts.body));
    const swing = (data: Float32Array, pivot: readonly [number, number, number]): Group => { const g = new Group(); g.position.set(pivot[0], pivot[1], pivot[2]); g.add(mesh(data)); frame.add(g); return g; };
    const legL = swing(parts.legL, parts.pivots.legL), legR = swing(parts.legR, parts.pivots.legR);
    const armL = swing(parts.armL, parts.pivots.armL), armR = swing(parts.armR, parts.pivots.armR);
    group.add(frame);
    // A soft ring of light and a small dark patch under the feet keep the figure findable and grounded.
    const ring = new Mesh(this.disc, this.ring); ring.scale.setScalar(5.5); ring.position.y = 0.05; ring.renderOrder = 2;
    const shadow = new Mesh(this.disc, this.shadow); shadow.scale.setScalar(1.8); shadow.position.y = 0.04;
    const beam = new Mesh(this.beam, this.beamMat); beam.renderOrder = 2;
    group.add(ring, shadow, beam);
    group.visible = false;
    this.hooks.scene.add(group);
    this.rigs.set(walker.id, { walker, group, frame, legL, legR, armL, armR, geos, pose: newWalkPose(), seconds: walker.animate ? walker.route.totalSec : STATIC_MS / 1000 });
  }

  private drop(id: string, rig: Rig): void {
    this.hooks.scene.remove(rig.group);
    for (const g of rig.geos) g.dispose();
    this.rigs.delete(id);
  }

  /** Puts every rig where its timeline says it is now; removes the ones whose time is over; asks for the next frame while a walking rig remains. */
  private tick = (): void => {
    this.timer = null;
    if (this.disposed) return;
    const now = performance.now();
    let walking = false;
    for (const [id, rig] of [...this.rigs]) {
      const t = (now - rig.walker.startedMs) / 1000;
      if (t >= rig.seconds) { this.drop(id, rig); continue; }
      const pose = rig.pose;
      if (rig.walker.animate) { walkPoseAt(rig.walker.route, t, pose); walking = true; } else restPose(rig.walker.route, pose);
      rig.group.visible = pose.visible;
      rig.group.position.set(pose.x, 0.03, pose.z);
      rig.group.rotation.y = pose.heading;
      const swing = pose.moving ? Math.sin(pose.phase) : 0;
      rig.legL.rotation.x = swing * 0.62; rig.legR.rotation.x = -swing * 0.62;
      rig.armL.rotation.x = -swing * 0.5; rig.armR.rotation.x = swing * 0.5;
      rig.frame.position.y = pose.moving ? Math.abs(Math.cos(pose.phase)) * 0.035 * WALKER_SCALE : 0;
      rig.frame.scale.setScalar(WALKER_SCALE * pose.grow);
    }
    this.hooks.report(this.rigs.size);
    this.hooks.request();
    if (walking && !this.disposed) this.timer = window.setTimeout(this.tick, this.hooks.slow() ? 125 : 66);
  };

  dispose(): void {
    this.disposed = true;
    if (this.timer !== null) window.clearTimeout(this.timer);
    for (const [id, rig] of [...this.rigs]) this.drop(id, rig);
    for (const g of this.shared) g.dispose();
    for (const m of this.mats) m.dispose();
    this.hooks.report(0);
    this.hooks.request();
  }
}

const layers = new WeakMap<object, Layer>();

/** Gives a renderer the current walkers. An empty plan removes everything. Safe to call on every change. */
export function setWalkers(owner: object, hooks: WalkerHooks, plan: WalkerLayerPlan): void {
  let layer = layers.get(owner);
  if (!layer) { layer = new Layer(hooks); layers.set(owner, layer); }
  layer.set(plan);
}

export function detachWalkers(owner: object): void {
  layers.get(owner)?.dispose();
  layers.delete(owner);
}
