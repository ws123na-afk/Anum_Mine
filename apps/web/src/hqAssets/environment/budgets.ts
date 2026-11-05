/**
 * Triangle budgets for the environment kit: one entry per asset with its cap and the count measured
 * with default settings. The unit test fails when an asset grows past its cap, so a change that
 * makes a tree or a car heavier has to say so here.
 */
import { MeshBuilder, hex } from '../../lib/hqGeometry.ts';
import { campusEnvironment, CAMPUS_ENVIRONMENT_TRIANGLES_CAP } from './campusEnvironment.ts';
import { bin, bench, BENCH_TRIANGLES_CAP, busShelter, fence, gateHouse, GATE_HOUSE_TRIANGLES_CAP, lamp, LAMP_TRIANGLES_CAP, SHELTER_TRIANGLES_CAP } from './furniture.ts';
import { treeTemplate, TREE_SPECIES, TREE_TRIANGLES_CAP, flowerBed, hedgeRun, shrub, topiary } from './planting.ts';
import { carPark, ringRoad, walkway, RING_ROAD_TRIANGLES_CAP } from './roads.ts';
import { footballPitch, helipad, tennisCourt, HELIPAD_TRIANGLES_CAP, PITCH_TRIANGLES_CAP, TENNIS_TRIANGLES_CAP } from './sports.ts';
import { skylineBackdrop, skylineTower, TOWER_TRIANGLES_CAP } from './skyline.ts';
import { bus, BUS_TRIANGLES_CAP, car, CAR_TRIANGLES_CAP, truck, TRUCK_TRIANGLES_CAP, van, VAN_TRIANGLES_CAP, type CarKind } from './vehicles.ts';
import { bridge, BRIDGE_TRIANGLES_CAP, canal, fountain, FOUNTAIN_TRIANGLES_CAP, pond, POND_TRIANGLES_CAP } from './water.ts';
import { roundedRectPath } from './paths.ts';
import { AVENUE_TRIANGLES_CAP, CENTER_PLAZA_TRIANGLES_CAP, ROUNDABOUT_TRIANGLES_CAP, avenue, centerPlaza, roundabout } from './plaza.ts';

export type BudgetRow = Readonly<{ name: string; triangles: number; cap: number }>;

