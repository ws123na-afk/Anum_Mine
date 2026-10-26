/** True when the text holds Arabic script (Arabic, Supplement, Extended-A, Presentation Forms A and B). Pure and dependency free. */
export function hasArabicScript(text: string): boolean {
  return /[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]/.test(text);
}
