// System health in the platform console (docs/system-health.md): the deployment's backups, AI model, disk,
// memory, email, background jobs, errors and restarts, read from GET /api/v1/platform/health. Problems come
// first, each with its numbers and what to do. Read-only. The owner notices need the organisation and
// workspace ids, which the page shows ready to paste into infra/oracle/.env.
import { useCallback, useEffect, useState } from 'react';
import { Activity, CircleAlert, CircleCheck, CircleDashed, CircleX, Copy, RefreshCw } from 'lucide-react';
import { apiBaseUrl } from '../lib/api';
import { describeError, isPermissionDenied } from '../lib/errors';
import { formatDateTime, t } from '../lib/i18n';
import {
  fixKey, formatUptime, healthLines, needsAttention, sortHealth, stateKey, titleKey, whyKey,
  type HealthItem, type HealthState, type SystemHealth,
} from '../lib/systemHealth';
import { getSystemHealth } from '../lib/systemHealthApi';
import { Badge, Note } from './parts';

type Load = { state: 'loading' } | { state: 'ready'; value: SystemHealth } | { state: 'denied' | 'error'; message: string };
const TONE: Record<HealthState, string> = { green: 'success', amber: 'warning', red: 'dangerTone', unknown: 'neutral' };
const CLASS: Record<HealthState, string> = { green: 'done', amber: 'warning', red: 'missing', unknown: 'unknown' };
const ICON = { green: CircleCheck, amber: CircleAlert, red: CircleX, unknown: CircleDashed } as const;

export function ConsoleHealth() {
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  const refresh = useCallback(async () => {
    setLoad({ state: 'loading' });
    try { setLoad({ state: 'ready', value: await getSystemHealth() }); }
    catch (e) { setLoad({ state: isPermissionDenied(e) ? 'denied' : 'error', message: describeError(e) }); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  if (load.state === 'loading') return <section className="surface blPanel" aria-busy="true"><Note>{t('health.loading')}</Note></section>;
  if (load.state !== 'ready') return <section className="surface blPanel"><div className="blRetry">
    <Note error>{load.state === 'denied' ? t('health.denied') : t('health.failed', { message: load.message })}</Note>
    {load.state === 'error' && <button type="button" className="secondary" onClick={() => void refresh()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button>}</div></section>;
  const value = load.value;
  const items = sortHealth(value.items);
  const attention = needsAttention(items);
  return <div className="blStack">
    <section className="surface blPanel" aria-labelledby="health-title">
      <div className="blHead"><div><h2 id="health-title">{t('health.title')}</h2><p className="muted">{t('health.lead')}</p></div>
        <div className="blBadges hlBadges"><Badge tone={attention ? (value.overall === 'red' ? 'dangerTone' : 'warning') : 'success'}>{attention ? t('health.attention', { count: attention }) : t('health.allGood')}</Badge>
          <button type="button" className="secondary" onClick={() => void refresh()}><RefreshCw aria-hidden="true" />{t('health.recheck')}</button></div></div>
      <p className="muted blSmall">{t('health.checked', {
        when: formatDateTime(value.checked_at, { dateStyle: 'medium', timeStyle: 'short' }),
        since: formatDateTime(value.started_at, { dateStyle: 'medium', timeStyle: 'short' }), uptime: formatUptime(value.uptime_seconds),
      })}</p>
      <ul className="blReady hlList" aria-label={t('health.listLabel')}>{items.map((item) => <HealthRow key={item.key} item={item} you={value.you} />)}</ul>
    </section>
    <UptimeMonitor />
  </div>;
}

function HealthRow({ item, you }: { item: HealthItem; you: SystemHealth['you'] }) {
  const Icon = ICON[item.state];
  return <li className={`blReadyItem hlItem ${CLASS[item.state]}`}>
    <Icon aria-hidden="true" className="blReadyIcon" />
    <div>
      <h3>{t(titleKey(item.key))} <Badge tone={TONE[item.state]}>{t(stateKey(item.state))}</Badge></h3>
      <p className="muted">{t(whyKey(item.key))}</p>
      {healthLines(item).map((line, i) => <p key={i} className="blSmall" dir="auto">{line}</p>)}
      {item.state !== 'green' && !(item.key === 'restarts' && item.detail.configured === false) && <p className="blSmall"><strong>{t('health.fixLabel')}</strong> {t(fixKey(item.key))}</p>}
      {item.key === 'notices' && item.state !== 'green' && <code dir="ltr" className="blRecord hlEnv">{`ANUM_SYSTEM_HEALTH_TENANT_ID=${you.tenant_id}\nANUM_SYSTEM_HEALTH_WORKSPACE_ID=${you.workspace_id}`}</code>}
    </div>
  </li>;
}

function UptimeMonitor() {
  const url = `${apiBaseUrl.replace(/\/+$/, '')}/health`;
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(url); setCopied(true); } catch { setCopied(false); }
  };
  return <section className="surface blPanel" aria-labelledby="health-monitor-title">
    <h2 id="health-monitor-title"><Activity aria-hidden="true" className="blInlineIcon" />{t('health.monitor.title')}</h2>
    <p className="muted">{t('health.monitor.text')}</p>
    <div className="hlMonitor"><code dir="ltr" className="blRecord">{url}</code>
      <button type="button" className="secondary" onClick={() => void copy()}><Copy aria-hidden="true" />{copied ? t('health.monitor.copied') : t('health.monitor.copy')}</button></div>
  </section>;
}
