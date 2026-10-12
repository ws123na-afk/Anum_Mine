// The voice assistant full screen (docs/voice.md#the-orb): the orb large and centred, the state and
// what was heard or answered under it. A modal dialog: focus moves in and stays in (Tab cycles),
// Escape and the close button leave, focus returns to what opened it, the page behind does not
// scroll. Its Orb3D mounts with the dialog and frees its WebGL context when the dialog closes.
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Mic, Square, VolumeX, X } from 'lucide-react';
import { Orb3D, type OrbState } from './Orb3D';
import { t } from './lib/i18n';

/** The orb size that fits the screen with the text under it: big on desktop, never past the edges. */
export function fullscreenOrbSize(width: number, height: number): number {
  return Math.round(Math.max(160, Math.min(width * 0.6, (height - 300) * 0.62, 520)));
}

export function OrbFullscreen({ name, state, level, alert, stateLabel, heard, answer, talkLabel, talkDisabled, onTalk, onClose, fallback }: {
  name: string; state: OrbState; level: number; alert: boolean; stateLabel: string; heard: string; answer: string;
  talkLabel: string; talkDisabled: boolean; onTalk: () => void; onClose: () => void; fallback: ReactNode;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  const [size] = useState(() => fullscreenOrbSize(window.innerWidth, window.innerHeight));
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const body = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    dialog.current?.querySelector<HTMLElement>('.orbFullscreenTalk')?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); close.current(); return; }
      if (event.key !== 'Tab' || !dialog.current) return;
      const focusable = [...dialog.current.querySelectorAll<HTMLElement>('button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])')];
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      const inside = dialog.current.contains(document.activeElement);
      if (event.shiftKey && (document.activeElement === first || !inside)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !inside)) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('keydown', onKey); document.body.style.overflow = body; opener?.focus?.(); };
  }, []);
  return createPortal(<div className="orbFullscreen" data-state={state} ref={dialog} role="dialog" aria-modal="true" aria-labelledby="orb-fullscreen-title">
    <button type="button" className="orbFullscreenClose" onClick={onClose} aria-label={t('voice.fullscreen.close')} title={t('voice.fullscreen.close')}><X aria-hidden="true" /></button>
    <div className="orbFullscreenStage">
      <h2 id="orb-fullscreen-title" className="visuallyHidden">{t('voice.label', { name }, { isolate: false })}</h2>
      <button type="button" className="voiceOrb orbFullscreenTalk" style={{ width: size, height: size }} onClick={onTalk} disabled={talkDisabled} aria-label={talkLabel}>
        <span className="voiceOrbHalo" aria-hidden="true" />
        <span className="voiceOrbGlow" aria-hidden="true" />
        <Orb3D state={state} alert={alert} level={level} size={size} fallback={fallback} />
        <span className="voiceOrbIcon" aria-hidden="true">{state === 'listening' ? <Square size={30} /> : state === 'speaking' ? <VolumeX size={32} /> : <Mic size={34} />}</span>
      </button>
      <p className="orbFullscreenState" role="status" aria-live="polite">{stateLabel}</p>
      {heard && <p className="orbFullscreenHeard" dir="auto">{heard}</p>}
      {answer && <p className="orbFullscreenAnswer" dir="auto" aria-live="polite">{answer}</p>}
      <p className="orbFullscreenHint">{t('voice.fullscreen.hint')}</p>
    </div>
  </div>, document.body);
}
