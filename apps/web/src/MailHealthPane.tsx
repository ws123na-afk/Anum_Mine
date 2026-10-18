// The platform operator's mail health (docs/email.md#operator-view), shown in the platform console
// under the organisations: per organisation the Email Center's mailboxes, last checks and send
// outcomes, and the mail server's settings with the optional read-only Mailcow status.
// Counts, states and timestamps only: the API never sends a message, subject or address here.
import { useCallback, useEffect, useState } from 'react';
import { Mail, Server } from 'lucide-react';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import {
  MAIL_HEALTH_DAYS, mailHealthMatches, mailHealthTone, notConnected, rateLimited, sortMailHealth,
  type MailOperatorView, type MailServerView,
} from './lib/mailOps';
import { getMailHealth, getMailServer } from './lib/mailOpsApi';
import { PaneProblem } from './PageParts';

/** A value isolated from the surrounding text direction (host names and numbers inside Arabic). */
const iso = (value: string | number): string => `\u2068${value}\u2069`;

type Load<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'error'; message: string };

export function MailHealthPane() {
  const [days, setDays] = useState<number>(7);
  const [health, setHealth] = useState<Load<MailOperatorView>>({ state: 'loading' });
  const [server, setServer] = useState<Load<MailServerView>>({ state: 'loading' });
  const [query, setQuery] = useState('');

  const loadHealth = useCallback(async (window: number) => {
    setHealth({ state: 'loading' });
    try { setHealth({ state: 'ready', value: await getMailHealth(window) }); } catch (error) { setHealth({ state: 'error', message: describeError(error) }); }
  }, []);
  const loadServer = useCallback(async () => {
    setServer({ state: 'loading' });
    try { setServer({ state: 'ready', value: await getMailServer() }); } catch (error) { setServer({ state: 'error', message: describeError(error) }); }
  }, []);
  useEffect(() => { void loadHealth(days); }, [days, loadHealth]);
  useEffect(() => { void loadServer(); }, [loadServer]);

  const view = health.state === 'ready' ? health.value : null;
  const rows = view ? sortMailHealth(view.organisations).filter((row) => mailHealthMatches(row, query)) : [];
  return <section className="pane mailHealthPane" aria-labelledby="mail-health-title">
    <div className="paneHead">
      <h2 id="mail-health-title"><Mail aria-hidden="true" />{t('mailops.title')}</h2>
      <div className="toolbar mailHealthToolbar">
        {view && view.organisations.length > 0 && <>
          <label className="visuallyHidden" htmlFor="mail-health-search">{t('mailops.search')}</label>
          <input id="mail-health-search" type="search" value={query} placeholder={t('mailops.search')} onChange={(e) => setQuery(e.target.value)} />
        </>}
        <label className="visuallyHidden" htmlFor="mail-health-days">{t('mailops.window')}</label>
        <select id="mail-health-days" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {MAIL_HEALTH_DAYS.map((d) => <option key={d} value={d}>{t('mailops.days', { count: d })}</option>)}
        </select>
      </div>
    </div>
    <div className="paneBody">
    <p className="muted mailHealthIntro">{t('mailops.intro')}</p>
    {health.state === 'loading' && <p className="muted" role="status">{t('mailops.loading')}</p>}
    {health.state === 'error' && <PaneProblem text={t('mailops.failed', { message: health.message })} onRetry={() => void loadHealth(days)} />}
    {view && !view.enabled && <p className="notice">{t('mailops.off')}</p>}
    {view && <p className="muted" role="status">{t('mailops.totals', {
      mailboxes: view.mailboxes, sent: view.totals.sent, failed: view.totals.send_failed, unknown: view.totals.send_unknown, limited: rateLimited(view.totals),
    })}</p>}
    {view && !view.organisations.length && <div className="paneEmpty"><Mail aria-hidden="true" /><h3>{t('mailops.empty')}</h3><p>{t('mailops.emptyText')}</p></div>}
    {view && view.organisations.length > 0 && !rows.length && <div className="paneEmpty"><h3>{t('mailops.noMatch')}</h3></div>}
    {rows.length > 0 && <div className="dataTableWrap"><table className="dataTable mailHealthTable">
      <caption className="visuallyHidden">{t('mailops.title')}</caption>
      <thead><tr>
        <th scope="col">{t('mailops.col.organisation')}</th><th scope="col">{t('mailops.col.health')}</th>
        <th scope="col" className="num">{t('mailops.col.mailboxes')}</th><th scope="col" className="num optional">{t('mailops.col.notConnected')}</th>
        <th scope="col" className="optional">{t('mailops.col.lastSync')}</th>
        <th scope="col" className="num">{t('mailops.col.sent')}</th><th scope="col" className="num">{t('mailops.col.failed')}</th>
        <th scope="col" className="num">{t('mailops.col.unknown')}</th><th scope="col" className="num optional">{t('mailops.col.limited')}</th>
      </tr></thead>
      <tbody>{rows.map((row) => <tr key={row.tenant_id}>
        <th scope="row"><strong dir="auto">{row.display_name}</strong><small dir="ltr">{row.tenant_id}</small></th>
        <td><span className={`statusBadge ${mailHealthTone(row.health)}`}>{t(`mailops.health.${row.health}` as MessageKey)}</span>
          {row.stale > 0 && <small>{t('mailops.stale', { count: row.stale })}</small>}</td>
        <td className="num">{row.mailboxes}</td>
        <td className={`num optional ${notConnected(row) ? 'overdueCell' : ''}`} title={t('mailops.notConnectedHelp', { auth: row.auth_failed, offline: row.offline, error: row.error })}>{notConnected(row)}</td>
        <td className="optional nowrap">{row.last_sync_at ? formatDateTime(row.last_sync_at) : t('mailops.never')}</td>
        <td className="num">{row.counts.sent}</td>
        <td className={`num ${row.counts.send_failed ? 'overdueCell' : ''}`}>{row.counts.send_failed}</td>
        <td className={`num ${row.counts.send_unknown ? 'overdueCell' : ''}`} title={t('mailops.unknownHelp')}>{row.counts.send_unknown}</td>
        <td className="num optional" title={t('mailops.limitedHelp', { send: row.counts.send_rate_limited, connect: row.counts.connect_rate_limited })}>{rateLimited(row.counts)}</td>
      </tr>)}</tbody>
    </table></div>}
    <MailServerCard load={server} onRetry={() => void loadServer()} />
    </div>
  </section>;
}

