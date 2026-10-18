// Shared page building blocks (docs/design/anum360.md#page-layout): one header row, the summary
// band, a split view whose panes fill the window on desktop and stack on phones, and the "New..."
// dialog that keeps create forms out of the way of the records.
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { CircleAlert, Lock, RefreshCw } from 'lucide-react';
import { Modal } from './Dialog';
import { fillHeight, type Figure, type Load } from './lib/pageSummary';
import { describeError, isPermissionDenied } from './lib/errors';
import { t } from './lib/i18n';
import './pageParts.css';

/** Two panes side by side from this width; three (with the context rail) from the next. */
export const SPLIT_QUERY = '(min-width: 1024px)';
export const RAIL_QUERY = '(min-width: 1280px)';

/** Whether a media query matches now, kept current as the window changes. */
export function useMediaQuery(query: string): boolean {
  const read = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(query).matches;
  const [matches, setMatches] = useState(read);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined;
    const list = window.matchMedia(query);
    const update = () => setMatches(list.matches);
    update();
    list.addEventListener('change', update);
    return () => list.removeEventListener('change', update);
  }, [query]);
  return matches;
}

/** One step of a breadcrumb trail: a link (a URL hash the shell opens) or, last, the current page. */
export interface Crumb { label: string; href?: string; onClick?: () => void }

/**
 * The way back from a sub-page (docs/module-checklist.md#navigation): Home, the module, then the
 * current page. Links are URL hashes, so they also work in a new tab and with Back and Forward.
 */
export function Breadcrumbs({ trail }: { trail: Crumb[] }) {
  return <nav className="breadcrumbs" aria-label={t('page.breadcrumb')}>
    <ol>{trail.map((crumb, index) => {
      const last = index === trail.length - 1;
      return <li key={`${index}-${crumb.label}`}>
        {last || !crumb.href ? <span aria-current={last ? 'page' : undefined}>{crumb.label}</span>
          : <a href={crumb.href} onClick={crumb.onClick ? (event) => { event.preventDefault(); crumb.onClick?.(); } : undefined}>{crumb.label}</a>}
      </li>;
    })}</ol>
  </nav>;
}

/**
 * The page's one header row: the title and a one-line subtitle on the start side, the page's
 * actions on the end side. Used by views that draw their own header (lib/shell.ts OWN_HEADER).
 */
export function PageHeader({ title, subtitle, actions, crumbs }: { title: string; subtitle?: string; actions?: ReactNode; crumbs?: Crumb[] }) {
  return <header className="pageHeader">
    <div className="pageHeaderText">{crumbs && crumbs.length > 0 && <Breadcrumbs trail={crumbs} />}<h1 id="view-title">{title}</h1>{subtitle && <p>{subtitle}</p>}</div>
    {actions && <div className="pageHeaderActions">{actions}</div>}
  </header>;
}

/**
 * The summary band: three to five figures from the API. A figure still loading shows a quiet
 * placeholder; one whose request failed says "Unavailable" (never 0), and the band offers a retry.
 */
export function PageSummary({ label, figures, onRetry, footer }: { label: string; figures: Figure[]; onRetry?: () => void; footer?: ReactNode }) {
  const failed = figures.some((item) => item.state === 'error');
  return <section className="pageSummary" aria-label={label}>
    <dl className="pageFigures" data-count={figures.length}>
      {figures.map((item) => <div key={item.id} className={`pageFigure ${item.state} ${item.state === 'ready' ? item.tone ?? '' : ''}`} aria-busy={item.state === 'loading' || undefined}>
        <dt>{item.label}</dt>
        <dd>
          {item.state === 'loading' && <><span className="figureSkeleton" aria-hidden="true" /><span className="visuallyHidden">{t('page.figure.loading')}</span></>}
          {item.state === 'error' && <span className="figureUnavailable">{t('page.figure.unavailable')}</span>}
          {item.state === 'ready' && <strong dir="auto">{item.value}</strong>}
          {item.state === 'ready' && item.hint && <small dir="auto">{item.hint}</small>}
          {item.state === 'error' && <small>{t('page.figure.failed')}</small>}
        </dd>
      </div>)}
    </dl>
    {(failed && onRetry) || footer ? <div className="pageSummaryFoot">
      {footer}
      {failed && onRetry && <button type="button" className="secondary pageSummaryRetry" onClick={onRetry}><RefreshCw aria-hidden="true" />{t('page.figure.retry')}</button>}
    </div> : null}
  </section>;
}

