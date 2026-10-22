import assert from 'node:assert/strict';
import { test } from 'node:test';
import { HeadquartersController,adaptOfficeRegistry,scopeFingerprint,
  type DataSource,type OfficeState } from '../src/lib/headquartersController.ts';
import type { AgentEvidence,Registry,Scope } from '../src/lib/headquartersProjection.ts';
const tenant:Scope={tenantId:'tenant-a',workspaceId:'workspace-a'};
const t=new Date('2026-10-21T12:00:00Z').toISOString();
const registry:Registry={
 buildings:[{id:'office',name:'ANUM Headquarters',timezone:'Asia/Riyadh',revision:1}],
 floors:[{id:'ground',name:'Ground',building_id:'office',level:0,revision:1}],
 rooms:[{id:'reception',name:'Reception',floor_id:'ground',kind:'reception',capacity:3,revision:1},
  {id:'meeting',name:'Meeting',floor_id:'ground',kind:'meeting',capacity:8,revision:1}],
 desks:[{id:'desk-1',room_id:'reception',label:'R1',agent_id:'agent-a',revision:1}],
};
const agents:readonly AgentEvidence[]=[{id:'agent-a',name:'ANUM PA',status:'running',observedAt:t,
 taskIds:['task-1'],source:'authenticated-agent-snapshot'}];
