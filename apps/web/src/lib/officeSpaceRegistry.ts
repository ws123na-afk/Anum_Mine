/**
 * ANUM MINE space registry domain.
 *
 * Safe, deterministic validation of buildings, floors, rooms, desk occupancy and
 * revisions. Transport/persistence belongs to a permissioned, workspace-scoped API:
 * the web preview never pretends an in-memory change is persisted.
 */
export type SpaceKind='office'|'meeting'|'reception'|'workshop'|'warehouse'|'lounge';
export interface Building {id:string;name:string;timezone:string;latitude:number;longitude:number}
export interface Floor {id:string;buildingId:string;level:number;name:string}
export interface Room {id:string;floorId:string;name:string;kind:SpaceKind;capacity:number}
export interface Desk {id:string;roomId:string;label:string;agentId:string|null}
export interface SpaceRegistry {
 revision:number;buildings:readonly Building[];floors:readonly Floor[];rooms:readonly Room[];desks:readonly Desk[];
}
export type SpaceCommand =
 | {type:'building.add';building:Building;expectedRevision:number}
 | {type:'floor.add';floor:Floor;expectedRevision:number}
 | {type:'room.add';room:Room;expectedRevision:number}
 | {type:'desk.add';desk:Desk;expectedRevision:number}
 | {type:'desk.assign';deskId:string;agentId:string;expectedRevision:number}
 | {type:'desk.unassign';deskId:string;expectedRevision:number}
 | {type:'room.rename';roomId:string;name:string;expectedRevision:number}
 | {type:'room.remove';roomId:string;expectedRevision:number};
