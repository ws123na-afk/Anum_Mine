import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Building2, DoorOpen, HardDrive, RefreshCw, ShieldAlert, ShieldCheck, Trash2, UserRoundCheck, X } from 'lucide-react';
import * as api from './lib/api.ts';
import { loadOrgSources } from './lib/orgChart.ts';
import { createOfficeSpaceClient, type OfficeRegistryData, type OfficeRoom } from './lib/officeSpaceClient.ts';
import { OfficeSpaceRegistry } from './OfficeSpaceRegistry';
import './officeSpaceRegistry.css';

const client=createOfficeSpaceClient(api.apiRequest,api.apiRequestVoid,api.currentScope);
const blank:OfficeRegistryData={buildings:[],floors:[],rooms:[],desks:[]};
const makeId=(prefix:string)=>prefix+'-'+crypto.randomUUID();
const zoneList=['Asia/Riyadh','Asia/Dubai','Asia/Karachi','Europe/London','America/New_York'];
function shortError(error:unknown):string {
 return error instanceof Error?error.message:'The office registry request failed. Try refreshing.';
}
export function OfficeSpaceLiveManager({onClose}:{onClose:()=>void}){
 const [rawData,setData]=useState<OfficeRegistryData>(blank),[rawLoaded,setLoaded]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [loadedScope,setLoadedScope]=useState('');
 const currentScopeKey=JSON.stringify(api.currentScope());
 const loaded=rawLoaded&&loadedScope===currentScopeKey;
 const data=loadedScope===currentScopeKey?rawData:blank;
 const [preview,setPreview]=useState(false),[version,setVersion]=useState(0);
 const [buildingId,setBuildingId]=useState(''),[floorId,setFloorId]=useState(''),[roomId,setRoomId]=useState('');
 const [buildingName,setBuildingName]=useState(''),[zone,setZone]=useState('Asia/Riyadh');
 const [floorName,setFloorName]=useState(''),[level,setLevel]=useState(0),[roomName,setRoomName]=useState(''),[kind,setKind]=useState<OfficeRoom['kind']>('office'),[capacity,setCapacity]=useState(8);
 const [deskLabel,setDeskLabel]=useState('');
 const [newRoomName,setNewRoomName]=useState('');
 const [people,setPeople]=useState<{id:string;name:string}[]>([]),[notice,setNotice]=useState('');
 const [search,setSearch]=useState('');
 const reload=useCallback(async()=>{
  setBusy(true);try{
   const response=await client.load();setData(response);setLoadedScope(JSON.stringify(api.currentScope()));setLoaded(true);setVersion(v=>v+1);setError('');
   setBuildingId(old=>response.buildings.some(b=>b.id===old)?old:response.buildings[0]?.id??'');
  }catch(e){setError(shortError(e))}
  finally{setBusy(false)}
 },[]);
 useEffect(()=>{
  let active=true;
  void reload();
  void loadOrgSources(api.apiRequest).then(src=>{if(active)setPeople(src.agents.map(a=>({id:a.id,name:a.name})))})
   .catch(()=>{if(active)setPeople([])});
  return()=>{active=false};
 },[reload]);
 const building=data.buildings.find(b=>b.id===buildingId);
 const floors=useMemo(()=>data.floors.filter(f=>f.building_id===buildingId).sort((a,b)=>a.level-b.level),[data.floors,buildingId]);
 const chosenFloor=floors.find(f=>f.id===floorId)??floors[0];
 const rooms=useMemo(()=>data.rooms.filter(r=>r.floor_id===chosenFloor?.id&&r.name.toLowerCase().includes(search.toLowerCase())).sort((a,b)=>a.name.localeCompare(b.name)),[data.rooms,chosenFloor?.id,search]);
 const chosenRoom=data.rooms.find(r=>r.id===roomId&&r.floor_id===chosenFloor?.id)??rooms[0];
 const desks=data.desks.filter(d=>d.room_id===chosenRoom?.id).sort((a,b)=>a.label.localeCompare(b.label));
 const occupied=desks.filter(d=>d.agent_id!==null).length;
 const occupiedIds=new Set(data.desks.map(d=>d.agent_id).filter(Boolean));
 const unassigned=people.filter(a=>!occupiedIds.has(a.id));
 async function act(action:()=>Promise<unknown>,label:string){
  if(busy)return;
  if(!loaded||loadedScope!==JSON.stringify(api.currentScope())){setError('Workspace changed. Refresh the office before writing.');return;}
  setBusy(true);setNotice('');
  try{
   await action();
   // Re-read canonical server state even after a successful response.
   const response=await client.load();setData(response);setLoadedScope(JSON.stringify(api.currentScope()));setLoaded(true);setVersion(v=>v+1);
   setNotice(label+' saved to your workspace.');setError('');
  }catch(e){setError(shortError(e)+' If the record changed, refresh and retry.')}
  finally{setBusy(false)}
 }
 const goFloor=(id:string)=>{setFloorId(id);setRoomId('');setNewRoomName('')};
 if(preview)return <OfficeSpaceRegistry onClose={()=>setPreview(false)}/>;
 return <section className="spaceRegistry liveSpaceManager" role="dialog" aria-modal="false" aria-label="Production Office Space Management">
  <header className="spaceRegistryHeader"><div><small>ANUM MINE / SERVER-BACKED OPERATIONS</small><h2>Office Space Management</h2><p>Workspace registry · Buildings, rooms, real agent assignments and audited changes</p></div><button onClick={onClose}><X size={18}/> Close</button></header>
  <div className="spaceRegistryBanner" role="note"><ShieldCheck size={18}/><span><strong>Server-backed mode.</strong> Successful changes are written through ANUM's permissioned PostgreSQL API. Assignments must reference an existing visible agent. If PostgreSQL is unavailable, no changes are silently saved.</span></div>
  <div className="spaceRegistryToolbar">
   <label>Building<select value={buildingId} onChange={e=>{setBuildingId(e.target.value);setFloorId('');setRoomId('')}}><option value="">Select a building</option>{data.buildings.map(b=><option key={b.id} value={b.id}>{b.name}</option>)}</select></label>
   <label>Floor<select value={chosenFloor?.id??''} onChange={e=>goFloor(e.target.value)}><option value="">Select a floor</option>{floors.map(f=><option key={f.id} value={f.id}>{f.level===0?'G':f.level} · {f.name}</option>)}</select></label>
   <label className="spaceRegistrySearch">Search rooms<input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Room name"/></label>
   <button onClick={()=>void reload()} disabled={busy}><RefreshCw size={15}/> {busy?'Working…':'Refresh'}</button>
  </div>
  {error&&<div role="alert" className="spaceRegistryError"><ShieldAlert size={16}/> {error} <button onClick={()=>void reload()}>Retry</button></div>}
  {notice&&<p role="status" className="spaceRegistryNotice">{notice}</p>}
  {!loaded&&<div className="spaceRegistryPlaceholder"><HardDrive size={32}/><h3>{busy?'Loading authenticated office registry…':'Server-backed office unavailable'}</h3><p>No live configuration has been loaded. Demo mode is separate and does not save records.</p><button onClick={()=>setPreview(true)}>Open local preview instead</button></div>}
  {loaded&&<>
   <div className="spaceRegistrySummary">
    {([['Buildings',data.buildings.length],['Floors',floors.length],['Rooms',data.rooms.filter(r=>r.floor_id===chosenFloor?.id).length],['Desks',data.desks.filter(d=>data.rooms.some(r=>r.floor_id===chosenFloor?.id&&r.id===d.room_id)).length]] as const).map(([label,value])=><article key={label}><span>{label}</span><strong>{value}</strong></article>)}
   </div>
   <div className="spaceRegistryColumns">
    <section className="spaceRegistryRooms">
     <h3>Building and floor configuration</h3>
     <form className="spaceRegistryCreate" onSubmit={e=>{e.preventDefault();void act(()=>client.post('buildings',{id:makeId('building'),name:buildingName,timezone:zone,latitude:null,longitude:null}),'Building')}}>
      <h4>Add a building</h4><label>Building name<input required maxLength={100} value={buildingName} onChange={e=>setBuildingName(e.target.value)} placeholder="e.g., ANUM New Headquarters"/></label>
      <label>Time zone<select value={zone} onChange={e=>setZone(e.target.value)}>{zoneList.map(z=><option key={z}>{z}</option>)}</select></label>
      <button type="submit" disabled={busy||!buildingName.trim()}>Create building</button>
     </form>
     {building&&<form className="spaceRegistryCreate" onSubmit={e=>{e.preventDefault();void act(()=>client.post('floors',{id:makeId('floor'),building_id:building.id,level,name:floorName}),'Floor')}}>
      <h4>Add floor to {building.name}</h4><label>Floor name<input required maxLength={100} value={floorName} onChange={e=>setFloorName(e.target.value)}/></label>
      <label>Level<input type="number" min={-20} max={120} value={level} onChange={e=>setLevel(Number(e.target.value))}/></label>
      <button type="submit" disabled={busy||!floorName.trim()}>Create floor</button>
     </form>}
     {chosenFloor&&<form className="spaceRegistryCreate" onSubmit={e=>{e.preventDefault();void act(()=>client.post('rooms',{id:makeId('room'),floor_id:chosenFloor.id,name:roomName,kind,capacity}),'Room')}}>
      <h4>Create a room</h4><label>Room name<input required maxLength={100} value={roomName} onChange={e=>setRoomName(e.target.value)}/></label>
      <div className="spaceRegistryFormRow"><label>Type<select value={kind} onChange={e=>setKind(e.target.value as OfficeRoom['kind'])}>{(['office','meeting','reception','workshop','warehouse','lounge'] as const).map(k=><option key={k}>{k}</option>)}</select></label><label>Capacity<input type="number" min={1} max={5000} value={capacity} onChange={e=>setCapacity(Number(e.target.value))}/></label></div>
      <button type="submit" disabled={busy||!roomName.trim()}>Create room</button>
     </form>}
    </section>
    <section className="spaceRegistryDetails">
     <h3>Rooms and live desk assignments</h3>
     <div className="spaceRegistryRoomList">{rooms.length?rooms.map(r=><button key={r.id} className={chosenRoom?.id===r.id?'selected':''} onClick={()=>{setRoomId(r.id);setNewRoomName('')}}><DoorOpen size={17}/><span><strong>{r.name}</strong><small>{r.kind} · Capacity {r.capacity} · {data.desks.filter(d=>d.room_id===r.id).length} desks</small></span></button>):<p>Select a floor or create a room to begin.</p>}</div>
     {chosenRoom&&<div className="spaceRegistryCreate" key={chosenRoom.id}>
      <h4>{chosenRoom.name}</h4><p>{chosenRoom.kind} · {occupied}/{desks.length} desks occupied</p>
      <form onSubmit={e=>{e.preventDefault();void act(()=>client.rename(chosenRoom.id,newRoomName,chosenRoom.revision),'Room name')}}>
       <label>Rename room<input required maxLength={100} placeholder={chosenRoom.name} value={newRoomName} onChange={e=>setNewRoomName(e.target.value)}/></label><button disabled={busy||!newRoomName.trim()}>Save name</button>
      </form>
      {['office','warehouse','workshop'].includes(chosenRoom.kind)&&<form onSubmit={e=>{e.preventDefault();void act(()=>client.post('desks',{id:makeId('desk'),room_id:chosenRoom.id,label:deskLabel}),'Desk')}}>
       <label>Workstation label<input required maxLength={100} value={deskLabel} onChange={e=>setDeskLabel(e.target.value)} placeholder="Desk A1"/></label><button disabled={busy||!deskLabel.trim()||desks.length>=chosenRoom.capacity}>Add desk</button>
      </form>}
      <div className="spaceRegistryDeskList">{desks.map(d=><article key={d.id}>
       <UserRoundCheck size={17}/><div><strong>{d.label}</strong><small>{d.agent_id?people.find(a=>a.id===d.agent_id)?.name??'Assigned (name restricted)':'Vacant'}</small></div>
       {d.agent_id?<button disabled={busy} onClick={()=>void act(()=>client.assign(d.id,null,d.revision),'Desk vacated')}>Unassign</button>:
        <select aria-label={'Assign agent to '+d.label} defaultValue="" onChange={e=>{if(e.target.value)void act(()=>client.assign(d.id,e.target.value,d.revision),'Desk assignment')}} disabled={busy||unassigned.length===0}><option value="">Assign agent</option>{unassigned.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select>}
       <button aria-label={'Delete '+d.label} disabled={busy} onClick={()=>{if(window.confirm('Remove the selected desk from the office?'))void act(()=>client.deleteDesk(d.id,d.revision),'Desk removed')}}><Trash2 size={14}/></button>
      </article>)}</div>
      <button disabled={busy||desks.length>0} onClick={()=>{if(window.confirm('Delete this empty room?'))void act(()=>client.deleteRoom(chosenRoom.id,chosenRoom.revision),'Room deleted')}}>Delete empty room</button>
     </div>}
    </section>
   </div>
  </>}
  <footer className="spaceRegistryFooter"><span>{loaded?'Server records loaded · '+data.buildings.length+' building(s) · Revision '+version:'Awaiting server verification'}</span><div><button onClick={()=>setPreview(true)}>Open demo layout</button><button onClick={onClose}><ArrowLeft size={15}/> Return to office</button></div></footer>
 </section>;
}
