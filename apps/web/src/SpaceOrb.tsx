// The voice assistant's space orb (docs/voice.md#the-space-orb). A React wrapper around
// lib/spaceScene.ts: the renderer is created in an effect and destroyed on unmount, so each mounted
// orb has exactly one animation loop. `compact` is the round orb on the Voice and Call pages;
// `full` is the cinematic scene in the full-screen view, with Pause and Shooting star.
import { useEffect, useRef, useState } from 'react';
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
  fallback?: React.ReactNode;
  /** Full screen only: the Pause and Shooting star controls. */
  controls?: boolean;
  children?: React.ReactNode;
}

export function SpaceOrb({ mode, variant, getLevel, fallback, controls = false, children }: SpaceOrbProps) {
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
      onStatus: (status) => setTextureError(status === 'texture-error'),
    });
    if (!created) { setUnavailable(true); return; }
    scene.current = created;
    created.setMode(mode);
    // Tests and the full-screen controls reach the live renderer through the canvas.
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

  // Without a 2D canvas the compact orb shows its fallback inside the outer orb button; the full
  // view keeps its children (the talk button) so speech can still be started and stopped.
  if (unavailable) {
    if (variant !== 'full') return <>{fallback}</>;
    return <div className={`spaceOrb ${variant} spaceFallback`} data-space-mode={mode}>{fallback}{children}</div>;
  }
  return <div ref={stage} className={`spaceOrb ${variant}`} data-space-mode={mode} data-space-paused={paused ? 'true' : undefined}>
    <canvas ref={canvas} className="spaceCanvas" aria-hidden="true" />
    {children}
    {variant === 'full' && (controls || textureError) && <div className="spaceBar">
      {textureError && <p className="spaceNote" role="alert">{t('voice.space.textureError')}</p>}
      {controls && <div className="spaceControls">
        <button type="button" aria-pressed={paused} onClick={() => { const next = !paused; setPaused(next); scene.current?.setPaused(next); }}>
          {paused ? <Play aria-hidden="true" /> : <Pause aria-hidden="true" />}{paused ? t('voice.space.resume') : t('voice.space.pause')}
        </button>
        <button type="button" disabled={paused || reduced} title={paused || reduced ? t('voice.space.meteorPaused') : undefined}
          onClick={() => scene.current?.shootingStar()}>
          <Sparkles aria-hidden="true" />{t('voice.space.meteor')}
        </button>
      </div>}
    </div>}
  </div>;
}
