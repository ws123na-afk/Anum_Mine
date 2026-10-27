/**
 * The three.js side of the ambient motion: instanced traffic on the ring road and round the roundabout. Loaded only through the
 * renderer's dynamic import. A frame writes one matrix per vehicle and one per wheel into preallocated objects and allocates
 * nothing. Vehicles are scenery (see `hqAssets/environment/scenery.ts`): nothing here reads a record.
 *
 * Two families are drawn: cars, SUVs, vans and buses (one model, scaled) and tyre haulers (their own model). Each family is body, trim and
 * lights sharing one matrix buffer, plus a pool of light on the road ahead of the nose that shows only once the lights are on. All wheels
 * are one instanced mesh whose matrices carry the spin, so they turn with the distance driven.
 */
import {
  AdditiveBlending, BufferGeometry, Color, InstancedMesh, MeshBasicMaterial, MeshLambertMaterial, MeshStandardMaterial, Matrix4, Quaternion, Vector3,
  type Material, type Scene,
} from 'three';
import { geometryOf } from './hqLayerGeo.ts';
import { vehiclePose, type SceneryPlan, type LanePose, type VehicleKind } from '../hqAssets/environment/scenery.ts';
import { VEHICLE_SCALE, WHEEL_RADIUS, WHEEL_SPOTS, haulerParts, sceneryBeam, sceneryVehicleParts, sceneryWheel } from '../hqAssets/environment/sceneryVehicle.ts';

/**
 * The vehicle models the work layer reuses. The renderer hands them over through `workHooks().kit`, so the work layer (its own lazy chunk) does not
 * import this module's dependencies and the bundler keeps them in the renderer chunk with the traffic.
 */
export const vehicleKit = { geometryOf, VEHICLE_SCALE, WHEEL_RADIUS, WHEEL_SPOTS, sceneryVehicleParts, haulerParts, sceneryWheel } as const;
export type VehicleKit = typeof vehicleKit;

type Family = { ids: number[]; mesh: InstancedMesh };

export class SceneryLayer {
  private meshes: InstancedMesh[] = [];
  private families: Family[] = [];
  private wheels: InstancedMesh | null = null;
  private beams: InstancedMesh[] = [];
  private lights: MeshBasicMaterial | null = null;
  private beamMaterial: MeshBasicMaterial | null = null;
  private plan: SceneryPlan | null = null;
  private readonly geometries: BufferGeometry[] = [];
  private readonly materials: Material[] = [];
  private readonly pose: LanePose = { x: 0, z: 0, heading: 0 };
  private readonly matrix = new Matrix4();
  private readonly position = new Vector3();
  private readonly offset = new Vector3();
  private readonly scale = new Vector3();
  private readonly rotation = new Quaternion();
  private readonly spinQ = new Quaternion();
  private readonly wheelQ = new Quaternion();
  private readonly up = new Vector3(0, 1, 0);
  private readonly right = new Vector3(1, 0, 0);
  private readonly tint = new Color();
  private lastLit = -1;

  constructor(private readonly scene: Scene, private readonly cheap: boolean) {}

  get count(): number { return this.plan?.vehicles.length ?? 0; }