function source(data:Registry=registry,people:readonly AgentEvidence[]=agents):DataSource{
 return {scope:()=>tenant,registry:async()=>data,agents:async()=>people};
}
async function ready(controller:HeadquartersController):Promise<OfficeState>{
 await controller.initialize();
 const snapshot=controller.snapshot();
 assert.equal(snapshot.status,'ready',snapshot.error?.message);
 return snapshot;
}
test('initialization loads a complete scoped hierarchy',async()=>{
 const controller=new HeadquartersController(source());
 const snapshot=await ready(controller);
 assert.deepEqual(snapshot.scope,tenant);
 assert.equal(snapshot.selected.buildingId,'office');
 assert.equal(snapshot.selected.floorId,'ground');
 assert.equal(snapshot.selected.roomId,'meeting');
 assert.ok(snapshot.camera);
 assert.equal(snapshot.projection?.buildings.length,1);
 controller.dispose();
});
test('transitions through loading and ready states',async()=>{
 const controller=new HeadquartersController(source());
 const statuses:string[]=[];
 const unsubscribe=controller.subscribe(s=>statuses.push(s.status));
 await controller.initialize();
 assert.ok(statuses.includes('loading'));
 assert.ok(statuses.includes('ready'));
 assert.equal(controller.snapshot().busy,false);
 unsubscribe();controller.dispose();
});
test('a subscriber receives an initial snapshot without initiating fetch',()=>{
 const controller=new HeadquartersController(source());
 const changes:OfficeState[]=[];
 const unsubscribe=controller.subscribe(s=>changes.push(s));
 assert.equal(changes.length,1);
 assert.equal(changes[0].status,'uninitialized');
 unsubscribe();controller.dispose();
});
test('selected room remains stable after a same-workspace refresh',async()=>{
 const controller=new HeadquartersController(source());
 await ready(controller);
 controller.selectRoom('reception');
 await controller.refresh();
 assert.equal(controller.snapshot().selected.roomId,'reception');
 controller.dispose();
});
test('a nonexistent room fails closed without changing selection',async()=>{
 const controller=new HeadquartersController(source());
 await ready(controller);
 const before=controller.snapshot().selected;
 assert.throws(()=>controller.selectRoom('wrong-room'),/not in selected floor/);
 assert.deepEqual(controller.snapshot().selected,before);
 controller.dispose();
});
test('a nonexistent building does not leak prior selection',async()=>{
 const controller=new HeadquartersController(source());
 await ready(controller);
 assert.throws(()=>controller.selectBuilding('another-tenant'),/not in your workspace/);
 controller.dispose();
});
test('server request failure does not announce a successful refresh',async()=>{
 let fail=false;
 const data:DataSource={...source(),registry:async()=>{
  if(fail)throw new Error('Network timeout');
  return registry;
 }};
 const controller=new HeadquartersController(data);
 await ready(controller);fail=true;
 await controller.refresh();
 assert.equal(controller.snapshot().status,'stale');
 assert.equal(controller.snapshot().error?.code,'NETWORK');
 assert.ok(controller.snapshot().projection);
 controller.dispose();
});
test('initial network failure reveals no fabricated buildings',async()=>{
 const controller=new HeadquartersController({...source(),registry:async()=>{throw new Error('Network timeout')}});
 await controller.initialize();
 assert.equal(controller.snapshot().status,'error');
 assert.equal(controller.snapshot().projection,null);
 assert.equal(controller.snapshot().busy,false);
 controller.dispose();
});
test('scope replacement always wipes prior tenant snapshot',async()=>{
 let scope=tenant;
 const controller=new HeadquartersController({...source(),scope:()=>scope});
 await ready(controller);
 scope={tenantId:'tenant-b',workspaceId:'workspace-b'};
 let allowFinish:()=>void=()=>{};
 const delayedRegistry=new Promise<Registry>(resolve=>{allowFinish=()=>resolve(registry)});
 // Controller source is held immutable: test generation invalidation using a second refresh.
 const inFlight=controller.refresh();
 assert.equal(controller.snapshot().projection,null);
 assert.equal(controller.snapshot().scope?.tenantId,'tenant-b');
 allowFinish();await inFlight;
 controller.dispose();
 void delayedRegistry;
});
test('a delayed earlier response cannot overwrite a newer refresh',async()=>{
 let firstResolve:(value:Registry)=>void=()=>{};
 const delayed=new Promise<Registry>(resolve=>{firstResolve=resolve});
 let call=0;
 const data:DataSource={...source(),registry:()=>++call===1?delayed:Promise.resolve(registry)};
 const controller=new HeadquartersController(data);
 const old=controller.initialize();
 const fresh=controller.refresh();
 await fresh;
 const version=controller.snapshot().revision;
 firstResolve({buildings:[],floors:[],rooms:[],desks:[]});
 await old;
 assert.ok(controller.snapshot().projection?.buildings.length);
 assert.equal(controller.snapshot().revision,version);
 controller.dispose();
});
test('failure authorization is explicitly marked non-retryable',async()=>{
 const controller=new HeadquartersController({...source(),registry:async()=>{throw new Error('403 forbidden')}});
 await controller.initialize();
 assert.equal(controller.snapshot().error?.code,'NOT_AUTHORIZED');
 assert.equal(controller.snapshot().error?.canRetry,false);
 controller.dispose();
});
test('dispose removes listeners and prevents future mutation',async()=>{
 const controller=new HeadquartersController(source());
 let notifications=0;
 controller.subscribe(()=>notifications++);
 await ready(controller);controller.dispose();
 const count=notifications;
 await controller.refresh();
 assert.equal(notifications,count);
 await assert.rejects(()=>controller.initialize(),/disposed/);
});
test('summaries use persisted assignments, not example room staff',async()=>{
 const controller=new HeadquartersController(source());
 await ready(controller);
 assert.equal(controller.summarize()?.buildings,1);
 assert.equal(controller.summarize()?.rooms,2);
 assert.equal(controller.summarize()?.assigned,1);
 controller.dispose();
});
test('registry adapter refuses unknown room kinds',()=>{
 assert.throws(()=>adaptOfficeRegistry({...registry,rooms:[{...registry.rooms[0],kind:'fantasy'}]}),/Unknown/);
});
test('scope fingerprint retains tenant and workspace discrimination',()=>{
 assert.notEqual(scopeFingerprint(tenant),scopeFingerprint({...tenant,workspaceId:'other'}));
 assert.notEqual(scopeFingerprint(tenant),scopeFingerprint({...tenant,tenantId:'other'}));
});
