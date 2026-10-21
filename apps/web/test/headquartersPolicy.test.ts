import assert from 'node:assert/strict';
import { test } from 'node:test';
import { canDisplayAgentEvidence,classifyRoomActivity,mayChangeDesk,mayEnterRoom,
  officeCapabilitySummary,routeForRoom,safeModuleHash, type Actor } from '../src/lib/headquartersPolicy.ts';
import type { RoomGeometry,Scope } from '../src/lib/headquartersProjection.ts';
const scope:Scope={tenantId:'a',workspaceId:'work'};
const actor:Actor={id:'owner',scope,active:true,permissions:new Set(['task:read','workspace:manage','agent:read','meeting:manage'])};
const room:RoomGeometry={id:'r',buildingId:'b',floorId:'f',label:'Boardroom',
 x:0,z:0,width:6,depth:4,height:3,capacity:8,revision:1,deskIds:[],evidence:'registry',kind:'meeting'};
test('authorized actors may enter only their own workspace',()=>{
 assert.equal(mayEnterRoom(actor,room,scope).allowed,true);
 assert.equal(mayEnterRoom(actor,room,{...scope,workspaceId:'other'}).allowed,false);
});
test('inactive members cannot enter rooms',()=>{
 assert.equal(mayEnterRoom({...actor,active:false},room,scope).allowed,false);
});
test('reading rooms requires an explicit task permission',()=>{
 assert.equal(mayEnterRoom({...actor,permissions:new Set()},room,scope).allowed,false);
});
test('meeting rooms prohibit permanent desk changes',()=>{
 assert.equal(mayChangeDesk(actor,room,scope).allowed,false);
});
test('work rooms require owner permission before desk changes',()=>{
 const office={...room,kind:'office' as const};
 assert.equal(mayChangeDesk(actor,office,scope).allowed,true);
 assert.equal(mayChangeDesk({...actor,permissions:new Set(['task:read'])},office,scope).allowed,false);
});
test('agent evidence cannot leak into other workspaces',()=>{
 assert.equal(canDisplayAgentEvidence(actor,scope,'authenticated-agent-snapshot',false),true);
 assert.equal(canDisplayAgentEvidence(actor,{...scope,tenantId:'another'},'authenticated-agent-snapshot',false),false);
 assert.equal(canDisplayAgentEvidence(actor,scope,'unknown',false),false);
 assert.equal(canDisplayAgentEvidence(actor,scope,'authenticated-agent-snapshot',true),false);
});
test('server-derived room types route to existing modules only',()=>{
 assert.equal(routeForRoom(room),'meetings');
 assert.equal(safeModuleHash(routeForRoom(room)),'#meetings');
 assert.equal(safeModuleHash(routeForRoom({...room,kind:'workshop'})),'#tyre');
});
test('room activity classification never asserts actual employee presence',()=>{
 assert.equal(classifyRoomActivity(room,0,0),'unassigned');
 assert.equal(classifyRoomActivity(room,2,0),'configured');
 assert.equal(classifyRoomActivity(room,2,1),'task-evidence');
 assert.equal(classifyRoomActivity(room,9,1),'over-capacity');
});
test('capability summary respects current membership',()=>{
 assert.deepEqual(officeCapabilitySummary({...actor,active:false}),{
  canView:false,canManage:false,canSeeAgentEvidence:false,canManageMeetings:false,
 });
});
