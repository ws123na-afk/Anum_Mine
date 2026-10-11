// Modal dialog and side drawer with the keyboard basics: focus moves in on open, Tab stays
// inside, Escape closes, and focus returns to whatever opened it.
import { useEffect, useId, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { t } from './lib/i18n';
import './dialog.css';

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function useModal(onClose: () => void) {
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const first = panel.current?.querySelector<HTMLElement>('[data-autofocus]') ?? panel.current?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); close.current(); return; }
      if (event.key !== 'Tab' || !panel.current) return;
      const items = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (!items.length) return;
      const [head, tail] = [items[0], items[items.length - 1]];
      if (event.shiftKey && document.activeElement === head) { event.preventDefault(); tail.focus(); }
      else if (!event.shiftKey && document.activeElement === tail) { event.preventDefault(); head.focus(); }
    };
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('keydown', onKey); opener?.focus(); };
  }, []);
  return panel;
}

export function ConfirmDialog({ title, children, confirmLabel, danger, busy, confirmDisabled, onConfirm, onCancel }: {
  title: string; children: ReactNode; confirmLabel: string; danger?: boolean; busy?: boolean; confirmDisabled?: boolean;
  onConfirm: () => void; onCancel: () => void;
}) {
  const panel = useModal(onCancel);
  const titleId = useId();
  const bodyId = useId();
  return <div className="dialogBackdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onCancel(); }}>
    <div className="surface dialogPanel" role="alertdialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={bodyId} ref={panel}>
      <h2 id={titleId}>{title}</h2>
      <div id={bodyId} className="dialogBody">{children}</div>
      <div className="actions">
        <button type="button" className={danger ? 'danger' : undefined} disabled={busy || confirmDisabled} onClick={onConfirm} data-autofocus>{busy ? t('dialog.working') : confirmLabel}</button>
        <button type="button" className="secondary" disabled={busy} onClick={onCancel}>{t('common.cancel')}</button>
      </div>
    </div>
  </div>;
}

export function Drawer({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const panel = useModal(onClose);
  const titleId = useId();
  return <div className="dialogBackdrop drawerBackdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <aside className="drawerPanel" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={panel}>
      <header className="drawerHeader">
        <h2 id={titleId}>{title}</h2>
        <button type="button" className="iconButton" aria-label={t('dialog.close')} title={t('dialog.close')} onClick={onClose} data-autofocus><X aria-hidden="true" /></button>
      </header>
      <div className="drawerBody">{children}</div>
    </aside>
  </div>;
}
