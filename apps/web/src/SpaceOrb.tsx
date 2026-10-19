// The voice assistant's space orb (docs/voice.md#the-space-orb). A React wrapper around
// lib/spaceScene.ts: the renderer is created in an effect and destroyed on unmount, so each mounted
// orb has exactly one animation loop. `full` is the cinematic scene that fills the Voice page, with
// Pause and Shooting star; `compact` is the round orb on the Call page.
import { useEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { Pause, Play, Sparkles } from 'lucide-react';
import atlasUrl from './assets/space-assets.webp';
import { createSpaceScene, type SpaceScene, type SpaceVariant } from './lib/spaceScene';
import type { SpaceMode } from './lib/spaceMode';
import { t } from './lib/i18n';
import './space.css';

export type { SpaceMode } from './lib/spaceMode';

interface SpaceOrbProps {
  mode: SpaceMode;
  variant: SpaceVariant;
  /** Measured audio level 0..1, read every frame (microphone while listening, reply output while speaking). */
  getLevel?: () => number;
  /** Shown when 2D canvas is unavailable. */
  fallback?: ReactNode;
  /** Full scene: the talk button the orb is drawn under, and the panel the sprites stay above. */
  orbAnchor?: RefObject<HTMLElement | null>;
  floorAnchor?: RefObject<HTMLElement | null>;
  className?: string;
  /**
   * What is laid over the scene. As a function it receives the Pause and Shooting star controls
   * (full scene only) to place where the layout wants them.
   */
  children?: ReactNode | ((controls: ReactNode) => ReactNode);
}

export function SpaceOrb({ mode, variant, getLevel, fallback, orbAnchor, floorAnchor, className, children }: SpaceOrbProps) {
  const stage = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const scene = useRef<SpaceScene | null>(null);
  const level = useRef(getLevel);
  level.current = getLevel;
  const [unavailable, setUnavailable] = useState(false);
  const [textureError, setTextureError] = useState(false);
  const [paused, setPaused] = useState(false);
  const [reduced] = useState(() => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches);

  useEffect(() => {
    if (!canvas.current) return;
    const created = createSpaceScene(canvas.current, {
      atlasUrl, variant, pointerTarget: stage.current ?? undefined,
      getLevel: () => level.current?.() ?? 0,
      orbAnchor: orbAnchor?.current, floorAnchor: floorAnchor?.current,
      onStatus: (status) => setTextureError(status === 'texture-error'),
    });
    if (!created) { setUnavailable(true); return; }
    scene.current = created;
    created.setMode(mode);
    // Tests reach the live renderer through the canvas.
    (canvas.current as HTMLCanvasElement & { anumSpace?: SpaceScene }).anumSpace = created;
    const element = canvas.current;
    return () => {
      created.destroy();
      scene.current = null;
      delete (element as HTMLCanvasElement & { anumSpace?: SpaceScene }).anumSpace;
    };
    // The renderer lives as long as the component; mode changes go through setMode below.
  }, [variant]);

  useEffect(() => { scene.current?.setMode(mode); }, [mode]);

  const controls = variant === 'full' ? <div className="spaceControls">
    {textureError && <p className="spaceNote" role="alert">{t('voice.space.textureError')}</p>}
    <button type="button" aria-pressed={paused} title={paused ? t('voice.space.resume') : t('voice.space.pause')} onClick={() => { const next = !paused; setPaused(next); scene.current?.setPaused(next); }}>
      {paused ? <Play aria-hidden="true" /> : <Pause aria-hidden="true" />}<span>{paused ? t('voice.space.resume') : t('voice.space.pause')}</span>
    </button>
    <button type="button" data-draws disabled={paused || reduced} title={paused || reduced ? t('voice.space.meteorPaused') : t('voice.space.meteor')}
      onClick={() => scene.current?.shootingStar()}>
      <Sparkles aria-hidden="true" /><span>{t('voice.space.meteor')}</span>
    </button>
  </div> : null;

  // Without a 2D canvas there is no scene to pause, so no Pause or Shooting star.
  const overlay = typeof children === 'function' ? children(variant === 'full' && !unavailable ? controls : null) : children;
  // Without a 2D canvas the compact orb shows its fallback inside the outer orb button; the full
  // scene keeps its sized container and its children (the talk button and the page's controls), so
  // speech can still be started and stopped.
  if (unavailable) {
    if (variant !== 'full') return <>{fallback}</>;
    return <div className={`spaceOrb ${variant} spaceFallback${className ? ` ${className}` : ''}`} data-space-mode={mode}>{fallback}{overlay}</div>;
  }
  return <div ref={stage} className={`spaceOrb ${variant}${className ? ` ${className}` : ''}`} data-space-mode={mode} data-space-paused={paused ? 'true' : undefined}>
    <canvas ref={canvas} className="spaceCanvas" aria-hidden="true" />
    {overlay}
  </div>;
}
