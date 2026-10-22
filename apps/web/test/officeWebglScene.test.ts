import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';

const renderer=readFileSync(new URL('../src/OfficeWebglScene.tsx',import.meta.url),'utf8');
const tour=readFileSync(new URL('../src/OfficeTourView.tsx',import.meta.url),'utf8');
const stylesheet=readFileSync(new URL('../src/officeWebglScene.css',import.meta.url),'utf8');

test('native WebGL geometry renderer uses GPU shaders rather than image slideshow',()=>{
  for(const expression of [/getContext\('webgl'/,/gl\.drawArrays\(gl\.TRIANGLES/,/createShader/,/buildOfficeGeometry/,/projectOfficePoint/,/getRoomHit/]){
    assert.match(renderer,expression);
  }
  assert.doesNotMatch(renderer, /Math\.random\(|<img|https?:\/\//);
});
test('scene preserves a plan fallback and accessible room selection',()=>{
  assert.match(tour,/sceneMode==='webgl'\?<OfficeWebglScene/);
  assert.match(tour,/sceneMode==='webgl'\?'3D Scene/);
  assert.match(renderer,/aria-label="Interactive 3D office/);
  assert.match(renderer,/aria-pressed=\{r\.id===selected\}/);
  assert.match(stylesheet, /officeWebglRoomPills/);
});
test('3D rooms are backed by same site and floor selections as the plan',()=>{
  assert.match(tour,/rooms=\{visible\}/);
  assert.match(tour,/visible\.find\(r=>r\.id===id\)/);
  assert.match(tour,/guide\(room\)/);
});
