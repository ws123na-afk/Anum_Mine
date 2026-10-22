import assert from 'node:assert/strict';
import { test } from 'node:test';
import { findSceneRoom, projectHeadquarters, summarizeOfficeEvidence,
 type Scope,type Registry,type Room, type AgentEvidence } from '../src/lib/headquartersProjection.ts';
const scope:Scope={tenantId:'tenant-a',workspaceId:'workspace-a'};
const at=new Date('2026-10-21T12:00:00.000Z');
const building={id:'main',name:'ANUM Headquarters',timezone:'Asia/Riyadh',revision:1};
const floor={id:'ground',name:'Ground Floor',building_id:'main',level:0,revision:1};
const room:Room={id:'ops',name:'Operations',floor_id:'ground',kind:'office',capacity:3,revision:1};
const registry:Registry={buildings:[building],floors:[floor],rooms:[room],desks:[
 {id:'desk-a',room_id:'ops',label:'A01',agent_id:'agent-a',revision:1},
 {id:'desk-b',room_id:'ops',label:'A02',agent_id:null,revision:1},
]};
const agent:AgentEvidence={id:'agent-a',name:'Maintenance Planner',status:'running',
 observedAt:at.toISOString(),taskIds:['task-1'],source:'authenticated-agent-snapshot'};
function projection(overrides:Partial<Registry>={},agents:readonly AgentEvidence[]=[agent]){
 return projectHeadquarters(scope,{...registry,...overrides},agents,at);
}
test('creates a scoped, deterministic building-floor-room hierarchy',()=>{
 const result=projection();
 assert.deepEqual(result.scope,scope);
 assert.equal(result.source,'authenticated-office-registry');
 assert.equal(result.buildings.length,1);
 assert.equal(result.buildings[0].name,'ANUM Headquarters');
 assert.equal(result.buildings[0].floors.length,1);
 assert.equal(result.buildings[0].floors[0].rooms.length,1);
 assert.equal(result.buildings[0].floors[0].rooms[0].label,'Operations');
 assert.equal(result.generatedAt,at.toISOString());
 assert.deepEqual(result.issues,[]);
});
test('does not fabricate a desk for an employee without an assignment',()=>{
 const result=projection();
 assert.equal(result.buildings[0].floors[0].desks.length,2);
 assert.equal(result.buildings[0].floors[0].desks.filter(d=>d.agentId).length,1);
 assert.equal(result.buildings[0].floors[0].desks[1].agent,null);
});
test('does not fabricate activity from an unrecognized employee identifier',()=>{
 const different={...registry.desks[0],agent_id:'agent-invisible'};
 const result=projection({desks:[different]});
 const desk=result.buildings[0].floors[0].desks[0];
 assert.equal(desk.agentId,'agent-invisible');
 assert.equal(desk.agent,null);
 assert.equal(desk.assignmentVisible,true);
});
test('rejects stale evidence as a live indicator',()=>{
 const old={...agent,observedAt:'2026-10-21T11:20:00.000Z'};
 const result=projection({},[old]);
 assert.equal(result.buildings[0].floors[0].desks[0].agent,null);
 assert.ok(result.issues.some(x=>x.code==='STALE_EVIDENCE'));
});
test('reports duplicate desk assignments',()=>{
 const extra={id:'desk-c',room_id:'ops',label:'A03',agent_id:'agent-a',revision:1};
 const result=projection({desks:[...registry.desks,extra]});
 assert.equal(result.issues.filter(x=>x.code==='ASSIGNMENT_CONFLICT').length,1);
});
test('reports rooms that exceed capacity',()=>{
 const extra={id:'desk-c',room_id:'ops',label:'A03',agent_id:null,revision:1};
 const extra2={id:'desk-d',room_id:'ops',label:'A04',agent_id:null,revision:1};
 const result=projection({desks:[...registry.desks,extra,extra2]});
 assert.ok(result.issues.some(x=>x.code==='CAPACITY_OVERFLOW'));
});
test('does not silently lose orphan floor references',()=>{
 const other={...floor,id:'orphan',building_id:'missing'};
 const result=projection({floors:[other]});
 assert.ok(result.issues.some(x=>x.code==='ORPHAN_FLOOR'&&x.recordId==='orphan'));
});
test('flags orphan room and desk references independently',()=>{
 const result=projection({floors:[],rooms:[room],desks:[{...registry.desks[0],room_id:'orphan-room'}]});
 assert.ok(result.issues.some(x=>x.code==='ORPHAN_ROOM'));
 assert.ok(result.issues.some(x=>x.code==='ORPHAN_DESK'));
});
test('rejects empty tenant/workspace selectors',()=>{
 assert.throws(()=>projectHeadquarters({...scope,workspaceId:''},registry,[agent],at),/scope/);
 assert.throws(()=>projectHeadquarters({...scope,tenantId:'   '},registry,[agent],at),/scope/);
});
test('rejects invalid snapshot time',()=>{
 assert.throws(()=>projectHeadquarters(scope,registry,[agent],new Date(NaN)),/observation/);
});
test('supports more than one independent business headquarters',()=>{
 const b={id:'store',name:'ANUM Commerce',timezone:'Asia/Dubai',revision:1};
 const f={id:'store-ground',name:'Store',building_id:b.id,level:0,revision:1};
 const r={id:'orders',name:'Orders',floor_id:f.id,kind:'warehouse' as const,capacity:4,revision:1};
 const result=projection({buildings:[building,b],floors:[floor,f],rooms:[room,r]});
 assert.equal(result.buildings.length,2);
 assert.equal(findSceneRoom(result,'store','store-ground','orders')?.label,'Orders');
 assert.equal(findSceneRoom(result,'main','ground','orders'),null);
});
test('summaries are derived from actual records only',()=>{
 const result=projection();
 assert.deepEqual(summarizeOfficeEvidence(result),{
  buildings:1,floors:1,rooms:1,desks:2,assigned:1,fresh:1,issues:0,
 });
});
test('layout records cannot mutate canonical data',()=>{
 const result=projection();
 const original=registry.rooms[0];
 assert.equal(original.name,'Operations');
 assert.equal(result.buildings[0].floors[0].rooms[0].evidence,'registry');
 assert.equal(registry.desks[0].label,'A01');
});
test('handles empty server registry without inventing a showcase',()=>{
 const empty:Registry={buildings:[],floors:[],rooms:[],desks:[]};
 const result=projectHeadquarters(scope,empty,[],at);
 assert.deepEqual(result.buildings,[]);
 assert.deepEqual(result.issues,[]);
 assert.equal(summarizeOfficeEvidence(result).rooms,0);
});
test('stable room sorting uses names and IDs',()=>{
 const rooms=[
  {...room,id:'beta',name:'Zed'},
  {...room,id:'alpha',name:'Alpha'},
  {...room,id:'gamma',name:'Alpha'},
 ];
 const result=projection({rooms,desks:[]});
 assert.deepEqual(result.buildings[0].floors[0].rooms.map(x=>x.id),['alpha','gamma','beta']);
});
