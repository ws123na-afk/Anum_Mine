/** The drawer every piece of work opens in inside the headquarters (task, approvals, meeting, agent work log).
 * One at a time, over the scene at the inline end; Escape or the close button closes it and focus goes back to what
 * opened it, or to the scene when that is gone. Its footer always has the module page as "Open full page". */
import { useEffect, useId, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { ExternalLink, RefreshCcw, X } from 'lucide-react';
import { t } from '../lib/i18n';
import { useLocale } from '../useLocale';
import './hqAssign.css';
import './hqWork.css';

const FOCUSABLE = 'button:not([disabled]),[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

/** Where focus goes when the opener is gone: the first control of the scene area, else the page heading. */
export function focusScene(): void {
  const host = document.querySelector<HTMLElement>('.hqMain');
  const first = host?.querySelector<HTMLElement>(FOCUSABLE);
  if (first) { first.focus(); return; }
  const title = document.getElementById('view-title');
  if (title) { if (!title.hasAttribute('tabindex')) title.setAttribute('tabindex', '-1'); title.focus(); }
}

export interface WorkDrawerProps {
  /** Accessible name of the region and the heading when no title is known yet. */
  region: string;
  title?: string | null;
  onClose: () => void;
  onRefresh?: () => void;
  /** The module page for the rare deep task. */
  fullPage?: { hash: string; label: string } | null;
  /** Extra footer actions next to "Open full page". */
  foot?: ReactNode;
  children: ReactNode;
  testId?: string;
}

export function WorkDrawer({ region, title, onClose, onRefresh, fullPage, foot, children, testId }: WorkDrawerProps) {
  const locale = useLocale();
  const heading = useRef<HTMLHeadingElement>(null);
  const titleId = useId();
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement && document.activeElement !== document.body ? document.activeElement : null;
    heading.current?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      // A dialog opened over the drawer (a confirm, the assign dialog) closes first.
      if (document.querySelector('[role=dialog][aria-modal=true]')) return;
      e.preventDefault(); e.stopPropagation(); close.current();
    };
    document.addEventListener('keydown', key);
    return () => {
      document.removeEventListener('keydown', key);
      // Focus returns to the opener while it is still on screen, else to the scene.
      window.setTimeout(() => {
        if (document.activeElement && document.activeElement !== document.body && document.activeElement.closest('.hqWd')) return;
        if (opener && opener.isConnected && !opener.closest('.hqWd')) opener.focus();
        else if (!document.querySelector('.hqWd')) focusScene();
      }, 0);
    };
  }, []);
  return createPortal(<aside className="hqAsSide hqWd" dir={locale === 'ar' ? 'rtl' : 'ltr'} role="complementary" aria-label={region} data-testid={testId}>
    <header className="hqAsHead">
      <h2 id={titleId} ref={heading} tabIndex={-1} dir="auto">{title || region}</h2>
      {onRefresh && <button type="button" className="hqAsX" onClick={onRefresh} aria-label={t('hqWork.refresh')} title={t('hqWork.refresh')}><RefreshCcw size={15} aria-hidden="true" /></button>}
      <button type="button" className="hqAsX" onClick={onClose} aria-label={t('hqWork.close', { region })} title={t('hqWork.close', { region })}><X size={16} aria-hidden="true" /></button>
    </header>
    <div className="hqAsBody">{children}</div>
    {(fullPage || foot) && <footer className="hqWdFoot">
      {foot}
      {fullPage && <a className="hqWdFull" href={fullPage.hash} onClick={onClose}><ExternalLink size={13} aria-hidden="true" />{fullPage.label}</a>}
    </footer>}
  </aside>, document.body);
}
