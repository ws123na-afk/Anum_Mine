// Tyre planning > Connection: the owner's wizard (source, credential and test, discover and map,
// sync). Everyone else sees the state read-only and who can change it (docs/tyre-planning.md).
import { useEffect, useId, useState, type FormEvent } from 'react';
import { AlertTriangle, CheckCircle2, Database, Eye, KeyRound, Plug, RefreshCw, Save, Search, Trash2, Wand2 } from 'lucide-react';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import {
  ENTITIES, SOURCE_KINDS, connectionBody, connectionDraft, emptyMapping, entityLabel, mappingProblems, setEntityField, setEntitySource, sourceKindLabel, wizardSteps,
  type ConnectionDraft, type EntityName, type PreviewResult, type SourceTable, type TyreMapping, type TyreSetup as Setup,
} from './lib/tyre';
import * as tyreApi from './lib/tyreApi';

interface Props { setup: Setup; onChange: (setup: Setup) => void; onSync: () => Promise<void>; syncing: boolean }

export function TyreSetup({ setup, onChange, onSync, syncing }: Props) {
  const steps = wizardSteps(setup);
  return <div className="tyreStack">
    <ol className="tyreSteps" aria-label={t('tyre.connect.stepsLabel')}>
      {steps.map((step, index) => <li key={step.key} className={`tyreStep ${step.state}`} aria-current={step.state === 'current' ? 'step' : undefined}>
        <span className="tyreStepDot" aria-hidden="true">{step.state === 'done' ? <CheckCircle2 /> : index + 1}</span>
        <span>{t(`tyre.step.${step.key}` as MessageKey)}<small>{t(`tyre.stepState.${step.state}` as MessageKey)}</small></span>
      </li>)}
    </ol>
    {!setup.can_manage && <p className="notice" role="note"><KeyRound aria-hidden="true" />{t('tyre.connect.ownerOnly')}</p>}
    <ConnectionCard setup={setup} onChange={onChange} />
    {setup.connection && <MappingCard setup={setup} onChange={onChange} />}
    {setup.connection && setup.mapping && <SyncCard setup={setup} onSync={onSync} syncing={syncing} />}
  </div>;
}

