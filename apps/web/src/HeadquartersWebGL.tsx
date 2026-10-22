/** A genuinely GPU-backed 3D rendering surface for saved ANUM MINE office layouts.
 * This renderer consumes the SAME server-derived floor that the 2D fallback uses.
 * No hardcoded sample employee names or invented task status are allowed.
 */
import { useEffect, useRef, useState, type PointerEvent, type WheelEvent } from 'react';
import type { CameraState } from './lib/headquartersSpatial.ts';
import { cameraBasis, cameraEye, dolly, orbit, pan, pickRoom, screenRay, vsub, dot } from './lib/headquartersSpatial.ts';
import type { SceneFloor,Vec3 } from './lib/headquartersProjection.ts';
import { sceneMeshes, HeadquartersCanvas } from './HeadquartersCanvas';
type Props={floor:SceneFloor;camera:CameraState;selectedRoomId:string|null;
 night:boolean;setCamera:(camera:CameraState)=>void;onSelectRoom:(id:string)=>void};
type Drag={pointerId:number;x:number;y:number;origin:CameraState;moved:boolean;button:number};
const VERTEX_SHADER=
 `attribute vec2 aPosition;attribute vec4 aColor;varying vec4 vColor;
 void main(){vColor=aColor;gl_Position=vec4(aPosition,0.0,1.0);}`;
const FRAGMENT_SHADER=
 `precision mediump float;varying vec4 vColor;
 void main(){gl_FragColor=vColor;}`;
