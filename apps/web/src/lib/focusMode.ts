// Focus mode for the Voice page (docs/voice.md#full-screen-focus): the browser's full screen on the
// space scene, with the webkit prefix where needed, or a fixed overlay where an element cannot go
// full screen (iPhone Safari), which the page's CSS draws over the whole window.

/** `native`: the browser's full screen; `overlay`: the page's own full-window layer; false: off. */
export type FocusMode = false | 'native' | 'overlay';

type FullscreenElement = HTMLElement & { webkitRequestFullscreen?: () => Promise<void> | void };
type FullscreenDocument = Document & { webkitFullscreenElement?: Element | null; webkitExitFullscreen?: () => Promise<void> | void };

/** The element the browser shows full screen now, if any. */
export function focusElement(doc: FullscreenDocument = document): Element | null {
  return doc.fullscreenElement ?? doc.webkitFullscreenElement ?? null;
}

/** Puts `element` full screen; falls back to the overlay when the browser cannot (or refuses). */
export async function enterFocus(element: FullscreenElement): Promise<Exclude<FocusMode, false>> {
  try {
    if (typeof element.requestFullscreen === 'function') {
      await element.requestFullscreen({ navigationUI: 'hide' });
      return 'native';
    }
    if (typeof element.webkitRequestFullscreen === 'function') {
      await element.webkitRequestFullscreen();
      return 'native';
    }
  } catch {
    // Refused (no user gesture, a permissions policy): the overlay still hides everything else.
  }
  return 'overlay';
}

/** Leaves the browser's full screen if the page is in it. */
export async function exitFocus(doc: FullscreenDocument = document): Promise<void> {
  if (!focusElement(doc)) return;
  try {
    if (typeof doc.exitFullscreen === 'function') await doc.exitFullscreen();
    else await doc.webkitExitFullscreen?.();
  } catch {
    // Already left (the browser's own exit).
  }
}
