// The help articles, bundled at build time from docs/help/*.md (docs/help.md), so the panel works
// offline. Vite inlines each file as a string; parsing lives in lib/help.ts (unit tested).
import { parseArticle, type HelpArticle } from './lib/help';

const files = import.meta.glob('../../../docs/help/*.md', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;

export const HELP_ARTICLES: readonly HelpArticle[] = Object.entries(files)
  .map(([path, source]) => parseArticle(path, source))
  .filter((article): article is HelpArticle => article !== null);
