// Saving a fetched file (an invoice PDF) from a same-origin blob URL. The file is fetched with the caller's
// own credentials, never through a link that carries a token. Imports nothing of the signed-in app, so the
// public customer portal can use it too.

/** The file name the server gave (`Content-Disposition: attachment; filename="INV-2026-0001.pdf"`) when it is a plain safe name, else `fallback`. */
export function fileNameFromDisposition(header: string | null | undefined, fallback: string): string {
  const given = /filename="?([^";]*)"?/i.exec(header ?? '')?.[1] ?? '';
  return /^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$/.test(given) && !given.includes('..') ? given : fallback;
}

/** Hands a blob to the browser as a download and returns the name it was saved under. */
export function saveBlob(blob: Blob, name: string): string {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = name;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  return name;
}
