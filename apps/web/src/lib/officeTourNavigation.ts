/** Deterministic, UI-only navigation for the ANUM MINE preview.
 * Commands never call agent tools or mutate tenant data. Real task commands belong to ANUM Command.
 */
export type TourRoom = { id:string; name:string; floor:number };
export type TourDirection =
  | {kind:'pause'}
  | {kind:'next'}
  | {kind:'restart'}
  | {kind:'floor'; floor:number}
  | {kind:'room'; id:string}
  | {kind:'help'};
const normalize=(value:string)=>value.toLocaleLowerCase('en').normalize('NFKC').replace(/[^\p{L}\p{N}\s-]+/gu,' ').replace(/\s+/g,' ').trim();
const words=(value:string)=>' '+normalize(value)+' ';
export function parseTourDirection(value:string, rooms:readonly TourRoom[]):TourDirection {
  const q=words(value);
  if(q.trim().length===0)return {kind:'help'};
  if(/\b(stop|pause|quiet|mute|halt)\b/.test(q))return {kind:'pause'};
  if(/\b(restart|start over|begin again)\b/.test(q))return {kind:'restart'};
  const floor=q.match(/\b(?:floor|level)\s*(ground|zero|0|one|first|1|two|second|2)\b/)||q.match(/\b(ground|first|second)\s+floor\b/);
  if(floor){const n=floor[1];return {kind:'floor',floor:['ground','zero','0'].includes(n)?0:['first','one','1'].includes(n)?1:2};}
  // Match full room names before identifiers, so 'executive office' wins over 'office'.
  const match=[...rooms].sort((a,b)=>b.name.length-a.name.length)
    .find(r=>q.includes(' '+normalize(r.name)+' ')||q.includes(' '+normalize(r.id)+' '));
  if(match)return {kind:'room',id:match.id};
  // A room's first distinctive word provides a short, spoken alternative.
  const matchShort=rooms.find(r=>r.name.split(/\s+/).some(w=>w.length>=5&&q.includes(' '+normalize(w)+' ')));
  if(matchShort)return {kind:'room',id:matchShort.id};
  if(/\b(next|continue|tour|guide|take me around)\b/.test(q))return {kind:'next'};
  return {kind:'help'};
}
export function nextTourRoom(rooms:readonly TourRoom[], selected:string, preferred:readonly string[]=[]):TourRoom|null {
  if(!rooms.length)return null;
  const order=preferred.length
    ? [...preferred.filter(id=>rooms.some(r=>r.id===id)),...rooms.map(r=>r.id).filter(id=>!preferred.includes(id))]
    : rooms.map(r=>r.id);
  const index=order.indexOf(selected);
  return rooms.find(r=>r.id===order[(index+1)%order.length])??rooms[0];
}
