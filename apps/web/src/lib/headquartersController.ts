/**
 * Scope-bound controller for ANUM MINE headquarters.
 *
 * This layer is kept separate from presentation so a design overhaul cannot
 * silently introduce an unscoped API request, fake a successful mutation, or
 * leak records across organization switches. All observations are immutable.
 */
import { projectHeadquarters, summarizeOfficeEvidence, type HeadquartersProjection,
  type AgentEvidence, type Registry, type Scope } from './headquartersProjection.ts';
import { initialCamera, focusRoom, type CameraState } from './headquartersSpatial.ts';
export type Status='uninitialized'|'loading'|'ready'|'refreshing'|'stale'|'error';
export type OfficeError = Readonly<{
 message:string; code:'NETWORK'|'SCOPE_CHANGED'|'NOT_AUTHORIZED'|'DATA_INVALID'|'UNKNOWN';
 at:string; canRetry:boolean;
}>;
export type Operation = 'load'|'refresh'|'switch-building'|'switch-floor'|'select-room'|'exit'|'change-scope';
export type Selection = Readonly<{buildingId:string|null;floorId:string|null;roomId:string|null}>;
export type OfficeState = Readonly<{
 scope:Scope|null; status:Status; projection:HeadquartersProjection|null;
 selected:Selection; camera:CameraState|null;
 updatedAt:string|null; error:OfficeError|null; busy:boolean;
 operation:Operation|null; revision:number;
}>;
export type DataSource = Readonly<{
 scope:()=>Scope;
 registry:()=>Promise<Registry>;
 agents:()=>Promise<readonly AgentEvidence[]>;
}>;
export type Subscriber=(state:OfficeState)=>void;
const EMPTY_SELECTION:Selection={buildingId:null,floorId:null,roomId:null};
const EMPTY_STATE:OfficeState={scope:null,status:'uninitialized',projection:null,
 selected:EMPTY_SELECTION,camera:null,updatedAt:null,error:null,busy:false,operation:null,revision:0};
