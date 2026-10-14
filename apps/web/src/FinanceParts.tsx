// Small parts shared by the Finance views (docs/finance.md): load states, notices, money.
import type { ReactNode } from 'react';
import { CircleDot, Lock, RefreshCw } from 'lucide-react';
import { amountTone, formatAmount } from './lib/finance';
import { describeError, isPermissionDenied } from './lib/errors';
import { t } from './lib/i18n';

export type Load<T> = { kind: 'loading' } | { kind: 'ready'; value: T } | { kind: 'denied'; message: string } | { kind: 'error'; message: string };
export const failed = (e: unknown): Load<never> => (isPermissionDenied(e) ? { kind: 'denied', message: describeError(e) } : { kind: 'error', message: describeError(e) });

export function Notice({ children, error, busy }: { children: ReactNode; error?: boolean; busy?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'} aria-live={error ? undefined : 'polite'} aria-busy={busy || undefined}><CircleDot aria-hidden="true" />{children}</p>;
}
export function LoadError({ message, retry }: { message: string; retry: () => void }) {
  return <div className="notice errorNotice cmLoadError" role="alert"><CircleDot aria-hidden="true" /><span>{message}</span><button type="button" className="secondary" onClick={retry}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>;
}
export function Denied() {
  return <section className="surface cmPanel cmDenied" aria-labelledby="fin-denied"><Lock aria-hidden="true" /><div><h2 id="fin-denied">{t('finance.denied.title')}</h2><p className="muted">{t('finance.denied.text')}</p></div></section>;
}
export function Empty({ icon, title, text, action }: { icon: ReactNode; title: string; text: string; action?: ReactNode }) {
  return <div className="emptyState">{icon}<h3>{title}</h3><p>{text}</p>{action}</div>;
}
export function Money({ value, currency, signed }: { value: string; currency: string; signed?: boolean }) {
  return <span className={`finMoney ${signed ? amountTone(value) : ''}`} dir="ltr">{formatAmount(value, currency)}</span>;
}
export function FormError({ id, text }: { id: string; text: string | null }) {
  return text ? <p id={id} className="cmFieldError" role="alert">{text}</p> : null;
}
export function FormActions({ busy, onCancel, label }: { busy: boolean; onCancel: () => void; label?: string }) {
  return <div className="actions"><button type="submit" disabled={busy}>{busy ? t('finance.saving') : label ?? t('finance.save')}</button><button type="button" className="secondary" disabled={busy} onClick={onCancel}>{t('common.cancel')}</button></div>;
}
