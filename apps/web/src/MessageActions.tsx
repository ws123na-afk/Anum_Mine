import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { Check, Copy, MoreHorizontal } from 'lucide-react';
import { copyText } from './lib/clipboard';
import { t } from './lib/i18n';
import './message.css';

/**
 * Message actions (docs/conversations.md#message-actions): the row of buttons under a
 * chat message or an AI answer anywhere in the app. Always visible (never hover-only),
 * so touch and keyboard users reach them; each button has a spoken name.
 */
export function MessageActions({ label, children }: { label: string; children: ReactNode }) {
  return <div className="msgActions" role="group" aria-label={label}>{children}</div>;
}

/** Copy `text`; the icon turns into a tick for two seconds and `onCopied` announces it. */
export function CopyAction({ text, label, onCopied, showLabel = false }: { text: string | (() => string); label?: string; onCopied?: (ok: boolean) => void; showLabel?: boolean }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  async function copy() {
    const ok = await copyText(typeof text === 'function' ? text() : text);
    setCopied(ok);
    onCopied?.(ok);
    window.clearTimeout(timer.current);
    if (ok) timer.current = window.setTimeout(() => setCopied(false), 2000);
  }
  const name = label ?? t('message.copy');
  return <button type="button" className={showLabel ? 'msgAction labelled' : 'msgAction'} onClick={() => void copy()} aria-label={showLabel ? undefined : name} title={name}>
    {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}{showLabel && <span>{copied ? t('message.copied') : name}</span>}
  </button>;
}

export interface MenuItem { id: string; label: string; icon?: ReactNode; danger?: boolean; disabled?: boolean; onSelect: () => void }

/**
 * A small menu behind a "More actions" button: arrow keys move, Escape closes and gives
 * focus back to the button, Tab leaves it; a click outside closes it.
 */
export function ActionMenu({ label, items, icon, className = 'msgAction' }: { label: string; items: MenuItem[]; icon?: ReactNode; className?: string }) {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement | null>(null);
  const menu = useRef<HTMLDivElement | null>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const first = menu.current?.querySelector<HTMLButtonElement>('button:not(:disabled)');
    first?.focus();
    const close = (event: MouseEvent) => {
      if (!menu.current?.contains(event.target as Node) && !button.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);
  function onKey(event: KeyboardEvent<HTMLDivElement>) {
    const buttons = [...(menu.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? [])];
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === 'Escape') { event.preventDefault(); setOpen(false); button.current?.focus(); }
    else if (event.key === 'ArrowDown') { event.preventDefault(); buttons[(index + 1) % buttons.length]?.focus(); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); buttons[(index - 1 + buttons.length) % buttons.length]?.focus(); }
    else if (event.key === 'Home') { event.preventDefault(); buttons[0]?.focus(); }
    else if (event.key === 'End') { event.preventDefault(); buttons[buttons.length - 1]?.focus(); }
    else if (event.key === 'Tab') setOpen(false);
  }
  return <span className="msgMenuWrap">
    <button ref={button} type="button" className={className} aria-label={label} title={label} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? id : undefined}
      onClick={() => setOpen((value) => !value)}>{icon ?? <MoreHorizontal aria-hidden="true" />}</button>
    {open && <div ref={menu} id={id} className="msgMenu" role="menu" aria-label={label} onKeyDown={onKey}>
      {items.map((item) => <button key={item.id} type="button" role="menuitem" className={item.danger ? 'danger' : undefined} disabled={item.disabled}
        onClick={() => { setOpen(false); button.current?.focus(); item.onSelect(); }}>{item.icon}<span>{item.label}</span></button>)}
    </div>}
  </span>;
}

/**
 * A polite live region for short confirmations ("Copied", "Deleted"), read by screen
 * readers and shown briefly. `say` replaces the message; it clears itself.
 */
export function useToast(): { toast: ReactNode; say: (text: string) => void } {
  const [text, setText] = useState('');
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const say = (next: string) => {
    window.clearTimeout(timer.current);
    // Cleared first so the same words twice are announced twice.
    setText('');
    window.setTimeout(() => setText(next), 30);
    timer.current = window.setTimeout(() => setText(''), 2600);
  };
  return { say, toast: <div className="msgToast" role="status" aria-live="polite" aria-atomic="true" data-shown={text ? 'true' : 'false'}>{text}</div> };
}