const scopeKey=(s:Scope)=>s.tenantId+'\u0000'+s.workspaceId;
const scopesEqual=(a:Scope,b:Scope)=>a.tenantId===b.tenantId&&a.workspaceId===b.workspaceId;
function firstSelection(projection:HeadquartersProjection):Selection{
 const building=projection.buildings[0],floor=building?.floors[0],room=floor?.rooms[0];
 return {buildingId:building?.id??null,floorId:floor?.id??null,roomId:room?.id??null};
}
function validSelection(projection:HeadquartersProjection,selection:Selection):boolean{
 const b=projection.buildings.find(x=>x.id===selection.buildingId);
 if(!b)return !selection.buildingId&&projection.buildings.length===0;
 const f=b.floors.find(x=>x.id===selection.floorId);
 if(!f)return !selection.floorId&&b.floors.length===0;
 if(selection.roomId===null)return true;
 return f.rooms.some(x=>x.id===selection.roomId);
}
function chooseCamera(p:HeadquartersProjection,selection:Selection):CameraState|null{
 const b=p.buildings.find(x=>x.id===selection.buildingId),floor=b?.floors.find(x=>x.id===selection.floorId);
 if(!floor)return null;
 const camera=initialCamera(floor);
 const room=floor.rooms.find(x=>x.id===selection.roomId);
 return room?focusRoom(camera,room):camera;
}
function reason(error:unknown):OfficeError{
 const message=error instanceof Error?error.message:'Office data could not be loaded';
 const code:OfficeError['code']=/workspace changed|scope changed/i.test(message)?'SCOPE_CHANGED':
  /403|unauthori|forbidden/i.test(message)?'NOT_AUTHORIZED':
  /record|invalid|duplicate|schema/i.test(message)?'DATA_INVALID':
  /network|fetch|offline|timeout/i.test(message)?'NETWORK':'UNKNOWN';
 return {code,message,at:new Date().toISOString(),canRetry:code!=='NOT_AUTHORIZED'};
}
export class HeadquartersController {
 private state:OfficeState=EMPTY_STATE;
 private listeners=new Set<Subscriber>();
 private generation=0;
 private disposed=false;
 private interval:ReturnType<typeof setTimeout>|null=null;
 private readonly source:DataSource;
 private readonly freshnessMs:number;
 constructor(source:DataSource,freshnessMs=30_000){
  this.source=source;this.freshnessMs=freshnessMs;
 }
 snapshot():OfficeState{return this.state}
 subscribe(subscriber:Subscriber):()=>void{
  if(this.disposed)throw new Error('Controller has been disposed');
  this.listeners.add(subscriber);subscriber(this.state);
  return ()=>this.listeners.delete(subscriber);
 }
 private publish(change:Partial<OfficeState>):void{
  this.state={...this.state,...change,revision:this.state.revision+1};
  for(const listener of [...this.listeners])listener(this.state);
 }
 async initialize():Promise<void>{
  if(this.disposed)throw new Error('Cannot initialize a disposed controller');
  await this.reload('load');
 }
 async refresh():Promise<void>{await this.reload('refresh')}
 async reload(operation:'load'|'refresh'):Promise<void>{
  if(this.disposed)return;
  const generation=++this.generation;
  const scope=this.source.scope();
  if(!scope.tenantId||!scope.workspaceId){
   this.clearForScope(scope);return;
  }
  const crossing=!this.state.scope||!scopesEqual(scope,this.state.scope);
  if(crossing){
   // Never retain a prior tenant's projection during a new tenant's load.
   this.publish({scope:{...scope},status:'loading',projection:null,
    selected:EMPTY_SELECTION,camera:null,updatedAt:null,error:null,busy:true,operation});
  }else{
   this.publish({status:this.state.projection?'refreshing':'loading',error:null,busy:true,operation});
  }
  try{
   const [registry,agents]=await Promise.all([this.source.registry(),this.source.agents()]);
   if(generation!==this.generation||this.disposed)return;
   const current=this.source.scope();
   if(!scopesEqual(current,scope)){
    this.clearForScope(current);
    await this.reload('load');return;
   }
   const observedAt=new Date();
   const projection=projectHeadquarters(scope,registry,agents,observedAt);
   const old=this.state.selected;
   const selection=!crossing&&validSelection(projection,old)?old:firstSelection(projection);
   const camera=chooseCamera(projection,selection);
   this.publish({scope,projection,selected:selection,camera,status:'ready',
    updatedAt:observedAt.toISOString(),error:null,busy:false,operation:null});
  }catch(error){
   if(generation!==this.generation||this.disposed)return;
   const failure=reason(error);
   this.publish({status:crossing?'error':this.state.projection?'stale':'error',
    busy:false,operation:null,error:failure});
  }
 }
 private clearForScope(scope:Scope):void{
  this.generation++;
  this.publish({scope:{...scope},status:'error',projection:null,selected:EMPTY_SELECTION,
   camera:null,updatedAt:null,busy:false,operation:null,
   error:{code:'SCOPE_CHANGED',message:'Select a valid workspace before opening the office.',at:new Date().toISOString(),canRetry:false}});
 }
 selectBuilding(buildingId:string):void{
  const p=this.state.projection;if(!p)return;
  const building=p.buildings.find(x=>x.id===buildingId);
  if(!building)throw new Error('The selected building is not in your workspace');
  const floor=building.floors[0];const room=floor?.rooms[0];
  const selected={buildingId,floorId:floor?.id??null,roomId:room?.id??null};
  this.publish({selected,camera:chooseCamera(p,selected),operation:null});
 }
 selectFloor(floorId:string):void{
  const p=this.state.projection;if(!p||!this.state.selected.buildingId)return;
  const building=p.buildings.find(x=>x.id===this.state.selected.buildingId);
  const floor=building?.floors.find(x=>x.id===floorId);
  if(!floor)throw new Error('Floor is not present in selected building');
  const selected={...this.state.selected,floorId,roomId:floor.rooms[0]?.id??null};
  this.publish({selected,camera:chooseCamera(p,selected),operation:null});
 }
 selectRoom(roomId:string):void{
  const p=this.state.projection;if(!p)return;
  const building=p.buildings.find(x=>x.id===this.state.selected.buildingId);
  const floor=building?.floors.find(x=>x.id===this.state.selected.floorId);
  if(!floor?.rooms.some(x=>x.id===roomId))throw new Error('Room is not in selected floor');
  const selected={...this.state.selected,roomId};
  this.publish({selected,camera:chooseCamera(p,selected),operation:null});
 }
 setCamera(camera:CameraState):void{if(this.state.camera)this.publish({camera})}
 scheduleRefresh():void{
  if(this.interval)clearTimeout(this.interval);
  if(this.disposed)return;
  this.interval=setTimeout(()=>{
   this.interval=null;
   if(!this.disposed){void this.refresh();this.scheduleRefresh()}
  },this.freshnessMs);
 }
 stopRefresh():void{if(this.interval){clearTimeout(this.interval);this.interval=null}}
 summarize():ReturnType<typeof summarizeOfficeEvidence>|null{
  return this.state.projection?summarizeOfficeEvidence(this.state.projection):null;
 }
 dispose():void{
  this.disposed=true;this.generation++;this.stopRefresh();this.listeners.clear();
  this.state=EMPTY_STATE;
 }
}
/** A strictly scoped loader for integration with the existing ANUM API adapter. */
export function adaptOfficeRegistry(records:Readonly<{
 buildings:readonly {id:string;name:string;timezone:string;revision:number}[];
 floors:readonly {id:string;name:string;level:number;building_id:string;revision:number}[];
 rooms:readonly {id:string;name:string;floor_id:string;kind:string;capacity:number;revision:number}[];
 desks:readonly {id:string;room_id:string;label:string;agent_id:string|null;revision:number}[];
}>):Registry{
 const kinds=new Set(['office','meeting','reception','workshop','warehouse','lounge']);
 for(const room of records.rooms){if(!kinds.has(room.kind))throw new Error('Unknown office room kind in server response')}
 // The adapter's office API has already validated tenant and workspace fields.
 return records as Registry;
}
export function scopeFingerprint(scope:Scope):string{return scopeKey(scope)}
