// Per-workspace model settings (docs/model-gateway.md, Per-workspace model configuration
// and Model routing). Pure, so `pnpm --filter @anum/web test:unit` can exercise it.
import { joinList, t } from './i18n.ts';

import type { ModelConnectionTest, WorkspaceModelConfig, WorkspaceModelConfigWrite, WorkspaceModelProvider } from '@anum/contracts';

export type ModelProvider = WorkspaceModelProvider;
export type ModelEffort = '' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';

/** `GET /api/v1/model-config`; the key itself is never returned, only its last four characters. */
export type ModelConfig = WorkspaceModelConfig;
/** `POST /api/v1/model-config/test`. */
export type { ModelConnectionTest };
/** The `PUT /api/v1/model-config` body. */
export type ModelConfigBody = WorkspaceModelConfigWrite;

export interface ModelDraft {
  provider: ModelProvider | '';
  model: string;
  baseUrl: string;
  apiKey: string;
  fastModel: string;
  capableModel: string;
  fastEffort: ModelEffort;
  capableEffort: ModelEffort;
}

export type DraftField = 'provider' | 'model' | 'baseUrl' | 'apiKey' | 'fastModel' | 'capableModel';

export const ANTHROPIC_BASE_URL = 'https://api.anthropic.com';
export const ANTHROPIC_FAST_MODEL = 'claude-haiku-5-5';
export const ANTHROPIC_CAPABLE_MODEL = 'claude-sonnet-5-5';

export const EFFORTS: { value: ModelEffort; label: string }[] = [
  // Labels are getters so they read in the locale current when shown.
  { value: '', get label() { return t('model.effort.default'); } },
  { value: 'low', get label() { return t('model.effort.low'); } },
  { value: 'medium', get label() { return t('model.effort.medium'); } },
  { value: 'high', get label() { return t('model.effort.high'); } },
  { value: 'xhigh', get label() { return t('model.effort.xhigh'); } },
  { value: 'max', get label() { return t('model.effort.max'); } },
];

export interface ProviderOption {
  value: ModelProvider;
  label: string;
  keyRequired: boolean;
  model: string;
  baseUrl: string;
}

export const PROVIDERS: ProviderOption[] = [
  { value: 'anthropic', get label() { return t('model.providerName.anthropic'); }, keyRequired: true, model: ANTHROPIC_CAPABLE_MODEL, baseUrl: ANTHROPIC_BASE_URL },
  { value: 'openai_compatible', get label() { return t('model.providerName.openai_compatible'); }, keyRequired: true, model: 'gpt-4.1-mini', baseUrl: 'https://api.openai.com/v1' },
  { value: 'ollama', get label() { return t('model.providerName.ollama'); }, keyRequired: false, model: 'llama3.2', baseUrl: 'http://localhost:11434/v1' },
  { value: 'mock', get label() { return t('model.providerName.mock'); }, keyRequired: false, model: 'anum-mock-planner', baseUrl: 'http://localhost:8000' },
];

export function providerOption(provider: string): ProviderOption | undefined {
  return PROVIDERS.find((option) => option.value === provider);
}

export function providerLabel(provider: string): string {
  return providerOption(provider)?.label ?? provider;
}

export const EMPTY_DRAFT: ModelDraft = { provider: '', model: '', baseUrl: '', apiKey: '', fastModel: '', capableModel: '', fastEffort: '', capableEffort: '' };

/** The form after choosing `provider`: its defaults (Claude: Haiku for quick replies, Sonnet for planning). */
export function presetDraft(provider: ModelProvider | ''): ModelDraft {
  const option = provider ? providerOption(provider) : undefined;
  if (!option) return { ...EMPTY_DRAFT, provider };
  if (option.value === 'anthropic') {
    return { ...EMPTY_DRAFT, provider: 'anthropic', model: option.model, baseUrl: option.baseUrl, fastModel: ANTHROPIC_FAST_MODEL, capableModel: ANTHROPIC_CAPABLE_MODEL, fastEffort: 'low', capableEffort: '' };
  }
  return { ...EMPTY_DRAFT, provider: option.value, model: option.model, baseUrl: option.baseUrl };
}

function effort(value: string | null | undefined): ModelEffort {
  return EFFORTS.some((item) => item.value === value) ? value as ModelEffort : '';
}

