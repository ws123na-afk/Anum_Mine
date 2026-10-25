import assert from 'node:assert/strict';
import { test } from 'node:test';
import { HeadquartersController, type DataSource, type OfficeState } from '../src/lib/headquartersController.ts';
import type { AgentEvidence, Registry, Scope } from '../src/lib/headquartersProjection.ts';

// F14 tenant-switch race: responses of the previous organisation must be discarded, whatever the order.
const A: Scope = { tenantId: 'tenant-a', workspaceId: 'workspace-a' };
const B: Scope = { tenantId: 'tenant-b', workspaceId: 'workspace-b' };
const registryFor = (label: string): Registry => ({
  buildings: [{ id: `${label}-b`, name: `${label} Headquarters`, timezone: 'Asia/Dubai', revision: 1 }],
  floors: [{ id: `${label}-f`, name: `${label} Floor`, building_id: `${label}-b`, level: 0, revision: 1 }],
  rooms: [{ id: `${label}-r`, name: `${label} Room`, floor_id: `${label}-f`, kind: 'office', capacity: 4, revision: 1 }],
  desks: [],
});
const none: readonly AgentEvidence[] = [];
function deferred<T>() { let resolve!: (v: T) => void; const promise = new Promise<T>((r) => { resolve = r; }); return { promise, resolve }; }

test('a slow answer for organisation A that arrives after the switch to B is never published', async () => {
  let current = A;
  const slowA = deferred<Registry>();
  const source: DataSource = {
    scope: () => current,
    registry: () => (current === A ? slowA.promise : Promise.resolve(registryFor('B'))),
    agents: async () => none,
  };
  const controller = new HeadquartersController(source);
  const seen: OfficeState[] = [];
  controller.subscribe((s) => seen.push(s));
  const first = controller.initialize();
  current = B;
  await controller.refresh();
  slowA.resolve(registryFor('A'));
  await first;
  const final = controller.snapshot();
  assert.equal(final.status, 'ready');
  assert.deepEqual(final.scope, B);
  assert.equal(final.projection?.buildings[0].name, 'B Headquarters');
  // No state at any moment pairs scope B with A's records, or the reverse.
  for (const s of seen) {
    const name = s.projection?.buildings[0]?.name;
    if (name) assert.equal(name.startsWith(s.scope?.tenantId === 'tenant-a' ? 'A' : 'B'), true, `leak in revision ${s.revision}`);
  }
  controller.dispose();
});

test('an answer for A that resolves while the scope already moved, with no new reload, is dropped and B is loaded', async () => {
  let current = A;
  const slowA = deferred<Registry>();
  let loadsB = 0;
  const source: DataSource = {
    scope: () => current,
    registry: async () => { if (current === A) return slowA.promise; loadsB += 1; return registryFor('B'); },
    agents: async () => none,
  };
  const controller = new HeadquartersController(source);
  const first = controller.initialize();
  current = B;
  slowA.resolve(registryFor('A'));
  await first;
  const s = controller.snapshot();
  assert.equal(s.projection?.buildings[0].name, 'B Headquarters');
  assert.ok(loadsB >= 1);
  controller.dispose();
});

test('a failure of the old organisation after the switch does not replace the new organisation state', async () => {
  let current = A;
  const slowA = deferred<Registry>();
  let failA!: (e: Error) => void;
  const failing = new Promise<Registry>((_, reject) => { failA = reject; });
  void slowA;
  const source: DataSource = {
    scope: () => current,
    registry: () => (current === A ? failing : Promise.resolve(registryFor('B'))),
    agents: async () => none,
  };
  const controller = new HeadquartersController(source);
  const first = controller.initialize();
  current = B;
  await controller.refresh();
  failA(new Error('network offline'));
  await first;
  const s = controller.snapshot();
  assert.equal(s.status, 'ready');
  assert.equal(s.error, null);
  assert.equal(s.projection?.buildings[0].name, 'B Headquarters');
  controller.dispose();
});

test('refresh in A, switch to B whose load fails: A records are not shown during the error', async () => {
  let current = A;
  const source: DataSource = {
    scope: () => current,
    registry: async () => { if (current === A) return registryFor('A'); throw new Error('403 forbidden'); },
    agents: async () => none,
  };
  const controller = new HeadquartersController(source);
  await controller.initialize();
  assert.equal(controller.snapshot().projection?.buildings[0].name, 'A Headquarters');
  current = B;
  await controller.refresh();
  const s = controller.snapshot();
  assert.equal(s.status, 'error');
  assert.equal(s.projection, null);
  assert.equal(s.error?.code, 'NOT_AUTHORIZED');
  controller.dispose();
});
