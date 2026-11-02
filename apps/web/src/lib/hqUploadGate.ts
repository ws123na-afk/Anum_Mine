/**
 * When a live canvas (the billboard atlas, the screen atlas) goes to the GPU again (docs/ANUM_MINE_HQ_GPU_CHECK.md, "Texture uploads"). The
 * whole canvas is uploaded each time, a few megabytes on an integrated GPU, so it happens only when the drawing changed: a new canvas makes a
 * new texture, the same canvas with a new version is uploaded again, and the same canvas at the same version is not touched. Pure.
 */
export type UploadStep = 'new' | 'upload' | 'none';

export class UploadGate<S> {
  private source: S | null = null;
  private version: number | null = null;
  /** Uploads of an existing texture so far (the renderer's `stats.uploads`, read by the Graphics check). */
  uploads = 0;

  /** What to do for `source` at `version` (no version: the caller does not know, so it is uploaded, as before). */
  step(source: S | null, version?: number): UploadStep {
    if (source !== this.source) { this.source = source; this.version = version ?? null; return 'new'; }
    if (source === null) return 'none';
    if (version !== undefined && version === this.version) return 'none';
    this.version = version ?? null;
    this.uploads += 1;
    return 'upload';
  }
}