/** Measures every asset with default settings and a fixed seed. */
export function measureEnvironmentKit(): BudgetRow[] {
  const rows: BudgetRow[] = [];
  const add = (name: string, cap: number, run: (b: MeshBuilder) => number) => rows.push({ name, cap, triangles: run(new MeshBuilder()) });
  for (const kind of ['sedan', 'hatch', 'suv', 'coupe', 'pickup'] as CarKind[]) {
    add(`car ${kind}`, CAR_TRIANGLES_CAP.full, (b) => car(b, 0, 0, { kind, color: hex('#2f4b66') }).triangles);
    add(`car ${kind} low`, CAR_TRIANGLES_CAP.low, (b) => car(b, 0, 0, { kind, color: hex('#2f4b66'), detail: 'low' }).triangles);
  }
  add('van cargo', VAN_TRIANGLES_CAP, (b) => van(b, 0, 0, { kind: 'cargo' }).triangles);
  add('van passenger', VAN_TRIANGLES_CAP, (b) => van(b, 0, 0, { kind: 'passenger' }).triangles);
  for (const kind of ['box', 'flatbed', 'tanker'] as const) add(`truck ${kind}`, TRUCK_TRIANGLES_CAP, (b) => truck(b, 0, 0, { kind }).triangles);
  add('truck semi', TRUCK_TRIANGLES_CAP + 700, (b) => truck(b, 0, 0, { kind: 'semi' }).triangles);
  add('bus', BUS_TRIANGLES_CAP, (b) => bus(b, 0, 0).triangles);
  add('ring road 220x160', RING_ROAD_TRIANGLES_CAP + 900, (b) => ringRoad(b, { cx: 0, cz: 0, halfW: 110, halfD: 80, junctionEdges: [0, 1], crosswalkEdges: [2, 3] }).triangles);
  add('walkway ring', 500, (b) => walkway(b, roundedRectPath(0, 0, 40, 30, 8, 5), { closed: true }).triangles);
  add('car park 8 bays, 70 percent', 5600, (b) => carPark(b, 0, 0, { cols: 8, seed: 'budget' }).triangles);
  add('canal 100 m', 400, (b) => canal(b, [[0, 0], [50, 0], [100, 10]]).triangles);
  for (const style of ['arch', 'flat', 'foot'] as const) add(`bridge ${style}`, BRIDGE_TRIANGLES_CAP, (b) => bridge(b, 0, 0, { style }).triangles);
  add('pond', POND_TRIANGLES_CAP, (b) => pond(b, 0, 0, 8, { seed: 'budget' }).triangles);
  add('fountain', FOUNTAIN_TRIANGLES_CAP, (b) => fountain(b, 0, 0).triangles);
  add('centre plaza and fountain', CENTER_PLAZA_TRIANGLES_CAP, (b) => centerPlaza(b, 0, 0).triangles);
  add('roundabout with two mouths and crossings', ROUNDABOUT_TRIANGLES_CAP, (b) => roundabout(b, 0, 0, { mouths: [0, Math.PI / 2], crossings: [0.5, 1, 2.2, 3, 4, 5, 5.8] }).triangles);
  add('avenue 90 m', AVENUE_TRIANGLES_CAP, (b) => avenue(b, [0, 0], [90, 0]).triangles);
  add('tennis court', TENNIS_TRIANGLES_CAP, (b) => tennisCourt(b, 0, 0).triangles);
  add('football pitch', PITCH_TRIANGLES_CAP, (b) => footballPitch(b, 0, 0).triangles);
  add('helipad', HELIPAD_TRIANGLES_CAP, (b) => helipad(b, 0, 0).triangles);
  for (const sp of TREE_SPECIES) for (let v = 0; v < 3; v++) add(`tree ${sp} ${v}`, TREE_TRIANGLES_CAP, () => treeTemplate(sp, v).triangles);
  add('shrub', 140, (b) => shrub(b, 0, 0, 1).triangles);
  add('topiary', 100, (b) => topiary(b, 0, 0).triangles);
  add('hedge 20 m', 40, (b) => hedgeRun(b, [[0, 0], [20, 0]]).triangles);
  add('flower bed', 560, (b) => flowerBed(b, 0, 0, 6, 3).triangles);
  add('bench', BENCH_TRIANGLES_CAP, (b) => bench(b, 0, 0).triangles);
  for (const kind of ['street', 'double', 'garden', 'bollard'] as const) add(`lamp ${kind}`, LAMP_TRIANGLES_CAP, (b) => lamp(b, 0, 0, { kind }).triangles);
  add('fence 25 m bar', 520, (b) => fence(b, [[0, 0], [25, 0]], { kind: 'bar' }).triangles);
  add('gate house', GATE_HOUSE_TRIANGLES_CAP, (b) => gateHouse(b, 0, 0).triangles);
  add('bus shelter', SHELTER_TRIANGLES_CAP, (b) => busShelter(b, 0, 0).triangles);
  add('bin', 60, (b) => bin(b, 0, 0).triangles);
  add('skyline tower', TOWER_TRIANGLES_CAP, (b) => skylineTower(b, 0, 0, { shape: 'setback', h: 120, seed: 'budget' }).triangles);
  add('skyline 18 towers', TOWER_TRIANGLES_CAP * 18, (b) => skylineBackdrop(b, { cx: 0, cz: 0, radius: 270, count: 18 }).triangles);
  add('campus environment 220x160', CAMPUS_ENVIRONMENT_TRIANGLES_CAP, (b) => campusEnvironment(b, { cx: 0, cz: 0, halfW: 110, halfD: 80, seed: 'budget' }).triangles);
  return rows;
}
