/** Route-level bridge for a safe rollout of the production headquarters.
 *
 * Both layouts use the existing authenticated shell. The real workspace
 * registry is the default. An explicitly labelled concept preview remains
 * available until design approval, without mixing fake people into the live
 * view. No new public or unauthenticated route is created.
 */
import {lazy,Suspense,useState} from 'react';
import {t} from './lib/i18n';
import {HeadquartersLive, type HeadquartersProps} from './HeadquartersLive';
// The concept preview (with its local reception simulation) loads only when someone opens it, so the live office carries none of it.
const OfficeTourView=lazy(()=>import('./OfficeTourView').then(m=>({default:m.OfficeTourView})));
export type HeadquartersMode='live'|'concept';
export function HeadquartersEntrypoint(props:HeadquartersProps={}){
 const [mode,setMode]=useState<HeadquartersMode>('live');
 const exit=()=>{window.location.hash='#home'};
 // The switch stays visibly separate from the office: authenticated records on one side, the old illustrative tour on the other.
 const switcher=<div className="hqModeSwitcher" role="group" aria-label="Office presentation mode">
   <button type="button" aria-pressed={mode==='live'} onClick={()=>setMode('live')}>
    Authenticated office
   </button>
   <button type="button" aria-pressed={mode==='concept'} onClick={()=>setMode('concept')}>
    Concept preview
   </button>
  </div>;
 return <div className="hqEntrypoint" data-mode={mode}>
  {mode==='live'?<HeadquartersLive onClose={exit} {...props} headerExtra={switcher}/>:<>{switcher}<Suspense fallback={<p role="status">{t('shell.loadingView')}</p>}><OfficeTourView/></Suspense></>}
 </div>;
}