function ConnectionCard({ setup, onChange }: { setup: Setup; onChange: (setup: Setup) => void }) {
  const saved = setup.connection;
  const [draft, setDraft] = useState<ConnectionDraft>(() => connectionDraft(saved));
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  const [editing, setEditing] = useState(!saved);
  const ids = { label: useId(), endpoint: useId(), schema: useId(), secret: useId() };
  useEffect(() => { setDraft(connectionDraft(saved)); }, [saved]);
  const disabled = !setup.can_manage || busy !== null;

  async function act(name: string, work: () => Promise<void>) {
    setBusy(name); setMessage(null);
    try { await work(); } catch (e) { setMessage({ text: describeError(e), error: true }); } finally { setBusy(null); }
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    const body = connectionBody(draft, saved);
    if (!body.ok) { setMessage({ text: t(body.error), error: true }); return; }
    await act('save', async () => {
      const next = await tyreApi.saveConnection(body.value);
      onChange(next); setEditing(false);
      setMessage({ text: t('tyre.connect.saved'), error: false });
    });
  }
  const test = () => act('test', async () => {
    const result = await tyreApi.testConnection();
    onChange(result.setup);
    setMessage({ text: result.message, error: !result.ok });
  });
  const disconnect = () => act('delete', async () => {
    if (!confirm(t('tyre.connect.confirmDisconnect'))) return;
    await tyreApi.deleteConnection();
    onChange(await tyreApi.getSetup());
    setEditing(true);
    setMessage({ text: t('tyre.connect.disconnected'), error: false });
  });

  const secretLabel = t(`tyre.connect.secret.${draft.kind}` as MessageKey);
  return <section className="surface tyreCard" aria-labelledby="tyre-connection-title">
    <div className="sectionHeader">
      <h2 id="tyre-connection-title"><Plug aria-hidden="true" />{t('tyre.connect.title')}</h2>
      {saved && <span className={`statusBadge ${saved.status === 'ok' ? 'success' : saved.status === 'failed' ? 'dangerTone' : 'neutral'}`}>{t(`tyre.connStatus.${saved.status}` as MessageKey)}</span>}
    </div>
    {saved && !editing && <dl className="tyreFacts">
      <div><dt>{t('tyre.connect.kind')}</dt><dd>{sourceKindLabel(saved.kind)}</dd></div>
      <div><dt>{t('tyre.connect.label')}</dt><dd>{saved.label}</dd></div>
      {saved.endpoint && <div><dt>{t('tyre.connect.where')}</dt><dd dir="ltr">{saved.endpoint}</dd></div>}
      <div><dt>{t('tyre.connect.credential')}</dt><dd dir="ltr">{saved.secret_hint ?? t('tyre.connect.notSaved')}</dd></div>
      <div><dt>{t('tyre.connect.lastTested')}</dt><dd>{saved.last_tested_at ? formatDateTime(saved.last_tested_at) : t('tyre.connect.never')}</dd></div>
    </dl>}
    {saved?.last_error && <p className="notice errorNotice" role="alert"><AlertTriangle aria-hidden="true" />{saved.last_error}</p>}
    {(editing || !saved) && setup.can_manage && <form onSubmit={(e) => void save(e)} className="tyreForm">
      <fieldset className="tyreKinds">
        <legend>{t('tyre.connect.kind')}</legend>
        {SOURCE_KINDS.map((kind) => <label key={kind} className={`tyreKind ${draft.kind === kind ? 'selected' : ''}`}>
          <input type="radio" name="tyre-kind" value={kind} checked={draft.kind === kind} onChange={() => setDraft({ ...draft, kind, secret: '' })} />
          <span><strong>{sourceKindLabel(kind)}</strong><small>{t(`tyre.kindHelp.${kind}` as MessageKey)}</small></span>
        </label>)}
      </fieldset>
      <div className="tyreFormGrid">
        <label className="field" htmlFor={ids.label}><span>{t('tyre.connect.label')}</span><input id={ids.label} value={draft.label} maxLength={120} onChange={(e) => setDraft({ ...draft, label: e.target.value })} /></label>
        {draft.kind === 'supabase_rest' && <label className="field" htmlFor={ids.endpoint}><span>{t('tyre.connect.projectUrl')}</span><input id={ids.endpoint} dir="ltr" inputMode="url" placeholder="https://<project-ref>.supabase.co" value={draft.endpoint} onChange={(e) => setDraft({ ...draft, endpoint: e.target.value })} /></label>}
        {draft.kind === 'firestore' && <label className="field" htmlFor={ids.endpoint}><span>{t('tyre.connect.databaseId')}</span><input id={ids.endpoint} dir="ltr" placeholder="(default)" value={draft.endpoint} onChange={(e) => setDraft({ ...draft, endpoint: e.target.value })} /></label>}
        {draft.kind !== 'firestore' && <label className="field" htmlFor={ids.schema}><span>{t('tyre.connect.schema')}</span><input id={ids.schema} dir="ltr" value={draft.schema_name} onChange={(e) => setDraft({ ...draft, schema_name: e.target.value })} /></label>}
      </div>
      <label className="field" htmlFor={ids.secret}>
        <span>{secretLabel}</span>
        {draft.kind === 'firestore'
          ? <textarea id={ids.secret} dir="ltr" autoComplete="off" spellCheck={false} placeholder={saved?.credential_configured && saved.kind === 'firestore' ? t('tyre.connect.keepSecret', { hint: saved.secret_hint ?? '' }) : '{ "type": "service_account", ... }'} value={draft.secret} onChange={(e) => setDraft({ ...draft, secret: e.target.value })} />
          : <input id={ids.secret} dir="ltr" type="password" autoComplete="new-password" spellCheck={false} placeholder={saved?.credential_configured && saved.kind === draft.kind ? t('tyre.connect.keepSecret', { hint: saved.secret_hint ?? '' }) : draft.kind === 'supabase_postgres' ? 'postgresql://anum_reader:...@db.<ref>.supabase.co:5432/postgres?sslmode=require' : ''} value={draft.secret} onChange={(e) => setDraft({ ...draft, secret: e.target.value })} />}
      </label>
      <p className="muted tyreHelp">{t(`tyre.connect.howTo.${draft.kind}` as MessageKey)}</p>
      <div className="actions">
        <button type="submit" disabled={disabled}><Save aria-hidden="true" />{busy === 'save' ? t('tyre.common.saving') : t('tyre.connect.save')}</button>
        {saved && <button type="button" className="secondary" disabled={disabled} onClick={() => { setDraft(connectionDraft(saved)); setEditing(false); }}>{t('common.cancel')}</button>}
      </div>
    </form>}
    {saved && !editing && setup.can_manage && <div className="actions">
      <button type="button" disabled={disabled} onClick={() => void test()}><CheckCircle2 aria-hidden="true" />{busy === 'test' ? t('tyre.connect.testing') : t('tyre.connect.test')}</button>
      <button type="button" className="secondary" disabled={disabled} onClick={() => setEditing(true)}><KeyRound aria-hidden="true" />{t('tyre.connect.edit')}</button>
      <button type="button" className="danger" disabled={disabled} onClick={() => void disconnect()}><Trash2 aria-hidden="true" />{t('tyre.connect.disconnect')}</button>
    </div>}
    {message && <p className={`notice ${message.error ? 'errorNotice' : ''}`} role={message.error ? 'alert' : 'status'}>{message.error ? <AlertTriangle aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}{message.text}</p>}
  </section>;
}

