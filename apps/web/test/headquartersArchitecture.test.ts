import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';

// The unit runner supports .ts but cannot import TSX/React renderers directly.
// Verify renderer integration contract without invoking the DOM or fabricating workers.
const src=readFileSync(new URL('../src/HeadquartersCanvas.tsx',import.meta.url),'utf8');
const scene=readFileSync(new URL('../src/lib/buildingScene.ts',import.meta.url),'utf8');
const view=readFileSync(new URL('../src/BuildingView.tsx',import.meta.url),'utf8');

test('office architecture renders furnished rooms, plants and glazed partitions',()=>{
 for(const key of ['boardroom-table','chair','plant','south-glass','reception-counter',
   'executive-desk','operations-worktop','shelf','sofa']){
  assert.ok(src.includes(key),`missing architectural fitting ${key}`);
 }
});
test('renderer always derives interior objects from server-backed rooms',()=>{
 assert.match(src,/for\(const room of rooms\)/);
 assert.match(src,/roomMeshes\(room,desks,night,selectedRoomId===room.id\)/);
 assert.match(scene,/for \(const room of floor\.rooms\)/);
 assert.match(view,/for \(const room of floor\.rooms\)/);
 assert.match(view,/onClick=\{props\.onPick\}/);
});
test('room markers describe observed activity and do not fake occupancy',()=>{
 assert.match(view,/presenceForDesk\(presence, d\)\?\.status === 'working'/);
 assert.match(view,/desks\.filter\(\(d\) => d\.agentId \|\| presenceForDesk\(presence, d\)\)\.length/);
});
