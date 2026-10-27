/**
 * Workspace-backed ANUM MINE scene projection.
 *
 * Scene entities are derived from authenticated server registry records rather
 * than the old SITE_ROOMS illustrative constants. This module has no effects,
 * network requests, DOM references or mutable process-wide state. Its output
 * may safely be recomputed when server snapshots change or when a tenant
 * switches workspace. No fabricated agent status is ever emitted.
 */
export type Scope = Readonly<{ tenantId:string; workspaceId:string }>;
export type Vec2 = Readonly<{x:number;y:number}>;
export type Vec3 = Readonly<{x:number;y:number;z:number}>;
export type Building = Readonly<{id:string;name:string;name_ar?:string|null;timezone:string;revision:number;shift_day_start?:string|null;shift_night_start?:string|null}>;
export type Floor = Readonly<{id:string;building_id:string;level:number;name:string;name_ar?:string|null;revision:number}>;
export type RoomKind = 'office'|'meeting'|'reception'|'workshop'|'warehouse'|'lounge'|'command';
export type Room = Readonly<{id:string;floor_id:string;name:string;name_ar?:string|null;kind:RoomKind;capacity:number;revision:number;scene_x?:number|null;scene_z?:number|null;scene_width?:number|null;scene_depth?:number|null;team_department?:string|null;door_side?:string|null;rotation?:number|null}>;
export type DeskFacing='north'|'south'|'east'|'west';
export type Desk = Readonly<{id:string;room_id:string;label:string;agent_id:string|null;revision:number;pos_x?:number|null;pos_z?:number|null;shift?:'day'|'night'|null;seat_order?:number|null;facing?:DeskFacing|null;seat_kind?:'team'|'manager'|null;table_group?:string|null}>;
export type Registry = Readonly<{buildings:readonly Building[];floors:readonly Floor[];rooms:readonly Room[];desks:readonly Desk[]}>;
export type AgentEvidence = Readonly<{
 id:string; name:string; status:'running'|'waiting'|'idle'|'unknown';
 observedAt:string; taskIds:readonly string[]; source:'authenticated-agent-snapshot';
}>;
export type DoorSide='north'|'south'|'east'|'west';
export type RoomGeometry = Readonly<{
 id:string; floorId:string; buildingId:string; label:string; /** The Arabic name, when the room has one (display only; `label` stays the English name). */ labelAr?:string|null; kind:RoomKind;
 x:number; z:number; width:number; depth:number; height:number;
 capacity:number; revision:number; deskIds:readonly string[];
 /** The department stored on the room by the builder or the starter campus; null when none was set. */
 teamDepartment?:string|null;
 evidence:'registry'|'layout-draft';
 /** The wall the door is in (the builder's `door_side`); absent means the south wall. The interior is dressed to face it. */
 doorSide?:DoorSide;
 /** Tables of the shift that is not shown whose place nobody uses in the shown shift: drawn as empty seats (furniture, never a desk or a person). */
 emptySeats?:readonly Readonly<{x:number;z:number;facing:DeskFacing|null;group:string|null}>[];
}>;
export type SceneDesk = Readonly<{
 id:string; roomId:string; label:string; position:Vec3; agentId:string|null;
 agent:AgentEvidence|null; assignmentVisible:boolean;
 /** Seat fields from the registry (docs/ANUM_MINE_GREEN_CONCRETE.md): the shift, the place along the table, the wall the person looks at, the manager seat and the table group. */
 shift?:'day'|'night'|null; seatOrder?:number|null; facing?:DeskFacing|null; seatKind?:'team'|'manager'|null; tableGroup?:string|null;
 /** Set by the shift view (lib/hqShifts.ts): what this desk says about its shift now. */
 shiftInfo?:import('./hqShifts.ts').DeskShiftInfo;
}>;
export type SceneFloor = Readonly<{
 id:string; name:string; /** The Arabic name, when the floor has one. */ nameAr?:string|null; level:number; rooms:readonly RoomGeometry[];
 desks:readonly SceneDesk[]; bounds:Readonly<{width:number;depth:number}>;
}>;
export type SceneBuilding = Readonly<{
 id:string; name:string; /** The Arabic name, when the building has one. */ nameAr?:string|null; timezone:string; floors:readonly SceneFloor[];
 /** Shift hours stored on the building ("HH:MM"); null means the unconfirmed defaults. */
 shiftDayStart?:string|null; shiftNightStart?:string|null;
 /** Set by the shift view for a building with shift desks. */
 shifts?:import('./hqShifts.ts').ShiftSnapshot;
}>;
export type ProjectionIssue = Readonly<{
 code:'DUPLICATE_ID'|'ORPHAN_FLOOR'|'ORPHAN_ROOM'|'ORPHAN_DESK'|'INVALID_DIMENSIONS'|'CAPACITY_OVERFLOW'|'ASSIGNMENT_CONFLICT'|'STALE_EVIDENCE';
 recordId:string; message:string;
}>;
export type HeadquartersProjection = Readonly<{
 scope:Scope; buildings:readonly SceneBuilding[]; issues:readonly ProjectionIssue[];
 generatedAt:string; source:'authenticated-office-registry';
}>;
const MAX_ROOMS_PER_FLOOR=160;
const ROOM_WIDTH=5.8;
const ROOM_DEPTH=4.2;
const AISLE=2;
const GRID_COLUMNS=5;
const EVIDENCE_MAX_AGE_MS=90_000;
function byName<T extends {name:string;id:string}>(a:T,b:T):number{
 return a.name.localeCompare(b.name)||a.id.localeCompare(b.id);
}
function idCount(records:readonly {id:string}[],issues:ProjectionIssue[]):void{
 const ids=new Set<string>();
 for(const record of records){
  if(ids.has(record.id))issues.push({code:'DUPLICATE_ID',recordId:record.id,message:'Duplicate scoped resource identifier'});
  ids.add(record.id);
 }
}
export function finiteBound(value:number,min:number,max:number):number{
 if(!Number.isFinite(value))throw new TypeError('Scene geometry requires finite numbers');
 return Math.min(max,Math.max(min,value));
}
export function layoutRoomsOnFloor(rooms:readonly Room[],floor:Floor,buildingId:string,issues:ProjectionIssue[]):RoomGeometry[]{
 const sorted=[...rooms].sort(byName);
 if(sorted.length>MAX_ROOMS_PER_FLOOR){
  issues.push({code:'INVALID_DIMENSIONS',recordId:floor.id,message:'Floor exceeds supported rendered room limit'});
 }
 return sorted.slice(0,MAX_ROOMS_PER_FLOOR).map((room,index)=>{
  const col=index%GRID_COLUMNS,row=Math.floor(index/GRID_COLUMNS);
  const width=ROOM_WIDTH*(room.kind==='meeting'?1.22:room.kind==='warehouse'?1.35:1);
  const depth=ROOM_DEPTH*(room.kind==='workshop'?1.3:1);
  const x=col*(ROOM_WIDTH*1.4+AISLE)-12;
  const z=row*(ROOM_DEPTH*1.7+AISLE)-5;
  return {id:room.id,floorId:floor.id,buildingId,label:room.name,labelAr:room.name_ar??null,kind:room.kind,
   x:room.scene_x??x,z:room.scene_z??z,width:room.scene_width??width,depth:room.scene_depth??depth,height:3.2,capacity:room.capacity,revision:room.revision,deskIds:[],teamDepartment:room.team_department??null,evidence:'registry',
   ...(room.door_side==='north'||room.door_side==='south'||room.door_side==='east'||room.door_side==='west'?{doorSide:room.door_side as DoorSide}:{})};
 });
}
function placeDesks(room:RoomGeometry,assigned:readonly Desk[],visibleAgents:ReadonlyMap<string,AgentEvidence>,nowMs:number,issues:ProjectionIssue[]):SceneDesk[]{
 const count=assigned.length;
 if(count>room.capacity){
  issues.push({code:'CAPACITY_OVERFLOW',recordId:room.id,message:'More desks than room capacity'});
 }
 const cols=Math.max(1,Math.min(5,Math.ceil(Math.sqrt(count))));
 const rows=Math.max(1,Math.ceil(count/cols));
 return assigned.map((desk,index)=>{
  const column=index%cols,row=Math.floor(index/cols);
  // A seat (a desk with a shift, a manager kind or a table group) keeps the place the owner saved; any other desk is on the grid.
  const seat=desk.pos_x!=null&&desk.pos_z!=null&&(desk.shift!=null||desk.seat_kind!=null||desk.table_group!=null);
  const x=seat?room.x+(desk.pos_x as number):room.x+room.width*(column+1)/(cols+1);
  const z=seat?room.z+(desk.pos_z as number):room.z+room.depth*(row+1)/(rows+1);
  const evidence=desk.agent_id?visibleAgents.get(desk.agent_id)??null:null;
  const observedAt=evidence?Date.parse(evidence.observedAt):NaN;
  const fresh=evidence&&Number.isFinite(observedAt)&&Math.abs(nowMs-observedAt)<=EVIDENCE_MAX_AGE_MS;
  if(evidence&&!fresh){
   issues.push({code:'STALE_EVIDENCE',recordId:desk.id,message:'Agent evidence is stale and should not be displayed as live'});
  }
  return {id:desk.id,roomId:room.id,label:desk.label,position:{x,y:.78,z},
   agentId:desk.agent_id,agent:fresh?evidence:null,assignmentVisible:desk.agent_id!==null,
   ...(seat||desk.shift!=null||desk.seat_kind!=null?{shift:desk.shift??null,seatOrder:desk.seat_order??null,facing:desk.facing??null,seatKind:desk.seat_kind??null,tableGroup:desk.table_group??null}:{})};
 });
}
/** Throws on corrupted scope input rather than accidentally mixing tenants. */
export function projectHeadquarters(scope:Scope,registry:Registry,evidence:readonly AgentEvidence[],observedAt=new Date()):HeadquartersProjection{
 if(!scope.tenantId.trim()||!scope.workspaceId.trim())throw new Error('Headquarters scope is required');
 if(!Number.isFinite(observedAt.getTime()))throw new Error('Invalid snapshot observation time');
 const issues:ProjectionIssue[]=[];
 for(const records of [registry.buildings,registry.floors,registry.rooms,registry.desks])idCount(records,issues);
 const buildingIds=new Set(registry.buildings.map(v=>v.id));
 const floorIds=new Set(registry.floors.map(v=>v.id));
 const roomIds=new Set(registry.rooms.map(v=>v.id));
 registry.floors.filter(f=>!buildingIds.has(f.building_id)).forEach(f=>issues.push({code:'ORPHAN_FLOOR',recordId:f.id,message:'Floor has no visible building'}));
 registry.rooms.filter(r=>!floorIds.has(r.floor_id)).forEach(r=>issues.push({code:'ORPHAN_ROOM',recordId:r.id,message:'Room has no visible floor'}));
 registry.desks.filter(d=>!roomIds.has(d.room_id)).forEach(d=>issues.push({code:'ORPHAN_DESK',recordId:d.id,message:'Desk has no visible room'}));
 const seenAssignments=new Map<string,string>();
 for(const desk of registry.desks){
  if(!desk.agent_id)continue;
  const old=seenAssignments.get(desk.agent_id);
  if(old)issues.push({code:'ASSIGNMENT_CONFLICT',recordId:desk.id,message:'An agent was assigned more than one desk'});
  else seenAssignments.set(desk.agent_id,desk.id);
 }
 const agentMap=new Map(evidence.map(agent=>[agent.id,agent]));
 const buildings=[...registry.buildings].sort(byName).map(building=>{
  const floors=registry.floors.filter(f=>f.building_id===building.id).sort((a,b)=>a.level-b.level||byName(a,b)).map(floor=>{
   const floorRooms=registry.rooms.filter(r=>r.floor_id===floor.id);
   const geometry=layoutRoomsOnFloor(floorRooms,floor,building.id,issues);
   const augmented=geometry.map(room=>({...room,
    deskIds:registry.desks.filter(d=>d.room_id===room.id).map(d=>d.id)}));
   const desks=augmented.flatMap(room=>placeDesks(room,registry.desks.filter(d=>d.room_id===room.id),agentMap,observedAt.getTime(),issues));
   const width=Math.max(20,...augmented.map(r=>r.x+r.width+10));
   const depth=Math.max(16,...augmented.map(r=>r.z+r.depth+8));
   return {id:floor.id,name:floor.name,nameAr:floor.name_ar??null,level:floor.level,rooms:augmented,desks,bounds:{width,depth}};
  });
  return {id:building.id,name:building.name,nameAr:building.name_ar??null,timezone:building.timezone,floors,...(building.shift_day_start||building.shift_night_start?{shiftDayStart:building.shift_day_start??null,shiftNightStart:building.shift_night_start??null}:{})};
 });
 return {scope:{...scope},buildings,issues,generatedAt:observedAt.toISOString(),source:'authenticated-office-registry'};
}
/** Generated geometry is transient; the canonical registry still owns room IDs. */
export function findSceneRoom(projection:HeadquartersProjection,buildingId:string,floorId:string,roomId:string):RoomGeometry|null{
 return projection.buildings.find(b=>b.id===buildingId)?.floors.find(f=>f.id===floorId)?.rooms.find(r=>r.id===roomId)??null;
}
export function summarizeOfficeEvidence(projection:HeadquartersProjection):Readonly<{buildings:number;floors:number;rooms:number;desks:number;assigned:number;fresh:number;issues:number}>{
 const floors=projection.buildings.flatMap(b=>b.floors);
 const desks=floors.flatMap(f=>f.desks);
 return {buildings:projection.buildings.length,floors:floors.length,rooms:floors.reduce((count,f)=>count+f.rooms.length,0),
  desks:desks.length,assigned:desks.filter(d=>d.assignmentVisible).length,fresh:desks.filter(d=>d.agent!==null).length,issues:projection.issues.length};
}
