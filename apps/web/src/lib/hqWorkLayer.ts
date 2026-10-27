/**
 * The three.js side of record-backed work motion (docs/ANUM_MINE_HQ_CAMPUS_MOTION.md). A lazy chunk: the campus fetches it only when a record
 * has produced a motion with a route, or a tyre rack is to stand at a building. Each motion is one rig (a van, a box truck, a tyre hauler or a
 * single tyre) that follows the pure timeline in `hqWorkMotion.ts` and is removed when that timeline ends, so nothing keeps moving once the
 * event is over. The layer drives its own frames (about 15 a second) only while a rig exists.
 */
import {
  AdditiveBlending, BufferGeometry, Color, DoubleSide, Group, Mesh, MeshBasicMaterial, MeshLambertMaterial, type Material, type Object3D, type Scene,
} from 'three';
import type { VehicleKit } from './hqSceneryLayer.ts';
import { BOX_TRUCK_WHEELS, BOX_TRUCK_WHEEL_RADIUS, beaconBlock, boxTruckParts, lightPool, tyreRack } from '../hqAssets/environment/workVehicle.ts';
import {
  KIND_BEACON, KIND_VEHICLE, TYRE_RADIUS, extraHoldOf, newPose, tyrePoseAt, vehiclePoseAt, vehicleSeconds, TYRE_TIMELINE,
  type WorkKind, type WorkPose, type WorkRoute, type WorkSite,
} from './hqWorkMotion.ts';

/** What the layer needs from the renderer (`HqRenderer.workHooks`). */
export type WorkHooks = Readonly<{ kit: VehicleKit; scene: Scene; cheap: boolean; request: () => void; lit: () => number; slow: () => boolean; report: (rigs: number) => void }>;

export type LayerMotion = Readonly<{ id: string; kind: WorkKind; startedMs: number; site: WorkSite; route: WorkRoute | null }>;
export type WorkLayerPlan = Readonly<{ motions: readonly LayerMotion[]; racks: readonly WorkSite[] }>;

const BEACON_HEX: Record<'amber' | 'green' | 'red', string> = { amber: '#ffb020', green: '#35d07f', red: '#ff3b30' };

type Rig = {
  id: string; motion: LayerMotion; group: Group; spinners: Object3D[]; beacon: Mesh | null; pool: Mesh | null; lightMats: MeshBasicMaterial[];
  seconds: number; wheelRadius: number; pose: WorkPose;
};

class Layer {
  private readonly rigs = new Map<string, Rig>();
  private racks: Group | null = null;
  private plan: WorkLayerPlan = { motions: [], racks: [] };
  private timer: number | null = null;
  private readonly geos = new Map<string, BufferGeometry>();
  private readonly mats: Material[] = [];
  private readonly sink: Array<() => void> = [];
  private disposed = false;

  constructor(private readonly hooks: WorkHooks) {}

  private geo(key: string, data: () => Float32Array): BufferGeometry {
    let g = this.geos.get(key);
    if (!g) { g = this.hooks.kit.geometryOf(data()); this.geos.set(key, g); }
    return g;
  }
  private lambert(color?: string): MeshLambertMaterial {
    const m = new MeshLambertMaterial({ vertexColors: true, ...(color ? { color: new Color(color) } : {}) });
    this.mats.push(m);
    return m;
  }

  set(plan: WorkLayerPlan): void {
    this.plan = plan;
    const now = performance.now();
    const wanted = new Set<string>();
    for (const m of plan.motions) {
      if (!m.route && KIND_VEHICLE[m.kind] !== 'tyre') continue;
      if ((now - m.startedMs) / 1000 > this.secondsOf(m.kind)) continue;
      wanted.add(m.id);
      if (!this.rigs.has(m.id)) this.build(m);
    }
    for (const [id, rig] of this.rigs) if (!wanted.has(id)) this.drop(id, rig);
    this.setRacks(plan.racks);
    this.tick();
  }

  private secondsOf(kind: WorkKind): number { return KIND_VEHICLE[kind] === 'tyre' ? TYRE_TIMELINE.out + TYRE_TIMELINE.rest + TYRE_TIMELINE.back : vehicleSeconds(kind); }

