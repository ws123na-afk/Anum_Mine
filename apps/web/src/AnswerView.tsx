import type { AnswerBlock, Inline } from './lib/tasks';
import './answer.css';

/** Renders parsed blocks as React text nodes only: model output can never inject markup. */
export function Answer({ blocks }: { blocks: AnswerBlock[] }) {
  return <div className="answerBody">{blocks.map((b, i) => {
    if (b.kind === 'heading') return b.level === 1 ? <h3 key={i}><Inlines parts={b.inline} /></h3> : <h4 key={i}><Inlines parts={b.inline} /></h4>;
    if (b.kind === 'code') return <pre key={i}><code>{b.text}</code></pre>;
    if (b.kind === 'list') { const items = b.items.map((it, j) => <li key={j}><Inlines parts={it} /></li>); return b.ordered ? <ol key={i}>{items}</ol> : <ul key={i}>{items}</ul>; }
    return <p key={i}><Inlines parts={b.inline} /></p>;
  })}</div>;
}

function Inlines({ parts }: { parts: Inline[] }) {
  return <>{parts.map((x, i) => x.kind === 'strong' ? <strong key={i}>{x.text}</strong> : x.kind === 'em' ? <em key={i}>{x.text}</em> : x.kind === 'code' ? <code key={i}>{x.text}</code> : <span key={i}>{x.text}</span>)}</>;
}
