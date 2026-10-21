import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const root = new URL('../src/', import.meta.url);
const tour = readFileSync(new URL('OfficeTourView.tsx', root), 'utf8');
const app = readFileSync(new URL('main.tsx', root), 'utf8');

test('office hash opens the interactive tour and retains real operations panels', () => {
  assert.match(app, /location\.hash==='\#office-preview'/);
  assert.match(app, /<OfficeTourView\/>/);
  for (const component of ['OfficeWorkstations', 'OfficeSpaceLiveManager', 'ReceptionOperations', 'GuidedExecutiveTour']) {
    assert.ok(tour.includes('<' + component), component + ' not reachable from office');
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
