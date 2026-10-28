// Settings > Workspace: how long meeting transcripts and visitor records are kept (owner decision O5,
// docs/record-retention.md). The API owns the periods and who may change them (GET/PUT /api/v1/workspace/record-retention):
// every member reads them, an owner changes them with a click and the revision they saw. Rendered inside the lazy
// DataLifecycle chunk.
import { useCallback, useEffect, useId, useState } from 'react';
import { CircleDot, History, MessageSquareText, RefreshCw, Save, Undo2, UserRound } from 'lucide-react';
import { apiRequest } from './lib/api';
import { ApiError, describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t } from './lib/i18n';
import {
  createRetentionClient, parseDays, saveBody, type RecordRetention, type RetentionKind, type RetentionPeriod,
} from './lib/recordRetention';
import './recordRetention.css';

const client = createRetentionClient(apiRequest);
type Load = { kind: 'loading' } | { kind: 'ready'; value: RecordRetention } | { kind: 'error'; detail: string; denied: boolean };
type Flash = { kind: RetentionKind; text: string; error?: boolean } | null;

const period = (days: number) => t('recordRetention.days', { count: days });

export function RecordRetentionCard() {
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [drafts, setDrafts] = useState<Record<RetentionKind, string>>({ transcripts: '', visitors: '' });
  const [busy, setBusy] = useState<RetentionKind | null>(null);
  const [flash, setFlash] = useState<Flash>(null);
  const label = t('recordRetention.title');

  const show = useCallback((value: RecordRetention) => {
    setLoad({ kind: 'ready', value });
    setDrafts({ transcripts: String(value.transcripts.days), visitors: String(value.visitors.days) });
  }, []);
  const read = useCallback(() => {
    setLoad({ kind: 'loading' });
    void client.get().then(show, (error: unknown) => setLoad({ kind: 'error', detail: describeError(error), denied: isPermissionDenied(error) }));
  }, [show]);
  useEffect(read, [read]);

  if (load.kind === 'loading') return <section className="surface workspaceAdmin recordRetention" aria-label={label} aria-busy="true">
    <h2>{label}</h2><p className="notice" role="status"><CircleDot aria-hidden="true" />{t('recordRetention.loading')}</p>
  </section>;
  if (load.kind === 'error') return <section className="surface workspaceAdmin recordRetention" aria-label={label}>
    <h2>{label}</h2>
    <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />
      {load.denied ? t('recordRetention.denied', { message: load.detail }) : t('recordRetention.error', { message: load.detail })}</p>
    {!load.denied && <div className="actions"><button type="button" className="secondary" onClick={read}><RefreshCw aria-hidden="true" />{t('recordRetention.retry')}</button></div>}
  </section>;

  const value = load.value;
  const save = (kind: RetentionKind, days: number | null) => {
    setBusy(kind); setFlash(null);
    void client.save(saveBody(value, kind, days))
      .then((next) => {
        show(next);
        setFlash({ kind, text: days === null ? t('recordRetention.savedStandard', { period: period(next[kind].days) }) : t('recordRetention.saved', { period: period(next[kind].days) }) });
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 409) {
          setFlash({ kind, text: t('recordRetention.stale'), error: true });
          void client.get().then(show, () => undefined);
          return;
        }
        setFlash({ kind, text: describeError(error), error: true });
      })
      .finally(() => setBusy(null));
  };
  const submit = (kind: RetentionKind) => {
    const parsed = parseDays(drafts[kind], value[kind]);
    if (!parsed.ok) {
      setFlash({ kind, error: true, text: parsed.reason === 'range'
        ? t('recordRetention.invalid.range', { min: value[kind].minDays, max: value[kind].maxDays })
        : t(parsed.reason === 'empty' ? 'recordRetention.invalid.empty' : 'recordRetention.invalid.notWhole') });
      return;
    }
    save(kind, parsed.value);
  };

  return <section className="surface workspaceAdmin recordRetention" aria-label={label}>
    <div className="sectionHeader"><div><h2>{label}</h2><p className="muted">{t('recordRetention.lead')}</p></div></div>
    <div className="recordRetentionCards">
      {(['transcripts', 'visitors'] as const).map((kind) => <PeriodCard key={kind} kind={kind} period={value[kind]} canManage={value.canManage}
        draft={drafts[kind]} onDraft={(text) => setDrafts((current) => ({ ...current, [kind]: text }))}
        busy={busy} flash={flash?.kind === kind ? flash : null} onSubmit={() => submit(kind)} onStandard={() => save(kind, null)} />)}
    </div>
    <p className="recordRetentionNote muted"><History aria-hidden="true" />{t('recordRetention.snapshots')}</p>
    <p className="muted">{t('recordRetention.daily')}</p>
    <p className="muted">{value.updatedBy && value.updatedAt
      ? t('recordRetention.lastChanged', { user: value.updatedBy, when: formatDateTime(value.updatedAt) })
      : t('recordRetention.isDefault')}</p>
    {!value.canManage && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('recordRetention.ownerOnly')}</p>}
  </section>;
}

function PeriodCard({ kind, period: p, canManage, draft, onDraft, busy, flash, onSubmit, onStandard }: {
  kind: RetentionKind; period: RetentionPeriod; canManage: boolean; draft: string; onDraft: (text: string) => void;
  busy: RetentionKind | null; flash: Flash; onSubmit: () => void; onStandard: () => void;
}) {
  const inputId = useId(), helpId = useId(), flashId = useId();
  const title = t(`recordRetention.${kind}.title`);
  const Icon = kind === 'transcripts' ? MessageSquareText : UserRound;
  return <section className="recordRetentionCard" aria-label={title}>
    <div className="recordRetentionHead">
      <h3><Icon aria-hidden="true" />{title}</h3>
      <span className={`statusBadge ${p.isDefault ? 'neutral' : 'success'}`}>{t(p.isDefault ? 'recordRetention.badge.standard' : 'recordRetention.badge.changed')}</span>
    </div>
    <p className="recordRetentionDays" data-testid={`retention-${kind}`}>{period(p.days)}</p>
    <p className="muted">{t(`recordRetention.${kind}.text`, { period: period(p.days) })}</p>
    {!p.isDefault && <p className="muted">{t('recordRetention.standardIs', { period: period(p.defaultDays) })}</p>}
    {canManage && <form className="recordRetentionForm" onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
      <label className="field" htmlFor={inputId}><span>{t('recordRetention.daysLabel', { min: p.minDays, max: p.maxDays })}</span>
        <input id={inputId} dir="ltr" inputMode="numeric" autoComplete="off" value={draft} disabled={busy !== null}
          aria-describedby={flash ? `${helpId} ${flashId}` : helpId} aria-invalid={flash?.error ? true : undefined}
          onChange={(event) => onDraft(event.target.value)} /></label>
      <p id={helpId} className="muted">{t('recordRetention.standardIs', { period: period(p.defaultDays) })}</p>
      <div className="actions">
        <button type="submit" disabled={busy !== null}><Save aria-hidden="true" />{busy === kind ? t('admin.saving') : t('recordRetention.save')}</button>
        {!p.isDefault && <button type="button" className="secondary" disabled={busy !== null} onClick={onStandard}>
          <Undo2 aria-hidden="true" />{t('recordRetention.useStandard', { period: period(p.defaultDays) })}</button>}
      </div>
    </form>}
    {flash && <p id={flashId} className={`notice ${flash.error ? 'errorNotice' : ''}`} role={flash.error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{flash.text}</p>}
  </section>;
}