  private setRacks(sites: readonly WorkSite[]): void {
    if (this.racks) { this.hooks.scene.remove(this.racks); this.racks = null; }
    if (!sites.length) return;
    const g = new Group();
    const geometry = this.geo('rack', tyreRack);
    const material = this.lambert();
    for (const site of sites) {
      const m = new Mesh(geometry, material);
      m.position.set(site.rack.x, 0.02, site.rack.z);
      // The rack's long side runs along the building's front (the sideways axis).
      m.rotation.y = Math.atan2(site.tx, site.tz) + Math.PI / 2;
      g.add(m);
    }
    this.hooks.scene.add(g);
    this.racks = g;
  }

  private build(motion: LayerMotion): void {
    const type = KIND_VEHICLE[motion.kind];
    const group = new Group();
    const rig: Rig = { id: motion.id, motion, group, spinners: [], beacon: null, pool: null, lightMats: [], seconds: this.secondsOf(motion.kind), wheelRadius: 0.4, pose: newPose() };
    if (type === 'tyre') {
      const inner = new Group();
      const wheel = new Mesh(this.geo('wheel', this.hooks.kit.sceneryWheel), this.lambert());
      wheel.scale.setScalar(TYRE_RADIUS);
      wheel.position.y = TYRE_RADIUS;
      inner.add(wheel);
      rig.spinners.push(wheel);
      group.add(inner);
      rig.wheelRadius = TYRE_RADIUS;
      // A tyre is small from the campus camera: a soft ring of light under it keeps the event findable.
      const ring = new MeshBasicMaterial({ vertexColors: true, color: new Color('#7fd6c2'), transparent: true, opacity: 0.5, blending: AdditiveBlending, depthWrite: false, toneMapped: false, side: DoubleSide, fog: false });
      this.mats.push(ring);
      const pool = new Mesh(this.geo('pool', lightPool), ring);
      pool.scale.set(2.6, 1, 2.6);
      pool.renderOrder = 2;
      group.add(pool);
      rig.pool = pool;
    } else {
      const { kit } = this.hooks;
      const parts = type === 'van' ? kit.sceneryVehicleParts() : type === 'hauler' ? kit.haulerParts() : boxTruckParts();
      const k = type === 'van' ? kit.VEHICLE_SCALE[2] : ([1, 1, 1] as const);
      const tint = type === 'van' ? '#e8ebee' : type === 'hauler' ? '#2f4b66' : '#2f4b66';
      const body = new Mesh(this.geo(`${type}:body`, () => parts.body), this.lambert(tint));
      const trim = new Mesh(this.geo(`${type}:trim`, () => parts.trim), this.lambert());
      const lm = new MeshBasicMaterial({ vertexColors: true, toneMapped: true });
      this.mats.push(lm);
      rig.lightMats.push(lm);
      const lights = new Mesh(this.geo(`${type}:lights`, () => parts.lights), lm);
      for (const m of [body, trim, lights]) { m.scale.set(k[0], k[1], k[2]); group.add(m); }
      const spots = type === 'van' ? kit.WHEEL_SPOTS[2] : type === 'hauler' ? kit.WHEEL_SPOTS[4] : BOX_TRUCK_WHEELS;
      const radius = type === 'van' ? kit.WHEEL_RADIUS[2] : type === 'hauler' ? kit.WHEEL_RADIUS[4] : BOX_TRUCK_WHEEL_RADIUS;
      rig.wheelRadius = radius;
      const tyreMat = this.lambert();
      for (const spot of spots) {
        const holder = new Group();
        holder.position.set(spot[0], radius, spot[1]);
        const wheel = new Mesh(this.geo('wheel', this.hooks.kit.sceneryWheel), tyreMat);
        wheel.scale.setScalar(radius);
        holder.add(wheel);
        group.add(holder);
        rig.spinners.push(wheel);
      }
      const beaconColour = KIND_BEACON[motion.kind];
      if (beaconColour !== 'none') {
        const beacon = new Mesh(this.geo('beacon', beaconBlock), new MeshBasicMaterial({ color: new Color(BEACON_HEX[beaconColour]), toneMapped: false, vertexColors: false }));
        this.mats.push(beacon.material as Material);
        beacon.position.set(0, type === 'van' ? 2.2 : type === 'hauler' ? 2.65 : 2.5, type === 'van' ? 0.4 : 3.3);
        group.add(beacon);
        rig.beacon = beacon;
        const pm = new MeshBasicMaterial({ vertexColors: true, color: new Color(BEACON_HEX[beaconColour]), transparent: true, blending: AdditiveBlending, depthWrite: false, toneMapped: false, side: DoubleSide, fog: false });
        this.mats.push(pm);
        const pool = new Mesh(this.geo('pool', lightPool), pm);
        pool.scale.set(5.5, 1, 5.5);
        pool.renderOrder = 2;
        group.add(pool);
        rig.pool = pool;
      }
    }
    group.visible = false;
    this.hooks.scene.add(group);
    this.rigs.set(motion.id, rig);
    this.sink.push(() => this.hooks.scene.remove(group));
  }

