// The Sync tab of the web Calendar page (docs/calendar.md#sync): which calendars can be connected,
// the connected ones with their health and what to do when one needs attention, Sync now and
// Disconnect. Connecting hands the browser to the provider; it comes back to `#calendar` and the
// page finishes the connection (CalendarView). Owners connect; everyone sees the status.
import { useEffect, useId, useState } from 'react';
import { CalendarCheck, Link2, ListChecks, RefreshCw, Unplug } from 'lucide-react';
import * as calendarApi from './lib/calendarApi';
import { chosenIds, extraCalendars, minutesSince, type CalendarChoices, type SyncConnection, type SyncProviderSetup, type SyncSetup } from './lib/calendar';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog } from './Dialog';
import { PaneProblem } from './PageParts';

export function agoText(value: string | null, now: Date = new Date()): string {
  const minutes = minutesSince(value, now);
  if (minutes === null) return t('calendar.sync.never');
  const when = minutes < 1 ? t('calendar.sync.ago.now')
    : minutes < 60 ? t('calendar.sync.ago.minutes', { count: minutes })
      : minutes < 48 * 60 ? t('calendar.sync.ago.hours', { count: Math.round(minutes / 60) })
        : t('calendar.sync.ago.days', { count: Math.round(minutes / 1440) });
  return t('calendar.sync.last', { when });
}

const TONE = { connected: 'success', error: 'warning', reauth_required: 'dangerTone' } as const;

type ChoiceLoad = { kind: 'loading' } | { kind: 'error'; text: string } | { kind: 'ready'; value: CalendarChoices };

/** Which of the account's calendars sync (docs/calendar.md#choosing-calendars): the list comes fresh from
 * the provider; the primary calendar always syncs both ways, the others chosen are read only. */
export function CalendarChoicePanel({ connection, onSaved, onClose }: { connection: SyncConnection; onSaved: (note: string) => void; onClose: () => void }) {
  const [load, setLoad] = useState<ChoiceLoad>({ kind: 'loading' });
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ids = { heading: useId(), error: useId() };
  async function fetchList() {
    setLoad({ kind: 'loading' }); setError(null);
    try {
      const value = await calendarApi.listCalendars(connection.id);
      setLoad({ kind: 'ready', value }); setPicked(new Set(value.calendars.filter((c) => c.selected).map((c) => c.id)));
    } catch (e) { setLoad({ kind: 'error', text: describeError(e) }); }
  }
  useEffect(() => { void fetchList(); }, [connection.id]); // eslint-disable-line react-hooks/exhaustive-deps
  async function save() {
    if (load.kind !== 'ready') return;
    const chosen = chosenIds(load.value.calendars, picked, load.value.max_extra);
    if (!chosen.ok) { setError(t('calendar.choose.tooMany', { max: load.value.max_extra })); return; }
    setSaving(true); setError(null);
    try {
      const saved = await calendarApi.chooseCalendars(connection.id, chosen.ids);
      onSaved(t('calendar.choose.saved', { count: saved.calendars.filter((c) => c.selected && !c.primary).length, email: connection.email }));
    } catch (e) { setError(describeError(e)); } finally { setSaving(false); }
  }
  const toggle = (id: string, on: boolean) => setPicked((now) => { const next = new Set(now); if (on) next.add(id); else next.delete(id); return next; });
  return <section className="calendarChoices" aria-labelledby={ids.heading} aria-busy={load.kind === 'loading'}>
    <h4 id={ids.heading}>{t('calendar.choose.title', { email: connection.email })}</h4>
    <p className="muted">{t('calendar.choose.lead')}</p>
    {load.kind === 'loading' && <p className="notice" role="status">{t('calendar.choose.loading', { provider: connection.provider_name })}</p>}
    {load.kind === 'error' && <div className="notice errorNotice" role="alert"><p>{t('calendar.choose.loadFailed', { reason: load.text })}</p>
      <div className="actions"><button type="button" className="secondary" onClick={() => void fetchList()}>{t('calendar.choose.retry')}</button></div></div>}
    {load.kind === 'ready' && (load.value.calendars.length === 0
      ? <p className="notice" role="status">{t('calendar.choose.empty', { provider: connection.provider_name })}</p>
      : <fieldset className="calendarChoiceList" aria-describedby={error ? ids.error : undefined}>
        <legend className="visuallyHidden">{t('calendar.choose.legend')}</legend>
        {load.value.calendars.map((calendar) => <label key={calendar.id} className="calendarChoice">
          <input type="checkbox" checked={calendar.primary || picked.has(calendar.id)} disabled={calendar.primary || saving}
            onChange={(e) => toggle(calendar.id, e.target.checked)} />
          <span className="calendarChoiceSwatch" aria-hidden="true" style={calendar.color ? { background: calendar.color } : undefined} />
          <span className="calendarChoiceName"><bdi>{calendar.name}</bdi>
            <small className="muted">{calendar.primary ? t('calendar.choose.primary') : t('calendar.choose.readOnly')}</small></span>
        </label>)}
      </fieldset>)}
    {error && <p id={ids.error} className="notice errorNotice" role="alert">{error}</p>}
    <div className="actions">
      {load.kind === 'ready' && load.value.calendars.length > 0 && <button type="button" disabled={saving} onClick={() => void save()}>{saving ? t('calendar.choose.saving') : t('calendar.choose.save')}</button>}
      <button type="button" className="secondary" disabled={saving} onClick={onClose}>{t('calendar.choose.close')}</button>
    </div>
  </section>;
}

