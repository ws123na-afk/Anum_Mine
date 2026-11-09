// The Finance shell (docs/finance.md#finance-shell): the parts every Finance page shares, so the pages
// read as one module. The section navigation (under Finance in the sidebar on wide screens, a strip
// on phones), the page head (title, subtitle, scope, period, primary action, tabs), the KPI row,
// cards, banners and status chips. Presentation only: pages pass real figures from the API.
//
// Props are kept stable for the other Finance pages; extend them by adding optional props only.
import { useEffect, useId, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Minus } from 'lucide-react';
import { monthLabel, shiftMonth, type FinanceTab } from './lib/finance';
import { FINANCE_NAV, FINANCE_SCOPES, groupOf, isCurrent, type Delta, type FinanceScope } from './lib/financeNav';
import { t, type MessageKey } from './lib/i18n';
import { useMediaQuery, type Crumb, Breadcrumbs } from './PageParts';
import './financeShell.css';

/** The sidebar slot Shell.tsx renders under the Finance entry while Finance is open. */
export const FINANCE_NAV_SLOT = 'nav-sub-finance';
const WIDE = '(min-width: 761px)';

/**
 * The Finance sections. On wide screens it renders into the sidebar under Finance (a portal into
 * the slot Shell.tsx provides), so Finance reads as one expanded group; on phones, and whenever the
 * slot is missing, it renders in place as a scrolling strip. Either way it is one
 * `<nav aria-label="Finance sections">` of buttons, the current one marked `aria-current="page"`.
 */
export function FinanceSectionNav({ tab, onPick }: { tab: FinanceTab; onPick: (next: FinanceTab) => void }) {
  const wide = useMediaQuery(WIDE);
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  useEffect(() => { setSlot(wide ? document.getElementById(FINANCE_NAV_SLOT) : null); }, [wide]);
  // Bring the current section into the sidebar's view (nearest edge only, like Shell.tsx does for views).
  useEffect(() => {
    const reveal = () => {
      const entry = slot?.querySelector<HTMLElement>('[aria-current="page"]'), nav = slot?.closest<HTMLElement>('nav[aria-label]');
      if (!entry || !nav) return;
      // Keep the Finance entry itself in view too when Finance and the current section fit together.
      const parent = slot?.parentElement?.querySelector<HTMLElement>('.navItem');
      const box = nav.getBoundingClientRect(), at = entry.getBoundingClientRect();
      const top = parent && at.bottom - parent.getBoundingClientRect().top <= box.height - 32 ? parent.getBoundingClientRect().top : at.top;
      if (at.bottom > box.bottom) nav.scrollTop += at.bottom - box.bottom + 24;
      else if (top < box.top) nav.scrollTop -= box.top - top + 8;
      const after = parent?.getBoundingClientRect();
      if (after && after.top < box.top && at.bottom - after.top <= box.height - 32) nav.scrollTop -= box.top - after.top + 8;
    };
    reveal();
    // Again once late entries (owner-only views) and web fonts have settled the sidebar's layout.
    const timer = window.setTimeout(reveal, 400);
    return () => window.clearTimeout(timer);
  }, [slot, tab]);
  const current = groupOf(tab);
  const nav = <nav className={`finNav ${slot ? 'finNavSidebar' : 'finNavStrip'}`} aria-label={t('finance.tabs')}>
    {FINANCE_NAV.map((group, index) => <ul key={`${group.id}-${index}`} className={`finNavGroup finNavGroup-${group.id} ${current === group.id ? 'current' : ''}`}
      aria-label={group.id === 'finance' || group.id === 'business' ? undefined : t(`fshell.group.${group.id}` as MessageKey)}>
      {group.id === 'trading' && <li className="finNavHeading" aria-hidden="true">{t('fshell.group.trading')}</li>}
      {group.entries.map((entry) => entry.kind === 'tab'
        ? <li key={entry.tab}><button type="button" className="finNavItem" aria-current={isCurrent(entry, tab) ? 'page' : undefined} onClick={() => onPick(entry.tab)}>
            <span>{t((entry.also ? `fshell.nav.${entry.tab}` : `finance.tab.${entry.tab}`) as MessageKey)}</span></button></li>
        : <li key={entry.id}><a className="finNavItem finNavLink" href={entry.href}>
            <span>{t('fshell.nav.books')}</span><span className="finNewBadge">{t('fshell.badge.new')}</span></a></li>)}
    </ul>)}
  </nav>;
  return slot ? createPortal(nav, slot) : nav;
}

