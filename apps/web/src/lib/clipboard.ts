// Copy to the clipboard (docs/conversations.md#message-actions). The async Clipboard API
// first; where it is missing or refused (an insecure origin, an older browser) a hidden
// textarea and `execCommand('copy')`. Resolves false when neither worked, so the caller
// can say so instead of claiming "Copied".
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* fall back below */ }
  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.insetInlineStart = '-9999px';
    document.body.appendChild(area);
    const focused = document.activeElement as HTMLElement | null;
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    focused?.focus();
    return ok;
  } catch {
    return false;
  }
}

/** Save `text` as a file the browser downloads (the conversation as Markdown). */
export function downloadText(fileName: string, text: string, type = 'text/markdown;charset=utf-8'): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  link.rel = 'noopener';
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
