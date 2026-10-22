/** Spatial geometry, office floor planning, and camera behavior. */
import type { RoomGeometry, SceneFloor, Vec2, Vec3 } from './headquartersProjection.ts';
export type Bounds = Readonly<{left:number;right:number;top:number;bottom:number}>;
export type CameraState = Readonly<{
 target:Vec3; distance:number; yaw:number; pitch:number; fov:number;
 minDistance:number;maxDistance:number;
}>;
export type Ray = Readonly<{origin:Vec3;direction:Vec3}>;
export type RoomIntersection = Readonly<{roomId:string;distance:number;hit:Vec3}>;
export type RoomTile = Readonly<{id:string; label:string;polygon:readonly Vec2[]; door:Vec2;kind:string}>;
export type LayoutConflict = Readonly<{a:string;b:string;area:number;code:'ROOM_OVERLAP'|'OUT_OF_BOUNDS'}>;
export const EPSILON=1e-8;
const rad=(degrees:number)=>degrees*Math.PI/180;
const clamp=(value:number,min:number,max:number)=>Math.max(min,Math.min(max,value));
export function vadd(a:Vec3,b:Vec3):Vec3{return {x:a.x+b.x,y:a.y+b.y,z:a.z+b.z}}
export function vsub(a:Vec3,b:Vec3):Vec3{return {x:a.x-b.x,y:a.y-b.y,z:a.z-b.z}}
export function vscale(a:Vec3,n:number):Vec3{return {x:a.x*n,y:a.y*n,z:a.z*n}}
export function dot(a:Vec3,b:Vec3):number{return a.x*b.x+a.y*b.y+a.z*b.z}
export function cross(a:Vec3,b:Vec3):Vec3{return {x:a.y*b.z-a.z*b.y,y:a.z*b.x-a.x*b.z,z:a.x*b.y-a.y*b.x}}
export function length(a:Vec3):number{return Math.hypot(a.x,a.y,a.z)}
export function normalize(a:Vec3):Vec3{const n=length(a);if(n<EPSILON)throw new Error('Cannot normalize a zero vector');return vscale(a,1/n)}
export function lerp(a:number,b:number,weight:number):number{return a+(b-a)*weight}
export function lerpVec(a:Vec3,b:Vec3,weight:number):Vec3{return {x:lerp(a.x,b.x,weight),y:lerp(a.y,b.y,weight),z:lerp(a.z,b.z,weight)}}
export function assertCamera(camera:CameraState):void{
 const nums=[camera.distance,camera.yaw,camera.pitch,camera.fov,camera.minDistance,camera.maxDistance,...Object.values(camera.target)];
 if(nums.some(v=>!Number.isFinite(v)))throw new Error('Camera contains non-finite coordinates');
 if(camera.minDistance<=0||camera.maxDistance<=camera.minDistance)throw new Error('Invalid camera distance bounds');
 if(camera.distance<camera.minDistance||camera.distance>camera.maxDistance)throw new Error('Camera distance outside allowed range');
 if(camera.pitch<3||camera.pitch>87)throw new Error('Camera pitch outside navigable range');
 if(camera.fov<20||camera.fov>90)throw new Error('Camera field of view outside supported range');
}
export function initialCamera(floor:SceneFloor):CameraState{
 const bounds=effectiveBounds(floor.rooms);
 const width=Math.max(10,bounds.right-bounds.left);
 const depth=Math.max(8,bounds.bottom-bounds.top);
 const target={x:(bounds.left+bounds.right)/2,y:0,z:(bounds.top+bounds.bottom)/2};
 const distance=clamp(Math.max(width,depth)*1.55,17,180);
 return {target,distance,yaw:35,pitch:48,fov:43,minDistance:3,maxDistance:250};
}
export function effectiveBounds(rooms:readonly RoomGeometry[]):Bounds{
 if(!rooms.length)return {left:-10,right:10,top:-8,bottom:8};
 return {left:Math.min(...rooms.map(r=>r.x)),right:Math.max(...rooms.map(r=>r.x+r.width)),
  top:Math.min(...rooms.map(r=>r.z)),bottom:Math.max(...rooms.map(r=>r.z+r.depth))};
}
export function orbit(camera:CameraState,deltaX:number,deltaY:number):CameraState{
 const next={...camera,yaw:((camera.yaw+deltaX*.28)%360+360)%360,
  pitch:clamp(camera.pitch+deltaY*.22,3,87)};
 assertCamera(next);return next;
}
export function dolly(camera:CameraState,delta:number):CameraState{
 const next={...camera,distance:clamp(camera.distance*Math.exp(delta*.001),camera.minDistance,camera.maxDistance)};
 assertCamera(next);return next;
}
export function pan(camera:CameraState,deltaX:number,deltaY:number):CameraState{
 const phi=rad(camera.yaw);const step=camera.distance*.001;
 const right={x:Math.cos(phi),y:0,z:-Math.sin(phi)};
 const front={x:Math.sin(phi),y:0,z:Math.cos(phi)};
 const move=vadd(vscale(right,-deltaX*step),vscale(front,deltaY*step));
 const next={...camera,target:vadd(camera.target,move)};
 assertCamera(next);return next;
}
export function focusRoom(camera:CameraState,room:RoomGeometry):CameraState{
 const next={...camera,target:{x:room.x+room.width/2,y:0,z:room.z+room.depth/2},
  distance:clamp(Math.max(room.width,room.depth)*2.3,camera.minDistance,camera.maxDistance),pitch:57};
 assertCamera(next);return next;
}
export function viewDirection(camera:CameraState):Vec3{
 const yaw=rad(camera.yaw),pitch=rad(camera.pitch);
 return normalize({x:Math.sin(yaw)*Math.cos(pitch),y:Math.sin(pitch),z:Math.cos(yaw)*Math.cos(pitch)});
}
export function cameraEye(camera:CameraState):Vec3{return vadd(camera.target,vscale(viewDirection(camera),camera.distance))}
export function cameraBasis(camera:CameraState):Readonly<{forward:Vec3;right:Vec3;up:Vec3}>{
 const forward=normalize(vsub(camera.target,cameraEye(camera)));
 const right=normalize(cross(forward,{x:0,y:1,z:0}));
 const up=normalize(cross(right,forward));
 return {forward,right,up};
}
export function screenRay(camera:CameraState,nx:number,ny:number,aspect:number):Ray{
 if(aspect<=0||!Number.isFinite(aspect))throw new Error('Invalid camera aspect ratio');
 const basis=cameraBasis(camera);
 const tangent=Math.tan(rad(camera.fov)*.5);
 const direction=normalize(vadd(basis.forward,
  vadd(vscale(basis.right,nx*tangent*aspect),vscale(basis.up,ny*tangent))));
 return {origin:cameraEye(camera),direction};
}
export function hitPlane(ray:Ray,height=0):Vec3|null{
 if(Math.abs(ray.direction.y)<EPSILON)return null;
 const t=(height-ray.origin.y)/ray.direction.y;
 if(t<0)return null;
 return vadd(ray.origin,vscale(ray.direction,t));
}
export function intersectRoomFloor(ray:Ray,room:RoomGeometry):RoomIntersection|null{
 const at=hitPlane(ray,.03);
 if(!at)return null;
 if(at.x<room.x||at.x>room.x+room.width||at.z<room.z||at.z>room.z+room.depth)return null;
 return {roomId:room.id,distance:length(vsub(at,ray.origin)),hit:at};
}
export function pickRoom(rooms:readonly RoomGeometry[],ray:Ray):RoomIntersection|null{
 let result:RoomIntersection|null=null;
 for(const room of rooms){
  const hit=intersectRoomFloor(ray,room);
  if(hit&&(!result||hit.distance<result.distance))result=hit;
 }
 return result;
}
export function polygonForRoom(room:RoomGeometry):RoomTile{
 const {x,z,width,depth}=room;
 return {id:room.id,label:room.label,kind:room.kind,
  polygon:[{x,y:z},{x:x+width,y:z},{x:x+width,y:z+depth},{x,y:z+depth}],
  door:{x:x+width*.5,y:z+depth}};
}
export function overlapArea(a:RoomGeometry,b:RoomGeometry):number{
 const w=Math.max(0,Math.min(a.x+a.width,b.x+b.width)-Math.max(a.x,b.x));
 const h=Math.max(0,Math.min(a.z+a.depth,b.z+b.depth)-Math.max(a.z,b.z));
 return w*h;
}
export function validateFloorGeometry(rooms:readonly RoomGeometry[],maxBounds?:Bounds):LayoutConflict[]{
 const issues:LayoutConflict[]=[];
 for(let i=0;i<rooms.length;i++){
  const room=rooms[i];
  if(maxBounds&&(room.x<maxBounds.left||room.z<maxBounds.top||room.x+room.width>maxBounds.right||room.z+room.depth>maxBounds.bottom)){
   issues.push({a:room.id,b:'floor',area:0,code:'OUT_OF_BOUNDS'});
  }
  for(let j=i+1;j<rooms.length;j++){
   const overlap=overlapArea(room,rooms[j]);
   if(overlap>.0001)issues.push({a:room.id,b:rooms[j].id,area:overlap,code:'ROOM_OVERLAP'});
  }
 }
 return issues;
}
export function fitCameraToFloors(floors:readonly SceneFloor[]):CameraState{
 if(!floors.length)throw new Error('Cannot focus an empty building');
 const rooms=floors.flatMap(f=>f.rooms);
 const floor={...floors[0],rooms};
 return initialCamera(floor);
}
export function isCameraAtRest(camera:CameraState,target:CameraState,tolerance=.0001):boolean{
 return Math.abs(camera.distance-target.distance)<=tolerance&&Math.abs(camera.yaw-target.yaw)<=tolerance&&
  Math.abs(camera.pitch-target.pitch)<=tolerance&&length(vsub(camera.target,target.target))<=tolerance;
}
export function stepCamera(camera:CameraState,target:CameraState,elapsedMs:number,halfLifeMs=130):CameraState{
 const weight=1-Math.pow(.5,clamp(elapsedMs,0,1000)/Math.max(1,halfLifeMs));
 const next={...camera,target:lerpVec(camera.target,target.target,weight),
  distance:lerp(camera.distance,target.distance,weight),
  yaw:lerp(camera.yaw,target.yaw,weight),pitch:lerp(camera.pitch,target.pitch,weight),
  fov:lerp(camera.fov,target.fov,weight)};
 assertCamera(next);return next;
}
export type TourRoute = Readonly<{rooms:readonly string[];current:number;mode:'stopped'|'running'|'paused'}>;
export function beginTour(ids:readonly string[]):TourRoute{
 return {rooms:[...new Set(ids)],current:0,mode:ids.length?'running':'stopped'};
}
export function nextTourStop(route:TourRoute):TourRoute{
 if(route.mode==='stopped'||!route.rooms.length)return route;
 const next=route.current+1;
 return next>=route.rooms.length?{...route,mode:'stopped'}:{...route,current:next};
}
export function visitTourStop(route:TourRoute,roomId:string):TourRoute{
 const index=route.rooms.indexOf(roomId);
 if(index<0)throw new Error('Selected room is not in the permitted tour');
 return {...route,current:index,mode:'running'};
}
export function pauseTour(route:TourRoute):TourRoute{return {...route,mode:'paused'}}
export function resumeTour(route:TourRoute):TourRoute{return route.rooms.length?{...route,mode:'running'}:route}
