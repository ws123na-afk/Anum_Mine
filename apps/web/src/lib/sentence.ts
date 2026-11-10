// How an error's sentence joins the template around it (docs/web-localization.md#error-sentences).
// Pure, so `pnpm --filter @anum/web test:unit` can exercise it without a browser.
//
// Catalog templates place an error's text in two ways: "Could not load: {message} Check your
// connection." (the value must end its own sentence) and "{message}. Try again." (the template
// ends it). The API's messages end with a full stop and the browser's do not, so one value cannot
// suit both: "Failed to fetch Check your connection" and "not reachable.. Try again" were the
// result. fitSentence adjusts the value to the text that follows its placeholder.

/** Placeholder names that carry an error's sentence (lib/i18n.ts applies fitSentence to these). */
export const SENTENCE_PLACEHOLDERS: ReadonlySet<string> = new Set(['message', 'reason', 'error', 'detail']);

/** Ends a sentence already: a full stop, a question or exclamation mark (Latin or Arabic) or an ellipsis, with any closing quote or bracket after it. */
const ENDED = /[.!?؟۔…]["'”’»)\]]*$/;
/** The template continues with its own full stop, after any closing bracket or quote. */
const STOP_FOLLOWS = /^["'”’»)\]]*\./;
/** The template continues with a new sentence: white space, then a capital letter, an Arabic letter or an opening quote or bracket. */
const SENTENCE_FOLLOWS = /^\s+["'“‘«(\[]?[A-Z؀-ۿ]/;

/**
 * `value` as it should read before `after`, the template text that follows its placeholder:
 * without its own full stop when the template adds one, and with one when a new sentence follows.
 * A value that is not a sentence (empty, or only a number) is returned as it is.
 */
export function fitSentence(value: string, after: string): string {
  const text = value.trimEnd();
  if (!text || /^[\d\s.,-]+$/.test(text)) return value;
  if (STOP_FOLLOWS.test(after)) return text.replace(/\.+$/, '');
  if (SENTENCE_FOLLOWS.test(after) && !ENDED.test(text)) return `${text}.`;
  return text.replace(/\.{2,}$/, '.');
}

/** `value` ending with exactly one full stop (or the mark it already ends with). */
export function asSentence(value: string): string {
  const text = value.trimEnd();
  if (!text) return text;
  return ENDED.test(text) ? text.replace(/\.{2,}$/, '.') : `${text}.`;
}

/** A request that never got an answer: the browser's fetch rejects with a TypeError ("Failed to fetch", "Load failed", "NetworkError when attempting to fetch resource."). */
export function isNetworkFailure(error: unknown): boolean {
  if (!(error instanceof Error) || error.name === 'AbortError') return false;
  return error instanceof TypeError && /failed to fetch|load failed|networkerror|network request failed|fetch/i.test(error.message);
}
