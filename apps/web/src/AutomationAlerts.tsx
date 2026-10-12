import { AlertTriangle, Bell, X } from 'lucide-react';
import type { AutomationAlert } from './lib/automation';
import { t } from './lib/i18n';

/**
 * In-app automation alerts (docs/automation.md#events-and-notifications): failed runs for people
 * whose "automation failed" notification preference is on, and `notify` steps. Each links to the
 * run; nothing disappears on its own, so a screen-reader user has time to act.
 */
export function AutomationAlerts({ alerts, onOpen, onDismiss }: { alerts: AutomationAlert[]; onOpen(alert: AutomationAlert): void; onDismiss(id: string): void }) {
  if (!alerts.length) return null;
  return <section className="autoAlerts" aria-label={t('auto.alert.region')}>
    {alerts.slice(-3).map((alert) => <div key={alert.id} className={`autoAlert ${alert.kind}`} role={alert.kind === 'failed' ? 'alert' : 'status'}>
      {alert.kind === 'failed' ? <AlertTriangle aria-hidden="true" /> : <Bell aria-hidden="true" />}
      <div><strong dir="auto">{alert.title}</strong><p dir="auto">{alert.message}</p></div>
      <div className="autoAlertActions">
        <button type="button" className="secondary" onClick={() => onOpen(alert)}>{t('auto.alert.openRun')}</button>
        <button type="button" className="iconButton" aria-label={t('auto.alert.dismiss', { title: alert.title })} title={t('auto.alert.dismissShort')} onClick={() => onDismiss(alert.id)}><X aria-hidden="true" /></button>
      </div>
    </div>)}
  </section>;
}

/** An operating-system notification too, when the user turned desktop notifications on and the browser allows it. */
export function desktopNotify(alert: AutomationAlert, enabled: boolean): void {
  if (!enabled || typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
  try { new Notification(alert.title, { body: alert.message, tag: alert.id }); } catch { /* some browsers only allow it from a service worker */ }
}
