// Whether this workspace's AI answers come from the built-in placeholder model (GET /api/v1/model-status,
// docs/model-gateway.md#placeholder-answers). Pure, so `pnpm --filter @anum/web test:unit` covers it.
import type { MessageKey } from './i18n.ts';

export interface ModelStatus {
  /** True when the answers come from the placeholder ("mock") model, not a real one. */
  placeholder: boolean;
  provider: string;
  /** "workspace" when this workspace saved its own model in Settings, else the server's default. */
  source: 'workspace' | 'server';
}

/** The banner text to show, or null when a real model answers (or the status could not be read:
 * an unknown state never claims the answers are placeholders). */
export function placeholderBannerKey(status: ModelStatus | null): MessageKey | null {
  if (!status || status.placeholder !== true) return null;
  return status.source === 'workspace' ? 'model.placeholder.workspace' : 'model.placeholder.server';
}
