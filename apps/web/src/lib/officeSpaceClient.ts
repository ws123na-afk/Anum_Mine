/** Authenticated ANUM MINE office-registry API adapter.
 * Transport is dependency injected so the contract can be tested under Node.
 * This module never supplies tenant/workspace IDs in request bodies.
 */
import type {OfficeRotation,OfficeDoorSide,OfficeRoomColor,OfficeEquipmentItem,OfficeTeamDepartment} from '@anum/contracts';
export type OfficeKind = 'buildings'|'floors'|'rooms'|'desks';
export interface OfficeRecord {id:string;revision:number;tenant_id:string;workspace_id:string;created_at:string;updated_at:string}
export interface OfficeBuilding extends OfficeRecord {name:string;name_ar?:string|null;timezone:string;latitude:number|null;longitude:number|null;shift_day_start?:string|null;shift_night_start?:string|null;
 /** The sign (docs/ANUM_MINE_HQ_LOGO_SIGNS.md): style and brand colour the owner chose, and the SHA-256 of the uploaded logo (null: no logo, the sign is lettered). */
 sign_style?:'plain'|'plaque'|'illuminated'|'banner'|null;brand_color?:string|null;logo_sha256?:string|null}
/** `width_m` and `depth_m` are the floor bounds in metres; null on floors made before the builder (unbounded). */
export interface OfficeFloor extends OfficeRecord {building_id:string;level:number;name:string;name_ar?:string|null;width_m?:number|null;depth_m?:number|null}
export interface OfficeRoom extends OfficeRecord {floor_id:string;name:string;kind:'office'|'meeting'|'reception'|'workshop'|'warehouse'|'lounge'|'command';capacity:number;scene_x?:number|null;scene_z?:number|null;scene_width?:number|null;scene_depth?:number|null;
 rotation?:OfficeRotation;door_side?:OfficeDoorSide;description?:string;notes?:string;color?:OfficeRoomColor|null;name_ar?:string|null;
 equipment?:OfficeEquipmentItem[];team_department?:OfficeTeamDepartment|null}
/** `pos_x` and `pos_z` are metres from the room's north west corner; null while the desk is not placed. */
export interface OfficeDesk extends OfficeRecord {room_id:string;label:string;agent_id:string|null;pos_x?:number|null;pos_z?:number|null;shift?:'day'|'night'|null;seat_order?:number|null;facing?:'north'|'south'|'east'|'west'|null;seat_kind?:'team'|'manager'|null;table_group?:string|null;work_start?:string|null;work_end?:string|null}
export interface OfficeRegistryData {buildings:OfficeBuilding[];floors:OfficeFloor[];rooms:OfficeRoom[];desks:OfficeDesk[]}
export interface OfficePage<T> {items:T[];next_cursor:string|null}
export type OfficeRequester = <T>(path:string,init:RequestInit)=>Promise<T>;
export type OfficeVoidRequester = (path:string,init:RequestInit)=>Promise<void>;
export class OfficeApiError extends Error {constructor(message:string){super(message);this.name='OfficeApiError'}}
const ROOT='/api/v1/office-spaces';
const assertId=(id:string)=>{if(!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$/.test(id))throw new OfficeApiError('Invalid office record identifier');return id};
export function officeResourcePath(kind:OfficeKind,id?:string):string {
 return ROOT+'/'+kind+(id?'/'+encodeURIComponent(assertId(id)):'');
}
export async function listOfficeRecords<T extends OfficeRecord>(
 request:OfficeRequester,kind:OfficeKind,scope:()=>{tenantId:string;workspaceId:string},parentId?:string
):Promise<T[]>{
 const start=scope(),result:T[]=[];
 let cursor:string|null=null;
 const ids=new Set<string>();
 for(let page=0;page<100;page++){
  const params=new URLSearchParams({limit:'100'});
  if(cursor)params.set('cursor',cursor);
  if(parentId)params.set('parent_id',assertId(parentId));
  const response=await request<OfficePage<T>>(officeResourcePath(kind)+'?'+params.toString(),{method:'GET'});
  const now=scope();
  if(now.tenantId!==start.tenantId||now.workspaceId!==start.workspaceId)throw new OfficeApiError('Workspace changed during office sync. Reload before continuing.');
  if(!response||!Array.isArray(response.items))throw new OfficeApiError('Office registry returned an invalid page');
  for(const item of response.items){
   if(item.tenant_id!==start.tenantId||item.workspace_id!==start.workspaceId)throw new OfficeApiError('Office registry returned data from another workspace');
   if(ids.has(item.id))throw new OfficeApiError('Office registry returned a repeated record');
   ids.add(item.id);result.push(item);
  }
  if(!response.next_cursor)return result;
  if(cursor===response.next_cursor)throw new OfficeApiError('Office cursor did not advance');
  cursor=response.next_cursor;
 }
 throw new OfficeApiError('Office registry exceeded the 10,000-record safety limit');
}
export function createOfficeSpaceClient(request:OfficeRequester,remove:OfficeVoidRequester,scope:()=>{tenantId:string;workspaceId:string}){
 const load=async():Promise<OfficeRegistryData>=>{
  // Each request remains within the same authenticated workspace.
  const started=scope();
  const [buildings,floors,rooms,desks]=await Promise.all([
   listOfficeRecords<OfficeBuilding>(request,'buildings',scope),
   listOfficeRecords<OfficeFloor>(request,'floors',scope),
   listOfficeRecords<OfficeRoom>(request,'rooms',scope),
   listOfficeRecords<OfficeDesk>(request,'desks',scope),
  ]);
  const after=scope();
  if(started.tenantId!==after.tenantId||started.workspaceId!==after.workspaceId)throw new OfficeApiError('Workspace changed while loading office data');
  return {buildings,floors,rooms,desks};
 };
 const post=async<T extends OfficeRecord>(kind:OfficeKind,input:Record<string,unknown>):Promise<T>=>request<T>(officeResourcePath(kind),{method:'POST',body:JSON.stringify(input)});
 const geometry=(id:string,expected_revision:number,x:number,z:number,width:number,depth:number)=>request<OfficeRoom>(officeResourcePath('rooms',id)+'/geometry',{method:'PATCH',body:JSON.stringify({expected_revision,x,z,width,depth})});
 const rename=(id:string,name:string,expected_revision:number)=>request<OfficeRoom>(officeResourcePath('rooms',id),{method:'PATCH',body:JSON.stringify({name,expected_revision})});
 const assign=(id:string,agent_id:string|null,expected_revision:number)=>request<OfficeDesk>(officeResourcePath('desks',id)+'/assignment',{method:'PATCH',body:JSON.stringify({agent_id,expected_revision})});
 const deleteRoom=(id:string,expected_revision:number)=>remove(officeResourcePath('rooms',id)+'?'+new URLSearchParams({expected_revision:String(expected_revision)}),{method:'DELETE'});
 const deleteDesk=(id:string,expected_revision:number)=>remove(officeResourcePath('desks',id)+'?'+new URLSearchParams({expected_revision:String(expected_revision)}),{method:'DELETE'});
 return {load,post,rename,geometry,assign,deleteRoom,deleteDesk};
}