function MappingCard({ setup, onChange }: { setup: Setup; onChange: (setup: Setup) => void }) {
  const [mapping, setMapping] = useState<TyreMapping>(() => setup.mapping ?? emptyMapping());
  const [tables, setTables] = useState<SourceTable[]>(() => setup.discovery?.tables ?? []);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [newStatus, setNewStatus] = useState('');
  const disabled = !setup.can_manage || busy !== null;
  const problems = mappingProblems(mapping, setup.fields);
  useEffect(() => { setTables(setup.discovery?.tables ?? []); }, [setup.discovery]);

  async function act(name: string, work: () => Promise<void>) {
    setBusy(name); setMessage(null);
    try { await work(); } catch (e) { setMessage({ text: describeError(e), error: true }); } finally { setBusy(null); }
  }
  const runDiscovery = () => act('discover', async () => {
    const found = await tyreApi.discover();
    setTables(found.tables);
    if (!setup.mapping && found.suggestion) setMapping(found.suggestion);
    onChange(await tyreApi.getSetup());
    setMessage({ text: t('tyre.map.discovered', { count: found.tables.length }), error: false });
  });
  const usePreset = () => act('preset', async () => { setMapping(await tyreApi.getPreset('tyrepulse')); setPreview(null); });
  const runPreview = () => act('preview', async () => setPreview(await tyreApi.previewMapping(mapping)));
  const save = () => act('save', async () => {
    const next = await tyreApi.saveMapping(mapping);
    onChange(next);
    setMessage({ text: next.mapping_problems.length ? t('tyre.map.savedWithProblems') : t('tyre.map.saved'), error: false });
  });

  const writable = setup.discovery?.writable_tables ?? [];
  return <section className="surface tyreCard" aria-labelledby="tyre-mapping-title">
    <div className="sectionHeader">
      <h2 id="tyre-mapping-title"><Database aria-hidden="true" />{t('tyre.map.title')}</h2>
      {mapping.preset && <span className="statusBadge success">{t('tyre.map.presetBadge')}</span>}
    </div>
    <p className="muted">{t('tyre.map.intro')}</p>
    {setup.can_manage && <div className="actions">
      <button type="button" disabled={disabled} onClick={() => void runDiscovery()}><Search aria-hidden="true" />{busy === 'discover' ? t('tyre.map.discovering') : t('tyre.map.discover')}</button>
      <button type="button" className="secondary" disabled={disabled} onClick={() => void usePreset()}><Wand2 aria-hidden="true" />{t('tyre.map.usePreset')}</button>
    </div>}
    {setup.discovery?.discovered_at && <p className="muted">{t('tyre.map.lastDiscovered', { count: tables.length, when: formatDateTime(setup.discovery.discovered_at) })}</p>}
    {writable.length > 0 && <p className="notice errorNotice" role="alert"><AlertTriangle aria-hidden="true" />{t('tyre.map.writable', { tables: writable.slice(0, 5).join(', ') })}</p>}
    {!tables.length && <p className="notice" role="status"><Search aria-hidden="true" />{setup.can_manage ? t('tyre.map.needDiscovery') : t('tyre.map.noDiscovery')}</p>}
    {tables.length > 0 && <>
      <div className="tyreEntities">
        {ENTITIES.map((entity) => <EntityEditor key={entity} entity={entity} mapping={mapping} tables={tables} fields={setup.fields[entity] ?? []} disabled={!setup.can_manage} onChange={(next) => { setMapping(next); setPreview(null); }} />)}
      </div>
      <fieldset className="tyreUnits" disabled={!setup.can_manage}>
        <legend>{t('tyre.map.units')}</legend>
        <label className="field"><span>{t('tyre.map.unitTread')}</span><select value={mapping.units.tread} onChange={(e) => setMapping({ ...mapping, units: { ...mapping.units, tread: e.target.value as 'mm' } })}><option value="mm">mm</option><option value="32nds">1/32 in</option></select></label>
        <label className="field"><span>{t('tyre.map.unitPressure')}</span><select value={mapping.units.pressure} onChange={(e) => setMapping({ ...mapping, units: { ...mapping.units, pressure: e.target.value as 'psi' } })}><option value="psi">psi</option><option value="bar">bar</option><option value="kpa">kPa</option></select></label>
        <label className="field"><span>{t('tyre.map.unitDistance')}</span><select value={mapping.units.distance} onChange={(e) => setMapping({ ...mapping, units: { ...mapping.units, distance: e.target.value as 'km' } })}><option value="km">km</option><option value="miles">{t('tyre.map.miles')}</option></select></label>
        <label className="field"><span>{t('tyre.map.currency')}</span><input dir="ltr" maxLength={3} value={mapping.units.currency} onChange={(e) => setMapping({ ...mapping, units: { ...mapping.units, currency: e.target.value.toUpperCase() } })} /></label>
      </fieldset>
      <fieldset className="tyreStatuses" disabled={!setup.can_manage}>
        <legend>{t('tyre.map.statusValues')}</legend>
        <p className="muted">{t('tyre.map.statusHelp')}</p>
        <ul>
          {Object.entries(mapping.status_values).map(([raw, canonical]) => <li key={raw}>
            <code dir="ltr">{raw}</code>
            <select aria-label={t('tyre.map.statusFor', { value: raw })} value={canonical} onChange={(e) => setMapping({ ...mapping, status_values: { ...mapping.status_values, [raw]: e.target.value } })}>
              {(['fitted', 'stock', 'removed', 'scrapped'] as const).map((value) => <option key={value} value={value}>{t(`tyre.tyreStatus.${value}` as MessageKey)}</option>)}
            </select>
            <button type="button" className="secondary iconOnly" aria-label={t('tyre.map.removeStatus', { value: raw })} onClick={() => { const next = { ...mapping.status_values }; delete next[raw]; setMapping({ ...mapping, status_values: next }); }}><Trash2 aria-hidden="true" /></button>
          </li>)}
        </ul>
        <div className="tyreInline">
          <input aria-label={t('tyre.map.newStatus')} placeholder={t('tyre.map.newStatus')} value={newStatus} onChange={(e) => setNewStatus(e.target.value)} />
          <button type="button" className="secondary" disabled={!newStatus.trim()} onClick={() => { setMapping({ ...mapping, status_values: { ...mapping.status_values, [newStatus.trim().toLowerCase()]: 'fitted' } }); setNewStatus(''); }}>{t('tyre.map.addStatus')}</button>
        </div>
      </fieldset>
      {problems.length > 0 && <ul className="tyreProblems" aria-label={t('tyre.map.problems')}>{problems.map((problem, index) => <li key={index}><AlertTriangle aria-hidden="true" />{t(problem.key, problem.vars)}</li>)}</ul>}
      {setup.can_manage && <div className="actions">
        <button type="button" className="secondary" disabled={disabled || problems.length > 0} onClick={() => void runPreview()}><Eye aria-hidden="true" />{busy === 'preview' ? t('tyre.map.previewing') : t('tyre.map.preview')}</button>
        <button type="button" disabled={disabled} onClick={() => void save()}><Save aria-hidden="true" />{busy === 'save' ? t('tyre.common.saving') : t('tyre.map.save')}</button>
      </div>}
      {preview && <PreviewTable preview={preview} />}
    </>}
    {message && <p className={`notice ${message.error ? 'errorNotice' : ''}`} role={message.error ? 'alert' : 'status'}>{message.text}</p>}
  </section>;
}

