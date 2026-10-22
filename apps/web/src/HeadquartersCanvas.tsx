import { useCallback, useEffect, useRef, useState, type PointerEvent, type WheelEvent } from 'react';
import type { CameraState } from './lib/headquartersSpatial.ts';
import { cameraBasis, cameraEye, dolly, orbit, pan, pickRoom, screenRay,
  vsub, dot, type Ray } from './lib/headquartersSpatial.ts';
import type { RoomGeometry, SceneDesk, SceneFloor, Vec3 } from './lib/headquartersProjection.ts';

type RGB=[number,number,number];
type Quad={points:readonly [Vec3,Vec3,Vec3,Vec3];color:RGB;alpha:number;key:string};
type Mesh={quads:readonly Quad[];roomId:string|null};
type Props={floor:SceneFloor;selectedRoomId:string|null;camera:CameraState;
 setCamera:(camera:CameraState)=>void;onSelectRoom:(id:string)=>void;night:boolean};
type Drag={x:number;y:number;button:number;camera:CameraState;distance:number;pointerId:number};
const bytes=(rgb:RGB)=>rgb.map(n=>Math.min(255,Math.max(0,Math.round(n*255)))) as RGB;
const color=(hex:string):RGB=>{
 if(!/^#[0-9a-f]{6}$/i.test(hex))return [70,140,133];
 return bytes([parseInt(hex.slice(1,3),16),parseInt(hex.slice(3,5),16),parseInt(hex.slice(5,7),16)]);
};
function mix(a:RGB,b:RGB,w:number):RGB{
 return bytes([a[0]*(1-w)+b[0]*w,a[1]*(1-w)+b[1]*w,a[2]*(1-w)+b[2]*w]);
}
function box(x:number,y:number,z:number,w:number,h:number,d:number,shade:RGB,key:string,alpha=1):Mesh{
 const p=(dx:number,dy:number,dz:number):Vec3=>({x:x+dx,y:y+dy,z:z+dz});
 const A=p(0,0,0),B=p(w,0,0),C=p(w,0,d),D=p(0,0,d);
 const E=p(0,h,0),F=p(w,h,0),G=p(w,h,d),H=p(0,h,d);
 return {roomId:null,quads:[
  {points:[E,F,G,H],color:mix(shade,[255,255,255],.22),alpha,key:key+'-top'},
  {points:[A,B,F,E],color:mix(shade,[0,0,0],.27),alpha,key:key+'-front'},
  {points:[B,C,G,F],color:mix(shade,[0,0,0],.14),alpha,key:key+'-right'},
  {points:[C,D,H,G],color:mix(shade,[0,0,0],.36),alpha,key:key+'-back'},
  {points:[D,A,E,H],color:mix(shade,[0,0,0],.08),alpha,key:key+'-left'},
 ]};
}
/** Fixed architectural fittings; furniture is not employee or activity evidence. */
function fitting(x:number,y:number,z:number,w:number,h:number,d:number,shade:RGB,id:string):Mesh{
 return box(x,y,z,w,h,d,shade,id);
}
function pottedPlant(x:number,z:number,id:string):Mesh[]{
 return [
  fitting(x,.1,z,.27,.42,.27,[115,93,70],id+'-pot'),
  fitting(x-.10,.52,z-.10,.47,.42,.47,[41,109,76],id+'-leaves'),
  fitting(x+.06,.78,z+.04,.28,.25,.28,[70,142,91],id+'-crown'),
 ];
}
function seating(x:number,z:number,id:string):Mesh[]{
 return [
  fitting(x,.16,z,.68,.18,.55,[66,91,99],id+'-base'),
  fitting(x,.34,z,.68,.54,.14,[96,118,125],id+'-back'),
  fitting(x+.13,.1,z+.65,.46,.14,.24,[44,58,66],id+'-foot')
 ];
}
function departmentFurniture(room:RoomGeometry,night:boolean):Mesh[]{
 const {x,z,width:w,depth:d,id,kind}=room;
 const wood:RGB=night?[115,90,68]:[162,119,81];
 const stone:RGB=night?[115,124,124]:[225,218,198];
 const dark:RGB=[42,61,72];
 const result:Mesh[]=[];
 // Wall-side millwork, foliage, decorative lighting and carefully inset furniture.
 if(w>3&&d>3){
  result.push(...pottedPlant(x+.35,z+.35,id+'-plant-nw'));
  result.push(...pottedPlant(x+w-.62,z+d-.63,id+'-plant-se'));
  result.push(fitting(x+.26,1.65,z+.13,w-.52,.055,.065,[207,176,112],id+'-warm-wall-light'));
 }
 if(kind==='meeting'){
  const tw=Math.min(w*.56,4.4),td=Math.min(d*.26,1.55);
  const tx=x+(w-tw)/2,tz=z+(d-td)/2;
  result.push(fitting(tx,.67,tz,tw,.13,td,wood,id+'-boardroom-table'));
  result.push(fitting(tx+.22,.12,tz+.16,tw-.44,.54,td-.32,dark,id+'-table-base'));
  for(let i=0;i<Math.min(5,Math.max(2,Math.floor(tw/.8)));i++){
   const px=tx+(i+.35)*tw/Math.min(5,Math.max(2,Math.floor(tw/.8)));
   result.push(...seating(px,z+d*.18,id+'-chair-n'+i));
   result.push(...seating(px,z+d*.72,id+'-chair-s'+i));
  }
  result.push(fitting(x+w*.22,1.05,z+.16,w*.55,1.05,.1,[35,82,103],id+'-presentation-display'));
 }else if(kind==='reception'){
  result.push(fitting(x+w*.18,.20,z+d*.57,w*.64,.82,.45,stone,id+'-reception-counter'));
  result.push(fitting(x+w*.20,1.02,z+d*.57,w*.60,.07,.46,[231,226,214],id+'-counter-cap'));
  result.push(fitting(x+w*.20,.15,z+.14,w*.60,1.9,.12,wood,id+'-reception-feature-wall'));
  result.push(fitting(x+w*.32,1.35,z+.28,w*.35,.14,.03,[215,195,124],id+'-reception-signage'));
  if(w>5&&d>4){result.push(...seating(x+w*.25,z+d*.22,id+'-visitor-seat'))}
 }else if(kind==='office'){
  result.push(fitting(x+w*.30,.65,z+d*.34,w*.40,.10,d*.22,wood,id+'-executive-desk'));
  result.push(fitting(x+w*.32,.20,z+d*.39,w*.34,.45,d*.12,dark,id+'-executive-cabinet'));
  result.push(fitting(x+w*.40,.77,z+d*.37,w*.17,.55,.04,[39,80,101],id+'-executive-monitor'));
  if(w>5&&d>4)result.push(...seating(x+w*.12,z+d*.69,id+'-guest-chair'));
 }else if(kind==='lounge'){
  const sy=z+d*.34;
  result.push(fitting(x+w*.20,.15,sy,w*.52,.44,.60,[126,139,133],id+'-sofa'));
  result.push(fitting(x+w*.20,.55,sy,w*.52,.38,.13,[102,121,122],id+'-sofa-back'));
  result.push(fitting(x+w*.38,.35,z+d*.65,w*.23,.16,.52,wood,id+'-coffee-table'));
 }else if(kind==='warehouse'||kind==='workshop'){
  for(let i=0;i<3;i++)result.push(fitting(x+w*.15+i*w*.245,.12,z+d*.22,w*.14,1.25,d*.52,
   [88,107,105],id+'-shelf'+i));
 }else{
  // Department workbench: meaningful spatial identity without synthetic people.
  result.push(fitting(x+w*.22,.68,z+d*.39,w*.53,.09,d*.19,wood,id+'-operations-worktop'));
  result.push(fitting(x+w*.28,.78,z+d*.40,.54,.48,.06,[34,77,93],id+'-operations-display'));
 }
 return result;
}
export function roomMeshes(room:RoomGeometry,desks:readonly SceneDesk[],night:boolean,selected:boolean):Mesh[]{
 const {x,z,width,depth,height}=room;
 const floorColor:RGB=selected?[76,184,157]:night?[64,88,103]:[183,198,189];
 const wallColor:RGB=night?[73,120,139]:[158,199,193];
 const meshes:Mesh[]=[
  box(x,0,z,width,.1,depth,floorColor,room.id+'-floor'),
  box(x,.1,z,width,height*.62,.10,wallColor,room.id+'-north',.7),
  box(x,.1,z,.11,height*.62,depth,wallColor,room.id+'-west',.65),
  box(x+width-.11,.1,z,.11,height*.62,depth,wallColor,room.id+'-east',.35),
  // Perimeter glass walls deliberately stop below ceiling for an unobstructed cutaway.
  box(x,.1,z+depth-.12,width,height*.48,.12,wallColor,room.id+'-south-glass',.24),
  box(x,.1,z+depth*.53,.12,height*.50,Math.max(.12,depth*.23),wallColor,room.id+'-glass-partition',.20),
 ];
 meshes.push(...departmentFurniture(room,night));
 const deskMaterial:RGB=night?[111,86,68]:[174,134,104];
 const chairMaterial:RGB=night?[48,67,82]:[74,91,106];
 const monitor:RGB=night?[39,91,112]:[43,77,94];
 for(const desk of desks){
  const dx=desk.position.x-.35,dz=desk.position.z-.23;
  meshes.push(box(dx,.71,dz,.7,.06,.44,deskMaterial,desk.id+'-desktop'));
  meshes.push(box(dx+.12,.77,dz+.07,.37,.28,.03,monitor,desk.id+'-screen'));
  meshes.push(box(dx+.19,.78,dz+.28,.24,.012,.08,[215,223,218],desk.id+'-keyboard'));
  meshes.push(box(dx+.2,.22,dz+.55,.31,.46,.30,chairMaterial,desk.id+'-chair'));
  meshes.push(box(dx+.25,.1,dz+.58,.2,.1,.2,[49,58,69],desk.id+'-chair-base'));
  const occupied=desk.agentId!==null;
  const marker:RGB=occupied?desk.agent?[64,204,152]:[224,175,103]:[93,124,143];
  meshes.push(box(dx+.02,.78,dz+.02,.07,.08,.07,marker,desk.id+'-status'));
 }
 if(room.kind==='meeting'){
  meshes.push(box(x+width*.28,.72,z+depth*.34,width*.43,.09,depth*.22,
   night?[93,104,113]:[173,145,117],room.id+'-conference'));
 }
 if(room.kind==='reception'){
  meshes.push(box(x+width*.22,.45,z+depth*.68,width*.58,.55,depth*.15,
   [74,132,131],room.id+'-reception-desk'));
 }
 return meshes;
}
export function sceneMeshes(floor:SceneFloor,selectedRoomId:string|null,night:boolean):Mesh[]{
 const rooms=floor.rooms;
 const bounds=floor.bounds;
 const foundation=box(-14,-.45,-9,bounds.width+14,.44,bounds.depth+12,
  night?[61,68,72]:[170,174,165],'foundation');
 const hallway=box(-1,.004,-3,2,.01,Math.max(10,bounds.depth+3),
  night?[69,104,109]:[210,222,210],'walkway');
 const meshes:Mesh[]=[foundation,hallway];
 // Building trim, perimeter downlights, and structural features anchor the scene.
 const trim:RGB=night?[186,146,90]:[197,181,145];
 meshes.push(fitting(-14,-.16,-9,bounds.width+14,.15,.12,trim,'building-front-trim'));
 meshes.push(fitting(-14,-.16,-9,.12,.15,bounds.depth+12,trim,'building-west-trim'));
 meshes.push(fitting(-14,-.16,bounds.depth+2,bounds.width+14,.15,.12,trim,'building-south-trim'));
 for(let i=0;i<Math.min(12,Math.floor(bounds.width/3));i++){
  meshes.push(fitting(-12+i*3,.055,-8,.43,.07,.16,[220,198,134],'light-'+i));
 }
 for(const room of rooms){
  const desks=floor.desks.filter(d=>d.roomId===room.id);
  meshes.push(...roomMeshes(room,desks,night,selectedRoomId===room.id));
 }
 return meshes;
}
function project(camera:CameraState,point:Vec3,width:number,height:number):Readonly<{x:number;y:number;depth:number}|null>{
 const basis=cameraBasis(camera),eye=cameraEye(camera);
 const relative=vsub(point,eye);
 const depth=dot(relative,basis.forward);
 if(depth<=.01)return null;
 const f=1/Math.tan(camera.fov*Math.PI/360);
 const aspect=width/height;
 return {x:width*.5+(dot(relative,basis.right)*f/depth/aspect)*width*.5,
  y:height*.5-(dot(relative,basis.up)*f/depth)*height*.5,depth};
}
function rgba(rgb:RGB,alpha:number){return `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${alpha})`}
function drawQuads(ctx:CanvasRenderingContext2D,meshes:readonly Mesh[],camera:CameraState,w:number,h:number):void{
 const faces=meshes.flatMap(mesh=>mesh.quads.map(face=>{
  const points=face.points.map(p=>project(camera,p,w,h));
  if(points.some(p=>p===null))return null;
  const pts=points as {x:number;y:number;depth:number}[];
  return {pts,depth:pts.reduce((v,p)=>v+p.depth,0)/pts.length,color:face.color,alpha:face.alpha};
 })).filter((f):f is NonNullable<typeof f>=>f!==null).sort((a,b)=>b.depth-a.depth);
 for(const face of faces){
  ctx.beginPath();ctx.moveTo(face.pts[0].x,face.pts[0].y);
  for(let i=1;i<face.pts.length;i++)ctx.lineTo(face.pts[i].x,face.pts[i].y);
  ctx.closePath();ctx.fillStyle=rgba(face.color,face.alpha);ctx.fill();
  ctx.lineWidth=.75;ctx.strokeStyle='rgba(11,36,48,.24)';ctx.stroke();
 }
}
function drawRoomNames(ctx:CanvasRenderingContext2D,floor:SceneFloor,camera:CameraState,w:number,h:number):void{
 ctx.textAlign='center';ctx.textBaseline='middle';
 for(const room of floor.rooms){
  const point=project(camera,{x:room.x+room.width/2,y:room.height*.66,z:room.z+room.depth*.5},w,h);
  if(!point)continue;
  const label=room.label.slice(0,40);
  ctx.font='600 12px system-ui';
  const textWidth=ctx.measureText(label).width;
  ctx.fillStyle='rgba(8,31,44,.92)';ctx.fillRect(point.x-textWidth/2-10,point.y-12,textWidth+20,24);
  ctx.fillStyle='#eefaf8';ctx.fillText(label,point.x,point.y);
 }
}
/** Canvas 2D is a reliable graphics fallback; it is not falsely labelled WebGL. */
export function HeadquartersCanvas({floor,selectedRoomId,camera,setCamera,onSelectRoom,night}:Props){
 const canvas=useRef<HTMLCanvasElement>(null);
 const pointer=useRef<Drag|null>(null);
 const raf=useRef<number|null>(null);
 const [renderIssue,setRenderIssue]=useState<string|null>(null);
 const render=useCallback(()=>{
  const element=canvas.current;if(!element)return;
  const ctx=element.getContext('2d',{alpha:false});
  if(!ctx){setRenderIssue('Canvas rendering is unavailable');return}
  const rect=element.getBoundingClientRect();
  const ratio=Math.min(2,devicePixelRatio||1);
  const width=Math.max(1,Math.round(rect.width*ratio)),height=Math.max(1,Math.round(rect.height*ratio));
  if(element.width!==width)element.width=width;
  if(element.height!==height)element.height=height;
  ctx.save();ctx.scale(ratio,ratio);
  const w=width/ratio,h=height/ratio;
  const gradient=ctx.createLinearGradient(0,0,w,h);
  gradient.addColorStop(0,night?'#081a2a':'#193b4c');
  gradient.addColorStop(1,night?'#1c3040':'#718b8d');
  ctx.fillStyle=gradient;ctx.fillRect(0,0,w,h);
  drawQuads(ctx,sceneMeshes(floor,selectedRoomId,night),camera,w,h);
  drawRoomNames(ctx,floor,camera,w,h);
  ctx.restore();
 },[floor,selectedRoomId,camera,night]);
 useEffect(()=>{
  const el=canvas.current;if(!el)return;
  const schedule=()=>{if(raf.current!==null)cancelAnimationFrame(raf.current);raf.current=requestAnimationFrame(render)};
  const resize=new ResizeObserver(schedule);resize.observe(el);schedule();
  return()=>{resize.disconnect();if(raf.current!==null)cancelAnimationFrame(raf.current)};
 },[render]);
 function down(event:PointerEvent<HTMLCanvasElement>){
  if(event.button!==0&&event.button!==1&&event.button!==2)return;
  pointer.current={x:event.clientX,y:event.clientY,button:event.button,camera,distance:0,pointerId:event.pointerId};
  event.currentTarget.setPointerCapture(event.pointerId);
 }
 function move(event:PointerEvent<HTMLCanvasElement>){
  const drag=pointer.current;if(!drag||drag.pointerId!==event.pointerId)return;
  const dx=event.clientX-drag.x,dy=event.clientY-drag.y;
  drag.distance=Math.max(drag.distance,Math.hypot(dx,dy));
  setCamera(drag.button===1||event.shiftKey?pan(drag.camera,dx,dy):orbit(drag.camera,dx,-dy));
 }
 function up(event:PointerEvent<HTMLCanvasElement>){
  const drag=pointer.current;if(!drag||drag.pointerId!==event.pointerId)return;
  pointer.current=null;
  if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId);
  if(drag.distance>5||drag.button!==0)return;
  const rect=event.currentTarget.getBoundingClientRect();
  const x=((event.clientX-rect.left)/rect.width-.5)*2;
  const y=(.5-(event.clientY-rect.top)/rect.height)*2;
  const ray:Ray=screenRay(camera,x,y,rect.width/rect.height);
  const hit=pickRoom(floor.rooms,ray);
  if(hit)onSelectRoom(hit.roomId);
 }
 function wheel(event:WheelEvent<HTMLCanvasElement>){
  event.stopPropagation();setCamera(dolly(camera,event.deltaY));
 }
 return <div className="hqCanvasWrapper">
  {renderIssue&&<div role="alert" className="hqCanvasError">{renderIssue}</div>}
  <canvas ref={canvas} className="hqCanvas" aria-label="Architectural office. Drag to orbit, Shift-drag to pan, scroll to zoom, click a room."
   onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={()=>{pointer.current=null}}
   onWheel={wheel} onContextMenu={e=>e.preventDefault()}/>
  <p className="hqCanvasHint">Drag to orbit · Shift-drag to move · Wheel to zoom · Select a room</p>
 </div>;
}
