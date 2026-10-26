/**
 * The three.js side of the ambient motion: instanced traffic on the ring road lanes. Loaded only through the
 * renderer's dynamic import. A frame writes one matrix per vehicle into preallocated objects and allocates
 * nothing. Vehicles are scenery (see `hqAssets/environment/scenery.ts`): nothing here reads a record.
 */
import {
  BufferGeometry, Color, Float32BufferAttribute, InstancedMesh, MeshBasicMaterial, MeshLambertMaterial, MeshStandardMaterial, Matrix4, Quaternion, Vector3,
  type Material, type Scene,
} from 'three';
import { VERTEX_FLOATS } from './hqGeometry.ts';
import { vehiclePose, type SceneryPlan, type LanePose } from '../hqAssets/environment/scenery.ts';
import { VEHICLE_SCALE, sceneryVehicleParts } from '../hqAssets/environment/sceneryVehicle.ts';

const srgbToLinear = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);

function geometryOf(v: Float32Array): BufferGeometry {
  const n = v.length / VERTEX_FLOATS;
  const position = new Float32Array(n * 3), normal = new Float32Array(n * 3), color = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const s = i * VERTEX_FLOATS;
    position.set([v[s], v[s + 1], v[s + 2]], i * 3);
    normal.set([v[s + 3], v[s + 4], v[s + 5]], i * 3);
    color.set([srgbToLinear(v[s + 6]), srgbToLinear(v[s + 7]), srgbToLinear(v[s + 8])], i * 3);
  }
  const g = new BufferGeometry();
  g.setAttribute('position', new Float32BufferAttribute(position, 3));
  g.setAttribute('normal', new Float32BufferAttribute(normal, 3));
  g.setAttribute('color', new Float32BufferAttribute(color, 3));
  g.computeBoundingSphere();
  return g;
}

export class SceneryLayer {
  private meshes: InstancedMesh[] = [];
  private lights: MeshBasicMaterial | null = null;
  private plan: SceneryPlan | null = null;
  private readonly geometries: BufferGeometry[] = [];
  private readonly materials: Material[] = [];
  private readonly pose: LanePose = { x: 0, z: 0, heading: 0 };
  private readonly matrix = new Matrix4();
  private readonly position = new Vector3();
  private readonly scale = new Vector3();
  private readonly rotation = new Quaternion();
  private readonly up = new Vector3(0, 1, 0);
  private readonly tint = new Color();
  private lastLit = -1;

  constructor(private readonly scene: Scene, private readonly cheap: boolean) {}

  get count(): number { return this.plan?.vehicles.length ?? 0; }

  /** Replaces the traffic. A plan without vehicles removes the meshes. */
  setPlan(plan: SceneryPlan | undefined): void {
    this.clear();
    this.plan = plan && plan.vehicles.length ? plan : null;
    if (!this.plan) return;
    const n = this.plan.vehicles.length;
    const parts = sceneryVehicleParts();
    const make = (data: Float32Array, material: Material, tinted: boolean): InstancedMesh => {
      const geometry = geometryOf(data);
      this.geometries.push(geometry); this.materials.push(material);
      const mesh = new InstancedMesh(geometry, material, n);
      mesh.frustumCulled = false;
      mesh.castShadow = false;
      mesh.receiveShadow = !this.cheap && tinted;
      this.scene.add(mesh);
      this.meshes.push(mesh);
      return mesh;
    };
    const paint = this.cheap ? new MeshLambertMaterial({ vertexColors: true }) : new MeshStandardMaterial({ vertexColors: true, roughness: 0.32, metalness: 0.55 });
    const trim = this.cheap ? new MeshLambertMaterial({ vertexColors: true }) : new MeshStandardMaterial({ vertexColors: true, roughness: 0.4, metalness: 0.2 });
    this.lights = new MeshBasicMaterial({ vertexColors: true, toneMapped: true });
    const body = make(parts.body, paint, true);
    const trimMesh = make(parts.trim, trim, false);
    const lightMesh = make(parts.lights, this.lights, false);
    // The three parts share one matrix buffer, so a frame writes each vehicle once.
    trimMesh.instanceMatrix = body.instanceMatrix;
    lightMesh.instanceMatrix = body.instanceMatrix;
    this.plan.vehicles.forEach((v, i) => body.setColorAt(i, this.tint.setRGB(v.color[0], v.color[1], v.color[2])));
    if (body.instanceColor) body.instanceColor.needsUpdate = true;
    this.update(0);
  }

  /** Puts every vehicle where it is at `clock` seconds. */
  update(clock: number): void {
    const plan = this.plan;
    if (!plan || !this.meshes.length) return;
    const { pose, matrix, position, scale, rotation, up } = this;
    for (let i = 0; i < plan.vehicles.length; i++) {
      vehiclePose(plan, i, clock, pose);
      const k = VEHICLE_SCALE[plan.vehicles[i].kind];
      position.set(pose.x, 0.02, pose.z);
      rotation.setFromAxisAngle(up, pose.heading);
      scale.set(k[0], k[1], k[2]);
      matrix.compose(position, rotation, scale);
      this.meshes[0].setMatrixAt(i, matrix);
    }
    this.meshes[0].instanceMatrix.needsUpdate = true;
  }

  /** Head and tail lights: dim in daylight, bright (and past the bloom threshold) once the lights are on. */
  setLit(lit: number): void {
    if (!this.lights || Math.abs(lit - this.lastLit) < 0.004) return;
    this.lastLit = lit;
    this.lights.color.setScalar(0.35 + 3.2 * lit);
  }

  private clear(): void {
    for (const m of this.meshes) { this.scene.remove(m); m.dispose(); }
    for (const g of this.geometries) g.dispose();
    for (const m of this.materials) m.dispose();
    this.meshes = []; this.geometries.length = 0; this.materials.length = 0; this.lights = null; this.lastLit = -1;
  }

  dispose(): void { this.clear(); this.plan = null; }
}
