import { useMemo, useReducer, useState } from 'react';
import { ArrowLeft, Building2, CheckCircle2, DoorOpen, Layers3, LayoutGrid, Search, ShieldAlert, UserRound, X } from 'lucide-react';
import { applySpaceCommand, occupation, roomsOnFloor, seedOfficeRegistry, SpaceError, type SpaceCommand, type SpaceKind } from './lib/officeSpaceRegistry';
import './officeSpaceRegistry.css';

/** This is an operationally structured but LOCAL PREVIEW.
 * Do not treat desk assignment as persisted enterprise data until backend API is built.
 */
export function OfficeSpaceRegistry({onClose}:{onClose:()=>void}){
 const [registry,dispatch]=useReducer(applySpaceCommand,undefined,seedOfficeRegistry);
 const [building,setBuilding]=useState('anum-hq'),[floor,setFloor]=useState('anum-hq-0'),[room,setRoom]=useState<string|null>(null);
 const [query,setQuery]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [newRoom,setNewRoom]=useState(''),[roomKind,setRoomKind]=useState<SpaceKind>('office'),[capacity,setCapacity]=useState(8);
 const [agent,setAgent]=useState('');
 const buildingFloors=registry.floors.filter(f=>f.buildingId===building).sort((a,b)=>a.level-b.level);
 const shownRooms=useMemo(()=>roomsOnFloor(registry,floor).filter(r=>r.name.toLowerCase().includes(query.toLowerCase())),[registry,floor,query]);
 const selected=registry.rooms.find(r=>r.id===room&&r.floorId===floor);
 const desks=registry.desks.filter(d=>d.roomId===selected?.id);
 function execute(command:SpaceCommand extends infer T ? T extends {expectedRevision:number} ? Omit<T,'expectedRevision'> : never : never,onSuccess?:()=>void){
  try{const next=applySpaceCommand(registry,{...command,expectedRevision:registry.revision} as SpaceCommand);dispatch({...command,expectedRevision:registry.revision} as SpaceCommand);setError('');setNotice('Preview updated to revision '+next.revision+'. Changes are not saved to the server.');onSuccess?.()}
  catch(e){setError(e instanceof SpaceError?e.message:'Unable to update the office registry')}
 }
 const counts={buildings:registry.buildings.length,floors:buildingFloors.length,rooms:roomsOnFloor(registry,floor).length,desks:registry.desks.filter(d=>registry.rooms.some(r=>r.floorId===floor&&r.id===d.roomId)).length};
 return <section className="spaceRegistry" role="dialog" aria-label="Office Space and Desk Registry" aria-modal="false">
  <header className="spaceRegistryHeader">
   <div><small>ANUM MINE / SPACE MANAGEMENT</small><h2>Office Space & Desk Registry</h2><p>Buildings, floor directory, rooms, capacity and workstation assignments</p></div>
   <button type="button" onClick={onClose}><X size={17}/> Close</button>
  </header>
  <div className="spaceRegistryBanner" role="note"><ShieldAlert size={18}/><span><strong>Preview only.</strong> No persisted backend, employee directory validation, or production access control is connected. Assignments are local and will be lost when this view closes.</span></div>
  <div className="spaceRegistrySummary">
   {([['Buildings',counts.buildings,Building2],['Building floors',counts.floors,Layers3],['Rooms on floor',counts.rooms,DoorOpen],['Desks on floor',counts.desks,LayoutGrid]] as const).map(([label,value,Icon])=><article key={label}><Icon size={18}/><span>{label}</span><strong>{value}</strong></article>)}
  </div>
  <div className="spaceRegistryToolbar">
   <label>Business building<select value={building} onChange={e=>{const id=e.target.value;const first=registry.floors.find(f=>f.buildingId===id);setBuilding(id);setFloor(first?.id??'');setRoom(null)}}>{registry.buildings.map(b=><option key={b.id} value={b.id}>{b.name}</option>)}</select></label>
   <label>Floor<select value={floor} onChange={e=>{setFloor(e.target.value);setRoom(null)}}>{buildingFloors.map(f=><option key={f.id} value={f.id}>Floor {f.level===0?'G':f.level} · {f.name}</option>)}</select></label>
   <label className="spaceRegistrySearch"><Search size={16}/><input aria-label="Search rooms" placeholder="Find room…" value={query} onChange={e=>setQuery(e.target.value)}/></label>
  </div>
  <div className="spaceRegistryColumns">
   <section className="spaceRegistryRooms"><h3>Rooms & shared spaces</h3>
    <div className="spaceRegistryRoomList">{shownRooms.length?shownRooms.map(r=>{const o=occupation(registry,r.id);return <button key={r.id} type="button" className={room===r.id?'selected':''} onClick={()=>setRoom(r.id)}>
     <span className="spaceRegistryRoomIcon"><DoorOpen size={19}/></span><span><strong>{r.name}</strong><small>{r.kind} · Capacity {r.capacity}</small><small>{o.occupied}/{o.total} desks assigned</small></span><span>›</span>
    </button>}):<p>No rooms match this floor and search.</p>}</div>
    <form className="spaceRegistryCreate" onSubmit={e=>{e.preventDefault();execute({type:'room.add',room:{id:'room-'+crypto.randomUUID(),floorId:floor,name:newRoom,kind:roomKind,capacity}},()=>setNewRoom(''))}}>
     <h4>Create room in selected floor</h4><label>Name<input required maxLength={100} value={newRoom} onChange={e=>setNewRoom(e.target.value)} placeholder="e.g., Fleet Command"/></label>
     <div className="spaceRegistryFormRow"><label>Room type<select value={roomKind} onChange={e=>setRoomKind(e.target.value as SpaceKind)}>{(['office','meeting','reception','workshop','warehouse','lounge'] as const).map(k=><option key={k}>{k}</option>)}</select></label><label>Capacity<input type="number" min={1} max={5000} value={capacity} onChange={e=>setCapacity(Number(e.target.value))}/></label></div>
     <button type="submit" className="spaceRegistryPrimary">Create room</button>
    </form>
   </section>
   <section className="spaceRegistryDetails"><h3>Room configuration</h3>
    {!selected?<div className="spaceRegistryPlaceholder"><LayoutGrid size={30}/><h4>Choose a room</h4><p>Inspect room capacity, desks and example assignments.</p></div>:<><div className="spaceRegistrySelected"><div><h4>{selected.name}</h4><p>{selected.kind} · {selected.capacity} person capacity</p></div><span>{desks.length} desk(s)</span></div>
      <div className="spaceRegistryDeskList">{desks.length?desks.map(d=><article key={d.id}><span className="spaceRegistryDeskIcon"><UserRound size={18}/></span><div><strong>{d.label}</strong><small>{d.agentId?'Assigned: '+d.agentId:'Vacant workstation'}</small></div>{d.agentId&&<button type="button" onClick={()=>execute({type:'desk.unassign',deskId:d.id})}>Unassign</button>}</article>):<p>No desks in this room. Add one if the room type allows workstations.</p>}</div>
      <div className="spaceRegistryDetailActions">
        {['office','workshop','warehouse'].includes(selected.kind)&&<button type="button" onClick={()=>execute({type:'desk.add',desk:{id:'desk-'+crypto.randomUUID(),roomId:selected.id,label:'Workstation '+(desks.length+1),agentId:null}})}>+ Add workstation</button>}
        {desks.some(d=>d.agentId===null)&&<form onSubmit={e=>{e.preventDefault();const vacant=desks.find(d=>!d.agentId);if(vacant)execute({type:'desk.assign',deskId:vacant.id,agentId:agent},()=>setAgent(''))}}>
         <label>Assign example agent ID<input required pattern="[A-Za-z0-9][A-Za-z0-9._:-]*" maxLength={100} placeholder="Existing agent ID (demo)" value={agent} onChange={e=>setAgent(e.target.value)}/></label><button type="submit">Assign vacant desk</button>
        </form>}
      </div>
      <p className="spaceRegistryWarning">An assignment here does not verify that an agent exists. The production API must verify identity, permissions, active workspace and concurrency.</p>
    </>}
   </section>
  </div>
  {error&&<p role="alert" className="spaceRegistryError">{error}</p>}
  {notice&&<p role="status" className="spaceRegistryNotice"><CheckCircle2 size={15}/>{notice}</p>}
  <footer className="spaceRegistryFooter"><span>Local registry revision {registry.revision} · Demo data only</span><button onClick={onClose}><ArrowLeft size={15}/> Return to office</button></footer>
 </section>;
}