export class SpaceError extends Error {
 constructor(readonly code:'conflict'|'invalid'|'not_found'|'occupied'|'restricted',message:string){super(message);this.name='SpaceError'}
}
export const EMPTY_REGISTRY:SpaceRegistry={revision:0,buildings:[],floors:[],rooms:[],desks:[]};
const name=(value:string,field:string)=>{
 const clean=value.trim().replace(/\s+/g,' ');
 if(!clean||clean.length>100)throw new SpaceError('invalid',field+' must contain 1 to 100 characters');
 return clean;
};
const ident=(value:string)=>{
 if(!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$/.test(value))throw new SpaceError('invalid','IDs must be stable safe identifiers');
 return value;
};
export function applySpaceCommand(state:SpaceRegistry,command:SpaceCommand):SpaceRegistry {
 if(command.expectedRevision!==state.revision)throw new SpaceError('conflict','This registry has changed. Reload before editing.');
 let next:SpaceRegistry;
 switch(command.type){
 case 'building.add':{
  const b={...command.building,id:ident(command.building.id),name:name(command.building.name,'Building name')};
  if(state.buildings.some(x=>x.id===b.id))throw new SpaceError('conflict','Duplicate building ID');
  if(Math.abs(b.latitude)>90||Math.abs(b.longitude)>180||!Number.isFinite(b.latitude)||!Number.isFinite(b.longitude))throw new SpaceError('invalid','Invalid coordinates');
  try{new Intl.DateTimeFormat('en',{timeZone:b.timezone})}catch{throw new SpaceError('invalid','Invalid IANA time zone')}
  next={...state,buildings:[...state.buildings,b]};break;
 }
 case 'floor.add':{
  const f={...command.floor,id:ident(command.floor.id),name:name(command.floor.name,'Floor name')};
  if(!state.buildings.some(x=>x.id===f.buildingId))throw new SpaceError('not_found','Building not found');
  if(!Number.isInteger(f.level)||Math.abs(f.level)>120)throw new SpaceError('invalid','Invalid floor level');
  if(state.floors.some(x=>x.id===f.id||(x.buildingId===f.buildingId&&x.level===f.level)))throw new SpaceError('conflict','Floor already exists');
  next={...state,floors:[...state.floors,f]};break;
 }
 case 'room.add':{
  const r={...command.room,id:ident(command.room.id),name:name(command.room.name,'Room name')};
  if(!state.floors.some(x=>x.id===r.floorId))throw new SpaceError('not_found','Floor not found');
  if(!Number.isInteger(r.capacity)||r.capacity<1||r.capacity>5000)throw new SpaceError('invalid','Invalid room capacity');
  if(state.rooms.some(x=>x.id===r.id||x.floorId===r.floorId&&x.name.toLowerCase()===r.name.toLowerCase()))throw new SpaceError('conflict','Room already exists');
  next={...state,rooms:[...state.rooms,r]};break;
 }
 case 'desk.add':{
  const d={...command.desk,id:ident(command.desk.id),label:name(command.desk.label,'Desk label')};
  const r=state.rooms.find(x=>x.id===d.roomId);
  if(!r)throw new SpaceError('not_found','Room not found');
  if(!['office','workshop','warehouse'].includes(r.kind))throw new SpaceError('restricted','This room type cannot contain permanent workstations');
  if(d.agentId!==null)throw new SpaceError('invalid','New desks must begin vacant; assign separately');
  const count=state.desks.filter(x=>x.roomId===d.roomId).length;
  if(count>=r.capacity)throw new SpaceError('restricted','Room capacity reached');
  if(state.desks.some(x=>x.id===d.id||(x.roomId===d.roomId&&x.label.toLowerCase()===d.label.toLowerCase())))throw new SpaceError('conflict','Desk already exists');
  next={...state,desks:[...state.desks,d]};break;
 }
 case 'desk.assign':{
  const target=state.desks.find(x=>x.id===command.deskId);
  if(!target)throw new SpaceError('not_found','Desk not found');
  if(target.agentId)throw new SpaceError('occupied','Desk is already occupied');
  const agentId=ident(command.agentId);
  if(state.desks.some(x=>x.agentId===agentId))throw new SpaceError('occupied','Agent already has a desk');
  next={...state,desks:state.desks.map(d=>d.id===target.id?{...d,agentId}:d)};break;
 }
 case 'desk.unassign':{
  if(!state.desks.some(x=>x.id===command.deskId))throw new SpaceError('not_found','Desk not found');
  next={...state,desks:state.desks.map(d=>d.id===command.deskId?{...d,agentId:null}:d)};break;
 }
 case 'room.rename':{
  const room=state.rooms.find(x=>x.id===command.roomId);
  if(!room)throw new SpaceError('not_found','Room not found');
  const newName=name(command.name,'Room name');
  if(state.rooms.some(x=>x.floorId===room.floorId&&x.id!==room.id&&x.name.toLowerCase()===newName.toLowerCase()))throw new SpaceError('conflict','Room name already in use on this floor');
  next={...state,rooms:state.rooms.map(x=>x.id===room.id?{...x,name:newName}:x)};break;
 }
 case 'room.remove':{
  if(!state.rooms.some(x=>x.id===command.roomId))throw new SpaceError('not_found','Room not found');
  if(state.desks.some(x=>x.roomId===command.roomId))throw new SpaceError('restricted','Move or remove desks before deleting this room');
  next={...state,rooms:state.rooms.filter(x=>x.id!==command.roomId)};break;
 }
 default:throw new SpaceError('invalid','Unsupported command');
 }
 return {...next,revision:state.revision+1};
}
export function roomsOnFloor(state:SpaceRegistry,floorId:string):Room[] {
 return state.rooms.filter(x=>x.floorId===floorId).sort((a,b)=>a.name.localeCompare(b.name));
}
export function occupation(state:SpaceRegistry,roomId:string){
 const desks=state.desks.filter(x=>x.roomId===roomId);
 return {total:desks.length,occupied:desks.filter(x=>x.agentId!==null).length,vacant:desks.filter(x=>x.agentId===null).length};
}
export function seedOfficeRegistry():SpaceRegistry {
 let state=EMPTY_REGISTRY;
 const apply=(value:Record<string,unknown>)=>{state=applySpaceCommand(state,{...value,expectedRevision:state.revision} as SpaceCommand)};
 apply({type:'building.add',building:{id:'anum-hq',name:'ANUM Headquarters',timezone:'Asia/Riyadh',latitude:24.7136,longitude:46.6753}});
 apply({type:'building.add',building:{id:'tyre-pulse',name:'Tyre Pulse HQ',timezone:'Asia/Riyadh',latitude:24.7136,longitude:46.6753}});
 apply({type:'building.add',building:{id:'anum-commerce',name:'ANUM360 Commerce',timezone:'Asia/Riyadh',latitude:24.7136,longitude:46.6753}});
 apply({type:'building.add',building:{id:'personal',name:'Personal Office',timezone:'Asia/Riyadh',latitude:24.7136,longitude:46.6753}});
 const floors=[['anum-hq',0,'Welcome'],['anum-hq',1,'Departments'],['anum-hq',2,'Executive'],['tyre-pulse',0,'Reception & Inspection'],['tyre-pulse',1,'Planning'],['anum-commerce',0,'Commerce Operations'],['personal',0,'Personal Workspace']] as const;
 for(const [id,level,floorName] of floors)apply({type:'floor.add',floor:{id:id+'-'+level,buildingId:id,level,name:floorName}});
 const rooms=[['anum-hq-0','Reception','reception'],['anum-hq-0','Command Center','office'],['anum-hq-1','Finance','office'],['anum-hq-1','Customer Experience','office'],['anum-hq-2','Executive Office','office'],['tyre-pulse-0','Inspection Control','office'],['tyre-pulse-1','Tyre Analytics','office'],['anum-commerce-0','Order Processing','office'],['personal-0','Planning Office','office']] as const;
 let index=0;
 for(const [floorId,roomName,kind] of rooms){const id='room-'+(++index);apply({type:'room.add',room:{id,floorId,name:roomName,kind,capacity:8}});if(kind==='office'){for(let j=0;j<3;j++)apply({type:'desk.add',desk:{id:id+'-desk-'+(j+1),roomId:id,label:'Workstation '+(j+1),agentId:null}})}}
 return state;
}
