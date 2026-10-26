// S-05: a refused meeting turn (429) reaches the person as the server's own sentence, which names the
// limit and the seconds to wait and follows the request language. No client text is invented.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { ApiError, describeError } from '../src/lib/errors.ts';

describe('meeting turn rate refusal', () => {
  test('English sentence is shown as sent', () => {
    const sentence = 'You have started the most agent meeting turns allowed in one minute (6). Wait 42 seconds and try again.';
    assert.equal(describeError(new ApiError(429, 'rate_limited', sentence)), sentence);
  });
  test('Arabic sentence is shown as sent', () => {
    const sentence = 'بدأت أقصى عدد مسموح من أدوار اجتماعات الوكلاء في الدقيقة الواحدة (6). انتظر 42 ثانية ثم حاول مرة أخرى.';
    assert.equal(describeError(new ApiError(429, 'rate_limited', sentence)), sentence);
  });
});
