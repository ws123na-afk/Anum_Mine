// Unit tests for wake-by-name matching in src/lib/wake.ts (mirrors apps/mobile/test wake_word tests).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { greeting, matchWakeWord, sanitizeAssistantName, wakeAliases } from '../src/lib/wake.ts';

describe('matchWakeWord', () => {
  test('returns what follows the name', () => {
    assert.equal(matchWakeWord("Anum, what's running?", 'Anum'), "what's running?");
    assert.equal(matchWakeWord('Hey Layla what is the status', 'Layla'), 'what is the status');
  });
  test('a bare wake phrase returns an empty command', () => {
    assert.equal(matchWakeWord('hey anum', 'Anum'), '');
    assert.equal(matchWakeWord('OK Layla.', 'Layla'), '');
  });
  test('the default name answers to common transcriptions, in Latin and Arabic', () => {
    assert.equal(matchWakeWord('hey annum open my tasks', 'Anum'), 'open my tasks');
    assert.equal(matchWakeWord('يا أنوم ما حالة مهامي', 'Anum'), 'ما حالة مهامي');
  });
  test('ignores the name inside another word and unrelated speech', () => {
    assert.equal(matchWakeWord('the anumber is wrong', 'Anum'), null);
    assert.equal(matchWakeWord('what time is it', 'Layla'), null);
    assert.equal(matchWakeWord('anything', ''), null);
  });
  test('a custom name has no aliases', () => {
    assert.deepEqual(wakeAliases(' Jarvis '), ['Jarvis']);
  });
});

test('sanitizeAssistantName keeps letters, digits and simple punctuation', () => {
  assert.equal(sanitizeAssistantName('  J.A.R.V.I.S <script>  '), 'J.A.R.V.I.S script');
  assert.equal(sanitizeAssistantName('x'.repeat(60)).length, 40);
});

test('greeting follows the time of day and language', () => {
  assert.equal(greeting('Anum', 'en-US', 9), "Good morning. I'm Anum.");
  assert.equal(greeting('Anum', 'en-GB', 14), "Good afternoon. I'm Anum.");
  assert.equal(greeting('Anum', 'en-US', 21), "Good evening. I'm Anum.");
  assert.equal(greeting('Anum', 'ar-SA', 8), 'صباح الخير. أنا Anum.');
});
