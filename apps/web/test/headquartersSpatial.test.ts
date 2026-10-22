import assert from 'node:assert/strict';
import { test } from 'node:test';
import { cameraBasis,cameraEye,beginTour,dolly,focusRoom,hitPlane,initialCamera,
  intersectRoomFloor,isCameraAtRest,nextTourStop,orbit,overlapArea,pan,pauseTour,
  pickRoom,screenRay,stepCamera,validateFloorGeometry,visitTourStop,
  resumeTour,normalize,vadd,vscale,vsub,dot,length, type CameraState,
} from '../src/lib/headquartersSpatial.ts';
import type { RoomGeometry,SceneFloor } from '../src/lib/headquartersProjection.ts';
const room:RoomGeometry={id:'room-a',buildingId:'b',floorId:'f',label:'Main Reception',
 x:0,z:0,width:6,depth:4,height:3,capacity:8,revision:1,deskIds:[],kind:'reception',evidence:'registry'};
const floor:SceneFloor={id:'f',name:'Ground',level:0,rooms:[room],desks:[],bounds:{width:20,depth:20}};
const camera=initialCamera(floor);
test('camera initializes with finite target and valid distances',()=>{
 assert.ok(Number.isFinite(camera.distance));
 assert.ok(camera.distance>camera.minDistance);
 assert.ok(camera.distance<camera.maxDistance);
 assert.equal(camera.target.y,0);
});
test('orbit stays within pitch limits',()=>{
 assert.equal(orbit(camera,1000000,1000000).pitch,87);
 assert.equal(orbit(camera,0,-1000000).pitch,3);
 assert.ok(orbit(camera,-30,15).yaw>=0);
 assert.ok(orbit(camera,-30,15).yaw<360);
});
test('zoom maintains declared zoom bounds',()=>{
 assert.equal(dolly(camera,1000000).distance,camera.maxDistance);
 assert.equal(dolly(camera,-1000000).distance,camera.minDistance);
});
test('panning translates the target in the office plane',()=>{
 const p=pan(camera,120,50);
 assert.notDeepEqual(p.target,camera.target);
 assert.equal(p.target.y,camera.target.y);
});
test('room camera focus reaches the room center',()=>{
 const focused=focusRoom(camera,room);
 assert.equal(focused.target.x,3);
 assert.equal(focused.target.z,2);
 assert.equal(focused.pitch,57);
});
test('basis vectors remain unit vectors',()=>{
 const basis=cameraBasis(camera);
 for(const vector of Object.values(basis))assert.ok(Math.abs(length(vector)-1)<.000001);
 assert.ok(Math.abs(dot(basis.right,basis.up))<.000001);
 assert.ok(Math.abs(dot(basis.forward,basis.up))<.000001);
});
test('screen ray rejects invalid aspect ratios',()=>{
 assert.throws(()=>screenRay(camera,0,0,0),/aspect ratio/);
 assert.throws(()=>screenRay(camera,0,0,Number.NaN),/aspect ratio/);
});
test('center view ray aims towards target',()=>{
 const ray=screenRay(camera,0,0,16/9);
 const toward=normalize(vsub(camera.target,cameraEye(camera)));
 assert.ok(dot(ray.direction,toward)>.9999);
});
test('ray-plane intersection exists when looking toward floor',()=>{
 const ray=screenRay(camera,0,0,1.4);
 const hit=hitPlane(ray,0);
 assert.ok(hit);
 assert.ok(Math.abs(hit.y)<.000001);
});
test('ray-plane intersection ignores parallel rays',()=>{
 const hit=hitPlane({origin:{x:0,y:2,z:0},direction:{x:1,y:0,z:0}});
 assert.equal(hit,null);
});
test('floor picking refuses to select a room outside the pointer target',()=>{
 const ray={origin:{x:100,y:5,z:100},direction:{x:0,y:-1,z:0}};
 assert.equal(intersectRoomFloor(ray,room),null);
 assert.equal(pickRoom([room],ray),null);
});
test('floor picking includes rooms when ray is directly above',()=>{
 const ray={origin:{x:3,y:5,z:2},direction:{x:0,y:-1,z:0}};
 assert.equal(pickRoom([room],ray)?.roomId,'room-a');
});
test('overlap warnings are limited to positive intersecting areas',()=>{
 const far={...room,id:'far',x:7,z:5};
 assert.equal(overlapArea(room,far),0);
 assert.equal(validateFloorGeometry([room,far]).length,0);
 const overlap={...room,id:'overlap',x:2,z:1};
 assert.ok(overlapArea(room,overlap)>0);
 assert.ok(validateFloorGeometry([room,overlap]).some(x=>x.code==='ROOM_OVERLAP'));
});
test('out-of-bounds rooms are surfaced rather than silently clipped',()=>{
 const violations=validateFloorGeometry([room],{left:1,right:2,top:1,bottom:2});
 assert.ok(violations.some(x=>x.code==='OUT_OF_BOUNDS'));
});
test('camera easing is deterministic with zero elapsed time',()=>{
 const target=orbit(camera,20,10);
 assert.ok(isCameraAtRest(camera,stepCamera(camera,target,0)));
 assert.ok(!isCameraAtRest(camera,target));
 assert.ok(!isCameraAtRest(stepCamera(camera,target,50),camera));
});
test('vector operations obey basic invariants',()=>{
 const a={x:2,y:3,z:4},b={x:5,y:6,z:7};
 assert.deepEqual(vadd(a,b),{x:7,y:9,z:11});
 assert.deepEqual(vsub(b,a),{x:3,y:3,z:3});
 assert.deepEqual(vscale(a,2),{x:4,y:6,z:8});
 assert.throws(()=>normalize({x:0,y:0,z:0}),/zero vector/);
});
test('guided tour does not invent stops',()=>{
 const route=beginTour(['reception','office','reception']);
 assert.deepEqual(route.rooms,['reception','office']);
 assert.equal(nextTourStop(route).current,1);
 assert.equal(nextTourStop(nextTourStop(route)).mode,'stopped');
 assert.equal(pauseTour(route).mode,'paused');
 assert.equal(resumeTour(pauseTour(route)).mode,'running');
 assert.equal(visitTourStop(route,'office').current,1);
 assert.throws(()=>visitTourStop(route,'unknown'),/not in/);
});