function compileShader(gl:WebGLRenderingContext,kind:number,source:string):WebGLShader{
 const shader=gl.createShader(kind);
 if(!shader)throw new Error('Unable to allocate GPU shader');
 gl.shaderSource(shader,source);gl.compileShader(shader);
 if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS)){
  const error=gl.getShaderInfoLog(shader)||'GPU shader compilation failed';
  gl.deleteShader(shader);throw new Error(error);
 }
 return shader;
}
function createProgram(gl:WebGLRenderingContext):WebGLProgram{
 const vertex=compileShader(gl,gl.VERTEX_SHADER,VERTEX_SHADER);
 const fragment=compileShader(gl,gl.FRAGMENT_SHADER,FRAGMENT_SHADER);
 const program=gl.createProgram();
 if(!program)throw new Error('Unable to create GPU program');
 gl.attachShader(program,vertex);gl.attachShader(program,fragment);
 gl.linkProgram(program);gl.deleteShader(vertex);gl.deleteShader(fragment);
 if(!gl.getProgramParameter(program,gl.LINK_STATUS)){
  const error=gl.getProgramInfoLog(program)||'GPU program linking failed';
  gl.deleteProgram(program);throw new Error(error);
 }
 return program;
}
function projectPoint(camera:CameraState,point:Vec3,aspect:number):Readonly<{x:number;y:number;depth:number}|null>{
 const basis=cameraBasis(camera);
 const relative=vsub(point,cameraEye(camera));
 const depth=dot(relative,basis.forward);
 if(depth<=.005)return null;
 const scale=1/Math.tan(camera.fov*Math.PI/360);
 return {x:dot(relative,basis.right)*scale/depth/aspect,
  y:dot(relative,basis.up)*scale/depth,depth};
}
export function buildGpuVertices(floor:SceneFloor,roomId:string|null,night:boolean,camera:CameraState,aspect:number):Float32Array{
 const quads=sceneMeshes(floor,roomId,night).flatMap(m=>m.quads);
 const projected=quads.map(face=>{
  const points=face.points.map(p=>projectPoint(camera,p,aspect));
  if(points.some(p=>p===null))return null;
  const vertices=points as {x:number;y:number;depth:number}[];
  return {vertices,color:face.color,alpha:face.alpha,
   depth:vertices.reduce((sum,p)=>sum+p.depth,0)/vertices.length};
 }).filter((value):value is NonNullable<typeof value>=>value!==null)
  .sort((a,b)=>b.depth-a.depth);
 const output:number[]=[];
 for(const face of projected){
  for(const i of [0,1,2,0,2,3]){
   const v=face.vertices[i];
   output.push(v.x,v.y,face.color[0]/255,face.color[1]/255,
    face.color[2]/255,face.alpha);
  }
 }
 return new Float32Array(output);
}
function draw(gl:WebGLRenderingContext,canvas:HTMLCanvasElement,program:WebGLProgram,
 buffer:WebGLBuffer,floor:SceneFloor,roomId:string|null,night:boolean,camera:CameraState):void{
 const box=canvas.getBoundingClientRect();
 const ratio=Math.min(2,window.devicePixelRatio||1);
 const w=Math.max(1,Math.floor(box.width*ratio));
 const h=Math.max(1,Math.floor(box.height*ratio));
 if(canvas.width!==w)canvas.width=w;
 if(canvas.height!==h)canvas.height=h;
 gl.viewport(0,0,w,h);
 gl.clearColor(night?.027:.09,night?.078:.17,night?.12:.21,1);
 gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
 const vertices=buildGpuVertices(floor,roomId,night,camera,w/h);
 gl.useProgram(program);gl.bindBuffer(gl.ARRAY_BUFFER,buffer);
 gl.bufferData(gl.ARRAY_BUFFER,vertices,gl.DYNAMIC_DRAW);
 const position=gl.getAttribLocation(program,'aPosition');
 const color=gl.getAttribLocation(program,'aColor');
 if(position<0||color<0)throw new Error('GPU program attribute not found');
 gl.enableVertexAttribArray(position);gl.vertexAttribPointer(position,2,gl.FLOAT,false,24,0);
 gl.enableVertexAttribArray(color);gl.vertexAttribPointer(color,4,gl.FLOAT,false,24,8);
 gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);
 gl.drawArrays(gl.TRIANGLES,0,vertices.length/6);
}
export function HeadquartersWebGL(props:Props){
 const {floor,camera,selectedRoomId,night,setCamera,onSelectRoom}=props;
 const canvas=useRef<HTMLCanvasElement>(null);
 const drag=useRef<Drag|null>(null);
 const [fallback,setFallback]=useState(false);
 const [gpuError,setGpuError]=useState<string|null>(null);
 const [canvasSize,setCanvasSize]=useState({width:1000,height:650});
 useEffect(()=>{
  const element=canvas.current;if(!element||fallback)return;
  const gl=element.getContext('webgl',{antialias:true,alpha:false,depth:false});
  if(!gl){setFallback(true);return}
  let program:WebGLProgram|null=null,buffer:WebGLBuffer|null=null;
  let observer:ResizeObserver|null=null;
  let scheduled:number|null=null;
  let disposed=false;
  const paint=()=>{
   if(disposed||!program||!buffer)return;
   if(scheduled!==null)cancelAnimationFrame(scheduled);
   scheduled=requestAnimationFrame(()=>{
    scheduled=null;
    if(disposed)return;
    try{draw(gl,element,program!,buffer!,floor,selectedRoomId,night,camera)}
    catch(error){setGpuError(error instanceof Error?error.message:'GPU rendering failed');setFallback(true)}
   });
  };
  const lost=(e:Event)=>{e.preventDefault();setGpuError('GPU context lost; showing accessible floor renderer');setFallback(true)};
  element.addEventListener('webglcontextlost',lost);
  try{
   program=createProgram(gl);
   buffer=gl.createBuffer();if(!buffer)throw new Error('GPU vertex buffer unavailable');
   observer=new ResizeObserver(()=>{
    const rect=element.getBoundingClientRect();
    setCanvasSize(old=>old.width===rect.width&&old.height===rect.height?old:{width:rect.width,height:rect.height});
    paint();
   });observer.observe(element);paint();
  }catch(error){setGpuError(error instanceof Error?error.message:'WebGL unavailable');setFallback(true)}
  return()=>{
   disposed=true;if(scheduled!==null)cancelAnimationFrame(scheduled);
   observer?.disconnect();element.removeEventListener('webglcontextlost',lost);
   if(buffer)gl.deleteBuffer(buffer);if(program)gl.deleteProgram(program);
  };
 },[floor,camera,night,selectedRoomId,fallback]);
 const onDown=(event:PointerEvent<HTMLCanvasElement>)=>{
  if(event.button!==0&&event.button!==1&&event.button!==2)return;
  drag.current={pointerId:event.pointerId,x:event.clientX,y:event.clientY,
   origin:camera,moved:false,button:event.button};
  event.currentTarget.setPointerCapture(event.pointerId);
 };
 const onMove=(event:PointerEvent<HTMLCanvasElement>)=>{
  const current=drag.current;
  if(!current||current.pointerId!==event.pointerId)return;
  const dx=event.clientX-current.x,dy=event.clientY-current.y;
  if(Math.hypot(dx,dy)>5)current.moved=true;
  setCamera(current.button===1||event.shiftKey?pan(current.origin,dx,dy):orbit(current.origin,dx,-dy));
 };
 const onUp=(event:PointerEvent<HTMLCanvasElement>)=>{
  const current=drag.current;if(!current||current.pointerId!==event.pointerId)return;
  drag.current=null;
  if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId);
  if(current.moved||current.button!==0)return;
  const rect=event.currentTarget.getBoundingClientRect();
  const x=((event.clientX-rect.left)/rect.width-.5)*2;
  const y=(.5-(event.clientY-rect.top)/rect.height)*2;
  const hit=pickRoom(floor.rooms,screenRay(camera,x,y,rect.width/rect.height));
  if(hit)onSelectRoom(hit.roomId);
 };
 const roomMarkers=floor.rooms.map(room=>{
  const point=projectPoint(camera,{x:room.x+room.width*.5,y:room.height*.70,z:room.z+room.depth*.48},
   canvasSize.width/Math.max(1,canvasSize.height));
  if(!point||Math.abs(point.x)>.96||Math.abs(point.y)>.96)return null;
  const desks=floor.desks.filter(d=>d.roomId===room.id);
  const active=desks.filter(d=>d.agent!==null).length;
  return {room,active,assigned:desks.filter(d=>d.assignmentVisible).length,
    left:(point.x+1)*50,top:(1-point.y)*50};
 }).filter((value):value is NonNullable<typeof value>=>value!==null);
 if(fallback)return <div className="hqGpuFallback">
  {gpuError&&<span className="hqGpuNote" role="status">{gpuError}</span>}
  <HeadquartersCanvas {...props}/>
 </div>;
 return <div className="hqImmersiveStage"><canvas ref={canvas} className="hqCanvas" aria-label="GPU-rendered 3D office. Drag to orbit, Shift-drag to pan, scroll to zoom, click a room"
  onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp}
  onPointerCancel={()=>{drag.current=null}}
  onWheel={(event:WheelEvent<HTMLCanvasElement>)=>{event.stopPropagation();setCamera(dolly(camera,event.deltaY))}}
  onContextMenu={event=>event.preventDefault()}/>
  <div className="hqSceneLabels" aria-label="Interactive room labels">
   {roomMarkers.map(({room,active,assigned,left,top})=><button key={room.id} type="button"
    className={`hqSceneRoomLabel ${room.id===selectedRoomId?"selected":""}`}
    style={{left:`${left}%`,top:`${top}%`}} onClick={()=>onSelectRoom(room.id)}
    aria-label={`Select ${room.label}, ${assigned} assigned workstations` }>
    <span className="hqRoomLabelName">{room.label}</span>
    <span className="hqRoomLabelMeta">{room.kind} · {active} activity records</span>
   </button>)}
  </div></div>;
}
