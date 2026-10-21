import { useEffect, useMemo, useReducer, useState } from 'react';
import { ArrowRight, ClipboardList, MapPin, Pause, Play, RotateCcw, Volume2, VolumeX, X } from 'lucide-react';
import { changeGuidedTour, createGuidedTour, describeTourStop, GuidedTourError, tourProgress, type TourStop } from './lib/guidedExecutiveTour';
import './guidedExecutiveTour.css';

interface Props {site:string;stops:readonly TourStop[];onClose:()=>void;onArrive:(roomId:string)=>void}
type Action=Parameters<typeof changeGuidedTour>[1];
export function GuidedExecutiveTour({site,stops,onClose,onArrive}:Props){
 const [state,dispatch]=useReducer(changeGuidedTour,{site,stops},s=>createGuidedTour(s.site,s.stops,new Date().toISOString()));
 const [voice,setVoice]=useState(false);
 const [error,setError]=useState('');
 const [showHistory,setShowHistory]=useState(false);
 const current=useMemo(()=>stops.find(s=>s.id===state.currentRoomId),[stops,state.currentRoomId]);
 useEffect(()=>()=>{if('speechSynthesis' in window)window.speechSynthesis.cancel()},[]);
 function announce(message:string){
  if(!voice||!('speechSynthesis' in window))return;
  window.speechSynthesis.cancel();
  const utterance=new SpeechSynthesisUtterance(message);utterance.lang='en-US';utterance.rate=.92;window.speechSynthesis.speak(utterance);
 }
 function perform(action:Action){
  try{
   const next=changeGuidedTour(state,action);
   dispatch(action);setError('');
   if(next.currentRoomId&&next.currentRoomId!==state.currentRoomId){
    onArrive(next.currentRoomId);
    const room=stops.find(s=>s.id===next.currentRoomId);
    if(room)announce(describeTourStop(room));
   }else if(next.phase==='finished')announce('We have completed the office tour. You can restart or choose any department.');
  }catch(e){setError(e instanceof GuidedTourError?e.message:'Tour action failed')}
 }
 function execute(type:'begin'|'next'|'pause'|'resume'){perform({type,at:new Date().toISOString()})}
 return <section className="executiveTour" role="dialog" aria-modal="false" aria-label="Executive guided tour">
  <header className="executiveTourHeader"><div><small>ANUM MINE · EXECUTIVE VISIT</small><h2>Your guided office tour</h2><p>{site} · Preview mode</p></div><button type="button" aria-label="Close tour" onClick={onClose}><X size={18}/></button></header>
  <div className="executiveTourStatus"><div><span>Tour progress</span><strong>{tourProgress(state)}%</strong></div><progress max={100} value={tourProgress(state)}/><span className="executiveTourPhase">{state.phase.toUpperCase()}</span></div>
  <div className="executiveTourCompanion"><div className="executiveTourAvatar" aria-hidden="true">✦</div><div><strong>ANUM · Executive PA</strong><p>{current?describeTourStop(current):'Welcome to the virtual headquarters. I will accompany you from reception through each department and back to the executive area.'}</p></div></div>
  <div className="executiveTourActions">
   {state.phase==='ready'&&<button className="executiveTourPrimary" onClick={()=>execute('begin')}><Play size={15}/> Begin tour</button>}
   {state.phase==='touring'&&<><button className="executiveTourPrimary" onClick={()=>execute('next')}><ArrowRight size={15}/> {state.index===state.stops.length-1?'Finish tour':'Next destination'}</button><button onClick={()=>execute('pause')}><Pause size={15}/> Pause</button></>}
   {state.phase==='paused'&&<button className="executiveTourPrimary" onClick={()=>execute('resume')}><Play size={15}/> Resume</button>}
   {state.phase==='finished'&&<button onClick={()=>perform({type:'reset',site,stops,at:new Date().toISOString()})}><RotateCcw size={15}/> Restart</button>}
   <button aria-pressed={voice} onClick={()=>{setVoice(v=>!v);if(voice)window.speechSynthesis?.cancel()}}>{voice?<Volume2 size={15}/>:<VolumeX size={15}/>} Narration {voice?'on':'off'}</button>
  </div>
  <h3>Tour destinations</h3>
  <ol className="executiveTourStops">
   {state.stops.map((s,i)=><li key={s.id} className={current?.id===s.id?'active':''}>
    <button type="button" onClick={()=>perform({type:'redirect',roomId:s.id,at:new Date().toISOString()})} disabled={state.phase==='finished'} aria-current={current?.id===s.id?'step':undefined}>
     <span className="executiveTourStopNumber">{state.visited.includes(s.id)?'✓':i+1}</span><span><strong>{s.name}</strong><small>Floor {s.floor===0?'G':s.floor} · {s.description}</small></span><MapPin size={15}/>
    </button>
   </li>)}
  </ol>
  <div className="executiveTourBottom"><button type="button" onClick={()=>setShowHistory(v=>!v)} aria-expanded={showHistory}><ClipboardList size={15}/> {showHistory?'Hide':'Show'} visit history ({state.events.length})</button>
   {showHistory&&<ol className="executiveTourHistory">{state.events.map((e,i)=><li key={i}><time>{new Date(e.at).toLocaleTimeString()}</time> {e.description}</li>)}</ol>}
  </div>
  {error&&<p className="executiveTourError" role="alert">{error}</p>}
  <p className="executiveTourDisclaimer">Illustrative PA and employees. No real location tracking, access control or employee activity is simulated as fact.</p>
 </section>;
}
