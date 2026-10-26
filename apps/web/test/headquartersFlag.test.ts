import assert from 'node:assert/strict';
import { test } from 'node:test';
import { headquartersEnabled } from '../src/lib/headquartersFlag.ts';

test('the headquarters is on unless the build says off', () => {
  assert.equal(headquartersEnabled(undefined), true);
  assert.equal(headquartersEnabled(''), true);
  assert.equal(headquartersEnabled('on'), true);
  assert.equal(headquartersEnabled('true'), true);
  assert.equal(headquartersEnabled('off'), false);
  assert.equal(headquartersEnabled(' OFF '), false);
});
