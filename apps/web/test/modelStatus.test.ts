// Unit tests for the placeholder-answers banner rule in src/lib/modelStatus.ts (docs/model-gateway.md#placeholder-answers).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { en } from '../src/lib/messages/en.ts';
import { ar } from '../src/lib/messages/ar.ts';
import { placeholderBannerKey } from '../src/lib/modelStatus.ts';

describe('placeholderBannerKey', () => {
  test('the server placeholder model shows the server banner', () => {
    assert.equal(placeholderBannerKey({ placeholder: true, provider: 'mock', source: 'server' }), 'model.placeholder.server');
  });
  test('a workspace set to the placeholder model says so', () => {
    assert.equal(placeholderBannerKey({ placeholder: true, provider: 'mock', source: 'workspace' }), 'model.placeholder.workspace');
  });
  test('a real model, or a status that could not be read, shows nothing', () => {
    assert.equal(placeholderBannerKey({ placeholder: false, provider: 'anthropic', source: 'server' }), null);
    assert.equal(placeholderBannerKey(null), null);
    assert.equal(placeholderBannerKey({} as never), null);
  });
  test('the banner is written in English and Arabic', () => {
    for (const key of ['model.placeholder.server', 'model.placeholder.workspace', 'model.placeholder.fix', 'model.placeholder.openSettings', 'model.placeholder.help'] as const) {
      assert.ok(en[key].length > 0 && ar[key].length > 0, key);
      assert.notEqual(en[key], ar[key], key);
    }
    assert.match(en['model.placeholder.server'], /^AI answers are placeholders: the server has no AI model set up\.$/);
  });
});
