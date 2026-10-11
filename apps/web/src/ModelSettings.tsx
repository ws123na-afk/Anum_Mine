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
  validateDraft,
  type DraftField,
  type ModelConfig,
  type ModelDraft,
  type ModelEffort,
  type ModelProvider,
} from './lib/modelConfig';
import './model-settings.css';

type Load = { kind: 'loading' } | { kind: 'ready'; config: ModelConfig | null } | { kind: 'forbidden'; detail: string } | { kind: 'error'; detail: string };
type Flash = { text: string; tone: 'ok' | 'error' | 'info' } | null;

const OWNER_ONLY = 'Choosing the model, saving its API key and testing the connection need the owner role for this organization. Ask an owner to change the model, or to change your role.';

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

function Forbidden({ detail }: { detail: string | null }) {
  return <section className="surface adminForbidden" aria-label="Owner access required">
    <span className="adminIcon"><Lock aria-hidden="true" /></span>
    <div><h3>Owner access required</h3><p>{OWNER_ONLY}</p>{detail && <p className="muted">The API answered: {detail}</p>}</div>
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
  if (error instanceof ApiError && error.status === 409) return `${describeError(error)} Finish "Workspace onboarding" above, then save again.`;
  if (error instanceof ApiError && error.status === 422) return `The API refused these settings: ${describeError(error)}`;
  return `The model was not saved. ${describeError(error)}`;
}