/** The page head: title and subtitle with the primary actions, the controls row, then the tabs. */
export function FinancePageHead({ title, subtitle, actions, controls, tabs, crumbs, icon }: {
  title: string; subtitle?: string; actions?: ReactNode; controls?: ReactNode; tabs?: ReactNode; crumbs?: Crumb[];
  /** A tinted tile with the module's icon before the title (the communication pages' heads). */
  icon?: ReactNode;
}) {
  return <header className="finHead">
    <div className="finHeadTop">
      {icon && <span className="finHeadIcon" aria-hidden="true">{icon}</span>}
      <div className="finHeadText">{crumbs && crumbs.length > 0 && <Breadcrumbs trail={crumbs} />}<h1 id="view-title">{title}</h1>{subtitle && <p>{subtitle}</p>}</div>
      {actions && <div className="finHeadButtons">{actions}</div>}
    </div>
    {controls && <div className="finHeadControls">{controls}</div>}
    {tabs}
  </header>;
}

/**
 * Personal / Business / All finance. `unavailable` greys an option out and says why in its title
 * and accessible description (for example Business when the person cannot read the business books).
 */
export function ScopeSwitch({ value, onChange, options = FINANCE_SCOPES, unavailable = {} }: {
  value: FinanceScope; onChange: (next: FinanceScope) => void; options?: readonly FinanceScope[]; unavailable?: Partial<Record<FinanceScope, string>>;
}) {
  const labelId = useId();
  return <div className="finControl">
    <span id={labelId} className="finControlLabel">{t('fshell.scope')}</span>
    <div className="finSegmented" role="radiogroup" aria-labelledby={labelId}>
      {options.map((scope) => {
        const why = unavailable[scope];
        return <button key={scope} type="button" role="radio" aria-checked={value === scope} disabled={Boolean(why)} title={why}
          aria-label={why ? `${t(`fshell.scope.${scope}` as MessageKey)}. ${why}` : undefined}
          onClick={() => onChange(scope)}>{t(`fshell.scope.${scope}` as MessageKey)}</button>;
      })}
    </div>
  </div>;
}

/** A month picker: previous, the month, next. `max` (a "YYYY-MM") stops it at the current month. */
export function PeriodPicker({ month, onChange, max, min }: { month: string; onChange: (next: string) => void; max?: string; min?: string }) {
  const labelId = useId();
  return <div className="finControl">
    <span id={labelId} className="finControlLabel">{t('fshell.period')}</span>
    <div className="finPeriod" role="group" aria-labelledby={labelId}>
      <button type="button" className="secondary" disabled={Boolean(min && month <= min)} onClick={() => onChange(shiftMonth(month, -1))} aria-label={t('finance.month.previous')}><ChevronLeft aria-hidden="true" className="cmFlip" /></button>
      <strong aria-live="polite">{monthLabel(month)}</strong>
      <button type="button" className="secondary" disabled={Boolean(max && month >= max)} onClick={() => onChange(shiftMonth(month, 1))} aria-label={t('finance.month.next')}><ChevronRight aria-hidden="true" className="cmFlip" /></button>
    </div>
  </div>;
}

export interface FinanceTabItem<T extends string> { id: T; label: string; badge?: string | number }

/** Tabs under the head with the green underline. Arrow keys move between them (WAI-ARIA tabs). */
export function FinanceTabs<T extends string>({ label, items, value, onChange, idPrefix }: {
  label: string; items: readonly FinanceTabItem<T>[]; value: T; onChange: (next: T) => void; idPrefix: string;
}) {
  const move = (from: number, by: number) => {
    const next = items[(from + by + items.length) % items.length];
    onChange(next.id);
    requestAnimationFrame(() => document.getElementById(`${idPrefix}-tab-${next.id}`)?.focus());
  };
  return <div className="finTabs" role="tablist" aria-label={label}>
    {items.map((item, index) => <button key={item.id} id={`${idPrefix}-tab-${item.id}`} type="button" role="tab" aria-selected={value === item.id}
      aria-controls={`${idPrefix}-panel`} tabIndex={value === item.id ? 0 : -1} onClick={() => onChange(item.id)}
      onKeyDown={(e) => {
        const rtl = document.documentElement.dir === 'rtl';
        if (e.key === 'ArrowRight') { e.preventDefault(); move(index, rtl ? -1 : 1); }
        if (e.key === 'ArrowLeft') { e.preventDefault(); move(index, rtl ? 1 : -1); }
        if (e.key === 'Home') { e.preventDefault(); move(0, 0); }
        if (e.key === 'End') { e.preventDefault(); move(items.length - 1, 0); }
      }}>
      {item.label}{item.badge !== undefined && <span className="finTabBadge">{item.badge}</span>}
    </button>)}
  </div>;
}