  private drop(id: string, rig: Rig): void {
    this.hooks.scene.remove(rig.group);
    this.rigs.delete(id);
  }

  /** Puts every rig where its timeline says it is now; ends the ones whose time is over; asks for the next frame while any remains. */
  private tick = (): void => {
    this.timer = null;
    if (this.disposed) return;
    const now = performance.now();
    const lit = this.hooks.lit();
    for (const [id, rig] of [...this.rigs]) {
      const t = (now - rig.motion.startedMs) / 1000;
      if (t >= rig.seconds) { this.drop(id, rig); continue; }
      const type = KIND_VEHICLE[rig.motion.kind];
      const pose = rig.pose;
      if (type === 'tyre') tyrePoseAt(rig.motion.site, t, pose);
      else if (rig.motion.route) vehiclePoseAt(rig.motion.route, t, rig.wheelRadius, pose, extraHoldOf(rig.motion.kind));
      rig.group.visible = pose.visible;
      rig.group.position.set(pose.x, 0.02, pose.z);
      rig.group.rotation.y = pose.heading;
      if (type === 'tyre') rig.group.scale.setScalar(Math.max(0.001, pose.grow));
      for (const s of rig.spinners) s.rotation.x = pose.spin;
      for (const m of rig.lightMats) m.color.setScalar(0.35 + 3.2 * lit);
      if (rig.beacon) { const s = 0.25 + 0.75 * pose.beacon; rig.beacon.scale.set(s, s, s); rig.beacon.visible = pose.beacon > 0; }
      if (rig.pool) {
        const tyre = type === 'tyre';
        rig.pool.visible = tyre ? pose.visible : pose.beacon > 0;
        (rig.pool.material as MeshBasicMaterial).opacity = tyre ? 0.55 : 0.15 + 0.55 * pose.beacon;
      }
    }
    this.hooks.report(this.rigs.size);
    this.hooks.request();
    if (this.rigs.size && !this.disposed) this.timer = window.setTimeout(this.tick, this.hooks.slow() ? 125 : 66);
  };

  dispose(): void {
    this.disposed = true;
    if (this.timer !== null) window.clearTimeout(this.timer);
    for (const fn of this.sink) fn();
    if (this.racks) this.hooks.scene.remove(this.racks);
    for (const g of this.geos.values()) g.dispose();
    for (const m of this.mats) m.dispose();
    this.rigs.clear();
    this.hooks.report(0);
    this.hooks.request();
  }
}

const layers = new WeakMap<object, Layer>();

/** Gives a renderer the current work plan. An empty plan removes everything. Safe to call on every change. */
export function setWork(owner: object, hooks: WorkHooks, plan: WorkLayerPlan): void {
  let layer = layers.get(owner);
  if (!layer) { layer = new Layer(hooks); layers.set(owner, layer); }
  layer.set(plan);
}

export function detachWork(owner: object): void {
  layers.get(owner)?.dispose();
  layers.delete(owner);
}
