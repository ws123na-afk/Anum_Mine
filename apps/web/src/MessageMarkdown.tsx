import { useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { highlight, languageLabel } from './lib/highlight';
import { parseMarkdown, type MdBlock, type MdInline } from './lib/markdown';
import { copyText } from './lib/clipboard';
import { t } from './lib/i18n';
import './answer.css';
import './message.css';

/**
 * An assistant answer rendered from Markdown (docs/conversations.md#rendering): headings,
 * lists, tables, quotes, links and highlighted code with a Copy button on every block.
 * Everything is React text nodes, so model output can never inject markup; links open in
 * a new tab without access to this page. Each block takes its direction from its own text.
 */
export function MessageMarkdown({ text, onCopied }: { text: string; onCopied?: (ok: boolean) => void }) {
  return <MarkdownBlocks blocks={parseMarkdown(text)} onCopied={onCopied} />;
}

export function MarkdownBlocks({ blocks, onCopied }: { blocks: MdBlock[]; onCopied?: (ok: boolean) => void }) {
  return <div className="answerBody mdBody">{blocks.map((b, i) => {
    switch (b.kind) {
      case 'heading': return b.level === 1 ? <h3 key={i} dir="auto"><Inlines parts={b.inline} /></h3> : <h4 key={i} dir="auto"><Inlines parts={b.inline} /></h4>;
      case 'code': return <CodeBlock key={i} lang={b.lang} code={b.text} onCopied={onCopied} />;
      case 'list': {
        const items = b.items.map((item, j) => <li key={j} dir="auto"><Inlines parts={item} /></li>);
        return b.ordered ? <ol key={i} start={b.start === 1 ? undefined : b.start}>{items}</ol> : <ul key={i}>{items}</ul>;
      }
      case 'table': return <div key={i} className="mdTableWrap" role="region" tabIndex={0} aria-label={t('message.table')}>
        <table><thead><tr>{b.header.map((cell, c) => <th key={c} scope="col" dir="auto" style={{ textAlign: alignment(b.align[c]) }}><Inlines parts={cell} /></th>)}</tr></thead>
          <tbody>{b.rows.map((row, r) => <tr key={r}>{row.map((cell, c) => <td key={c} dir="auto" style={{ textAlign: alignment(b.align[c]) }}><Inlines parts={cell} /></td>)}</tr>)}</tbody></table>
      </div>;
      case 'quote': return <blockquote key={i} dir="auto"><Inlines parts={b.inline} /></blockquote>;
      case 'rule': return <hr key={i} />;
      default: return <p key={i} dir="auto"><Inlines parts={b.inline} /></p>;
    }
  })}</div>;
}

function alignment(align: 'left' | 'center' | 'right' | null): 'start' | 'center' | 'end' | undefined {
  return align === 'left' ? 'start' : align === 'right' ? 'end' : align === 'center' ? 'center' : undefined;
}

function Inlines({ parts }: { parts: MdInline[] }) {
  return <>{parts.map((x, i) => {
    switch (x.kind) {
      case 'strong': return <strong key={i}>{x.text}</strong>;
      case 'em': return <em key={i}>{x.text}</em>;
      case 'del': return <del key={i}>{x.text}</del>;
      case 'code': return <code key={i} dir="ltr">{x.text}</code>;
      case 'link': return <a key={i} href={x.href} target="_blank" rel="noopener noreferrer nofollow" dir="auto">{x.text}</a>;
      default: return <span key={i}>{x.text}</span>;
    }
  })}</>;
}

/** A fenced code block: its language, highlighted code (left to right in any interface language) and Copy. */
export function CodeBlock({ lang, code, onCopied }: { lang: string; code: string; onCopied?: (ok: boolean) => void }) {
  const [copied, setCopied] = useState(false);
  const label = languageLabel(lang);
  async function copy() {
    const ok = await copyText(code);
    setCopied(ok);
    onCopied?.(ok);
    if (ok) window.setTimeout(() => setCopied(false), 2000);
  }
  return <figure className="mdCode">
    <figcaption>
      <span>{label || t('message.code')}</span>
      <button type="button" className="mdCopy" onClick={() => void copy()} aria-label={label ? t('message.copyCodeNamed', { language: label }) : t('message.copyCode')}>
        {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}<span>{copied ? t('message.copied') : t('message.copy')}</span>
      </button>
    </figcaption>
    <pre dir="ltr" tabIndex={0}><code>{highlight(code, lang).map((token, i) => token.kind === 'plain' ? token.text : <span key={i} className={`tok-${token.kind}`}>{token.text}</span>)}</code></pre>
  </figure>;
}
