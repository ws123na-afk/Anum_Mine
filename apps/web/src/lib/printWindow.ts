// Opens a printable HTML page (an invoice) in a new window through a Blob URL and calls print().
// The window is opened inside the click so a pop-up blocker allows it; the page is fetched with the
// caller's own credentials, never put in a URL.
import { t } from './i18n';

export async function printHtml(fetchHtml: () => Promise<Blob>): Promise<void> {
  const win = window.open('', '_blank');
  if (!win) throw new Error(t('inv.print.blocked'));
  let url: string;
  try {
    const blob = await fetchHtml();
    url = URL.createObjectURL(new Blob([await blob.arrayBuffer()], { type: 'text/html;charset=utf-8' }));
  } catch (error) { win.close(); throw error; }
  win.location.href = url;
  const started = Date.now();
  const timer = window.setInterval(() => {
    try {
      if (win.closed || Date.now() - started > 15_000) { window.clearInterval(timer); URL.revokeObjectURL(url); return; }
      if (win.location.href === url && win.document.readyState === 'complete') { window.clearInterval(timer); win.focus(); win.print(); }
    } catch { window.clearInterval(timer); }
  }, 100);
}
