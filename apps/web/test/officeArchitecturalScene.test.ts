import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const scene = readFileSync(new URL('../src/OfficeTourView.tsx', import.meta.url), 'utf8');
const styles = readFileSync(new URL('../src/officeTour.css', import.meta.url), 'utf8');

test('office is an architectural interactive scene, not emoji furniture placeholders', () => {
  assert.match(scene, /officeRoomInterior/);
  assert.match(scene, /officeDeskFurniture/);
  assert.match(scene, /officeChairFurniture/);
  assert.match(scene, /officeCentralWalkway/);
  assert.doesNotMatch(scene, /officeRoomFurniture/);
  assert.match(styles, /\.officeRoomInterior/);
  assert.match(styles, /\.officeMonitor/);
});

test('existing server-backed workspace modules remain reachable', () => {
  for (const moduleName of ['OfficeWorkstations', 'OfficeSpaceLiveManager', 'ReceptionOperations']) {
    assert.ok(scene.includes('<' + moduleName), moduleName + ' missing from office');
  }
  // The guided visit walks the office registry's real rooms; the concept preview draws invented ones, so it has no visit.
  assert.ok(!scene.includes('<GuidedExecutiveTour'), 'the concept preview must not offer a guided visit of invented rooms');
  assert.match(scene, /spaceRegistryOpen/);
  assert.match(scene, /officeWorld/);
});

test('office camera, spoken guidance and real-data disclaimers are preserved', () => {
  for (const token of ['onScenePointerDown', 'onSceneWheel', 'SpeechRecognition', 'officeLightAt', 'MANUAL']) {
    assert.ok(scene.includes(token), token + ' is required');
  }
});
