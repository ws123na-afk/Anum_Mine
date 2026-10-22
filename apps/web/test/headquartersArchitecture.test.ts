import assert from 'node:assert/strict';
import {test} from 'node:test';
import {sceneMeshes,roomMeshes} from '../src/HeadquartersCanvas.tsx';
import type {RoomGeometry,SceneFloor} from '../src/lib/headquartersProjection.ts';

const room:RoomGeometry={
 id:'meeting-1',floorId:'floor',buildingId:'building',label:'Boardroom',kind:'meeting',
 x:0,z:0,width:9,depth:6,height:3.2,capacity:12,revision:1,deskIds:[],evidence:'registry'
};
test('boardroom interior is a real geometry model, not only an empty slab',()=>{
 const keys=roomMeshes(room,[],false,false).flatMap(mesh=>mesh.quads.map(face=>face.key));
 assert.ok(keys.some(key=>key.includes('boardroom-table')));
 assert.ok(keys.some(key=>key.includes('chair')));
 assert.ok(keys.some(key=>key.includes('glass')));
 assert.ok(keys.some(key=>key.includes('plant')));
});
test('saved dimensions control architectural room meshes',()=>{
 const model=roomMeshes({...room,width:12,depth:7},[],true,false);
 const points=model.flatMap(m=>m.quads.flatMap(q=>q.points));
 assert.ok(points.some(p=>p.x>9));
 assert.ok(points.some(p=>p.z>6));
});
test('architectural scene never invents rooms or employees',()=>{
 const floor:SceneFloor={id:'floor',name:'Main',level:0,rooms:[],desks:[],bounds:{width:24,depth:24}};
 const meshes=sceneMeshes(floor,null,false);
 assert.ok(meshes.length>1);
 assert.equal(meshes.filter(mesh=>mesh.roomId!==null).length,0);
});
