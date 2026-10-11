import { useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
// The legacy build: the modern one needs JavaScript features (Map.getOrInsertComputed) that
// current browsers and the desktop webviews do not all ship yet.
import { GlobalWorkerOptions, getDocument, type PDFDocumentProxy } from 'pdfjs-dist/legacy/build/pdf.mjs';
import workerUrl from 'pdfjs-dist/legacy/build/pdf.worker.min.mjs?url';
import { t } from './lib/i18n';

GlobalWorkerOptions.workerSrc = workerUrl;

/**
 * A PDF drawn page by page onto a canvas by pdf.js (docs/files.md#previews). Nothing in the PDF
 * runs: pdf.js's core API executes no PDF JavaScript, XFA forms stay off, and the page is pixels in
 * this app's own document, so no plugin and no foreign frame is involved. Loaded on first use only.
 */
export default function PdfPreview({ data, name }: { data: ArrayBuffer; name: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const holder = useRef<HTMLDivElement>(null);
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    // pdf.js transfers the buffer to its worker; give it a copy so a re-render can load again.
    const task = getDocument({ data: new Uint8Array(data.slice(0)), enableXfa: false });
    task.promise.then((loaded) => { if (live) { setDoc(loaded); setPage(1); } }, () => { if (live) setError(t('files.pdf.unreadable')); });
    return () => { live = false; void task.destroy(); };
  }, [data]);

  useEffect(() => {
    if (!doc || !canvas.current) return;
    let cancelled = false;
    let render: { cancel(): void; promise: Promise<void> } | null = null;
    void doc.getPage(page).then((current) => {
      if (cancelled || !canvas.current) return;
      const width = Math.max(240, holder.current?.clientWidth ?? 640);
      const base = current.getViewport({ scale: 1 });
      const ratio = window.devicePixelRatio || 1;
      const viewport = current.getViewport({ scale: (width / base.width) * ratio });
      const target = canvas.current;
      target.width = Math.floor(viewport.width);
      target.height = Math.floor(viewport.height);
      target.style.width = `${Math.floor(viewport.width / ratio)}px`;
      target.style.height = `${Math.floor(viewport.height / ratio)}px`;
      render = current.render({ canvas: target, viewport });
      render.promise.catch(() => undefined);
    }, () => { if (!cancelled) setError(t('files.pdf.pageFailed')); });
    return () => { cancelled = true; render?.cancel(); };
  }, [doc, page]);

  if (error) return <p className="notice errorNotice" role="alert">{error}</p>;
  if (!doc) return <p className="notice" role="status">{t('files.pdf.opening')}</p>;
  return (
    <div className="pdfPreview" ref={holder}>
      <div className="pdfPager" role="group" aria-label={t('files.pdf.pages')}>
        <button type="button" className="iconButton" aria-label={t('files.pdf.previous')} disabled={page <= 1} onClick={() => setPage((n) => n - 1)}><ChevronLeft className="flipRtl" aria-hidden="true" /></button>
        <span aria-live="polite">{t('files.pdf.pageOf', { page, total: doc.numPages })}</span>
        <button type="button" className="iconButton" aria-label={t('files.pdf.next')} disabled={page >= doc.numPages} onClick={() => setPage((n) => n + 1)}><ChevronRight className="flipRtl" aria-hidden="true" /></button>
      </div>
      <canvas ref={canvas} role="img" aria-label={t('files.pdf.pageImage', { page, name })} />
    </div>
  );
}
