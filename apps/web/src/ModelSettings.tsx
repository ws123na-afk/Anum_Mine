// Settings > Model gateway: the model this workspace uses (docs/model-gateway.md, Per-workspace
// model configuration). Everything shown comes from the API; who may change it is decided by the
// API's 403 (organization:manage), never guessed here. The provider key is write-only.
import { useCallback, useEffect, useId, useState, type ReactNode } from 'react';
import { CircleDot, Lock, PlugZap, RefreshCw, Save } from 'lucide-react';
import { getModelConfig, saveModelConfigBody, testModelConfig } from './lib/api';
import { ApiError, describeError, isPermissionDenied } from './lib/errors';
import {
  ANTHROPIC_BASE_URL,
  ANTHROPIC_CAPABLE_MODEL,
  ANTHROPIC_FAST_MODEL,
  EFFORTS,
  EMPTY_DRAFT,
  PROVIDERS,
  connectionSummary,
  draftFromConfig,
  modelConfigBody,
  presetDraft,
  providerLabel,
  routeSummary,
  savedKeyState,
  validateDraft,
  type DraftField,
  type ModelConfig,
  type ModelDraft,
  type ModelEffort,
  type ModelProvider,
} from './lib/modelConfig';
import { t } from './lib/i18n';
import { rich } from './rich';
import './model-settings.css';

type Load = { kind: 'loading' } | { kind: 'ready'; config: ModelConfig | null } | { kind: 'forbidden'; detail: string } | { kind: 'error'; detail: string };
type Flash = { text: string; tone: 'ok' | 'error' | 'info' } | null;

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

function Forbidden({ detail }: { detail: string | null }) {
  return <section className="surface adminForbidden" aria-label={t('model.ownerRequired')}>
    <span className="adminIcon"><Lock aria-hidden="true" /></span>
    <div><h3>{t('model.ownerRequired')}</h3><p>{t('model.ownerOnly')}</p>{detail && <p className="muted">{t('model.apiAnswered', { detail })}</p>}</div>
  </section>;
}

function Field({ label, error, hint, children }: { label: string; error?: string; hint?: ReactNode; children: (props: { id: string; 'aria-invalid'?: boolean; 'aria-describedby'?: string }) => ReactNode }) {
  const id = useId();
  const noteId = `${id}-note`;
  return <div className="field">
    <label htmlFor={id}>{label}</label>
    {children({ id, ...(error ? { 'aria-invalid': true } : {}), ...(error || hint ? { 'aria-describedby': noteId } : {}) })}
    {error ? <small id={noteId} className="fieldError">{error}</small> : hint ? <small id={noteId} className="muted">{hint}</small> : null}
  </div>;
}

function saveFailure(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) return t('model.saveConflict', { reason: describeError(error) });
  if (error instanceof ApiError && error.status === 422) return t('model.saveRefused', { reason: describeError(error) });
  return t('model.saveFailed', { reason: describeError(error) });
}

function testFailure(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) return t('model.testNothing');
  return t('model.testFailed', { reason: describeError(error) });
}

