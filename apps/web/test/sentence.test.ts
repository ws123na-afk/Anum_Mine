import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { asSentence, fitSentence, isNetworkFailure } from '../src/lib/sentence.ts';
import { ApiError, describeError } from '../src/lib/errors.ts';
import { translate } from '../src/lib/i18n.ts';

describe('fitSentence: an error sentence joins the template around it', () => {
  it('adds the full stop a browser message lacks when a new sentence follows', () => {
    assert.equal(fitSentence('Failed to fetch', ' Check your connection, then try again.'), 'Failed to fetch.');
    assert.equal(fitSentence('The file is locked', ' "Unlock" opens it.'), 'The file is locked.');
    assert.equal(fitSentence('تعذّر الوصول إلى الخادم', ' تحقق من الاتصال ثم حاول مرة أخرى.'), 'تعذّر الوصول إلى الخادم.');
  });
  it('keeps a sentence that already ends, whatever mark ends it', () => {
    for (const text of ['The database is not reachable.', 'Are you signed in?', 'Stop!', 'هل أنت متصل؟', 'Loading…', 'She said "no."'])
      assert.equal(fitSentence(text, ' Try again.'), text);
  });
  it('drops its own full stop when the template supplies one', () => {
    assert.equal(fitSentence('The database is not reachable.', '. Try again in a moment.'), 'The database is not reachable');
    assert.equal(fitSentence('The database is not reachable.', '). You can still open the ticket.'), 'The database is not reachable');
    assert.equal(fitSentence('Not reachable', '. Try again.'), 'Not reachable');
  });
  it('leaves the value alone when nothing sentence-like follows', () => {
    assert.equal(fitSentence('Failed to fetch', ''), 'Failed to fetch');
    assert.equal(fitSentence('timeout', ' on the second try'), 'timeout');
    assert.equal(fitSentence('timeout', ' · 120 ms'), 'timeout');
    assert.equal(fitSentence('Bad gateway..', ''), 'Bad gateway.');
  });
  it('never punctuates a count or an empty value', () => {
    assert.equal(fitSentence('3', ' With errors'), '3');
    assert.equal(fitSentence('', ' Try again.'), '');
  });
});

describe('asSentence', () => {
  it('ends with exactly one full stop', () => {
    assert.equal(asSentence('Could not reach the server'), 'Could not reach the server.');
    assert.equal(asSentence('Could not reach the server..'), 'Could not reach the server.');
    assert.equal(asSentence('Really?'), 'Really?');
    assert.equal(asSentence(''), '');
  });
});

describe('a request that got no answer', () => {
  it('is recognised in every browser\'s wording, and nothing else is', () => {
    for (const text of ['Failed to fetch', 'Load failed', 'NetworkError when attempting to fetch resource.']) assert.equal(isNetworkFailure(new TypeError(text)), true);
    assert.equal(isNetworkFailure(new TypeError('x is not a function')), false);
    assert.equal(isNetworkFailure(new Error('Failed to fetch')), false);
    assert.equal(isNetworkFailure(new ApiError(500, null, 'Failed to fetch')), false);
    assert.equal(isNetworkFailure(Object.assign(new TypeError('Failed to fetch'), { name: 'AbortError' })), false);
    assert.equal(isNetworkFailure('Failed to fetch'), false);
  });
  it('is described in the person\'s language, never in the browser\'s words', () => {
    assert.equal(describeError(new TypeError('Failed to fetch')), 'Could not reach the server.');
    assert.equal(translate('ar', 'common.networkError'), 'تعذّر الوصول إلى الخادم.');
  });
});

describe('catalog templates read as whole sentences', () => {
  const network = describeError(new TypeError('Failed to fetch'));
  const api = describeError(new ApiError(500, null, 'The database is not reachable.'));
  it('"{message} Next sentence" templates', () => {
    assert.equal(translate('en', 'att.error.load', { message: 'Failed to fetch' }), "Couldn't read your attendance: Failed to fetch. Check your connection, then try again.");
    assert.equal(translate('en', 'att.error.load', { message: network }), "Couldn't read your attendance: Could not reach the server. Check your connection, then try again.");
    assert.equal(translate('en', 'att.error.load', { message: api }), "Couldn't read your attendance: The database is not reachable. Check your connection, then try again.");
  });
  it('"{message}. Next sentence" templates', () => {
    assert.equal(translate('en', 'finance.error.load', { message: network }), 'Finance could not be loaded: Could not reach the server. Check your connection and try again.');
    assert.equal(translate('en', 'biz.errorText', { message: api }), 'The database is not reachable. Try again in a moment.');
  });
  it('Arabic sentences carry no browser English and one full stop', () => {
    const text = translate('ar', 'att.error.load', { message: translate('ar', 'common.networkError') });
    assert.doesNotMatch(text, /[A-Za-z]/);
    assert.doesNotMatch(text, /\.\./);
    assert.match(text, /الخادم\. /);
  });
});
