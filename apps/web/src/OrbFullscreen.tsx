// The voice assistant full screen (docs/voice.md#the-space-orb): the cinematic space scene with the
// orb in the middle, the state and what was heard or answered under it, Pause and Shooting star.
// A modal dialog: focus moves in and stays in (Tab cycles), Escape and the close button leave,
// focus returns to what opened it, the page behind does not scroll. Its scene mounts with the
// dialog and its animation loop, observer and listeners are released when the dialog closes.
import { useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Mic, Square, VolumeX, X } from 'lucide-react';
import { SpaceOrb } from './SpaceOrb';
import type { SpaceMode } from './lib/spaceMode';
import { t } from './lib/i18n';

export function OrbFullscreen({ name, mode, getLevel, stateLabel, heard, answer, talkLabel, talkDisabled, onTalk, onClose, fallback }: {
  name: string; mode: SpaceMode; getLevel: () => number; stateLabel: string; heard: string; answer: string;
  talkLabel: string; talkDisabled: boolean; onTalk: () => void; onClose: () => void; fallback: ReactNode;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
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
  return createPortal(<div className="orbFullscreen" data-state={mode} ref={dialog} role="dialog" aria-modal="true" aria-labelledby="orb-fullscreen-title">
    <h2 id="orb-fullscreen-title" className="visuallyHidden">{t('voice.label', { name }, { isolate: false })}</h2>
    <button type="button" className="orbFullscreenClose" onClick={onClose} aria-label={t('voice.fullscreen.close')} title={t('voice.fullscreen.close')}><X aria-hidden="true" /></button>
    <SpaceOrb variant="full" mode={mode} getLevel={getLevel} controls fallback={fallback}>
      {/* The orb itself is the talk button, laid over the projected sphere. */}
      <button type="button" className="orbFullscreenTalk" onClick={onTalk} disabled={talkDisabled} aria-label={talkLabel}>
        <span className="voiceOrbIcon" aria-hidden="true">{mode === 'listening' ? <Square size={30} /> : mode === 'speaking' ? <VolumeX size={32} /> : <Mic size={34} />}</span>
      </button>
    </SpaceOrb>
    <div className="orbFullscreenText">
      <p className="orbFullscreenState" role="status" aria-live="polite">{stateLabel}</p>
      {heard && <p className="orbFullscreenHeard" dir="auto">{heard}</p>}
      {answer && <p className="orbFullscreenAnswer" dir="auto" aria-live="polite">{answer}</p>}
      <p className="orbFullscreenHint">{t('voice.fullscreen.hint')}</p>
    </div>
  </div>, document.body);
}