export function SyncPanel({ setup, onChanged }: { setup: SyncSetup; onChanged: (note: string) => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<SyncConnection | null>(null);
  const [choosing, setChoosing] = useState<string | null>(null);
  const headingId = useId();

  async function connect(provider: SyncProviderSetup) {
    setBusy(`connect:${provider.provider}`); setProblem(null);
    try {
      const started = await calendarApi.startConnect(provider.provider);
      window.location.assign(started.authorization_url);
    } catch (e) { setProblem(t('calendar.sync.connectFailed', { reason: describeError(e) })); setBusy(null); }
  }

  async function syncNow(connection: SyncConnection) {
    setBusy(`sync:${connection.id}`); setProblem(null);
    try {
      const result = await calendarApi.syncNow(connection.id);
      if (result.connection.last_sync_status === 'error') setProblem(t('calendar.sync.syncFailed', { reason: result.connection.last_error ?? '' }));
      onChanged(result.connection.last_sync_status === 'error' ? '' : t('calendar.sync.syncedNote', { email: connection.email }));
    } catch (e) { setProblem(t('calendar.sync.syncFailed', { reason: describeError(e) })); } finally { setBusy(null); }
  }

  async function disconnect(connection: SyncConnection) {
    setBusy(`disconnect:${connection.id}`); setProblem(null);
    try {
      const result = await calendarApi.disconnect(connection.id);
      setConfirm(null);
      onChanged(result.detail);
    } catch (e) { setConfirm(null); setProblem(t('calendar.sync.disconnectFailed', { reason: describeError(e) })); } finally { setBusy(null); }
  }

  return <div className="paneBody calendarSync">
    <p className="muted">{t('calendar.sync.intro')}</p>
    {problem && <PaneProblem text={problem} />}
    {!setup.can_manage && <p className="notice" role="status">{t('calendar.sync.ownersOnly')}</p>}

    <section aria-labelledby={`${headingId}-connections`}>
      <h3 id={`${headingId}-connections`} className="calendarSubhead">{t('calendar.sync.connections')}</h3>
      {!setup.connections.length && <div className="paneEmpty"><CalendarCheck aria-hidden="true" /><h3>{t('calendar.sync.none')}</h3><p>{t('calendar.sync.noneText')}</p></div>}
      {setup.connections.length > 0 && <ul className="calendarConnections">
        {setup.connections.map((connection) => {
          const provider = setup.providers.find((p) => p.provider === connection.provider);
          const summary = connection.last_summary;
          const pass = summary && (summary.created !== undefined);
          return <li key={connection.id} className={`calendarConnection ${connection.status}`}>
            <div className="calendarConnectionHead">
              <strong dir="ltr">{connection.email}</strong>
              <span className="calendarConnectionKind">{connection.provider_name}</span>
              <span className={`statusBadge ${TONE[connection.status]}`}>{t(`calendar.sync.status.${connection.status}` as MessageKey)}</span>
            </div>
            <p className="muted calendarConnectionMeta">
              {agoText(connection.last_sync_at)} · {t('calendar.sync.events', { count: connection.events_synced })}
              {connection.last_sync_at && <> · <time dateTime={connection.last_sync_at}>{formatDateTime(connection.last_sync_at, { dateStyle: 'medium', timeStyle: 'short' })}</time></>}
            </p>
            {pass && <p className="muted calendarConnectionMeta">
              {t('calendar.sync.pass', { created: summary.created ?? 0, updated: summary.updated ?? 0, deleted: summary.deleted ?? 0, pushed: summary.pushed ?? 0 })}
              {((summary.conflicts_remote_won ?? 0) + (summary.conflicts_local_won ?? 0)) > 0 && ` ${t('calendar.sync.conflicts', { remote: summary.conflicts_remote_won ?? 0, local: summary.conflicts_local_won ?? 0 })}`}
            </p>}
            {extraCalendars(connection).length > 0 && <p className="muted calendarConnectionMeta">
              {t('calendar.choose.also', { names: extraCalendars(connection).map((c) => c.name).join(', ') })}</p>}
            {(summary?.calendar_errors?.length ?? 0) > 0 && <p className="notice" role="status">
              {t('calendar.choose.readFailed', { reasons: (summary.calendar_errors ?? []).join('; ') })}</p>}
            {connection.status !== 'connected' && <div className="notice errorNotice" role="alert">
              {connection.last_error && <p>{t('calendar.sync.error', { reason: connection.last_error })}</p>}
              <p>{t(`calendar.sync.fix.${connection.status}` as MessageKey, { provider: connection.provider_name })}</p>
            </div>}
            {setup.can_manage && <div className="actions">
              {connection.status === 'reauth_required' && provider?.configured
                && <button type="button" disabled={busy !== null} onClick={() => void connect(provider)}><Link2 aria-hidden="true" />{t('calendar.sync.reconnect', { provider: connection.provider_name })}</button>}
              <button type="button" className="secondary" disabled={busy !== null || connection.status === 'reauth_required'} onClick={() => void syncNow(connection)}>
                <RefreshCw aria-hidden="true" />{busy === `sync:${connection.id}` ? t('calendar.sync.syncing') : t('calendar.sync.now')}</button>
              {connection.status !== 'reauth_required' && <button type="button" className="secondary" disabled={busy !== null} aria-expanded={choosing === connection.id}
                onClick={() => setChoosing(choosing === connection.id ? null : connection.id)}><ListChecks aria-hidden="true" />{t('calendar.choose.open')}</button>}
              <button type="button" className="secondary" disabled={busy !== null} onClick={() => setConfirm(connection)}><Unplug aria-hidden="true" />{t('calendar.sync.disconnect')}</button>
            </div>}
            {setup.can_manage && choosing === connection.id && <CalendarChoicePanel connection={connection} onClose={() => setChoosing(null)}
              onSaved={(note) => { setChoosing(null); onChanged(note); }} />}
          </li>;
        })}
      </ul>}
    </section>

    <section aria-labelledby={`${headingId}-providers`}>
      <h3 id={`${headingId}-providers`} className="calendarSubhead">{t('calendar.sync.providers')}</h3>
      <ul className="calendarProviders">
        {setup.providers.map((provider) => <li key={provider.provider}>
          <div>
            <strong>{provider.name}</strong>
            {!provider.configured && <>
              <span className="statusBadge neutral">{t('calendar.sync.notSetUp')}</span>
              <p className="muted">{t('calendar.sync.notSetUpText', { provider: provider.name, settings: provider.missing.join(', ') })}</p>
            </>}
          </div>
          {setup.can_manage && provider.configured && <button type="button" className="secondary" disabled={busy !== null} onClick={() => void connect(provider)}>
            <Link2 aria-hidden="true" />{busy === `connect:${provider.provider}` ? t('calendar.sync.opening', { provider: provider.name })
              : setup.connections.some((c) => c.provider === provider.provider) ? t('calendar.sync.connectAnother', { provider: provider.name }) : t('calendar.sync.connect', { provider: provider.name })}</button>}
        </li>)}
      </ul>
      {setup.can_manage && setup.providers.some((p) => p.configured) && <p className="muted calendarRedirect">{t('calendar.sync.redirect', { uri: setup.redirect_uri })}</p>}
    </section>

    <section className="calendarNotes" aria-label={t('calendar.sync.rule')}>
      <h3>{t('calendar.sync.rule')}</h3>
      <p>{t('calendar.sync.ruleText')}</p>
    </section>

    {confirm && <ConfirmDialog title={t('calendar.sync.disconnectTitle')} confirmLabel={t('calendar.sync.disconnect')} danger busy={busy === `disconnect:${confirm.id}`}
      onCancel={() => setConfirm(null)} onConfirm={() => void disconnect(confirm)}>
      <p>{t('calendar.sync.disconnectText', { email: confirm.email, provider: confirm.provider_name })}</p>
    </ConfirmDialog>}
  </div>;
}
