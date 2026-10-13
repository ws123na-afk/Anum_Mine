// The panel states Home and ANUM Command share (docs/home.md#the-web-home, docs/command.md#the-web-command-center):
// loading, empty with a next action, an error that says what failed with Try again, permission denied
// that says who can help, and "Show more" paging. One copy, styled in commandCenter.css.
import type { ReactNode } from 'react';
import { CircleAlert, Lock, RefreshCw } from 'lucide-react';
import { t } from './lib/i18n';

export function PanelLoading({ label }: { label?: string }) {
  return <div className="panelLoading" role="status"><span className="skeletonRow" aria-hidden="true" /><span className="skeletonRow short" aria-hidden="true" /><span className="visuallyHidden">{label ?? t('common.loading')}</span></div>;
}

export function EmptyPanel({ icon, title, text, action, onAction }: { icon: ReactNode; title: string; text: string; action?: string; onAction?: () => void }) {
  return <div className="panelEmpty"><span aria-hidden="true">{icon}</span><h3>{title}</h3><p>{text}</p>{action && onAction && <button type="button" className="secondary smallButton" onClick={onAction}>{action}</button>}</div>;
}

export function PanelError({ message, onRetry, retryLabel }: { message: string; onRetry?: () => void; retryLabel?: string }) {
  return <div className="panelError" role="alert"><p><CircleAlert aria-hidden="true" />{message}</p>{onRetry && <button type="button" className="secondary smallButton" onClick={onRetry}><RefreshCw aria-hidden="true" />{retryLabel ?? t('common.retry')}</button>}</div>;
}

export function PanelDenied({ title, text }: { title: string; text: string }) {
  return <div className="panelDenied"><Lock aria-hidden="true" /><div><strong>{title}</strong><p>{text}</p></div></div>;
}

/** "Show more" for a list shown a page at a time; says how many are left. */
export function ShowMore({ more, onMore, label }: { more: number; onMore: () => void; label?: string }) {
  if (more <= 0) return null;
  return <button type="button" className="linkButton showMore" onClick={onMore}>{label ?? t('panels.showMore', { count: more })}</button>;
}
