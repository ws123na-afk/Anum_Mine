/** Route-level bridge for a safe rollout of the production headquarters.
 *
 * Both layouts use the existing authenticated shell. The real workspace
 * registry is the default. An explicitly labelled concept preview remains
 * available until design approval, without mixing fake people into the live
 * view. No new public or unauthenticated route is created.
 */
import {useState} from 'react';
import {HeadquartersLive} from './HeadquartersLive';
import {OfficeTourView} from './OfficeTourView';
export type HeadquartersMode='live'|'concept';
export function HeadquartersEntrypoint(){
 const [mode,setMode]=useState<HeadquartersMode>('live');
 const exit=()=>{window.location.hash='#home'};
 return <div className="hqEntrypoint" data-mode={mode}>
  <div className="hqModeSwitcher" role="group" aria-label="Office presentation mode">
   <button type="button" aria-pressed={mode==='live'} onClick={()=>setMode('live')}>
    Authenticated office
   </button>
   <button type="button" aria-pressed={mode==='concept'} onClick={()=>setMode('concept')}>
    Concept preview
   </button>
  </div>
  {mode==='live'?<HeadquartersLive onClose={exit}/>:<OfficeTourView/>}
 </div>;
}