function EntityEditor({ entity, mapping, tables, fields, disabled, onChange }: { entity: EntityName; mapping: TyreMapping; tables: SourceTable[]; fields: Setup['fields'][EntityName]; disabled: boolean; onChange: (mapping: TyreMapping) => void }) {
  const current = mapping.entities[entity];
  const columns = tables.find((table) => table.name === current?.source)?.columns ?? [];
  const id = useId();
  return <details className="tyreEntity" open={entity === 'tyres' || undefined}>
    <summary><strong>{entityLabel(entity)}</strong><small>{current ? `${current.source} · ${t('tyre.map.fieldsMapped', { count: Object.keys(current.fields).length })}` : t('tyre.map.notMapped')}</small></summary>
    <label className="field" htmlFor={`${id}-source`}><span>{t('tyre.map.sourceTable')}</span>
      <select id={`${id}-source`} disabled={disabled} value={current?.source ?? ''} onChange={(e) => onChange(setEntitySource(mapping, entity, e.target.value, tables))}>
        <option value="">{t('tyre.map.notMapped')}</option>
        {tables.map((table) => <option key={table.name} value={table.name}>{table.name}{table.rows !== null ? ` (${table.rows})` : ''}</option>)}
      </select>
    </label>
    {current && <div className="tyreFieldGrid">
      {fields.map((spec) => <label key={spec.name} className="field" htmlFor={`${id}-${spec.name}`}>
        <span>{spec.name}{spec.required ? ' *' : ''}<small>{spec.description}</small></span>
        <select id={`${id}-${spec.name}`} disabled={disabled} value={current.fields[spec.name] ?? ''} aria-required={spec.required || undefined} onChange={(e) => onChange(setEntityField(mapping, entity, spec.name, e.target.value))}>
          <option value="">{t('tyre.map.noColumn')}</option>
          {columns.map((column) => <option key={column.name} value={column.name}>{column.name} ({column.type})</option>)}
        </select>
      </label>)}
    </div>}
  </details>;
}