function testFailure(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) return 'There is no saved workspace model to test. Save one first.';
  return `Connection failed. ${describeError(error)} Fix the settings, save, and test again.`;
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
    if (Object.keys(problems).length) { setFlash({ tone: 'error', text: 'Check the highlighted fields.' }); return; }
    setBusy('save'); setFlash(null);
    try {
      const saved = await saveModelConfigBody(modelConfigBody(draft));
      setLoad({ kind: 'ready', config: saved });
      setDraft(draftFromConfig(saved));
      const routes = routeSummary(saved);
      setFlash({ tone: 'ok', text: saved.provider === 'anthropic'
        ? `Saved. ${providerLabel(saved.provider)} now answers this workspace: ${routes.fast} for chat, voice and memory, ${routes.capable} for planning. Test the connection to check the key.`
        : `Saved. ${providerLabel(saved.provider)} (${saved.model}) now answers this workspace.` });
    } catch (error) {
      if (isPermissionDenied(error)) { setDenied(describeError(error)); setFlash(null); }
      else setFlash({ tone: 'error', text: saveFailure(error) });
    } finally { setBusy(null); }
  };

  const test = async () => {
    if (!config) { setFlash({ tone: 'info', text: 'This workspace uses the server default model: there is no workspace model to test. Save one first.' }); return; }
    setBusy('test'); setFlash(null);
    try {
      const result = await testModelConfig();
      setFlash({ tone: 'ok', text: connectionSummary(result) });
    } catch (error) {
      if (isPermissionDenied(error)) { setDenied(describeError(error)); setFlash(null); }
      else setFlash({ tone: 'error', text: testFailure(error) });
    } finally { setBusy(null); }
  };

  const keyHint = config?.credential_configured && config.provider === draft.provider
    ? `Saved key ${config.credential_hint ?? ''} is never shown. Paste it again (or a new one) to save changes.`
    : isClaude ? 'Create one in the Anthropic Console (API keys). It is stored encrypted and never shown again.' : undefined;
  const effortSelect = (value: ModelEffort, onChange: (next: ModelEffort) => void) => (props: { id: string }) =>
    <select {...props} value={value} onChange={(event) => onChange(event.target.value as ModelEffort)}>{EFFORTS.map((item) => <option key={item.value || 'default'} value={item.value}>{item.label}</option>)}</select>;

  return <section className="surface settingsForm modelSettings" aria-labelledby="model-gateway-heading">
    <div className="sectionHeader">
      <div><p className="eyebrow">Runtime</p><h2 id="model-gateway-heading">Model gateway</h2></div>
      {load.kind === 'ready' && (config
        ? <span className={`statusBadge ${config.credential_configured ? 'success' : 'neutral'}`}>{providerLabel(config.provider)}{config.credential_configured ? ` · key ${config.credential_hint ?? 'saved'}` : ''}</span>
        : <span className="statusBadge neutral">server default</span>)}
    </div>
    {load.kind === 'loading' && <Notice>Loading the workspace model...</Notice>}
    {load.kind === 'error' && <>
      <Notice error>The model settings could not be loaded. {load.detail} Check that the API is running, then retry.</Notice>
      <div className="actions"><button type="button" className="secondary" onClick={() => void refresh()}><RefreshCw aria-hidden="true" />Retry</button></div>
    </>}
    {load.kind === 'forbidden' && <Forbidden detail={load.detail} />}
    {load.kind === 'ready' && <form noValidate aria-label="Workspace model" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      {config
        ? <p className="muted">{(() => { const routes = routeSummary(config); return config.provider === 'anthropic'
          ? `Saved: ${routes.fast} answers chat, voice and memory; ${routes.capable} plans tasks. Every call counts against this workspace's model budget.`
          : `Saved: ${providerLabel(config.provider)}, model ${config.model}. It answers chat, voice and task planning.`; })()}</p>
        : <p className="muted">This workspace uses the deployment's model settings. Choose a provider only to override them for this workspace.{onboarded === false ? ' Complete workspace onboarding first.' : ''}</p>}
      <div className="formGrid">
        <Field label="Provider" error={errors.provider}>{(props) =>
          <select {...props} value={draft.provider} onChange={(event) => { setDraft(presetDraft(event.target.value as ModelProvider | '')); setErrors({}); setFlash(null); }}>
            {!config && <option value="">Server default</option>}
            {PROVIDERS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>}</Field>
        {!isClaude && draft.provider && <Field label="Model" error={errors.model}>{(props) => <input {...props} value={draft.model} onChange={(event) => set({ model: event.target.value })} autoComplete="off" />}</Field>}
        {draft.provider && <Field label={isClaude ? 'Base URL (optional)' : 'Base URL'} error={errors.baseUrl} hint={isClaude ? 'Leave empty for api.anthropic.com.' : undefined}>{(props) =>
          <input {...props} value={draft.baseUrl} placeholder={isClaude ? ANTHROPIC_BASE_URL : undefined} onChange={(event) => set({ baseUrl: event.target.value })} autoComplete="off" inputMode="url" />}</Field>}
        {draft.provider && draft.provider !== 'mock' && <Field label={isClaude ? 'Anthropic API key' : 'API key'} error={errors.apiKey} hint={keyHint}>{(props) =>
          <input {...props} type="password" value={draft.apiKey} onChange={(event) => set({ apiKey: event.target.value })} autoComplete="new-password" spellCheck={false}
            placeholder={draft.provider === 'ollama' ? 'Optional for local models' : isClaude ? 'sk-ant-...' : 'Required for external providers'} />}</Field>}
      </div>
      {isClaude && <fieldset className="modelRoutes">
        <legend>Model routing</legend>
        <p className="muted">Quick replies use a fast model; planning uses a capable one. Effort is how much the model thinks before answering.</p>
        <div className="formGrid modelRouteGrid">
          <Field label="Quick replies model" error={errors.fastModel} hint="Chat, voice and memory. Empty uses the planning model.">{(props) =>
            <input {...props} value={draft.fastModel} placeholder={ANTHROPIC_FAST_MODEL} onChange={(event) => set({ fastModel: event.target.value })} autoComplete="off" />}</Field>
          <Field label="Quick replies effort">{effortSelect(draft.fastEffort, (fastEffort) => set({ fastEffort }))}</Field>
          <Field label="Planning model" error={errors.capableModel} hint="Tasks and agent runs; also the workspace's default model.">{(props) =>
            <input {...props} value={draft.capableModel} placeholder={ANTHROPIC_CAPABLE_MODEL} onChange={(event) => set({ capableModel: event.target.value })} autoComplete="off" />}</Field>
          <Field label="Planning effort">{effortSelect(draft.capableEffort, (capableEffort) => set({ capableEffort }))}</Field>
        </div>
        <p className="muted">Anthropic has no embeddings: memory search uses ANUM's local embedder, so no text leaves the server for it. Clear the effort for a model that does not support it (for example claude-haiku-4-5).</p>
      </fieldset>}
      {draft.provider === 'ollama' && <p className="muted">Free and private: runs on your computer. Install Ollama, then run <code>ollama pull {draft.model || 'llama3.2'}</code> and <code>ollama serve</code>.</p>}
      {denied && <Forbidden detail={denied} />}
      {flash && <Notice error={flash.tone === 'error'}>{flash.text}</Notice>}
      <div className="actions">
        <button type="submit" disabled={!draft.provider || busy !== null}><Save aria-hidden="true" />{busy === 'save' ? 'Saving...' : 'Save model'}</button>
        <button type="button" className="secondary" disabled={busy !== null} onClick={() => void test()}><PlugZap aria-hidden="true" />{busy === 'test' ? 'Testing...' : 'Test connection'}</button>
      </div>
      {config && <p className="muted modelTestNote">The test calls the saved settings with a tiny request{config.provider === 'anthropic' ? ' per model (not cached, not counted against the budget)' : ''}.</p>}
    </form>}
  </section>;
}
