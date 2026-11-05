import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Building2, DoorOpen, HardDrive, RefreshCw, ShieldAlert, ShieldCheck, Trash2, UserRoundCheck, X } from 'lucide-react';
import * as api from './lib/api.ts';
import { formatNumber, t, type MessageKey } from './lib/i18n.ts';
import { describeError } from './lib/errors.ts';
import { useLocale } from './useLocale';
import { loadOrgSources } from './lib/orgChart.ts';
import { createOfficeSpaceClient, type OfficeRegistryData, type OfficeRoom } from './lib/officeSpaceClient.ts';
import { OfficeSpaceRegistry } from './OfficeSpaceRegistry';
import './officeSpaceRegistry.css';

const HqBuilder=lazy(()=>import('./hqBuilder/HqBuilder').then(m=>({default:m.HqBuilder})));
const client=createOfficeSpaceClient(api.apiRequest,api.apiRequestVoid,api.currentScope);
const blank:OfficeRegistryData={buildings:[],floors:[],rooms:[],desks:[]};
const makeId=(prefix:string)=>prefix+'-'+crypto.randomUUID();
const zoneList=['Asia/Riyadh','Asia/Dubai','Asia/Karachi','Europe/London','America/New_York'];
const KINDS=['office','meeting','reception','workshop','warehouse','lounge','command'] as const;
// Room types read in the page language (the same names as the campus); the stored value stays the English id.
const kindLabel=(kind:string)=>(KINDS as readonly string[]).includes(kind)?t(`hqSc.kind.${kind}` as MessageKey):kind;
type Saved='building'|'floor'|'room'|'roomName'|'roomLayout'|'desk'|'deskVacated'|'deskAssigned'|'deskRemoved'|'roomDeleted';
export function OfficeSpaceLiveManager({onClose}:{onClose:()=>void}){
 useLocale();
 const [rawData,setData]=useState<OfficeRegistryData>(blank),[rawLoaded,setLoaded]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [loadedScope,setLoadedScope]=useState('');
 const currentScopeKey=JSON.stringify(api.currentScope());
 const loaded=rawLoaded&&loadedScope===currentScopeKey;
 const data=loadedScope===currentScopeKey?rawData:blank;
 const [builder,setBuilder]=useState(false),[preview,setPreview]=useState(false),[version,setVersion]=useState(0);
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
  }catch(e){setError(t('osm.loadFailed',{detail:describeError(e)}))}
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
 async function act(action:()=>Promise<unknown>,saved:Saved){
  if(busy)return;
  if(!loaded||loadedScope!==JSON.stringify(api.currentScope())){setError(t('osm.scopeChanged'));return;}
  setBusy(true);setNotice('');
  try{
   await action();
   // Re-read canonical server state even after a successful response.
   const response=await client.load();setData(response);setLoadedScope(JSON.stringify(api.currentScope()));setLoaded(true);setVersion(v=>v+1);
   setNotice(t(`osm.saved.${saved}` as MessageKey));setError('');
  }catch(e){setError(t('osm.writeFailed',{detail:describeError(e)}))}
  finally{setBusy(false)}
 }
 const goFloor=(id:string)=>{setFloorId(id);setRoomId('');setNewRoomName('')};
 if(builder)return <Suspense fallback={<section className="spaceRegistry" role="status">{t('hqb.state.loading')}</section>}><HqBuilder onClose={()=>{setBuilder(false);void reload()}}/></Suspense>;
 if(preview)return <OfficeSpaceRegistry onClose={()=>setPreview(false)}/>;
 return <section className="spaceRegistry liveSpaceManager" role="dialog" aria-modal="false" aria-label={t('osm.dialog')}>
  <header className="spaceRegistryHeader"><div><small>{t('osm.kicker')}</small><h2>{t('osm.title')}</h2><p>{t('osm.sub')}</p></div><div className="spaceRegistryDetailActions"><button onClick={()=>setBuilder(true)}><Building2 size={16} aria-hidden="true"/> {t('hqb.open')}</button><button onClick={onClose}><X size={18} aria-hidden="true"/> {t('osm.close')}</button></div></header>
  <div className="spaceRegistryBanner" role="note"><ShieldCheck size={18} aria-hidden="true"/><span><strong>{t('osm.banner.strong')}</strong> {t('osm.banner.body')}</span></div>
  <div className="spaceRegistryToolbar">
   <label>{t('osm.building')}<select value={buildingId} onChange={e=>{setBuildingId(e.target.value);setFloorId('');setRoomId('')}}><option value="">{t('osm.building.pick')}</option>{data.buildings.map(b=><option key={b.id} value={b.id}>{b.name}</option>)}</select></label>
   <label>{t('osm.floor')}<select value={chosenFloor?.id??''} onChange={e=>goFloor(e.target.value)}><option value="">{t('osm.floor.pick')}</option>{floors.map(f=><option key={f.id} value={f.id}>{t('osm.floor.option',{level:f.level===0?t('osm.floor.ground'):String(f.level),name:f.name})}</option>)}</select></label>
   <label className="spaceRegistrySearch">{t('osm.search')}<input type="search" dir="auto" value={search} onChange={e=>setSearch(e.target.value)} placeholder={t('osm.search.placeholder')}/></label>
   <button onClick={()=>void reload()} disabled={busy}><RefreshCw size={15} aria-hidden="true"/> {busy?t('osm.working'):t('osm.refresh')}</button>
  </div>
  {error&&<div role="alert" className="spaceRegistryError"><ShieldAlert size={16} aria-hidden="true"/> {error} <button onClick={()=>void reload()}>{t('osm.retry')}</button></div>}
  {notice&&<p role="status" className="spaceRegistryNotice">{notice}</p>}
  {!loaded&&<div className="spaceRegistryPlaceholder"><HardDrive size={32} aria-hidden="true"/><h3 role="status">{busy?t('osm.loading'):t('osm.unavailable')}</h3><p>{t('osm.unavailable.body')}</p><button onClick={()=>setPreview(true)}>{t('osm.preview')}</button></div>}
  {loaded&&<>
   <div className="spaceRegistrySummary">
    {([[t('osm.sum.buildings'),data.buildings.length],[t('osm.sum.floors'),floors.length],[t('osm.sum.rooms'),data.rooms.filter(r=>r.floor_id===chosenFloor?.id).length],[t('osm.sum.desks'),data.desks.filter(d=>data.rooms.some(r=>r.floor_id===chosenFloor?.id&&r.id===d.room_id)).length]] as const).map(([label,value])=><article key={label}><span>{label}</span><strong>{formatNumber(value)}</strong></article>)}
   </div>
   <div className="spaceRegistryColumns">
    <section className="spaceRegistryRooms">
     <h3>{t('osm.config')}</h3>
     <form className="spaceRegistryCreate" onSubmit={e=>{e.preventDefault();void act(()=>client.post('buildings',{id:makeId('building'),name:buildingName,timezone:zone,latitude:null,longitude:null}),'building')}}>
      <h4>{t('osm.addBuilding')}</h4><label>{t('osm.buildingName')}<input required dir="auto" maxLength={100} value={buildingName} onChange={e=>setBuildingName(e.target.value)} placeholder={t('osm.buildingName.placeholder')}/></label>
      <label>{t('osm.zone')}<select value={zone} onChange={e=>setZone(e.target.value)}>{zoneList.map(z=><option key={z} value={z}>{z}</option>)}</select></label>
      <button type="submit" disabled={busy||!buildingName.trim()}>{t('osm.createBuilding')}</button>
     </form>
     {building&&<form className="spaceRegistryCreate" onSubmit={e=>{e.preventDefault();void act(()=>client.post('floors',{id:makeId('floor'),building_id:building.id,level,name:floorName}),'floor')}}>
      <h4>{t('osm.addFloor',{building:building.name})}</h4><label>{t('osm.floorName')}<input required dir="auto" maxLength={100} value={floorName} onChange={e=>setFloorName(e.target.value)}/></label>
      <label>{t('osm.level')}<input type="number" min={-20} max={120} value={level} onChange={e=>setLevel(Number(e.target.value))}/></label>
      <button type="submit" disabled={busy||!floorName.trim()}>{t('osm.createFloor')}</button>
     </form>}
     {chosenFloor&&<form className="spaceRegistryCreate" onSubmit={e=>{e.preventDefault();void act(()=>client.post('rooms',{id:makeId('room'),floor_id:chosenFloor.id,name:roomName,kind,capacity}),'room')}}>
      <h4>{t('osm.addRoom')}</h4><label>{t('osm.roomName')}<input required dir="auto" maxLength={100} value={roomName} onChange={e=>setRoomName(e.target.value)}/></label>
      <div className="spaceRegistryFormRow"><label>{t('osm.type')}<select value={kind} onChange={e=>setKind(e.target.value as OfficeRoom['kind'])}>{KINDS.map(k=><option key={k} value={k}>{kindLabel(k)}</option>)}</select></label><label>{t('osm.capacity')}<input type="number" min={1} max={5000} value={capacity} onChange={e=>setCapacity(Number(e.target.value))}/></label></div>
      <button type="submit" disabled={busy||!roomName.trim()}>{t('osm.createRoom')}</button>
     </form>}
    </section>
    <section className="spaceRegistryDetails">
     <h3>{t('osm.roomsTitle')}</h3>
     <div className="spaceRegistryRoomList">{rooms.length?rooms.map(r=><button key={r.id} className={chosenRoom?.id===r.id?'selected':''} onClick={()=>{setRoomId(r.id);setNewRoomName('')}}><DoorOpen size={17} aria-hidden="true"/><span><strong><bdi>{r.name}</bdi></strong><small>{t('osm.roomMeta',{kind:kindLabel(r.kind),capacity:r.capacity,count:data.desks.filter(d=>d.room_id===r.id).length})}</small></span></button>):<p>{t('osm.noRooms')}</p>}</div>
     {chosenRoom&&<div className="spaceRegistryCreate" key={chosenRoom.id}>
      <h4><bdi>{chosenRoom.name}</bdi></h4><p>{t('osm.occupancy',{kind:kindLabel(chosenRoom.kind),occupied,total:desks.length})}</p>
      <form onSubmit={e=>{e.preventDefault();void act(()=>client.rename(chosenRoom.id,newRoomName,chosenRoom.revision),'roomName')}}>
       <label>{t('osm.rename')}<input required dir="auto" maxLength={100} placeholder={chosenRoom.name} value={newRoomName} onChange={e=>setNewRoomName(e.target.value)}/></label><button disabled={busy||!newRoomName.trim()}>{t('osm.saveName')}</button>
      </form>
      <form key={'geometry-'+chosenRoom.id+'-'+chosenRoom.revision}
       onSubmit={e=>{e.preventDefault();const values=new FormData(e.currentTarget);
        void act(()=>client.geometry(chosenRoom.id,chosenRoom.revision,
          Number(values.get('x')),Number(values.get('z')),
          Number(values.get('width')),Number(values.get('depth'))),'roomLayout')}}>
       <h4>{t('osm.placement')}</h4>
       <div className="spaceRegistryFormRow">
        {(['x','z','width','depth'] as const).map(field=><label key={field}>{t(`osm.geo.${field}` as MessageKey)}
         <input name={field} type="number" step="0.1" required
          min={field==='width'||field==='depth'?1:-10000}
          max={field==='width'||field==='depth'?1000:10000}
          defaultValue={field==='x'?chosenRoom.scene_x??0:field==='z'?chosenRoom.scene_z??0:field==='width'?chosenRoom.scene_width??5.8:chosenRoom.scene_depth??4.2}/>
        </label>)}
       </div>
       <button disabled={busy}>{t('osm.saveGeometry')}</button>
      </form>
      {['office','warehouse','workshop','command'].includes(chosenRoom.kind)&&<form onSubmit={e=>{e.preventDefault();void act(()=>client.post('desks',{id:makeId('desk'),room_id:chosenRoom.id,label:deskLabel}),'desk')}}>
       <label>{t('osm.deskLabel')}<input required dir="auto" maxLength={100} value={deskLabel} onChange={e=>setDeskLabel(e.target.value)} placeholder={t('osm.deskLabel.placeholder')}/></label><button disabled={busy||!deskLabel.trim()||desks.length>=chosenRoom.capacity}>{t('osm.addDesk')}</button>
      </form>}
      <div className="spaceRegistryDeskList">{desks.map(d=><article key={d.id}>
       <UserRoundCheck size={17} aria-hidden="true"/><div><strong><bdi>{d.label}</bdi></strong><small><bdi>{d.agent_id?people.find(a=>a.id===d.agent_id)?.name??t('osm.desk.restricted'):t('osm.desk.vacant')}</bdi></small></div>
       {d.agent_id?<button disabled={busy} onClick={()=>void act(()=>client.assign(d.id,null,d.revision),'deskVacated')}>{t('osm.unassign')}</button>:
        <select aria-label={t('osm.assignTo',{desk:d.label})} defaultValue="" onChange={e=>{if(e.target.value)void act(()=>client.assign(d.id,e.target.value,d.revision),'deskAssigned')}} disabled={busy||unassigned.length===0}><option value="">{t('osm.assign')}</option>{unassigned.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select>}
       <button aria-label={t('osm.deleteDesk',{desk:d.label})} disabled={busy} onClick={()=>{if(window.confirm(t('osm.confirmDesk')))void act(()=>client.deleteDesk(d.id,d.revision),'deskRemoved')}}><Trash2 size={14} aria-hidden="true"/></button>
      </article>)}</div>
      <button disabled={busy||desks.length>0} onClick={()=>{if(window.confirm(t('osm.confirmRoom')))void act(()=>client.deleteRoom(chosenRoom.id,chosenRoom.revision),'roomDeleted')}}>{t('osm.deleteRoom')}</button>
     </div>}
    </section>
   </div>
  </>}
  <footer className="spaceRegistryFooter"><span>{loaded?t('osm.footer.loaded',{count:data.buildings.length,revision:version}):t('osm.footer.waiting')}</span><div><button onClick={()=>setPreview(true)}>{t('osm.demo')}</button><button onClick={onClose}><ArrowLeft size={15} className="flipRtl" aria-hidden="true"/> {t('osm.return')}</button></div></footer>
 </section>;
}