/** Keeps `--fill-h` on `ref` at the height that fills the window below it (lib/pageSummary.ts fillHeight). */
export function useFillHeight(ref: RefObject<HTMLElement | null>, min = 480): void {
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return undefined;
    const container = element.closest('main') ?? element.parentElement;
    const update = () => {
      const top = element.getBoundingClientRect().top + window.scrollY;
      const gutter = container ? Number.parseFloat(getComputedStyle(container).paddingBottom) || 16 : 16;
      const next = `${fillHeight(window.innerHeight, top, { min, gutter })}px`;
      if (element.style.getPropertyValue('--fill-h') !== next) element.style.setProperty('--fill-h', next);
    };
    update();
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(update) : null;
    if (observer && element.parentElement) observer.observe(element.parentElement);
    window.addEventListener('resize', update);
    return () => { observer?.disconnect(); window.removeEventListener('resize', update); };
  }, [ref, min]);
}

/**
 * Panes side by side that fill the window's height on desktop (each pane scrolls on its own) and
 * stack on narrower screens, where `show` keeps only the list or only the detail in view. Children
 * are elements with `data-pane="list" | "detail" | "rail"`; the rail shows only on wide screens.
 */
export function SplitView({ className = '', layout = 'two', show, children, label }: { className?: string; layout?: 'two' | 'three'; show?: 'list' | 'detail'; label?: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useFillHeight(ref);
  return <div ref={ref} className={`splitView ${layout} ${className}`} data-show={show} role={label ? 'group' : undefined} aria-label={label}>{children}</div>;
}

/** A button that opens a form in a dialog ("New collection", "Add memory"); the form gets `close`. */
export function DialogButton({ label, title, icon, className, disabled, describedBy, children }: {
  label: string; title?: string; icon?: ReactNode; className?: string; disabled?: boolean; describedBy?: string; children: (close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return <>
    <button type="button" className={className} disabled={disabled} aria-haspopup="dialog" aria-describedby={describedBy} onClick={() => setOpen(true)}>{icon}{label}</button>
    {open && <Modal title={title ?? label} className="formDialog" onClose={() => setOpen(false)}>{children(() => setOpen(false))}</Modal>}
  </>;
}

/** A small "something is wrong" line inside a pane: what failed and the way to fix it. */
export function PaneProblem({ text, onRetry }: { text: string; onRetry?: () => void }) {
  return <div className="notice errorNotice paneProblem" role="alert"><CircleAlert aria-hidden="true" /><span>{text}</span>{onRetry && <button type="button" className="secondary" onClick={onRetry}><RefreshCw aria-hidden="true" />{t('common.retry')}</button>}</div>;
}

/** A failed request as a page state: "denied" (the API's reason, naming who may) for a 403, else the error. */
export function loadFailure<T>(error: unknown): Load<T> {
  return isPermissionDenied(error) ? { state: 'denied', message: describeError(error) } : { state: 'error', message: describeError(error) };
}

/**
 * A loading placeholder shaped like the content it stands for (a heading bar, then lines), instead
 * of a "Loading..." sentence. The label is for screen readers; the bars are decoration.
 */
export function LoadingSkeleton({ label, rows = 3, className = '' }: { label: string; rows?: number; className?: string }) {
  return <div className={`loadingSkeleton ${className}`} role="status" aria-live="polite">
    <span className="visuallyHidden">{label}</span>
    <span className="skeletonBar head" aria-hidden="true" />
    {Array.from({ length: rows }, (_, index) => <span key={index} className="skeletonBar" aria-hidden="true" />)}
  </div>;
}

/**
 * A read-only notice for tenant-wide settings: one line saying what is read only and why, and the
 * way to get the role behind a "How to get access" disclosure (lib/governance.ts tenantWriteAccess).
 */
export function AccessNotice({ notice, howTo }: { notice: string | null; howTo: { text: string; server: string; command: string } | null }) {
  if (!notice) return null;
  return <div className="notice accessNotice" role="note">
    <Lock aria-hidden="true" />
    <div>
      <p>{notice}</p>
      {howTo && <details>
        <summary>{t('gov.readOnlyHowTitle')}</summary>
        <p>{howTo.text}</p>
        <p>{howTo.server} <code dir="ltr">{howTo.command}</code></p>
      </details>}
    </div>
  </div>;
}

/** A pane or page that the person's role cannot open: what it is and who can. */
export function PaneDenied({ title, text }: { title: string; text: string }) {
  return <div className="paneEmpty paneDenied" role="note"><Lock aria-hidden="true" /><h3>{title}</h3><p>{text}</p></div>;
}
