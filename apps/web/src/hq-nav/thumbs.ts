/**
 * Thumbnails of the scene: a real snapshot of the stage's canvas, drawn once into a small canvas and
 * cached (memory and, per tab, sessionStorage). A place that was not shown yet has no thumbnail and the
 * strip draws an icon instead; nothing is ever a stock photo. The snapshot is taken inside the renderer's
 * frame callback (the only moment a WebGL canvas can be read without keeping its buffer).
 */
export const THUMB_W = 192;
export const THUMB_H = 108;
const STORE = 'anum.hq.thumbs';
const MAX_ENTRIES = 48;

const memory = new Map<string, string>();
let loaded = false;
const listeners = new Set<() => void>();

function load(): void {
  if (loaded) return;
  loaded = true;
  try {
    const raw = window.sessionStorage.getItem(STORE);
    const rows = raw ? (JSON.parse(raw) as Array<[string, string]>) : [];
    for (const [k, v] of rows) if (typeof k === 'string' && typeof v === 'string' && v.startsWith('data:image/')) memory.set(k, v);
  } catch { /* storage blocked or damaged: start empty */ }
}

function persist(): void {
  try { window.sessionStorage.setItem(STORE, JSON.stringify([...memory.entries()])); } catch { /* quota or blocked: memory only */ }
}

/** The cache key: tenant, workspace, a signature of what is drawn (so a changed layout is a new key), the place and the light. */
export function thumbKey(parts: Readonly<{ tenantId: string; workspaceId: string; signature: string; place: string; night: boolean }>): string {
  return [parts.tenantId, parts.workspaceId, parts.signature, parts.place, parts.night ? 'n' : 'd'].join('|');
}

export function getThumb(key: string): string | null { load(); return memory.get(key) ?? null; }
export function subscribeThumbs(listener: () => void): () => void { listeners.add(listener); return () => { listeners.delete(listener); }; }

export function putThumb(key: string, dataUrl: string): void {
  load();
  memory.delete(key);
  memory.set(key, dataUrl);
  while (memory.size > MAX_ENTRIES) { const oldest = memory.keys().next().value; if (oldest === undefined) break; memory.delete(oldest); }
  persist();
  for (const l of [...listeners]) l();
}

/** Draws the canvas (cover fit) into a small canvas and returns a JPEG data URL, or null when the browser refuses. */
export function snapshotCanvas(source: HTMLCanvasElement): string | null {
  try {
    if (source.width < 2 || source.height < 2) return null;
    const out = document.createElement('canvas');
    out.width = THUMB_W; out.height = THUMB_H;
    const ctx = out.getContext('2d');
    if (!ctx) return null;
    const ratio = THUMB_W / THUMB_H;
    let sw = source.width, sh = source.height, sx = 0, sy = 0;
    if (sw / sh > ratio) { const w = sh * ratio; sx = (sw - w) / 2; sw = w; } else { const h = sw / ratio; sy = (sh - h) / 2; sh = h; }
    ctx.drawImage(source, sx, sy, sw, sh, 0, 0, THUMB_W, THUMB_H);
    return out.toDataURL('image/jpeg', 0.7);
  } catch { return null; }
}

/** One pending capture at a time: the next drawn frame of the stage fills it. */
export class ThumbCapture {
  private want: string | null = null;
  request(key: string): void { load(); if (!memory.has(key)) this.want = key; }
  /** Called from the stage's frame callback with its canvas. */
  onFrame = (canvas: HTMLCanvasElement): void => {
    const key = this.want;
    if (!key) return;
    this.want = null;
    const url = snapshotCanvas(canvas);
    if (url) putThumb(key, url);
  };
  get waiting(): boolean { return this.want !== null; }
}