function MailServerCard({ load, onRetry }: { load: Load<MailServerView>; onRetry(): void }) {
  if (load.state === 'loading') return <p className="muted" role="status">{t('mailops.loading')}</p>;
  if (load.state === 'error') return <PaneProblem text={t('mailops.serverFailed', { message: load.message })} onRetry={onRetry} />;
  const { server, mailcow } = load.value;
  const serverLine = !server.enabled ? t('mailops.serverOff')
    : !server.configured ? t('mailops.serverNotSet')
      : t('mailops.serverOn', { imap: iso(server.imap_host ?? ''), smtp: iso(server.smtp_host ?? ''), tenants: server.allowed_tenants, sends: server.send_per_hour });
  const running = (mailcow.containers_total ?? 0) - mailcow.containers_not_running.length;
  return <div className="mailServerCard">
    <h3><Server aria-hidden="true" />{t('mailops.server')}</h3>
    <p>{serverLine}</p>
    <h3>{t('mailops.mailcow')}</h3>
    {mailcow.state === 'off' && <p className="muted">{t('mailops.mailcow.off')}</p>}
    {mailcow.state !== 'off' && mailcow.state !== 'ok' && <p className="notice errorNotice" role="alert">{t(`mailops.mailcow.${mailcow.state}` as MessageKey, { message: mailcow.message ?? '' })}</p>}
    {mailcow.state === 'ok' && <ul className="mailcowFacts">
      {mailcow.version && <li>{t('mailops.mailcow.version', { version: iso(mailcow.version) })}</li>}
      {mailcow.containers_total !== null && <li>{t('mailops.mailcow.containers', { running, total: mailcow.containers_total })}</li>}
      {mailcow.containers_not_running.length > 0 && <li className="overdueCell">{t('mailops.mailcow.stopped', { names: iso(mailcow.containers_not_running.join(', ')) })}</li>}
      {mailcow.queue_messages !== null && <li>{t('mailops.mailcow.queue', { count: mailcow.queue_messages })}</li>}
      {mailcow.domains.map((d) => <li key={d.domain}>{d.active
        ? t('mailops.mailcow.domain', { domain: iso(d.domain), mailboxes: d.mailboxes, quota: d.quota_used_percent === null ? t('mailops.mailcow.noQuota') : iso(`${d.quota_used_percent}%`) })
        : t('mailops.mailcow.domainOff', { domain: iso(d.domain), mailboxes: d.mailboxes })}</li>)}
    </ul>}
  </div>;
}
