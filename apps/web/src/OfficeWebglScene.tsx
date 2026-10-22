/** ANUM MINE procedural WebGL headquarters.
 *
 * True geometry rendered on the GPU, not screenshots or a rotated HTML floor.
 * No fake workspace presence: people/activity remain managed by authenticated
 * operations panels. All room selection is explicitly user-driven.
 */
import { useEffect, useRef, useState } from 'react';
import './officeWebglScene.css';

export type WebglRoom = {
  id: string; name: string; x: number; y: number;
  width: number; height: number; accent: string;
};

type Vec3 = [number, number, number];
type Face = {points: Vec3[]; color: [number,number,number]};
type Projected = {x:number;y:number;depth:number};
const clamp=(value:number,min:number,max:number)=>Math.max(min,Math.min(max,value));
function rgb(hex:string):[number,number,number]{
  const match=/^#([0-9a-f]{6})$/i.exec(hex);
  if(!match)return [0.24,0.65,0.62];
  const n=parseInt(match[1],16);
  return [((n>>16)&255)/255,((n>>8)&255)/255,(n&255)/255];
}
function shade(color:[number,number,number],factor:number):[number,number,number]{
  return [clamp(color[0]*factor,0,1),clamp(color[1]*factor,0,1),clamp(color[2]*factor,0,1)];
}
function cuboid(out:Face[],x:number,z:number,w:number,d:number,h:number,color:[number,number,number],base=0):void{
  const a:[number,number,number]=[x,base,z],b:[number,number,number]=[x+w,base,z],
    c:[number,number,number]=[x+w,base,z+d],e:[number,number,number]=[x,base,z+d],
    A:[number,number,number]=[x,base+h,z],B:[number,number,number]=[x+w,base+h,z],
    C:[number,number,number]=[x+w,base+h,z+d],D:[number,number,number]=[x,base+h,z+d];
  out.push({points:[A,B,C,D],color:shade(color,1.08)},
    {points:[a,b,B,A],color:shade(color,.68)},
    {points:[b,c,C,B],color:shade(color,.83)},
    {points:[c,e,D,C],color:shade(color,.57)},
    {points:[e,a,A,D],color:shade(color,.79)});
}
/** Pure scene generator. Workspace entities are never invented or persisted. */
export function buildOfficeGeometry(rooms:readonly WebglRoom[],night:boolean):Face[]{
  const faces:Face[]=[];
  cuboid(faces,-1.28,-.96,2.56,1.92,.085,night?[.2,.33,.37]:[.68,.77,.74],-.14);
  cuboid(faces,-1.17,-.86,2.34,1.72,.02,night?[.43,.52,.55]:[.84,.87,.81],-.045);
  // Pedestrian corridor and a central raised entrance threshold.
  cuboid(faces,-.12,-.79,.24,1.58,.009,night?[.28,.43,.47]:[.65,.74,.68],-.022);
  rooms.forEach((room,i)=>{
    const x=(room.x-50)*.0217,z=(room.y-50)*.0158;
    const w=room.width*.0217,d=room.height*.0158;
    const accent=rgb(room.accent),floor: [number,number,number]=night?[.22,.37,.42]:[.83,.89,.86];
    cuboid(faces,x,z,w,d,.035,floor,.002);
    // Three exterior partitions leave the near/front wall open so interiors stay visible.
    const glass:[number,number,number]=night?[.25,.47,.53]:[.53,.73,.73];
    cuboid(faces,x,z,w,.015,.15,glass,.034);
    cuboid(faces,x,z,.015,d,.15,glass,.034);
    cuboid(faces,x+w-.015,z,.015,d,.15,glass,.034);
    const deskColor:[number,number,number]=night?[.43,.39,.34]:[.66,.54,.44];
    // Two furnished workstations with monitor, keyboard and chair.
    for(let j=0;j<2;j++){
      const dx=x+w*(j===0?.23:.62),dz=z+d*(j===0?.39:.67);
      const dw=Math.max(.045,Math.min(.14,w*.23));
      const dd=Math.max(.04,Math.min(.1,d*.27));
      cuboid(faces,dx,dz,dw,dd,.065,deskColor,.04);
      cuboid(faces,dx+dw*.4,dz+dd*.12,dw*.24,.013,.063,[.1,.22,.29],.11);
      cuboid(faces,dx+dw*.22,dz+dd*.75,dw*.52,.012,.005,[.72,.76,.75],.109);
      cuboid(faces,dx+dw*.28,dz+dd*1.3,dw*.4,dd*.44,.035,accent,.03);
    }
    // Distinct architectural feature: reception desk, executive conference table or focus pod.
    if(room.id.includes('reception')||room.id==='welcome'){
      cuboid(faces,x+w*.25,z+d*.79,w*.52,d*.1,.07,accent,.04);
    } else if(room.id.includes('meeting')||room.id.includes('boardroom')||room.id==='strategy'){
      cuboid(faces,x+w*.37,z+d*.38,w*.3,d*.18,.055,accent,.05);
    }
    // Accent pillar differentiates departments without simulated avatars.
    cuboid(faces,x+w*.05,z+d*.08,.018,.018,.19,accent,.035);
    if(i===0)cuboid(faces,x+w*.72,z+d*.75,.022,.022,.11,[.25,.53,.38],.035);
  });
  return faces;
}
/** Camera mathematics are exported for regression tests and room picking. */
export function projectOfficePoint(point:Vec3,angle:number,tilt:number,zoom:number):Projected{
  const yaw=(angle-15)*Math.PI/180,pitch=(48+tilt)*Math.PI/180;
  const [x,y,z]=point;
  const rx=x*Math.cos(yaw)-z*Math.sin(yaw);
  const rz=x*Math.sin(yaw)+z*Math.cos(yaw);
  const ry=y*Math.cos(pitch)-rz*Math.sin(pitch);
  const depth=y*Math.sin(pitch)+rz*Math.cos(pitch);
  return {x:rx*zoom*.64,y:ry*zoom*.90,depth};
}
export function getRoomHit(
  rooms:readonly WebglRoom[],x:number,y:number,angle:number,tilt:number,zoom:number,
):string|null{
  let nearest:{id:string;distance:number}|null=null;
  for(const r of rooms){
    const center:Vec3=[(r.x+r.width/2-50)*.0217,.13,(r.y+r.height/2-50)*.0158];
    const p=projectOfficePoint(center,angle,tilt,zoom);
    const d=Math.hypot(x-p.x,y-p.y);
    if(d<.19*zoom&&(!nearest||d<nearest.distance))nearest={id:r.id,distance:d};
  }
  return nearest?.id??null;
}
function shader(gl:WebGLRenderingContext,kind:number,source:string):WebGLShader{
  const result=gl.createShader(kind);
  if(!result)throw new Error('WebGL shader unavailable');
  gl.shaderSource(result,source);gl.compileShader(result);
  if(!gl.getShaderParameter(result,gl.COMPILE_STATUS)){
    const message=gl.getShaderInfoLog(result)??'Shader compilation failed';
    gl.deleteShader(result);throw new Error(message);
  }
  return result;
}
type Props={rooms:readonly WebglRoom[];selected:string;night:boolean;angle:number;tilt:number;zoom:number;onSelect:(id:string)=>void};
export function OfficeWebglScene({rooms,selected,night,angle,tilt,zoom,onSelect}:Props){
  const canvas=useRef<HTMLCanvasElement>(null);
  const [available,setAvailable]=useState(true);
  useEffect(()=>{
    const element=canvas.current;if(!element)return;
    const gl=element.getContext('webgl',{antialias:true,alpha:true,preserveDrawingBuffer:false});
    if(!gl){setAvailable(false);return}
    let program:WebGLProgram|null=null,buffer:WebGLBuffer|null=null;
    try{
      const vertex=shader(gl,gl.VERTEX_SHADER,`attribute vec2 position;attribute vec3 color;varying vec3 tint;void main(){gl_Position=vec4(position,0.0,1.0);tint=color;}`);
      const fragment=shader(gl,gl.FRAGMENT_SHADER,`precision mediump float;varying vec3 tint;void main(){gl_FragColor=vec4(tint,1.0);}`);
      program=gl.createProgram();if(!program)throw new Error('WebGL program unavailable');
      gl.attachShader(program,vertex);gl.attachShader(program,fragment);gl.linkProgram(program);
      gl.deleteShader(vertex);gl.deleteShader(fragment);
      if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(program)??'Shader linking failed');
      buffer=gl.createBuffer();if(!buffer)throw new Error('WebGL buffer unavailable');
      const faces=buildOfficeGeometry(rooms,night);
      const sorted=faces.map(f=>({...f,projected:f.points.map(p=>projectOfficePoint(p,angle,tilt,zoom))}))
        .sort((a,b)=>b.projected.reduce((sum,p)=>sum+p.depth,0)-a.projected.reduce((sum,p)=>sum+p.depth,0));
      const vertices:number[]=[];
      for(const face of sorted){
        const chosen=face.projected;
        for(const id of [0,1,2,0,2,3]){
          const p=chosen[id];vertices.push(p.x,p.y,...face.color);
        }
      }
      const pixels=new Float32Array(vertices);
      const paint=()=>{
        const ratio=Math.min(devicePixelRatio||1,2);
        const rect=element.getBoundingClientRect();
        element.width=Math.max(1,Math.round(rect.width*ratio));
        element.height=Math.max(1,Math.round(rect.height*ratio));
        gl.viewport(0,0,element.width,element.height);
        gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
        gl.useProgram(program);gl.bindBuffer(gl.ARRAY_BUFFER,buffer);
        gl.bufferData(gl.ARRAY_BUFFER,pixels,gl.STATIC_DRAW);
        const pos=gl.getAttribLocation(program!,'position'),color=gl.getAttribLocation(program!,'color');
        gl.enableVertexAttribArray(pos);gl.vertexAttribPointer(pos,2,gl.FLOAT,false,20,0);
        gl.enableVertexAttribArray(color);gl.vertexAttribPointer(color,3,gl.FLOAT,false,20,8);
        gl.drawArrays(gl.TRIANGLES,0,pixels.length/5);
      };
      paint();
      const resize=new ResizeObserver(paint);resize.observe(element);
      return()=>{resize.disconnect();gl.deleteBuffer(buffer);gl.deleteProgram(program)};
    }catch{
      setAvailable(false);if(buffer)gl.deleteBuffer(buffer);if(program)gl.deleteProgram(program);
    }
  },[rooms,night,angle,tilt,zoom]);
  if(!available)return <div className="officeWebglUnavailable" role="status">WebGL is unavailable on this device. Use the Plan mode for room navigation.</div>;
  return <div className="officeWebglStage">
    <canvas ref={canvas} className="officeWebglCanvas" aria-label="Interactive 3D office. Drag to orbit, scroll to zoom, click a room to visit." onClick={e=>{
      const rect=e.currentTarget.getBoundingClientRect();
      const x=((e.clientX-rect.left)/rect.width-.5)*2,y=(.5-(e.clientY-rect.top)/rect.height)*2;
      const id=getRoomHit(rooms,x,y,angle,tilt,zoom);if(id)onSelect(id);
    }}/>
    <div className="officeWebglLegend">
      <span>3D HEADQUARTERS</span><strong>{rooms.find(r=>r.id===selected)?.name??'Select a room'}</strong>
      <small>GPU-rendered architectural scene · Select a room or use the floor directory</small>
      <div className="officeWebglRoomPills">{rooms.map(r=><button type="button" key={r.id} aria-pressed={r.id===selected} onClick={()=>onSelect(r.id)}>{r.name}</button>)}</div>
    </div>
  </div>;
}