/** Props for the panel the tabs control: `<div {...tabPanel('frep', tab)}>`. */
export function tabPanel(idPrefix: string, value: string) {
  return { id: `${idPrefix}-panel`, role: 'tabpanel', 'aria-labelledby': `${idPrefix}-tab-${value}`, className: 'finTabPanel' } as const;
}

/** The KPI row: one card per figure, four across on desktop, two on tablets, one on phones. */
export function KpiRow({ label, children, className = '' }: { label: string; children: ReactNode; className?: string }) {
  return <section className={`finKpis ${className}`} aria-label={label}><ul>{children}</ul></section>;
}

/**
 * One KPI: icon, label, the figure and an optional delta chip (green when the change is good, red
 * when bad). `deltaText` is the chip's words ("8% vs October"); without a delta no chip is drawn.
 */
export function KpiCard({ icon, label, value, hint, delta, deltaText, tone = '' }: {
  icon: ReactNode; label: string; value: ReactNode; hint?: ReactNode; delta?: Delta | null; deltaText?: string; tone?: '' | 'good' | 'warn' | 'bad';
}) {
  return <li className={`finKpi ${tone}`}>
    <span className="finKpiIcon" aria-hidden="true">{icon}</span>
    <div className="finKpiBody">
      <span className="finKpiLabel">{label}</span>
      <strong className="finKpiValue">{value}</strong>
      {(hint || delta) && <span className="finKpiMeta">{delta && <DeltaChip delta={delta} text={deltaText} />}{hint && <small>{hint}</small>}</span>}
    </div>
  </li>;
}

/** The delta chip on its own (tables use it too). Arrows are decoration; the words carry the meaning. */
export function DeltaChip({ delta, text, compact }: { delta: Delta; text?: string; compact?: boolean }) {
  const Icon = delta.direction === 'up' ? ArrowUp : delta.direction === 'down' ? ArrowDown : Minus;
  const words = text ?? (delta.percent === null ? t(`fshell.delta.${delta.direction}` as MessageKey) : t(`fshell.delta.pct.${delta.direction}` as MessageKey, { percent: Math.abs(delta.percent) }));
  // Compact (tables): the arrow and the figure show; the words are read out.
  if (compact) return <span className={`finDelta ${delta.tone}`}><Icon aria-hidden="true" /><span aria-hidden="true" dir="ltr">{delta.percent === null ? '' : `${Math.abs(delta.percent)}%`}</span><span className="visuallyHidden">{words}</span></span>;
  return <span className={`finDelta ${delta.tone}`}><Icon aria-hidden="true" /><span>{words}</span></span>;
}

/** A white card with a soft border: title (with an optional icon and subtitle), actions, body. */
export function FinCard({ title, icon, subtitle, actions, children, className = '', id }: {
  title: string; icon?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; id?: string;
}) {
  const autoId = useId();
  const headingId = id ?? autoId;
  return <section className={`finCard ${className}`} aria-labelledby={headingId}>
    <div className="finCardHead">
      <h2 id={headingId}>{icon && <span className="finCardIcon" aria-hidden="true">{icon}</span>}<span>{title}</span>{subtitle && <small className="finCardSub">{subtitle}</small>}</h2>
      {actions && <div className="finCardActions">{actions}</div>}
    </div>
    {children}
  </section>;
}

/** An honest banner: amber for warnings, green for a reassurance, grey for information. */
export function FinBanner({ tone = 'info', icon, title, children, action }: {
  tone?: 'warn' | 'ok' | 'info'; icon?: ReactNode; title?: string; children?: ReactNode; action?: ReactNode;
}) {
  return <div className={`finBanner ${tone}`} role={tone === 'warn' ? 'note' : undefined}>
    {icon && <span className="finBannerIcon" aria-hidden="true">{icon}</span>}
    <div className="finBannerText">{title && <strong>{title}</strong>}{children && <div>{children}</div>}</div>
    {action && <div className="finBannerAction">{action}</div>}
  </div>;
}

/** A status chip (Matched, Review, Verified, Filled (paper)). The text says the status; colour only helps. */
export function StatusChip({ tone = 'muted', children }: { tone?: 'ok' | 'warn' | 'bad' | 'info' | 'muted'; children: ReactNode }) {
  return <span className={`finChip ${tone}`}>{children}</span>;
}
