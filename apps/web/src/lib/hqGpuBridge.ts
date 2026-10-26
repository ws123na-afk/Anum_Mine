/** Where the live renderer registers itself, so the Graphics check (a separate lazy chunk) can measure the real scene. Type-only imports: no three.js here. */
import type { HqRenderer } from './hqThreeRenderer.ts';

let live: HqRenderer | null = null;
export const getLiveRenderer = (): HqRenderer | null => live;
export function setLiveRenderer(renderer: HqRenderer | null): void { live = renderer; }
