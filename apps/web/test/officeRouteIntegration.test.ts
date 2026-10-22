import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { viewFromHash } from '../src/lib/shell.ts';

const root = new URL('../src/', import.meta.url);
const tour = readFileSync(new URL('OfficeTourView.tsx', root), 'utf8');
const app = readFileSync(new URL('main.tsx', root), 'utf8');
const live = readFileSync(new URL('HeadquartersLive.tsx', root), 'utf8');

test('office hash opens the interactive tour and retains real operations panels', () => {
  assert.equal(viewFromHash('#office-preview'), 'office');
  assert.equal(viewFromHash('#office'), 'office');
  assert.match(app, /view==='office'&&<OfficeTourView /);
  for (const component of ['OfficeWorkstations', 'OfficeSpaceLiveManager', 'ReceptionOperations', 'GuidedExecutiveTour']) {
    assert.ok(tour.includes('<' + component), component + ' not reachable from the concept preview');
    assert.ok(live.includes('<' + component), component + ' not reachable from the live office');
  }
});
test('office scene labels its example activity rather than claiming live agents', () => {
  assert.match(tour, /MANUAL/);
  assert.match(tour, /illustrative/);
});

test('office rooms can open actual ANUM modules, with no invented deep links', () => {
  for (const entry of ["command:'command'", "meeting:'meetings'", "finance:'finance'", "tyre:'tyre'"]) {
    assert.ok(tour.includes(entry), entry + ' is missing');
  }
  assert.match(tour, /officeOpenModule/);
  assert.match(tour, /window.location.hash='#'\+moduleDestination\(active.id\)/);
  assert.match(tour, /window.location.hash='#home'/);
});