export function ModelGatewaySettings({ onboarded }: { onboarded: boolean | null }) {
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [draft, setDraft] = useState<ModelDraft>(EMPTY_DRAFT);
  const [errors, setErrors] = useState<Partial<Record<DraftField, string>>>({});
  const [busy, setBusy] = useState<'save' | 'test' | null>(null);
  const [flash, setFlash] = useState<Flash>(null);
  const [denied, setDenied] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoad({ kind: 'loading' });
    try {
      const config = await getModelConfig();
      setLoad({ kind: 'ready', config });
      setDraft(draftFromConfig(config));
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) { setLoad({ kind: 'ready', config: null }); setDraft(EMPTY_DRAFT); return; }
      setLoad(isPermissionDenied(error) ? { kind: 'forbidden', detail: describeError(error) } : { kind: 'error', detail: describeError(error) });
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  const config = load.kind === 'ready' ? load.config : null;
  const set = (patch: Partial<ModelDraft>) => { setDraft((current) => ({ ...current, ...patch })); setErrors({}); };
  const isClaude = draft.provider === 'anthropic';

  const save = async () => {
    const problems = validateDraft(draft, config);
    setErrors(problems);
    if (Object.keys(problems).length) { setFlash({ tone: 'error', text: t('model.checkFields') }); return; }
    setBusy('save'); setFlash(null);
    try {
      const saved = await saveModelConfigBody(modelConfigBody(draft));
      setLoad({ kind: 'ready', config: saved });
      setDraft(draftFromConfig(saved));
      const routes = routeSummary(saved);
      setFlash({ tone: 'ok', text: saved.provider === 'anthropic'
        ? t('model.savedClaude', { provider: providerLabel(saved.provider), fast: routes.fast, capable: routes.capable })
        : t('model.savedOther', { provider: providerLabel(saved.provider), model: saved.model }) });
    } catch (error) {
      if (isPermissionDenied(error)) { setDenied(describeError(error)); setFlash(null); }
      else setFlash({ tone: 'error', text: saveFailure(error) });
    } finally { setBusy(null); }
  };

  const test = async () => {
    if (!config) { setFlash({ tone: 'info', text: t('model.testServerDefault') }); return; }
    setBusy('test'); setFlash(null);
    try {
      const result = await testModelConfig();
      setFlash({ tone: 'ok', text: connectionSummary(result) });
    } catch (error) {
      if (isPermissionDenied(error)) { setDenied(describeError(error)); setFlash(null); }
      else setFlash({ tone: 'error', text: testFailure(error) });
    } finally { setBusy(null); }
  };

  const keyState = savedKeyState(draft, config);
  const keyHint = keyState === 'keep'
    ? t('model.keyKeep', { hint: config?.credential_hint ?? t('model.hidden') })
    : keyState === 'moved'
      ? t('model.keyMoved', { hint: config?.credential_hint ?? t('model.hidden') })
      : isClaude ? t('model.keyClaude') : undefined;
  const effortSelect = (value: ModelEffort, onChange: (next: ModelEffort) => void) => (props: { id: string }) =>
    <select {...props} value={value} onChange={(event) => onChange(event.target.value as ModelEffort)}>{EFFORTS.map((item) => <option key={item.value || 'default'} value={item.value}>{item.label}</option>)}</select>;

  return <section className="surface settingsForm modelSettings" aria-labelledby="model-gateway-heading">
    <div className="sectionHeader">
      <div><p className="eyebrow">{t('model.eyebrow')}</p><h2 id="model-gateway-heading">{t('model.title')}</h2></div>
      {load.kind === 'ready' && (config
        ? <span className={`statusBadge ${config.credential_configured ? 'success' : 'neutral'}`}>{config.credential_configured ? t('model.badgeKey', { provider: providerLabel(config.provider), hint: config.credential_hint ?? t('model.saved') }) : providerLabel(config.provider)}</span>
        : <span className="statusBadge neutral">{t('model.serverDefault')}</span>)}
    </div>
    {load.kind === 'loading' && <Notice>{t('model.loading')}</Notice>}
    {load.kind === 'error' && <>
      <Notice error>{t('model.loadFailed', { detail: load.detail })}</Notice>
      <div className="actions"><button type="button" className="secondary" onClick={() => void refresh()}><RefreshCw aria-hidden="true" />{t('common.retry')}</button></div>
    </>}
    {load.kind === 'forbidden' && <Forbidden detail={load.detail} />}
    {load.kind === 'ready' && <form noValidate aria-label={t('model.form')} onSubmit={(event) => { event.preventDefault(); void save(); }}>
      {config
        ? <p className="muted">{(() => { const routes = routeSummary(config); return config.provider === 'anthropic'
          ? t('model.summaryClaude', { fast: routes.fast, capable: routes.capable })
          : t('model.summaryOther', { provider: providerLabel(config.provider), model: config.model }); })()}</p>
        : <p className="muted">{t('model.usesDeployment')}{onboarded === false ? ` ${t('model.onboardFirst')}` : ''}</p>}
      <div className="formGrid">
        <Field label={t('model.provider')} error={errors.provider}>{(props) =>
          <select {...props} value={draft.provider} onChange={(event) => { setDraft(presetDraft(event.target.value as ModelProvider | '')); setErrors({}); setFlash(null); }}>
            {!config && <option value="">{t('model.serverDefaultOption')}</option>}
            {PROVIDERS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>}</Field>
        {!isClaude && draft.provider && <Field label={t('model.model')} error={errors.model}>{(props) => <input {...props} value={draft.model} onChange={(event) => set({ model: event.target.value })} autoComplete="off" />}</Field>}
        {draft.provider && <Field label={isClaude ? t('model.baseUrlOptional') : t('model.baseUrl')} error={errors.baseUrl} hint={isClaude ? t('model.baseUrlClaudeHint') : undefined}>{(props) =>
          <input {...props} value={draft.baseUrl} placeholder={isClaude ? ANTHROPIC_BASE_URL : undefined} onChange={(event) => set({ baseUrl: event.target.value })} autoComplete="off" inputMode="url" dir="ltr" />}</Field>}
        {draft.provider && draft.provider !== 'mock' && <Field label={isClaude ? t('model.anthropicKey') : t('model.apiKey')} error={errors.apiKey} hint={keyHint}>{(props) =>
          <input {...props} type="password" value={draft.apiKey} onChange={(event) => set({ apiKey: event.target.value })} autoComplete="new-password" spellCheck={false}
            placeholder={keyState === 'keep' ? t('model.placeholderKeep') : draft.provider === 'ollama' ? t('model.placeholderOllama') : isClaude ? 'sk-ant-...' : t('model.placeholderExternal')} />}</Field>}
      </div>
      {isClaude && <fieldset className="modelRoutes">
        <legend>{t('model.routing')}</legend>
        <p className="muted">{t('model.routingIntro')}</p>
        <div className="formGrid modelRouteGrid">
          <Field label={t('model.fastModel')} error={errors.fastModel} hint={t('model.fastModelHint')}>{(props) =>
            <input {...props} value={draft.fastModel} placeholder={ANTHROPIC_FAST_MODEL} onChange={(event) => set({ fastModel: event.target.value })} autoComplete="off" />}</Field>
          <Field label={t('model.fastEffort')}>{effortSelect(draft.fastEffort, (fastEffort) => set({ fastEffort }))}</Field>
          <Field label={t('model.capableModel')} error={errors.capableModel} hint={t('model.capableModelHint')}>{(props) =>
            <input {...props} value={draft.capableModel} placeholder={ANTHROPIC_CAPABLE_MODEL} onChange={(event) => set({ capableModel: event.target.value })} autoComplete="off" />}</Field>
          <Field label={t('model.capableEffort')}>{effortSelect(draft.capableEffort, (capableEffort) => set({ capableEffort }))}</Field>
        </div>
        <p className="muted">{t('model.embeddingsNote')}</p>
      </fieldset>}
      {draft.provider === 'ollama' && <p className="muted">{rich('model.ollamaNote', { pull: <code dir="ltr">ollama pull {draft.model || 'llama3.2'}</code>, serve: <code dir="ltr">ollama serve</code> })}</p>}
      {denied && <Forbidden detail={denied} />}
      {flash && <Notice error={flash.tone === 'error'}>{flash.text}</Notice>}
      <div className="actions">
        <button type="submit" disabled={!draft.provider || busy !== null}><Save aria-hidden="true" />{busy === 'save' ? t('model.saving') : t('model.save')}</button>
        <button type="button" className="secondary" disabled={busy !== null} onClick={() => void test()}><PlugZap aria-hidden="true" />{busy === 'test' ? t('model.testing') : t('model.test')}</button>
      </div>
      {config && <p className="muted modelTestNote">{config.provider === 'anthropic' ? t('model.testNoteClaude') : t('model.testNote')}</p>}
    </form>}
  </section>;
}