  /** Replaces the traffic. A plan without vehicles removes the meshes. */
  setPlan(plan: SceneryPlan | undefined): void {
    this.clear();
    this.plan = plan && plan.vehicles.length ? plan : null;
    if (!this.plan) return;
    const vehicles = this.plan.vehicles;
    const add = (data: Float32Array, material: Material, n: number, shadow: boolean): InstancedMesh => {
      const geometry = geometryOf(data);
      this.geometries.push(geometry);
      const mesh = new InstancedMesh(geometry, material, n);
      mesh.frustumCulled = false;
      mesh.castShadow = false;
      mesh.receiveShadow = shadow;
      this.scene.add(mesh);
      this.meshes.push(mesh);
      return mesh;
    };
    const lambertOrPbr = (roughness: number, metalness: number): Material => (this.cheap ? new MeshLambertMaterial({ vertexColors: true }) : new MeshStandardMaterial({ vertexColors: true, roughness, metalness }));
    const paint = lambertOrPbr(0.32, 0.55), trim = lambertOrPbr(0.4, 0.2);
    this.lights = new MeshBasicMaterial({ vertexColors: true, toneMapped: true });
    this.beamMaterial = new MeshBasicMaterial({ vertexColors: true, transparent: true, blending: AdditiveBlending, depthWrite: false, toneMapped: false, fog: false });
    this.materials.push(paint, trim, this.lights, this.beamMaterial);
    const groups: Array<{ kinds: (k: VehicleKind) => boolean; parts: ReturnType<typeof sceneryVehicleParts> }> = [
      { kinds: (k) => k !== 4, parts: sceneryVehicleParts() },
      { kinds: (k) => k === 4, parts: haulerParts() },
    ];
    let wheelCount = 0;
    for (const g of groups) {
      const ids: number[] = [];
      vehicles.forEach((v, i) => { if (g.kinds(v.kind)) { ids.push(i); wheelCount += WHEEL_SPOTS[v.kind].length; } });
      if (!ids.length) continue;
      const body = add(g.parts.body, paint, ids.length, !this.cheap);
      const trimMesh = add(g.parts.trim, trim, ids.length, false);
      const lightMesh = add(g.parts.lights, this.lights, ids.length, false);
      const beam = add(sceneryBeam(), this.beamMaterial, ids.length, false);
      beam.visible = false;
      beam.renderOrder = 2;
      // The parts of a family share one matrix buffer, so a frame writes each vehicle once.
      trimMesh.instanceMatrix = body.instanceMatrix; lightMesh.instanceMatrix = body.instanceMatrix; beam.instanceMatrix = body.instanceMatrix;
      this.beams.push(beam);
      ids.forEach((id, j) => body.setColorAt(j, this.tint.setRGB(vehicles[id].color[0], vehicles[id].color[1], vehicles[id].color[2])));
      if (body.instanceColor) body.instanceColor.needsUpdate = true;
      this.families.push({ ids, mesh: body });
    }
    this.wheels = add(sceneryWheel(), trim, wheelCount, false);
    this.update(0);
  }

  /** Puts every vehicle, and every wheel with its spin, where they are at `clock` seconds. */
  update(clock: number): void {
    const plan = this.plan;
    if (!plan || !this.meshes.length) return;
    const { pose, matrix, position, scale, rotation, up, right, offset, spinQ, wheelQ } = this;
    let w = 0;
    for (const family of this.families) {
      for (let j = 0; j < family.ids.length; j++) {
        const i = family.ids[j], v = plan.vehicles[i];
        vehiclePose(plan, i, clock, pose);
        const k = VEHICLE_SCALE[v.kind];
        position.set(pose.x, 0.02, pose.z);
        rotation.setFromAxisAngle(up, pose.heading);
        scale.set(k[0], k[1], k[2]);
        matrix.compose(position, rotation, scale);
        family.mesh.setMatrixAt(j, matrix);
        // Wheels: turn by the arc length driven over the wheel's radius, about the vehicle's own sideways axis.
        const radius = WHEEL_RADIUS[v.kind];
        spinQ.setFromAxisAngle(right, (v.start + v.speed * clock) / radius);
        wheelQ.copy(rotation).multiply(spinQ);
        scale.setScalar(radius);
        for (const spot of WHEEL_SPOTS[v.kind]) {
          offset.set(spot[0], radius, spot[1]).applyQuaternion(rotation);
          position.set(pose.x + offset.x, 0.02 + offset.y, pose.z + offset.z);
          matrix.compose(position, wheelQ, scale);
          this.wheels!.setMatrixAt(w++, matrix);
        }
      }
      family.mesh.instanceMatrix.needsUpdate = true;
    }
    if (this.wheels) this.wheels.instanceMatrix.needsUpdate = true;
  }

  /** Head and tail lights and the pools of light ahead of the nose: dim or hidden in daylight, bright (and past the bloom threshold) once the lights are on. */
  setLit(lit: number): void {
    if (!this.lights || Math.abs(lit - this.lastLit) < 0.004) return;
    this.lastLit = lit;
    this.lights.color.setScalar(0.35 + 3.2 * lit);
    this.beamMaterial?.color.setScalar(0.55 * lit);
    for (const b of this.beams) b.visible = lit > 0.05;
  }

  private clear(): void {
    for (const m of this.meshes) { this.scene.remove(m); m.dispose(); }
    for (const g of this.geometries) g.dispose();
    for (const m of this.materials) m.dispose();
    this.meshes = []; this.families = []; this.beams = []; this.wheels = null;
    this.geometries.length = 0; this.materials.length = 0; this.lights = null; this.beamMaterial = null; this.lastLit = -1;
  }

  dispose(): void { this.clear(); this.plan = null; }
}
