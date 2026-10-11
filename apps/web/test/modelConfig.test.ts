// Unit tests for the workspace model settings (src/lib/modelConfig.ts), including the
// Anthropic (Claude) provider with model routing. Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  ANTHROPIC_CAPABLE_MODEL,
  ANTHROPIC_FAST_MODEL,
  PROVIDERS,
  connectionSummary,
  draftFromConfig,
  modelConfigBody,
  presetDraft,
  providerLabel,
  routeSummary,
  validateDraft,
  type ModelConfig,
} from '../src/lib/modelConfig.ts';

const saved = (overrides: Partial<ModelConfig> = {}): ModelConfig => ({
  provider: 'anthropic', model: 'claude-sonnet-5-5', base_url: 'https://api.anthropic.com', fast_model: 'claude-haiku-5-5', capable_model: 'claude-sonnet-5-5',
  fast_effort: 'low', capable_effort: null, credential_configured: true, credential_hint: '...wxyz', updated_at: '2026-10-11T00:00:00Z', ...overrides,
});

describe('model settings', () => {
  test('offers Anthropic (Claude) first, next to the other providers', () => {
    assert.deepEqual(PROVIDERS.map((p) => p.value), ['anthropic', 'openai_compatible', 'ollama', 'mock']);
    assert.equal(providerLabel('anthropic'), 'Anthropic (Claude)');
    assert.equal(providerLabel('something_else'), 'something_else');
  });

  test('choosing Claude fills Haiku for quick replies and Sonnet for planning', () => {
    const draft = presetDraft('anthropic');
    assert.equal(draft.fastModel, ANTHROPIC_FAST_MODEL);
    assert.equal(draft.capableModel, ANTHROPIC_CAPABLE_MODEL);
    assert.equal(draft.fastEffort, 'low');
    assert.equal(draft.capableEffort, '');
    assert.equal(draft.baseUrl, 'https://api.anthropic.com');
    assert.equal(draft.apiKey, '');
    assert.equal(presetDraft('ollama').baseUrl, 'http://localhost:11434/v1');
  });

  test('a Claude draft needs a key and a planning model', () => {
    const draft = presetDraft('anthropic');
    assert.match(validateDraft(draft, null).apiKey ?? '', /Anthropic API key/);
    assert.deepEqual(validateDraft({ ...draft, apiKey: 'sk-ant-example' }, null), {});
    assert.match(validateDraft({ ...draft, apiKey: 'sk-ant-example', capableModel: ' ' }, null).capableModel ?? '', /planning/);
    // The base URL is optional for Claude but must be a URL when given.
    assert.deepEqual(validateDraft({ ...draft, apiKey: 'k', baseUrl: '' }, null), {});
    assert.match(validateDraft({ ...draft, apiKey: 'k', baseUrl: 'api.anthropic.com' }, null).baseUrl ?? '', /https:\/\//);
  });

  test('the saved key is never shown and must be pasted again to save changes', () => {
    const draft = draftFromConfig(saved());
    assert.equal(draft.apiKey, '');
    assert.match(validateDraft(draft, saved()).apiKey ?? '', /\.\.\.wxyz.*never shown/);
  });

  test('other providers still need a model and a base URL', () => {
    assert.equal(validateDraft(presetDraft(''), null).provider, 'Choose a provider.');
    const ollama = { ...presetDraft('ollama'), model: '', baseUrl: '' };
    const errors = validateDraft(ollama, null);
    assert.ok(errors.model && errors.baseUrl);
    assert.equal(errors.apiKey, undefined);
  });

  test('the Claude body sends routing, the planning model as the default, and nulls for unset values', () => {
    const body = modelConfigBody({ ...presetDraft('anthropic'), apiKey: ' sk-ant-example ', baseUrl: '' });
    assert.deepEqual(body, {
      provider: 'anthropic', model: 'claude-sonnet-5-5', api_key: 'sk-ant-example',
      fast_model: 'claude-haiku-5-5', capable_model: 'claude-sonnet-5-5', fast_effort: 'low', capable_effort: null,
    });
    const blankFast = modelConfigBody({ ...presetDraft('anthropic'), apiKey: 'k', fastModel: '', fastEffort: '' });
    assert.equal(blankFast.fast_model, null);
    assert.equal(blankFast.fast_effort, null);
    assert.equal(blankFast.base_url, 'https://api.anthropic.com');
  });

  test('other providers send no routing fields', () => {
    const body = modelConfigBody({ ...presetDraft('ollama') });
    assert.deepEqual(body, { provider: 'ollama', model: 'llama3.2', api_key: null, base_url: 'http://localhost:11434/v1' });
    assert.throws(() => modelConfigBody(presetDraft('')));
  });

  test('a saved Claude config round-trips into the form', () => {
    const draft = draftFromConfig(saved({ capable_model: null, fast_model: null, fast_effort: 'bogus' }));
    assert.equal(draft.provider, 'anthropic');
    assert.equal(draft.capableModel, 'claude-sonnet-5-5');
    assert.equal(draft.fastModel, '');
    assert.equal(draft.fastEffort, '');
  });

  test('summaries name every routed and tested model', () => {
    assert.deepEqual(routeSummary(saved()), { fast: 'claude-haiku-5-5', capable: 'claude-sonnet-5-5' });
    assert.deepEqual(routeSummary(saved({ fast_model: null })), { fast: 'claude-sonnet-5-5', capable: 'claude-sonnet-5-5' });
    assert.deepEqual(routeSummary(saved({ provider: 'ollama', model: 'llama3.2' })), { fast: 'llama3.2', capable: 'llama3.2' });
    assert.equal(connectionSummary({ provider: 'anthropic', model: 'claude-sonnet-5-5', models: ['claude-sonnet-5-5', 'claude-haiku-5-5'], latency_ms: 420, status: 'connected' }), 'Connected to claude-sonnet-5-5 and claude-haiku-5-5 in 420 ms.');
    assert.equal(connectionSummary({ provider: 'mock', model: 'anum-mock', latency_ms: 0, status: 'connected' }), 'Connected to anum-mock in 0 ms.');
  });
});