/** The form for a saved configuration. The key field starts empty: the key is never sent back. */
export function draftFromConfig(config: ModelConfig): ModelDraft {
  const provider = providerOption(config.provider)?.value ?? '';
  return {
    provider,
    model: config.model,
    baseUrl: config.base_url,
    apiKey: '',
    fastModel: config.fast_model ?? '',
    // Claude's form has no separate main model: planning's model is the saved default.
    capableModel: config.capable_model ?? (provider === 'anthropic' ? config.model : ''),
    fastEffort: effort(config.fast_effort),
    capableEffort: effort(config.capable_effort),
  };
}

/** The base URL as the API stores it: no trailing slash; Claude's empty value is the default
 *  endpoint and its OpenAI-style `/v1` suffix is dropped. */
export function normalizedBaseUrl(provider: string, baseUrl: string): string {
  let url = baseUrl.trim().replace(/\/+$/, '');
  if (provider === 'anthropic') {
    if (!url) url = ANTHROPIC_BASE_URL;
    if (url.endsWith('/v1')) url = url.slice(0, -3);
  }
  return url;
}

/** What happens to the saved API key if the draft is saved with an empty key field:
 *  `keep` (same provider and base URL: the API keeps it), `moved` (the base URL changed: a
 *  saved key is never sent to a new address, so a key is needed) or `none` (no saved key for
 *  this provider). */
export function savedKeyState(draft: ModelDraft, saved: ModelConfig | null): 'keep' | 'moved' | 'none' {
  if (!saved?.credential_configured || !draft.provider || saved.provider !== draft.provider) return 'none';
  return normalizedBaseUrl(draft.provider, draft.baseUrl) === normalizedBaseUrl(saved.provider, saved.base_url) ? 'keep' : 'moved';
}

/** Problems that stop a save, by field; an empty object means the draft can be sent. */
export function validateDraft(draft: ModelDraft, saved: ModelConfig | null): Partial<Record<DraftField, string>> {
  const errors: Partial<Record<DraftField, string>> = {};
  const option = draft.provider ? providerOption(draft.provider) : undefined;
  if (!option) { errors.provider = t('model.error.provider'); return errors; }
  if (option.value === 'anthropic') {
    if (!draft.capableModel.trim()) errors.capableModel = t('model.error.capable');
  } else if (!draft.model.trim()) errors.model = t('model.error.model');
  const base = draft.baseUrl.trim();
  if (!base && option.value !== 'anthropic') errors.baseUrl = t('model.error.baseUrl');
  else if (base && !/^https?:\/\/[^\s/]+/i.test(base)) errors.baseUrl = t('model.error.baseUrlFull');
  const keyState = savedKeyState(draft, saved);
  if (option.keyRequired && !draft.apiKey.trim() && keyState !== 'keep') {
    errors.apiKey = keyState === 'moved'
      ? t('model.error.keyMoved')
      : t(option.value === 'anthropic' ? 'model.error.keyAnthropic' : 'model.error.keyProvider');
  }
  return errors;
}

/** The PUT body. Routing fields are sent for Claude only; empty means "use the main model" / "model's default". */
export function modelConfigBody(draft: ModelDraft): ModelConfigBody {
  if (!draft.provider) throw new Error(t('model.error.provider'));
  const body: ModelConfigBody = { provider: draft.provider, model: draft.model.trim(), api_key: draft.apiKey.trim() || null };
  const base = draft.baseUrl.trim();
  if (draft.provider === 'anthropic') {
    // The planning model is also the workspace's default model.
    const capable = draft.capableModel.trim() || draft.model.trim();
    body.model = capable;
    if (base) body.base_url = base;
    body.fast_model = draft.fastModel.trim() || null;
    body.capable_model = capable || null;
    body.fast_effort = draft.fastEffort || null;
    body.capable_effort = draft.capableEffort || null;
  } else {
    body.base_url = base;
  }
  return body;
}

/** One sentence for a successful connection test. */
export function connectionSummary(result: ModelConnectionTest): string {
  const models = result.models && result.models.length ? result.models : [result.model];
  return t('model.connected', { models: joinList(models), ms: result.latency_ms });
}

/** What answers each kind of call, for the saved configuration. */
export function routeSummary(config: ModelConfig): { fast: string; capable: string } {
  if (config.provider !== 'anthropic') return { fast: config.model, capable: config.model };
  return { fast: config.fast_model || config.model, capable: config.capable_model || config.model };
}
