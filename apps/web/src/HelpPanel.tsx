// The in-app help panel (docs/help.md): opened from the "?" in the top bar (HelpLink.tsx) or from a
// page's empty state. Articles are Markdown files in docs/help, bundled at build time so help works
// offline. The shell mounts this lazily when `openHelp()` is called (main.tsx).
import { useMemo, useState } from 'react';
import { ArrowLeft, BookOpen, ExternalLink, Search } from 'lucide-react';
import { Drawer } from './Dialog';
import { MessageMarkdown } from './MessageMarkdown';
import { HELP_ARTICLES } from './helpArticles';
import { DOCS_BASE_URL, articleForView, articlesFor, searchArticles, type HelpArticle } from './lib/help';
import { t } from './lib/i18n';
import { viewFromHash } from './lib/shell';
import { useLocale } from './useLocale';
import './help.css';

export function HelpPanel({ initialArticle, onClose }: { initialArticle?: string; onClose: () => void }) {
  const locale = useLocale();
  const [articleId, setArticleId] = useState<string | null>(initialArticle ?? null);
  const [query, setQuery] = useState('');
  const articles = useMemo(() => articlesFor(HELP_ARTICLES, locale), [locale]);
  const results = useMemo(() => searchArticles(articles, query), [articles, query]);
  const article = articleId ? articles.find((a) => a.id === articleId) ?? null : null;
  const here = articles.find((a) => a.id === articleForView(viewFromHash(location.hash)));
  return <Drawer title={t('help.title')} onClose={onClose}>
    {article ? <ArticleView article={article} onBack={() => setArticleId(null)} /> : <>
      <label className="helpSearch">
        <span className="visuallyHidden">{t('help.search')}</span>
        <Search aria-hidden="true" />
        <input type="search" value={query} placeholder={t('help.searchPlaceholder')} onChange={(e) => setQuery(e.target.value)} aria-describedby="helpResultCount" />
      </label>
      <p id="helpResultCount" className="muted helpCount" aria-live="polite">{t('help.count', { count: results.length })}</p>
      {!query && here && <section aria-labelledby="helpHere"><h3 id="helpHere" className="helpGroup">{t('help.thisPage')}</h3><ArticleList items={[here]} onPick={setArticleId} /></section>}
      {results.length > 0
        ? <section aria-labelledby="helpAll"><h3 id="helpAll" className="helpGroup">{query ? t('help.results') : t('help.allArticles')}</h3><ArticleList items={results} onPick={setArticleId} /></section>
        : <div className="paneEmpty helpEmpty"><Search aria-hidden="true" /><p>{t('help.noResults', { query })}</p><a href={`${DOCS_BASE_URL}help.md`} target="_blank" rel="noreferrer noopener">{t('help.docs')} <ExternalLink aria-hidden="true" /></a></div>}
      <p className="muted helpOffline">{t('help.offline')}</p>
    </>}
  </Drawer>;
}

function ArticleList({ items, onPick }: { items: HelpArticle[]; onPick: (id: string) => void }) {
  return <ul className="helpList">{items.map((a) => <li key={a.id}>
    <button type="button" className="helpItem" onClick={() => onPick(a.id)}>
      <BookOpen aria-hidden="true" />
      <span><strong dir="auto">{a.title}</strong><span className="muted" dir="auto">{a.summary}</span></span>
    </button>
  </li>)}</ul>;
}

function ArticleView({ article, onBack }: { article: HelpArticle; onBack: () => void }) {
  return <article className="helpArticle" aria-labelledby="helpArticleTitle">
    <button type="button" className="secondary helpBack" onClick={onBack}><ArrowLeft aria-hidden="true" />{t('help.back')}</button>
    <h3 id="helpArticleTitle" dir="auto">{article.title}</h3>
    {article.summary && <p className="muted" dir="auto">{article.summary}</p>}
    <MessageMarkdown text={article.body} />
    {article.docsUrl && <a className="helpDocs" href={article.docsUrl} target="_blank" rel="noreferrer noopener">{t('help.docs')} <ExternalLink aria-hidden="true" /></a>}
  </article>;
}
