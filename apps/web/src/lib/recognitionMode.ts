// Which speech recognizer the Voice and Call views use (docs/voice.md). Pure, so `pnpm test:unit`
// covers it without a browser.

export type RecognitionMode = 'on-device' | 'cloud' | 'typing';

export interface AvailabilityProbe {
  available?: (options: { langs: string[]; processLocally: boolean }) => Promise<string>;
}

/** Whether the page runs under browser automation (`navigator.webdriver`): headless test browsers. */
export function automatedBrowser(): boolean {
  try { return typeof navigator !== 'undefined' && navigator.webdriver === true; } catch { return false; }
}

/**
 * On-device when the browser says so, else the cloud recognizer, else typing.
 * `SpeechRecognition.available()` crashes the whole renderer in headless Chromium (the Chromium 141
 * headless shell has no on-device speech service), which no try/catch can stop, so an automated
 * browser skips the on-device probe.
 */
export async function chooseRecognitionMode(recognizer: AvailabilityProbe | undefined, locale: string, automated: boolean): Promise<RecognitionMode> {
  if (!recognizer) return 'typing';
  if (!automated && typeof recognizer.available === 'function') {
    try {
      const availability = await recognizer.available({ langs: [locale], processLocally: true });
      if (availability === 'available') return 'on-device';
    } catch {
      // Older implementations reject the options object; treat as cloud.
    }
  }
  return 'cloud';
}
