import { useEffect, useRef } from 'react';
import { t } from '../lib/i18n.ts';
import { CELL_H, CELL_W } from './signModel.ts';
import { paintSign, type SignInput } from './signPainter.ts';

/** The sign as it will be drawn on the campus: the same painter, on a canvas twice the cell size. */
export function SignPreview({ input }: { input: SignInput }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const { name, rtl, style, color, logo, logoWidth, logoHeight } = input;
  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return undefined;
    let live = true;
    const draw = () => {
      if (!live) return;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      paintSign(ctx as never, 0, 0, 2, { name, rtl, style, color, logo, logoWidth, logoHeight });
    };
    draw();
    // The Arabic face loads on demand: draw again once fonts are ready so the preview matches the campus.
    void document.fonts?.ready.then(draw).catch(() => undefined);
    return () => { live = false; };
  }, [name, rtl, style, color, logo, logoWidth, logoHeight]);
  return (
    <canvas
      ref={ref} className="hqlPreview" width={CELL_W * 2} height={CELL_H * 2} role="img" data-testid="hql-preview"
      aria-label={t('hqLogo.previewAria', { name, kind: logo ? t('hqLogo.kindLogo') : t('hqLogo.kindLettered') })}
    />
  );
}