function PreviewTable({ preview }: { preview: PreviewResult }) {
  return <div className="tyreTableWrap" tabIndex={0} role="region" aria-label={t('tyre.map.previewTitle')}>
    <table className="tyreTable">
      <caption>{t('tyre.map.previewTitle')}</caption>
      <thead><tr><th scope="col">{t('tyre.map.entity')}</th><th scope="col">{t('tyre.map.read')}</th><th scope="col">{t('tyre.map.kept')}</th><th scope="col">{t('tyre.map.skipped')}</th><th scope="col">{t('tyre.map.why')}</th></tr></thead>
      <tbody>{Object.entries(preview.stats).map(([entity, stats]) => <tr key={entity}>
        <th scope="row">{entityLabel(entity as EntityName)}</th><td>{stats.read}</td><td>{stats.kept}</td><td>{stats.skipped}</td>
        <td>{Object.entries(stats.issues).map(([reason, count]) => `${reason} (${count})`).join('; ') || '-'}</td>
      </tr>)}</tbody>
    </table>
  </div>;
}

function SyncCard({ setup, onSync, syncing }: { setup: Setup; onSync: () => Promise<void>; syncing: boolean }) {
  const sync = setup.sync;
  const emptyTyres = sync && (sync.counts.tyres ?? 0) === 0;
  return <section className="surface tyreCard" aria-labelledby="tyre-sync-title">
    <div className="sectionHeader">
      <h2 id="tyre-sync-title"><RefreshCw aria-hidden="true" />{t('tyre.sync.title')}</h2>
      {sync && <span className="statusBadge success">{t('tyre.sync.lastSynced', { when: formatDateTime(sync.synced_at) })}</span>}
    </div>
    {setup.mapping_problems.length > 0 && <p className="notice errorNotice" role="alert"><AlertTriangle aria-hidden="true" />{t('tyre.sync.fixMapping')}</p>}
    {sync ? <div className="tyreTableWrap" tabIndex={0} role="region" aria-label={t('tyre.sync.tableTitle')}>
      <table className="tyreTable">
        <caption>{t('tyre.sync.tableTitle')}</caption>
        <thead><tr><th scope="col">{t('tyre.map.entity')}</th><th scope="col">{t('tyre.map.read')}</th><th scope="col">{t('tyre.map.kept')}</th><th scope="col">{t('tyre.map.skipped')}</th><th scope="col">{t('tyre.map.why')}</th></tr></thead>
        <tbody>{Object.entries(sync.entities).map(([entity, stats]) => <tr key={entity}>
          <th scope="row">{entityLabel(entity as EntityName)}</th><td>{stats.read}{stats.cut ? ` ${t('tyre.sync.cut')}` : ''}</td><td>{stats.kept}</td><td>{stats.skipped}</td>
          <td>{Object.entries(stats.issues).map(([reason, count]) => `${reason} (${count})`).join('; ') || '-'}</td>
        </tr>)}</tbody>
      </table>
    </div> : <p className="muted">{t('tyre.sync.never')}</p>}
    {emptyTyres && <p className="notice errorNotice" role="alert"><AlertTriangle aria-hidden="true" />{t('tyre.sync.noRows')}</p>}
    {setup.can_operate
      ? <div className="actions"><button type="button" disabled={syncing || setup.mapping_problems.length > 0} onClick={() => void onSync()}><RefreshCw aria-hidden="true" />{syncing ? t('tyre.sync.syncing') : t('tyre.sync.now')}</button></div>
      : <p className="muted">{t('tyre.sync.whoCan')}</p>}
  </section>;
}
