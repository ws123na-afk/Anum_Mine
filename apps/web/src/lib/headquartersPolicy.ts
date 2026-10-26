/** Workspace authorization and navigation policy for the headquarters shell. */
import type { Scope, RoomGeometry } from './headquartersProjection.ts';
export type OfficeRoute = 'command'|'meetings'|'tasks'|'tyre'|'commerce'|'team'|'calendar';
export type Permission = 'task:read'|'workspace:manage'|'agent:read'|'meeting:manage';
export type Actor = Readonly<{id:string;scope:Scope;permissions:ReadonlySet<Permission>;active:boolean}>;
export type PolicyOutcome = Readonly<{allowed:boolean;reason:string;route:OfficeRoute|null}>;
const ROUTE_BY_KIND:Readonly<Record<RoomGeometry['kind'],OfficeRoute>>={
 office:'tasks',meeting:'meetings',reception:'team',workshop:'tyre',
 warehouse:'commerce',lounge:'calendar',command:'command',
};
const MANAGE_KINDS=new Set<RoomGeometry['kind']>(['office','workshop','warehouse','command']);
export function routeForRoom(room:RoomGeometry):OfficeRoute{return ROUTE_BY_KIND[room.kind]}
export function mayEnterRoom(actor:Actor,room:RoomGeometry,expectedScope:Scope):PolicyOutcome{
 if(!actor.active)return {allowed:false,reason:'Workspace membership is inactive',route:null};
 if(actor.scope.tenantId!==expectedScope.tenantId||actor.scope.workspaceId!==expectedScope.workspaceId){
  return {allowed:false,reason:'Room request does not match the signed-in workspace',route:null};
 }
 if(!room.buildingId||!room.floorId)return {allowed:false,reason:'Room is not linked to a configured office',route:null};
 if(!actor.permissions.has('task:read'))return {allowed:false,reason:'Permission to view the workspace is required',route:null};
 return {allowed:true,reason:'Authenticated workspace navigation',route:routeForRoom(room)};
}
export function mayChangeDesk(actor:Actor,room:RoomGeometry,expectedScope:Scope):PolicyOutcome{
 const entry=mayEnterRoom(actor,room,expectedScope);
 if(!entry.allowed)return entry;
 if(!MANAGE_KINDS.has(room.kind))return {allowed:false,reason:'Permanent desk assignments are not supported in this room',route:null};
 if(!actor.permissions.has('workspace:manage'))return {allowed:false,reason:'Workspace manager permission is required',route:null};
 return {allowed:true,reason:'Desk change permitted subject to server revision and occupancy checks',route:'tasks'};
}
export function canDisplayAgentEvidence(actor:Actor,expectedScope:Scope,source:'authenticated-agent-snapshot'|'unknown',stale:boolean):boolean{
 return actor.active&&actor.scope.tenantId===expectedScope.tenantId&&
  actor.scope.workspaceId===expectedScope.workspaceId&&actor.permissions.has('agent:read')&&
  source==='authenticated-agent-snapshot'&&!stale;
}
export function safeModuleHash(route:OfficeRoute):string{
 const allowed=new Set<OfficeRoute>(Object.values(ROUTE_BY_KIND));
 if(!allowed.has(route))throw new Error('Unsupported headquarters module route');
 return '#'+route;
}
export function classifyRoomActivity(room:RoomGeometry,assigned:number,running:number):'unassigned'|'configured'|'task-evidence'|'over-capacity'{
 if(assigned>room.capacity)return 'over-capacity';
 if(assigned===0)return 'unassigned';
 if(running>0)return 'task-evidence';
 return 'configured';
}
export function officeCapabilitySummary(actor:Actor):Readonly<{
 canView:boolean;canManage:boolean;canSeeAgentEvidence:boolean;canManageMeetings:boolean;
}>{
 return {canView:actor.active&&actor.permissions.has('task:read'),
  canManage:actor.active&&actor.permissions.has('workspace:manage'),
  canSeeAgentEvidence:actor.active&&actor.permissions.has('agent:read'),
  canManageMeetings:actor.active&&actor.permissions.has('meeting:manage')};
}
