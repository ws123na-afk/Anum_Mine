// The caller's in-app team notifications (docs/team-work.md#notifications): assignments, comments,
// overdue reminders and ANUM follow-ups, listed in the top bar's notifications menu (Shell.tsx).
// The list is the API's (only the recipient can read it); a `notification.created` event for this
// user refreshes it, and with "Desktop notifications" on in Settings an OS notification is shown
// while the page is in the background.
import { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCheck, ClipboardCheck } from 'lucide-react';
import type { UserNotification } from '@anum/contracts';
import * as team from './lib/teamApi';
import { getNotificationPreferences, currentUserId } from './lib/api';
import { describeError } from './lib/errors';
import { formatDateTime, t } from './lib/i18n';
import { REPORTS_HASH } from './lib/financeReports';
import { shouldNotifyDesktop } from './lib/team';
import './team.css';

export interface TeamNotifications {
  items: UserNotification[];
  unread: number;
  cursor: string | null;
  error: string | null;
  permission: string;
  loadMore(): void;
  open(n: UserNotification): Promise<void>;
  readAll(): Promise<void>;
  enableDesktop(): void;
}

export function useTeamNotifications(signal: { recipient: unknown; at: number } | null, onOpenItem: (itemId: string) => void): TeamNotifications {
  const [items, setItems] = useState<UserNotification[]>([]);
  const [unread, setUnread] = useState(0);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [permission, setPermission] = useState<string>(() => (typeof Notification === 'undefined' ? 'unsupported' : Notification.permission));
  const desktop = useRef(false);
  const openItem = useRef(onOpenItem);
  openItem.current = onOpenItem;

  const load = useCallback(async (after: string | null = null) => {
    try {
      const page = await team.getNotifications(after);
      setItems((old) => (after ? [...old, ...page.items] : page.items));
      setUnread(page.unread); setCursor(page.next_cursor); setError(null);
      return page.items[0] ?? null;
    } catch (e) { setError(describeError(e)); return null; }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void getNotificationPreferences().then((p) => { desktop.current = p.desktop_enabled; }).catch(() => undefined); }, []);
  useEffect(() => {
    if (!signal || signal.recipient !== currentUserId()) return;
    void load().then((latest) => {
      const show = shouldNotifyDesktop({ recipient: signal.recipient, me: currentUserId(), hidden: document.hidden, desktopEnabled: desktop.current, permission: typeof Notification === 'undefined' ? 'unsupported' : Notification.permission });
      if (show && latest) { try { new Notification(latest.title, { body: latest.body, tag: latest.id }); } catch { /* the in-app list still has it */ } }
    });
  }, [signal, load]);

  return {
    items, unread, cursor, error, permission,
    loadMore: () => { if (cursor) void load(cursor); },
    open: async (n) => {
      if (!n.read_at) { try { await team.readNotification(n.id); } catch { /* still open it */ } }
      void load();
      if (n.work_item_id) openItem.current(n.work_item_id);
      // The post-close trading report notice opens the reports (docs/finance-trading-intel.md#daily-report).
      else if (n.kind === 'finance_daily_report') location.hash = REPORTS_HASH;
    },
    readAll: async () => { try { await team.readAllNotifications(); await load(); } catch (e) { setError(describeError(e)); } },
    enableDesktop: () => { void Notification.requestPermission().then(setPermission); },
  };
}

/** The team part of the notifications menu; `pick` closes the menu before acting. */
export function TeamNotificationsSection({ state, pick }: { state: TeamNotifications; pick(action: () => void): void }) {
  const { items, unread, cursor, error, permission } = state;
  return <div className="teamNotifications">
    <div className="sectionHeader"><h3>{t('bell.title')}</h3>{unread > 0 && <button type="button" className="secondary linkButton" onClick={() => void state.readAll()}><CheckCheck aria-hidden="true" />{t('bell.readAll')}</button>}</div>
    {error && <p className="notice errorNotice" role="alert">{t('bell.failed', { message: error })}</p>}
    {!error && !items.length && <p className="muted">{t('bell.empty')}</p>}
    <ul className="popoverList bellList">{items.map((n) => <li key={n.id}>
      <button type="button" className={`bellItem ${n.read_at ? '' : 'unread'}`} onClick={() => pick(() => void state.open(n))}>
        <ClipboardCheck aria-hidden="true" /><span><strong dir="auto">{n.title}</strong><small dir="auto">{n.body}</small>
          <small>{formatDateTime(n.created_at)}{n.email_status === 'sent' ? ` · ${t('bell.emailed')}` : n.email_status === 'failed' ? ` · ${t('bell.emailFailed')}` : ''}{!n.read_at && <span className="srOnly"> · {t('bell.unread')}</span>}</small></span>
      </button>
    </li>)}</ul>
    {cursor && <button type="button" className="secondary loadMore" onClick={state.loadMore}>{t('team.loadMore')}</button>}
    {permission === 'default' && <button type="button" className="secondary loadMore" onClick={state.enableDesktop}>{t('bell.enableDesktop')}</button>}
    {permission === 'denied' && <p className="muted">{t('bell.desktopBlocked')}</p>}
  </div>;
}
