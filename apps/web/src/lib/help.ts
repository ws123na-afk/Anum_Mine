// In-app help (docs/help.md): short task-based articles per module, written as Markdown in
// docs/help/<id>.md (English) and docs/help/<id>.ar.md (Arabic) and bundled into the app at build
// time, so help works offline. Pure: parsing, locale choice, search and the page-to-article map are
// unit tested by `pnpm test:unit` without a browser.
import type { Locale } from './i18n.ts';
import type { View } from './shell.ts';

/** Where the full documentation lives; an article's relative `../x.md` links open there. */
export const DOCS_BASE_URL = 'https://github.com/ws123na-afk/Anum_Mine/blob/main/docs/';

export type HelpArticle = {
  id: string;
  locale: Locale;
  title: string;
  summary: string;
  keywords: string[];
  /** The full documentation page for this module, as an absolute URL (or null). */
  docsUrl: string | null;
  /** The article body (Markdown), without the frontmatter and the first heading. */
  body: string;
};

const FRONTMATTER = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/;

/** The docs URL for a link written relative to docs/help (`../tasks.md#x`), or an http(s) link as is. */
export function docsHref(target: string): string | null {
  const value = target.trim();
  if (/^https?:\/\//i.test(value)) return value;
  const relative = value.replace(/^\.\.\//, '');
  if (!relative || relative.startsWith('/') || relative.includes('..') || !/^[A-Za-z0-9_./#-]+$/.test(relative)) return null;
  return DOCS_BASE_URL + relative;
}

/** Reads one article file. `fileName` is `<id>.md` or `<id>.ar.md`; returns null when malformed. */
export function parseArticle(fileName: string, source: string): HelpArticle | null {
  const name = fileName.split('/').pop() ?? fileName;
  const match = /^([a-z0-9-]+)(?:\.(ar))?\.md$/.exec(name);
  if (!match) return null;
  const front = FRONTMATTER.exec(source);
  if (!front) return null;
  const meta: Record<string, string> = {};
  for (const line of front[1].split(/\r?\n/)) {
    const at = line.indexOf(':');
    if (at > 0) meta[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  if (!meta.title || meta.id !== match[1]) return null;
  // The first heading repeats the title; the panel shows the title itself.
  const body = source.slice(front[0].length).replace(/^\s*#\s+[^\n]*\n/, '').trim();
  // Relative doc links become absolute so the shared Markdown renderer keeps them as links.
  const withLinks = body.replace(/\]\(([^)\s]+)\)/g, (all, target: string) => {
    const href = docsHref(target);
    return href ? `](${href})` : all;
  });
  return {
    id: match[1],
    locale: match[2] === 'ar' ? 'ar' : 'en',
    title: meta.title,
    summary: meta.summary ?? '',
    keywords: (meta.keywords ?? '').split(',').map((k) => k.trim()).filter(Boolean),
    docsUrl: meta.docs ? docsHref(meta.docs) : null,
    body: withLinks,
  };
}

/** Every article in the requested locale, English where no translation exists, sorted by title. */
export function articlesFor(all: readonly HelpArticle[], locale: Locale): HelpArticle[] {
  const byId = new Map<string, HelpArticle>();
  for (const article of all) if (article.locale === 'en') byId.set(article.id, article);
  if (locale !== 'en') for (const article of all) if (article.locale === locale) byId.set(article.id, article);
  return [...byId.values()].sort((a, b) => a.title.localeCompare(b.title, locale));
}

const fold = (text: string) => text.normalize('NFKD').replace(/[̀-ًͯ-ٟ]/g, '').toLocaleLowerCase();

/**
 * Articles matching every word of the query, best first: title hits outrank keyword and summary
 * hits, which outrank body hits. An empty query returns the list unchanged.
 */
export function searchArticles(articles: readonly HelpArticle[], query: string): HelpArticle[] {
  const words = fold(query).split(/\s+/).filter(Boolean);
  if (!words.length) return [...articles];
  const scored: { article: HelpArticle; score: number }[] = [];
  for (const article of articles) {
    const title = fold(article.title), tags = fold(article.keywords.join(' ') + ' ' + article.summary), body = fold(article.body);
    let score = 0;
    for (const word of words) {
      if (title.includes(word)) score += 10;
      else if (tags.includes(word)) score += 5;
      else if (body.includes(word)) score += 1;
      else { score = 0; break; }
    }
    if (score > 0) scored.push({ article, score });
  }
  return scored.sort((a, b) => b.score - a.score || a.article.title.localeCompare(b.article.title)).map((s) => s.article);
}

/** Pages that share one article (tabs and companion settings pages). */
const ARTICLE_OF: Partial<Record<View, string>> = { memory: 'knowledge', autonomy: 'activity', agentControls: 'agent-controls' };

/** The help article id for a page. */
export const articleForView = (view: View): string => ARTICLE_OF[view] ?? view;

/** Opens the help panel, optionally on one article (the shell listens; see HelpPanel.tsx). */
export const HELP_EVENT = 'anum:help';
export function openHelp(article?: string): void {
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(HELP_EVENT, { detail: { article } }));
}
