/**
 * One look for every panel beside the scene: a title, one line on what it is for, its live status and a fold button.
 * The fold state is kept per browser (`anum.hq.nav.fold.<id>`). Folded, the body is hidden but the header stays, so the
 * panel can always be opened again and its status is still readable.
 */
import { ChevronDown, ChevronUp } from 'lucide-react';
import { useCallback, useId, useState, type ReactNode } from 'react';
import { t } from '../lib/i18n.ts';
import './panelHeader.css';

const KEY = 'anum.hq.nav.fold.';
const readFold = (id: string): boolean => { try { return window.localStorage.getItem(KEY + id) === '1'; } catch { return false; } };
const writeFold = (id: string, folded: boolean) => { try { if (folded) window.localStorage.setItem(KEY + id, '1'); else window.localStorage.removeItem(KEY + id); } catch { /* remembered for this page only */ } };

export function PanelHeader(props: { id: string; title: string; purpose?: string; live?: ReactNode; actions?: ReactNode; folded?: boolean; onFold?: () => void; bodyId?: string }) {
  return (
    <header className="hqPh">
      <div className="hqPhTitle">
        <h2 id={props.id}>{props.title}</h2>
        <div className="hqPhSide">
          {props.actions}
          {props.onFold && (
            <button type="button" className="hqIconButton hqPhFold" aria-expanded={!props.folded} aria-controls={props.bodyId}
              aria-label={props.folded ? t('hqPanels.open', { name: props.title }) : t('hqPanels.fold', { name: props.title })}
              title={props.folded ? t('hqPanels.open', { name: props.title }) : t('hqPanels.fold', { name: props.title })} onClick={props.onFold}>
              {props.folded ? <ChevronDown aria-hidden="true" /> : <ChevronUp aria-hidden="true" />}
            </button>
          )}
        </div>
      </div>
      {props.purpose && <p className="hqPhPurpose">{props.purpose}</p>}
      {props.live && <div className="hqPhLive">{props.live}</div>}
    </header>
  );
}

/** The fold state of one panel, kept per browser under `anum.hq.nav.fold.<id>`. */
export function useFold(id: string, enabled = true): [boolean, () => void] {
  const [folded, setFolded] = useState(() => (enabled ? readFold(id) : false));
  const toggle = useCallback(() => setFolded((v) => { writeFold(id, !v); return !v; }), [id]);
  return [folded, toggle];
}

export function PanelSection(props: { id: string; title: string; purpose?: string; live?: ReactNode; actions?: ReactNode; foldable?: boolean; className?: string; children: ReactNode; ariaLabel?: string }) {
  const uid = useId();
  const [folded, toggle] = useFold(props.id, props.foldable !== false);
  const heading = `${uid}-h`;
  const body = `${uid}-b`;
  return (
    <section className={`hqSec ${props.className ?? ''}`} aria-labelledby={props.ariaLabel ? undefined : heading} aria-label={props.ariaLabel} data-folded={folded} data-panel={props.id}>
      <PanelHeader id={heading} title={props.title} purpose={props.purpose} live={props.live} actions={props.actions} folded={folded} onFold={props.foldable === false ? undefined : toggle} bodyId={body} />
      <div id={body} className="hqSecBody" hidden={folded}>{props.children}</div>
    </section>
  );
}
