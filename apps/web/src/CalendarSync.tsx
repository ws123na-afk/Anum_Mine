// The Sync tab of the web Calendar page (docs/calendar.md#sync): which calendars can be connected,
// the connected ones with their health and what to do when one needs attention, Sync now and
// Disconnect. Connecting hands the browser to the provider; it comes back to `#calendar` and the
// page finishes the connection (CalendarView). Owners connect; everyone sees the status.
import { useId, useState } from 'react';
import { CalendarCheck, Link2, RefreshCw, Unplug } from 'lucide-react';
import * as calendarApi from './lib/calendarApi';
import { minutesSince, type SyncConnection, type SyncProviderSetup, type SyncSetup } from './lib/calendar';
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

export function SyncPanel({ setup, onChanged }: { setup: SyncSetup; onChanged: (note: string) => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<SyncConnection | null>(null);
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
            {connection.status !== 'connected' && <div className="notice errorNotice" role="alert">
              {connection.last_error && <p>{t('calendar.sync.error', { reason: connection.last_error })}</p>}
              <p>{t(`calendar.sync.fix.${connection.status}` as MessageKey, { provider: connection.provider_name })}</p>
            </div>}
            {setup.can_manage && <div className="actions">
              {connection.status === 'reauth_required' && provider?.configured
                && <button type="button" disabled={busy !== null} onClick={() => void connect(provider)}><Link2 aria-hidden="true" />{t('calendar.sync.reconnect', { provider: connection.provider_name })}</button>}
              <button type="button" className="secondary" disabled={busy !== null || connection.status === 'reauth_required'} onClick={() => void syncNow(connection)}>
                <RefreshCw aria-hidden="true" />{busy === `sync:${connection.id}` ? t('calendar.sync.syncing') : t('calendar.sync.now')}</button>
              <button type="button" className="secondary" disabled={busy !== null} onClick={() => setConfirm(connection)}><Unplug aria-hidden="true" />{t('calendar.sync.disconnect')}</button>
            </div>}
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
