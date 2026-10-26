import { useEffect, useState } from 'react';

const FACE = '700 30px "Noto Sans Arabic"';
const SAMPLE = 'ا';
const ready = (): boolean => typeof document !== 'undefined' && !!document.fonts?.check && document.fonts.check(FACE, SAMPLE);

/**
 * True once the self-hosted Arabic face (public/fonts/fonts.css) is loaded. The 3D signs are drawn on a canvas, which does not
 * wait for a web font, so a view that letters Arabic signs asks for the face here and adds the result to its sign texts: the
 * lettering changes once, and the renderer draws the atlas again with the right glyphs. Always false when `active` is false.
 */
export function useArabicSignFont(active: boolean): boolean {
  const [loaded, setLoaded] = useState(ready);
  useEffect(() => {
    if (!active || loaded || typeof document === 'undefined' || !document.fonts?.load) return;
    let on = true;
    document.fonts.load(FACE, SAMPLE).then(() => { if (on) setLoaded(true); }, () => undefined);
    return () => { on = false; };
  }, [active, loaded]);
  return active && loaded;
}
