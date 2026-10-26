import assert from 'node:assert/strict';
import {describe,it} from 'node:test';
import {applySpaceCommand,EMPTY_REGISTRY,occupation,roomsOnFloor,seedOfficeRegistry,SpaceError,type SpaceRegistry,type SpaceCommand} from '../src/lib/officeSpaceRegistry.ts';
function run(state:SpaceRegistry,command:SpaceCommand extends infer T ? T extends {expectedRevision:number} ? Omit<T,'expectedRevision'> : never : never){
 return applySpaceCommand(state,{...command,expectedRevision:state.revision} as SpaceCommand);
}
describe('office space registry',()=>{
 it('seeds separate offices with unique floors and safe initial desk availability',()=>{
  const state=seedOfficeRegistry();
  assert.equal(state.buildings.length,4);
  assert.equal(state.floors.filter(f=>f.buildingId==='anum-hq').length,3);
  assert.ok(state.desks.length>15);
  assert.ok(state.desks.every(d=>d.agentId===null));
  assert.equal(roomsOnFloor(state,'anum-hq-0').length,2);
 });
 it('supports a full building->floor->room->desk->agent assignment lifecycle',()=>{
  let s=run(EMPTY_REGISTRY,{type:'building.add',building:{id:'company',name:'Company HQ',timezone:'Asia/Riyadh',latitude:24.7,longitude:46.6}});
  s=run(s,{type:'floor.add',floor:{id:'floor-0',buildingId:'company',level:0,name:'Ground'}});
  s=run(s,{type:'room.add',room:{id:'room-1',floorId:'floor-0',name:'Product Studio',kind:'office',capacity:2}});
  s=run(s,{type:'desk.add',desk:{id:'desk-1',roomId:'room-1',label:'A',agentId:null}});
  s=run(s,{type:'desk.assign',deskId:'desk-1',agentId:'agent-1'});
  assert.deepEqual(occupation(s,'room-1'),{total:1,occupied:1,vacant:0});
  assert.throws(()=>run(s,{type:'desk.assign',deskId:'desk-1',agentId:'agent-2'}),(e:unknown)=>e instanceof SpaceError&&e.code==='occupied');
  s=run(s,{type:'room.rename',roomId:'room-1',name:'Digital Product Studio'});
  assert.equal(s.rooms[0].name,'Digital Product Studio');
  s=run(s,{type:'desk.unassign',deskId:'desk-1'});
  assert.equal(occupation(s,'room-1').vacant,1);
 });
 it('rejects stale revisions, duplicate floor numbers, invalid timezone and coordinates',()=>{
  const s=seedOfficeRegistry();
  assert.throws(()=>applySpaceCommand(s,{type:'room.rename',roomId:s.rooms[0].id,name:'Changed',expectedRevision:s.revision-1}),(e:unknown)=>e instanceof SpaceError&&e.code==='conflict');
  assert.throws(()=>run(s,{type:'floor.add',floor:{id:'duplicate',buildingId:'anum-hq',level:0,name:'Ground Again'}}),SpaceError);
  assert.throws(()=>run(s,{type:'building.add',building:{id:'bad',name:'Bad',timezone:'No/Such_Zone',latitude:10,longitude:20}}),SpaceError);
  assert.throws(()=>run(s,{type:'building.add',building:{id:'bad',name:'Bad',timezone:'Asia/Riyadh',latitude:91,longitude:20}}),SpaceError);
 });
 it('does not let meeting/reception rooms claim permanent desks',()=>{
  const s=seedOfficeRegistry(),r=s.rooms.find(x=>x.kind==='reception')!;
  assert.throws(()=>run(s,{type:'desk.add',desk:{id:'desk-extra',roomId:r.id,label:'A',agentId:null}}),(e:unknown)=>e instanceof SpaceError&&e.code==='restricted');
 });
 it('prevents a single agent occupying two desks',()=>{
  let s=seedOfficeRegistry();
  s=run(s,{type:'desk.assign',deskId:s.desks[0].id,agentId:'agent-a'});
  assert.throws(()=>run(s,{type:'desk.assign',deskId:s.desks[1].id,agentId:'agent-a'}),(e:unknown)=>e instanceof SpaceError&&e.code==='occupied');
 });
 it('does not delete a room containing workstations',()=>{
  const s=seedOfficeRegistry(),r=s.rooms.find(x=>x.kind==='office')!;
  assert.throws(()=>run(s,{type:'room.remove',roomId:r.id}),(e:unknown)=>e instanceof SpaceError&&e.code==='restricted');
 });
});
